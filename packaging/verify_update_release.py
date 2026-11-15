"""Verify a real GitHub upgrade and cached restart in an isolated frozen exe."""
import argparse
import json
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
from urllib.request import ProxyHandler, Request, build_opener

from verify_release import ROOT, clean_environment, stop_test_tree


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-version", required=True)
    args = parser.parse_args()
    expected = args.expected_version
    snapshots = {}
    with tempfile.TemporaryDirectory(prefix="gina-online-update-", ignore_cleanup_errors=True) as temporary:
        root = Path(temporary)
        executable = root / "分货工具.exe"
        shutil.copy2(ROOT / "dist" / "GinaSephora.exe", executable)
        environment = clean_environment()
        environment["LOCALAPPDATA"] = str(root / "AppData")
        opener = build_opener(ProxyHandler({}))
        for mode in ("--test-update-server", "--test-server"):
            with socket.socket() as reservation:
                reservation.bind(("127.0.0.1", 0))
                port = reservation.getsockname()[1]
            base_url = f"http://127.0.0.1:{port}"
            log_path = root / f"{mode}.log"
            with log_path.open("w", encoding="utf-8") as log:
                process = subprocess.Popen([str(executable), mode, str(port)], env=environment,
                                           cwd=root, stdout=log, stderr=subprocess.STDOUT,
                                           creationflags=subprocess.CREATE_NO_WINDOW)
                try:
                    deadline = time.monotonic() + 120
                    while True:
                        assert process.poll() is None, log_path.read_text(encoding="utf-8", errors="replace")
                        try:
                            with opener.open(base_url + "/_stcore/health", timeout=0.5) as response:
                                if response.status == 200:
                                    break
                        except OSError:
                            pass
                        assert time.monotonic() < deadline, "Update/server startup timed out"
                        time.sleep(0.2)
                    with opener.open(base_url + "/_stcore/agent/openapi.json") as response:
                        schema = json.load(response)
                        assert "/_stcore/agent/interact" in schema["paths"]

                    def interact(payload):
                        request = Request(base_url + "/_stcore/agent/interact",
                                          data=json.dumps(payload).encode(),
                                          headers={"Content-Type": "application/json"})
                        with opener.open(request, timeout=30) as response:
                            return json.load(response)

                    page = interact({})
                    assert page["status"] == "ready", page
                    page_text = json.dumps(page, ensure_ascii=False)
                    assert f"分货工具 v{expected}" in page_text, page_text
                    if mode == "--test-update-server":
                        assert "已自动更新到最新版本" in page_text, page_text
                    else:
                        assert "使用本机版本" in page_text, page_text
                    existing = interact({"session_id": page["session_id"], "page": "existing_stock"})
                    assert existing["status"] == "ready", existing
                    assert "检查文件并计算需求" in json.dumps(existing, ensure_ascii=False)
                    snapshots[mode] = {"new_stock": page, "existing_stock": existing}
                    interact({"session_id": page["session_id"], "trigger": {"key": "exit_desktop"}})
                    assert process.wait(timeout=15) == 0
                    log.flush()
                    console = log_path.read_text(encoding="utf-8", errors="replace")
                    assert "Traceback" not in console, console
                    print(f"PASS: {mode}, real frozen pages at v{expected}", flush=True)
                finally:
                    stop_test_tree(process)
        pointer_path, = (root / "AppData" / "GinaSephora" / "updates").rglob("active.json")
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        active_root = pointer_path.parent / pointer["current"]
        metadata = json.loads((active_root / "release.json").read_text(encoding="utf-8"))
        assert metadata["version"] == expected
        assert f'APP_VERSION = "{expected}"' in (active_root / "version.py").read_text(encoding="utf-8")
        report = {"ok": True, "version": expected, "modes": list(snapshots),
                  "runtime_id": metadata["runtime_id"], "files": len(metadata["files"])}
    (ROOT / "build" / "update-acceptance.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (ROOT / "build" / "update-snapshots.json").write_text(
        json.dumps(snapshots, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
