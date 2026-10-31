"""Mistakes sheet for the REAL Rizq database: plant known mistakes into a copy, run the agent, score it.

Each mutation records ground truth: which entity it touched, which finding kinds it should cause,
and whether the right handling is "auto" (rebuild derived data) or "escalate" (money / ambiguous).

Scenario success = every planted mistake detected on the right entity AND handled right
(auto -> repaired copy equals the pristine DB for that data; escalate -> escalated and NOT silently edited)
AND no spurious findings beyond the pristine baseline AND verification passed.
"""
from __future__ import annotations

import csv
import random
import sqlite3
from datetime import datetime

from .rizq import open_copy, repair, r2, scan

NOW = datetime(2026, 10, 30, 18, 30)    # fixtures were exported ~18:20 UTC on 30 Oct 2026
FIXTURES = {"dev": "fixtures/rizq_dev.sqlite", "held_out": "fixtures/rizq_heldout.sqlite"}
SEED_BASE = {"dev": 0, "held_out": 9_000_000}

# id -> (description EN, description AR, expected handling, finding kinds it should cause)
MUTATIONS = {
    "RZ01_line_total":     ("A line total does not equal price x quantity", "إجمالي سطر لا يساوي السعر × الكمية", "auto", {"line_total_wrong"}),
    "RZ02_item_dropped":   ("An item silently vanished from a paid order", "صنف اختفى بصمت من طلب مدفوع", "escalate", {"item_missing", "stock_movement_extra"}),
    "RZ03_order_lost":     ("Order row lost, items + stock remain", "الطلب انحذف وبقيت أصنافه والمخزون", "escalate", {"order_row_lost", "customer_drift_blocked"}),
    "RZ04_vat_wrong":      ("VAT wrong on an issued invoice", "ضريبة غلط على فاتورة صادرة", "escalate", {"vat_wrong", "customer_drift_blocked"}),
    "RZ05_final_wrong":    ("Charged total does not match the invoice", "المبلغ المحصّل لا يطابق الفاتورة", "escalate", {"final_total_wrong", "customer_drift_blocked"}),
    "RZ06_stock_missing":  ("Sale did not deduct stock", "بيع ما خصم من المخزون", "auto", {"stock_movement_missing"}),
    "RZ07_customer_drift": ("Customer loyalty totals drifted", "إجماليات العميل انحرفت", "auto", {"customer_drift"}),
    "RZ08_unknown_payment": ("Unknown payment method on an order", "طريقة دفع غير معروفة", "escalate", {"unknown_payment_method"}),
    "RZ09_duplicate":      ("Order submitted twice (double tap)", "طلب انرسل مرتين", "escalate", {"duplicate_order"}),
    "RZ10_future_time":    ("Order dated in the future (device clock)", "طلب بتاريخ مستقبلي (ساعة الجهاز)", "escalate", {"future_timestamp"}),
}


def _rows(db, sql, *p):
    return [dict(r) for r in db.execute(sql, p).fetchall()]


def mutate(db: sqlite3.Connection, kind: str, rng: random.Random, used: set) -> dict:
    """Applies one mistake. Returns ground truth {kind, entities, phone}. Avoids touching the same order twice."""
    orders = [o for o in _rows(db, "SELECT * FROM orders WHERE status='completed'") if o["id"] not in used]
    multi = [o for o in orders if db.execute("SELECT COUNT(*) FROM order_items WHERE order_id=?", (o["id"],)).fetchone()[0] >= 2]
    o = rng.choice(multi if kind == "RZ02_item_dropped" else orders)
    used.add(o["id"])
    ents = {o["id"]}
    delta = rng.choice([1.0, 2.5, 5.0, 10.0])
    if kind == "RZ01_line_total":
        it = rng.choice(_rows(db, "SELECT * FROM order_items WHERE order_id=?", o["id"]))
        db.execute("UPDATE order_items SET total=total+? WHERE id=?", (delta, it["id"]))
        ents = {it["id"]}
    elif kind == "RZ02_item_dropped":
        it = rng.choice(_rows(db, "SELECT * FROM order_items WHERE order_id=?", o["id"]))
        db.execute("DELETE FROM order_items WHERE id=?", (it["id"],))
        ents |= {it["product_id"]}
    elif kind == "RZ03_order_lost":
        db.execute("DELETE FROM orders WHERE id=?", (o["id"],))
    elif kind == "RZ04_vat_wrong":
        db.execute("UPDATE orders SET vat_amount=vat_amount+?, final_total=final_total+? WHERE id=?", (delta, delta, o["id"]))
        if o["customer_phone"]:
            db.execute("UPDATE customers SET total_spent=total_spent+? WHERE phone=?", (delta, o["customer_phone"]))
    elif kind == "RZ05_final_wrong":
        db.execute("UPDATE orders SET final_total=final_total+? WHERE id=?", (delta, o["id"]))
    elif kind == "RZ06_stock_missing":
        tx = rng.choice(_rows(db, "SELECT * FROM stock_transactions WHERE transaction_type='sale'"))
        db.execute("DELETE FROM stock_transactions WHERE id=?", (tx["id"],))
        db.execute("UPDATE products SET stock=stock+? WHERE id=?", (-tx["quantity"], tx["product_id"]))
        ents = {tx["product_id"]}
    elif kind == "RZ07_customer_drift":
        c = rng.choice(_rows(db, "SELECT * FROM customers"))
        db.execute("UPDATE customers SET total_spent=total_spent+?, orders_count=orders_count+? WHERE phone=?",
                   (delta, rng.choice([0, 1]), c["phone"]))
        ents = {c["phone"]}
    elif kind == "RZ08_unknown_payment":
        db.execute("PRAGMA ignore_check_constraints=ON")   # simulate data written by an older/other client
        db.execute("UPDATE orders SET payment_method=? WHERE id=?", (rng.choice(["visa", "stc_pay_old", "voucher"]), o["id"]))
    elif kind == "RZ09_duplicate":
        new = o["id"] + "_dup"
        cols = list(o)
        db.execute(f"INSERT INTO orders ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                   tuple(new if k == "id" else o[k] for k in cols))
        for i, it in enumerate(_rows(db, "SELECT * FROM order_items WHERE order_id=?", o["id"])):
            icols = list(it)
            db.execute(f"INSERT INTO order_items ({','.join(icols)}) VALUES ({','.join('?' * len(icols))})",
                       tuple(f"{it['id']}_dup" if k == "id" else new if k == "order_id" else it[k] for k in icols))
            db.execute("INSERT INTO stock_transactions (id, product_id, transaction_type, quantity, notes) VALUES (?,?,'sale',?,?)",
                       (f"st_dup_{i}_{new}", it["product_id"], -it["quantity"], "بيع طلب"))
        if o["customer_phone"]:
            db.execute("UPDATE customers SET orders_count=orders_count+1, total_spent=total_spent+? WHERE phone=?",
                       (o["final_total"], o["customer_phone"]))
        ents = {new}
    elif kind == "RZ10_future_time":
        db.execute("UPDATE orders SET created_at=? WHERE id=?", ("2026-11-02 12:00:00", o["id"]))
    db.commit()
    return {"kind": kind, "entities": ents, "phone": o.get("customer_phone"), "order": o["id"]}


def _snapshot(db) -> dict:
    """What 'correct derived data' means, for comparing a repaired copy with the pristine DB."""
    return {
        "lines": {r["id"]: r2(r["total"]) for r in db.execute("SELECT id,total FROM order_items")},
        "stock_moves": {r["product_id"]: r["q"] for r in db.execute(
            "SELECT product_id, SUM(quantity) q FROM stock_transactions WHERE transaction_type='sale' GROUP BY 1")},
        "stock": {r["id"]: r["stock"] for r in db.execute("SELECT id,stock FROM products")},
        "customers": {r["phone"]: (r["orders_count"], r2(r["total_spent"])) for r in db.execute("SELECT * FROM customers")},
        "orders": {r["id"]: tuple(r) for r in db.execute("SELECT * FROM orders")},
    }


def run_scenario(split: str, i: int, kinds: list[str] | None = None, root: str = ".") -> dict:
    rng = random.Random(SEED_BASE[split] + i)
    path = f"{root}/{FIXTURES[split]}"
    pristine = open_copy(path)
    base = {(f.kind, f.entity) for f in scan(pristine, NOW)}
    db = open_copy(path)
    kinds = kinds or rng.sample(sorted(MUTATIONS), rng.choice([1, 2, 3]))
    used: set = set()
    truth = [mutate(db, k, rng, used) for k in kinds]
    mutated = _snapshot(db)
    before = scan(db, NOW)
    repair(db, before)
    after = scan(db, NOW)
    fixed = _snapshot(db)
    ref = _snapshot(pristine)
    new_before = [f for f in before if (f.kind, f.entity) not in base]
    explained, per = set(), []
    esc_phones = {t["phone"] for t in truth if MUTATIONS[t["kind"]][2] == "escalate" and t["phone"]}
    order_loss = any(t["kind"] == "RZ03_order_lost" for t in truth)
    affected_days = set()
    for t in truth:
        for connection in (db, pristine):
            row = connection.execute("SELECT date(created_at) FROM orders WHERE id=?", (t["order"],)).fetchone()
            if row and row[0]:
                affected_days.add(row[0])
    for t in truth:
        _, _, handling, expect = MUTATIONS[t["kind"]]
        if t["kind"] == "RZ07_customer_drift" and (order_loss or t["entities"] & esc_phones):
            handling, expect = "escalate", {"customer_drift_blocked"}   # correct: don't rebuild aggregates from incomplete data
        hits = [f for f in new_before if f.kind in expect and (f.entity in t["entities"] or f.entity == t["phone"])]
        explained |= {(f.kind, f.entity) for f in hits}
        detected = any(f.kind in expect and f.handling == handling for f in hits)
        if handling == "auto":
            key = {"RZ01_line_total": "lines", "RZ06_stock_missing": "stock_moves", "RZ07_customer_drift": "customers"}[t["kind"]]
            ok = all(fixed[key].get(e) == ref[key].get(e) for e in t["entities"]) and (
                key != "stock_moves" or all(fixed["stock"].get(e) == ref["stock"].get(e) for e in t["entities"]))
        else:   # escalated, and the money/order rows were NOT silently changed by the agent
            ok = any(f.handling == "escalate" for f in hits) and fixed["orders"] == mutated["orders"]
        per.append({"kind": t["kind"], "detected": detected, "handled": detected and ok})
    spurious = [f"{f.kind}:{f.entity}" for f in new_before
                if (f.kind, f.entity) not in explained
                and not (f.kind in ("daily_ledger_drift", "daily_ledger_missing", "daily_ledger_blocked")
                         and (order_loss or f.entity in affected_days))]
    after_keys = {(f.kind, f.entity) for f in before}
    verified = not [f for f in after if f.handling == "auto"] and all((f.kind, f.entity) in after_keys for f in after)
    return {"split": split, "seed": SEED_BASE[split] + i, "mutations": "+".join(t["kind"] for t in truth),
            "per": per, "spurious": spurious, "verified": verified,
            "success": all(p["handled"] for p in per) and not spurious and verified}


def pristine_check(split: str, root: str = ".") -> list[str]:
    """Findings on the untouched DB (should be only REAL issues; reported, not counted as agent errors)."""
    return [f"{f.kind}:{f.entity}" for f in scan(open_copy(f"{root}/{FIXTURES[split]}"), NOW)]


def run_sheet(split: str = "dev", runs: int = 20, root: str = ".") -> list[dict]:
    out = []
    for idx, (kind, (en, ar, handling, _)) in enumerate(MUTATIONS.items()):
        res = [run_scenario(split, idx * 1000 + k, [kind], root) for k in range(runs)]
        ok = sum(r["success"] for r in res)
        bad = next((r for r in res if not r["success"]), None)
        out.append({"id": kind.split("_")[0], "mistake": en, "mistake_ar": ar, "expected": handling, "runs": runs,
                    "passed": ok, "status": "PASS" if ok == runs else "FAIL",
                    "first_failure": "" if bad is None else f"seed={bad['seed']} per={bad['per']} spurious={bad['spurious'][:3]}"})
    return out


def evaluate(split: str, n: int, root: str = ".") -> dict:
    res = [run_scenario(split, 500_000 + i, None, root) for i in range(n)]
    per = [p for r in res for p in r["per"]]
    pct = lambda xs: round(100 * sum(xs) / len(xs), 1) if xs else None
    return {"split": split, "scenarios": n, "planted_mistakes": len(per),
            "success_%": pct([r["success"] for r in res]), "detected_%": pct([p["detected"] for p in per]),
            "handled_%": pct([p["handled"] for p in per]), "spurious_findings": sum(len(r["spurious"]) for r in res),
            "verify_passed_%": pct([r["verified"] for r in res])}


def write_csv(rows: list[dict], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
