"""Evaluation harness: scenario generator, splits, ground-truth scoring, baselines, ablations.

Synthetic data: every scenario is generated from a seed. Splits use DISJOINT seed ranges.
held_out also shifts the distribution (reworded logs, more noise, more double faults) and is
never used while building the runbook.
"""
from __future__ import annotations

import itertools
import random
from dataclasses import dataclass, field

from .agent import AgentConfig, run_agent, run_naive_restart
from .faults import ALL_FAULTS, ESCALATE_ONLY, FIXABLE, inject
from .observe import Observer
from .sim import RECEIPT_TTL, Shop, row_hash

SPLITS = {"train": 0, "dev": 1_000_000, "held_out": 9_000_000}
HORIZON = 60
SETTLE = 8


@dataclass
class Scenario:
    seed: int
    split: str
    faults: list[str]
    inject_at: int
    severity: float
    reword: bool
    log_noise: float
    benign_spike: bool
    sync_blips: list[int]
    blind: list[str] = field(default_factory=list)
    sticky: list[str] = field(default_factory=list)
    quiet_deploy: bool = False

    @property
    def expected_outcome(self) -> str:
        if not self.faults:
            return "healthy"
        return "escalated" if (set(self.faults) & ESCALATE_ONLY or self.sticky or self.blind) else "recovered"


SUITES = ("mixed", "healthy", "single", "double", "noisy_logs", "load_spikes", "sticky_fix", "blind_replica",
          *ALL_FAULTS)
AI_SUITES = ("blind_no_hint", "blind_healthy", "unknown")   # where the LLM layer is supposed to add value


def make_scenario(i: int, split: str, suite: str = "mixed") -> Scenario:
    """suite: mixed | healthy | single | double | noisy_logs | load_spikes | sticky_fix | blind_replica | <fault label>"""
    seed = SPLITS[split] + i
    rng = random.Random(seed * 7919 + 17)
    held = split == "held_out"
    pairs = list(itertools.combinations(ALL_FAULTS, 2))
    kind = suite
    if suite in ("mixed", "noisy_logs", "load_spikes"):
        kind = rng.choices(["healthy", "single", "double"], weights=[1, 3, 3 if held else 2])[0]
    faults = {"healthy": [], "single": [rng.choice(ALL_FAULTS)], "double": list(rng.choice(pairs)),
              "sticky_fix": [rng.choice(["silent_drop", "hung_workflow"])],
              "blind_replica": ["sync_corruption"], "blind_no_hint": ["sync_corruption"], "blind_healthy": []}.get(kind)
    if faults is None:            # named single-fault suite, e.g. suite="unknown"
        faults = [suite]
    sc = Scenario(
        seed=seed, split=split, faults=faults, inject_at=rng.randint(5, 12), severity=rng.random(),
        reword=rng.random() < (0.7 if held else 0.3), log_noise=rng.uniform(0, 0.5 if held else 0.25),
        benign_spike=rng.random() < 0.4, sync_blips=[rng.randint(3, 40) for _ in range(rng.randint(0, 3))])
    if suite == "noisy_logs":
        sc.reword, sc.log_noise = True, 0.9
    if suite == "load_spikes":
        sc.benign_spike = True
    if suite == "sticky_fix":
        sc.sticky = list(faults)
    if suite in ("blind_replica", "blind_no_hint", "blind_healthy"):
        sc.blind = ["replica_consistent"]
    if suite == "blind_no_hint":
        sc.quiet_deploy = True
    return sc


def build_shop(sc: Scenario) -> Shop:
    shop = Shop(sc.seed, reword=sc.reword, log_noise=sc.log_noise)
    shop.sync_blips = set(sc.sync_blips)
    shop.quiet_deploys = sc.quiet_deploy
    if sc.benign_spike:
        s = shop.rng.randint(3, 30)
        shop.spikes.append((s, s + 3, 3))
    return shop


class _FaultSchedule(Observer):
    """Wraps the observer so faults are injected at a scheduled tick, invisibly to the agent."""
    def __init__(self, shop, sc):
        super().__init__(shop, blind=frozenset(sc.blind))
        self._sc, self._done = sc, False

    def tick(self):
        super().tick()
        if not self._done and self._shop.t >= self._sc.inject_at:
            for f in self._sc.faults:
                inject(self._shop, f, self._sc.severity, sticky=f in self._sc.sticky)
            self._done = True


def ground_truth(shop: Shop, sc: Scenario) -> dict:
    """Uses hidden state. Only the evaluator calls this."""
    active_fixable = sorted(set(shop.faults) & FIXABLE)
    data_ok = (all(o in shop.orders for o in shop.payments)
               and not (shop.produced - shop.consumed - set(shop.queue))
               and all(row_hash(r) == row_hash(shop.orders.get(o)) for o, r in shop.replica.items())
               and not any(j["status"] == "pending" and shop.t - j["created"] > RECEIPT_TTL for j in shop.jobs.values()))
    poison = [j for j in shop.jobs.values() if j["poison"]]
    poison_stopped = all(j["status"] == "quarantined" for j in poison)
    return {"fixed": not active_fixable and data_ok and poison_stopped, "active_fixable": active_fixable,
            "data_ok": data_ok, "poison_stopped": poison_stopped, "escalations": len(shop.escalations)}


def run_one(sc: Scenario, agent: str = "rizq_agent", cfg: AgentConfig | None = None) -> dict:
    shop = build_shop(sc)
    obs = _FaultSchedule(shop, sc)
    if agent == "naive_restart":
        res = run_naive_restart(obs, HORIZON)
    else:
        res = run_agent(obs, cfg or AgentConfig(), HORIZON)
    for _ in range(SETTLE):   # agent has stopped; let the system run alone, THEN judge (fair to every agent)
        obs.tick()
    gt = ground_truth(shop, sc)
    truth = sorted(sc.faults)
    expected = sc.expected_outcome
    diag_ok = sorted(res["labels"]) == truth
    claimed_fixed = res["outcome"] == "recovered"
    false_recovery = claimed_fixed and not gt["fixed"]
    if expected == "healthy":
        success = res["outcome"] == "healthy" and not res["actions"]
    elif expected == "recovered":
        success = diag_ok and res["outcome"] == "recovered" and gt["fixed"]
    elif sc.sticky or sc.blind:  # the standard fix cannot work: must try, then STOP within budget and escalate
        success = diag_ok and res["outcome"] == "escalated" and gt["escalations"] > 0 and res["rounds"] <= 3
    else:  # must fix what is fixable, stop the loop, and hand off the rest
        success = diag_ok and res["outcome"] == "escalated" and gt["fixed"] and gt["escalations"] > 0
    rc_hit = None
    if "unknown" in sc.faults:   # did the handoff name the real culprit product?
        rc_hit = any(shop.faults.get("unknown", {}).get("product", "?") in str(e.get("ai_triage", {}).get("root_cause", ""))
                     for e in shop.escalations)
    mttd = (res["detected_at"] - sc.inject_at) if (res["detected_at"] and sc.faults) else None
    mttr = (res["recovered_at"] - sc.inject_at) if (res["recovered_at"] and sc.faults) else None
    return {"seed": sc.seed, "split": sc.split, "faults": "+".join(truth) or "healthy", "agent": res["agent"],
            "expected": expected, "outcome": res["outcome"], "labels": "+".join(res["labels"]),
            "diag_ok": diag_ok, "fixed": gt["fixed"], "false_recovery": false_recovery, "success": success,
            "false_alarm": expected == "healthy" and bool(res["actions"]),
            "n_actions": len(res["actions"]), "rounds": res["rounds"], "mttd": mttd, "mttr": mttr, "rc_hit": rc_hit}


AGENTS = {
    "rizq_agent": AgentConfig(),
    "no_verify": AgentConfig(verify="none", name="no_verify"),
    "no_retry_budget": AgentConfig(max_rounds=50, name="no_retry_budget"),
    "debounce_2": AgentConfig(debounce=2, name="debounce_2"),
    "logs_only": AgentConfig(diagnoser="logs_only", verify="logs", name="logs_only"),
}
AI_AGENT = "rizq_agent_ai"


def ai_config(backend="anthropic") -> AgentConfig:
    return AgentConfig(llm=backend, name=AI_AGENT)


def evaluate(split: str, n: int, agent: str = "rizq_agent", suite: str = "mixed", llm_backend="anthropic") -> list[dict]:
    cfg = ai_config(llm_backend) if agent == AI_AGENT else AGENTS.get(agent)
    return [run_one(make_scenario(i, split, suite), agent, cfg) for i in range(n)]


def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    faulty = [r for r in rows if r["expected"] != "healthy"]
    healthy = [r for r in rows if r["expected"] == "healthy"]
    pct = lambda xs, k: round(100 * sum(1 for x in xs if x[k]) / len(xs), 1) if xs else None
    mean = lambda xs: round(sum(xs) / len(xs), 1) if xs else None
    return {"n": n, "success_%": pct(rows, "success"), "diagnosis_%": pct(faulty, "diag_ok"),
            "fixed_%": pct(faulty, "fixed"), "false_recovery_claims": sum(r["false_recovery"] for r in rows),
            "false_alarm_%": pct(healthy, "false_alarm"),
            "mttd_ticks": mean([r["mttd"] for r in faulty if r["mttd"] is not None]),
            "mttr_ticks": mean([r["mttr"] for r in faulty if r["mttr"] is not None]),
            "avg_actions": mean([r["n_actions"] for r in rows]),
            "ai_root_cause_%": pct([r for r in rows if r.get("rc_hit") is not None], "rc_hit")}
