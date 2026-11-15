"""Windowless, single-instance Streamlit desktop launcher for Windows 11."""
import ctypes
from datetime import datetime
import json
import logging
from multiprocessing import freeze_support
import os
from pathlib import Path
import socket
import sys
from threading import Event, Thread
import time
import traceback
from urllib.parse import urlparse
from urllib.request import ProxyHandler, build_opener
import webbrowser

from desktop_runtime import RUNTIME_DIR, STATE_FILE, remove_own_state
from auto_update import probe_app, read_json, select_app

APP_ROOT = Path(__file__).resolve().parent


def configure_app(*, check_updates=False, rollback=False) -> None:
    """Select the app before importing any app modules or starting Streamlit."""
    global APP_ROOT
    if not getattr(sys, "frozen", False):
        return
    bundle = Path(__file__).resolve().parent
    repository = read_json(bundle / "update-config.json")["repository"]
    cache = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "GinaSephora" / "updates"
    selection = select_app(bundle / "bundled_app", cache, repository,
                           check=check_updates, rollback=rollback)
    APP_ROOT = selection.root
    sys.path.insert(0, str(APP_ROOT))
    sys.dont_write_bytecode = True
    os.environ["GINA_APP_VERSION"] = selection.version
    os.environ["GINA_UPDATE_STATUS"] = selection.status
    logging.info("Selected application %s at %s: %s", selection.version, APP_ROOT, selection.status)


def write_state(state: dict) -> None:
    temporary = STATE_FILE.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(state), encoding="utf-8")
    temporary.replace(STATE_FILE)


def valid_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "http" and parsed.hostname == "127.0.0.1" and parsed.port is not None


def open_existing() -> None:
    opener = build_opener(ProxyHandler({}))
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            url = state.get("url", "")
            if state.get("ready") and valid_url(url):
                with opener.open(url + "/_stcore/health", timeout=0.5) as response:
                    if response.status == 200:
                        webbrowser.open(url)
                        return
        except (OSError, ValueError):
            pass
        time.sleep(0.25)
    raise RuntimeError("已有程序尚未完成启动，请稍后重试。")


def acquire_instance():
    kernel = ctypes.windll.kernel32
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.CreateMutexW(None, False, "Local\\GinaSephoraDesktopV1")
    if not handle:
        raise ctypes.WinError()
    if kernel.GetLastError() == 183:
        kernel.CloseHandle(handle)
        return None
    return handle


def configure_logs() -> Path:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    log_path = RUNTIME_DIR / f"startup-{datetime.now():%Y%m%d-%H%M%S}-{os.getpid()}.log"
    stream = log_path.open("a", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream
    logging.basicConfig(level=logging.INFO, stream=stream)
    return log_path


def run_server(port: int, *, test_mode=False) -> None:
    from streamlit.web import cli

    os.environ["GINA_DESKTOP"] = "1"
    os.chdir(RUNTIME_DIR)
    sys.argv = [
        "streamlit", "run", str(APP_ROOT / "main.py"),
        "--global.developmentMode=false", "--server.address=127.0.0.1",
        f"--server.port={port}", "--server.headless=true", "--server.fileWatcherType=none",
        "--server.runOnSave=false", "--browser.gatherUsageStats=false",
        "--client.toolbarMode=minimal", "--client.showErrorDetails=none",
        f"--server.enableAgentApi={'true' if test_mode else 'false'}",
        "--server.baseUrlPath=",
    ]
    cli.main()


def launch() -> None:
    handle = acquire_instance()
    if handle is None:
        open_existing()
        return
    stopped = Event()
    try:
        configure_app(check_updates="--skip-update" not in sys.argv, rollback="--rollback" in sys.argv)
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        url = f"http://127.0.0.1:{port}"
        state = {"pid": os.getpid(), "url": url, "ready": False}
        write_state(state)

        def open_when_ready():
            from streamlit import config

            opener = build_opener(ProxyHandler({}))
            deadline = time.monotonic() + 90
            while not stopped.is_set() and time.monotonic() < deadline:
                try:
                    with opener.open(url + "/_stcore/health", timeout=0.5) as response:
                        ready = response.status == 200
                except OSError:
                    ready = False
                if ready:
                    state.update({
                        "ready": True,
                        "development_mode": config.get_option("global.developmentMode"),
                        "file_watcher": config.get_option("server.fileWatcherType"),
                        "agent_api": config.get_option("server.enableAgentApi"),
                        "toolbar": config.get_option("client.toolbarMode"),
                    })
                    write_state(state)
                    webbrowser.open(url)
                    return
                stopped.wait(0.2)
            if not stopped.is_set():
                ctypes.windll.user32.MessageBoxW(None, "启动超时，请关闭后重试。", "分货工具", 16)

        Thread(target=open_when_ready, daemon=True, name="open-browser").start()
        run_server(port)
    finally:
        stopped.set()
        remove_own_state()
        ctypes.windll.kernel32.CloseHandle(handle)


def verify_bundle(master_path: str, sales_path: str, report_path: str) -> None:
    """Release check mode exercises actual packaged parsers, MILP and Excel export."""
    from gina_sephora.domain.allocation import calculate_allocation
    from gina_sephora.data_io.exporting import details_to_excel
    from gina_sephora.data_io.validation import validate_uploads

    result = validate_uploads(Path(master_path).read_bytes(), Path(sales_path).read_bytes(),
                              use_benchmark=True, use_dm=True)
    if result.errors:
        raise ValueError(result.errors)
    allocation = calculate_allocation(result.tables["SKU"], result.tables["Store list"],
                                       result.tables["销售数据"], result.warehouses,
                                       use_benchmark=True, use_dm=True)
    details = allocation.details
    columns = list(details.columns)
    position = columns.index("Store")
    assert columns[position + 1:position + 3] == ["curstatus", "storename"]
    excel_path = Path(report_path).with_suffix(".xlsx")
    excel_path.write_bytes(details_to_excel(details))
    report = {
        "ok": True, "rows": len(details), "sku_count": len(allocation.summary),
        "stock": int(allocation.summary["Total QTY"].sum()),
        "shipments": int(allocation.summary["实际出库合计"].sum()),
        "dm": int(details["DM 加量"].sum()),
        "dm_demand": int(details["DM 需求"].sum()),
        "regular": int(details["常规实际出库"].sum()),
        "remaining": int(allocation.summary["剩余库存"].sum()),
        "paused_skus": int(allocation.summary["状态"].str.contains("暂停|负销量").sum()),
        "final": int(details["最终分配量"].sum()),
        "metadata_complete": bool(details["storename"].notna().all() and details["curstatus"].notna().all()),
        "excel": str(excel_path), "frozen": bool(getattr(sys, "frozen", False)),
    }
    Path(report_path).write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")


def main() -> None:
    log_path = configure_logs()
    diagnostic_mode = len(sys.argv) > 1 and sys.argv[1] in {
        "--bundle-check", "--test-server", "--test-update-server", "--check-update",
    }
    try:
        if len(sys.argv) == 3 and sys.argv[1] == "--check-update":
            probe_app(Path(sys.argv[2]).resolve())
        elif len(sys.argv) == 5 and sys.argv[1] == "--bundle-check":
            configure_app()
            verify_bundle(*sys.argv[2:])
        elif len(sys.argv) == 3 and sys.argv[1] in {"--test-server", "--test-update-server"}:
            configure_app(check_updates=sys.argv[1] == "--test-update-server")
            run_server(int(sys.argv[2]), test_mode=True)
        else:
            launch()
    except Exception:
        traceback.print_exc()
        if diagnostic_mode:
            raise SystemExit(1)
        ctypes.windll.user32.MessageBoxW(
            None, f"程序启动失败，请重试。\n诊断日志：{log_path}", "分货工具", 16,
        )
        raise SystemExit(1)


if __name__ == "__main__":
    freeze_support()
    main()
