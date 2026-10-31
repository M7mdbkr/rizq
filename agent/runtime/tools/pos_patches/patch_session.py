"""Re-runnable surgical patch for the compiled Rizq POS bundle: sign-in that survives a reload.

Companion to patch_categories.py. Same contract: exact-string replacement of unique literals
in `rizq-pos/assets/index-*.js`, idempotent, `--check` dry-runs, timestamped `.bak` on first write.

PROBLEMS FIXED
  P5 The cashier session was never restored. `useState(null)` meant every page reload threw the
     user back to the PIN keypad, and the assistant lost its conversation with it. The user id was
     already being written to localStorage on login; nothing ever read it back.
  P6 The agent had no way to learn the PIN, so it had to prompt for one separately -- a second
     password box for a user who had just signed in. Now a successful POS sign-in silently opens
     the agent's server-side session with the same PIN, and signing out closes it.

WHY THE AGENT STILL NEEDS ITS OWN SESSION
  The cashier's sign-in is client-side only: the browser deciding "I am the owner" proves nothing
  to the server. The PIN is therefore handed once to /api/auth/login, which verifies it against the
  cashier's own users table and returns an opaque token. The browser never claims a role, and the
  PIN is never stored -- only the token is, so a reload restores the session without re-asking.

Proper fix when the React source is used instead (RIZQ-SESSION-1): restore the session in the auth
provider itself and have the provider own the agent token.
"""
from __future__ import annotations

import datetime
import glob
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
POS = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", "rizq-pos"))

# P5a: a successful sign-in also opens the agent session, silently.
P5A_OLD = "let l=(0,v.useCallback)(e=>{let t=Je(e);return t&&(i(t),localStorage.setItem(Rt,t.id)),t},[])"
P5A_NEW = ("let l=(0,v.useCallback)(e=>{let t=Je(e);return t&&(i(t),localStorage.setItem(Rt,t.id),"
           "window.rizqAgentLogin&&window.rizqAgentLogin(e).catch(()=>{})),t},[])")

# P5b: signing out of the cashier also closes the agent session and clears its token.
P5B_OLD = "u=(0,v.useCallback)(()=>{i(null),localStorage.removeItem(Rt)},[])"
P5B_NEW = ("u=(0,v.useCallback)(()=>{i(null),localStorage.removeItem(Rt),"
           "window.rizqAgentLogout&&window.rizqAgentLogout()},[])")

# P6: once the database is ready, restore the signed-in user from localStorage.
P6_OLD = "(0,v.useEffect)(()=>{fe().then(()=>n(!0))},[])"
P6_NEW = ("(0,v.useEffect)(()=>{fe().then(()=>{n(!0);try{let rizqId=localStorage.getItem(Rt);"
          "if(rizqId){let rizqU=qe().find(x=>x.id===rizqId);rizqU&&i(rizqU)}}catch(rizqE){}})},[])")

PATCHES = [
    ("P5a cashier sign-in opens the agent session", P5A_OLD, P5A_NEW),
    ("P5b cashier sign-out closes the agent session", P5B_OLD, P5B_NEW),
    ("P6  restore the signed-in user after a reload", P6_OLD, P6_NEW),
]


def bundle_path() -> str:
    hits = glob.glob(os.path.join(POS, "assets", "index-*.js"))
    hits = [h for h in hits if not h.endswith(".bak")]
    if len(hits) != 1:
        raise SystemExit(f"expected exactly one assets/index-*.js in {POS}, found {len(hits)}")
    return hits[0]


def main(check_only: bool = False) -> int:
    path = bundle_path()
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    original = text
    report, applied, already, missing = [], 0, 0, 0

    for label, old, new in PATCHES:
        if new in text:
            report.append(f"  = {label}: already patched")
            already += 1
            continue
        n = text.count(old)
        if n != 1:
            report.append(f"  ! {label}: target found {n} times (expected 1) - NOT applied")
            missing += 1
            continue
        text = text.replace(old, new, 1)
        report.append(f"  + {label}: applied")
        applied += 1

    print(f"bundle: {path}")
    print("\n".join(report))

    if missing:
        print(f"\n{missing} patch target(s) not found as a unique string. Nothing written.")
        return 2
    if check_only:
        print("\ncheck-only: no file written.")
        return 0 if not applied else 1
    if text == original:
        print("\nno change needed.")
        return 0

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = f"{path}.before-session-patch-{stamp}.bak"
    shutil.copy2(path, backup)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f"\nbackup: {backup}")
    print(f"written: {path}  ({applied} applied, {already} already in place)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(check_only="--check" in sys.argv))
