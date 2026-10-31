"""Read-only observability layer: invariants, metrics and logs.

The agent sees the system ONLY through this class. It never reads Shop.faults.
Invariants are business truths that must always hold; silent failures break them
even when every log line looks healthy.
"""
from __future__ import annotations

from .sim import CATALOG, MAX_JOB_ATTEMPTS, RECEIPT_TTL, Shop, row_hash, vat_of

INVARIANTS = (
    "payments_match_orders",   # every captured payment has an order row
    "queue_conservation",      # produced == consumed + still queued
    "replica_consistent",      # cloud replica rows == local rows
    "receipts_timely",         # no receipt job pending longer than TTL
    "retry_budget",            # no job retried past the budget
    "prices_match_catalog",    # sold prices == HQ catalog
    "ledger_balanced",         # daily ledger == sum of order totals
    "order_arithmetic",        # VAT 15% and totals add up on every order
)


class Observer:
    def __init__(self, shop: Shop, blind: frozenset[str] = frozenset()):
        self._shop = shop
        self.blind = blind   # invariant sources that are unavailable (robustness tests)

    @property
    def t(self) -> int:
        return self._shop.t

    def invariants(self) -> dict[str, dict]:
        s = self._shop
        out: dict[str, dict] = {}
        missing = sorted(o for o in s.payments if o not in s.orders)
        out["payments_match_orders"] = {"ok": not missing, "count": len(missing), "sample": missing[:5]}
        lost = sorted(s.produced - s.consumed - set(s.queue))
        out["queue_conservation"] = {"ok": not lost, "count": len(lost), "sample": lost[:5]}
        bad = sorted(o for o, r in s.replica.items() if row_hash(r) != row_hash(s.orders.get(o)))
        out["replica_consistent"] = {"ok": not bad, "count": len(bad), "sample": bad[:5]}
        pending = [j for j in s.jobs.values() if j["status"] == "pending"]
        overdue = [j for j in pending if s.t - j["created"] > RECEIPT_TTL]
        out["receipts_timely"] = {
            "ok": not overdue, "count": len(overdue),
            "never_attempted": sum(1 for j in overdue if j["attempts"] == 0),
            "failing": sorted(j["id"] for j in overdue if j["attempts"] > 0),
        }
        over = sorted(j["id"] for j in pending if j["attempts"] > MAX_JOB_ATTEMPTS)
        errors = sorted({j["last_error"] for j in pending if j["attempts"] > MAX_JOB_ATTEMPTS})
        out["retry_budget"] = {"ok": not over, "count": len(over), "jobs": over, "errors": errors}
        bad_items = [i for i in s.order_items if i["unit_price"] != CATALOG[i["product_id"]]]
        bad_price = sorted({i["product_id"] for i in bad_items})
        out["prices_match_catalog"] = {"ok": not bad_price, "count": len(bad_items), "products": bad_price,
                                       "sample": [{"order": i["order_id"], "product": i["product_id"], "sold_halalas": i["unit_price"],
                                                   "catalog_halalas": CATALOG[i["product_id"]]} for i in bad_items[:3]]}
        ledger_sum = sum(r["final_total"] for r in s.orders.values())
        out["ledger_balanced"] = {"ok": ledger_sum == s.ledger_total, "count": abs(ledger_sum - s.ledger_total)}
        wrong = [o for o, r in s.orders.items()
                 if r["vat_amount"] != vat_of(r["total"] - r["discount_amount"])
                 or r["final_total"] != r["total"] - r["discount_amount"] + r["vat_amount"]]
        out["order_arithmetic"] = {"ok": not wrong, "count": len(wrong)}
        for name in self.blind:
            out[name] = {"ok": None, "count": None, "unavailable": True}
        return out

    def failing(self) -> list[str]:
        return [k for k, v in self.invariants().items() if v["ok"] is False]

    def metrics(self, last: int = 10) -> list[dict]:
        return [dict(m) for m in self._shop.metrics[-last:]]

    def logs(self, last: int = 80) -> list[tuple[int, str, str, str]]:
        return list(self._shop.logs[-last:])

    def admin(self, op: str, **kw) -> str:
        """Single write path to the system. Only the executor calls this (whitelisted ops)."""
        return self._shop.admin(op, **kw)

    def tick(self) -> None:
        self._shop.tick()
