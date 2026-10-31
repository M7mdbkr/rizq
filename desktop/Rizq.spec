# Build on the target operating system with PyInstaller.
from pathlib import Path
import sqlite3
import sys
import tempfile

root = Path(SPECPATH).parent
runtime = root / "agent" / "runtime"
datas = [(str(root / "rizq-pos" / "assets" / "fonts"), "rizq-pos/assets/fonts")]
for filename in ("index-c3uFjRpt.js", "index-DRwVUeB_.css", "rizq-local-style.css"):
    datas.append((str(root / "rizq-pos" / "assets" / filename), "rizq-pos/assets"))
for filename in ("index.html", "sql-wasm.wasm", "favicon.svg", "icons.svg"):
    datas.append((str(root / "rizq-pos" / filename), "rizq-pos"))
for directory in ("web", "features", "problems"):
    datas.append((str(runtime / directory), "agent/runtime/" + directory))

# The source fixture was derived from a real export. Ship the schema only,
# never customer, order, or employee data from the developer's fixture.
empty_db = Path(tempfile.mkdtemp(prefix="rizq-fixture-")) / "rizq_dev.sqlite"
with sqlite3.connect((runtime / "fixtures" / "rizq_dev.sqlite").as_uri() + "?mode=ro", uri=True) as source:
    schema = [row[0] for row in source.execute(
        "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' "
        "ORDER BY CASE type WHEN 'table' THEN 0 WHEN 'index' THEN 1 ELSE 2 END")]
with sqlite3.connect(str(empty_db)) as target:
    for statement in schema:
        target.execute(statement)
    target.commit()
datas.append((str(empty_db), "agent/runtime/fixtures"))

a = Analysis([str(root / "desktop" / "main.py")],
             pathex=[str(runtime)], datas=datas,
             hiddenimports=["rizq_agent.web"],
             excludes=[], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Rizq",
          debug=False, bootloader_ignore_signals=False, strip=False,
          upx=False, console=False)
app = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Rizq")
if sys.platform == "darwin":
    app = BUNDLE(app, name="Rizq.app", bundle_identifier="com.rizq.pos.desktop")
