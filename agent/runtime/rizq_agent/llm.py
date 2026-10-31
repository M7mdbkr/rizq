"""LLM triage layer: used ONLY where the deterministic runbook runs out.

1. `unknown` diagnosis  -> the LLM writes the root-cause hypothesis + human handoff (it cannot trigger fixes).
2. reduced coverage      -> the LLM watches logs/metrics for risk the rules can't see (it can only escalate).

Safety contract (enforced in code, tested in tests/test_llm.py):
- output must be JSON with a label from a CLOSED set, else it is coerced to "unknown"
- the LLM never chooses actions; the planner only maps known labels to whitelisted playbooks,
  and LLM-sourced labels are escalation-only
- every call is cached (results/llm_cache.json) so results are reproducible without an API key

Backends: "anthropic" (stdlib HTTP, key from env ANTHROPIC_API_KEY or a local .env file),
"cache" (replay recorded answers only), or any callable(system, user) -> str (tests).
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
CACHE_PATH = ROOT / "results" / "llm_cache.json"
MODEL = os.environ.get("RIZQ_MODEL", "claude-haiku-5-5")
WATCH_LABELS = ("healthy", "silent_drop", "queue_loss", "sync_corruption", "hung_workflow", "runaway_repair", "unknown")

SYSTEM = """You are the triage brain of Rizq Agent, an SRE agent guarding a restaurant POS
(orders, payments, VAT 15%, ZATCA receipts, delivery-app queue, cloud sync).
You only see evidence. You NEVER run fixes; a human acts on your report.
Known failure labels: silent_drop (paid orders not saved), queue_loss (messages lost under load),
sync_corruption (cloud replica rows wrong), hung_workflow (receipt worker frozen),
runaway_repair (a job retried forever), unknown (anything else), healthy (nothing wrong).
Answer with ONE JSON object and nothing else:
{"label": <one label>, "confidence": <0..1>, "root_cause": <one sentence, cite exact ids/values from the evidence>,
 "affected": [<ids>], "human_action": <one sentence>, "why_not_auto_fix": <one sentence>}
Be conservative: if the evidence does not clearly show a problem, answer healthy with low confidence."""


def _load_key() -> str | None:
    key = os.environ.get("ANTHROPIC_API_KEY")
    env = ROOT / ".env"
    if not key and env.exists():
        for line in env.read_text().splitlines():
            if line.strip().startswith("ANTHROPIC_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
    return key or None


def available() -> bool:
    return _load_key() is not None


_CACHE: dict | None = None
STATS = {"calls": 0, "cache_hits": 0, "api_calls": 0, "misses": 0}


def _cache() -> dict:
    global _CACHE
    if _CACHE is None:
        _CACHE = json.loads(CACHE_PATH.read_text()) if CACHE_PATH.exists() else {}
    return _CACHE


def save_cache() -> None:
    if _CACHE:
        CACHE_PATH.parent.mkdir(exist_ok=True)
        CACHE_PATH.write_text(json.dumps(_CACHE, indent=0, sort_keys=True))


class LLM:
    """One instance per agent run (bounded number of calls per incident)."""
    def __init__(self, backend="anthropic", max_calls: int = 12):
        self.backend = backend
        self.max_calls = max_calls
        self.calls = 0

    def _raw(self, system: str, user: str) -> str | None:
        STATS["calls"] += 1
        if callable(self.backend):
            return self.backend(system, user)
        k = hashlib.sha256(f"{MODEL}|{system}|{user}".encode()).hexdigest()[:24]
        cache = _cache()
        if k in cache:
            STATS["cache_hits"] += 1
            return cache[k]
        if self.backend == "anthropic" and available():
            STATS["api_calls"] += 1
            try:
                cache[k] = _anthropic(system, user)
            except Exception as e:  # network/quota errors must never crash the agent
                STATS["misses"] += 1
                return None
            return cache[k]
        STATS["misses"] += 1
        return None

    def triage(self, mode: str, invariants: dict, logs: list, metrics: list) -> dict | None:
        """mode: 'unknown' (explain what rules couldn't) or 'watch' (blind spot, look for risk)."""
        if self.calls >= self.max_calls:
            return None
        self.calls += 1
        failing = {k: v for k, v in invariants.items() if v.get("ok") is False}
        blind = sorted(k for k, v in invariants.items() if v.get("unavailable"))
        user = json.dumps({
            "mode": mode,
            "task": ("The deterministic runbook could not explain these violations. Find the most likely root cause."
                     if mode == "unknown" else
                     f"These checks are OFFLINE: {blind}. Using logs and metrics only, is something going wrong?"),
            "failing_invariants": failing, "unavailable_checks": blind,
            "metrics_last5": metrics[-5:], "logs_last40": [f"t={t} {lvl} {svc}: {m}" for t, lvl, svc, m in logs[-40:]],
        }, default=str)
        return parse(self._raw(SYSTEM, user))


def parse(text: str | None) -> dict | None:
    """Strict parser: anything malformed or outside the closed label set becomes a safe 'unknown'."""
    if text is None:
        return None
    try:
        s = text[text.index("{"): text.rindex("}") + 1]
        d = json.loads(s)
    except (ValueError, json.JSONDecodeError):
        return {"label": "unknown", "confidence": 0.0, "root_cause": "unparseable model output", "affected": [],
                "human_action": "inspect manually", "why_not_auto_fix": "model output invalid", "coerced": True}
    label = d.get("label")
    coerced = label not in WATCH_LABELS
    try:
        conf = max(0.0, min(1.0, float(d.get("confidence", 0))))
    except (TypeError, ValueError):
        conf, coerced = 0.0, True
    return {"label": "unknown" if coerced else label, "confidence": conf,
            "root_cause": str(d.get("root_cause", ""))[:400], "affected": [str(a) for a in d.get("affected", [])][:20],
            "human_action": str(d.get("human_action", ""))[:300], "why_not_auto_fix": str(d.get("why_not_auto_fix", ""))[:300],
            "coerced": coerced}


def _anthropic(system: str, user: str) -> str:
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps({"model": MODEL, "max_tokens": 400, "temperature": 0, "system": system,
                         "messages": [{"role": "user", "content": user}]}).encode(),
        headers={"x-api-key": _load_key(), "anthropic-version": "2023-06-01", "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        body = json.loads(r.read())
    return "".join(b.get("text", "") for b in body.get("content", []))
