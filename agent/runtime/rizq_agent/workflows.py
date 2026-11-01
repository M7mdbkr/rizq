"""Workflows: fixed sequences of the agent's own checked tools, ending in a plain report.

Each workflow reads the POS through the same role-gated tools as the chat (so the same
permissions and audit log apply), uses only skills whose two independent computations
agree, changes nothing, and is written to the audit log. Repairs and discounts stay
behind the owner's confirm button; a workflow only tells the owner what needs doing.

Triggers: the "Workflows" button in the assistant, `/api/workflows/run`, or the CLI
`python3 -m rizq_agent workflow <name> <db> [--every MINUTES]`.
"""
from __future__ import annotations

from . import permissions, phrase, rizq

TITLES = {
    "day_close": "إقفال اليوم",
    "reconcile": "مطابقة المدفوعات",
    "low_stock": "تنبيه المخزون",
    "daily_report": "تقرير اليوم",
    "zakat": "تذكير الزكاة",
}

PAYMENT_KINDS = {"unknown_payment_method", "final_total_wrong", "order_row_lost", "duplicate_order", "vat_wrong"}


def _skill(tools, name, **params) -> tuple:
    out = tools.call("run_skill", {"name": name, "params": params})
    if out.get("status") == "ok":
        return out, phrase.skill_answer(out)
    if out.get("status") == "needs_input":
        return out, None
    return out, None


def _scan_summary(tools) -> tuple:
    scan = tools.call("scan_pos", {})
    if "error" in scan:
        return scan, "ما قدرت أفحص البيانات."
    real = {k: v for k, v in scan.get("by_kind", {}).items() if k != "daily_ledger_missing"}
    if not real:
        return scan, "الفحص: ما فيه مشاكل في الطلبات."
    parts = "، ".join(f"{n} {kind}" for kind, n in real.items())
    return scan, f"الفحص: {sum(real.values())} ملاحظة ({parts})، {scan.get('must_escalate', 0)} منها تحتاج قرارك."


def run(name: str, tools, params: dict | None = None) -> dict:
    if name not in TITLES:
        raise ValueError("سير عمل غير معروف.")
    params = params or {}
    lines, steps = [], []

    def step(label, ok=True):
        steps.append({"step": label, "ok": ok})

    if name in ("day_close", "daily_report"):
        sales, text = _skill(tools, "sales_period", days=1)
        step("مبيعات اليوم", bool(text)); lines.append(text or "ما قدرت أتحقق من مبيعات اليوم.")
        if sales.get("status") == "ok" and sales["value"].get("orders"):
            profit, ptext = _skill(tools, "profit_estimate", days=1)
            step("الربح", bool(ptext)); ptext and lines.append(ptext.split("\n")[0])
            _, top = _skill(tools, "top_products", days=1, n=3)
            step("الأكثر مبيعًا", bool(top)); top and lines.append(top)
        _, low = _skill(tools, "low_stock")
        step("المخزون", bool(low)); low and lines.append(low.split("\n")[0].rstrip(":") + ".")
        if name == "day_close":
            scan, summary = _scan_summary(tools)
            step("فحص البيانات", "error" not in scan); lines.append(summary)
            # Verification: the sales figure is re-read and must agree with the first read.
            again, _ = _skill(tools, "sales_period", days=1)
            same = again.get("value") == sales.get("value")
            step("التحقق", same)
            lines.append("تم التحقق من الأرقام مرتين." if same else "الأرقام تغيّرت أثناء الإقفال، أعد التشغيل.")

    elif name == "reconcile":
        summary = tools.call("sales_summary", {"days": 1})
        methods = summary.get("by_payment_method", []) if "error" not in summary else []
        step("طرق الدفع اليوم", "error" not in summary)
        if methods:
            lines.append("طرق الدفع اليوم:")
            lines += [f"{m.get('name') or m['payment_method']}: {phrase.money(m['total'])} من {m['n']} طلب" for m in methods]
        else:
            lines.append("ما فيه مدفوعات اليوم لحد الحين.")
        scan = tools.call("scan_pos", {})
        step("مطابقة الطلبات", "error" not in scan)
        issues = [f for f in getattr(tools, "findings", None) or [] if f.kind in PAYMENT_KINDS]
        if issues:
            lines.append(f"فيه {len(issues)} طلب يحتاج مراجعة في الدفع:")
            lines += [f"{f.entity}: {rizq.ACTIONS_AR.get(f.kind) or f.human_action_ar or f.kind}" for f in issues[:8]]
        else:
            lines.append("كل الطلبات مطابقة لطرق دفع معروفة ومبالغها صحيحة.")

    elif name == "low_stock":
        _, low = _skill(tools, "low_stock")
        step("المخزون", bool(low)); lines.append(low or "ما قدرت أتحقق من المخزون.")

    elif name == "zakat":
        cash = params.get("cash")
        out, text = _skill(tools, "zakat_estimate", **({"cash": cash} if cash not in (None, "") else {}))
        step("الزكاة", out.get("status") in ("ok", "needs_input"))
        if out.get("status") == "needs_input":
            lines.append("عشان أحسب الزكاة: " + "، ".join(q["question_ar"] for q in out.get("questions", [])))
        else:
            lines.append(text or "ما قدرت أحسب الزكاة.")

    report = "\n".join(l for l in lines if l)
    if getattr(tools, "actor", None) is not None:
        permissions.audit(tools.actor, f"workflow.{name}", allowed=True,
                          detail={"steps": [s["step"] for s in steps], "ok": all(s["ok"] for s in steps)})
    return {"name": name, "title": TITLES[name], "steps": steps, "report": report,
            "ok": all(s["ok"] for s in steps)}
