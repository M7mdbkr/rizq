"""Re-runnable patch for the compiled Rizq POS bundle: one PIN per employee.

Companion to patch_categories.py and patch_session.py. Same contract: exact-string
replacement of unique literals, idempotent, `--check` dry-runs, timestamped `.bak`.

PROBLEM
  Adding an employee or changing a PIN validated only "8 digits". Nothing stopped two
  people being given the same PIN, and sign-in resolves a user with
  `SELECT * FROM users WHERE pin = $pin`, which returns whichever row comes first. Two
  employees sharing a PIN therefore sign in as the same person, so a shift, a repair and
  every audit entry get attributed to the wrong human.

  The server now refuses to resolve an ambiguous PIN (permissions.verify_pin), which
  closes the security hole. These patches stop the duplicate being created in the first
  place, so the owner gets a clear message instead of a login that silently stops working.
"""
from __future__ import annotations

import datetime
import glob
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
POS = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", "rizq-pos"))
TAKEN = "هذا الرمز مستخدم لموظف ثاني. اختر رمزًا مختلفًا."

ADD_OLD = ("if(s.length!==8||!/^\\d+$/.test(s))return n(`الرمز يجب أن يكون 8 أرقام`,`error`);"
           "Ye(r.trim(),a,s),")
ADD_NEW = ("if(s.length!==8||!/^\\d+$/.test(s))return n(`الرمز يجب أن يكون 8 أرقام`,`error`);"
           f"if(qe().some(rizqU=>String(rizqU.pin)===s))return n(`{TAKEN}`,`error`);"
           "Ye(r.trim(),a,s),")

EDIT_OLD = ("if(i.length!==8||!/^\\d+$/.test(i))return r(`الرمز يجب أن يكون 8 أرقام`,`error`);"
            "Xe(e.id,i),")
EDIT_NEW = ("if(i.length!==8||!/^\\d+$/.test(i))return r(`الرمز يجب أن يكون 8 أرقام`,`error`);"
            f"if(qe().some(rizqU=>String(rizqU.pin)===i&&rizqU.id!==e.id))return r(`{TAKEN}`,`error`);"
            "Xe(e.id,i),")

PATCHES = [
    ("P7a a new employee cannot reuse another's PIN", ADD_OLD, ADD_NEW),
    ("P7b changing a PIN cannot collide with another", EDIT_OLD, EDIT_NEW),
]


def bundle_path() -> str:
    hits = [h for h in glob.glob(os.path.join(POS, "assets", "index-*.js")) if not h.endswith(".bak")]
    if len(hits) != 1:
        raise SystemExit(f"expected exactly one assets/index-*.js in {POS}, found {len(hits)}")
    return hits[0]


def main(check_only: bool = False) -> int:
    path = bundle_path()
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    original, report, applied, already, missing = text, [], 0, 0, 0
    for label, old, new in PATCHES:
        if new in text:
            report.append(f"  = {label}: already patched"); already += 1; continue
        n = text.count(old)
        if n != 1:
            report.append(f"  ! {label}: target found {n} times (expected 1) - NOT applied"); missing += 1; continue
        text = text.replace(old, new, 1)
        report.append(f"  + {label}: applied"); applied += 1
    print(f"bundle: {path}")
    print("\n".join(report))
    if missing:
        print(f"\n{missing} patch target(s) not unique. Nothing written."); return 2
    if check_only:
        print("\ncheck-only: no file written."); return 0 if not applied else 1
    if text == original:
        print("\nno change needed."); return 0
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = f"{path}.before-users-patch-{stamp}.bak"
    shutil.copy2(path, backup)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f"\nbackup: {backup}")
    print(f"written: {path}  ({applied} applied, {already} already in place)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(check_only="--check" in sys.argv))
