"""Desktop-only lifecycle helpers shared by the launcher and browser UI."""
import json
import os
from pathlib import Path
import tempfile
from threading import Thread
import time


RUNTIME_DIR = Path(tempfile.gettempdir()) / "GinaSephoraDesktop"
STATE_FILE = RUNTIME_DIR / "running.json"


def is_desktop() -> bool:
    return os.environ.get("GINA_DESKTOP") == "1"


def remove_own_state() -> None:
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if state.get("pid") == os.getpid():
            STATE_FILE.unlink(missing_ok=True)
    except (OSError, ValueError):
        pass


def request_shutdown() -> None:
    """Let the page acknowledge the click before stopping the desktop process."""
    def stop():
        time.sleep(1.0)
        remove_own_state()
        os._exit(0)

    Thread(target=stop, daemon=True, name="desktop-exit").start()
