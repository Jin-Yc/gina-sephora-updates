"""Verify a copied single-file release without Python/Conda search paths."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import ProxyHandler, Request, build_opener

import pefile
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]


def clean_environment():
    environment = {key: value for key, value in os.environ.items()
                   if not key.upper().startswith(("PYTHON", "CONDA", "VIRTUAL_ENV"))}
    environment["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")
    return environment


def stop_test_tree(process):
    if process.poll() is None:
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        process.wait(timeout=10)


def main():
    source = ROOT / "dist" / "GinaSephora.exe"
    assert pefile.PE(str(source), fast_load=True).OPTIONAL_HEADER.Subsystem == 2
    with tempfile.TemporaryDirectory(prefix="gina-release-test-", ignore_cleanup_errors=True) as temporary:
        root = Path(temporary)
        isolated = root / "仅单文件 no Python"
        isolated.mkdir()
        executable = isolated / "分货工具.exe"
        shutil.copy2(source, executable)
        assert len(list(isolated.iterdir())) == 1
        report = root / "result.json"
        environment = clean_environment()
        environment["LOCALAPPDATA"] = str(root / "AppData")
        with (root / "business.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen([
                str(executable), "--bundle-check", str(ROOT / "SKU & Store List.xlsx"),
                str(ROOT / "StoreSOH1107.xls"), str(report),
            ], cwd=isolated, env=environment, stdout=log, stderr=subprocess.STDOUT,
               creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                code = process.wait(timeout=120)
                assert code == 0, (code, log.name)
            finally:
                stop_test_tree(process)
        result = json.loads(report.read_text(encoding="utf-8"))
        assert result["ok"] and result["frozen"] and result["metadata_complete"], result
        assert result["rows"] == 4665 and result["sku_count"] == 15, result
        assert result["stock"] == result["shipments"] + result["remaining"]
        assert result["final"] == result["shipments"]
        assert result["shipments"] == result["regular"] + result["dm"]
        assert result["dm"] <= result["dm_demand"]
        book = load_workbook(result["excel"], read_only=True)
        sheet = book.active
        headers = [cell.value for cell in sheet[1]]
        index = headers.index("Store")
        assert headers[index + 1:index + 3] == ["curstatus", "storename"]
        assert sheet.max_row == result["rows"] + 1
        book.close()
        print("PASS: copied exe alone; clean PATH; parsers, SciPy allocation and Excel export", flush=True)
        print(json.dumps({key: value for key, value in result.items() if key != "excel"}), flush=True)

        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        opener = build_opener(ProxyHandler({}))
        base_url = f"http://127.0.0.1:{port}"
        with (root / "server.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen([str(executable), "--test-server", str(port)],
                cwd=isolated, env=environment, stdout=log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                deadline = time.monotonic() + 90
                while True:
                    assert process.poll() is None, "Frozen server exited"
                    try:
                        with opener.open(base_url + "/_stcore/health", timeout=0.5) as response:
                            if response.status == 200:
                                break
                    except OSError:
                        pass
                    assert time.monotonic() < deadline, "Server startup timed out"
                    time.sleep(0.2)
                with opener.open(base_url + "/_stcore/agent/openapi.json") as response:
                    schema = json.load(response)
                assert "/_stcore/agent/interact" in schema["paths"]

                def interact(payload):
                    request = Request(base_url + "/_stcore/agent/interact",
                        data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
                    with opener.open(request, timeout=30) as response:
                        return json.load(response)

                page = interact({})
                assert page["status"] == "ready", page
                session_id = page["session_id"]
                page = interact({"session_id": session_id,
                                 "widget_state": {"new_stock_use_benchmark": True, "new_stock_use_dm": True}})
                assert page["status"] == "ready", page
                actions = {item["key"] for item in page["actions"]}
                assert {"new_stock_use_benchmark", "new_stock_use_dm", "exit_desktop"}.issubset(actions)
                page_text = json.dumps(page, ensure_ascii=False)
                assert "CDC 可分配库存比例与阈值" in page_text
                page = interact({"session_id": session_id, "page": "existing_stock"})
                assert page["status"] == "ready", page
                page_text = json.dumps(page, ensure_ascii=False)
                assert "检查文件并计算需求" in page_text and "现货不使用" in page_text
                print("PASS: frozen production-configured page and native checkbox interaction via agent API", flush=True)
                log.flush()
                console = (root / "server.log").read_text(encoding="utf-8", errors="replace")
                assert "Traceback" not in console, console
                exit_page = interact({"session_id": session_id, "trigger": {"key": "exit_desktop"}})
                assert exit_page["status"] == "ready", exit_page
                assert process.wait(timeout=15) == 0
                print("PASS: browser exit action stops server and one-file bootstrap cleanly", flush=True)
            except Exception:
                log.flush()
                print((root / "server.log").read_text(encoding="utf-8", errors="replace"), flush=True)
                raise
            finally:
                stop_test_tree(process)


if __name__ == "__main__":
    main()
