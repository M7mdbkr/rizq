"""Export everything the restaurant holds as one file the owner can hand over.

An accountant, an auditor or a new system asks for "the data". This builds a single ZIP
containing every table as CSV, a copy of the database, the agent's audit log, the chat
history, the escalations it raised, and the current integrity findings -- with a README
that says what each file is.

Two rules:
  - **PINs never leave.** Every employee PIN is redacted in both the CSV and the SQLite
    copy. The export identifies people by name and role, never by credential.
  - **The live database is never modified.** The copy is taken read-only.
"""
from __future__ import annotations

import csv
import datetime
import io
import json
import pathlib
import sqlite3
import zipfile

from . import rizq

REDACTED = "[REDACTED]"
SENSITIVE_COLUMNS = {"pin", "password", "token", "secret", "passcode"}

TEMPLATE = """تقرير رزق — نموذج للتعبئة
=====================================
هذا الملف يُعاد إلى وكيل رزق فيعبّئ الفراغات من بيانات المطعم.
كل رقم يُكتب فقط إذا وافق عليه المدقّق المستقل؛ وإلا يُترك الفراغ مع سبب واضح.

التاريخ                 : {{today}}
وقت الإنشاء             : {{generated_at}}

المبيعات (٣٠ يوم)        : {{sales_total}} ر.س
عدد الطلبات             : {{sales_orders}}
ضريبة المبيعات          : {{sales_vat}} ر.س
الضريبة المحصّلة         : {{vat_collected}} ر.س

الإيراد بدون ضريبة       : {{revenue_ex_vat}} ر.س
تكلفة المبيعات          : {{cost_of_goods}} ر.س
الربح الإجمالي           : {{gross_profit}} ر.س

قيمة المخزون بالتكلفة    : {{inventory_at_cost}} ر.س
قيمة المخزون بالبيع      : {{inventory_at_retail}} ر.س
عدد الأصناف             : {{products_count}}
أصناف ناقصة             : {{low_stock_count}}
أصناف منتهية            : {{out_of_stock_count}}

الزكاة التقديرية         : {{zakat_due}}
   (تحتاج رصيدك النقدي، ورزق لا يسجّله — يبقى فارغًا حتى تعطيه الوكيل)

ملاحظة: هذه سجلات رزق وليست إقرارًا ضريبيًا، ولم يُرسل منها شيء إلى هيئة الزكاة والضريبة.
"""

README = """RIZQ DATA EXPORT
================

Created        : {created}
Exported by    : {who}
Source database: {source}

WHAT IS IN HERE

data/*.csv
    One file per table in the cashier database, exported exactly as stored.

data/rizq.sqlite
    A complete copy of the database, openable with any SQLite tool.

logs/audit.jsonl
    Every action the AI agent was allowed or refused, one JSON object per line:
    who asked, which tool, whether it was permitted.

logs/chat_history.csv
    Conversations with the assistant, per user.

logs/escalations.jsonl
    Problems the agent refused to fix on its own and handed to a human.

reports/integrity.csv
    The current business-rule findings: what does not reconcile and what the agent
    would do about it.

reports/summary.json
    Row counts and totals, so the recipient can check nothing was lost in transit.

templates/monthly-report.txt
    A form with {{{{blanks}}}}. Hand this file back to the Rizq agent and it fills every
    blank from the database. A figure is written only when an independent checker
    agrees with it; anything it cannot verify stays blank with the reason given.

PRIVACY
    Employee PINs are redacted everywhere in this export. Customer records are
    included as stored; treat this file as confidential business data.

LIMITS
    Figures here are Rizq's own records. They are not a tax filing and nothing in
    this export has been submitted to ZATCA.
"""


def _tables(con: sqlite3.Connection) -> list:
    return [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]


def _table_csv(con: sqlite3.Connection, table: str) -> str:
    cur = con.execute(f'SELECT * FROM "{table}"')
    cols = [d[0] for d in cur.description]
    redact = {i for i, c in enumerate(cols) if c.lower() in SENSITIVE_COLUMNS}
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for row in cur:
        w.writerow([REDACTED if i in redact else v for i, v in enumerate(row)])
    return buf.getvalue()


def _redacted_copy(db_path: str) -> bytes:
    """A full SQLite copy with every credential column blanked."""
    work = rizq.open_copy(db_path)
    try:
        for table in _tables(work):
            cols = [r[1] for r in work.execute(f'PRAGMA table_info("{table}")')]
            for col in cols:
                if col.lower() in SENSITIVE_COLUMNS:
                    work.execute(f'UPDATE "{table}" SET "{col}"=?', (REDACTED,))
        work.commit()
        out = io.BytesIO()
        for line in work.iterdump():
            out.write((line + "\n").encode("utf-8"))
        dump = out.getvalue()
    finally:
        work.close()
    # Rebuild a real database file from the dump so the recipient gets a .sqlite.
    import os
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    try:
        disk = sqlite3.connect(tmp)
        disk.executescript(dump.decode("utf-8"))
        disk.commit()
        disk.close()
        return pathlib.Path(tmp).read_bytes()
    finally:
        pathlib.Path(tmp).unlink(missing_ok=True)


def _chat_csv(memory_path: pathlib.Path) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["user_id", "role", "text", "created_at"])
    if memory_path.exists():
        con = sqlite3.connect(f"file:{memory_path}?mode=ro", uri=True)
        try:
            for row in con.execute("SELECT user_id, role, text, created_at FROM messages ORDER BY id"):
                w.writerow(row)
        except sqlite3.Error:
            pass
        finally:
            con.close()
    return buf.getvalue()


def _integrity_csv(db_path: str) -> tuple:
    db = rizq.open_copy(db_path)
    try:
        findings = rizq.scan(db)
    finally:
        db.close()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["kind", "entity", "handling", "action_ar", "detail"])
    for f in findings:
        w.writerow([f.kind, f.entity, f.handling, f.human_action_ar,
                    json.dumps(f.detail, ensure_ascii=False, default=str)])
    return buf.getvalue(), len(findings)


def build(db_path: str, *, who: str = "owner", audit_path: pathlib.Path | None = None,
          memory_path: pathlib.Path | None = None,
          escalations_path: pathlib.Path | None = None) -> tuple:
    """Return (filename, zip bytes). The source database is opened read-only."""
    created = datetime.datetime.now()
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        tables = _tables(con)
        counts = {t: con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
        csvs = {t: _table_csv(con, t) for t in tables}
    finally:
        con.close()

    integrity, finding_count = _integrity_csv(db_path)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.txt", README.format(
            created=created.strftime("%Y-%m-%d %H:%M"), who=who,
            source=pathlib.Path(db_path).name))
        for table, body in csvs.items():
            z.writestr(f"data/{table}.csv", body)
        z.writestr("data/rizq.sqlite", _redacted_copy(db_path))
        if audit_path and pathlib.Path(audit_path).exists():
            z.writestr("logs/audit.jsonl", pathlib.Path(audit_path).read_text(encoding="utf-8"))
        z.writestr("logs/chat_history.csv", _chat_csv(pathlib.Path(memory_path)) if memory_path else "")
        if escalations_path and pathlib.Path(escalations_path).exists():
            z.writestr("logs/escalations.jsonl", pathlib.Path(escalations_path).read_text(encoding="utf-8"))
        z.writestr("reports/integrity.csv", integrity)
        z.writestr("templates/monthly-report.txt", TEMPLATE)
        z.writestr("reports/summary.json", json.dumps({
            "created": created.isoformat(timespec="seconds"),
            "exported_by": who,
            "tables": counts,
            "integrity_findings": finding_count,
            "pins_redacted": True,
            "submitted_to_zatca": False,
        }, ensure_ascii=False, indent=1))
    name = f"rizq-export-{created.strftime('%Y%m%d-%H%M%S')}.zip"
    return name, buf.getvalue()
