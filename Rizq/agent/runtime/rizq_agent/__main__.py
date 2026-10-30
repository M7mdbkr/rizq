"""CLI.

  python -m rizq_agent demo --faults silent_drop+queue_loss --seed 7
  python -m rizq_agent sheet [--runs 20]            # mistakes.csv -> results/mistakes_results.csv
  python -m rizq_agent eval  [--n 200]              # print dev + robustness + ablations
  python -m rizq_agent heldout [--n 300]            # run ONCE at the end; print results
  python -m rizq_agent rizq scan fixtures/rizq_dev.sqlite   # agent on a REAL Rizq database (original never modified)
  python -m rizq_agent rizq sheet [--split dev]             # 10 planted Rizq mistakes x 20 runs -> results/rizq_mistakes_results.csv
  python -m rizq_agent rizq eval  [--split dev] [--n 100]   # 1-3 mistakes at once; print results
  python -m rizq_agent chat [db] [--llm auto|claude|ollama|openai|rules]   # AI agent: LLM brain + POS tools
  python -m rizq_agent requests [--split dev|held_out]       # owner-request eval (Challenge 3 score)
  python -m rizq_agent ai [--n 20]                  # rules vs rules+LLM where the rules run out
  python -m rizq_agent web [--port 8000]            # local Rizq POS + AI dashboard
  (LLM: put ANTHROPIC_API_KEY=... in the package-root .env file; answers are cached in results/llm_cache.json)
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import time

from .agent import AgentConfig, run_agent
from .evaluate import AGENTS, AI_AGENT, AI_SUITES, SUITES, Scenario, _FaultSchedule, build_shop, evaluate, ground_truth, summarize
from . import llm
from .sheet import run_sheet, write

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "results")
COLS = ["n", "success_%", "diagnosis_%", "fixed_%", "false_recovery_claims", "false_alarm_%", "mttd_ticks", "mttr_ticks", "avg_actions"]
ICON = {"detect": "!!", "diagnose": "??", "dry_run": "..", "action": ">>", "verify": "OK", "coverage": "~~", "note": "--",
        "ai_watch": "AI", "ai_triage": "AI"}


def md_table(rows: list[tuple[str, dict]], first: str) -> str:
    out = [f"| {first} | " + " | ".join(COLS) + " |", "|" + "---|" * (len(COLS) + 1)]
    for name, s in rows:
        out.append(f"| {name} | " + " | ".join("-" if s[c] is None else str(s[c]) for c in COLS) + " |")
    return "\n".join(out)


def cmd_demo(a) -> None:
    faults = [f for f in a.faults.split("+") if f]
    sc = Scenario(seed=a.seed, split="demo", faults=faults, inject_at=8, severity=0.7, reword=False,
                  log_noise=0.2, benign_spike=False, sync_blips=[],
                  sticky=faults if a.sticky else [], blind=["replica_consistent"] if a.blind else [],
                  quiet_deploy=a.quiet)
    shop = build_shop(sc)
    obs = _FaultSchedule(shop, sc)
    res = run_agent(obs, AgentConfig(llm="anthropic" if a.ai else None), 60)
    llm.save_cache()
    print(f"Rizq Agent demo | injected at t=8: {faults or 'nothing'} | sticky={a.sticky} blind={a.blind}\n")
    for e in res["trace"]:
        msg = e["msg"] if isinstance(e["msg"], str) else json.dumps(e["msg"])
        print(f"t={e['t']:>3} {ICON.get(e['type'], '  ')} {e['type']:<9} {msg}")
        for label, ev in (e.get("evidence") or {}).items():
            for line in ev:
                print(f"{'':18}evidence[{label}]: {line}")
    gt = ground_truth(shop, sc)
    print(f"\nAGENT SAYS: {res['outcome'].upper()}  | diagnosis: {res['labels']}")
    print(f"GROUND TRUTH: fixed={gt['fixed']} escalations={gt['escalations']} (expected outcome: {sc.expected_outcome})")
    for e in shop.escalations:
        print(f"ESCALATION REPORT: {json.dumps({k: v for k, v in e.items() if k != 'hypotheses'}, ensure_ascii=False, indent=1)}")
    if a.ai and llm.STATS["misses"]:
        print("NOTE: LLM unavailable (no ANTHROPIC_API_KEY and no cached answer) - ran rules only.")


def cmd_sheet(a) -> None:
    os.makedirs(RES, exist_ok=True)
    rows = run_sheet(os.path.join(ROOT, "mistakes.csv"), a.runs, a.agent)
    llm.save_cache()
    write(rows, os.path.join(RES, f"mistakes_results{'' if a.agent == 'rizq_agent' else '_' + a.agent}.csv"))
    for r in rows:
        print(f"{r['id']} {r['status']:4} {r['passed']:>3}/{r['runs']}  {r['mistake'][:60]}"
              + (f"   <- {r['first_failure']}" if r["first_failure"] else ""))
    ran = [r for r in rows if r["status"] != "SKIP"]
    print(f"\n{sum(r['status'] == 'PASS' for r in ran)}/{len(ran)} mistakes resolved correctly in every run"
          + (f" ({len(rows) - len(ran)} skipped: need the AI layer)" if len(ran) < len(rows) else ""))


def cmd_eval(a) -> None:
    os.makedirs(RES, exist_ok=True)
    t0 = time.time()
    parts = [f"# Rizq Agent results (dev split, n={a.n} per row, generated {time.strftime('%Y-%m-%d %H:%M')})\n",
             "Dev/iteration results only; held-out is a separate freeze-time run.\n"]
    parts += ["## 1. Agents and ablations (mixed scenarios)\n",
              md_table([(ag, summarize(evaluate("dev", a.n, ag))) for ag in [*AGENTS, "naive_restart"]], "agent"), ""]
    parts += ["## 2. Robustness suites (Rizq Agent)\n",
              md_table([(s, summarize(evaluate("dev", max(20, a.n // 4), "rizq_agent", s))) for s in SUITES], "suite"), ""]
    parts += ["## 3. Where each component earns its place\n",
              md_table([(f"{s} / {ag}", summarize(evaluate("dev", max(20, a.n // 4), ag, s)))
                        for s in ("double", "sticky_fix", "hung_workflow") for ag in ("rizq_agent", "no_verify", "no_retry_budget", "logs_only")], "suite / agent"), ""]
    parts.append(f"_runtime {time.time() - t0:.1f}s_\n")
    print("\n".join(parts))


def cmd_heldout(a) -> None:
    os.makedirs(RES, exist_ok=True)
    rows = []
    for ag in [*AGENTS, "naive_restart"]:
        rs = evaluate("held_out", a.n, ag)
        rows.append((ag, summarize(rs)))
        if ag == "rizq_agent":
            with open(os.path.join(RES, "held_out_runs.csv"), "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(rs[0]))
                w.writeheader()
                w.writerows(rs)
    text = (f"# HELD-OUT results (n={a.n}, seeds 9,000,000+, never used while building)\n\n"
            "Shifted distribution vs dev: 70% reworded logs, up to 50% log noise, more double faults.\n\n"
            + md_table(rows, "agent") + "\n")
    print(text)


def cmd_ai(a) -> None:
    os.makedirs(RES, exist_ok=True)
    if not llm.available() and not llm.CACHE_PATH.exists():
        print("No ANTHROPIC_API_KEY (env or .env) and no results/llm_cache.json - cannot run the AI comparison.")
        return
    rows = []
    for suite in (*AI_SUITES, "mixed"):
        for ag in ("rizq_agent", AI_AGENT):
            rows.append((f"{suite} / {'rules + AI' if ag == AI_AGENT else 'rules only'}",
                         summarize(evaluate(a.split, a.n, ag, suite, "anthropic" if llm.available() else "cache"))))
        llm.save_cache()
    cols = COLS + ["ai_root_cause_%"]
    out = [f"# Rules vs rules+LLM ({a.split} split, n={a.n}, model {llm.MODEL})\n",
           "The LLM is only consulted for `unknown` diagnoses and when a check is offline. It can only escalate.\n",
           f"| suite / agent | " + " | ".join(cols) + " |", "|" + "---|" * (len(cols) + 1)]
    out += [f"| {name} | " + " | ".join("-" if s[c] is None else str(s[c]) for c in cols) + " |" for name, s in rows]
    out.append(f"\nLLM calls: {llm.STATS}")
    print("\n".join(out))


def cmd_rizq(a) -> None:
    from . import rizq, rizq_eval
    os.makedirs(RES, exist_ok=True)
    if a.action == "demo":   # plant 5 real-world mistakes in a COPY of the real DB, then let the agent loose on it
        import random
        db = rizq.open_copy(a.db)
        rng, used = random.Random(31), set()
        kinds = ["RZ02_item_dropped", "RZ04_vat_wrong", "RZ06_stock_missing", "RZ07_customer_drift", "RZ09_duplicate"]
        print("Planting mistakes in a copy of the real Rizq DB:")
        for k in kinds:
            t = rizq_eval.mutate(db, k, rng, used)
            print(f"  - {rizq_eval.MUTATIONS[k][0]}  /  {rizq_eval.MUTATIONS[k][1]}  ({sorted(t['entities'])[0]})")
        print()
        a.db, a.action, a.label = db, "scan", "rizq_broken_demo"
    if a.action == "scan":
        label = getattr(a, "label", None) or os.path.splitext(os.path.basename(a.db))[0]
        prefix = os.path.join(RES, label)
        r = rizq.run_agent(a.db, prefix)
        print(f"Rizq database: {label if not isinstance(a.db, str) else a.db} (read-only copy; original untouched)")
        print(f"STATUS: {r['status'].upper()} | findings: {r['findings_before']} | auto-fixed (derived data): {r['auto_fixed']}"
              f" | escalated: {len(r['escalated'])} | verification passed: {r['verification']['passed']}")
        print("diagnosis:", json.dumps(r["diagnosis"], ensure_ascii=False))
        for f in r["escalated"][:20]:
            print(f"  ESCALATE {f['kind']:<22} {f['entity']:<16} {json.dumps(f['detail'], ensure_ascii=False)}\n{'':11}-> {f['human_action']}\n{'':11}-> {f['human_action_ar']}")
        print(f"\nwrote {prefix}.fixed.sqlite, {prefix}.audit.sql, {prefix}.report.json")
    elif a.action == "sheet":
        rows = rizq_eval.run_sheet(a.split, a.runs, ROOT)
        rizq_eval.write_csv(rows, os.path.join(RES, f"rizq_mistakes_results{'' if a.split == 'dev' else '_' + a.split}.csv"))
        for r in rows:
            print(f"{r['id']} {r['status']:4} {r['passed']:>3}/{r['runs']}  [{r['expected']:<8}] {r['mistake']}"
                  + (f"   <- {r['first_failure']}" if r["first_failure"] else ""))
        print(f"\n{sum(r['status'] == 'PASS' for r in rows)}/{len(rows)} real-POS mistakes handled correctly in every run")
    else:
        res = rizq_eval.evaluate(a.split, a.n, ROOT)
        base = rizq_eval.pristine_check(a.split, ROOT)
        text = (f"# Rizq real-database results ({a.split}, {a.n} scenarios with 1-3 planted mistakes each)\n\n"
                f"Fixture: `{rizq_eval.FIXTURES[a.split]}` (exported from the real Rizq app). "
                f"Findings on the untouched DB (real issues, not agent errors): {base}\n\n"
                "| " + " | ".join(res) + " |\n|" + "---|" * len(res) + "\n| " + " | ".join(str(v) for v in res.values()) + " |\n")
        print(text)


def cmd_chat(a) -> None:
    """Talk to the agent. It thinks with an LLM (or rules) and acts on the POS database through safe tools."""
    from . import copilot, rizq
    db = a.db
    if a.broken:   # demo: a copy of the real DB with 5 planted mistakes (same as `rizq demo`)
        import random
        from . import rizq_eval
        db, rng, used = rizq.open_copy(a.db), random.Random(31), set()
        for k in ["RZ02_item_dropped", "RZ04_vat_wrong", "RZ06_stock_missing", "RZ07_customer_drift", "RZ09_duplicate"]:
            rizq_eval.mutate(db, k, rng, used)
    confirm = (lambda q: (print(q + "y (--yes)"), True)[1]) if a.yes else None
    tools = copilot.PosTools(db, RES, confirm)
    brain = copilot.pick_brain(a.llm, a.model)
    print(f"Rizq Agent | brain: {brain.name}{' / ' + getattr(brain, 'model', '') if getattr(brain, 'model', '') else ''}"
          f" | system: Rizq POS ({'demo copy with 5 planted mistakes' if a.broken else a.db})")
    questions = [a.ask] if a.ask else None
    while True:
        q = questions.pop(0) if questions else (None if questions is not None else input("\nأنت / you > ").strip())
        if not q or q.lower() in ("exit", "quit", "خروج"):
            break
        try:
            print("\nRizq Agent > " + copilot.ask(brain, tools, q))
        except Exception as e:   # model offline / quota: never crash, fall back to the rules brain
            print(f"  (brain error: {e} -> switching to rules brain)")
            brain = copilot.RulesBrain()
            print("\nRizq Agent > " + copilot.ask(brain, tools, q))
        if questions is not None and not questions:
            break


def cmd_requests(a) -> None:
    from . import copilot, requests_eval
    if a.llm == "rules":
        res, label = requests_eval.evaluate(a.split), "rules"
    else:
        probe = copilot.pick_brain(a.llm, a.model)
        if isinstance(probe, copilot.RulesBrain):
            raise SystemExit("no model available (no ANTHROPIC_API_KEY and Ollama not running) - use --llm rules")
        label = f"{probe.name} / {getattr(probe, 'model', '')}"
        print(f"  brain: {label} (one fresh conversation per request)")
        res = requests_eval.evaluate_with_brain(a.split, lambda: copilot.pick_brain(a.llm, a.model))
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", label)
    report = requests_eval.write_report(res, brain=label)
    for k in ("n", "action_accuracy", "per_class", "answers_correct", "dangerous_refused", "false_refusals", "unsafe_writes", "original_file_unchanged"):
        print(f"  {k:26} {res[k]:.1%}" if k == "action_accuracy" else f"  {k:26} {res[k]}")
    for m in res["misses"]:
        print(f"  MISS {m['id']}: expected {m['expect']}, got {m['got']} {m['skill'] or ''} | {m['text']}")
    print(report)


def main() -> None:
    p = argparse.ArgumentParser(prog="rizq_agent")
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("demo"); d.add_argument("--faults", default="silent_drop+queue_loss")
    d.add_argument("--seed", type=int, default=7); d.add_argument("--sticky", action="store_true")
    d.add_argument("--blind", action="store_true"); d.add_argument("--quiet", action="store_true", help="no deploy log line")
    d.add_argument("--ai", action="store_true", help="enable the LLM triage layer")
    s = sub.add_parser("sheet"); s.add_argument("--runs", type=int, default=20); s.add_argument("--agent", default="rizq_agent")
    e = sub.add_parser("eval"); e.add_argument("--n", type=int, default=200)
    h = sub.add_parser("heldout"); h.add_argument("--n", type=int, default=300)
    z = sub.add_parser("rizq"); z.add_argument("action", choices=["scan", "demo", "sheet", "eval"])
    z.add_argument("db", nargs="?", default=os.path.join(ROOT, "fixtures", "rizq_dev.sqlite"))
    z.add_argument("--split", default="dev"); z.add_argument("--runs", type=int, default=20); z.add_argument("--n", type=int, default=100)
    c = sub.add_parser("chat", help="AI agent: talks to an LLM and acts on the POS through safe tools")
    c.add_argument("db", nargs="?", default=os.path.join(ROOT, "fixtures", "rizq_dev.sqlite"))
    c.add_argument("--llm", default="auto", choices=["auto", "claude", "ollama", "openai", "compatible", "rules"])
    c.add_argument("--model"); c.add_argument("--ask", help="one question, then exit")
    c.add_argument("--broken", action="store_true", help="use a copy with 5 planted mistakes")
    c.add_argument("--yes", action="store_true", help="auto-confirm fixes (demos only)")
    q = sub.add_parser("requests"); q.add_argument("--split", default="dev", choices=["dev", "held_out"])
    q.add_argument("--llm", default="rules", choices=["rules", "ollama", "claude", "openai", "compatible", "auto"]); q.add_argument("--model", default=None)
    i = sub.add_parser("ai"); i.add_argument("--n", type=int, default=20); i.add_argument("--split", default="dev")
    w = sub.add_parser("web", help="run the full-stack Rizq POS and AI dashboard locally")
    w.add_argument("--port", type=int, default=8000)
    a = p.parse_args()
    if a.cmd == "web":
        from .web import main as serve_web
        serve_web(a.port)
        return
    {"demo": cmd_demo, "sheet": cmd_sheet, "eval": cmd_eval, "heldout": cmd_heldout, "ai": cmd_ai, "rizq": cmd_rizq, "chat": cmd_chat, "requests": cmd_requests}[a.cmd](a)


if __name__ == "__main__":
    main()
