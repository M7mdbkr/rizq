"""Let pos-agent.js drive the cashier's assistant: send a message, propose an owner-confirmed
action, load a saved conversation; and give it safe helpers for staff, stock and products.

Actions still go through the assistant's own confirm button (owner only), so nothing changes
data without the owner pressing it. Re-runnable; `--check` reports without writing.
"""
from pathlib import Path
import datetime
import shutil
import sys

MARK = "rizqAssistantPropose"
bundle = Path(__file__).resolve().parents[4] / "rizq-pos/assets/index-c3uFjRpt.js"
source = bundle.read_text(encoding="utf-8")
if MARK in source:
    print("Assistant hooks are already present.")
    raise SystemExit(0)

HOOKS = ("window.rizqAssistantSend=s;"
         "window.rizqAssistantPropose=(text,rows,action)=>r(x=>[...x,{id:OB++,role:`bot`,text,rows,action}]);"
         "window.rizqAssistantLoad=list=>r(list.length?list.map(m=>({id:OB++,role:m.role===`user`?`user`:`bot`,text:m.text})):[{id:OB++,role:`bot`,text:`محادثة جديدة. كيف أقدر أساعدك؟`}]);")
HELPERS = ("window.rizqPos={"
           "products:()=>nt().map(p=>({id:p.id,name:p.name_ar,stock:p.stock,price:p.price})),"
           "restock:(id,qty)=>{qty=Number(qty);if(!(qty>0&&qty<=10000))throw Error(`الكمية لازم تكون بين 1 و10000`);"
           "let p=nt().find(x=>x.id===id);if(!p)throw Error(`الصنف غير موجود`);ct(id,qty,`جرد من المساعد`);"
           "return`تمت إضافة ${qty} إلى «${p.name_ar}». المخزون الآن ${p.stock+qty}.`},"
           "addStaff:(name,pin)=>{name=String(name||``).trim();pin=String(pin||``);"
           "if(!name)throw Error(`اكتب اسم الموظف`);if(!/^\\d{8}$/.test(pin))throw Error(`الرقم السري لازم 8 أرقام`);"
           "if(qe().some(u=>String(u.pin)===pin))throw Error(`هذا الرقم السري مستخدم لموظف ثاني`);"
           "Ye(name,`staff`,pin);return`تمت إضافة ${name} ككاشير.`}};")

targets = [
    ("async function c(n,i){if(agentUser?.role!==`owner`)return;", HOOKS + "async function c(n,i){if(agentUser?.role!==`owner`)return;"),
    ("function Ln(e){let t=jn(e);", HELPERS + "function Ln(e){let t=jn(e);"),
]
updated = source
for old, new in targets:
    if updated.count(old) != 1:
        raise SystemExit(f"Expected one patch target, found {updated.count(old)}: {old[:80]}")
    updated = updated.replace(old, new, 1)
if "--check" in sys.argv:
    print("All targets found; nothing written.")
    raise SystemExit(0)
backup = bundle.with_name(bundle.name + ".before-assistant-hooks-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + ".bak")
shutil.copy2(bundle, backup)
bundle.write_text(updated, encoding="utf-8")
print(f"Assistant hooks added; backup: {backup}")
