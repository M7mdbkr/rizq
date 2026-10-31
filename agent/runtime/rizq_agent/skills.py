"""Skill library for the Rizq agent: read-only capabilities with a MAKER and an independent CHECKER.

A skill answers one kind of owner question (sales, VAT, inventory value, profit, zakat estimate...).
  compute(db, params)  -> the answer (SQL aggregation)
  check(db, params)    -> the same answer re-derived another way (row loop / components)
The answer is returned only when both agree. When they disagree the agent does NOT answer: the data itself is
suspect (broken VAT, wrong totals...), so the case is escalated to a human.

New skills can be learned: `build_learned_skill` takes a SELECT and an independently written second SELECT, runs both,
and saves the skill (after the human confirms) so it runs later without any model call.

Everything here is read-only (PRAGMA query_only); nothing in this file can change money or stock.
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable

TOL = 0.011
VAT_RATE = 0.15
HIJRI_RATE = 0.025
GREGORIAN_RATE = 0.025 * 365 / 354      # ZATCA: 2.5% prorated for a 365-day year (~2.5777%)
REQUIRED = object()


def _latest(db) -> datetime:
    row = db.execute("SELECT MAX(created_at) FROM orders").fetchone()[0]
    return datetime.fromisoformat(row)


def _day(s: str):
    return datetime.fromisoformat(s).date()


def _completed(db) -> list[dict]:
    return [dict(r) for r in db.execute("SELECT * FROM orders WHERE status='completed'")]


def _within(stored: float, expected: float) -> float:
    """Rizq rounds each order to the halala its own way, so a 1-halala gap per order is normal.
    Keep the stored value when it is within that gap; otherwise use the expected one so a real error shows up."""
    return stored if abs(stored - expected) <= TOL else expected


def r2(x: float) -> float:
    return round(x + 1e-9, 2)


# ------------------------------------------------------------------------------------------ built-in skills
def _sales_compute(db, p):
    cut = (_latest(db).date() - timedelta(days=p["days"])).isoformat()
    row = db.execute("SELECT COUNT(*), COALESCE(SUM(final_total),0), COALESCE(SUM(vat_amount),0) FROM orders "
                     "WHERE status='completed' AND date(created_at) > ?", (cut,)).fetchone()
    # `days` is returned so the answer states its own window. A silently wrong period
    # is worse than a wrong number, because nobody notices it.
    return {"days": p["days"], "orders": row[0], "sales": r2(row[1]), "vat": r2(row[2])}


def _sales_check(db, p):
    cut = _latest(db).date() - timedelta(days=p["days"])
    rows = [o for o in _completed(db) if _day(o["created_at"]) > cut]
    # re-derive the charged total from its components; only a gap bigger than rounding counts as a disagreement
    return {"days": p["days"], "orders": len(rows),
            "sales": r2(sum(_within(o["final_total"], o["total"] - o["discount_amount"] + o["vat_amount"]) for o in rows)),
            "vat": r2(sum(o["vat_amount"] for o in rows))}


def _vat_compute(db, p):
    month = p["month"] or _latest(db).strftime("%Y-%m")
    v = db.execute("SELECT COALESCE(SUM(vat_amount),0) FROM orders WHERE status='completed' "
                   "AND strftime('%Y-%m', created_at)=?", (month,)).fetchone()[0]
    return {"month": month, "vat_collected": r2(v)}


def _vat_check(db, p):
    month = p["month"] or _latest(db).strftime("%Y-%m")
    rows = [o for o in _completed(db) if o["created_at"][:7] == month]
    # what VAT SHOULD be: 15% of the net amount after discount
    return {"month": month, "vat_collected": r2(sum(_within(o["vat_amount"], (o["total"] - o["discount_amount"]) * VAT_RATE) for o in rows))}


def _top_compute(db, p):
    cut = (_latest(db).date() - timedelta(days=p["days"])).isoformat()
    rows = db.execute("SELECT COALESCE(pr.name_ar, oi.name, oi.product_id), SUM(oi.quantity) q FROM order_items oi "
                      "JOIN orders o ON o.id=oi.order_id LEFT JOIN products pr ON pr.id=oi.product_id "
                      "WHERE o.status='completed' AND date(o.created_at) > ? GROUP BY oi.product_id "
                      "ORDER BY q DESC, 1 LIMIT ?", (cut, int(p["n"]))).fetchall()
    return {"top": [[r[0], r[1]] for r in rows]}


def _top_check(db, p):
    cut = _latest(db).date() - timedelta(days=p["days"])
    ok = {o["id"] for o in _completed(db) if _day(o["created_at"]) > cut}
    names = {r["id"]: r["name_ar"] for r in db.execute("SELECT id, name_ar FROM products")}
    cnt: Counter = Counter()
    for it in db.execute("SELECT order_id, product_id, quantity, name FROM order_items"):
        if it["order_id"] in ok:
            cnt[(it["product_id"], names.get(it["product_id"]) or it["name"] or it["product_id"])] += it["quantity"]
    ranked = sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0][1]))[: int(p["n"])]
    return {"top": [[k[1], v] for k, v in ranked]}


def _low_stock_compute(db, p):
    rows = db.execute("""SELECT name_ar, stock, min_stock FROM products
                         WHERE status='active' AND stock < min_stock
                         ORDER BY stock ASC, name_ar""").fetchall()
    return {"count": len(rows),
            "items": [[r[0], r[1], r[2]] for r in rows],
            "out_of_stock": sum(1 for r in rows if (r[1] or 0) <= 0)}


def _low_stock_check(db, p):
    """Independently written: pull every active product and filter in Python."""
    short = []
    for name, stock, minimum in db.execute(
            "SELECT name_ar, stock, min_stock FROM products WHERE status='active'").fetchall():
        if not (stock >= minimum):
            short.append([name, stock, minimum])
    short.sort(key=lambda r: (r[1], r[0]))
    return {"count": len(short), "items": short,
            "out_of_stock": len([r for r in short if (r[1] or 0) <= 0])}


def _inv_compute(db, p):
    row = db.execute("SELECT COALESCE(SUM(stock*cost),0), COALESCE(SUM(stock*price),0), COUNT(*) FROM products").fetchone()
    return {"inventory_at_cost": r2(row[0]), "inventory_at_retail": r2(row[1]), "products": row[2]}


def _inv_check(db, p):
    cost = retail = 0.0
    rows = db.execute("SELECT stock, cost, price FROM products").fetchall()
    for s, c, pr in rows:
        cost += (s or 0) * (c or 0)
        retail += (s or 0) * (pr or 0)
    return {"inventory_at_cost": r2(cost), "inventory_at_retail": r2(retail), "products": len(rows)}


def _profit_compute(db, p):
    cut = (_latest(db).date() - timedelta(days=p["days"])).isoformat()
    rev = db.execute("SELECT COALESCE(SUM(final_total - vat_amount),0) FROM orders WHERE status='completed' "
                     "AND date(created_at) > ?", (cut,)).fetchone()[0]
    cogs = db.execute("SELECT COALESCE(SUM(oi.quantity * COALESCE(pr.cost,0)),0) FROM order_items oi "
                      "JOIN orders o ON o.id=oi.order_id LEFT JOIN products pr ON pr.id=oi.product_id "
                      "WHERE o.status='completed' AND date(o.created_at) > ?", (cut,)).fetchone()[0]
    return {"revenue_ex_vat": r2(rev), "cost_of_goods": r2(cogs), "gross_profit": r2(rev - cogs)}


def _profit_check(db, p):
    cut = _latest(db).date() - timedelta(days=p["days"])
    ok = {o["id"]: o for o in _completed(db) if _day(o["created_at"]) > cut}
    cost = {r["id"]: (r["cost"] or 0) for r in db.execute("SELECT id, cost FROM products")}
    rev = sum(o["total"] - o["discount_amount"] for o in ok.values())       # net of VAT, from components
    cogs = sum(it["quantity"] * cost.get(it["product_id"], 0)
               for it in db.execute("SELECT order_id, product_id, quantity FROM order_items") if it["order_id"] in ok)
    return {"revenue_ex_vat": r2(rev), "cost_of_goods": r2(cogs), "gross_profit": r2(rev - cogs)}


def _zakat_compute(db, p):
    inv = db.execute("SELECT COALESCE(SUM(stock*cost),0) FROM products").fetchone()[0]
    base = inv + p["cash"] + p["receivables"] - p["liabilities"]
    rate = HIJRI_RATE if p["calendar"] == "hijri" else GREGORIAN_RATE
    due = 0.0 if base <= 0 or (p["nisab_sar"] is not None and base < p["nisab_sar"]) else base * rate
    return {"inventory_at_cost": r2(inv), "zakat_base": r2(base), "rate_percent": round(rate * 100, 4), "zakat_due": r2(due)}


def _zakat_check(db, p):
    inv = sum((s or 0) * (c or 0) for s, c in db.execute("SELECT stock, cost FROM products"))
    base = inv + p["cash"] + p["receivables"] - p["liabilities"]
    rate = 2.5 if p["calendar"] == "hijri" else 2.5 * 365 / 354
    below_nisab = p["nisab_sar"] is not None and base < p["nisab_sar"]
    due = 0.0 if (base <= 0 or below_nisab) else base * rate / 100
    return {"inventory_at_cost": r2(inv), "zakat_base": r2(base), "rate_percent": round(rate, 4), "zakat_due": r2(due)}


@dataclass
class Skill:
    name: str
    title_ar: str
    pattern: str                         # regex over the owner's message (EN + AR)
    compute: Callable
    check: Callable
    params: dict = field(default_factory=dict)       # name -> (type, default | REQUIRED, question_ar)
    assumptions: list = field(default_factory=list)


SKILLS: dict[str, Skill] = {s.name: s for s in [
    Skill("zakat_estimate", "تقدير الزكاة", r"zakat|زكا", _zakat_compute, _zakat_check,
          {"cash": (float, REQUIRED, "كم النقد + الرصيد البنكي الحالي (ريال)؟ رزق ما يعرفه"),
           "receivables": (float, 0.0, "كم المبالغ اللي لك عند الناس (ديون مستحقة)؟"),
           "liabilities": (float, 0.0, "كم الديون القريبة اللي عليك (موردين، رواتب مستحقة)؟"),
           "calendar": (str, "hijri", "سنتك هجرية أو ميلادية؟ (hijri / gregorian)"),
           "nisab_sar": (float, None, "اختياري: قيمة النصاب اليوم بالريال (85 جرام ذهب)")},
          ["المخزون يُقدَّر بسعر التكلفة من بيانات رزق", "النقد والديون من كلامك أنت لأن رزق ما يسجّلها",
           "هجرية 2.5% | ميلادية ≈ 2.5777% (2.5×365÷354)", "تقدير للمراجعة مع هيئة الزكاة أو محاسب، مو إقرار رسمي"]),
    Skill("profit_estimate", "تقدير الربح", r"profit|margin|ربح|أرباح|ارباح|هامش", _profit_compute, _profit_check,
          {"days": (int, 30, "كم يوم للخلف؟")}, ["الربح الإجمالي = المبيعات بدون ضريبة − تكلفة الأصناف المباعة (بدون رواتب أو إيجار)"]),
    Skill("vat_collected", "الضريبة المحصّلة", r"vat|tax|ضريب", _vat_compute, _vat_check,
          {"month": (str, None, "أي شهر؟ مثال 2026-10")}, ["الضريبة 15% من الصافي بعد الخصم"]),
    # Must precede inventory_value: "المخزون الناقص" contains "مخزون", and match()
    # returns the first pattern that hits, so the shortage wording has to win first.
    Skill("low_stock", "المخزون الناقص", r"low stock|restock|out of stock|running low|ناقص|نواقص|نفد|نفذ|قارب|يحتاج تخزين",
          _low_stock_compute, _low_stock_check, {},
          ["الأصناف المفعّلة اللي كميتها أقل من حد التنبيه"]),
    Skill("inventory_value", "قيمة المخزون", r"inventory|stock value|مخزون", _inv_compute, _inv_check, {}, []),
    Skill("top_products", "الأكثر مبيعًا", r"top|best|أكثر|اكثر|اكتر|الأعلى", _top_compute, _top_check,
          {"days": (int, 30, "كم يوم؟"), "n": (int, 3, "كم صنف؟")}, []),
    Skill("sales_period", "مبيعات فترة", r"sales|revenue|income|مبيع|دخل|إيراد|ايراد", _sales_compute, _sales_check,
          {"days": (int, 7, "كم يوم للخلف؟")}, ["مبيعات الطلبات المكتملة فقط، شاملة الضريبة"]),
]}


def match(text: str) -> str | None:
    """Pick the skill for an owner's message (first pattern that matches; order = specificity)."""
    for s in SKILLS.values():
        if re.search(s.pattern, text, re.I):
            return s.name
    return None


def extract_params(text: str) -> dict:
    """Best-effort numbers from free text (rules brain). An LLM brain passes params itself."""
    out: dict = {}
    for key, pat in (("cash", r"(?:cash|bank|نقد|كاش|بنك|رصيد)\D{0,12}([\d,\.]+)"),
                     ("receivables", r"(?:receivable|owed to me|ديون لي|لي عند|مستحق(?:ات)? لي)\D{0,12}([\d,\.]+)"),
                     ("liabilities", r"(?:ديون\s*(?:علي|عليّ)?|(?:علي|عليّ)\s*ديون|التزامات|liabilit\w*|debts?|\bowe[sd]?)[\s:=,]{0,4}([\d,\.]+)")):
        m = re.search(pat, text, re.I)
        if m:
            try:
                out[key] = float(m.group(1).replace(",", "").rstrip("."))
            except ValueError:
                pass
    if re.search(r"gregorian|ميلادي", text, re.I):
        out["calendar"] = "gregorian"
    # An explicit number always wins: "آخر ٧ أيام" means seven.
    m = re.search(r"(\d+)\s*(?:days|day|يوم|أيام|ايام)", text, re.I)
    if m:
        out["days"] = int(m.group(1))
    else:
        # Otherwise understand the ordinary words for a period. Without this,
        # "كم مبيعات اليوم؟" silently answered for the last seven days.
        for pattern, days in ((r"\btoday\b|اليوم(?!\s*ي)|النهارده|هاليوم", 1),
                              (r"\bthis week\b|\bweek\b|الاسبوع|الأسبوع|هالاسبوع|هالأسبوع", 7),
                              (r"\bthis month\b|\bmonth\b|الشهر|هالشهر", 30),
                              (r"\bthis year\b|\byear\b|السنة|العام|هالسنة", 365)):
            if re.search(pattern, text, re.I):
                out["days"] = days
                break
    return out


def _resolve(spec: dict, given: dict):
    out, missing = {}, []
    for k, (typ, default, question) in spec.items():
        v = (given or {}).get(k)
        if v is None:
            if default is REQUIRED:
                missing.append({"param": k, "question_ar": question})
                continue
            v = default
        if v is not None:
            try:
                v = typ(v)
            except (TypeError, ValueError):
                missing.append({"param": k, "question_ar": f"قيمة غير صالحة لـ {k}. {question}"})
                continue
        out[k] = v
    if "calendar" in out and out["calendar"] not in ("hijri", "gregorian"):
        missing.append({"param": "calendar", "question_ar": "اكتب hijri أو gregorian"})
    return out, missing


def same(a, b) -> bool:
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return abs(a - b) <= TOL
    return a == b


def run(db, name: str, params: dict | None = None) -> dict:
    """Run one built-in skill: maker + independent checker. Never changes the database."""
    sk = SKILLS.get(name)
    if sk is None:
        return {"status": "error", "error": f"skill '{name}' does not exist", "available": sorted(SKILLS)}
    p, missing = _resolve(sk.params, params or {})
    if missing:
        return {"status": "needs_input", "skill": name, "questions": missing}
    db.execute("PRAGMA query_only=ON")
    try:
        a, b = sk.compute(db, p), sk.check(db, p)
    finally:
        db.execute("PRAGMA query_only=OFF")
    if not same(a, b):
        return {"status": "checker_disagrees", "skill": name, "maker": a, "checker": b,
                "action": "escalate: the stored numbers do not reconcile, so no answer is given until a human reviews the data"}
    return {"status": "ok", "skill": name, "title_ar": sk.title_ar, "value": a, "params": p, "assumptions": sk.assumptions}


# ------------------------------------------------------------------------------------------ learned skills
def _select_only(sql: str) -> str | None:
    q = (sql or "").strip().rstrip(";")
    if not q or ";" in q or not re.match(r"(?is)^\s*(select|with)\b", q):
        return None
    if re.search(r"(?i)\b(insert|update|delete|drop|alter|create|replace|attach|pragma|vacuum)\b", q):
        return None
    return q


def _rows(db, sql: str):
    db.execute("PRAGMA query_only=ON")
    try:
        return [[round(v, 2) if isinstance(v, float) else v for v in r] for r in db.execute(sql).fetchmany(200)]
    finally:
        db.execute("PRAGMA query_only=OFF")


def load_learned(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def build_learned_skill(db, path: str, name: str, description: str, sql: str, check_sql: str, confirm=None) -> dict:
    """Maker/checker for a NEW capability: two independently written SELECTs must return the same rows."""
    if not re.fullmatch(r"[a-z][a-z0-9_]{2,40}", name or ""):
        return {"status": "error", "error": "name must be snake_case, 3-41 chars"}
    if name in SKILLS:
        return {"status": "error", "error": f"'{name}' is a built-in skill"}
    a_sql, b_sql = _select_only(sql), _select_only(check_sql)
    if not a_sql or not b_sql:
        return {"status": "rejected", "error": "both queries must be ONE read-only SELECT"}
    if re.sub(r"\s+", " ", a_sql).lower() == re.sub(r"\s+", " ", b_sql).lower():
        return {"status": "rejected", "error": "the checker must be written differently from the maker"}
    try:
        a, b = _rows(db, a_sql), _rows(db, b_sql)
    except Exception as e:      # bad SQL from a model must never crash the agent
        return {"status": "error", "error": f"query failed: {e}"}
    if not same(a, b):
        return {"status": "checker_disagrees", "maker": a[:5], "checker": b[:5], "installed": False}
    ask = confirm or (lambda q: False)
    if not ask(f"\n  [Rizq agent] Install new skill '{name}' ({description})? result={a[:3]} [y/N]: "):
        return {"status": "not_installed", "reason": "the human did not approve", "preview": a[:5]}
    lib = load_learned(path)
    lib[name] = {"description": description, "sql": a_sql, "check_sql": b_sql, "created": datetime.now().isoformat(timespec="seconds")}
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(lib, fh, ensure_ascii=False, indent=1)
    return {"status": "installed", "skill": name, "preview": a[:5]}


def run_learned(db, path: str, name: str) -> dict:
    """Run an installed learned skill. No model involved; maker and checker are re-run every time."""
    sk = load_learned(path).get(name)
    if sk is None:
        return {"status": "error", "error": f"learned skill '{name}' does not exist"}
    a, b = _rows(db, sk["sql"]), _rows(db, sk["check_sql"])
    if not same(a, b):
        return {"status": "checker_disagrees", "maker": a[:5], "checker": b[:5], "action": "escalate"}
    return {"status": "ok", "skill": name, "value": a, "description": sk["description"]}
