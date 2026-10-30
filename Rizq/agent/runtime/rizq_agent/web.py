"""Local full-stack Rizq dashboard and POS bridge (Python standard library only)."""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import pathlib
import secrets
import sqlite3
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

from . import code_agent, copilot, memory, rizq, skills, zatca

ROOT = pathlib.Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
POS = ROOT.parent.parent / "rizq-pos"
DEFAULT_DB = ROOT / "fixtures" / "rizq_dev.sqlite"
ACTIVE_DB = pathlib.Path(os.environ.get("RIZQ_DB", str(DEFAULT_DB))).resolve()
DB_TEMP: str | None = None
SESSIONS: dict[str, dict] = {}
LOCAL_SESSIONS: dict[str, dict] = {}
LOCAL_SESSIONS_LOCK = threading.Lock()
MAX_BODY = 30 * 1024 * 1024
CODE_LOCK = threading.Lock()
CODE_FAILURES: dict[str, list[float]] = {}


def load_dotenv() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def provider_status() -> dict:
    load_dotenv()
    ollama = copilot.local_model()
    return {
        "active": os.environ.get("RIZQ_LLM_PROVIDER", "auto"),
        "ollama": {"available": bool(ollama), "model": ollama},
        "claude": {"available": bool(copilot._load_key()), "model": os.environ.get("RIZQ_CLAUDE_MODEL", "claude-sonnet-5-5")},
        "openai": {"available": bool(os.environ.get("OPENAI_API_KEY")), "model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini")},
        "compatible": {"available": bool(os.environ.get("RIZQ_LLM_BASE_URL")), "model": os.environ.get("RIZQ_LLM_MODEL", ""),
                       "base_url": os.environ.get("RIZQ_LLM_BASE_URL", "")},
    }


def validate_database(path: pathlib.Path) -> None:
    with path.open("rb") as fh:
        if fh.read(16) != b"SQLite format 3\x00":
            raise ValueError("الملف ليس قاعدة SQLite صالحة")
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "orders" not in names or "products" not in names:
            raise ValueError("قاعدة البيانات لا تبدو قاعدة رزق؛ جدول الطلبات أو المنتجات غير موجود")
    finally:
        con.close()


class Handler(BaseHTTPRequestHandler):
    server_version = "RizqLocal/1.0"

    def log_message(self, fmt, *args):
        print("[rizq-web] " + fmt % args)

    def send_json(self, obj: dict, status: int = 200):
        raw = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def read_json(self) -> dict:
        size = int(self.headers.get("Content-Length", "0"))
        if size > MAX_BODY:
            raise ValueError("حجم الطلب أكبر من الحد المسموح")
        return json.loads(self.rfile.read(size) or b"{}")

    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        if path == "/api/health":
            try:
                validate_database(ACTIVE_DB)
                db_ok = True
            except Exception:
                db_ok = False
            self.send_json({"ok": True, "database": {"ready": db_ok, "name": ACTIVE_DB.name,
                            "source": "browser export" if DB_TEMP else "fixture/default"},
                            "providers": provider_status()})
            return
        if path == "/api/skills":
            learned = skills.load_learned(str(ROOT / "results" / "skills_learned.json"))
            self.send_json({"skills": {n: s.title_ar for n, s in skills.SKILLS.items()},
                            "learned": {n: v["description"] for n, v in learned.items()}})
            return
        if path == "/api/features":
            self.send_json(json.loads((ROOT / "features" / "parked.json").read_text(encoding="utf-8")))
            return
        if path == "/api/problems":
            self.send_json(json.loads((ROOT / "problems" / "catalog.json").read_text(encoding="utf-8")))
            return
        if path == "/api/zatca/status":
            self.send_json(zatca.readiness())
            return
        if path.startswith("/api/zatca/vat-summary"):
            from .skills import run
            month = ""
            try:
                month = urlparse(self.path).query.split("month=", 1)[1].split("&", 1)[0]
            except IndexError:
                pass
            db = rizq.open_copy(str(ACTIVE_DB))
            try:
                summary = run(db, "vat_collected", {"month": month or None})
                self.send_json({"report": summary, "filing_status": "local_estimate_only",
                                "submitted_to_zatca": False})
            finally:
                db.close()
            return
        if path == "/api/scan":
            try:
                db = rizq.open_copy(str(ACTIVE_DB))
                try:
                    findings = rizq.scan(db)
                finally:
                    db.close()
                self.send_json({"count": len(findings), "findings": [
                    {"kind": f.kind, "entity": f.entity, "handling": f.handling,
                     "detail": f.detail, "action_ar": f.human_action_ar, "action_en": f.human_action}
                    for f in findings
                ]})
            except Exception as e:
                self.send_json({"error": str(e)}, 400)
            return
        if path == "/" or path == "/index.html":
            # The cashier is the single user-facing Rizq app. Keep the agent in its
            # existing assistant tab instead of exposing a second dashboard.
            self.send_response(302)
            self.send_header("Location", "/pos/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path.startswith("/web/"):
            return self.send_static(WEB, path[len("/web/"):])
        if path == "/pos" or path == "/pos/":
            return self.send_file(POS / "index.html")
        if path.startswith("/pos/"):
            return self.send_static(POS, path[len("/pos/"):])
        # The POS bundle uses root-absolute asset URLs, so expose its assets at the same origin.
        if path.startswith("/assets/") or path in ("/sql-wasm.wasm", "/favicon.svg", "/icons.svg"):
            return self.send_static(POS, path.lstrip("/"))
        self.send_json({"error": "not found"}, 404)

    def send_file(self, file: pathlib.Path):
        try:
            data = file.read_bytes()
        except OSError:
            self.send_json({"error": "file not found"}, 404)
            return
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(str(file))[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def send_static(self, root: pathlib.Path, rel: str):
        target = (root / rel).resolve()
        if root.resolve() not in target.parents or not target.is_file():
            self.send_json({"error": "not found"}, 404)
            return
        self.send_file(target)

    def do_POST(self):
        global ACTIVE_DB, DB_TEMP
        path = urlparse(self.path).path
        try:
            body = self.read_json()
            if path == "/api/memory/history":
                user_id = str(body.get("user_id", ""))
                if not user_id or len(user_id) > 100:
                    raise ValueError("A signed-in user is required.")
                self.send_json({"messages": memory.history(user_id)})
                return
            if path in ("/api/code/propose", "/api/code/apply"):
                origin = self.headers.get("Origin", "")
                host = self.headers.get("Host", "")
                valid_hosts = {"127.0.0.1:" + str(self.server.server_port),
                               "localhost:" + str(self.server.server_port)}
                if host not in valid_hosts or origin != "http://" + host:
                    raise ValueError("Code changes require a same-origin request from Rizq.")
                client = self.client_address[0]
                with CODE_LOCK:
                    failures = [t for t in CODE_FAILURES.get(client, []) if time.monotonic() - t < 300]
                    CODE_FAILURES[client] = failures
                    if len(failures) >= 5:
                        self.send_json({"error": "Too many failed attempts. Try again in five minutes."}, 429)
                        return
                    if not code_agent.authenticate(str(body.get("password", ""))):
                        failures.append(time.monotonic())
                        self.send_json({"error": "Code administrator authentication failed or is not configured."}, 403)
                        return
                    CODE_FAILURES.pop(client, None)
                    if path == "/api/code/propose":
                        self.send_json(code_agent.generate(str(body.get("path", "")), str(body.get("request", ""))))
                    else:
                        self.send_json(code_agent.apply(str(body.get("proposal", ""))))
                return
            if path == "/api/database/import":
                raw = base64.b64decode(body.get("sqlite_base64", ""), validate=True)
                if not raw or len(raw) > MAX_BODY:
                    raise ValueError("قاعدة البيانات فارغة أو أكبر من 30 ميغابايت")
                fd, filename = tempfile.mkstemp(prefix="rizq-live-", suffix=".sqlite")
                with os.fdopen(fd, "wb") as fh:
                    fh.write(raw)
                candidate = pathlib.Path(filename)
                try:
                    validate_database(candidate)
                except Exception:
                    candidate.unlink(missing_ok=True)
                    raise
                if DB_TEMP:
                    pathlib.Path(DB_TEMP).unlink(missing_ok=True)
                DB_TEMP, ACTIVE_DB = filename, candidate
                SESSIONS.clear()
                self.send_json({"ok": True, "name": "قاعدة رزق من هذا المتصفح", "bytes": len(raw)})
                return
            if path == "/api/database/reset":
                if DB_TEMP:
                    pathlib.Path(DB_TEMP).unlink(missing_ok=True)
                DB_TEMP, ACTIVE_DB = None, DEFAULT_DB
                SESSIONS.clear()
                self.send_json({"ok": True, "name": ACTIVE_DB.name})
                return
            if path == "/api/chat/local":
                message = str(body.get("message", "")).strip()
                user_id = str(body.get("user_id", ""))
                if not message or len(message) > 5000 or not user_id or len(user_id) > 100:
                    raise ValueError("A signed-in cashier user and a valid message are required.")
                raw = base64.b64decode(body.get("sqlite_base64", ""), validate=True)
                if not raw or len(raw) > MAX_BODY:
                    raise ValueError("قاعدة البيانات فارغة أو أكبر من الحد المسموح")
                fd, filename = tempfile.mkstemp(prefix="rizq-chat-", suffix=".sqlite")
                try:
                    with os.fdopen(fd, "wb") as fh:
                        fh.write(raw)
                    validate_database(pathlib.Path(filename))
                    tools = copilot.PosTools(filename, str(ROOT / "results"), confirm=lambda _: False)
                finally:
                    pathlib.Path(filename).unlink(missing_ok=True)
                sid = str(body.get("session", ""))
                with LOCAL_SESSIONS_LOCK:
                    state = LOCAL_SESSIONS.get(sid)
                    if not state or state["user_id"] != user_id:
                        brain = copilot.pick_brain("ollama")
                        if isinstance(brain, copilot.RulesBrain):
                            tools.db.close()
                            raise ValueError("Ollama or a supported local model is unavailable.")
                        if isinstance(brain, copilot.OpenAICompatBrain):
                            for msg in memory.recent(user_id, 12):
                                brain.msgs.append({"role": msg["role"], "content": msg["text"][:4000]})
                        sid = secrets.token_urlsafe(24)
                        state = {"user_id": user_id, "brain": brain, "lock": threading.Lock()}
                        LOCAL_SESSIONS[sid] = state
                try:
                    with state["lock"]:
                        answer = copilot.ask(state["brain"], tools, message, on_event=lambda _: None)
                        memory.append_exchange(user_id, message, answer)
                    self.send_json({"session": sid, "answer": answer, "provider": "ollama",
                                    "model": getattr(state["brain"], "model", ""),
                                    "actions": tools.log[-10:]})
                finally:
                    tools.db.close()
                return
            if path == "/api/chat":
                message = str(body.get("message", "")).strip()
                if not message or len(message) > 5000:
                    raise ValueError("اكتب رسالة من 1 إلى 5000 حرف")
                provider = str(body.get("provider", "auto"))
                if provider not in {"auto", "claude", "ollama", "openai", "compatible", "rules"}:
                    raise ValueError("مزود النموذج غير مدعوم")
                model = str(body.get("model", "")).strip() or None
                sid = str(body.get("session", ""))
                state = SESSIONS.get(sid)
                if not state or state["provider"] != provider or state["model"] != model:
                    sid = secrets.token_urlsafe(18)
                    brain = copilot.pick_brain(provider, model)
                    state = {"provider": provider, "model": model, "brain": brain,
                             "tools": copilot.PosTools(str(ACTIVE_DB), str(ROOT / "results"),
                                                       confirm=lambda _: False)}
                    SESSIONS[sid] = state
                # A browser-provided flag is not proof of admin approval.
                # Until server-side user authorization exists, deny these writes.
                state["tools"].confirm = lambda _: False
                answer = copilot.ask(state["brain"], state["tools"], message, on_event=lambda _: None)
                self.send_json({"session": sid, "answer": answer, "provider": state["brain"].name,
                                "model": getattr(state["brain"], "model", ""),
                                "actions": state["tools"].log[-10:]})
                return
            self.send_json({"error": "not found"}, 404)
        except Exception as e:
            self.send_json({"error": str(e)}, 400)

    def do_OPTIONS(self):
        self.send_json({"ok": True})


def main(port: int = 8000):
    load_dotenv()
    host = "127.0.0.1"
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Rizq is running at http://{host}:{port} (local only). POS: http://{host}:{port}/pos/")
    print("LLM provider status:", json.dumps(provider_status(), ensure_ascii=False))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nRizq stopped.")
    finally:
        server.server_close()
        if DB_TEMP:
            pathlib.Path(DB_TEMP).unlink(missing_ok=True)

