"""Build allowlisted source updates; never include user Excel files or credentials."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from release_layout import BOOTSTRAP_FILES, MAX_PACKAGE_BYTES, SCHEMA, app_files, repository_name, version_tuple


# The installed 2.1 launcher accepts its original payload paths. Keep that wire
# format stable while allowing the development tree to use ordinary packages.
PACKAGE_LAYOUT = {
    "domain": "",
    "data_io": "",
    "services": "",
    "ui": "ui",
    "app_pages": "app_pages",
}


def source_files(root: Path) -> list[tuple[Path, str]]:
    """Map development files to the paths understood by existing desktop exes."""
    sources = [(path, path.relative_to(root).as_posix()) for path in app_files(root)]
    for package, payload_prefix in PACKAGE_LAYOUT.items():
        directory = root / "gina_sephora" / package
        for path in sorted(directory.rglob("*.py")):
            if not payload_prefix and path.name == "__init__.py":
                continue
            name = (Path(payload_prefix) / path.relative_to(directory)).as_posix()
            sources.append((path, name))
    names = [name for _, name in sources]
    if len(names) != len(set(names)):
        raise ValueError("更新包路径重复，请检查是否遗留旧目录的同名模块")
    from release_layout import REQUIRED_FILES, allowed_source
    if not REQUIRED_FILES.issubset(names) or any(not allowed_source(name) for name in names):
        raise ValueError("源码布局不能转换为当前桌面更新格式")
    return sorted(sources, key=lambda item: item[1])


def payload_source(content: bytes) -> bytes:
    """Translate package imports and page paths only at the release boundary."""
    source = content.decode("utf-8")
    for package, prefix in PACKAGE_LAYOUT.items():
        target = prefix + "." if prefix else ""
        source = re.sub(r"(?m)^(\s*from )gina_sephora\." + re.escape(package) + r"\.",
                        r"\g<1>" + target, source)
    source = source.replace('"gina_sephora/app_pages/', '"app_pages/')
    source = source.replace("'gina_sephora/app_pages/", "'app_pages/")
    return source.encode("utf-8")


def app_modules(root: Path) -> set[str]:
    """Names for PyInstaller analysis, including development-only package parents."""
    paths = {path for path, _ in source_files(root)}
    paths.update((root / "gina_sephora").rglob("__init__.py"))
    return {path.relative_to(root).with_suffix("").as_posix().replace("/", ".").removesuffix(".__init__")
            for path in paths}


def app_version(root: Path) -> str:
    tree = ast.parse((root / "version.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and
                                               target.id == "APP_VERSION" for target in node.targets):
            version = ast.literal_eval(node.value)
            version_tuple(version)
            return version
    raise ValueError("version.py 缺少 APP_VERSION")


def runtime_id(root: Path) -> str:
    digest = hashlib.sha256(f"source-update-{SCHEMA}-py{sys.version_info[:2]}".encode())
    for name in sorted({"requirements.txt", *BOOTSTRAP_FILES}):
        digest.update(name.encode())
        content = (root / name).read_bytes()
        if name.endswith(".py"):
            content = payload_source(content)
        digest.update(content.replace(b"\r\n", b"\n"))
    return digest.hexdigest()[:24]


def prepare_bundle(root: Path, destination: Path) -> dict:
    destination.mkdir(parents=True, exist_ok=True)
    metadata = {"schema": SCHEMA, "version": app_version(root), "runtime_id": runtime_id(root), "files": {}}
    for path, name in source_files(root):
        content = payload_source(path.read_bytes())
        compile(content, str(path), "exec")
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        metadata["files"][name] = hashlib.sha256(content).hexdigest()
    (destination / "release.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return metadata


def make_update(root: Path, output: Path, repository: str) -> Path:
    repository = repository_name(repository)
    version = app_version(root)
    release_dir = output / version
    release_dir.mkdir(parents=True, exist_ok=True)
    # TemporaryDirectory keeps rebuilt ZIPs free of deleted modules from prior releases.
    import tempfile
    archive = release_dir / f"GinaSephora-app-{version}.zip"
    with tempfile.TemporaryDirectory(prefix="gina-payload-") as temporary:
        payload = Path(temporary)
        metadata = prepare_bundle(root, payload)
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for path in sorted(payload.rglob("*")):
                if path.is_file():
                    bundle.write(path, path.relative_to(payload).as_posix())
    if archive.stat().st_size > MAX_PACKAGE_BYTES:
        raise ValueError("业务更新包超过大小限制")
    manifest = {"schema": SCHEMA, "version": version, "runtime_id": metadata["runtime_id"],
                "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                "url": f"https://github.com/{repository}/releases/download/v{version}/{archive.name}"}
    (release_dir / "update.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return release_dir


def main():
    parser = argparse.ArgumentParser(description="生成 GitHub Releases 业务更新包")
    parser.add_argument("--repository", help="owner/repository；默认读取 update-config.json")
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / "updates")
    args = parser.parse_args()
    repository = args.repository or json.loads((ROOT / "update-config.json").read_text(encoding="utf-8"))["repository"]
    destination = make_update(ROOT, args.output, repository)
    print(f"Ready: {destination}")
    print("Upload the ZIP and update.json together to a published latest release.")


if __name__ == "__main__":
    main()
