"""Fill the blanks in a document with figures from the restaurant's own data.

The owner receives a form -- an accountant's monthly sheet, a VAT working, a landlord's
statement -- and has to copy numbers into it by hand. Here they hand the file to the
agent instead: every `{{placeholder}}` is replaced with the real figure.

The rule that makes this trustworthy: **every value comes from a verified skill**. Each
figure is computed twice, by a maker query and an independently written checker, and if
the two disagree the blank is left untouched and reported. The agent never writes a
number into someone's tax form that it could not verify, and it never invents one.

Placeholders look like:

    {{sales_total}}                 use the field's default period
    {{sales_total:days=30}}         override a parameter
    {{vat_collected:month=2026-10}}

Unknown names and unverified values are returned in `unresolved` with the reason, and
left exactly as they were in the document so a human can see what is missing.
"""
from __future__ import annotations

import datetime
import re

from . import skills

TOKEN = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*(?::\s*([^}]*))?\}\}")

# field -> (skill, key inside the skill's value, Arabic label, default params)
FIELDS: dict = {
    "sales_total":        ("sales_period", "sales", "إجمالي المبيعات", {"days": 30}),
    "sales_orders":       ("sales_period", "orders", "عدد الطلبات", {"days": 30}),
    "sales_vat":          ("sales_period", "vat", "ضريبة المبيعات", {"days": 30}),
    "vat_collected":      ("vat_collected", "vat_collected", "الضريبة المحصّلة", {}),
    "inventory_at_cost":  ("inventory_value", "inventory_at_cost", "قيمة المخزون بالتكلفة", {}),
    "inventory_at_retail": ("inventory_value", "inventory_at_retail", "قيمة المخزون بالبيع", {}),
    "products_count":     ("inventory_value", "products", "عدد الأصناف", {}),
    "revenue_ex_vat":     ("profit_estimate", "revenue_ex_vat", "الإيراد بدون ضريبة", {"days": 30}),
    "cost_of_goods":      ("profit_estimate", "cost_of_goods", "تكلفة المبيعات", {"days": 30}),
    "gross_profit":       ("profit_estimate", "gross_profit", "الربح الإجمالي", {"days": 30}),
    "low_stock_count":    ("low_stock", "count", "عدد الأصناف الناقصة", {}),
    "out_of_stock_count": ("low_stock", "out_of_stock", "عدد الأصناف المنتهية", {}),
    "zakat_due":          ("zakat_estimate", "zakat_due", "الزكاة التقديرية", {}),
}

# Values that are not a skill result.
META = {
    "today": "تاريخ اليوم",
    "generated_at": "وقت الإنشاء",
}


def available_fields() -> dict:
    """What an owner may put in a template, for the UI and for the agent's tool."""
    out = {}
    for name, (skill, key, label_ar, defaults) in FIELDS.items():
        sk = skills.SKILLS.get(skill)
        out[name] = {"label_ar": label_ar, "skill": skill,
                     "params": {k: v for k, v in defaults.items()},
                     "accepts": sorted((sk.params or {}).keys()) if sk else []}
    for name, label_ar in META.items():
        out[name] = {"label_ar": label_ar, "skill": None, "params": {}, "accepts": []}
    return out


def _parse_params(raw: str | None) -> dict:
    """`days=30, month=2026-10` -> {'days': '30', 'month': '2026-10'}"""
    params: dict = {}
    for part in (raw or "").split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            k, v = k.strip(), v.strip()
            if k:
                params[k] = v
    return params


def _format(value) -> str:
    if isinstance(value, float):
        return f"{value:,.2f}"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, (list, tuple)):
        return "، ".join(str(v[0]) if isinstance(v, (list, tuple)) and v else str(v) for v in value)
    return str(value)


def fill(db, text: str, now: datetime.datetime | None = None) -> dict:
    """Replace every known placeholder in `text`. Never raises on a bad placeholder."""
    now = now or datetime.datetime.now()
    filled: dict = {}
    unresolved: list = []
    cache: dict = {}

    def resolve(name: str, raw_params: str | None):
        key = (name, raw_params or "")
        if key in cache:
            return cache[key]
        if name in META:
            value = (now.strftime("%Y-%m-%d") if name == "today"
                     else now.strftime("%Y-%m-%d %H:%M"))
            cache[key] = ("ok", value)
            return cache[key]
        if name not in FIELDS:
            cache[key] = ("unknown_field", None)
            return cache[key]
        skill, value_key, _label, defaults = FIELDS[name]
        params = dict(defaults)
        params.update(_parse_params(raw_params))
        result = skills.run(db, skill, params)
        status = result.get("status")
        if status != "ok":
            # needs_input / checker_disagrees -> leave the blank, say why. Never guess.
            cache[key] = (status or "error", result)
            return cache[key]
        value = result["value"]
        if isinstance(value, dict):
            if value_key not in value:
                cache[key] = ("missing_value", result)
                return cache[key]
            value = value[value_key]
        cache[key] = ("ok", _format(value))
        return cache[key]

    def replace(match: re.Match) -> str:
        name, raw_params = match.group(1), match.group(2)
        status, value = resolve(name, raw_params)
        if status == "ok":
            filled[match.group(0)] = value
            return value
        reason = {
            "unknown_field": "no such field",
            "needs_input": "needs information Rizq does not store",
            "checker_disagrees": "the independent checker disagreed, so no number was written",
            "missing_value": "the skill did not return that value",
        }.get(status, str(status))
        unresolved.append({"placeholder": match.group(0), "field": name, "reason": reason})
        return match.group(0)            # leave the blank visible

    output = TOKEN.sub(replace, text)
    return {"text": output,
            "filled": filled,
            "filled_count": len(filled),
            "unresolved": unresolved,
            "verified": "every value was produced by a skill whose independent checker agreed"}
