"""HTTPS source updates for a fixed PyInstaller runtime, before Streamlit imports."""
from dataclasses import dataclass
import hashlib
import importlib
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
import zipfile

from release_layout import (MAX_PACKAGE_BYTES, REQUIRED_FILES, SCHEMA, allowed_source,
                            repository_name, version_tuple)

LOG = logging.getLogger(__name__)


@dataclass
class Selection:
    root: Path
    version: str
    status: str


def read_json(path: Path) -> dict:
    result = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ValueError("JSON must be an object")
    return result


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def validate_metadata(metadata: dict, runtime_id: str) -> None:
    if metadata.get("schema") != SCHEMA or metadata.get("runtime_id") != runtime_id:
        raise ValueError("更新包不兼容当前运行环境，需要新版 exe")
    version_tuple(metadata["version"])
    files = metadata.get("files")
    if not isinstance(files, dict) or not REQUIRED_FILES.issubset(files) or len(files) > 1000:
        raise ValueError("更新包缺少必要文件")
    for name, digest in files.items():
        if not allowed_source(name) or not isinstance(digest, str) or len(digest) != 64:
            raise ValueError("更新包文件清单无效")
        int(digest, 16)


def verify_tree(root: Path, runtime_id: str) -> dict:
    metadata = read_json(root / "release.json")
    validate_metadata(metadata, runtime_id)
    for name, digest in metadata["files"].items():
        source = (root / name).read_bytes()
        if hashlib.sha256(source).hexdigest() != digest:
            raise ValueError(f"代码校验失败：{name}")
    return metadata


class HTTPSRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlparse(newurl).scheme != "https":
            raise ValueError("拒绝非 HTTPS 下载重定向")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url: str, destination: Path, limit: int, *, seconds: float = 25) -> None:
    if urlparse(url).scheme != "https":
        raise ValueError("更新下载必须使用 HTTPS")
    request = Request(url, headers={"User-Agent": "GinaSephora-Updater/1", "Cache-Control": "no-cache"})
    deadline = time.monotonic() + seconds
    with build_opener(HTTPSRedirects()).open(request, timeout=4) as response:
        if urlparse(response.url).scheme != "https":
            raise ValueError("下载地址无效")
        with destination.open("wb") as stream:
            total = 0
            while chunk := response.read(64 * 1024):
                total += len(chunk)
                if total > limit or time.monotonic() > deadline:
                    raise ValueError("更新下载超过大小或时间限制")
                stream.write(chunk)


def unpack(archive: Path, destination: Path, manifest: dict, runtime_id: str) -> None:
    """Validate the complete ZIP before writing anything outside its fresh staging dir."""
    with zipfile.ZipFile(archive) as bundle:
        entries = bundle.infolist()
        names = [item.filename for item in entries]
        if (len(entries) > 1001 or len(set(name.casefold() for name in names)) != len(names)
                or sum(item.file_size for item in entries) > MAX_PACKAGE_BYTES
                or any(item.is_dir() or item.flag_bits & 1 or
                       (item.external_attr >> 16) & 0o170000 == 0o120000 for item in entries)
                or any(name != "release.json" and not allowed_source(name) for name in names)):
            raise ValueError("ZIP 包含不允许的路径、链接或文件")
        metadata = json.loads(bundle.read("release.json"))
        validate_metadata(metadata, runtime_id)
        if metadata["version"] != manifest["version"] or set(names) != {*metadata["files"], "release.json"}:
            raise ValueError("ZIP 内容与更新清单不一致")
        for name in names:
            content = bundle.read(name)
            if name != "release.json":
                if hashlib.sha256(content).hexdigest() != metadata["files"][name]:
                    raise ValueError(f"代码校验失败：{name}")
                compile(content, name, "exec")
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)


def probe_app(root: Path) -> None:
    """Run in a disposable child: catch missing dependencies without polluting the launcher."""
    metadata = read_json(root / "release.json")
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(root))
    importlib.invalidate_caches()
    for name in metadata["files"]:
        compile((root / name).read_bytes(), name, "exec")
        # Page scripts need a Streamlit session; reusable modules must import cleanly.
        if name == "main.py" or name.startswith("app_pages/"):
            continue
        module = name.removesuffix(".py").replace("/", ".").removesuffix(".__init__")
        importlib.import_module(module)


def check_in_child(root: Path) -> None:
    command = [sys.executable]
    if not getattr(sys, "frozen", False):
        command.append(str(Path(__file__).with_name("launcher.py")))
    subprocess.run(command + ["--check-update", str(root)], check=True, timeout=45,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def select_app(bundle_root: Path, cache: Path, repository: str, *, check: bool = True,
               rollback: bool = False) -> Selection:
    """Caller holds the desktop mutex throughout selection and the running server."""
    baseline = read_json(bundle_root / "release.json")
    runtime_id = baseline["runtime_id"]
    channel = hashlib.sha256(repository.lower().encode()).hexdigest()[:16]
    cache = cache / channel / runtime_id
    current = Selection(bundle_root, baseline["version"], "使用内置版本")
    pointer: dict = {}
    try:
        cache.mkdir(parents=True, exist_ok=True)
        pointer_path = cache / "active.json"
        if pointer_path.exists():
            pointer = read_json(pointer_path)

        def cached(name):
            if not isinstance(name, str) or Path(name).name != name or not name.startswith("app-"):
                raise ValueError("本机版本记录无效")
            root = cache / name
            metadata = verify_tree(root, runtime_id)
            return Selection(root, metadata["version"], "使用本机版本")

        if rollback:
            previous = cached(pointer["previous"]) if pointer.get("previous") else current
            if pointer.get("current"):
                pointer["blocked"] = pointer["current"]
            pointer["current"] = previous.root.name if previous.root != bundle_root else None
            pointer["previous"] = None
            write_json(pointer_path, pointer)
            previous.status = "已回退版本，本次跳过更新"
            return previous
        if pointer.get("current"):
            try:
                candidate = cached(pointer["current"])
                if version_tuple(candidate.version) >= version_tuple(current.version):
                    current = candidate
            except (OSError, ValueError, KeyError):
                LOG.exception("Cached version damaged; trying previous version")
                if pointer.get("previous"):
                    try:
                        candidate = cached(pointer["previous"])
                        if version_tuple(candidate.version) >= version_tuple(current.version):
                            current = candidate
                    except (OSError, ValueError, KeyError):
                        LOG.exception("Previous cached version damaged; using bundled version")
                current.status = "本机更新文件损坏，已使用可用版本"
        if not check or not repository:
            return current
        repository = repository_name(repository)
        prefix = f"https://github.com/{repository}/releases/"
        with tempfile.TemporaryDirectory(prefix="download-", dir=cache) as temporary:
            staging = Path(temporary)
            manifest_path = staging / "update.json"
            download(prefix + "latest/download/update.json", manifest_path, 64 * 1024, seconds=8)
            manifest = read_json(manifest_path)
            if manifest.get("schema") != SCHEMA:
                raise ValueError("不支持的更新清单格式")
            if version_tuple(manifest["version"]) <= version_tuple(current.version):
                current.status = "已是最新版本"
                return current
            if manifest.get("runtime_id") != runtime_id:
                current.status = "有新版，但需要更换新版 exe；当前版本可继续使用"
                return current
            digest = manifest["sha256"]
            if not isinstance(digest, str) or len(digest) != 64:
                raise ValueError("更新校验码无效")
            int(digest, 16)
            name = f"app-{manifest['version']}-{digest[:16]}"
            if pointer.get("blocked") == name:
                current.status = "已跳过曾回退的更新版本"
                return current
            url = manifest["url"]
            if not isinstance(url, str) or not url.startswith(prefix + "download/"):
                raise ValueError("更新包必须来自配置的 GitHub 仓库")
            archive = staging / "app.zip"
            download(url, archive, MAX_PACKAGE_BYTES)
            if hashlib.sha256(archive.read_bytes()).hexdigest() != digest:
                raise ValueError("更新包 SHA256 校验失败")
            candidate_root = staging / "app"
            candidate_root.mkdir()
            unpack(archive, candidate_root, manifest, runtime_id)
            check_in_child(candidate_root)
            final = cache / name
            if final.exists():
                # A completed, interrupted install is safe to reuse after verification.
                metadata = verify_tree(final, runtime_id)
                if metadata != read_json(candidate_root / "release.json"):
                    raise ValueError("已缓存版本与下载内容不符")
            else:
                candidate_root.rename(final)
            write_json(pointer_path, {"current": name,
                                      "previous": current.root.name if current.root != bundle_root else None})
            return Selection(final, manifest["version"], "已自动更新到最新版本")
    except Exception:
        LOG.exception("Update unavailable; keeping the last usable version")
        current.status = "更新检查或安装失败，继续使用本机版本"
        return current
