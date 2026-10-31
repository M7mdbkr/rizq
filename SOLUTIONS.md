# What Rizq actually solves

Nine problems a Saudi restaurant owner has this week. Each one names the feature that
solves it, the test that proves it, and — where it matters — what is still missing.

---

## 1 · "My card payments vanish and nothing tells me"

**The problem.** On a fresh install, a mada checkout produced no order, no receipt and no
error. The console showed `CHECK constraint failed: payment_method`. The money was taken
at the terminal; Rizq had no record. The owner would find out at the end of the day, from
a till that does not balance.

**What Rizq does.** The agent checks business invariants rather than reading logs, because
this failure writes no log line. It finds orders whose items do not reconcile, stock moved
without a sale, a day that never reached the ledger, and payment methods it does not
recognise.

**Honest limit.** A *dropped* checkout leaves nothing in the database, so a scan cannot see
it. That needs comparison with the card terminal's settlement report. The bug is documented
as BUG-1 with its reproduction.

**Proof.** `tests/test_rizq.py` — 212 planted faults, 99.0% detected on the held-out set,
0 spurious findings.

---

## 2 · "I need a number and my developer is asleep"

**The problem.** Zakat, VAT for the month, margin by item, what is running low. Every one
is a ticket, a wait, and a bill.

**What Rizq does.** Ask in your own dialect and get the figure in **under a tenth of a
second**, computed from your own data:

> مبيعات اليوم: 2,343.70 ر.س من 16 طلب، منها 305.70 ر.س ضريبة.

Every figure is computed **twice**, by a query and by an independently written checker. If
they disagree you get no number and an escalation, because a confident wrong number costs
more than no answer.

**Proof.** `tests/test_skills.py`, `test_scenarios.py::S1`. Held-out: 90.0% action
accuracy, 11/11 answers matching an independent reference query.

---

## 3 · "The internet is down and the till still has to work"

**The problem.** Wi-Fi drops mid-service. A cloud assistant becomes a blank screen.

**What Rizq does.** Everything is local: the database is in the browser, the model is on
the machine, and the common questions never need a model at all. In the test, *any* attempt
to reach a model raises an error — and the questions still answer, which is what proves it.

**Proof.** `test_scenarios.py::S1_ItWorksWithNoInternet` — four questions answered with the
model path booby-trapped, dangerous requests still refused, all under five seconds.

---

## 4 · "My staff should not see my margins"

**The problem.** The chef needs to look up an order. He does not need last month's profit,
the VAT position, or a database export.

**What Rizq does.** Every employee has their own PIN and their own identity. Two workers
get *identical* functionality and separate conversations. Everything that discloses money
across orders, or changes or exports anything, is owner-only — enforced in the agent's
tools, so no clever prompt reaches around it, and again at the HTTP layer.

Two employees can never share a PIN: the till refuses to create a duplicate, and the server
refuses to resolve an ambiguous one rather than guessing who is signing in.

**Proof.** `test_scenarios.py::S2`, `test_permissions.py`, `test_web_permissions.py` — a
worker gets `403` on scan, VAT, export and audit.

---

## 5 · "The accountant wants everything, and sends back a form"

**The problem.** A week of copying numbers into someone else's spreadsheet, by hand, badly.

**What Rizq does.** One click exports every table, a database copy, the audit log, chat
history and the integrity report as a single ZIP — **with every employee PIN redacted**.
Inside is a form with blanks. Hand that form back and it returns filled.

Anything that cannot be independently verified stays blank **with the reason**, so a figure
is never invented into a tax document.

**Proof.** `test_fill_and_export.py`, `test_scenarios.py::S3` — the exported form round-trips
with 14 blanks filled and the zakat left empty because it needs a balance Rizq does not store.

---

## 6 · "National Day is next week and I will forget the discount"

**The problem.** The occasions that matter commercially — National Day, Founding Day — land
on the same date every year and are remembered too late.

**What Rizq does.** Schedule it once:

> خصم اليوم الوطني السعودي 20% · يُجهَّز 22 سبتمبر · يتكرر كل سنة

It prepares the day before and repeats yearly with no further input. The discount is
**queued for the owner to apply in the till** — money never moves on a timer alone.

**Honest limit.** Hijri occasions (Ramadan, the two Eids) move every Gregorian year and are
not computed. They are scheduled by explicit date, because guessing the day of Eid wrongly
is worse than asking.

**Proof.** `test_schedule.py` — 18 tests, including that the queued discount is not applied
until a human applies it, and that it rolls forward to the following year by itself.

---

## 7 · "How do I know the AI is not making this up?"

**The problem.** An assistant that is confidently wrong is worse than no assistant.

**What Rizq does.** Every answer carries a score built from signals that can be checked —
did an independent checker agree, was the question matched to a real capability, were the
parameters yours, is there enough data behind the figure. **It never asks the model how
confident it is**, because models are badly calibrated about their own correctness.

Below 95% the answer is not presented as fact: it escalates to a larger local model, and if
that still does not clear the bar, to you — with the reason written out.

> ⚠️ ثقتي في هذه الإجابة 40% فقط — ما فيه أي طلبات في هذه الفترة.

**Proof.** `test_confidence.py` — an empty period scores 40 and is refused; a zakat estimate
is never silently approved; a model answer with no tool call scores 30.

---

## 8 · "My menu is a mess and deleting a section broke things"

**The problem.** The items screen listed newest-first with no grouping, so nothing could be
found. Deleting a section silently orphaned its items, because foreign keys were never
enabled — and the screen still said "deleted".

**What Rizq does.** Items group by section in menu order (المقبلات → المشاوي → الوجبات →
المشروبات → الحلويات). Deleting a populated section is refused with a clear reason. An item
that somehow belongs to no section is reported — and the agent **escalates instead of
guessing**, because choosing a menu classification is the owner's decision.

**Proof.** `tools/pos_patches/patch_categories.py`, and the `product_without_category`
invariant: 0 false positives on clean data, 3 correct escalations when a section is deleted.

---

## 9 · "I need a change and I am not paying for a sprint"

**The problem.** Small fixes wait behind someone else's backlog.

**What Rizq does.** Describe the change and the agent writes a targeted edit, reviewed before
it is applied. It fixed a real `FileNotFoundError` leak in its own code in **52 seconds on a
free local model**, and the test suite stayed green afterwards.

Guards: owner role **and** a separate code-admin password, same-origin only, rate limited,
syntax validated, and **structurally checked** — a proposal that guts a file or deletes
functions is rejected, which is not hypothetical: an earlier model silently deleted three.

**Honest limit.** It proposes; a human applies. It does not autonomously ship production code.

**Proof.** `test_code_agent.py` — 13 tests covering every guard.

---

## What it costs

Nothing per question. Python standard library only, a free local model, the database on your
own device. No API key, no subscription, no per-seat fee.

## What is not solved yet

- **ZATCA is not connected.** VAT and zakat are local estimates; nothing is submitted.
- **Employee PINs are stored in clear text** by the cashier. Fixing it needs the POS source.
- **Data is per-device.** Multi-branch needs a shared backend.
- **Arabic matching is keyword-based.** All three held-out misses come from missing
  normalisation. That is the next fix.

Full security position: [SECURITY.md](SECURITY.md).
