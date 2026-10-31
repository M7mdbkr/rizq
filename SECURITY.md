# Security audit — Rizq

Audited 31 Oct 2026 against the full checklist. Every ✅ below was **verified by running
it**, not by reading the code. 205 automated tests cover these behaviours.

| # | Item | Status |
|---|---|---|
| 1 | Hide API keys | ✅ Done |
| 2 | Check env variables | ✅ Done |
| 3 | Check git for leaked secrets | ✅ Done — one real issue found and fixed |
| 4 | Proper authentication | ✅ Done |
| 5 | 2FA for admins | ✅ Done (TOTP) |
| 6 | Check user access | ✅ Done |
| 7 | Protect admin routes | ✅ Done |
| 8 | Secure API endpoints | ✅ Done |
| 9 | Secure the database | ⚠️ Partial — not encrypted at rest |
| 10 | RLS on every table | ⚠️ Not applicable as written — equivalent in place |
| 11 | Hash passwords properly | ❌ **Open — the main remaining gap** |
| 12 | Encrypt sensitive data | ⚠️ Partial |
| 13 | Don't send extra data | ✅ Done |
| 14 | Sanitize forms | ⚠️ Partial |
| 15 | Rate limiting | ✅ Done |
| 16 | Spending cap | ✅ Done |
| 17 | Update dependencies | ✅ Nothing to update |
| 18 | Remove unused packages | ✅ Nothing to remove |
| 19 | Logs and alerts | ✅ Done |
| 20 | Full security audit | ✅ This document |

---

## What was found and fixed

**A real PIN and five customer names were committed to a public repository.** The repo was
public, contained the fixtures, and one held-out fixture held a PIN that was not a demo
value. The repo was made private, the PIN was reset to the published demo value, the five
names were replaced, and the evaluations were re-run to prove the numbers were unchanged.
The remaining PINs are the two the login screen already displays on-screen as hints.

**Four endpoints ignored the `Authorization` header**, accepting a token only in the JSON
body. They were protected, but inconsistently; all now use one resolver (header → body →
query).

**Code endpoints checked same-origin before authentication**, so an unauthenticated caller
got a `400` that made a protected route look merely malformed. Authorisation now runs first
and returns `403`.

**`/api/health` described the setup to anyone** — database filename and which model
providers were configured. Anonymous callers now get liveness only.

**Two employees could share a PIN.** Sign-in resolves a user with
`SELECT * FROM users WHERE pin = ?`, which returns whichever row comes first, so a shift, a
repair and every audit entry would be attributed to the wrong person. The server now refuses
to resolve an ambiguous PIN, and the cashier refuses to create one.

---

## Item by item

### 1–3 · Secrets, environment, git history
No key material exists in the working tree or in any commit. All history was scanned for
OpenAI/Anthropic/AWS/GitHub/Telegram key shapes and private-key headers: clean. The only
`ANTHROPIC_API_KEY` matches are the *variable name* in code and docs.

Keys are read from the process environment or `agent/runtime/.env`, which is gitignored.
No key is ever logged, returned by an endpoint, or written to the audit log.

### 4 · Authentication
A PIN is checked **on the server** against the cashier's own `users` table, in constant time
(`hmac.compare_digest`), compared against every row so timing does not reveal which user
matched. Success issues a 32-byte opaque token. Sessions persist to disk with **only a
SHA-256 of the token stored** — the raw token never touches disk. Absolute lifetime 12 h,
idle timeout 1 h. The browser never sends a role or a user id; the server resolves both.

### 5 · Two-factor for admins
RFC 6238 TOTP, opt-in per owner, verified server-side. Matches the RFC's published test
vector, so any standard authenticator app works. One step of clock drift tolerated; a used
code cannot be replayed; the secret file is `0600`. Checked **before** a session is created.
Disabling it requires a current code.

### 6–7 · User access and admin routes
Deny-by-default: an unknown tool requires `owner`. 16 tools; a cashier may reach 5 (look up
an order, escalate, list capabilities). Everything that discloses money across orders or
changes/exports anything is owner-only. Enforced in `PosTools.call` — the agent's hands —
so no prompt can talk past it, **and** independently at the HTTP layer.

Verified live: every sensitive route returns `403` for both an anonymous caller and a signed-in
worker, and `200` for the owner.

### 8 · API endpoints
Every route that reads or changes business data requires a session. Bodies are capped at
30 MB, chat messages at 5 000 characters, documents at 500 KB. Errors map to `403` (denied),
`429` (rate limited), `402` (spend cap) rather than leaking detail. Code endpoints additionally
require same-origin.

### 9 · Database
The source database is opened **read-only** and copied; the original is never modified, which
is asserted by hash in the tests. The agent's SQL tool is SELECT-only, enforced with
`PRAGMA query_only`, and credential columns are unreadable to it. Repairs touch derived data
only, never money on an issued invoice, and never delete.

**Not done:** the cashier's SQLite is not encrypted at rest. Anyone with the device and the
browser profile can read it. Full-device encryption is the practical mitigation today.

### 10 · Row-level security
RLS is a Postgres/Supabase feature. Rizq's database is SQLite inside the browser, so there is
no server to enforce row policies and the term does not map. The equivalent control is in
place: **no client ever queries the database directly over the network.** Every read goes
through a named, role-gated tool, and free-form SQL is owner-only, SELECT-only and blocked
from credential columns. If Rizq moves to a shared Postgres backend, real RLS should be
enabled per table at that point.

### 11 · Password hashing — OPEN
The cashier stores employee PINs in **clear text**, and sign-in matches on the raw value. The
role layer compares them in constant time and never logs or returns one, and they are redacted
from every export — but it cannot fix the storage, because hashing requires changing the
cashier's login query and user management.

The React source exists in a separate repository, so this **is** fixable properly: add a
`pin_hash` column, hash with PBKDF2 via Web Crypto, migrate existing rows, and match on the
hash. Note that an 8-digit PIN has only 10⁸ possibilities, so hashing must be paired with the
rate limiting already in place.

Until then: the code-admin password *is* hashed correctly (PBKDF2-HMAC-SHA256, 400 000
iterations, per-secret salt).

### 12 · Encrypting sensitive data
Session tokens are stored only as SHA-256. The 2FA secrets, spend counter, audit log and alert
log are written `0600` in a `0700` directory. The cashier database itself is not encrypted —
see item 9.

### 13 · Not sending extra data
PINs are never returned by any endpoint — asserted by a test. `/api/health` no longer describes
the database or providers to anonymous callers. Exports redact credential columns in both the
CSV and the SQLite copy. Chat history is scoped to the requesting user; naming someone else's
id returns nothing.

### 14 · Form and input sanitisation
Every SQL statement is parameterised — there is no string-built SQL anywhere in the agent.
Types, lengths and ranges are validated server-side, and base64 payloads are decoded with
`validate=True` and checked to be a real SQLite file before use. File paths for code proposals
reject absolute paths, `..`, hidden directories, symlinks and anything outside the project.

**Partial:** the cashier's own form validation is client-side, in the compiled bundle. It
constrains what a user types, but a determined local user can bypass it by writing to their
own browser database. The server-side invariants (the scan) are what actually catch bad data.

### 15 · Rate limiting
One sliding-window limiter across all routes, keyed by client address and route class:
sign-in 10/min, chat 30/min, document filling 20/min, scans 20/min, exports 5/5 min, code
10/5 min. Verified live: 14 rapid sign-in attempts → 10 refused with `429`. Separately, five
failed PINs lock that client out for five minutes, and five failed code-admin passwords do the
same.

### 16 · Spending cap
Local models are free and uncounted. Calls to a **paid** provider are counted per day and
refused past a cap (default 200, `RIZQ_MAX_PAID_CALLS_PER_DAY`), returning `402` and raising an
alert, so a retry loop against a metered API cannot run up a bill unattended.

### 17–18 · Dependencies
The agent imports **nothing outside the Python standard library** — verified by walking every
import in every module. There is no `requirements.txt`, `package.json` or lockfile to audit,
no transitive supply chain, and no unused package to remove. Third-party code redistributed in
the compiled cashier bundle is listed in `THIRD-PARTY-NOTICES.md`.

### 19 · Logs and alerts
Every tool call, allowed or denied, is appended to `data/audit.jsonl` with who, what and
whether it was permitted. High-signal events — repeated auth failures, denied privileged
access, code applied, data exported, 2FA disabled, spend cap reached — also go to
`data/alerts.jsonl`, readable by the owner at `/api/alerts`, so trouble is visible without
reading thousands of ordinary lines. Both files are `0600`. Neither ever records a PIN or a
raw token.

---

## Ranked remaining work

1. **Hash the employee PINs** (item 11) — needs the cashier source; the clear remaining gap.
2. **Encrypt the cashier database at rest** (items 9, 12), or move to a server-side database
   with real RLS (item 10).
3. **Server-side form validation** for cashier writes (item 14), which arrives naturally with
   a server-side database.

## Reporting a vulnerability

Open a private security advisory on the repository rather than a public issue.
