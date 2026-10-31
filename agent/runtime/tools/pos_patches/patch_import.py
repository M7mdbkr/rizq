"""Add `rizqPos.importProducts`: adds the rows of an owner-approved sheet as new products.
Names that already exist are left alone (never overwritten). Run after patch_assistant_hooks.py."""
from pathlib import Path
import datetime
import shutil
import sys

bundle = Path(__file__).resolve().parents[4] / "rizq-pos/assets/index-c3uFjRpt.js"
source = bundle.read_text(encoding="utf-8")
if "importProducts:" in source:
    print("Import helper is already present.")
    raise SystemExit(0)
OLD = "window.rizqPos={"
NEW = ("window.rizqPos={importProducts:list=>{let have=new Set(nt().map(p=>p.name_ar)),cats=Qe(),added=0,dup=0;"
       "for(let it of list){if(have.has(it.name)){dup++;continue}let c=cats.find(x=>x.name_ar===it.category)||cats[0];"
       "at({name_ar:it.name,category_id:c?c.id:null,price:+it.price,cost:+it.cost||0,stock:+it.stock||0,min_stock:+it.min_stock||5,status:`active`});"
       "have.add(it.name);added++}return`تمت إضافة ${added} صنف${dup?`، وتجاهلت ${dup} موجود من قبل`:``}.`},")
if source.count(OLD) != 1:
    raise SystemExit(f"Expected one target, found {source.count(OLD)}")
if "--check" in sys.argv:
    print("Target found; nothing written.")
    raise SystemExit(0)
backup = bundle.with_name(bundle.name + ".before-import-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + ".bak")
shutil.copy2(bundle, backup)
bundle.write_text(source.replace(OLD, NEW, 1), encoding="utf-8")
print(f"Import helper added; backup: {backup}")
