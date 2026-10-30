"""Target system: a Rizq-style restaurant POS backend (deterministic, seeded).

Flow per tick:
  delivery apps / cashier -> gateway queue -> consumer -> payment capture
  -> order writer (local DB: orders, order_items, stock, ledger)
  -> receipt worker (ZATCA-style QR receipt jobs) -> sync job (local -> cloud replica)

Money is stored as integer halalas (1 SAR = 100) so arithmetic is exact.

`Shop.faults` is GROUND TRUTH used only by the fault injector, the admin API and the
evaluator. The agent never reads it (enforced by tests/test_safety.py).
"""
from __future__ import annotations

import hashlib
import random

VAT_PCT = 15
RECEIPT_TTL = 3          # ticks a receipt job may stay pending
MAX_JOB_ATTEMPTS = 5     # retry budget for a single receipt job
WORKER_CAPACITY = 50     # receipt jobs per tick
LOG_WINDOW = 400

# Official HQ price list (halalas). The POS keeps its own copy in `price_table`.
CATALOG = {"P01": 1800, "P02": 2500, "P03": 1200, "P04": 900, "P05": 3200, "P06": 600}

SERVICES = ("gateway", "queue_consumer", "payments", "order_writer", "receipt_worker", "sync_job")


def vat_of(net: int) -> int:
    return (net * VAT_PCT + 50) // 100


def row_hash(row: dict | None) -> str:
    if row is None:
        return "-"
    key = f"{row['id']}|{row['total']}|{row['discount_amount']}|{row['vat_amount']}|{row['final_total']}|{row['status']}"
    return hashlib.sha1(key.encode()).hexdigest()[:12]


# Normal log templates. Two wordings each so "reworded logs" can be tested.
TEMPLATES = {
    "gateway": ["gateway: accepted {n} order messages (depth={d})", "gw: {n} msgs in, queue depth {d}"],
    "consumer": ["queue_consumer: processed {n} messages", "consumer: handled {n} msgs"],
    "payments": ["payments: captured {n} payments", "pay-svc: {n} charges authorised"],
    "writer": ["order_writer: wrote {n} orders", "db-writer: persisted {n} orders"],
    "receipts": ["receipt_worker: issued {n} receipts", "zatca: {n} invoices signed + QR"],
    "heartbeat": ["receipt_worker: heartbeat ok", "receipt_worker: alive"],
    "sync": ["sync_job: synced {n} rows (version {v})", "replicator {v}: pushed {n} rows"],
}

NOISE_LINES = [
    ("ERROR", "printer", "printer-2: paper low"),
    ("WARN", "wifi", "ap-kitchen: client reconnect"),
    ("WARN", "pos-ui", "tablet-3: slow render 820ms"),
    ("ERROR", "telegram", "daily report: rate limited, will retry"),
    ("INFO", "auth", "cashier login"),
]


class Shop:
    def __init__(self, seed: int, base_load: int = 4, reword: bool = False, log_noise: float = 0.0):
        self.rng = random.Random(seed)
        self.seed = seed
        self.t = 0
        self.base_load = base_load
        self.reword = reword
        self.log_noise = log_noise
        self.spikes: list[tuple[int, int, int]] = []   # (start, end, multiplier)
        self.sync_blips: set[int] = set()               # ticks where sync is slow (benign)
        self._seq = 0
        # queue
        self.outbox: dict[str, dict] = {}   # delivery partners keep every message (replayable)
        self.produced: set[str] = set()
        self.consumed: set[str] = set()
        self.queue: list[str] = []
        self.queue_cap = 10_000
        self.consumer_rate = 10
        # money + local DB
        self.price_table = dict(CATALOG)
        self.payments: dict[str, int] = {}
        self.orders: dict[str, dict] = {}
        self.order_items: list[dict] = []
        self.stock_tx: list[dict] = []
        self.ledger_total = 0
        # receipts workflow
        self.jobs: dict[str, dict] = {}
        self.receipts: set[str] = set()
        # sync
        self.replica: dict[str, dict] = {}
        self.sync_paused = False
        self.sync_version = "v1"
        self.services = {s: "up" for s in SERVICES}
        self.faults: dict[str, dict] = {}   # GROUND TRUTH ONLY
        self.logs: list[tuple[int, str, str, str]] = []
        self.metrics: list[dict] = []
        self.escalations: list[dict] = []
        self.action_log: list[dict] = []

    # ------------------------------------------------------------------ logging
    def log(self, level: str, service: str, msg: str) -> None:
        self.logs.append((self.t, level, service, msg))
        if len(self.logs) > LOG_WINDOW:
            del self.logs[: len(self.logs) - LOG_WINDOW]

    def _tpl(self, key: str, **kw) -> str:
        return TEMPLATES[key][1 if self.reword else 0].format(**kw)

    # ------------------------------------------------------------------ main loop
    def tick(self) -> None:
        self.t += 1
        self._tick_stats = {"captured": 0, "written": 0, "receipts": 0, "retries": 0, "synced": 0}
        n = self._load()
        for _ in range(n):
            self._produce()
        self.log("INFO", "gateway", self._tpl("gateway", n=n, d=len(self.queue)))
        self._consume()
        self._run_receipts()
        self._run_sync()
        if self.log_noise and self.rng.random() < self.log_noise:
            lvl, svc, msg = self.rng.choice(NOISE_LINES)
            self.log(lvl, svc, msg)
        self._record_metrics()

    def _load(self) -> int:
        mult = 1
        for s, e, m in self.spikes:
            if s <= self.t <= e:
                mult = m
        return max(0, self.base_load * mult + self.rng.randint(-1, 1))

    def _produce(self) -> None:
        self._seq += 1
        mid, oid = f"M{self._seq:05d}", f"O{self._seq:05d}"
        items = []
        for p in self.rng.sample(sorted(self.price_table), self.rng.randint(1, 3)):
            q = self.rng.randint(1, 2)
            items.append({"product_id": p, "quantity": q, "unit_price": self.price_table[p],
                          "total": q * self.price_table[p]})
        total = sum(i["total"] for i in items)
        discount = total // 10 if self.rng.random() < 0.1 else 0
        net = total - discount
        vat = vat_of(net)
        payload = {"id": oid, "msg_id": mid, "items": items, "total": total, "discount_amount": discount,
                   "vat_amount": vat, "final_total": net + vat,
                   "payment_method": self.rng.choice(["cash", "akita", "merzool", "ninja"])}
        self.outbox[mid] = payload
        self.produced.add(mid)
        f = self.faults.get("queue_loss")
        if f and len(self.queue) >= f["cap"]:
            return  # silently dropped: the buffer is full and the bug swallows the overflow
        self.queue.append(mid)

    def _consume(self) -> None:
        if self.services["queue_consumer"] != "up":
            return
        n = 0
        while self.queue and n < self.consumer_rate:
            mid = self.queue.pop(0)
            self.consumed.add(mid)
            self._process(self.outbox[mid])
            n += 1
        self.log("INFO", "queue_consumer", self._tpl("consumer", n=n))
        self.log("INFO", "payments", self._tpl("payments", n=self._tick_stats["captured"]))
        self.log("INFO", "order_writer", self._tpl("writer", n=self._tick_stats["written"]))

    def _process(self, p: dict) -> None:
        self.payments[p["id"]] = p["final_total"]
        self._tick_stats["captured"] += 1
        f = self.faults.get("silent_drop")
        if f and self.rng.random() < f["rate"]:
            return  # write swallowed by an exhausted connection pool: no error, no order
        self._write_order(p)

    def _write_order(self, p: dict) -> None:
        row = {k: p[k] for k in ("id", "total", "discount_amount", "vat_amount", "final_total", "payment_method")}
        row.update(status="completed", created_at=self.t)
        self.orders[p["id"]] = row
        for it in p["items"]:
            self.order_items.append({"order_id": p["id"], **it})
            self.stock_tx.append({"product_id": it["product_id"], "type": "sale", "quantity": it["quantity"],
                                  "order_id": p["id"]})
        self.ledger_total += row["final_total"]
        jid = "J" + p["id"][1:]
        poison = self.faults.get("runaway_repair")
        self.jobs[jid] = {"id": jid, "order_id": p["id"], "created": self.t, "attempts": 0,
                          "status": "pending", "last_error": None,
                          "poison": bool(poison and len(poison["jobs"]) < poison["n"])}
        if self.jobs[jid]["poison"]:
            poison["jobs"].append(jid)
        self._tick_stats["written"] += 1

    def _run_receipts(self) -> None:
        if "hung_workflow" in self.faults or self.services["receipt_worker"] != "up":
            self.log("DEBUG", "receipt_worker", self._tpl("heartbeat"))  # the heartbeat lies
            return
        done = 0
        pending = [j for j in self.jobs.values() if j["status"] == "pending"]
        for j in pending[:WORKER_CAPACITY]:
            j["attempts"] += 1
            if j["poison"]:
                j["last_error"] = "ZATCA rejected: invalid seller VAT number"
                self._tick_stats["retries"] += 1
                self.log("ERROR", "receipt_worker",
                         f"job {j['id']} failed: {j['last_error']} (attempt {j['attempts']}) - auto-retrying")
                continue
            j["status"] = "done"
            self.receipts.add(j["order_id"])
            done += 1
        self._tick_stats["receipts"] = done
        self.log("INFO", "receipt_worker", self._tpl("receipts", n=done))

    def _run_sync(self) -> None:
        if self.sync_paused or self.services["sync_job"] != "up" or self.t in self.sync_blips:
            return
        f = self.faults.get("sync_corruption")
        n = 0
        for oid, row in self.orders.items():
            if oid in self.replica:
                continue
            copy = dict(row)
            if f:
                rate = min(0.7, f["growth"] * (self.t - f["since"] + 1))
                if self.rng.random() < rate:
                    copy["vat_amount"] += self.rng.choice([-100, 100, 37])
                    copy["final_total"] = copy["total"] - copy["discount_amount"] + copy["vat_amount"]
            self.replica[oid] = copy
            n += 1
        self._tick_stats["synced"] = n
        self.log("INFO", "sync_job", self._tpl("sync", n=n, v=self.sync_version))

    def _record_metrics(self) -> None:
        pending = [j for j in self.jobs.values() if j["status"] == "pending"]
        self.metrics.append({
            "t": self.t, "queue_depth": len(self.queue), "produced": len(self.produced),
            "consumed": len(self.consumed), "payments": len(self.payments), "orders": len(self.orders),
            "receipts": len(self.receipts), "pending_jobs": len(pending),
            "oldest_job_age": max((self.t - j["created"] for j in pending), default=0),
            "replica_rows": len(self.replica), **{f"tick_{k}": v for k, v in self._tick_stats.items()},
        })

    # ------------------------------------------------------------------ admin API
    # The only way an agent may change the system. Each call mirrors a real ops tool.
    def admin(self, op: str, **kw) -> str:
        self.action_log.append({"t": self.t, "op": op, **kw})
        fn = getattr(self, f"_op_{op}", None)
        if fn is None:
            raise ValueError(f"unknown admin op {op}")
        return fn(**kw)

    def _op_restart_service(self, name: str) -> str:
        if name not in SERVICES:
            raise ValueError(name)
        # A restart resets in-process state: clears transient faults living in that service.
        cleared = {"order_writer": "silent_drop", "receipt_worker": "hung_workflow"}.get(name)
        if cleared and not self.faults.get(cleared, {}).get("sticky"):
            self.faults.pop(cleared, None)
        return f"restarted {name}"

    def _op_reconcile_orders(self) -> str:
        """Rebuild orders that have a captured payment but no order row (payments are the source of truth)."""
        payload_by_oid = {p["id"]: p for p in self.outbox.values()}
        missing = [oid for oid in self.payments if oid not in self.orders]
        for oid in missing:
            self._write_order(payload_by_oid[oid])
        return f"reconciled {len(missing)} orders from payment records"

    def _op_rollback_config(self, component: str) -> str:
        if component == "queue":
            self.faults.pop("queue_loss", None)
            self.queue_cap = 10_000
            return "queue buffer config rolled back"
        if component == "sync":
            self.faults.pop("sync_corruption", None)
            self.sync_version = "v1"
            return "sync_job rolled back to v1"
        raise ValueError(component)

    def _op_replay_lost_messages(self) -> str:
        lost = sorted(self.produced - self.consumed - set(self.queue))
        self.queue.extend(lost)
        return f"replayed {len(lost)} messages from partner outbox"

    def _op_pause_sync(self) -> str:
        self.sync_paused = True
        return "sync paused"

    def _op_resume_sync(self) -> str:
        self.sync_paused = False
        return "sync resumed"

    def _op_resync_replica(self) -> str:
        bad = [oid for oid, r in self.replica.items() if row_hash(r) != row_hash(self.orders.get(oid))]
        for oid in bad:
            self.replica[oid] = dict(self.orders[oid])
        return f"re-synced {len(bad)} corrupted replica rows from local DB"

    def _op_quarantine_jobs(self, job_ids: list[str]) -> str:
        for jid in job_ids:
            if jid in self.jobs:
                self.jobs[jid]["status"] = "quarantined"
        return f"quarantined {len(job_ids)} jobs (auto-retry stopped)"

    def _op_escalate(self, report: dict) -> str:
        self.escalations.append({"t": self.t, **report})
        return "escalated to on-call human"
