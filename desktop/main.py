"""Desktop window for Rizq, backed by its loopback-only agent server."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import threading
from http.server import ThreadingHTTPServer

# This address must remain stable: the POS database is stored per web origin.
PORT = 47192


def app_data_dir(platform: str | None = None, environ=None, home: Path | None = None) -> Path:
    platform = platform or sys.platform
    environ = os.environ if environ is None else environ
    home = home or Path.home()
    if platform == "darwin":
        return home / "Library" / "Application Support" / "Rizq"
    if platform == "win32":
        return Path(environ.get("LOCALAPPDATA") or home / "AppData" / "Local") / "Rizq"
    return Path(environ.get("XDG_DATA_HOME") or home / ".local" / "share") / "rizq"


def configure_environment(data_dir: Path, runtime: Path) -> None:
    data = data_dir / "agent"
    results = data_dir / "results"
    for path in (data, results, data_dir / "webview"):
        path.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.environ.update({
        "RIZQ_DESKTOP": "1",
        "RIZQ_ASSET_RUNTIME": str(runtime),
        "RIZQ_DATA_DIR": str(data),
        "RIZQ_RESULTS_DIR": str(results),
        "RIZQ_MEMORY_DB": str(data / "chat_memory.sqlite"),
        "RIZQ_AUDIT_LOG": str(data / "audit.jsonl"),
        "RIZQ_SESSION_DB": str(data / "sessions.sqlite"),
        "RIZQ_SCHEDULE_DB": str(data / "schedule.sqlite"),
    })


def main() -> None:
    try:
        import webview
    except ImportError as exc:
        raise SystemExit("Desktop dependencies are missing. See desktop/README.md.") from exc
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    runtime = root / "agent" / "runtime"
    data = app_data_dir()
    configure_environment(data, runtime)
    sys.path.insert(0, str(runtime))
    from rizq_agent import web
    web.load_dotenv()

    # Never fall back to a random port or attach to another server: either would
    # open a different POS database, or expose the window to an unrelated service.
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), web.Handler)
    except OSError as exc:
        # Windowed builds have no terminal in which to show a startup error.
        webview.create_window("رزق | Rizq", html=(
            "<!doctype html><html lang='ar' dir='rtl'><meta charset='utf-8'>"
            "<body style='font-family:system-ui;padding:2rem'>"
            "<h2>تعذر تشغيل رزق</h2><p>المنفذ المحلي 47192 مستخدم. "
            "أغلق البرنامج الذي يستخدمه ثم حاول مجددًا.</p></body></html>"),
            width=600, height=300)
        webview.start()
        return
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        webview.settings["ALLOW_DOWNLOADS"] = True
        webview.create_window("رزق | Rizq", f"http://127.0.0.1:{PORT}/pos/",
                              width=1280, height=820, min_size=(900, 600),
                              background_color="#18211b", text_select=True)
        webview.start(private_mode=False, storage_path=str(data / "webview"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
