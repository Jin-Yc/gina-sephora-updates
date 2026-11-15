"""Exercise real update archives, atomic activation and safe offline fallback."""
import hashlib
import ast
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import auto_update
from auto_update import read_json, select_app
from release_layout import BOOTSTRAP_FILES, allowed_source, version_tuple

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("release_tools", ROOT / "packaging" / "release_tools.py")
assert spec and spec.loader
release_tools = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_tools)
REPOSITORY = "example/gina-updates"


class AutoUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="gina-update-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        for path, _ in release_tools.source_files(ROOT):
            target = self.source / path.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
        for name in {*BOOTSTRAP_FILES, "requirements.txt"}:
            shutil.copyfile(ROOT / name, self.source / name)
        self.baseline = self.root / "baseline"
        (self.source / "version.py").write_text('APP_VERSION = "2.1.0"\n', encoding="utf-8")
        release_tools.prepare_bundle(self.source, self.baseline)
        self.cache = self.root / "cache"
        self.release = self.build_release("2.1.1")

    def build_release(self, version):
        (self.source / "version.py").write_text(f'APP_VERSION = "{version}"\n', encoding="utf-8")
        return release_tools.make_update(self.source, self.root / "releases", REPOSITORY)

    def download(self, url, destination, limit, **kwargs):
        source = self.release / ("update.json" if url.endswith("update.json") else
                                 f"GinaSephora-app-{read_json(self.release / 'update.json')['version']}.zip")
        self.assertLessEqual(source.stat().st_size, limit)
        shutil.copyfile(source, destination)

    def select(self, **kwargs):
        with patch("auto_update.download", side_effect=self.download):
            return select_app(self.baseline, self.cache, REPOSITORY, **kwargs)

    def alter_manifest(self, **changes):
        path = self.release / "update.json"
        manifest = read_json(path)
        manifest.update(changes)
        path.write_text(json.dumps(manifest), encoding="utf-8")

    def test_real_install_loads_new_source_and_offline_reuses_it(self):
        # Real child interpreter validates actual app imports in the downloaded tree.
        selected = self.select()
        self.assertEqual(selected.version, "2.1.1")
        self.assertEqual(selected.status, "已自动更新到最新版本")
        script = "import sys; sys.path.insert(0, sys.argv[1]); from version import APP_VERSION; print(APP_VERSION)"
        result = subprocess.run([sys.executable, "-c", script, str(selected.root)], cwd=self.root,
                                capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), "2.1.1")
        with patch("auto_update.download", side_effect=OSError("offline")), self.assertLogs("auto_update"):
            offline = select_app(self.baseline, self.cache, REPOSITORY)
        self.assertEqual(offline.root, selected.root)
        self.assertEqual(offline.version, "2.1.1")

    def test_no_update_for_equal_or_older_version(self):
        for version in ("2.1.0", "2.0.9"):
            with self.subTest(version=version), patch("auto_update.check_in_child") as probe:
                self.alter_manifest(version=version)
                selected = self.select()
                self.assertEqual(selected.root, self.baseline)
                probe.assert_not_called()

    def test_runtime_mismatch_keeps_current_app(self):
        self.alter_manifest(runtime_id="incompatible-runtime")
        with patch("auto_update.check_in_child") as probe:
            selected = self.select()
            self.assertEqual(selected.root, self.baseline)
            self.assertIn("新版 exe", selected.status)
            probe.assert_not_called()

    def test_bad_digest_does_not_activate(self):
        self.alter_manifest(sha256="0" * 64)
        with self.assertLogs("auto_update"):
            selected = self.select()
        self.assertEqual(selected.root, self.baseline)
        self.assertFalse(list(self.cache.rglob("active.json")))

    def test_failed_import_does_not_activate(self):
        (self.source / "new_helper.py").write_text("import gina_dependency_that_does_not_exist\n", encoding="utf-8")
        self.release = self.build_release("2.1.1")
        with self.assertLogs("auto_update"):
            selected = self.select()
        self.assertEqual(selected.root, self.baseline)
        self.assertFalse(list(self.cache.rglob("active.json")))

    def test_rollback_skips_the_same_release(self):
        with patch("auto_update.check_in_child"):
            self.assertEqual(self.select().version, "2.1.1")
            self.release = self.build_release("2.1.2")
            self.assertEqual(self.select().version, "2.1.2")
            self.assertEqual(self.select(rollback=True).version, "2.1.1")
            again = self.select()
            self.assertEqual(again.version, "2.1.1")
            self.assertIn("回退", again.status)
            self.release = self.build_release("2.1.3")
            self.assertEqual(self.select().version, "2.1.3")

    def test_damaged_cache_falls_back_to_previous(self):
        with patch("auto_update.check_in_child"):
            previous = self.select()
            self.release = self.build_release("2.1.2")
            current = self.select()
        (current.root / "version.py").write_text("broken", encoding="utf-8")
        with self.assertLogs("auto_update"):
            selected = self.select(check=False)
        self.assertEqual(selected.root, previous.root)

    def test_zip_traversal_and_bootstrap_changes_are_rejected(self):
        archive = self.release / "GinaSephora-app-2.1.1.zip"
        with zipfile.ZipFile(archive, "a") as bundle:
            bundle.writestr("../escaped.py", "pass")
            bundle.writestr("launcher.py", "pass")
        self.alter_manifest(sha256=hashlib.sha256(archive.read_bytes()).hexdigest())
        with self.assertLogs("auto_update"):
            selected = self.select()
        self.assertEqual(selected.root, self.baseline)
        self.assertFalse(list(self.root.rglob("escaped.py")))

    def test_skip_update_never_uses_network(self):
        with patch("auto_update.download", side_effect=AssertionError("must not download")):
            selected = select_app(self.baseline, self.cache, REPOSITORY, check=False)
        self.assertEqual(selected.root, self.baseline)

    def test_builder_excludes_business_data_and_runtime(self):
        with zipfile.ZipFile(self.release / "GinaSephora-app-2.1.1.zip") as bundle:
            self.assertTrue(all(name == "release.json" or allowed_source(name) for name in bundle.namelist()))
            self.assertTrue(set(BOOTSTRAP_FILES).isdisjoint(bundle.namelist()))
        self.assertGreater(version_tuple("2.10.0"), version_tuple("2.9.9"))
        for name in ("../x.py", "ui/../../x.py", "ui\\x.py", "launcher.py", "CON.py", "input.xlsx"):
            self.assertFalse(allowed_source(name), name)

    def test_package_layout_remains_compatible_with_existing_exes(self):
        with zipfile.ZipFile(self.release / "GinaSephora-app-2.1.1.zip") as bundle:
            names = set(bundle.namelist())
            self.assertTrue({"allocation.py", "validation.py", "workflows.py",
                             "ui/allocation_page.py", "app_pages/existing_stock.py"}.issubset(names))
            self.assertFalse(any(name.startswith("gina_sephora/") for name in names))
            main = bundle.read("main.py").decode("utf-8")
            self.assertIn('"app_pages/existing_stock.py"', main)
            for name in names - {"release.json"}:
                tree = ast.parse(bundle.read(name), filename=name)
                self.assertFalse(any(isinstance(node, ast.ImportFrom) and node.module and
                                     node.module.startswith("gina_sephora") for node in ast.walk(tree)), name)

    def test_builder_rejects_old_and_new_module_name_collisions(self):
        (self.source / "allocation.py").write_text("pass\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "路径重复"):
            release_tools.prepare_bundle(self.source, self.root / "collision")


if __name__ == "__main__":
    unittest.main()
