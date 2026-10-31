"""Server-enforced roles for the Rizq Agent (challenge CH-05).

Before this module the agent trusted a browser-supplied `user_id`: any local caller
could claim to be the owner and could read another user's chat history, so every write
tool had to be hard-disabled for everyone. Roles are now resolved on the server from a
PIN checked against the cashier's own `users` table, and the policy is enforced in the
agent's hands (`PosTools.call`), not in the prompt -- the same rule as every other Rizq
safety control.

Three parts:
  policy  - tool name -> minimum role. Deny by default: an unknown tool needs `owner`.
  tokens  - opaque server-side sessions. The client never sends a role or a user id.
  audit   - append-only JSONL of every allowed and denied tool call.

Honest limits (do not overstate these in project material):
  - The POS stores PINs in clear text (problem P-4). This module compares them in
    constant time and never logs or returns a PIN, but it cannot fix that storage.
  - Tokens live in this process's memory, so restarting the server ends every session.
  - A PIN is the only factor; anyone holding an owner PIN is the owner to this server.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import pathlib
import secrets
import sqlite3
import threading
import time

ROLE_RANK = {"staff": 1, "owner": 2}
DEFAULT_ROLE_FOR_UNKNOWN_TOOL = "owner"

# Minimum role per agent tool.
#
# `staff` is deliberately narrow: a worker may look up an order they rang up, raise a
# problem, and see what the agent can do. Everything that discloses money across orders
# (scan findings quote totals and VAT; sales_summary and every built-in skill are
# financial; run_sql is free-form read access) or that changes or exports anything is
# owner-only.
TOOL_MIN_ROLE = {
    "get_order": "staff",
    "escalate": "staff",
    "list_skills": "staff",
    "list_features": "staff",
    "list_known_problems": "staff",
    "scan_pos": "owner",
    "run_sql": "owner",
    "sales_summary": "owner",
    "run_skill": "owner",
    "plan_fixes": "owner",
    "apply_fixes": "owner",
    "build_skill": "owner",
    "save_fixed_copy": "owner",
    "list_fill_fields": "owner",
    "fill_template": "owner",
    "export_data": "owner",
    "list_schedule": "owner",
    "schedule_occasion_discount": "owner",
}

# Tools that change or export data. Allowed only for the owner AND only when the
# session was opened with a PIN in this process (never on a role guessed by a client).
WRITE_TOOLS = {"apply_fixes", "build_skill", "save_fixed_copy", "export_data",
               "schedule_occasion_discount"}

TOKEN_TTL_SECONDS = 12 * 60 * 60      # absolute lifetime
TOKEN_IDLE_SECONDS = 60 * 60          # idle timeout
MAX_PIN_FAILURES = 5
PIN_FAILURE_WINDOW = 300

_LOCK = threading.Lock()
_TOKENS: dict[str, dict] = {}
_PIN_FAILURES: dict[str, list[float]] = {}

AUDIT_PATH = pathlib.Path(
    os.environ.get("RIZQ_AUDIT_LOG",
                   str(pathlib.Path(__file__).resolve().parents[1] / "data" / "audit.jsonl")))

# Sessions are stored on disk, not only in memory, so that refreshing the cashier page
# or restarting the server does not throw the user back to a PIN prompt. Only a SHA-256
# of the token is stored: the token itself never touches the disk.
DEFAULT_SESSION_DB = pathlib.Path(__file__).resolve().parents[1] / "data" / "sessions.sqlite"
SESSION_DB = pathlib.Path(os.environ.get("RIZQ_SESSION_DB", str(DEFAULT_SESSION_DB)))


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _session_store() -> sqlite3.Connection:
    SESSION_DB.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    db = sqlite3.connect(str(SESSION_DB), timeout=10)
    db.execute("CREATE TABLE IF NOT EXISTS sessions ("
               "token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL,"
               " role TEXT NOT NULL, created REAL NOT NULL, last_seen REAL NOT NULL)")
    try:
        os.chmod(SESSION_DB, 0o600)
    except OSError:
        pass
    return db


def _store_session(token: str, who: dict, now: float) -> None:
    try:
        with _session_store() as db:
            db.execute("INSERT OR REPLACE INTO sessions VALUES (?,?,?,?,?,?)",
                       (_hash(token), who["user_id"], who["name"], who["role"], now, now))
    except sqlite3.Error:
        pass


def _load_session(token: str) -> dict | None:
    try:
        with _session_store() as db:
            db.execute("DELETE FROM sessions WHERE ? - created > ? OR ? - last_seen > ?",
                       (time.time(), TOKEN_TTL_SECONDS, time.time(), TOKEN_IDLE_SECONDS))
            row = db.execute("SELECT user_id,name,role,created,last_seen FROM sessions "
                             "WHERE token_hash=?", (_hash(token),)).fetchone()
    except sqlite3.Error:
        return None
    if not row:
        return None
    return {"user_id": row[0], "name": row[1], "role": row[2],
            "created": row[3], "last_seen": row[4], "client": "restored"}


def _touch_session(token: str, now: float) -> None:
    try:
        with _session_store() as db:
            db.execute("UPDATE sessions SET last_seen=? WHERE token_hash=?", (now, _hash(token)))
    except sqlite3.Error:
        pass


def _forget_session(token: str) -> None:
    try:
        with _session_store() as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (_hash(token),))
    except sqlite3.Error:
        pass


class PermissionDenied(Exception):
    """Raised when a session may not use a tool. The message is shown to the user."""


# --------------------------------------------------------------------------- policy
def role_allows(role: str, tool: str) -> bool:
    needed = TOOL_MIN_ROLE.get(tool, DEFAULT_ROLE_FOR_UNKNOWN_TOOL)
    return ROLE_RANK.get(role, 0) >= ROLE_RANK[needed]


def denial_message(role: str, tool: str, arabic: bool = True) -> str:
    needed = TOOL_MIN_ROLE.get(tool, DEFAULT_ROLE_FOR_UNKNOWN_TOOL)
    if arabic:
        who = "صاحب المطعم" if needed == "owner" else "الكاشير"
        return f"هذا الطلب يحتاج صلاحية {who}. حسابك الحالي ({role}) ما يملك هذي الصلاحية."
    return f"This action requires the {needed} role; this session is {role}."


def allowed_tools(role: str) -> list[str]:
    return sorted(t for t in TOOL_MIN_ROLE if role_allows(role, t))


# --------------------------------------------------------------------------- login
def _pin_rate_limited(client: str) -> bool:
    recent = [t for t in _PIN_FAILURES.get(client, []) if time.monotonic() - t < PIN_FAILURE_WINDOW]
    _PIN_FAILURES[client] = recent
    return len(recent) >= MAX_PIN_FAILURES


def verify_pin(db_path: str, pin: str) -> dict | None:
    """Return {'user_id','name','role'} for this PIN, or None. Never returns the PIN."""
    if not pin or not isinstance(pin, str) or not (4 <= len(pin) <= 64):
        return None
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        con.row_factory = sqlite3.Row
        rows = con.execute("SELECT id, name, role, pin FROM users").fetchall()
    except sqlite3.Error:
        return None
    finally:
        con.close()
    # Compare against every row so the time taken does not reveal which user matched.
    matches = [r for r in rows
               if hmac.compare_digest(str(r["pin"]), pin) and r["role"] in ROLE_RANK]
    if len(matches) != 1:
        # Zero is a wrong PIN. More than one means two employees share a PIN, so the
        # server cannot tell who is signing in -- refuse rather than pick one.
        return None
    r = matches[0]
    return {"user_id": r["id"], "name": r["name"], "role": r["role"]}


def duplicate_pins(db_path: str) -> list:
    """Employees who share a PIN. Each person must have their own, or the till cannot
    attribute a shift, a repair or an audit entry to the right human."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        con.row_factory = sqlite3.Row
        rows = con.execute("SELECT id, name, pin FROM users").fetchall()
    except sqlite3.Error:
        return []
    finally:
        con.close()
    seen: dict = {}
    for r in rows:
        seen.setdefault(str(r["pin"]), []).append({"user_id": r["id"], "name": r["name"]})
    return [{"users": v} for v in seen.values() if len(v) > 1]


def login(db_path: str, pin: str, client: str = "local") -> dict:
    """Verify a PIN against the cashier database and open a server-side session."""
    with _LOCK:
        if _pin_rate_limited(client):
            raise PermissionDenied("محاولات كثيرة خاطئة. انتظر خمس دقائق.")
    who = verify_pin(db_path, pin)
    if not who:
        with _LOCK:
            _PIN_FAILURES.setdefault(client, []).append(time.monotonic())
        audit(None, "auth.login", allowed=False, detail={"reason": "bad_pin", "client": client})
        raise PermissionDenied("الرقم السري غير صحيح.")
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _LOCK:
        _PIN_FAILURES.pop(client, None)
        _TOKENS[token] = {**who, "created": now, "last_seen": now, "client": client}
    _store_session(token, who, now)
    audit(who, "auth.login", allowed=True, detail={"client": client})
    return {"token": token, "role": who["role"], "name": who["name"], "user_id": who["user_id"]}


def session(token: str) -> dict:
    """Resolve a token to {'user_id','name','role'}; raises if missing or expired."""
    now = time.time()
    with _LOCK:
        state = _TOKENS.get(token or "")
        if not state and token:
            # Not in memory: the server may have restarted since the user signed in.
            state = _load_session(token)
            if state:
                _TOKENS[token] = state
        if not state:
            raise PermissionDenied("انتهت الجلسة. سجّل الدخول في رزق مرة ثانية.")
        if now - state["created"] > TOKEN_TTL_SECONDS or now - state["last_seen"] > TOKEN_IDLE_SECONDS:
            _TOKENS.pop(token, None)
            _forget_session(token)
            raise PermissionDenied("انتهت الجلسة. سجّل الدخول في رزق مرة ثانية.")
        state["last_seen"] = now
    _touch_session(token, now)
    return {"user_id": state["user_id"], "name": state["name"], "role": state["role"]}


def logout(token: str) -> None:
    with _LOCK:
        state = _TOKENS.pop(token or "", None)
    _forget_session(token or "")
    if state:
        audit(state, "auth.logout", allowed=True)


def reset_for_tests() -> None:
    """Clear in-memory test state, and the session store ONLY if it is a test one.

    This used to delete from whatever SESSION_DB pointed at. Two test files do not
    redirect it, so running the suite wiped the real store and signed out every signed-in
    user on the machine -- the agent then answered "enter your PIN" to someone the POS
    was still showing as signed in. A test must never reach production state.
    """
    from . import security          # local import keeps the module dependency one-way
    security.reset_for_tests()
    with _LOCK:
        _TOKENS.clear()
        _PIN_FAILURES.clear()
    if SESSION_DB.resolve() == DEFAULT_SESSION_DB.resolve():
        return                      # refuse to touch the live store
    try:
        with _session_store() as db:
            db.execute("DELETE FROM sessions")
    except sqlite3.Error:
        pass


# --------------------------------------------------------------------------- audit
def audit(who: dict | None, tool: str, allowed: bool, detail: dict | None = None) -> None:
    """Append one line to the audit log. Never raises into the caller's path."""
    entry = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "user_id": (who or {}).get("user_id"),
        "role": (who or {}).get("role"),
        "tool": tool,
        "allowed": bool(allowed),
    }
    if detail:
        entry["detail"] = detail
    try:
        AUDIT_PATH.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        with open(AUDIT_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        os.chmod(AUDIT_PATH, 0o600)
    except OSError:
        pass


def read_audit(limit: int = 50) -> list[dict]:
    try:
        lines = AUDIT_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines[-max(1, min(limit, 500)):]:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
