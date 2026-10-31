"""Turn a verified skill result into a sentence a restaurant owner would read.

The numbers were always right; the agent just printed them as JSON:

    - مبيعات فترة: {"days": 1, "orders": 16, "sales": 2343.7, "vat": 305.7}

which reads like debug output, not an assistant. The same result should read:

    مبيعات اليوم: 2,343.70 ر.س من 16 طلب، منها 305.70 ر.س ضريبة.

Nothing here computes anything. It only phrases what a skill already verified, so a
wrong sentence can never invent a number -- and if a skill returns a shape this module
does not know, it falls back to listing the values rather than hiding them.
"""
from __future__ import annotations

SAR = "ر.س"


def money(value) -> str:
    try:
        return f"{float(value):,.2f} {SAR}"
    except (TypeError, ValueError):
        return str(value)


def count(value) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value)


def period(days) -> str:
    """A window in words, so '7' never shows up as a bare number in a sentence."""
    try:
        days = int(days)
    except (TypeError, ValueError):
        return ""
    return {1: "اليوم", 7: "آخر ٧ أيام", 14: "آخر أسبوعين",
            30: "آخر شهر", 90: "آخر ٣ أشهر", 365: "آخر سنة"}.get(days, f"آخر {days} يوم")


def _sales(v: dict) -> str:
    when = period(v.get("days"))
    head = f"مبيعات {when}" if when else "المبيعات"
    line = f"{head}: {money(v.get('sales'))} من {count(v.get('orders'))} طلب"
    if v.get("vat") is not None:
        line += f"، منها {money(v['vat'])} ضريبة"
    return line + "."


def _vat(v: dict) -> str:
    return f"الضريبة المحصّلة في {v.get('month', '')}: {money(v.get('vat_collected'))}."


def _inventory(v: dict) -> str:
    return (f"قيمة المخزون: {money(v.get('inventory_at_cost'))} بالتكلفة، "
            f"و{money(v.get('inventory_at_retail'))} بسعر البيع، على {count(v.get('products'))} صنف.")


def _low_stock(v: dict) -> str:
    items = v.get("items") or []
    n, out = v.get("count", len(items)), v.get("out_of_stock", 0)
    if not n:
        return "كل الأصناف ضمن المستوى الآمن ✓"
    head = f"{count(n)} صنف يحتاج إعادة تخزين"
    if out:
        head += f"، منها {count(out)} نفد تمامًا"
    lines = [head + ":"]
    for row in items[:8]:
        name, stock, minimum = (list(row) + [None, None])[:3]
        state = "نفد" if (stock or 0) <= 0 else f"باقي {count(stock)} من {count(minimum)}"
        lines.append(f"  • {name} — {state}")
    if len(items) > 8:
        lines.append(f"  • و{count(len(items) - 8)} صنف غيرها")
    return "\n".join(lines)


def _top(v: dict) -> str:
    rows = v.get("top") or []
    if not rows:
        return "ما فيه مبيعات في هذه الفترة."
    lines = ["الأكثر مبيعًا:"]
    for i, row in enumerate(rows, 1):
        name, qty = (list(row) + [None])[:2]
        lines.append(f"  {i}. {name} — {count(qty)} قطعة")
    return "\n".join(lines)


def _profit(v: dict) -> str:
    rev, cogs, gross = v.get("revenue_ex_vat"), v.get("cost_of_goods"), v.get("gross_profit")
    line = (f"الربح الإجمالي: {money(gross)}\n"
            f"  الإيراد بدون ضريبة {money(rev)} − تكلفة الأصناف المباعة {money(cogs)}")
    try:
        if float(rev):
            line += f"\n  هامش الربح: {float(gross) / float(rev) * 100:.1f}%"
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    return line


def _zakat(v: dict) -> str:
    return f"الزكاة التقديرية: {money(v.get('zakat_due'))}."


PHRASERS = {
    "sales_period": _sales,
    "vat_collected": _vat,
    "inventory_value": _inventory,
    "low_stock": _low_stock,
    "top_products": _top,
    "profit_estimate": _profit,
    "zakat_estimate": _zakat,
}


def _fallback(title: str, value) -> str:
    """A skill with no phraser (a learned one) still reads as a list, not as JSON."""
    if isinstance(value, dict):
        parts = []
        for k, v in value.items():
            if isinstance(v, (list, tuple)):
                v = "، ".join(str(x[0]) if isinstance(x, (list, tuple)) and x else str(x) for x in v[:5])
            elif isinstance(v, float):
                v = f"{v:,.2f}"
            elif isinstance(v, int):
                v = f"{v:,}"
            parts.append(f"  • {k}: {v}")
        return (title + ":\n" + "\n".join(parts)) if parts else title
    return f"{title}: {value}"


def skill_answer(result: dict) -> str:
    """Phrase an `ok` skill result. Assumptions are appended as a short note."""
    title = result.get("title_ar") or result.get("skill") or "النتيجة"
    value = result.get("value")
    phraser = PHRASERS.get(result.get("skill"))
    if phraser and isinstance(value, dict):
        try:
            text = phraser(value)
        except Exception:
            text = _fallback(title, value)
    else:
        text = _fallback(title, value)
    notes = [a for a in (result.get("assumptions") or []) if a]
    if notes:
        text += "\n" + "\n".join("  — " + n for n in notes)
    return text
