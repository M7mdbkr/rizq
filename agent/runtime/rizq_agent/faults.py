"""Fault injector: the 5 failures named on the Challenge 1 page + one novel fault.

| label            | challenge example                          | root cause in the sim                      |
|------------------|--------------------------------------------|--------------------------------------------|
| silent_drop      | payments that silently drop orders         | order_writer pool exhausted, swallows write|
| queue_loss       | queue loses messages under load            | bad buffer config drops overflow on spikes |
| sync_corruption  | sync job slowly corrupts data              | bad sync deploy (v2) writes wrong VAT      |
| hung_workflow    | workflow that hangs forever                | receipt worker deadlock, heartbeat lies    |
| runaway_repair   | repair agent that never escalates          | poison receipt job + legacy auto-retry loop|
| unknown          | (novel, not in any runbook)                | POS price table tampered vs HQ catalog     |

runaway_repair and unknown are NOT auto-fixable: the correct outcome is a safe stop + escalation.
"""
from __future__ import annotations

from .sim import Shop

FIXABLE = {"silent_drop", "queue_loss", "sync_corruption", "hung_workflow"}
ESCALATE_ONLY = {"runaway_repair", "unknown"}
ALL_FAULTS = sorted(FIXABLE | ESCALATE_ONLY)


def inject(shop: Shop, label: str, severity: float = 1.0, sticky: bool = False) -> None:
    """sticky=True: the standard fix (restart) does NOT clear it - the agent must stop and escalate."""
    _inject(shop, label, severity)
    if sticky and label in shop.faults:
        shop.faults[label]["sticky"] = True


def _inject(shop: Shop, label: str, severity: float) -> None:
    rng = shop.rng
    if label == "silent_drop":
        shop.faults[label] = {"rate": 0.2 + 0.3 * severity * rng.random()}
    elif label == "queue_loss":
        shop.faults[label] = {"cap": rng.randint(6, 10)}
        start = shop.t + rng.randint(1, 3)
        shop.spikes.append((start, start + rng.randint(3, 6), rng.choice([4, 5, 6])))
        shop.log("WARN", "gateway", "queue: high watermark config reloaded")
    elif label == "sync_corruption":
        shop.faults[label] = {"growth": 0.04 + 0.06 * severity, "since": shop.t}
        shop.sync_version = "v2"
        if not getattr(shop, "quiet_deploys", False):   # some deploys don't announce themselves
            shop.log("INFO", "deploy", "sync_job updated to v2")
    elif label == "hung_workflow":
        shop.faults[label] = {}
    elif label == "runaway_repair":
        shop.faults[label] = {"n": rng.randint(1, 2), "jobs": []}
    elif label == "unknown":
        p = rng.choice(sorted(shop.price_table))
        shop.price_table[p] += rng.choice([-300, 200, 500])
        shop.faults[label] = {"product": p}
    else:
        raise ValueError(label)
