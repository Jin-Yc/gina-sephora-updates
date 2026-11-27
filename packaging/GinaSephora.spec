from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

root = Path(SPECPATH).parent
sys.path.insert(0, str(root / "packaging"))
sys.path.insert(0, str(root))
from release_tools import app_modules, payload_source, prepare_bundle

payload = root / "build" / "update-payload"
metadata = prepare_bundle(root, payload)
runtime_launcher = root / "build" / "runtime-entry" / "launcher.py"
runtime_launcher.parent.mkdir(parents=True, exist_ok=True)
runtime_launcher.write_bytes(payload_source((root / "launcher.py").read_bytes()))
datas = collect_data_files("streamlit", excludes=[".agents/**", "**/.agents/**", "testing/**", "hello/**"])
datas += collect_data_files("certifi")
datas += copy_metadata("streamlit", recursive=True)
datas += [(str(payload / name), str(Path("bundled_app") / Path(name).parent))
          for name in metadata["files"]]
datas += [(str(payload / "release.json"), "bundled_app"), (str(root / "update-config.json"), ".")]
# Analyze app modules for dependencies, then ship only replaceable source copies.
business_modules = app_modules(root)
hiddenimports = sorted(business_modules)
hiddenimports += collect_submodules("streamlit", filter=lambda name: not name.startswith("streamlit.testing"))
hiddenimports += collect_submodules("uvicorn")

a = Analysis(
    [str(runtime_launcher)], pathex=[str(root)], datas=datas, binaries=[],
    hiddenimports=hiddenimports, hookspath=[], hooksconfig={},
    runtime_hooks=[str(root / "packaging" / "runtime_certificates.py")],
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "ty", "streamlit.testing"],
    noarchive=False,
)
a.pure = [entry for entry in a.pure if entry[0] not in business_modules]
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [], name="GinaSephora",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False, disable_windowed_traceback=True,
)
