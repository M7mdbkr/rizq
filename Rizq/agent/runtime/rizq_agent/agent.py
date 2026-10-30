"""The Rizq Agent loop: detect -> investigate -> diagnose -> act (dry-run first) -> verify -> recover | retry | escalate."""
from __future__ import annotations

from dataclasses import dataclass, field

from . import act
from .diagnose import DIAGNOSERS


@dataclass
class AgentConfig:
    diagnoser: str = "runbook"
    debounce: int = 1          # ticks a violation must persist (1 = act on first sighting; see Rizq/Rizq.md)
    soak: int = 6              # ticks of clean invariants required to declare recovery
    max_rounds: int = 3        # retry budget: remediation rounds before forced escalation
    verify: str = "invariants"  # "invariants" | "logs" | "none"
    llm: object = None          # None = rules only; "anthropic" | "cache" | callable -> LLM triage layer
    watch_every: int = 6        # with blind checks: ask the LLM to scan logs every N ticks (bounded cost)
    name: str = "rizq_agent"


def _verify(obs, cfg: AgentConfig, excluded: set[str], trace: list) -> bool:
    if cfg.verify == "none":
        trace.append({"t": obs.t, "type": "verify", "msg": "skipped (ablation)"})
        return True
    clean_streak = 0
    for i in range(cfg.soak):
        obs.tick()
        if cfg.verify == "logs":
            ok = not any(lvl == "ERROR" and t == obs.t for t, lvl, _, _ in obs.logs())
        else:
            ok = all(v["ok"] is not False for k, v in obs.invariants().items() if k not in excluded)
        clean_streak = clean_streak + 1 if ok else 0
    passed = clean_streak >= max(1, cfg.soak - 2)   # 2-tick grace for in-flight work to drain
    trace.append({"t": obs.t, "type": "verify",
                  "msg": f"{'PASS' if passed else 'FAIL'}: {clean_streak}/{cfg.soak} clean ticks"
                         + (f" (excluded, handed to human: {sorted(excluded)})" if excluded else "")})
    return passed


# If an invariant source is unavailable, watch for risky changes to the component it guards.
BLIND_WATCH = {"replica_consistent": ("sync_job", "sync_corruption"),
               "queue_conservation": ("gateway", "queue_loss"),
               "payments_match_orders": ("order_writer", "silent_drop")}


def _coverage_gap(obs, blind: list[str]):
    for name in blind:
        if name not in BLIND_WATCH:
            continue
        svc, label = BLIND_WATCH[name]
        for t, lvl, src, msg in obs.logs(30):
            if src == "deploy" and svc in msg:
                return label, f"{name} check unavailable AND '{msg}' at t={t}: cannot verify data integrity"
    return None


def run_agent(obs, cfg: AgentConfig | None = None, horizon: int = 60) -> dict:
    cfg = cfg or AgentConfig()
    diagnose = DIAGNOSERS[cfg.diagnoser]
    trace: list[dict] = []
    streak: dict[str, int] = {}
    labels_seen: set[str] = set()
    excluded: set[str] = set()
    rounds, detected_at, recovered_at = 0, None, None
    outcome, escalated = "healthy", False
    blind = sorted(getattr(obs, "blind", ()))
    brain = None
    if cfg.llm is not None:
        from .llm import LLM
        brain = LLM(cfg.llm)
    if blind:
        trace.append({"t": obs.t, "type": "coverage", "msg": f"reduced coverage, unavailable: {blind}"})

    while obs.t < horizon:
        obs.tick()
        inv = obs.invariants()
        gap = _coverage_gap(obs, blind) if cfg.diagnoser == "runbook" else None
        if gap:   # a check we rely on is unavailable AND something risky changed: don't act blind, hand off
            label, why = gap
            detected_at, rounds = obs.t, rounds + 1
            labels_seen.add(label)
            trace.append({"t": obs.t, "type": "diagnose", "msg": {label: 0.4}, "evidence": {label: [why]}})
            act.execute(obs, [("escalate", {"report": {"reason": f"coverage gap - suspected {label}, not auto-fixing blind",
                                                       "evidence": why}})], trace)
            outcome, escalated = "escalated", True
            break
        if brain and blind and obs.t % cfg.watch_every == 0 and not any(v["ok"] is False for v in inv.values()):
            ai = brain.triage("watch", inv, obs.logs(), obs.metrics())
            if ai:
                trace.append({"t": obs.t, "type": "ai_watch", "msg": f"{ai['label']} ({ai['confidence']:.2f}): {ai['root_cause']}"})
            if ai and ai["label"] != "healthy" and ai["confidence"] >= 0.6:
                detected_at, rounds = obs.t, rounds + 1
                labels_seen.add(ai["label"])
                act.execute(obs, [("escalate", {"report": {"reason": f"AI watch (checks offline) suspects {ai['label']}"
                                                                     " - escalation only, no blind fixes", "ai_triage": ai}})], trace)
                outcome, escalated = "escalated", True
                break
        if cfg.diagnoser == "runbook":
            failing = [k for k, v in inv.items() if v["ok"] is False and k not in excluded]
        else:
            failing = ["logs"] if diagnose(inv, obs.logs(20), []).get("labels") else []
        for k in list(streak):
            if k not in failing:
                streak[k] = 0
        for k in failing:
            streak[k] = streak.get(k, 0) + 1
        if not any(streak.get(k, 0) >= cfg.debounce for k in failing):
            continue

        # ---- incident ----
        if detected_at is None:
            detected_at = obs.t
        rounds += 1
        trace.append({"t": obs.t, "type": "detect", "msg": f"round {rounds}: failing {failing}"})
        diag = diagnose(inv, obs.logs(), obs.metrics())
        labels_seen |= set(diag["labels"])
        trace.append({"t": obs.t, "type": "diagnose",
                      "msg": {l: round(v["confidence"], 2) for l, v in diag["labels"].items()},
                      "evidence": {l: v["evidence"] for l, v in diag["labels"].items()}})
        steps = act.plan(diag, inv)
        if brain and "unknown" in diag["labels"]:
            ai = brain.triage("unknown", inv, obs.logs(), obs.metrics())
            if ai:
                trace.append({"t": obs.t, "type": "ai_triage", "msg": f"{ai['root_cause']} -> human: {ai['human_action']}"})
                for op, args in steps:
                    if op == "escalate":
                        args["report"]["ai_triage"] = ai   # the AI writes the handoff; it adds no actions
        if not steps:
            trace.append({"t": obs.t, "type": "note", "msg": "no matching playbook"})
        act.execute(obs, steps, trace)
        if any(op == "escalate" for op, _ in steps):
            escalated = True
            if "unknown" in diag["labels"]:
                excluded |= act.LABEL_INVARIANTS["unknown"]

        if _verify(obs, cfg, excluded, trace):
            outcome = "escalated" if escalated else "recovered"
            recovered_at = obs.t
            break
        if rounds >= cfg.max_rounds:
            act.execute(obs, [("escalate", {"report": {
                "reason": f"verification still failing after {rounds} rounds - stopping, human needed",
                "failing": obs.failing(), "labels_tried": sorted(labels_seen)}})], trace)
            outcome, escalated = "escalated", True
            break
        streak = {}

    if detected_at is not None and recovered_at is None and outcome == "healthy":
        act.execute(obs, [("escalate", {"report": {"reason": "incident still open at end of watch window",
                                                    "failing": obs.failing()}})], trace)
        outcome = "escalated"
    return {"agent": cfg.name, "outcome": outcome, "labels": sorted(labels_seen), "rounds": rounds,
            "detected_at": detected_at, "recovered_at": recovered_at, "end_t": obs.t,
            "actions": [e["op"] for e in trace if e["type"] == "action"], "trace": trace}


def run_naive_restart(obs, horizon: int = 60) -> dict:
    """Baseline: 'have you tried turning it off and on again' bot. Restarts everything on any ERROR, claims success."""
    trace: list[dict] = []
    detected_at = None
    while obs.t < horizon:
        obs.tick()
        if any(lvl == "ERROR" and t == obs.t for t, lvl, _, _ in obs.logs(30)) or obs.failing():
            detected_at = obs.t
            for svc in ("queue_consumer", "order_writer", "receipt_worker", "sync_job"):
                trace.append({"t": obs.t, "type": "action", "op": "restart_service",
                              "msg": obs.admin("restart_service", name=svc)})
            return {"agent": "naive_restart", "outcome": "recovered", "labels": [], "rounds": 1,
                    "detected_at": detected_at, "recovered_at": obs.t, "end_t": obs.t,
                    "actions": [e["op"] for e in trace], "trace": trace}
    return {"agent": "naive_restart", "outcome": "healthy", "labels": [], "rounds": 0, "detected_at": None,
            "recovered_at": None, "end_t": obs.t, "actions": [], "trace": trace}
