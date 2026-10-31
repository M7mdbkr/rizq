"""Mistakes sheet runner: every row of mistakes.csv is a test the agent must pass.

Each row runs `runs` times with different seeds (different timing, severity, load, wording).
A row PASSES only if every run passes. Add your own rows and re-run.
"""
from __future__ import annotations

import csv
import random

from .evaluate import AGENTS, AI_AGENT, Scenario, ai_config, run_one

SHEET_SEED = 5_000_000


def load(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def scenario_for(row: dict, idx: int, k: int) -> Scenario:
    seed = SHEET_SEED + idx * 1000 + k
    rng = random.Random(seed)
    faults = [f for f in row["faults"].split("+") if f]
    noisy = row.get("noisy_logs", "").strip().lower() == "yes"
    sc = Scenario(seed=seed, split="sheet", faults=faults, inject_at=rng.randint(5, 12), severity=rng.random(),
                  reword=noisy or rng.random() < 0.3, log_noise=0.9 if noisy else rng.uniform(0, 0.2),
                  benign_spike=row.get("rush_hour", "").strip().lower() == "yes" or rng.random() < 0.3,
                  sync_blips=[rng.randint(3, 40) for _ in range(rng.randint(0, 2))])
    if row.get("sticky", "").strip().lower() == "yes":
        sc.sticky = list(faults)
    sc.quiet_deploy = row.get("quiet_deploy", "").strip().lower() == "yes"
    if row.get("blind", "").strip():
        sc.blind = [b for b in row["blind"].split("+") if b]
    return sc


def run_sheet(path: str, runs: int = 20, agent: str = "rizq_agent", llm_backend="anthropic") -> list[dict]:
    out = []
    cfg = ai_config(llm_backend) if agent == AI_AGENT else AGENTS.get(agent)
    for idx, row in enumerate(load(path)):
        if row.get("needs_ai", "").strip().lower() == "yes" and agent != AI_AGENT:
            out.append({"id": row["id"], "mistake": row["mistake"], "criterion": row["criterion"], "agent": agent,
                        "runs": 0, "passed": 0, "pass_%": None, "status": "SKIP", "expected": row["expected_outcome"],
                        "false_recovery_claims": 0, "avg_detect_ticks": None, "avg_recover_ticks": None,
                        "avg_actions": None, "first_failure": "needs the AI layer (run with --agent rizq_agent_ai)"})
            continue
        results = []
        for k in range(runs):
            sc = scenario_for(row, idx, k)
            if sc.expected_outcome != row["expected_outcome"]:
                raise ValueError(f"{row['id']}: sheet says {row['expected_outcome']}, scenario implies {sc.expected_outcome}")
            results.append(run_one(sc, agent, cfg))
        passed = sum(r["success"] for r in results)
        mean = lambda key: round(sum(r[key] for r in results if r[key] is not None) /
                                 max(1, sum(1 for r in results if r[key] is not None)), 1)
        fail = next((r for r in results if not r["success"]), None)
        out.append({"id": row["id"], "mistake": row["mistake"], "criterion": row["criterion"], "agent": agent,
                    "runs": runs, "passed": passed, "pass_%": round(100 * passed / runs, 1),
                    "status": "PASS" if passed == runs else "FAIL",
                    "expected": row["expected_outcome"], "false_recovery_claims": sum(r["false_recovery"] for r in results),
                    "avg_detect_ticks": mean("mttd"), "avg_recover_ticks": mean("mttr"), "avg_actions": mean("n_actions"),
                    "first_failure": "" if fail is None else
                    f"seed={fail['seed']} got {fail['outcome']} labels={fail['labels']} fixed={fail['fixed']}"})
    return out


def write(rows: list[dict], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
