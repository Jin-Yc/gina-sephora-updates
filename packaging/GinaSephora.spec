from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

root = Path(SPECPATH).parent
sys.path.insert(0, str(root / "packaging"))
sys.path.insert(0, str(root))
from release_tools import prepare_bundle
from release_layout import app_files

payload = root / "build" / "update-payload"
metadata = prepare_bundle(root, payload)
datas = collect_data_files("streamlit", excludes=[".agents/**", "**/.agents/**", "testing/**", "hello/**"])
datas += copy_metadata("streamlit", recursive=True)
datas += [(str(payload / name), str(Path("bundled_app") / Path(name).parent))
          for name in metadata["files"]]
datas += [(str(payload / "release.json"), "bundled_app"), (str(root / "update-config.json"), ".")]
# Analyze app modules for dependencies, then ship only replaceable source copies.
app_modules = {path.relative_to(root).with_suffix("").as_posix().replace("/", ".").removesuffix(".__init__")
               for path in app_files(root)}
hiddenimports = sorted(app_modules)
hiddenimports += collect_submodules("streamlit", filter=lambda name: not name.startswith("streamlit.testing"))
hiddenimports += collect_submodules("uvicorn")

a = Analysis(
    [str(root / "launcher.py")], pathex=[str(root)], datas=datas, binaries=[],
    hiddenimports=hiddenimports, hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "ty", "streamlit.testing"],
    noarchive=False,
)
a.pure = [entry for entry in a.pure if entry[0] not in app_modules]
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [], name="GinaSephora",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False, disable_windowed_traceback=True,
)
