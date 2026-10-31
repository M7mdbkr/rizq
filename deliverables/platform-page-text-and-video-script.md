# Rizq Agent: platform page, complete draft (for Mohammad to approve; nothing published)

## Project name
Rizq Agent: The POS That Builds Its Own Features

## Description (<= 100 chars)
Restaurant owners describe a problem in Arabic; the agent builds, verifies and ships the fix.

## Challenge
Challenge 3: Autonomous Enterprise

## Links (fill before upload)
- Code: https://github.com/M7mdbkr/rizq
- Demo video (max 3:00): [YOUR VIDEO LINK]
- Run it: `python3 -m rizq_agent chat fixtures/rizq_dev.sqlite`

## Why we built it (the short story)
Small Saudi restaurants run a POS that cannot change. Every new need waits days for a developer. We built Rizq so the owner, in Arabic, can ask for what is missing and get it safely, without a developer and without a cloud bill.

## What makes it different (rare, and true today)
1. **Works offline.** The POS runs SQLite inside the browser: no server, no internet needed for sales. An iPad on the same Wi-Fi can open it.
2. **Local AI agent, free.** The agent runs on a local model (Qwen3 via Ollama): no API key, no cloud cost, data stays in the restaurant. A rules-only mode works with no model at all.
3. **Plug in any external LLM.** Add a Claude key and the same agent uses it instead; switching is configuration, not a rewrite.
4. **It builds its own features.** A missing capability becomes a new read-only skill: two different SELECT queries must return the same rows, and it installs only after the owner types yes. From then on it answers with no model.
5. **Two checks before any number.** An answer is given only when two independent computations agree; otherwise it escalates instead of guessing.
6. **Safety lives in the tools, not the prompt.** Server-side PIN login, deny-by-default roles (owner 13 tools, staff 5), read-only SQL, repair only on a copy, append-only audit log. A clever prompt cannot bypass it.
7. **Arabic and Saudi first.** Arabic RTL cashier, Gulf-dialect requests, zakat estimate, 15% VAT and ZATCA-style QR receipts.
8. **Honest evaluation.** Held-out sets run once at freeze, ablations, 234 automated tests, and the misses reported openly.

## The problem (before) and the fix (after)
| Before | After |
|---|---|
| A new report or zakat estimate = a ticket to a developer, days of waiting | The owner asks in Arabic; the agent answers or builds the skill after one yes |
| Silent data errors (e.g. mada payments failing with nothing in the logs) | The agent finds broken derived data, repairs a copy after confirmation, escalates money problems |
| Cloud AI = cost, privacy risk, needs internet | Local free model, works offline, external LLM optional |
| An AI that can break things | Dangerous requests refused before any tool runs; 0 unsafe writes on held-out |

## The Product: Rizq
Rizq is a POS I built for Saudi restaurants: Arabic right-to-left cashier screen, SQLite running inside the browser (no server needed, an iPad on the same Wi-Fi can open it), 15% VAT and ZATCA-style QR receipts, orders, inventory, customers, a Telegram daily report and backups. Roadmap: Local app (built), Telegram bot, WhatsApp orders, Cloud SaaS. For this hackathon I added Rizq Agent, the after-sales support layer that lives inside the POS.

## The Issue
Small restaurants in Saudi Arabia run POS systems that cannot adapt. Every new need (a zakat estimate, a custom report, numbers that do not add up) becomes a ticket to a developer: days of waiting and extra cost, while silent data errors cost real money. In our own POS, Rizq, we found card (mada) payments failing silently on fresh installs, with nothing in the logs.

## Our Solution
The agent handles after-sales support for Rizq. The owner describes the problem in their own dialect and the agent either (1) answers with exact numbers that two independent computations agree on, (2) repairs derived data on a copy and proves it with business-rule checks, (3) builds a missing capability as a read-only skill that an independent checker must confirm and the owner approves, (4) escalates with evidence when a change is unsafe (e.g. money on an issued invoice), or (5) refuses dangerous requests before any tool runs. Safety is enforced in the tools: server-side PIN login, deny-by-default roles (owner 13 tools, staff 5), read-only SQL, audit log.
Results (held-out, run once at freeze after the 147-test suite was green and the code frozen): owner requests 90.0% right decisions (27/30), 11/11 answers match independent reference SQL, 6/6 dangerous requests refused, 0 unsafe writes, database file unchanged. On a separate held-out real-Rizq database fixture (100 scenarios, 212 planted mistakes): 99.0% success, 0 spurious findings. Recovery layer on 300 shifted simulator scenarios: 100% success, 0 false "recovered" claims; without the verification step 64% and 73 false claims; logs-only 10%; naive restart 0%. We report the 3 misses honestly: keyword matching with no Arabic normalization (hamza forms, prefixes), which is our first next step. Dev scores were 100%, so held-out is the number that counts.

## How it works (3 steps)
1. Describe the problem in Arabic or English.
2. The agent reads the POS data and a second, independent checker must reach the same result.
3. After approval the skill is saved and next time answers instantly without a model.

## Tech stack
React + TypeScript (POS, compiled bundle), SQLite in the browser, Python agent runtime (`rizq_agent`), local Qwen3 via Ollama (no API key; Claude optional), Tajawal font, 234 automated tests.

## Limitations
ZATCA not connected (zakat/VAT are estimates); held-out owner-request accuracy is 90% because triage uses keyword rules with no Arabic normalization; "build a skill" tested with fake models only; live write-back to the cashier not built; POS source unavailable and PINs stored in clear text in the POS.

## Future work
Next: Arabic text normalization (the root cause of all held-out misses), then a NEW held-out set to re-test; a dialect and typo robustness suite. Then: ZATCA FATOORA e-invoicing, a day-end reconciliation agent, low-stock forecasting, a shared server database with backups and write-back to the cashier, multi-branch offline sync, hashed PINs. Product roadmap: local app (built), Telegram bot, WhatsApp orders, cloud SaaS.

## Disclosure
Built with an AI coding assistant (Anthropic Claude). Local model: Qwen3 via Ollama (Apache 2.0). Data: real Rizq exports + hand-written requests.

## Why should HUMAIN hire you? (draft: rewrite in your own words, then approve)
I built Rizq end to end as a Computer Engineering graduate from Taif University, then added an agent that proves its own answers instead of guessing. I report what failed (3 of 30 held-out requests and one wrong refusal, root cause found) rather than hiding it, and I enforce safety in the tools, not the prompt. That is the engineer I would be inside HUMAIN: ships real products, tests honestly, and keeps Arabic users first.

## Video script (max 3:00, screen recording)
| Time | Show | Say |
|---|---|---|
| 0:00-0:20 | Rizq POS, then the chat page | "This is Rizq, my restaurant POS. Every new need used to be a ticket to a developer. Now the owner just asks." |
| 0:20-0:55 | Chat: «كم مبيعاتي آخر 7 أيام؟» then «كم زكاتي؟» | "Exact numbers, only because two independent computations agree. For zakat it asks for cash and liabilities, lists its assumptions, and says it is an estimate." |
| 0:55-1:30 | A request needing a new skill, then "yes" | "It builds a read-only skill from two different queries that must match, and installs it only after I approve. Next time no model is needed." |
| 1:30-2:00 | `python3 -m rizq_agent rizq sheet` or scan, then a repair with `y` | "On the real Rizq database it finds broken derived data, repairs a copy after my confirmation, and escalates money problems instead of touching them." |
| 2:00-2:25 | Type "delete all orders" and "set total to 0"; then log in as staff and ask for sales | "Dangerous requests are refused before any tool runs. Staff cannot see sales: permissions live in the tools, not the prompt." |
| 2:25-3:00 | `python3 -m rizq_agent requests --split dev`, then the held-out results slide | "On held-out requests it never saw: 27 of 30 right, 99% on real Rizq data, zero unsafe writes. Rizq Agent: the owner asks, the agent proves it." |
