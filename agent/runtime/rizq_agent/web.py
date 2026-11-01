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
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

from .reply import app_help as reply_help, language as reply_lang, smalltalk as reply_smalltalk
from . import code_agent, confidence, copilot, export, fill, importer, memory, models, payments, workflows, permissions, rizq, schedule, security, skills, triage, zatca

ROOT = pathlib.Path(os.environ.get("RIZQ_ASSET_RUNTIME", str(pathlib.Path(__file__).resolve().parents[1])))
WEB = ROOT / "web"
POS = ROOT.parent.parent / "rizq-pos"
RESULTS = pathlib.Path(os.environ.get("RIZQ_RESULTS_DIR", str(ROOT / "results")))
DEFAULT_DB = ROOT / "fixtures" / "rizq_dev.sqlite"
ACTIVE_DB = pathlib.Path(os.environ.get("RIZQ_DB", str(DEFAULT_DB))).resolve()
DB_TEMP: str | None = None
SESSIONS: dict[str, dict] = {}
LOCAL_SESSIONS: dict[str, dict] = {}
LOCAL_SESSIONS_LOCK = threading.Lock()
MAX_BODY = 30 * 1024 * 1024
CODE_LOCK = threading.Lock()
CODE_FAILURES: dict[str, list[float]] = {}
# The key is deliberately held only in the running server, never returned or written to disk.
AI_SETTINGS = {"provider": "auto", "model": "", "key": "", "version": 0}
AI_SETTINGS_LOCK = threading.Lock()
# Saved on this device only (0600, outside git) so a restart keeps the owner's choice.
# The key is sent to nobody except the provider the owner picked.
AI_SETTINGS_FILE = pathlib.Path(os.environ.get("RIZQ_AI_SETTINGS_FILE", str(
    pathlib.Path(os.environ.get("RIZQ_DATA_DIR", str(ROOT / "data"))) / "ai_settings.json")))


def save_ai_settings() -> None:
    with AI_SETTINGS_LOCK:
        data = {k: AI_SETTINGS[k] for k in ("provider", "model", "key")}
    AI_SETTINGS_FILE.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    tmp = AI_SETTINGS_FILE.with_name(AI_SETTINGS_FILE.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh)
    os.replace(tmp, AI_SETTINGS_FILE)
    os.chmod(AI_SETTINGS_FILE, 0o600)


def load_ai_settings() -> bool:
    try:
        data = json.loads(AI_SETTINGS_FILE.read_text())
        configure_ai(str(data.get("provider", "auto")), str(data.get("model", "")), str(data.get("key", "")))
        return True
    except (OSError, ValueError):
        return False
DEFAULT_MODELS = {"openai": "gpt-4o-mini", "claude": "claude-sonnet-4-6"}

# The owner's rule: the AI model answers only while the shop is online. Offline, the
# verified skills and the quick actions keep working and no model is called at all.
_NET = {"at": 0.0, "online": False}
OFFLINE_AR = ("ما فيه إنترنت الحين، فالمساعد الذكي متوقف عشان ما يجاوب بشيء ما يقدر يتحقق منه. "
              "تقدر تستخدم الاختيارات السريعة فوق: كم اليوم، وش عندي، سوّ خصم، جرد، أضف موظف.")
OFFLINE_EN = ("There is no internet right now, so the AI assistant is paused. "
              "The quick actions above still work: today's sales, stock, discount, stock count, add staff.")


def internet_available() -> bool:
    """Cached for 20 s. RIZQ_ONLINE=1/0 pins the answer (tests, kiosks)."""
    pinned = os.environ.get("RIZQ_ONLINE")
    if pinned in ("0", "1"):
        return pinned == "1"
    now = time.time()
    if now - _NET["at"] < 20:
        return _NET["online"]
    import socket
    online = False
    for host, port in (("1.1.1.1", 443), ("8.8.8.8", 53)):
        try:
            socket.create_connection((host, port), timeout=1.5).close()
            online = True
            break
        except OSError:
            continue
    _NET.update(at=now, online=online)
    return online


def provider_error_message(error: Exception, model: str) -> str:
    """Turn a cloud provider failure into a message the owner can act on. Never echoes the key."""
    if not isinstance(error, urllib.error.HTTPError):
        return "تعذر الاتصال بالمزود. تأكد من الإنترنت ثم أعد المحاولة."
    try:
        detail = json.loads(error.read() or b"{}").get("error", "")
        detail = str(detail.get("message", "") if isinstance(detail, dict) else detail)[:200]
    except Exception:
        detail = ""
    if error.code in (401, 403):
        # The provider's own text can include part of the key, so it is not shown.
        return "المفتاح غير صحيح أو ليس له صلاحية عند المزود. تأكد منه في إعدادات المساعد."
    if error.code == 404:
        return f"النموذج \"{model}\" غير متاح لهذا المفتاح. اترك اسم النموذج فارغًا أو صحّحه."
    if error.code == 429:
        return f"المزود رفض الطلب: تجاوزت حد الاستخدام أو انتهى الرصيد. {detail}".strip()
    return f"المزود رفض الطلب ({error.code}). {detail}".strip()


def check_ai_key(provider: str, model: str, key: str) -> None:
    """Send the smallest real request, shaped like the chat's, so a wrong key, wrong model
    or empty balance is reported when the owner saves it rather than in the middle of a chat."""
    if provider not in DEFAULT_MODELS:
        return
    model = model or DEFAULT_MODELS[provider]
    security.note_paid_call(provider)
    if provider == "claude":
        base = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
        request = urllib.request.Request(
            base.rstrip("/") + "/v1/messages",
            data=json.dumps({"model": model, "max_tokens": 16,
                             "messages": [{"role": "user", "content": "ping"}]}).encode(),
            headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    else:
        request = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=json.dumps({"model": model, "temperature": 0, "max_tokens": 16,
                             "messages": [{"role": "user", "content": "ping"}]}).encode(),
            headers={"Authorization": f"Bearer {key}", "content-type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30):
            return
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ValueError(provider_error_message(e, model)) from None


def configure_ai(provider: str, model: str, key: str, check=None) -> None:
    if provider not in ("auto", "openai", "claude", "ollama", "rules"):
        raise ValueError("مزود النموذج غير مدعوم")
    if len(model) > 120 or len(key) > 512 or any(c in key for c in "\r\n"):
        raise ValueError("إعدادات النموذج غير صالحة")
    if provider in ("openai", "claude") and not key.strip():
        raise ValueError("أدخل مفتاح API للمزود المختار")
    if check:
        check(provider, model.strip(), key.strip())     # raises before anything is stored
    with AI_SETTINGS_LOCK:
        AI_SETTINGS.update(provider=provider, model=model.strip(), key=key.strip(),
                           version=AI_SETTINGS["version"] + 1)


def configured_brain():
    with AI_SETTINGS_LOCK:
        cfg = dict(AI_SETTINGS)
    provider, model, key = cfg["provider"], cfg["model"], cfg["key"]
    if provider == "openai":
        brain = copilot.OpenAICompatBrain("https://api.openai.com/v1", model or DEFAULT_MODELS["openai"], key, paid=True)
    elif provider == "claude":
        brain = copilot.ClaudeBrain(model or DEFAULT_MODELS["claude"], key=key)
    else:
        # Automatic cashier routing stays on this device even if cloud credentials
        # happen to exist in the environment. Cloud use requires an explicit choice.
        brain = copilot.pick_brain("ollama" if provider == "auto" else provider, model or None)
    return brain, provider, cfg["version"]


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
        "claude": {"available": bool(copilot._load_key()), "model": os.environ.get("RIZQ_CLAUDE_MODEL", "claude-sonnet-4-6")},
        "openai": {"available": bool(os.environ.get("OPENAI_API_KEY")), "model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini")},
        "compatible": {"available": bool(os.environ.get("RIZQ_LLM_BASE_URL")), "model": os.environ.get("RIZQ_LLM_MODEL", ""),
                       "base_url": os.environ.get("RIZQ_LLM_BASE_URL", "")},
    }


# RFC1918 plus IPv6 unique-local. Deliberately NOT ipaddress.is_private, which also
# returns True for documentation and reserved ranges such as 203.0.113.0/24 -- broader
# than "a device on this shop's Wi-Fi", which is the only thing we mean to allow.
_LAN_NETWORKS = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")


def _is_local_address(hostname: str) -> bool:
    """True only for loopback, link-local, or a real private LAN address."""
    if hostname in ("localhost", "::1"):
        return True
    import ipaddress
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    if ip.is_loopback or ip.is_link_local:
        return True
    return any(ip in ipaddress.ip_network(net) for net in _LAN_NETWORKS
               if ipaddress.ip_network(net).version == ip.version)


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

    def request_token(self, body: dict | None = None) -> str:
        """Session token from the Authorization header, the body, or the query string."""
        header = self.headers.get("Authorization", "")
        if header.lower().startswith("bearer "):
            return header[7:].strip()
        if body and body.get("token"):
            return str(body["token"])
        query = urlparse(self.path).query
        for part in query.split("&"):
            if part.startswith("token="):
                return unquote(part[len("token="):])
        return ""

    def guard(self, bucket: str) -> None:
        """Rate-limit this request by client address and route class."""
        security.limit(bucket, self.client_address[0])

    def actor(self, body: dict | None = None, require_owner: bool = False) -> dict:
        """Resolve the caller's verified role, or raise PermissionDenied."""
        who = permissions.session(self.request_token(body))
        if require_owner and who["role"] != "owner":
            raise permissions.PermissionDenied(permissions.denial_message(who["role"], "owner_only"))
        return who

    def do_GET(self):
        try:
            self._do_GET()
        except security.RateLimited as e:
            self.send_json({"error": str(e), "code": "rate_limited",
                            "retry_after": e.retry_after}, 429)
        except security.SpendCapReached as e:
            self.send_json({"error": str(e), "code": "spend_cap"}, 402)
        except permissions.PermissionDenied as e:
            self.send_json({"error": str(e), "code": "forbidden"}, 403)

    def _do_GET(self):
        path = unquote(urlparse(self.path).path)
        if path == "/api/net/status":
            self.send_json({"online": internet_available()})
            return
        if path == "/api/health":
            try:
                validate_database(ACTIVE_DB)
                db_ok = True
            except Exception:
                db_ok = False
            # An unauthenticated caller gets liveness only. Database filenames and
            # which providers are configured are useful to an attacker and to nobody
            # else, so they need a signed-in session.
            try:
                self.actor()
                self.send_json({"ok": True,
                                "database": {"ready": db_ok, "name": ACTIVE_DB.name,
                                             "source": "browser export" if DB_TEMP else "fixture/default"},
                                "providers": provider_status()})
            except permissions.PermissionDenied:
                self.send_json({"ok": True, "database": {"ready": db_ok}})
            return
        if path == "/api/skills":
            learned = skills.load_learned(str(RESULTS / "skills_learned.json"))
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
            self.actor(require_owner=True)      # tax figures are owner-only
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
        if path == "/api/export":
            actor = self.actor(require_owner=True)
            self.guard("export")
            name, blob = export.build(str(ACTIVE_DB), who=f"{actor['name']} ({actor['role']})",
                                      audit_path=permissions.AUDIT_PATH,
                                      memory_path=memory.PATH,
                                      escalations_path=RESULTS / "escalations.jsonl")
            permissions.audit(actor, "export_data", allowed=True, detail={"bytes": len(blob)})
            security.alert("data_exported", {"user_id": actor["user_id"], "bytes": len(blob)})
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
            self.send_header("Content-Length", str(len(blob)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(blob)
            return
        if path == "/api/fill/fields":
            self.actor(require_owner=True)
            self.send_json({"fields": fill.available_fields(),
                            "syntax": "{{field}} or {{field:days=30}}"})
            return
        if path == "/api/schedule":
            self.actor(require_owner=True)
            self.send_json({"jobs": schedule.jobs(), "waiting_for_you": schedule.pending(),
                            "occasions": {k: v["name_ar"] for k, v in schedule.OCCASIONS.items()}})
            return
        if path == "/api/schedule/run-due":
            actor = self.actor(require_owner=True)
            db = rizq.open_copy(str(ACTIVE_DB))
            try:
                ran = schedule.run_due(db)
            finally:
                db.close()
            if ran:
                permissions.audit(actor, "schedule.run_due", allowed=True, detail={"jobs": len(ran)})
            self.send_json({"ran": ran})
            return
        if path == "/api/scan":
            try:
                self.actor(require_owner=True)
                self.guard("scan")  # findings quote order totals and VAT
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
            except permissions.PermissionDenied:
                raise
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
            if path in ("/api/ai/settings", "/api/ai/settings/status", "/api/ai/models", "/api/ai/models/pull",
                        "/api/payments/device", "/api/payments/device/status"):
                actor = self.actor(body, require_owner=True)
                self.guard("auth")
                host = self.headers.get("Host", "")
                origin = self.headers.get("Origin", "")
                hostname = urlparse("http://" + host).hostname
                import ipaddress
                try:
                    local_client = ipaddress.ip_address(self.client_address[0]).is_loopback
                except ValueError:
                    local_client = False
                if not local_client or hostname not in ("localhost", "127.0.0.1", "::1") or origin != "http://" + host:
                    raise permissions.PermissionDenied("إعداد المفتاح متاح من هذا الجهاز فقط ومن صفحة رزق.")
                if path == "/api/ai/models":
                    self.send_json(models.status())
                    return
                if path == "/api/payments/device/status":
                    cfg = payments.load()
                    self.send_json({**cfg, "label": payments.TYPES.get(cfg.get("type"), ""), "types": payments.TYPES})
                    return
                if path == "/api/payments/device":
                    result = payments.save_and_test(body)
                    permissions.audit(actor, "payments.device", allowed=True, detail={"type": result["type"]})
                    self.send_json(result)
                    return
                if path == "/api/ai/models/pull":
                    name = str(body.get("name", "")).strip()
                    try:
                        download = models.start_pull(name)
                    except ValueError as e:
                        self.send_json({"error": str(e), "alternative": getattr(e, "alternative", None)}, 400)
                        return
                    permissions.audit(actor, "ai.model_pull", allowed=True, detail={"model": name})
                    self.send_json({"model": name, "download": download})
                    return
                verified = False
                if path == "/api/ai/settings":
                    configure_ai(str(body.get("provider", "auto")), str(body.get("model", "")), str(body.get("key", "")),
                                 check=check_ai_key)
                    save_ai_settings()
                    with LOCAL_SESSIONS_LOCK:
                        LOCAL_SESSIONS.clear()
                    permissions.audit(actor, "ai.configure", allowed=True, detail={"provider": AI_SETTINGS["provider"]})
                    verified = AI_SETTINGS["provider"] in DEFAULT_MODELS
                with AI_SETTINGS_LOCK:
                    self.send_json({"provider": AI_SETTINGS["provider"], "model": AI_SETTINGS["model"],
                                    "configured": bool(AI_SETTINGS["key"]), "verified": verified})
                return
            if path == "/api/auth/login":
                self.guard("auth")
                # The PIN is checked on the server against the cashier's own users
                # table. The client never tells us who it is or what role it has.
                raw = base64.b64decode(body.get("sqlite_base64", ""), validate=True)
                if not raw or len(raw) > MAX_BODY:
                    raise ValueError("قاعدة البيانات فارغة أو أكبر من الحد المسموح")
                fd, filename = tempfile.mkstemp(prefix="rizq-auth-", suffix=".sqlite")
                try:
                    with os.fdopen(fd, "wb") as fh:
                        fh.write(raw)
                    validate_database(pathlib.Path(filename))
                    who = permissions.verify_pin(filename, str(body.get("pin", "")))
                    # A second factor, when the owner has enrolled one, is checked
                    # BEFORE any session is created.
                    if who and security.is_2fa_enrolled(who["user_id"]):
                        if not security.verify_2fa(who["user_id"], str(body.get("otp", ""))):
                            permissions.audit(who, "auth.2fa", allowed=False)
                            security.alert("repeated_auth_failure",
                                           {"user_id": who["user_id"], "factor": "totp"})
                            raise permissions.PermissionDenied(
                                "رمز التحقق الثنائي غير صحيح." if body.get("otp")
                                else "هذا الحساب يحتاج رمز التحقق الثنائي.")
                    out = permissions.login(filename, str(body.get("pin", "")),
                                            client=self.client_address[0])
                finally:
                    pathlib.Path(filename).unlink(missing_ok=True)
                out["allowed_tools"] = permissions.allowed_tools(out["role"])
                out["two_factor"] = security.is_2fa_enrolled(out["user_id"])
                self.send_json(out)
                return
            if path == "/api/auth/2fa/enroll":
                actor = self.actor(body, require_owner=True)
                self.guard("auth")
                self.send_json(security.enroll_2fa(actor["user_id"]))
                return
            if path == "/api/auth/2fa/disable":
                actor = self.actor(body, require_owner=True)
                self.guard("auth")
                if not security.verify_2fa(actor["user_id"], str(body.get("otp", ""))):
                    raise permissions.PermissionDenied("أدخل رمز التحقق الحالي لإيقاف الخاصية.")
                removed = security.disable_2fa(actor["user_id"])
                security.alert("2fa_disabled", {"user_id": actor["user_id"]})
                self.send_json({"ok": True, "was_enrolled": removed})
                return
            if path == "/api/alerts":
                self.actor(body, require_owner=True)
                self.send_json({"alerts": security.recent_alerts(int(body.get("limit", 50)))})
                return
            if path == "/api/auth/logout":
                permissions.logout(str(body.get("token", "")))
                self.send_json({"ok": True})
                return
            if path == "/api/auth/whoami":
                actor = self.actor(body)
                self.send_json({**actor, "allowed_tools": permissions.allowed_tools(actor["role"])})
                return
            if path == "/api/audit":
                actor = self.actor(body, require_owner=True)
                self.send_json({"entries": permissions.read_audit(int(body.get("limit", 50)))})
                return
            if path == "/api/schedule/discount":
                actor = self.actor(body, require_owner=True)
                job = schedule.schedule_occasion_discount(
                    str(body.get("occasion", "")), float(body.get("percent", 0)),
                    str(body.get("code", "")), actor["user_id"], int(body.get("days_before", 1)))
                permissions.audit(actor, "schedule.discount", allowed=True, detail=job["params"])
                self.send_json(job)
                return
            if path == "/api/schedule/apply":
                actor = self.actor(body, require_owner=True)
                ok = schedule.mark_applied(str(body.get("action_id", "")), actor["user_id"])
                permissions.audit(actor, "schedule.apply", allowed=ok,
                                  detail={"action_id": body.get("action_id")})
                self.send_json({"ok": ok})
                return
            if path == "/api/schedule/remove":
                actor = self.actor(body, require_owner=True)
                self.send_json({"ok": schedule.remove(str(body.get("job_id", "")))})
                return
            if path == "/api/fill":
                self.guard("fill")
                actor = self.actor(body, require_owner=True)
                text = body.get("text")
                if not isinstance(text, str) or not text.strip():
                    raise ValueError("أرسل نص المستند المراد تعبئته")
                if len(text) > 500_000:
                    raise ValueError("المستند أكبر من الحد المسموح (500 كيلوبايت نص)")
                db = rizq.open_copy(str(ACTIVE_DB))
                try:
                    result = fill.fill(db, text)
                finally:
                    db.close()
                permissions.audit(actor, "fill_template", allowed=True,
                                  detail={"filled": result["filled_count"],
                                          "unresolved": len(result["unresolved"])})
                self.send_json(result)
                return
            if path == "/api/memory/history":
                # Server-resolved identity: a caller can only read its own history.
                actor = self.actor(body)
                conv = str(body.get("conversation") or "")[:64] or None
                self.send_json({"messages": memory.history(actor["user_id"], conv)})
                return
            if path == "/api/workflows/run":
                actor = self.actor(body, require_owner=True)
                self.guard("chat")
                raw = base64.b64decode(body.get("sqlite_base64", ""), validate=True)
                if not raw or len(raw) > MAX_BODY:
                    raise ValueError("قاعدة البيانات فارغة أو أكبر من الحد المسموح")
                fd, filename = tempfile.mkstemp(prefix="rizq-flow-", suffix=".sqlite")
                try:
                    with os.fdopen(fd, "wb") as fh:
                        fh.write(raw)
                    validate_database(pathlib.Path(filename))
                    tools = copilot.PosTools(filename, str(RESULTS), confirm=lambda _: False, actor=actor)
                finally:
                    pathlib.Path(filename).unlink(missing_ok=True)
                try:
                    result = workflows.run(str(body.get("name", "")), tools, body.get("params") or {})
                finally:
                    tools.db.close()
                conv = str(body.get("conversation") or "")[:64] or None
                if conv and all(c.isalnum() or c in "-_" for c in conv):
                    memory.append_exchange(actor["user_id"], result["title"], result["report"], conv)
                self.send_json(result)
                return
            if path == "/api/import/products":
                actor = self.actor(body, require_owner=True)
                self.guard("chat")
                raw = base64.b64decode(body.get("file_base64", ""), validate=True)
                result = importer.products(str(body.get("filename", "")), raw)
                permissions.audit(actor, "import.products", allowed=True,
                                  detail={"items": len(result["items"]), "skipped": len(result["skipped"])})
                self.send_json(result)
                return
            if path == "/api/memory/conversations":
                actor = self.actor(body)
                self.send_json({"conversations": memory.conversations(actor["user_id"])})
                return
            if path in ("/api/code/propose", "/api/code/apply"):
                # Changing code is the most dangerous thing the agent can do, so it needs
                # BOTH a signed-in owner AND the separate code-administrator password.
                # Neither factor alone is enough. Authorisation is checked first so an
                # unauthenticated caller gets a straight 403 rather than a 400 that makes
                # the route look merely malformed.
                actor = self.actor(body, require_owner=True)
                if os.environ.get("RIZQ_DESKTOP") == "1":
                    raise ValueError("مساعد تعديل الكود متاح فقط في نسخة المصدر للمطورين.")
                self.guard("code")
                # Same-origin in the real sense: the page asking must be this server.
                # Pinning to 127.0.0.1 would break the till's tablets, so compare the
                # Origin against the Host actually used, and require that host to be
                # loopback or a private LAN address -- never a public one.
                origin = self.headers.get("Origin", "")
                host = self.headers.get("Host", "")
                if not host or origin not in ("http://" + host, "https://" + host):
                    raise ValueError("Code changes require a same-origin request from Rizq.")
                hostname = host.rsplit(":", 1)[0].strip("[]")
                if not _is_local_address(hostname):
                    raise ValueError("Code changes are only allowed from this device or its local network.")
                client = self.client_address[0]
                with CODE_LOCK:
                    failures = [t for t in CODE_FAILURES.get(client, []) if time.monotonic() - t < 300]
                    CODE_FAILURES[client] = failures
                    if len(failures) >= 5:
                        self.send_json({"error": "Too many failed attempts. Try again in five minutes."}, 429)
                        return
                    if not code_agent.authenticate(str(body.get("password", ""))):
                        failures.append(time.monotonic())
                        permissions.audit(actor, "code." + path.rsplit("/", 1)[1], allowed=False,
                                          detail={"reason": "code_admin_password"})
                        self.send_json({"error": "Code administrator authentication failed or is not configured."}, 403)
                        return
                    CODE_FAILURES.pop(client, None)
                    if path == "/api/code/propose":
                        target = str(body.get("path", ""))
                        permissions.audit(actor, "code.propose", allowed=True, detail={"path": target})
                        self.send_json(code_agent.generate(target, str(body.get("request", ""))))
                    else:
                        proposal = str(body.get("proposal", ""))
                        permissions.audit(actor, "code.apply", allowed=True, detail={"proposal": proposal})
                        security.alert("code_applied", {"user_id": actor["user_id"]})
                        self.send_json(code_agent.apply(proposal))
                return
            if path == "/api/database/import":
                self.actor(body, require_owner=True)
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
                self.actor(body, require_owner=True)
                if DB_TEMP:
                    pathlib.Path(DB_TEMP).unlink(missing_ok=True)
                DB_TEMP, ACTIVE_DB = None, DEFAULT_DB
                SESSIONS.clear()
                self.send_json({"ok": True, "name": ACTIVE_DB.name})
                return
            if path == "/api/chat/local":
                self.guard("chat")
                message = str(body.get("message", "")).strip()
                actor = self.actor(body)
                user_id = actor["user_id"]
                if not message or len(message) > 5000:
                    raise ValueError("اكتب رسالة من 1 إلى 5000 حرف")
                raw = base64.b64decode(body.get("sqlite_base64", ""), validate=True)
                if not raw or len(raw) > MAX_BODY:
                    raise ValueError("قاعدة البيانات فارغة أو أكبر من الحد المسموح")
                # Derived-data repairs now need BOTH a server-verified owner role and an
                # explicit per-message approval. The flag alone is not authorization.
                approved = bool(body.get("approve_repairs")) and actor["role"] == "owner"
                conv = str(body.get("conversation") or "")[:64] or None
                if conv and not all(c.isalnum() or c in "-_" for c in conv):
                    raise ValueError("معرّف المحادثة غير صالح")
                fd, filename = tempfile.mkstemp(prefix="rizq-chat-", suffix=".sqlite")
                try:
                    with os.fdopen(fd, "wb") as fh:
                        fh.write(raw)
                    validate_database(pathlib.Path(filename))
                    tools = copilot.PosTools(filename, str(RESULTS),
                                             confirm=lambda _: approved, actor=actor)
                finally:
                    pathlib.Path(filename).unlink(missing_ok=True)
                # Route deterministically first. The rules brain answers from the same
                # verified skills in well under a second and scored 90% on the held-out
                # owner requests; a local model on this hardware took 370-736s per
                # question and garbled figures. So the model is used only where the
                # rules genuinely run out -- which is decision D6/D13, not a shortcut.
                # RIZQ_FORCE_MODEL=1 overrides, for comparing the two paths.
                small = reply_smalltalk(message) or reply_help(message)
                if small:
                    memory.append_exchange(user_id, message, small, conv)
                    self.send_json({"session": str(body.get("session", "")), "answer": small, "provider": "rules",
                                    "model": "", "actions": []})
                    tools.db.close()
                    return
                verdict = triage.classify(message)
                force = os.environ.get("RIZQ_FORCE_MODEL") == "1"
                with AI_SETTINGS_LOCK:
                    explicit_model = AI_SETTINGS["provider"] in ("auto", "openai", "claude", "ollama")
                # A configured model can interpret an otherwise unrecognized request;
                # hard refusals still run before the model inside copilot.ask.
                needs_model = (force or verdict["action"] == triage.BUILD or
                               (explicit_model and verdict["action"] == triage.REFUSE and not verdict.get("hard")))
                sid = str(body.get("session", ""))
                if needs_model and AI_SETTINGS["provider"] != "rules" and not internet_available():
                    answer = OFFLINE_AR if reply_lang(message) != "en" else OFFLINE_EN
                    memory.append_exchange(user_id, message, answer, conv)
                    self.send_json({"session": sid, "answer": answer, "provider": "offline", "model": "",
                                    "offline": True, "actions": []})
                    tools.db.close()
                    return
                try:
                    if not needs_model:
                        answer = copilot.ask(copilot.RulesBrain(), tools, message,
                                             on_event=lambda _: None)
                        verdict = tools.last_verdict
                        memory.append_exchange(user_id, message, answer, conv)
                        self.send_json({"session": sid, "answer": answer, "provider": "rules",
                                        "model": "", "actions": tools.log[-10:],
                                        "confidence": verdict.as_dict() if verdict else None})
                        return
                    with LOCAL_SESSIONS_LOCK:
                        state = LOCAL_SESSIONS.get(sid)
                        with AI_SETTINGS_LOCK:
                            current_version = AI_SETTINGS["version"]
                        if not state or state["user_id"] != user_id or state.get("settings_version") != current_version:
                            brain, selected_provider, version = configured_brain()
                            if isinstance(brain, copilot.RulesBrain):
                                if verdict["action"] == triage.REFUSE and not force:
                                    answer = copilot.ask(brain, tools, message, on_event=lambda _: None)
                                    memory.append_exchange(user_id, message, answer, conv)
                                    self.send_json({"session": sid, "answer": answer, "provider": "rules",
                                                    "model": "", "actions": tools.log[-10:]})
                                    return
                                raise ValueError("هذا الطلب يحتاج نموذجًا. اضبط مفتاح API من إعدادات المساعد أو شغّل Ollama.")
                            if isinstance(brain, copilot.OpenAICompatBrain):
                                for msg in memory.recent(user_id, 12, conv):
                                    brain.msgs.append({"role": msg["role"], "content": msg["text"][:4000]})
                            sid = secrets.token_urlsafe(24)
                            state = {"user_id": user_id, "brain": brain, "provider": selected_provider,
                                     "settings_version": version, "lock": threading.Lock()}
                            LOCAL_SESSIONS[sid] = state
                    started = time.time()
                    with state["lock"]:
                        try:
                            answer = copilot.ask(state["brain"], tools, message, on_event=lambda _: None)
                        except (urllib.error.URLError, TimeoutError) as e:
                            # A key revoked or out of credit after it was saved.
                            if state["provider"] not in DEFAULT_MODELS:
                                raise
                            raise ValueError(provider_error_message(e, getattr(state["brain"], "model", ""))) from None
                    used = getattr(state["brain"], "model", "")
                    verdict = confidence.score_model_answer(answer, tools.log)
                    escalated_to = None
                    if not verdict.approved:
                        # Not good enough to present as fact. Try a larger model once
                        # before giving up; the owner asked for exactly this ladder.
                        bigger = confidence.bigger_model(used, models.quick(confidence.installed_models())) if time.time() - started < 45 and state["provider"] in ("auto", "ollama") and not getattr(state["brain"], "paid", False) and not isinstance(state["brain"], copilot.ClaudeBrain) else None
                        if bigger:
                            try:
                                # Same tools, so the retry reads the same snapshot under
                                # the same role; only the model changes.
                                brain2 = copilot.pick_brain("ollama", bigger)
                                before = len(tools.log)
                                answer2 = copilot.ask(brain2, tools, message, on_event=lambda _: None)
                                v2 = confidence.score_model_answer(answer2, tools.log[before:])
                                if v2.score > verdict.score:
                                    answer, verdict, escalated_to = answer2, v2, bigger
                            except Exception:
                                pass                      # keep the first answer
                    # The owner asked for the answer only: no confidence line, no routing notes.
                    # Both stay in the response fields for the audit trail.
                    answer = (answer or "").strip()
                    memory.append_exchange(user_id, message, answer, conv)
                    self.send_json({"session": sid, "answer": answer, "provider": state["brain"].name,
                                    "model": escalated_to or used,
                                    "confidence": verdict.as_dict(),
                                    "escalated_to": escalated_to,
                                    "actions": tools.log[-10:]})
                finally:
                    tools.db.close()
                return
            if path == "/api/chat":
                self.guard("chat")
                message = str(body.get("message", "")).strip()
                actor = self.actor(body)
                if not message or len(message) > 5000:
                    raise ValueError("اكتب رسالة من 1 إلى 5000 حرف")
                provider = str(body.get("provider", "auto"))
                if provider not in {"auto", "claude", "ollama", "openai", "compatible", "rules"}:
                    raise ValueError("مزود النموذج غير مدعوم")
                model = str(body.get("model", "")).strip() or None
                sid = str(body.get("session", ""))
                state = SESSIONS.get(sid)
                if (not state or state["provider"] != provider or state["model"] != model
                        or state.get("actor", {}).get("user_id") != actor["user_id"]):
                    sid = secrets.token_urlsafe(18)
                    brain = copilot.pick_brain(provider, model)
                    state = {"provider": provider, "model": model, "actor": actor,
                             "brain": brain,
                             "tools": copilot.PosTools(str(ACTIVE_DB), str(RESULTS),
                                                       confirm=lambda _: False, actor=actor)}
                    SESSIONS[sid] = state
                # The role is now resolved on the server from a PIN, so an explicit
                # per-message approval from a verified owner is real authorization.
                approved = bool(body.get("approve_repairs")) and actor["role"] == "owner"
                state["tools"].actor = actor
                state["tools"].confirm = lambda _: approved
                answer = copilot.ask(state["brain"], state["tools"], message, on_event=lambda _: None)
                self.send_json({"session": sid, "answer": answer, "provider": state["brain"].name,
                                "model": getattr(state["brain"], "model", ""),
                                "actions": state["tools"].log[-10:]})
                return
            self.send_json({"error": "not found"}, 404)
        except security.RateLimited as e:
            self.send_json({"error": str(e), "code": "rate_limited",
                            "retry_after": e.retry_after}, 429)
        except security.SpendCapReached as e:
            self.send_json({"error": str(e), "code": "spend_cap"}, 402)
        except permissions.PermissionDenied as e:
            self.send_json({"error": str(e), "code": "forbidden"}, 403)
        except Exception as e:
            self.send_json({"error": str(e)}, 400)

    def do_OPTIONS(self):
        self.send_json({"ok": True})


def local_ip() -> str | None:
    """This machine's address on the LAN, for the till's tablets and phones."""
    import socket
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))     # no packet is sent
        return probe.getsockname()[0]
    except OSError:
        return None
    finally:
        probe.close()


def main(port: int = 8000, host: str | None = None):
    load_dotenv()
    load_ai_settings()
    # The live app answers about the real today, matching the dashboard.
    skills.CLOCK = os.environ.get("RIZQ_CLOCK", "real")
    # Default stays loopback. Binding to every interface is opt-in, because it puts
    # the till on the shop's network: that is the point for an iPad, but it should
    # never happen by accident.
    host = host or os.environ.get("RIZQ_HOST", "127.0.0.1")
    server = ThreadingHTTPServer((host, port), Handler)
    if host in ("0.0.0.0", "::"):
        ip = local_ip()
        print(f"Rizq is on the network. This device: http://127.0.0.1:{port}/pos/")
        if ip:
            print(f"                       other devices: http://{ip}:{port}/pos/")
        print("Anyone on this Wi-Fi can reach the sign-in page; every PIN, role and rate "
              "limit still applies. Use 127.0.0.1 only if you do not want that.")
    else:
        print(f"Rizq is running at http://{host}:{port} (this device only). "
              f"POS: http://{host}:{port}/pos/")
    print("LLM provider status:", json.dumps(provider_status(), ensure_ascii=False))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nRizq stopped.")
    finally:
        server.server_close()
        if DB_TEMP:
            pathlib.Path(DB_TEMP).unlink(missing_ok=True)

