"""Rizq Agent for the REAL Rizq POS database (the SQLite file Rizq keeps in the browser's IndexedDB).

Rules below are copied from Rizq's own checkout code (pt / Re / Le in the bundle):
  line total = round(unit_price * qty, 2)          subtotal = round(sum(lines), 2)
  discount   = percent -> round(subtotal*v/100, 2) | fixed -> min(v, subtotal)
  VAT        = round((subtotal - discount) * rate, 2)   final = round(net + VAT, 2)
  every non-custom line -> one 'sale' stock movement of -qty; customers aggregate their orders by phone.

Loop: scan (invariants) -> findings -> diagnose -> repair ONLY derived data on a COPY (dry-run + audit SQL)
      -> verify the copy (auto findings gone, no new findings) -> escalate the rest with exact numbers.
Money on an issued invoice (VAT, final total) is NEVER edited: ZATCA needs a credit/debit note, so a human decides.
The original database is opened read-only and never modified.
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

EPS = 0.011
VAT_RATE = 0.15


def r2(x: float) -> float:
    return round(x + 1e-9, 2)


@dataclass
class Finding:
    kind: str
    entity: str
    handling: str            # "auto" (derived data, safe to rebuild) | "escalate" (money / ambiguous / unknown)
    label: str               # challenge-level diagnosis
    detail: dict = field(default_factory=dict)
    human_action: str = ""
    human_action_ar: str = ""


# kind -> (challenge label, default handling, human action template)
KINDS = {
    "line_total_wrong":       ("data_corruption", "auto", "line total rebuilt from unit price x quantity"),
    "item_missing":           ("silent_drop", "escalate", "an item vanished from a paid order: check the kitchen ticket / receipt copy and re-add it"),
    "order_row_lost":         ("silent_drop", "escalate", "items and stock were recorded but the order row is gone: re-create the order from the receipt; payment method unknown"),
    "order_without_items":    ("silent_drop", "escalate", "a paid order has no items: check the receipt and restore the items"),
    "vat_wrong":              ("data_corruption", "escalate", "VAT on an issued invoice is wrong: issue a ZATCA credit/debit note for the difference"),
    "final_total_wrong":      ("data_corruption", "escalate", "the charged total does not match the invoice lines: verify the payment and issue a correction note"),
    "stock_movement_missing": ("sync_desync", "auto", "missing 'sale' stock movement restored and stock deducted"),
    "stock_movement_extra":   ("sync_desync", "escalate", "stock was deducted without a sale: check for waste, staff meals or theft"),
    "customer_drift":         ("ledger_drift", "auto", "customer totals recomputed from their orders"),
    "daily_ledger_drift":     ("ledger_drift", "auto", "daily ledger rebuilt from completed orders"),
    "daily_ledger_blocked":   ("ledger_drift", "escalate", "ledger day includes orders under investigation; reconcile after resolving those orders"),
    "customer_drift_blocked": ("ledger_drift", "escalate", "customer totals disagree, but some of their orders are under investigation: recompute after those are resolved"),
    "duplicate_order":        ("unknown", "escalate", "two identical orders at the same moment: confirm with the cashier whether one is a double submit, then void it"),
    "unknown_payment_method": ("unknown", "escalate", "payment method not configured in Rizq: confirm how this order was paid"),
    "future_timestamp":       ("unknown", "escalate", "order dated in the future: check the device clock"),
}


ACTIONS_AR = {'line_total_wrong': 'أُعيد حساب إجمالي السطر من السعر × الكمية',
    'item_missing': 'صنف اختفى من طلب مدفوع: راجع تذكرة المطبخ أو نسخة الفاتورة وأرجع الصنف',
    'order_row_lost': 'الأصناف والمخزون مسجّلة لكن الطلب نفسه انحذف: أعد إنشاء الطلب من الفاتورة (طريقة الدفع غير معروفة)',
    'order_without_items': 'طلب مدفوع بدون أصناف: راجع الفاتورة وأرجع الأصناف',
    'vat_wrong': 'ضريبة القيمة المضافة غلط في فاتورة صادرة: أصدر إشعار دائن/مدين لزاتكا بالفرق',
    'final_total_wrong': 'المبلغ المحصّل لا يطابق أسطر الفاتورة: تحقق من الدفع وأصدر إشعار تصحيح',
    'stock_movement_missing': 'أُعيدت حركة البيع المفقودة وخُصمت الكمية من المخزون',
    'stock_movement_extra': 'انخصم مخزون بدون بيع: تحقق من الهدر أو وجبات الموظفين أو السرقة',
    'customer_drift': 'أُعيد حساب إجماليات العميل من طلباته',
    'daily_ledger_drift': 'أُعيد بناء دفتر اليومية من الطلبات المكتملة',
    'daily_ledger_blocked': 'دفتر اليومية يتضمن طلبات تحتاج مراجعة؛ عالج الطلبات أولاً',
    'customer_drift_blocked': 'إجماليات العميل غير مطابقة وبعض طلباته تحت المراجعة: أعد الحساب بعد حلها',
    'duplicate_order': 'طلبين متطابقين بنفس اللحظة: تأكد من الكاشير هل هو إرسال مكرر ثم ألغِ واحد',
    'unknown_payment_method': 'طريقة دفع غير معرّفة في رزق: تأكد كيف انْدفع هذا الطلب',
    'future_timestamp': 'طلب بتاريخ مستقبلي: تحقق من ساعة الجهاز'}


def open_copy(path_or_conn) -> sqlite3.Connection:
    """In-memory copy. The source file is opened read-only (or an existing connection is copied)."""
    src = path_or_conn if isinstance(path_or_conn, sqlite3.Connection) else sqlite3.connect(f"file:{path_or_conn}?mode=ro", uri=True)
    dst = sqlite3.connect(":memory:")
    src.backup(dst)
    if src is not path_or_conn:
        src.close()
    dst.row_factory = sqlite3.Row
    return dst


def save_db(conn: sqlite3.Connection, path: str) -> None:
    """Write via a temp file + plain copy: works on synced/network folders where SQLite can't lock or journal."""
    import os, shutil, tempfile
    fd, tmp = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    disk = sqlite3.connect(tmp)
    conn.backup(disk)
    disk.close()
    shutil.copyfile(tmp, path)
    os.remove(tmp)


def _f(kind: str, entity: str, **detail) -> Finding:
    label, handling, action = KINDS[kind]
    return Finding(kind, entity, handling, label, detail, action, ACTIONS_AR[kind])


def scan(db: sqlite3.Connection, now: datetime | None = None, vat_rate: float = VAT_RATE) -> list[Finding]:
    q = lambda sql, params=(): db.execute(sql, params).fetchall()
    orders = {r["id"]: dict(r) for r in q("SELECT * FROM orders")}
    items = [dict(r) for r in q("SELECT * FROM order_items")]
    products = {r["id"] for r in q("SELECT id FROM products")}
    methods = {r["id"] for r in q("SELECT id FROM payment_methods")}
    by_order = defaultdict(list)
    for it in items:
        by_order[it["order_id"]].append(it)
    out: list[Finding] = []
    now = now or datetime.utcnow()

    # lines
    for it in items:
        if abs(r2(it["unit_price"] * it["quantity"]) - it["total"]) > EPS:
            out.append(_f("line_total_wrong", it["id"], order=it["order_id"], stored=it["total"],
                          expected=r2(it["unit_price"] * it["quantity"])))
    # orphans: items whose order row is gone
    for oid in sorted({it["order_id"] for it in items} - set(orders)):
        lines = by_order[oid]
        sub = r2(sum(r2(i["unit_price"] * i["quantity"]) for i in lines))
        out.append(_f("order_row_lost", oid, items=len(lines), reconstructed_subtotal=sub,
                      reconstructed_total_if_no_discount=r2(sub + r2(sub * vat_rate))))
    # orders
    for oid, o in orders.items():
        lines = by_order.get(oid, [])
        if o["status"] != "completed":
            continue
        if not lines:
            out.append(_f("order_without_items", oid, final_total=o["final_total"]))
            continue
        expected_sub = r2(sum(r2(i["unit_price"] * i["quantity"]) for i in lines))
        if abs(expected_sub - o["total"]) > EPS:
            out.append(_f("item_missing", oid, invoice_subtotal=o["total"], items_subtotal=expected_sub,
                          gap=r2(o["total"] - expected_sub)))
        net = r2(o["total"] - o["discount_amount"])
        if abs(r2(net * vat_rate) - o["vat_amount"]) > EPS:
            out.append(_f("vat_wrong", oid, net=net, stored_vat=o["vat_amount"], correct_vat=r2(net * vat_rate),
                          difference=r2(o["vat_amount"] - r2(net * vat_rate))))
        elif abs(r2(net + o["vat_amount"]) - o["final_total"]) > EPS:
            out.append(_f("final_total_wrong", oid, stored_final=o["final_total"], correct_final=r2(net + o["vat_amount"]),
                          difference=r2(o["final_total"] - r2(net + o["vat_amount"]))))
        if o["payment_method"] not in methods:
            out.append(_f("unknown_payment_method", oid, payment_method=o["payment_method"]))
        try:
            ts = datetime.fromisoformat(str(o["created_at"]).replace("Z", ""))
            if ts > now + timedelta(hours=1):
                out.append(_f("future_timestamp", oid, created_at=o["created_at"]))
        except ValueError:
            out.append(_f("future_timestamp", oid, created_at=o["created_at"], unparseable=True))
    # duplicates: same second, same staff, same lines, same money
    sig = defaultdict(list)
    for oid, o in orders.items():
        lines = tuple(sorted((i["product_id"], i["quantity"], i["unit_price"]) for i in by_order.get(oid, [])))
        sig[(o["created_at"], o["staff_id"], o["final_total"], lines)].append(oid)
    for key, ids in sig.items():
        if len(ids) > 1 and key[3]:
            for dup in sorted(ids)[1:]:
                out.append(_f("duplicate_order", dup, same_as=sorted(ids)[0], created_at=key[0], final_total=key[2]))
    # stock: sold quantity per product == sale movements per product
    sold, moved = defaultdict(int), defaultdict(int)
    for it in items:
        if it["product_id"] in products:
            sold[it["product_id"]] += it["quantity"]
    for r in q("SELECT product_id, quantity FROM stock_transactions WHERE transaction_type='sale'"):
        moved[r["product_id"]] += -r["quantity"]
    for pid in sorted(set(sold) | set(moved)):
        if sold[pid] > moved[pid]:
            out.append(_f("stock_movement_missing", pid, sold=sold[pid], moved=moved[pid], missing_qty=sold[pid] - moved[pid]))
        elif moved[pid] > sold[pid]:
            out.append(_f("stock_movement_extra", pid, sold=sold[pid], moved=moved[pid], extra_qty=moved[pid] - sold[pid]))
    # customers: aggregates must equal their orders
    agg = defaultdict(lambda: [0, 0.0])
    for o in orders.values():
        if o["customer_phone"]:
            agg[o["customer_phone"]][0] += 1
            agg[o["customer_phone"]][1] += o["final_total"]
    suspicious_phones = {orders[f.entity]["customer_phone"] for f in out
                         if f.handling == "escalate" and f.entity in orders and orders[f.entity]["customer_phone"]}
    lost_orders = any(f.kind in ("order_row_lost", "order_without_items") for f in out)
    for r in q("SELECT phone, orders_count, total_spent FROM customers"):
        n, t = agg[r["phone"]]
        if r["orders_count"] != n or abs(r["total_spent"] - t) > EPS:
            kind = "customer_drift_blocked" if (r["phone"] in suspicious_phones or lost_orders) else "customer_drift"
            out.append(_f(kind, r["phone"], stored_count=r["orders_count"], orders_count=n,
                          stored_spent=r2(r["total_spent"]), orders_spent=r2(t)))
    # daily_ledger is derived from completed orders. Rebuild expected values without multiplying sales by item count.
    ledger_orders = q("""SELECT date(created_at) AS day, COUNT(*) AS orders, SUM(final_total) AS sales,
                              SUM(vat_amount) AS vat, SUM(discount_amount) AS discount
                       FROM orders WHERE status='completed' GROUP BY date(created_at)""")
    ledger_costs = {r["day"]: r["cost"] for r in q("""SELECT date(o.created_at) AS day,
                              SUM(oi.quantity * COALESCE(p.cost,0)) AS cost
                       FROM orders o JOIN order_items oi ON oi.order_id=o.id
                       LEFT JOIN products p ON p.id=oi.product_id
                       WHERE o.status='completed' GROUP BY date(o.created_at)""")}
    current_ledger = {r["date"]: dict(r) for r in q("SELECT * FROM daily_ledger")}
    suspect_days = {str(orders[f.entity]["created_at"])[:10] for f in out
                    if f.handling == "escalate" and f.entity in orders}
    for r in ledger_orders:
        day = str(r["day"])
        by_payment = {p["method"]: {"orders": p["orders"], "sales": r2(p["sales"])} for p in q(
            """SELECT payment_method AS method, COUNT(*) AS orders, SUM(final_total) AS sales
               FROM orders WHERE status='completed' AND date(created_at)=? GROUP BY payment_method ORDER BY payment_method""", (day,))}
        cost = r2(ledger_costs.get(day, 0) or 0)
        expected = {"date": day, "orders": int(r["orders"]), "total_sales": r2(r["sales"] or 0),
                    "total_vat": r2(r["vat"] or 0), "total_discount": r2(r["discount"] or 0),
                    "total_cost": cost, "gross_profit": r2((r["sales"] or 0) - (r["vat"] or 0) - cost),
                    "payment_breakdown": json.dumps(by_payment, ensure_ascii=False, sort_keys=True)}
        stored = current_ledger.get(day)
        mismatch = stored is None
        if stored:
            for key in ("total_sales", "total_vat", "total_discount", "total_cost", "gross_profit"):
                if stored.get(key) is None or abs(float(stored[key]) - expected[key]) > EPS:
                    mismatch = True
            try:
                old = json.loads(stored.get("payment_breakdown") or "{}")
                if old != by_payment:
                    mismatch = True
            except (TypeError, json.JSONDecodeError):
                mismatch = True
        if mismatch:
            kind = "daily_ledger_blocked" if lost_orders or day in suspect_days else "daily_ledger_drift"
            out.append(_f(kind, day, expected=expected,
                          stored={k: stored.get(k) for k in expected if stored} if stored else None))
    return out


def diagnose(findings: list[Finding]) -> dict:
    labels = defaultdict(list)
    for f in findings:
        labels[f.label].append(f"{f.kind}:{f.entity}")
    return {l: {"count": len(v), "evidence": v[:5]} for l, v in labels.items()}


def repair(db: sqlite3.Connection, findings: list[Finding], dry_run: bool = False) -> list[str]:
    """Rebuilds DERIVED data only. Returns the audit trail (every statement, dry-run or applied)."""
    audit: list[str] = []

    def run(sql: str, params: tuple, why: str) -> None:
        audit.append(f"-- {why}\n{sql.strip()};  -- params={json.dumps(params, ensure_ascii=False)}")
        if not dry_run:
            db.execute(sql, params)

    n = 0
    for f in findings:
        if f.handling != "auto":
            continue
        if f.kind == "line_total_wrong":
            run("UPDATE order_items SET total=? WHERE id=?", (f.detail["expected"], f.entity),
                f"line total {f.detail['stored']} -> {f.detail['expected']} (unit x qty)")
        elif f.kind == "stock_movement_missing":
            n += 1
            q = f.detail["missing_qty"]
            run("INSERT INTO stock_transactions (id, product_id, transaction_type, quantity, notes) VALUES (?,?, 'sale', ?, ?)",
                (f"st_rizq_agent_{n}", f.entity, -q, "Rizq Agent: missing sale movement restored"),
                f"restore {q} missing sale movement(s)")
            run("UPDATE products SET stock = stock - ? WHERE id=?", (q, f.entity), f"deduct {q} from stock")
        elif f.kind == "customer_drift":
            run("UPDATE customers SET orders_count=?, total_spent=? WHERE phone=?",
                (f.detail["orders_count"], f.detail["orders_spent"], f.entity), "recompute customer totals from orders")
        elif f.kind == "daily_ledger_drift":
            e = f.detail["expected"]
            existing = db.execute("SELECT id FROM daily_ledger WHERE date=? LIMIT 1", (f.entity,)).fetchone()
            values = (e["total_sales"], e["total_vat"], e["total_discount"], e["total_cost"],
                      e["gross_profit"], e["payment_breakdown"])
            if existing:
                run("UPDATE daily_ledger SET total_sales=?, total_vat=?, total_discount=?, total_cost=?, gross_profit=?, payment_breakdown=? WHERE date=?",
                    (*values, f.entity), "rebuild derived daily ledger from completed orders")
            else:
                run("INSERT INTO daily_ledger (id,date,total_sales,total_vat,total_discount,total_cost,gross_profit,payment_breakdown) VALUES (?,?,?,?,?,?,?,?)",
                    (f"ledger_{f.entity}", f.entity, *values), "create derived daily ledger from completed orders")
    if not dry_run:
        db.commit()
    return audit


def run_agent(path, out_prefix: str | None = None, now: datetime | None = None) -> dict:
    """Full loop on a real Rizq DB file. Writes <prefix>.fixed.sqlite, .audit.sql, .report.json if out_prefix."""
    db = open_copy(path)
    before = scan(db, now)
    plan = repair(db, before, dry_run=True)
    audit = repair(db, before)
    after = scan(db, now)
    before_keys = {(f.kind, f.entity) for f in before}
    auto_left = [f for f in after if f.handling == "auto"]
    new = [f for f in after if (f.kind, f.entity) not in before_keys]
    verified = not auto_left and not new
    escalated = [f for f in after if f.handling == "escalate"]
    status = ("healthy" if not before else
              "recovered" if verified and not escalated else
              "escalated" if verified else "verification_failed")
    report = {"status": status, "findings_before": len(before), "auto_fixed": sum(f.handling == "auto" for f in before) if verified else 0,
              "escalated": [asdict(f) for f in escalated], "diagnosis": diagnose(before),
              "verification": {"auto_findings_left": len(auto_left), "new_findings": len(new), "passed": verified},
              "plan": plan}
    if out_prefix:
        save_db(db, f"{out_prefix}.fixed.sqlite")
        with open(f"{out_prefix}.audit.sql", "w", encoding="utf-8") as fh:
            fh.write("\n".join(audit) + "\n")
        with open(f"{out_prefix}.report.json", "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=1)
    report["_db"] = db
    report["_before"] = before
    return report
