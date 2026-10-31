"""The owner wants only the answer in the chat: no emoji prefix on confirmed actions and no
"welcome back" line above restored history."""
from pathlib import Path
import datetime
import shutil

bundle = Path(__file__).resolve().parents[4] / "rizq-pos/assets/index-c3uFjRpt.js"
source = bundle.read_text(encoding="utf-8")
PAIRS = [
    ("{id:OB++,role:`bot`,text:`✅ `+a}", "{id:OB++,role:`bot`,text:a}"),
    ("r([ {id:0,role:`bot`,text:`أهلًا بعودتك 👋 هذه محادثاتك المحفوظة محليًا.`},...history.map(", "r([...history.map("),
]
if all(new in source for _, new in PAIRS):
    print("Plain messages are already in place.")
    raise SystemExit(0)
updated = source
for old, new in PAIRS:
    if updated.count(old) != 1:
        raise SystemExit(f"Expected one target, found {updated.count(old)}: {old[:60]}")
    updated = updated.replace(old, new, 1)
backup = bundle.with_name(bundle.name + ".before-plain-messages-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + ".bak")
shutil.copy2(bundle, backup)
bundle.write_text(updated, encoding="utf-8")
print(f"Plain messages applied; backup: {backup}")
