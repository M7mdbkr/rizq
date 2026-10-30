"""Diagnosers: turn observations into ranked hypotheses from a CLOSED label set.

- runbook_diagnose: deterministic, invariant-driven (the main path; no API, reproducible).
- logs_only_diagnose: what a typical alerting bot does (reads ERROR/WARN lines). Baseline/ablation.

A diagnosis is {"labels": {label: {"confidence": float, "evidence": [...]}}, "unexplained": [...]}.
Anything the runbook cannot explain becomes "unknown" -> the planner escalates, never guesses.
"""
from __future__ import annotations

from collections import Counter

LABELS = ("silent_drop", "queue_loss", "sync_corruption", "hung_workflow", "runaway_repair", "unknown")

DIRECT_RULES = {
    "payments_match_orders": "silent_drop",
    "queue_conservation": "queue_loss",
    "replica_consistent": "sync_corruption",
    "retry_budget": "runaway_repair",
}


def _add(d: dict, label: str, conf: float, ev: str) -> None:
    cur = d["labels"].setdefault(label, {"confidence": 0.0, "evidence": []})
    cur["confidence"] = max(cur["confidence"], conf)
    cur["evidence"].append(ev)


def runbook_diagnose(invariants: dict, logs: list, metrics: list) -> dict:
    d: dict = {"labels": {}, "unexplained": []}
    for name, v in invariants.items():
        if v["ok"] is not False:
            continue
        if name in DIRECT_RULES:
            _add(d, DIRECT_RULES[name], 0.95, f"{name} violated (count={v['count']}, sample={v.get('sample') or v.get('jobs')})")
        elif name == "receipts_timely":
            if v["never_attempted"]:
                _add(d, "hung_workflow", 0.9,
                     f"{v['never_attempted']} receipt jobs overdue and never attempted (worker heartbeat still 'ok')")
            if v["failing"]:
                _add(d, "runaway_repair", 0.8, f"jobs {v['failing'][:3]} keep failing and are being auto-retried")
        else:
            d["unexplained"].append(f"{name} violated (count={v['count']}) - no runbook rule")
    if d["unexplained"]:
        _add(d, "unknown", 0.5, "; ".join(d["unexplained"]))
    return d


def logs_only_diagnose(invariants: dict, logs: list, metrics: list) -> dict:
    """Typical log-alerting bot: only looks at ERROR/WARN lines, ignores invariants."""
    d: dict = {"labels": {}, "unexplained": []}
    loud = [(lvl, svc, msg) for _, lvl, svc, msg in logs if lvl in ("ERROR", "WARN")]
    if not loud:
        return d
    retry = [m for _, _, m in loud if "retry" in m.lower()]
    if len(retry) >= 3:
        _add(d, "runaway_repair", 0.7, f"{len(retry)} retry errors in logs")
    if any("watermark" in m for _, _, m in loud):
        _add(d, "queue_loss", 0.6, "queue high watermark warning")
    other = Counter(svc for _, svc, m in loud if "retry" not in m.lower() and "watermark" not in m)
    if other and not d["labels"]:
        _add(d, "unknown", 0.4, f"errors from {dict(other)}")
    return d


DIAGNOSERS = {"runbook": runbook_diagnose, "logs_only": logs_only_diagnose}
