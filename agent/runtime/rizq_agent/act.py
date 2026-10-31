"""Action toolbox (whitelist), planner and executor.

Every action has a blast radius and a dry-run description. The executor refuses anything
not on the whitelist, so the agent cannot invent destructive commands.
"""
from __future__ import annotations

WHITELIST = {
    "restart_service":      {"blast": "low",    "undo": "none needed (stateless restart)"},
    "reconcile_orders":     {"blast": "medium", "undo": "orders tagged reconciled; can be voided"},
    "rollback_config":      {"blast": "low",    "undo": "re-apply previous config"},
    "replay_lost_messages": {"blast": "medium", "undo": "idempotent by message id"},
    "pause_sync":           {"blast": "low",    "undo": "resume_sync"},
    "resume_sync":          {"blast": "low",    "undo": "pause_sync"},
    "resync_replica":       {"blast": "medium", "undo": "replica snapshot kept"},
    "quarantine_jobs":      {"blast": "low",    "undo": "release jobs"},
    "escalate":             {"blast": "none",   "undo": "n/a"},
}

PLAYBOOKS = {
    "silent_drop": [("restart_service", {"name": "order_writer"}), ("reconcile_orders", {})],
    "queue_loss": [("rollback_config", {"component": "queue"}), ("replay_lost_messages", {})],
    "sync_corruption": [("pause_sync", {}), ("rollback_config", {"component": "sync"}),
                        ("resync_replica", {}), ("resume_sync", {})],
    "hung_workflow": [("restart_service", {"name": "receipt_worker"})],
}
ESCALATE_LABELS = {"runaway_repair", "unknown"}

# Which invariants a label "owns" (used to exclude handed-off problems from verification).
LABEL_INVARIANTS = {
    "silent_drop": {"payments_match_orders"},
    "queue_loss": {"queue_conservation"},
    "sync_corruption": {"replica_consistent"},
    "hung_workflow": {"receipts_timely"},
    "runaway_repair": {"retry_budget", "receipts_timely"},
    "unknown": {"prices_match_catalog", "ledger_balanced", "order_arithmetic"},
}


def plan(diagnosis: dict, invariants: dict) -> list[tuple[str, dict]]:
    steps: list[tuple[str, dict]] = []
    labels = diagnosis["labels"]
    for label in sorted(labels, key=lambda l: -labels[l]["confidence"]):
        steps += PLAYBOOKS.get(label, [])
    if "runaway_repair" in labels:
        jobs = sorted(set(invariants["retry_budget"].get("jobs", [])) | set(invariants["receipts_timely"].get("failing", [])))
        steps.append(("quarantine_jobs", {"job_ids": jobs}))
    handoff = [l for l in labels if l in ESCALATE_LABELS]
    if handoff:
        steps.append(("escalate", {"report": {
            "reason": "needs a human: " + ", ".join(handoff),
            "hypotheses": {l: labels[l] for l in handoff},
            "safe_actions_taken": [s[0] for s in steps],
        }}))
    return steps


def dry_run(step: tuple[str, dict]) -> str:
    op, args = step
    meta = WHITELIST[op]
    shown = {k: v for k, v in args.items() if k != "report"}
    return f"[dry-run] {op}({shown}) blast={meta['blast']} undo={meta['undo']}"


def execute(obs, steps: list[tuple[str, dict]], trace: list) -> None:
    for step in steps:
        op, args = step
        if op not in WHITELIST:
            raise PermissionError(f"action {op} is not whitelisted")
        trace.append({"t": obs.t, "type": "dry_run", "msg": dry_run(step)})
        result = obs.admin(op, **args)
        trace.append({"t": obs.t, "type": "action", "op": op, "msg": result})
