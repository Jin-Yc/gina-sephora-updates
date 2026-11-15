"""Shared release format; this module belongs to the fixed desktop runtime."""
from pathlib import Path, PurePosixPath
import re


SCHEMA = 1
MAX_PACKAGE_BYTES = 20 * 1024 * 1024
BOOTSTRAP_FILES = {"launcher.py", "desktop_runtime.py", "auto_update.py", "release_layout.py"}
REQUIRED_FILES = {"main.py", "version.py", "app_pages/new_stock.py", "app_pages/existing_stock.py",
                  "ui/__init__.py", "allocation.py", "validation.py", "workflows.py",
                  "adjustments.py", "existing_stock.py", "exporting.py"}


def version_tuple(value: str) -> tuple[int, int, int]:
    if not isinstance(value, str) or not re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", value):
        raise ValueError("版本号必须为 major.minor.patch，例如 2.1.1")
    major, minor, patch = value.split(".")
    return int(major), int(minor), int(patch)


def repository_name(value: str) -> str:
    value = value.removeprefix("https://github.com/").removesuffix("/").removesuffix(".git")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+", value):
        raise ValueError("请提供公开 GitHub 仓库，格式为 owner/repository")
    if value.split("/")[1] in {".", ".."}:
        raise ValueError("GitHub 仓库名无效")
    return value


def allowed_source(name: str) -> bool:
    """Only app Python sources, never executables, secrets, data or bootstrap files."""
    path = PurePosixPath(name)
    if "\\" in name or path.is_absolute() or str(path) != name:
        return False
    if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", part) for part in path.parts[:-1]):
        return False
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\.py", path.name):
        return False
    if path.stem.upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)),
                             *(f"LPT{i}" for i in range(10))}:
        return False
    return (len(path.parts) == 1 and name not in BOOTSTRAP_FILES) or (
        len(path.parts) > 1 and path.parts[0] in {"ui", "app_pages"}
    )


def app_files(root: Path) -> list[Path]:
    candidates = list(root.glob("*.py"))
    for directory in ("ui", "app_pages"):
        candidates.extend((root / directory).rglob("*.py"))
    return sorted(path for path in candidates if allowed_source(path.relative_to(root).as_posix()))
