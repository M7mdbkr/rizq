"""Shared security controls: rate limiting, owner 2FA, a spend cap, and alerts.

Standard library only, like the rest of the agent.

What lives here and why:

  RateLimiter   One sliding-window limiter used by every endpoint, not just login.
                Before this, only the PIN check and the code-admin password were
                limited, so an unauthenticated caller could hammer the expensive
                routes and a stolen token had no ceiling at all.

  TOTP          A second factor for the owner, RFC 6238, verified on the server.
                A PIN is four to eight digits typed on a shared till in front of
                staff and customers; it is a convenience credential, not a strong
                one. Owner actions deserve something the shoulder-surfer does not
                have. Enrolment is opt-in and the secret is written 0600.

  spend         A hard ceiling on calls to a *paid* provider. Local models cost
                nothing, so this only bites when an API key is configured -- which
                is exactly when a runaway loop would cost real money.

  alert         High-signal events written to their own file, separate from the
                full audit log, so the owner can see trouble without reading
                thousands of ordinary lines.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import pathlib
import secrets
import struct
import threading
import time

DATA = pathlib.Path(os.environ.get(
    "RIZQ_DATA_DIR", str(pathlib.Path(__file__).resolve().parents[1] / "data")))
TOTP_PATH = DATA / "owner_2fa.json"
ALERT_PATH = DATA / "alerts.jsonl"
SPEND_PATH = DATA / "spend.json"

_LOCK = threading.Lock()


def _secure_dir() -> None:
    DATA.mkdir(parents=True, mode=0o700, exist_ok=True)


# --------------------------------------------------------------------------- rate limiting
class RateLimiter:
    """Sliding-window limiter keyed by (bucket, client)."""

    def __init__(self) -> None:
        self._hits: dict = {}
        self._lock = threading.Lock()

    def check(self, bucket: str, client: str, limit: int, window: float) -> None:
        """Raise RateLimited when `client` exceeds `limit` hits in `window` seconds."""
        now = time.monotonic()
        key = (bucket, client)
        with self._lock:
            recent = [t for t in self._hits.get(key, []) if now - t < window]
            if len(recent) >= limit:
                retry = int(window - (now - recent[0])) + 1
                self._hits[key] = recent
                raise RateLimited(bucket, retry)
            recent.append(now)
            self._hits[key] = recent

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


class RateLimited(Exception):
    def __init__(self, bucket: str, retry_after: int) -> None:
        self.bucket, self.retry_after = bucket, retry_after
        super().__init__(f"طلبات كثيرة. حاول بعد {retry_after} ثانية.")


LIMITER = RateLimiter()

# bucket -> (requests, seconds). Tuned so normal use never notices and a loop does.
LIMITS = {
    "auth": (10, 60),         # sign-in attempts
    "chat": (30, 60),         # questions to the assistant
    "export": (5, 300),       # whole-database exports
    "fill": (20, 60),         # document filling
    "scan": (20, 60),         # integrity scans
    "code": (10, 300),        # code proposals and applies
    "default": (60, 60),
}


def limit(bucket: str, client: str) -> None:
    requests, window = LIMITS.get(bucket, LIMITS["default"])
    LIMITER.check(bucket, client, requests, window)


# --------------------------------------------------------------------------- owner 2FA
def _b32(secret: bytes) -> str:
    return base64.b32encode(secret).decode().rstrip("=")


def totp_code(secret_b32: str, when: float | None = None, step: int = 30) -> str:
    """RFC 6238 six-digit code."""
    pad = secret_b32 + "=" * (-len(secret_b32) % 8)
    key = base64.b32decode(pad, casefold=True)
    counter = int((when if when is not None else time.time()) // step)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return f"{value % 1_000_000:06d}"


def enroll_2fa(user_id: str, issuer: str = "Rizq") -> dict:
    """Create a secret for this user. Returns it ONCE; it is never shown again."""
    secret = _b32(secrets.token_bytes(20))
    _secure_dir()
    with _LOCK:
        store = _read_json(TOTP_PATH, {})
        store[user_id] = {"secret": secret, "created": time.time()}
        _write_json(TOTP_PATH, store)
    uri = (f"otpauth://totp/{issuer}:{user_id}?secret={secret}"
           f"&issuer={issuer}&algorithm=SHA1&digits=6&period=30")
    return {"secret": secret, "otpauth_uri": uri,
            "note": "Scan this in an authenticator app now. It is not shown again."}


def is_2fa_enrolled(user_id: str) -> bool:
    return user_id in _read_json(TOTP_PATH, {})


def verify_2fa(user_id: str, code: str, when: float | None = None) -> bool:
    """Accept the current code or the one either side, for clock drift. Single use."""
    entry = _read_json(TOTP_PATH, {}).get(user_id)
    if not entry or not code or not str(code).strip().isdigit():
        return False
    code = str(code).strip()
    now = when if when is not None else time.time()
    for drift in (-30, 0, 30):
        if hmac.compare_digest(totp_code(entry["secret"], now + drift), code):
            with _LOCK:
                store = _read_json(TOTP_PATH, {})
                used = store.get(user_id, {}).get("used_codes", [])
                if code in used:
                    return False           # replay of a code already spent
                store[user_id]["used_codes"] = (used + [code])[-5:]
                _write_json(TOTP_PATH, store)
            return True
    return False


def disable_2fa(user_id: str) -> bool:
    with _LOCK:
        store = _read_json(TOTP_PATH, {})
        existed = store.pop(user_id, None) is not None
        _write_json(TOTP_PATH, store)
    return existed


# --------------------------------------------------------------------------- spend cap
def spend_state() -> dict:
    return _read_json(SPEND_PATH, {"day": "", "calls": 0})


def note_paid_call(provider: str, max_per_day: int | None = None) -> None:
    """Count one call to a paid provider; raise once the daily cap is reached.

    Local models are free, so they are not counted. The cap exists so a retry loop
    against a metered API cannot run up a bill unattended.
    """
    if provider in ("rules", "ollama", "openai-compatible-local"):
        return
    cap = max_per_day if max_per_day is not None else int(
        os.environ.get("RIZQ_MAX_PAID_CALLS_PER_DAY", "200"))
    today = time.strftime("%Y-%m-%d")
    _secure_dir()
    with _LOCK:
        state = _read_json(SPEND_PATH, {"day": "", "calls": 0})
        if state.get("day") != today:
            state = {"day": today, "calls": 0}
        if state["calls"] >= cap:
            alert("spend_cap_reached", {"provider": provider, "cap": cap})
            raise SpendCapReached(
                f"بلغت الحد اليومي لاستدعاءات المزوّد المدفوع ({cap}). "
                "ارفع RIZQ_MAX_PAID_CALLS_PER_DAY أو استخدم النموذج المحلي.")
        state["calls"] += 1
        _write_json(SPEND_PATH, state)


class SpendCapReached(Exception):
    pass


# --------------------------------------------------------------------------- alerts
ALERTABLE = {
    "repeated_auth_failure", "privileged_denied", "code_applied",
    "data_exported", "2fa_disabled", "spend_cap_reached", "duplicate_pins",
}


def alert(kind: str, detail: dict | None = None) -> None:
    """Record something the owner should actually look at. Never raises."""
    entry = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "kind": kind,
             "detail": detail or {}}
    try:
        _secure_dir()
        with open(ALERT_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        os.chmod(ALERT_PATH, 0o600)
    except OSError:
        pass


def recent_alerts(limit_n: int = 50) -> list:
    try:
        lines = ALERT_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines[-max(1, min(limit_n, 500)):]:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


# --------------------------------------------------------------------------- helpers
def _read_json(path: pathlib.Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: pathlib.Path, value) -> None:
    _secure_dir()
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(value, fh, ensure_ascii=False)


def reset_for_tests() -> None:
    LIMITER.reset()
