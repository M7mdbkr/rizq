"""Things Rizq should do on a date, without the owner remembering them.

The case that defines this: Saudi National Day. The owner wants the discount live on the
day, prepared the day before -- every year, without being reminded.

Two facts shape the design.

The cashier's database lives in the browser, so a job on this server cannot write an
order or a price into it. A job therefore does one of two things:

  report   computed here and delivered as text. Safe, immediate, no approval needed.
  action   queued as a *pending action* the cashier applies next time it is open. The
           owner approves the action when they schedule it, and again in the till before
           it takes effect, because a discount is money.

And the agent never invents the business decision. "20% off on National Day" is the
owner's call; the scheduler only makes sure it happens on time.

Hijri dates (Ramadan, the two Eids) move every Gregorian year and cannot be computed
from a fixed rule, so they are scheduled by explicit date and flagged as needing the
owner to set next year's date. Saying otherwise would quietly produce a wrong day.
"""
from __future__ import annotations

import datetime
import json
import os
import pathlib
import sqlite3
import uuid

DB = pathlib.Path(os.environ.get(
    "RIZQ_SCHEDULE_DB",
    str(pathlib.Path(__file__).resolve().parents[1] / "data" / "schedule.sqlite")))

# Fixed Gregorian occasions in Saudi Arabia. Hijri ones are deliberately absent.
OCCASIONS = {
    "national_day":   {"month": 9, "day": 23, "name_ar": "اليوم الوطني السعودي"},
    "founding_day":   {"month": 2, "day": 22, "name_ar": "يوم التأسيس"},
}

KINDS = ("report", "discount", "reminder")
REPEATS = ("once", "daily", "weekly", "monthly", "yearly")


def _connect() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    con = sqlite3.connect(str(DB), timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
        repeat TEXT NOT NULL, next_run TEXT NOT NULL, params TEXT NOT NULL,
        created_by TEXT NOT NULL, created_at TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1, last_run TEXT, last_result TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS pending_actions (
        id TEXT PRIMARY KEY, job_id TEXT, kind TEXT NOT NULL, summary TEXT NOT NULL,
        payload TEXT NOT NULL, due TEXT NOT NULL, created_at TEXT NOT NULL,
        applied_at TEXT, applied_by TEXT)""")
    try:
        os.chmod(DB, 0o600)
    except OSError:
        pass
    return con


def _iso(d: datetime.date | datetime.datetime) -> str:
    return d.isoformat()


def next_occurrence(repeat: str, start: datetime.date,
                    after: datetime.date | None = None) -> datetime.date:
    """The next date on or after `after` for this repeat rule."""
    after = after or datetime.date.today()
    if repeat == "once":
        return start
    if start >= after:
        return start
    if repeat == "daily":
        return after
    if repeat == "weekly":
        gap = (after - start).days
        return start + datetime.timedelta(days=((gap + 6) // 7) * 7)
    if repeat == "monthly":
        year, month = after.year, after.month
        day = min(start.day, _days_in_month(year, month))
        candidate = datetime.date(year, month, day)
        if candidate < after:
            month += 1
            if month > 12:
                month, year = 1, year + 1
            day = min(start.day, _days_in_month(year, month))
            candidate = datetime.date(year, month, day)
        return candidate
    if repeat == "yearly":
        year = after.year
        day = min(start.day, _days_in_month(year, start.month))
        candidate = datetime.date(year, start.month, day)
        if candidate < after:
            year += 1
            day = min(start.day, _days_in_month(year, start.month))
            candidate = datetime.date(year, start.month, day)
        return candidate
    raise ValueError(f"unknown repeat: {repeat}")


def _days_in_month(year: int, month: int) -> int:
    import calendar
    return calendar.monthrange(year, month)[1]


def occasion_date(key: str, year: int | None = None, days_before: int = 0) -> datetime.date:
    """The date for a named Saudi occasion, optionally some days earlier."""
    if key not in OCCASIONS:
        raise ValueError(f"unknown occasion: {key}. known: {sorted(OCCASIONS)}")
    o = OCCASIONS[key]
    year = year or datetime.date.today().year
    return datetime.date(year, o["month"], o["day"]) - datetime.timedelta(days=days_before)


def add(name: str, kind: str, repeat: str, start: datetime.date, params: dict,
        created_by: str) -> dict:
    if kind not in KINDS:
        raise ValueError(f"نوع غير معروف: {kind}. المتاح: {', '.join(KINDS)}")
    if repeat not in REPEATS:
        raise ValueError(f"تكرار غير معروف: {repeat}. المتاح: {', '.join(REPEATS)}")
    if not str(name).strip():
        raise ValueError("أعطِ المهمة اسمًا")
    job = {"id": "job_" + uuid.uuid4().hex[:10], "name": name.strip(), "kind": kind,
           "repeat": repeat, "next_run": _iso(start), "params": json.dumps(params, ensure_ascii=False),
           "created_by": created_by, "created_at": _iso(datetime.datetime.now()), "enabled": 1}
    with _connect() as con:
        con.execute("INSERT INTO jobs (id,name,kind,repeat,next_run,params,created_by,created_at,enabled)"
                    " VALUES (:id,:name,:kind,:repeat,:next_run,:params,:created_by,:created_at,:enabled)", job)
    job["params"] = params
    return job


def schedule_occasion_discount(occasion: str, percent: float, code: str, created_by: str,
                               days_before: int = 1) -> dict:
    """The National Day case, as one call: prepare the discount the day before, yearly."""
    if not 0 < percent <= 90:
        raise ValueError("نسبة الخصم لازم تكون بين 1 و 90")
    code = (code or "").strip().upper()
    if not code.isalnum():
        raise ValueError("كود الخصم يكون حروف وأرقام فقط")
    o = OCCASIONS.get(occasion)
    if not o:
        raise ValueError(f"مناسبة غير معروفة: {occasion}")
    start = occasion_date(occasion, days_before=days_before)
    if start < datetime.date.today():
        start = occasion_date(occasion, year=datetime.date.today().year + 1, days_before=days_before)
    return add(name=f"خصم {o['name_ar']} {percent:g}%", kind="discount", repeat="yearly",
               start=start, created_by=created_by,
               params={"occasion": occasion, "percent": percent, "code": code,
                       "days_before": days_before, "occasion_name_ar": o["name_ar"]})


def jobs(include_disabled: bool = False) -> list:
    with _connect() as con:
        rows = con.execute(
            "SELECT * FROM jobs" + ("" if include_disabled else " WHERE enabled=1")
            + " ORDER BY next_run").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["params"] = json.loads(d["params"])
        out.append(d)
    return out


def remove(job_id: str) -> bool:
    with _connect() as con:
        return con.execute("DELETE FROM jobs WHERE id=?", (job_id,)).rowcount > 0


def set_enabled(job_id: str, enabled: bool) -> bool:
    with _connect() as con:
        return con.execute("UPDATE jobs SET enabled=? WHERE id=?",
                           (1 if enabled else 0, job_id)).rowcount > 0


def due(now: datetime.date | None = None) -> list:
    now = now or datetime.date.today()
    return [j for j in jobs() if datetime.date.fromisoformat(j["next_run"]) <= now]


def pending(include_applied: bool = False) -> list:
    with _connect() as con:
        rows = con.execute(
            "SELECT * FROM pending_actions" + ("" if include_applied else " WHERE applied_at IS NULL")
            + " ORDER BY due").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["payload"] = json.loads(d["payload"])
        out.append(d)
    return out


def _queue_action(job: dict, summary: str, payload: dict, when: datetime.date) -> dict:
    row = {"id": "act_" + uuid.uuid4().hex[:10], "job_id": job["id"], "kind": job["kind"],
           "summary": summary, "payload": json.dumps(payload, ensure_ascii=False),
           "due": _iso(when), "created_at": _iso(datetime.datetime.now())}
    with _connect() as con:
        con.execute("INSERT INTO pending_actions (id,job_id,kind,summary,payload,due,created_at)"
                    " VALUES (:id,:job_id,:kind,:summary,:payload,:due,:created_at)", row)
    row["payload"] = payload
    return row


def mark_applied(action_id: str, by: str) -> bool:
    with _connect() as con:
        return con.execute("UPDATE pending_actions SET applied_at=?, applied_by=? "
                           "WHERE id=? AND applied_at IS NULL",
                           (_iso(datetime.datetime.now()), by, action_id)).rowcount > 0


def run_due(db=None, now: datetime.date | None = None) -> list:
    """Run everything due. Reports are produced here; actions are queued for the till."""
    now = now or datetime.date.today()
    results = []
    for job in due(now):
        try:
            result = _run_one(job, db, now)
        except Exception as exc:                      # a bad job must not stop the rest
            result = {"ok": False, "error": str(exc)}
        start = datetime.date.fromisoformat(job["next_run"])
        nxt = (None if job["repeat"] == "once"
               else next_occurrence(job["repeat"], start, now + datetime.timedelta(days=1)))
        with _connect() as con:
            if nxt:
                con.execute("UPDATE jobs SET last_run=?, last_result=?, next_run=? WHERE id=?",
                            (_iso(now), json.dumps(result, ensure_ascii=False)[:2000], _iso(nxt), job["id"]))
            else:
                con.execute("UPDATE jobs SET last_run=?, last_result=?, enabled=0 WHERE id=?",
                            (_iso(now), json.dumps(result, ensure_ascii=False)[:2000], job["id"]))
        results.append({"job": job["name"], "job_id": job["id"], "kind": job["kind"],
                        "result": result, "next_run": _iso(nxt) if nxt else None})
    return results


def _run_one(job: dict, db, now: datetime.date) -> dict:
    params = job["params"]
    if job["kind"] == "report":
        if db is None:
            return {"ok": False, "error": "no database available for the report"}
        from . import phrase, skills
        name = params.get("skill", "sales_period")
        out = skills.run(db, name, params.get("params") or {})
        if out.get("status") != "ok":
            return {"ok": False, "status": out.get("status"), "skill": name}
        return {"ok": True, "skill": name, "text": phrase.skill_answer(out)}

    if job["kind"] == "discount":
        when = params.get("occasion")
        on_day = (occasion_date(when, year=now.year) if when in OCCASIONS
                  else now + datetime.timedelta(days=params.get("days_before", 0)))
        summary = (f"خصم {params['percent']:g}% بكود {params['code']} "
                   f"لمناسبة {params.get('occasion_name_ar', '')} يوم {on_day.isoformat()}")
        action = _queue_action(job, summary, {"code": params["code"],
                                              "percent": params["percent"],
                                              "active_on": on_day.isoformat()}, on_day)
        return {"ok": True, "queued_action": action["id"], "summary": summary,
                "note": "جاهز للتطبيق من الكاشير بموافقتك"}

    if job["kind"] == "reminder":
        summary = params.get("text") or job["name"]
        action = _queue_action(job, summary, {"text": summary}, now)
        return {"ok": True, "queued_action": action["id"], "summary": summary}

    return {"ok": False, "error": f"unknown kind {job['kind']}"}
