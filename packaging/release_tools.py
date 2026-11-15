"""Build allowlisted source updates; never include user Excel files or credentials."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from release_layout import BOOTSTRAP_FILES, MAX_PACKAGE_BYTES, SCHEMA, app_files, repository_name, version_tuple


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
        digest.update((root / name).read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()[:24]


def prepare_bundle(root: Path, destination: Path) -> dict:
    destination.mkdir(parents=True, exist_ok=True)
    metadata = {"schema": SCHEMA, "version": app_version(root), "runtime_id": runtime_id(root), "files": {}}
    for path in app_files(root):
        content = path.read_bytes()
        compile(content, str(path), "exec")
        name = path.relative_to(root).as_posix()
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
