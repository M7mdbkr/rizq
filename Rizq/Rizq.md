# Rizq (رزق) — Restaurant POS + Rizq Agent — THE project file
Owner: Mohammad | Created: 30 Oct 2026 | Last update: 30 Oct 2026, ~23:30 Riyadh
**This is the single project document.** Project notes and reports are maintained here. The app, agent source, fixtures, and machine-readable evaluation artifacts remain in their working folders because the software needs them.

=====================================================================
## ★ INSTRUCTIONS FOR ANY AI MODEL — READ THIS FIRST (handover / keep-up)
=====================================================================
You are continuing work for Mohammad. Read this whole section before doing anything. Update it (in place) at the end of every work session.

### A. Who and what
- **Mohammad (Mohammed Bakr)**: Computer Engineering, Taif University, AI Engineer. Writes in Egyptian/Gulf Arabic and English (often voice-typed, so expect typos: "risk file" = Rizq file, "disk file" = this file). Reply in Arabic unless he writes in English; short, direct, no fluff.
- **Rizq (رزق)**: his own restaurant POS (React/TS bundle, SQLite in the browser, Arabic RTL, ZATCA-style VAT 15% receipts). Only the compiled bundle exists here (`rizq-pos/`); the source code is NOT available.
- **Rizq Agent**: the AI agent we built for Rizq. The restaurant owner describes a problem or need in any dialect; the agent answers with exact numbers, repairs derived data, builds a new verified read-only skill, escalates to a human, or refuses. Headline example from Mohammad: "the owner links zakat and income without me (the developer) touching the code".
- **Goal right now: WIN 1st place** at the HUMAIN "Future AI Builders Hackathon" (30-31 Oct 2026), **Challenge 3 — Autonomous Enterprise**, deadline **Sat 31 Oct 2026, 8:00 PM Riyadh**. Judging criteria and platform requirements: section 9.

### B. Mohammad's standing rules (follow exactly)
1. **ONE project, ONE document: this file.** Do not create new doc/notes/to-do files. Update this file in place. (Agent code lives in `agent/runtime/` inside this project.)
2. **His personal to-do list goes in the chat**, not in files.
3. **Never add or publish anything on the hackathon platform without his explicit OK.** Some fields may lock after saving, there is no auto-save, and texts are drafted in this file first.
4. Keep the current project name ("Rizq Agent: the POS that builds its own features"); he said do not rebrand.
5. LLM: use the **easiest free option**. Local Ollama works now with no key (`qwen3:8b` is installed). Claude/Anthropic is supported if he adds `ANTHROPIC_API_KEY` to `agent/runtime/.env`. Never ask him to paste keys in chat.
6. Every addition only after **all tests pass**; every feature comes with its tests; run the full suite again after.
7. **Held-out set (`eval/owner_requests_heldout.json`, simulator `heldout`) is run ONCE at freeze. Never tune against it.**
8. The agent must never edit money on an issued invoice, never delete data, never reveal PINs/tokens. Safety is enforced in the tools, not in prompts.
9. Never publish secrets (PINs in `rizq-pos/اقرأني.txt`, Telegram tokens).
10. Don't claim something works without running it. State what was not verified.
11. Global (from his CLAUDE.md): don't save sessions to his Obsidian "AI Black Box" unless he says احفظ/وثق; check his reference library before building from scratch (it was NOT found on this Mac: `~/Desktop/AI` does not exist).

### C. What happened, step by step (30 Oct 2026)
1. Before this session: **Rizq recovery agent** was built for Challenge 1 (agent that detects/repairs silent POS failures with invariants), tested on Rizq's REAL exported database, found 2 real Rizq bugs. Hackathon notes: `../../playbook.md`.
2. Mohammad asked for an AI agent inside Rizq that users talk to (zakat + income example) and a full program with tests.
3. Ideas compared on the 6 judging criteria (my estimate /60): POS recovery agent 48 · support chatbot 35 · day-close agent 39 · "cheaper as it learns" (Ch2) 44 · **"Rizq builds itself" (Ch3) 53 ← chosen** ("اعتمدها").
4. Platform: project created as DRAFT, About saved (name, description, Challenge = Autonomous Enterprise). Requirements read from the platform (section 9). Demo video link is REQUIRED.
5. Built: `skills.py` (6 skills with maker + independent checker, zakat estimate, learned skills via `build_skill`), `triage.py` (answer/repair/build/escalate/refuse), `requests_eval.py` + `eval/owner_requests_{dev,heldout}.json` (30 + 30 requests, written before the triage code), tests. Bugs found by the eval and fixed: VAT checker false alarm (1-halala rounding), "how much zakat do I **owe**" parsed cash as debt.
6. Historical note: duplicate project Markdown files formerly sat alongside the agent code. Their useful Rizq information is consolidated in this file; runtime code and artifacts remain in `agent/runtime/`.
7. Rizq POS started locally (`python3 -m http.server 4173` in `rizq-pos/`), this file opened in VS Code.
8. Agent connected to a free local LLM: auto-picks an installed Ollama model (`qwen3:8b` first), hard-dangerous requests are refused BEFORE the model, evaluation can run with the real model (`requests --llm ollama`). First real run: inventory question answered correctly via `list_skills` -> `run_skill` in ~30 s.

### D. Where things stand / what is in progress
- Tests: **99/99 pass** (30 Oct, ~23:40). The model-connection tests are in `tests/test_owner_requests.py`: hard refusals never reach the model, unclear requests still go to it, Arabic refusals in Arabic, local model auto-pick, scoring of model runs. Two holes the new tests found and fixed: "خل الشاي مجاني" (make an item free) and raw SQL like "UPDATE orders SET total=0" now get a hard refusal before any model call.
- A prior note says a real-model dev evaluation had started, but its completion/result was not present during this consolidation. Re-run it only if still needed; evaluation reports print to the terminal.

### E. Next steps (in order)
1. (done) failing triage test fixed; 99/99 green.
2. Read the real-model dev result; fix the misses (dev only); re-run.
3. F-04 dialect/typo robustness, F-05 ablations (rules only / no checker / no skill library / model), F-07 model calls per request falling after a skill is learned.
4. F-08 simple Arabic chat page for the demo video.
5. Sat before 3 PM: FREEZE -> run held-out ONCE (rules + model) -> put the numbers in section 0/7 and the deck.
6. Deck 8-10 slides (official order, section 9) as PDF; video script (<= 3 min); page texts (section 9 drafts) -> show Mohammad -> he approves -> fill the platform -> he publishes before 8 PM.

### F. Waiting on Mohammad (do not do these for him)
Record + upload the demo video (link is required) · background 1920x600, logo 100x100, 3 step images (any image model) · "Why should HUMAIN hire you" text in his own words · approve page texts · optional `ANTHROPIC_API_KEY` in `.env` · GitHub repo (or `gh auth login`) · Rizq source code into `Projects/Rizq/` · press Publish.

### G. How to run everything (from `agent/runtime/`)
```bash
python3 -m unittest discover -s tests                                 # all tests
python3 -m rizq_agent chat fixtures/rizq_dev.sqlite                     # chat (auto: Claude if key, else local Ollama, else rules)
python3 -m rizq_agent chat fixtures/rizq_dev.sqlite --llm ollama --ask "كم زكاتي؟"
python3 -m rizq_agent requests --split dev                              # owner-request eval, rules brain
python3 -m rizq_agent requests --split dev --llm ollama                 # same, real local model (~30 s per request)
python3 -m rizq_agent rizq sheet                                        # planted mistakes on the real Rizq DB
cd ../../rizq-pos && python3 -m http.server 4173                 # open http://localhost:4173 (the POS app)
```
Machine facts: macOS, Python 3.9.6 (keep `from __future__ import annotations`), Ollama running with qwen3:8b / qwen3.5:9b / qwen3.5:4b. SQLite cannot write directly into this synced Desktop folder: write to a temp file, then copy (`rizq.save_db`).
=====================================================================
Hackathon challenge: **Challenge 3 — Autonomous Enterprise** (platform project name: "Rizq Agent: the POS that builds its own features").
Code of the agent: `agent/runtime/` (inside this same Rizq project).

---------------------------------------------------------------------
## Current assistant integration update

- The existing cashier assistant connects to the local agent through `agent/runtime/web/pos-agent.js`. The assistant also appears in the worker navigation.
- Existing cashier changes such as prices, stock and settings are proposed to the signed-in owner for confirmation. Workers do not receive those action buttons. Server-side authorization and coverage of all actions still need implementation and testing.
- Each question imports a browser database snapshot. Agent repair tools operate on a copy and are not automatically synchronized to the cashier. Conversation state does not persist across imports. Multiple clients need isolation.
- A live API request returned a model response from Ollama `qwen3:8b` after calling `sales_summary`. Login and test the updated cashier screen separately. Verify model calculations and currency.
- `daily_ledger_drift` identifies a ledger mismatch and can be rebuilt on a working copy; `daily_ledger_blocked` escalates a mismatch when the related orders are under investigation or orders are missing. Live cashier write-back is not implemented.

## Agent, ZATCA and deployment status

- The assistant can propose a single-file code change by using `/code relative/path.py | describe the change` in the owner view. It produces a model-generated diff; the code admin separately approves application. It validates Python, JavaScript and JSON syntax, rejects stale proposals, and saves a backup. It does not automatically run generated code or tests.
- The code administrator must initialize a separate local password using `cd agent/runtime` followed by `python3 -m rizq_agent.code_agent`. Never put the password in chat or in the project document. The updated server needs to be restarted to load the code-edit endpoints. The original cashier bundle is compiled, larger than the single-file proposal limit, and its original React source is unavailable.
- The model-generated workflow was tested against a temporary example with the local Ollama Qwen3 model; server authentication rejected an incorrect password. Full owner and worker UI flows remain to be tested after setup.
- Capability inventory: `agent/runtime/features/capabilities.json`; scanner findings: `agent/runtime/problems/scanner.json`; parked features: `agent/runtime/features/parked.json`; known issues: `agent/runtime/problems/catalog.json`.
- Local model inference can run without paid APIs. Qwen3-8B upstream declares Apache 2.0; project and bundled dependency licensing and original source availability require review before the complete application can be called open source.
- ZATCA is NOT connected. Local VAT and zakat estimates do not submit returns or invoices. `rizq_agent/zatca.py` lists onboarding, signed invoice XML, SDK validation, and reporting/clearance work; it does not submit data. References: https://zatca.gov.sa/en/E-Invoicing/SystemsDevelopers/Pages/TechnicalRequirementsSpec.aspx and https://zatca.gov.sa/en/E-Invoicing/Introduction/Guidelines/Documents/DEVELOPER-PORTAL-MANUAL.pdf.
- The cashier database persists in the browser. The agent currently imports a temporary snapshot; it is not a production shared backend database, and agent changes do not automatically write back. Real server-side role authorization, persistent shared storage, backups, client isolation and migration from browser data are still required.

## Latest local assistant update

- The preferred local model is `qwen3.5:0.8b`, selected for speed. In a local demo-data test it called the sales tool; a separate temporary-file code proposal also succeeded. Small models can make mistakes and generated code requires review.
- The new `/api/chat/local` endpoint receives a fresh in-memory cashier snapshot per request, keeps model context per conversation and does not switch to an online provider. It does not replace the cashier's browser database with a shared server database.
- Chat history is stored in `agent/runtime/data/chat_memory.sqlite` and reloaded when the assistant is reopened. Recent messages are provided as model context. The current user ID is not a secure server-side authentication mechanism; do not claim conversations are protected from other local users. Only messages sent via the updated endpoint are recorded; older chats are not recovered.
- The 8000 server was restarted with the local chat, memory and code endpoints. The cashier browser database remains separate from the server snapshot. Test servers on other ports use separate browser storage.
- Rizq uses local Tajawal font files with the accompanying OFL license; the custom Made in KSA wordmark does not represent official Saudi Made certification. The official Saudi Font has separate license terms, and the official Saudi Made certification mark needs authorization.
- Major challenge list: `agent/runtime/problems/challenges.json`. ZATCA, server-side permissions, durable shared database, and production-grade feature generation remain incomplete.

## Restart and verification update

- The local service was restarted on 127.0.0.1:8000 with the lightweight local model, persistent chat history and code proposal endpoints. The current browser database was not deleted. A snapshot backup was saved under `agent/runtime/data/backups/`.
- The cashier login screen was observed in Chrome after restart; the assistant still needs a signed-in end-to-end check. The model and memory path were exercised earlier using demo data and a temporary history database.
- Full test suite: 109 tests passed. Existing resource warnings remain. A passing suite does not mean the full product is production-ready or ZATCA-connected.
- Daily ledgers are now blocked from automatic rebuild on days with suspect orders or when order rows are missing. The agent works on a database copy; live cashier write-back remains unfinished.
- The server and browser identify chat memory using a client-supplied user ID, not authenticated server-side identity. Until permissions are implemented, do not treat local chat history as protected from other local users.

## 0. Status in one glance
| Area | State |
|---|---|
| Rizq POS app | working local app, compiled bundle only (`rizq-pos/`), source NOT available |
| Rizq Agent (chat, skills, triage, repair, escalate) | built, **99/99 tests pass** (30 Oct 2026, ~23:40) |
| LLM connection | works with a FREE local model (Ollama `qwen3:8b`, auto-picked, no key). Claude used instead if `ANTHROPIC_API_KEY` is in `.env` |
| Owner-request eval, dev split (30 requests) | decision accuracy 100%, answers 12/12 vs independent SQL, dangerous refused 6/6, wrong refusals 0, unsafe writes 0 |
| Owner-request eval, **held-out** split (30 requests) | **NOT run yet** (run once at freeze) |
| Real LLM | local `qwen3:8b` tried for real (correct inventory answer via the skills, ~30 s); full dev eval with it running. Claude not tried (no key) |
| Hackathon platform page | created as a DRAFT (unpublished). About saved (name, description, challenge). Everything else empty |

Run (from `agent/runtime/`):
```bash
python3 -m unittest discover -s tests                 # all tests
python3 -m rizq_agent chat fixtures/rizq_dev.sqlite     # talk to the agent
python3 -m rizq_agent requests --split dev              # owner-request evaluation
python3 -m rizq_agent rizq sheet                        # 10 planted Rizq mistakes x 20 runs
```
Agent with Claude: put `ANTHROPIC_API_KEY=...` in `agent/runtime/.env`. Without it the agent runs in rules mode.

---------------------------------------------------------------------
## 1. What Rizq is [FACT]
- Restaurant cashier/POS, Arabic RTL, dark luxury UI ("رزق — نظام الكاشير الفاخر").
- React/TypeScript/Tailwind, SQLite running in the browser (sql.js / `sql-wasm.wasm`).
- Data lives inside each device's browser (one DB per device). No server, no cloud yet.
- Runs with `start.command` (Mac) / `start-windows.bat` -> `python3 -m http.server 4173`; iPad/phone on same Wi-Fi can open it.
- Logins: owner + cashier PINs (see اقرأني.txt — don't share them in public material).
- Has: VAT, QR receipts (ZATCA-style), Telegram daily report (telegramToken / telegramChatId / telegramAutoDaily), backup file, factory reset.
- Roadmap: local app -> Telegram bot -> WhatsApp orders -> cloud SaaS (Railway).
- Storage facts: DB = IndexedDB `rizq-pos` / `sqlite` / `db` (sql.js export); settings = localStorage `rizq-business`. Export script: `agent/runtime/tools/export_from_your_browser.js`.

## 2. Data model [FACT, from the real exported database]
| Table | Key fields |
|---|---|
| orders | id, total, discount_amount, vat_amount, final_total, payment_method (cash/akita/merzool/ninja), status (pending/completed/cancelled), created_at, staff_id, order_type, table_no |
| order_items | order_id, product_id, quantity, unit_price, total, name |
| products | id, name_ar, category_id, price, cost, stock, min_stock, status |
| stock_transactions | product_id, transaction_type (sale/restock/adjustment), quantity |
| daily_ledger | date, total_sales, total_vat, total_discount, total_cost, gross_profit, payment_breakdown |
| customers | phone, name, orders_count, total_spent, first_seen, last_seen |
| also | users, categories, discount_codes, report_exports, payment_methods, stock_consumption |
Fixtures (real exports): `agent/runtime/fixtures/rizq_dev.sqlite` (477 orders) and `rizq_heldout.sqlite` (held-out only).

## 3. Business invariants (what must ALWAYS be true)
1. sum(order_items.total) == orders.total
2. vat_amount == 15% x (total - discount_amount) — Rizq rounds each order to the halala its own way, so a 1-halala gap per order is normal and is NOT an error
3. final_total == total - discount_amount + vat_amount
4. every completed order has a QR receipt / invoice
5. daily_ledger.total_sales == sum(final_total of completed orders that day)  (cannot be checked today: `daily_ledger` is empty in the fixtures, see P-6)
6. every sold item has a matching `sale` stock_transaction
7. (future) local order count == cloud order count after sync

---------------------------------------------------------------------
## 4. The Rizq Agent — what is built (all in `agent/runtime/rizq_agent/`)
| Feature | File | How it stays safe |
|---|---|---|
| Chat in Arabic/English; brains: Claude, Ollama, OpenAI-compatible, or built-in rules | `copilot.py` | safety is enforced by the TOOLS, not by the prompt |
| **Triage**: every request becomes answer / repair / build / escalate / refuse. Dangerous words are checked first | `triage.py` | "delete", PIN/password, prompt injection, "set total to N", out-of-scope -> refuse, no tool runs |
| **Skills** with a maker + independent checker: `sales_period`, `vat_collected`, `inventory_value`, `profit_estimate`, `top_products`, `zakat_estimate` | `skills.py` | the answer is given only if both computations agree; otherwise the agent escalates instead of guessing |
| **Zakat estimate**: asks the owner for cash/bank, receivables, liabilities (Rizq does not store them); Hijri 2.5%, Gregorian ~2.5777% (2.5 x 365/354); optional nisab; always lists assumptions and says "confirm with ZATCA or an accountant" | `skills.py` | estimate, not a filing |
| **Build a new skill** (`build_skill`): two differently written SELECTs must return the same rows; installed only after the human types yes; later runs need no model | `skills.py` | SELECT-only, writes/PRAGMA/ATTACH rejected, same query twice rejected |
| Scan the real database (13 error kinds, section 6) | `rizq.py` | original file opened read-only; work happens on a copy |
| Repair DERIVED data only (line totals, stock movements, customer totals) after the human confirms `y` | `rizq.py`, `copilot.py` | never edits money on an issued invoice (ZATCA credit/debit note -> human) |
| Escalate to the owner with evidence | `copilot.py` | writes `results/escalations.jsonl` |
| Read-only SQL tool (one SELECT, 50 rows), 10-step budget per question | `copilot.py` | `PRAGMA query_only` |
| Owner-request evaluation (dev 30 + held-out 30) with independent reference SQL and before/after fingerprint | `requests_eval.py`, `eval/*.json` | counts ANY change as an unsafe write |
| Original POS recovery loop on a restaurant simulator (5 silent faults + 1 novel): detect -> diagnose -> dry-run -> fix -> verify -> escalate | `agent.py`, `sim.py`, `faults.py`, ... | whitelisted actions, retry budget 3 then escalate |

Owner requests the agent answers today (Arabic dialects + English):
| Ask | Skill |
|---|---|
| «كم مبيعاتي آخر 7 أيام؟» | sales_period |
| «كم الضريبة اللي حصّلتها هذا الشهر؟» | vat_collected |
| «كم قيمة المخزون؟» | inventory_value |
| «كم ربحي آخر 30 يوم؟» | profit_estimate |
| «ايش أكثر صنف انباع؟» | top_products |
| «كم زكاتي؟» (then cash/liabilities) | zakat_estimate |
| anything else that is a data question | build_skill (needs a model) |

---------------------------------------------------------------------
## 5. PROBLEMS and SOLUTIONS (ordered by priority)
Status: ✅ solved and tested | 🟡 partly | ⛔ needs Rizq source code | 👤 needs Mohammad.
How to use: fix the problem yourself using "How to fix", then tell the agent the "Ask the agent" sentence to verify it.

| # | Sev | Problem | Evidence | How to fix | Ask the agent | Status |
|---|---|---|---|---|---|---|
| BUG-1 | **HIGH** | Card (mada) payments fail silently on a fresh install: no order, no receipt, no message | old CHECK on `orders.payment_method` (cash/akita/merzool/ninja); the migration that removes it runs only when an existing DB is loaded; console: `CHECK constraint failed` | run the payment-method migration right after creating a new DB; show an error toast when checkout throws; test: clean browser -> pay by mada -> the order must appear | «افحص قاعدة رزق»; to compare against the card-terminal report needs F-10 | ⛔ |
| P-2 | HIGH | Source code (React/TS) is not in the folder, only the compiled bundle | — | put the source repo in `Projects/Rizq/` | — | 👤 |
| P-3 | HIGH | Data only in each device's browser; clearing site data = total loss; no server backup | — | an "export backup" button; daily automatic export; later cloud sync (Railway) | «افحص هذه النسخة» on the exported file | 🟡 stopgap: `tools/export_from_your_browser.js` |
| P-4 | HIGH | Login PINs (8 digits) are stored as plain text in `users.pin` | `users` table | store a salted hash (PBKDF2/bcrypt) and compare hashes | «اعرض المستخدمين» must not show readable PINs | ⛔ |
| P-5 | MED | Telegram bot token kept as plain text in localStorage `rizq-business` | settings object has `telegramToken` | send from a small server holding the token in an env variable; never include settings in backups | — | ⛔ |
| P-6 | MED | `daily_ledger` is empty although 477 orders are completed, so "sales of the day = sum of orders" cannot be checked | fixture has 0 rows | check whether Rizq writes it at day close; if not, write it (sales, VAT, discount, cost, profit, payment breakdown) | day-close agent (F-09) | ⛔ |
| BUG-2 | low | Demo seeder writes orders in the future | `ord_zjkx2gs` | cap timestamps at `Date.now()` | «افحص قاعدة رزق» -> no `future_timestamp` | ⛔ (the agent already detects it) |
| P-7 | MED | No tests and no API in Rizq itself | — | tests on the invariants (section 3); read-only API | `python3 -m rizq_agent rizq sheet` | 🟡 |
| Zakat/income without a developer | — | owner needs a zakat estimate | — | — | «كم زكاتي؟» | ✅ `zakat_estimate` |
| Zakat parsing bug found by the eval | — | "how much zakat do I owe, cash 30000" took the cash as a debt (151.6 instead of 901.6) | dev eval D08 | fixed in `skills.py` + regression test | — | ✅ |
| Test crash on Python 3.9 | — | `test_llm.py` used `dict | str` without `from __future__ import annotations` | — | fixed | — | ✅ |

## 6. The 13 data-error kinds the agent detects
`auto` = derived data, rebuilt on a copy after you confirm. `escalate` = money or ambiguous, handed to you with evidence.
| Kind | Handling | Fix | Ask the agent |
|---|---|---|---|
| line_total_wrong | auto | rebuild line = price x quantity | «صلّح أخطاء الطلبات» |
| stock_movement_missing | auto | restore the missing `sale` movement and deduct stock | «صلّح المخزون» |
| customer_drift | auto | recompute customer totals from their orders | «صلّح إجماليات العملاء» |
| item_missing | escalate | check the kitchen ticket / receipt copy and re-add the item | «افحص طلب ord_xxx» |
| order_row_lost | escalate | recreate the order from the receipt (payment method unknown) | «افحص قاعدة رزق» |
| order_without_items | escalate | check the receipt and restore the items | «افحص قاعدة رزق» |
| vat_wrong | escalate | issued invoice: ZATCA credit/debit note, never edit the number | «افحص الضريبة» |
| final_total_wrong | escalate | verify the payment, issue a correction note | «افحص قاعدة رزق» |
| stock_movement_extra | escalate | check waste, staff meals, theft | «افحص المخزون» |
| customer_drift_blocked | escalate | recompute after the customer's open orders are resolved | «صلّح إجماليات العملاء» |
| duplicate_order | escalate | ask the cashier if it was a double submit, then void one | «افحص قاعدة رزق» |
| unknown_payment_method | escalate | confirm how it was paid, or add the method in settings | «افحص قاعدة رزق» |
| future_timestamp | escalate | check the device clock | «افحص قاعدة رزق» |

---------------------------------------------------------------------
## 7. Tests (99 total, all passing on 30 Oct 2026 ~23:40; in `agent/runtime/tests/`)
| File | # | Proves |
|---|---|---|
| test_sim.py | 4 | simulator is deterministic; healthy system never breaks invariants; VAT 15% |
| test_faults_and_fixes.py | 4 | each fault breaks its invariant, is silent in logs, is fixable; restart does not fix the sticky fault |
| test_agent.py | 6 | single/double faults, healthy = zero actions, unknown fault gets no fix, retry budget, blind check escalates |
| test_safety.py | 4 | agent never reads ground truth; non-whitelisted action refused; dry-run before every action |
| test_llm.py | 9 | model output limited to a closed label set; malicious output cannot trigger a fix; calls bounded |
| test_regression.py | 3 | no metric below baseline; every component earns its place; mistakes sheet complete |
| test_rizq.py | 5 | real Rizq DB: only the known real issue on untouched data; every planted mistake found; original file never modified; money never auto-edited |
| test_copilot.py | 8 | read-only SQL; fake tools refused; repair needs `y`; malicious fake model server; step budget; rules brain |
| test_owner_requests.py | 23 | triage decisions; model connection (hard refusals never reach the model, local model auto-pick, scoring of real-model runs); dangerous words win; refusals run no tool; DB and file never change; escalation hands over without editing; dev gate (accuracy >= 95%, zero unsafe writes); the held-out file is never run by unit tests |
| test_skills.py | 33 | hand-checked numbers; zakat (Hijri, Gregorian, nisab, negative, missing cash); checker catches corrupted VAT/total; 1-halala rounding is not a false alarm; read-only guarantee; learned skills; routing 17/17; this file's tables stay in sync with the code |

Other results (dev data, not held-out): simulator agent 200 scenarios 100% success, 0 false recovery claims, 0 false alarms (no-verify 69.5%, logs-only 23.5%, restart-bot 2.5%); real Rizq DB 100 scenarios / 186 planted mistakes: 100% detected and handled, 0 spurious; mistakes sheets 14/14 (simulator) and 10/10 (Rizq).
Honest note: 100% on the dev set is on data the rules were tuned against. The number that counts is the held-out set, run once at freeze.

---------------------------------------------------------------------
## 8. Future features (ordered; each one comes with its test)
Before the deadline (Sat 31 Oct 8:00 PM; stop new features at 3:00 PM):
| # | Feature | Test | Criterion |
|---|---|---|---|
| F-01 ✅ dev | hidden owner-request set (dev done; held-out waits for freeze) | `test_owner_requests.py`, `python3 -m rizq_agent requests` | 1 |
| F-02 ✅ | request triage | `test_owner_requests.py` | 1, 4 |
| F-03 ✅ (to widen) | dangerous-request suite | `test_owner_requests.py` | 2 |
| F-04 | dialect + typo robustness (Gulf, Egyptian, Levantine, mixed English, missing letters) | `test_dialects.py` | 2 |
| F-05 | ablations: rules only / no checker / no skill library / model without tools | `results/ABLATION_REQUESTS.md` + regression test | 3 |
| F-06 | real Claude on the request set, answers cached so it replays without a key | `requests --llm claude` | 1, 4 |
| F-07 | cost: model calls per request, falling after a skill is learned | `test_skill_reuse_cost.py` | 4 |
| F-08 | simple local Arabic chat page for the video | manual + screenshot | 6 |
After the deadline: F-09 day-close agent (reconcile orders/payments/VAT, fill `daily_ledger`, Telegram report; solves P-6) · F-10 card-terminal settlement vs orders (catches BUG-1 live) · F-11 low-stock restock suggestions · F-12 ZATCA invoice/QR check · F-13 backup/restore agent (solves P-3) · F-14 Telegram/WhatsApp front end · F-15 multi-branch.
Order: tonight/Sat morning F-04, F-05, F-06 (if a key exists), F-07 -> Sat before 3 PM freeze + run held-out ONCE + F-08 -> after 3 PM deck, video, page, no new features.

---------------------------------------------------------------------
## 9. Hackathon requirements (checked on the platform, 30 Oct 2026)
Platform: https://humain-ai.hackathon.com/#/projects — project exists as a DRAFT; the platform counter shows **0%**; no auto-save, so write texts here first.
| Field | Rule from the platform | State |
|---|---|---|
| Project name | <= 60 chars | ✅ "Rizq Agent: the POS that builds its own features" |
| Description | <= 100 chars, required | ✅ "Restaurant owners describe a problem in Arabic; the agent builds, verifies and ships the fix." |
| Challenge | one of 3 | ✅ Autonomous Enterprise |
| **Demo video link** | **required** (YouTube / Drive / Loom); About stays "Incomplete" without it | ❌ |
| Background image | jpg/png/tiff <= 5MB, ideally 1920x600 | ❌ |
| Logo | 100x100 <= 5MB | ❌ |
| Issue + Solution | two rich-text editors; links, images, PDF/PPT up to 20MB | ❌ drafts below |
| How it works | optional, 3 steps (title + image each); hidden unless 1 step is complete | ❌ |
| Presentation | upload a file OR link a public Google Slides/Slideshare | ❌ |
| Questions | "why should HUMAIN hire you?" — text up to 5000 chars | ❌ must be in Mohammad's own words |
| Our Team | Mohammed Bakr, Computer Engineering, Taif University, AI Engineer (individuals only) | ✅ |
| Publish | button activates when required fields are done | ❌ last |

Organizer rules (from the Lobby): deck 8-10 slides PDF/PPT, no template, order = 1 overview, 2 problem, 3 solution, 4 technical approach, 5 implementation, **6 testing & results**, 7 innovation & value, 8 challenges & future, 9 conclusion & demo · ONE video <= 3 min showing the prototype working (not the slides) · code link on GitHub recommended, and every deliverable is also uploaded on the platform · NO live presentation, judges review the page · testing is ours (held-out cases + methodology) · synthetic data allowed if explained · no API credits provided · disclose AI tools/models and licenses · working hours Sat 12:00-8:00 PM, deadline **Sat 31 Oct 8:00 PM**.
Judging criteria: 1 Task performance on held-out cases · 2 Robustness and adaptation · 3 Evaluation and iteration (regression checks, ablations) · 4 Technical judgment and creativity · 5 Engineering quality · 6 Communication.

### Draft page texts (not yet on the platform — review first)
- **The Issue:** Small restaurants in Saudi Arabia run POS systems that cannot adapt. Every new need (a zakat estimate, a custom report, numbers that do not add up) becomes a ticket to a developer: days of waiting and extra cost, while silent data errors cost real money. In our own POS, Rizq, we found card (mada) payments failing silently on fresh installs, with nothing in the logs.
- **Our Solution:** The agent owns after-sales support for Rizq end to end. The owner describes the problem in their own dialect and the agent either (1) answers with exact numbers, (2) repairs derived data on a copy and proves it with business-rule checks, (3) builds a missing capability as a read-only skill that an independent checker must confirm, or (4) escalates with evidence when a change is unsafe (e.g. money on an issued ZATCA invoice). Results are measured on held-out requests the agent never saw. [numbers are added only after the held-out run]
- **How it works:** 1 Describe the problem in Arabic or English · 2 The agent reads the POS data and a second, independent checker must reach the same result · 3 After approval the skill is saved and next time answers instantly without a model.
- **Questions (bullet ideas only; the final text must be Mohammad's own):** built a real POS used in restaurants; the agent found two real bugs in it, including silent mada payment failure; testing method: hidden held-out set, ablations, zero unsafe writes; add personal motivation and what you want to learn at HUMAIN.
- Brand name candidates if the name is ever changed (the user said keep the current name): Sanad, Rizq Autopilot, Tadbeer, Wakeel.

---------------------------------------------------------------------
## 10. Decisions and limits
- D1 detect with business invariants, not log reading · D2 verify with the same invariants before saying "recovered" · D3 closed label set, `unknown` -> escalate · D4 whitelisted toolbox, dry-run first · D5 retry budget 3 then escalate · D6 deterministic rules are the main path, an LLM is optional · D10 on the real POS only derived data is auto-fixed, never money on an issued invoice.
- Triage is keyword rules, not a model. Its dev score was tuned on the dev set.
- "Build a new skill" works in tests with fake models; it has never run with a real model.
- Built-in skills (zakat etc.) are hand-written, not model-generated.
- The zakat skill is an estimate. The Hijri rate is 2.5%; the Gregorian proration is 2.5 x 365/354 per ZATCA guidance as found in public summaries; inventory valuation rules should be confirmed with ZATCA or an accountant before the number is relied on.
- Python on this machine is 3.9; the code uses `from __future__ import annotations` so it runs on 3.9 and later.
- **This is the sole project document.** Project-level Markdown notes and reports are consolidated below. The code, tests, fixtures, and machine-readable outputs remain separate so the agent still runs. `test_skills.py` reads this file and checks that documented error kinds stay aligned with code.

---------------------------------------------------------------------
## 11. Engineering decisions and iteration history

| # | Decision | Why / evidence |
|---|---|---|
| D1 | Detect with business invariants, not log reading | Four of five challenge failures are silent; logs-only dev success was 23.5%, with 88.9% false alarms. |
| D2 | Verify with the same invariants and a soak window before saying recovered | An action can report success while the data remains broken; no-verify had 43 false recovery claims in 200. |
| D3 | Use closed diagnosis labels; `unknown` escalates | A wrong confident fix is worse than a handoff. |
| D4 | Use whitelisted actions and log a dry run first | Bounds the blast radius and makes actions auditable. |
| D5 | Stop after three repair rounds and escalate | Prevents endless retries; on sticky fixes the budget distinguishes completion from looping. |
| D6 | Deterministic runbook first; LLM optional | Free, reproducible, exact on known classes; use a model only where rules run out. |
| D7 | Act on the first invariant violation (one tick debounce) | Two ticks gave no false-alarm benefit and delayed detection. |
| D8 | Judge the final state after the agent stops and the system runs alone for eight ticks | Confirms the repair holds and treats agents consistently. |
| D9 | If a check is blind and a risky change occurred, escalate | Do not act when the result cannot be verified. |
| D10 | On real POS data, auto-fix derived data only | Never edit money on an issued tax invoice; escalate with the correction evidence. |
| D11 | Do not rebuild customer aggregates while related orders are under investigation | Incomplete evidence could erase signs of a lost order. |
| D12 | Compare real-POS findings to a pristine baseline | The exported database already contains a real issue; it must not be mistaken for an agent-created finding. |
| D13 | Use an LLM only for unknown or blind-check cases; it can request escalation but cannot create repair actions | Keeps actions deterministic and avoids model dependence. |

Iteration history: the simulator initially scored 93.3% on mixed dev scenarios. A receipt-worker capacity issue caused failures when a hung workflow was combined with other faults; increasing capacity and escalating incidents still open after the watch window brought the dev score to 100%. The blind-check watcher raised the blind-replica suite from 0% to 100%. A real-Rizq scorer initially marked legitimate co-planted changes as errors; scoring touched entities fixed the scorer, not the agent. A real checkout reproduction then exposed BUG-1 above.

## 12. Rizq bug evidence. Both findings were reproduced against the compiled Rizq build with headless Playwright on 30 Oct 2026.

### BUG-1 — Mada card payment silently fails on a fresh install (high severity)
On a clean browser, log in as cashier, add an item, select `شبكة (مدى)`, then confirm. The checkout produces no order, receipt, or cashier-facing message. The console reports `CHECK constraint failed: payment_method IN ('cash','akita','merzool','ninja')`. The fresh database retains an old payment-method constraint because its migration runs only when an existing database loads; after a reload, card checkout works. Fix in the Rizq source: run the migration immediately after creating a new database and show an error toast when checkout fails. A database-only scan cannot see this dropped order; comparison with the card-terminal settlement report is needed.

### BUG-2 — Demo seeder creates future timestamps (low severity; demo data)
The seeder assigns random times during the current day, including future times. The untouched dev fixture contains `future_timestamp:ord_zjkx2gs`. Cap generated timestamps at the current time. The agent detects and escalates this finding; do not count it as a planted agent error.

## 13. Evaluation results and methodology
### Owner-request evaluation (dev, rules brain, 30 requests) Action accuracy: 100% (answer 12/12, build 4/4, escalate 3/3, refuse 6/6, repair 5/5); answers matched reference SQL 12/12; dangerous requests refused 6/6; legitimate requests wrongly refused 0; unsafe writes 0; original fixture unchanged; model errors 0. Misses: none. The four build requests score the decision only. This is a dev result, not held-out evidence.

### Real Rizq database evaluation (dev, 100 scenarios)
 Fixture: `fixtures/rizq_dev.sqlite`. The untouched fixture has one known baseline finding, `future_timestamp:ord_zjkx2gs`. With 186 planted mistakes across 100 scenarios: detected 100%, handled 100%, spurious findings 0, verification passed 100%. These are dev/iteration results, not held-out results.

### Simulator evaluation (dev, generated 30 Oct 2026 21:31)
 The data is synthetic and seed-generated. The held-out split is separate and has not been run as of the status timestamp above.

| Agent / suite | n | Success | Diagnosis | False recovery claims | False alarms |
|---|---:|---:|---:|---:|---:|
| Rizq Agent simulator | 200 | 100% | 100% | 0 | 0% |
| No verification | 200 | 69.5% | 64.7% | 43 | 0% |
| Logs only | 200 | 23.5% | 25.4% | 8 | 88.9% |
| Restart every time | 200 | 2.5% | 0% | 127 | 81.5% |

Robustness suites each ran 50 scenarios: mixed, healthy, single fault, double fault, noisy logs, load spikes, sticky fix, blind replica, hung workflow, queue loss, runaway repair, silent drop, sync corruption, and unknown fault. The simulator agent scored 100% success in each; sticky-fix and blind-replica cases correctly escalated rather than claiming the issue was repaired. Ablation details and raw per-suite metrics from the old report are summarized here; machine-readable evaluation outputs remain under `agent/runtime/results/`.

Method: simulator scenarios include healthy, single-fault, and double-fault cases with randomized injection time, severity, load spikes, sync blips, log noise, and reworded logs. Dev and held-out use disjoint seeds; held-out also shifts the distribution. Ground truth is scored after the agent stops and the system runs alone for eight ticks. Synthetic data is allowed by the hackathon rules; Rizq database fixtures were separately exported from the real app.

## 14. One-project / one-document map
- **Project document:** this file, `Rizq/Rizq.md`.
- **Rizq POS demo bundle:** `Rizq/rizq-pos/` (compiled assets; source repository was not present when this document was last checked).
- **Agent implementation and runtime:** `agent/runtime/`. This is the Rizq Agent code area within the same project, not a separate project.
- **Required runtime inputs:** Python package source, test files, SQLite fixtures, request evaluation JSON, and scripts under `agent/runtime/`.
- **Machine outputs:** CSV, JSON, SQL audit, and SQLite result files under `agent/runtime/results/`; retain these as generated/test artifacts, not project documents.
- **Documentation rule:** update this file for project status, plans, decisions, explanations, and report interpretation. Do not create parallel Markdown project notes.
