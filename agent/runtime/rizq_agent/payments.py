"""Payment devices: one interface, a simulator for tests and demos, and a network terminal slot.

A receipt is issued only after the device confirms the payment. Unconfirmed payments are
re-checked a few times; if still unconfirmed there is no receipt, the order stays unpaid and
the owner gets an alert with the evidence. The same payment id can never produce two
receipts, and an amount that differs from the order total is escalated, never adjusted.

Real terminals (mada terminals, gateway SDKs) plug in as adapters with the same three
methods. No vendor protocol is invented here: the network adapter only proves the device is
reachable on the shop network. Not verified on hardware.
"""
from __future__ import annotations

import json
import os
import pathlib
import socket
import threading
import time

CONFIG = pathlib.Path(os.environ.get("RIZQ_DATA_DIR", str(pathlib.Path(__file__).resolve().parents[1] / "data"))) / "payment_device.json"
TYPES = {"none": "بدون جهاز (تسجيل يدوي)", "simulator": "جهاز تجريبي (محاكاة)", "network": "جهاز دفع على الشبكة (IP)"}
_LOCK = threading.Lock()
RECEIPTS: dict = {}            # payment_id -> receipt, for idempotency


class SimulatorAdapter:
    """Scripted outcomes so every path can be tested: approved, approved after N checks, never."""
    name = "simulator"

    def __init__(self, outcome: str = "approved", confirm_after: int = 0, amount_offset: float = 0.0):
        self.outcome, self.confirm_after, self.offset = outcome, confirm_after, amount_offset
        self.payments: dict = {}

    def start_payment(self, order_id: str, amount: float) -> str:
        pid = f"sim-{order_id}"
        self.payments[pid] = {"amount": round(amount + self.offset, 2), "checks": 0}
        return pid

    def get_status(self, payment_id: str) -> dict:
        p = self.payments[payment_id]
        p["checks"] += 1
        if self.outcome == "never":
            return {"status": "pending", "amount": p["amount"]}
        if p["checks"] <= self.confirm_after:
            return {"status": "pending", "amount": p["amount"]}
        return {"status": "approved" if self.outcome == "approved" else "declined", "amount": p["amount"]}

    def refund(self, payment_id: str) -> dict:
        return {"status": "refunded", "payment_id": payment_id}


class NetworkTerminalAdapter:
    """A terminal on the shop network. Only reachability is implemented; the vendor's
    payment protocol is added when the device and its SDK are in hand."""
    name = "network"

    def __init__(self, host: str, port: int):
        self.host, self.port = host, int(port)

    def test(self, timeout: float = 2.0) -> None:
        socket.create_connection((self.host, self.port), timeout=timeout).close()

    def start_payment(self, order_id, amount):
        raise NotImplementedError("بروتوكول الجهاز يُضاف مع حزمة المورّد (غير متحقق على جهاز حقيقي).")

    get_status = refund = start_payment


def load() -> dict:
    try:
        return json.loads(CONFIG.read_text())
    except (OSError, ValueError):
        return {"type": "none"}


def save_and_test(cfg: dict) -> dict:
    """Validate, test the connection, then save. Nothing secret is stored."""
    kind = str(cfg.get("type", "none"))
    if kind not in TYPES:
        raise ValueError("نوع الجهاز غير مدعوم.")
    clean = {"type": kind}
    if kind == "network":
        host = str(cfg.get("host", "")).strip()
        try:
            port = int(cfg.get("port") or 0)
        except ValueError:
            port = 0
        import ipaddress
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            raise ValueError("اكتب عنوان IP الجهاز، مثل 192.168.1.50") from None
        if not (ip.is_private or ip.is_loopback) or not 0 < port < 65536:
            raise ValueError("الجهاز لازم يكون على شبكة المحل (IP داخلي) ومنفذ بين 1 و65535.")
        try:
            NetworkTerminalAdapter(host, port).test()
        except OSError:
            raise ValueError(f"ما قدرت أوصل للجهاز على {host}:{port}. تأكد إنه شغّال وعلى نفس الشبكة.") from None
        clean.update(host=host, port=port)
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(clean))
    return {**clean, "label": TYPES[kind], "connected": kind != "none"}


def take_payment(adapter, order_id: str, total: float, checks: int = 4, wait: float = 1.0, sleep=time.sleep) -> dict:
    """Ask the device for the money; issue the receipt only on a confirmed, exact payment."""
    payment_id = adapter.start_payment(order_id, total)
    with _LOCK:
        if payment_id in RECEIPTS:
            return {"status": "duplicate", "receipt": RECEIPTS[payment_id], "payment_id": payment_id}
    evidence = []
    for attempt in range(checks):
        state = adapter.get_status(payment_id)
        evidence.append({"check": attempt + 1, **state})
        if state["status"] == "approved":
            if round(float(state["amount"]), 2) != round(float(total), 2):
                return {"status": "escalated", "payment_id": payment_id, "evidence": evidence,
                        "alert": f"المبلغ المدفوع {state['amount']} لا يساوي إجمالي الطلب {total}. لم تصدر فاتورة."}
            with _LOCK:
                if payment_id in RECEIPTS:
                    return {"status": "duplicate", "receipt": RECEIPTS[payment_id], "payment_id": payment_id}
                RECEIPTS[payment_id] = {"order_id": order_id, "amount": round(total, 2), "payment_id": payment_id}
                return {"status": "paid", "receipt": RECEIPTS[payment_id], "payment_id": payment_id, "evidence": evidence}
        if state["status"] == "declined":
            return {"status": "declined", "payment_id": payment_id, "evidence": evidence,
                    "alert": "الجهاز رفض الدفع. الطلب بقي غير مدفوع."}
        sleep(wait * (2 ** attempt))                      # back off before the next check
    return {"status": "unconfirmed", "payment_id": payment_id, "evidence": evidence,
            "alert": f"الدفع ما تأكد بعد {checks} محاولات. ما صدرت فاتورة والطلب بقي غير مدفوع."}
