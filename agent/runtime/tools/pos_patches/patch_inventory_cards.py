"""Add category navigation to the compiled inventory screen without changing data."""
from pathlib import Path
import datetime
import shutil

bundle = Path(__file__).resolve().parents[4] / "rizq-pos/assets/index-c3uFjRpt.js"
source = bundle.read_text(encoding="utf-8")
if "rizqSelectedCategory" in source:
    print("Inventory cards are already present.")
    raise SystemExit(0)
start, end = source.index("function PB(){"), source.index("function FB(")
section = source[start:end]

def replace_once(old, new):
    global section
    if section.count(old) != 1:
        raise SystemExit(f"Expected one inventory patch target, found {section.count(old)}: {old[:80]}")
    section = section.replace(old, new, 1)

replace_once("[d,f]=(0,v.useState)(!1);function p", "[d,f]=(0,v.useState)(!1),[rizqSelectedCategory,rizqChooseCategory]=(0,v.useState)(null);function p")
replace_once(
    "]})]}),(0,z.jsx)(rn,{children:(0,z.jsx)(`div`,{className:`overflow-x-auto`",
    "]})]}),rizqSelectedCategory===null?(0,z.jsxs)(`div`,{children:[(0,z.jsx)(`p`,{className:`mb-3 text-sm text-rizq-light/60`,children:`اختر فئة لعرض أصنافها`}),(0,z.jsxs)(`div`,{className:`grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5`,children:[...i.map(rizqCategory=>(0,z.jsxs)(`button`,{type:`button`,onClick:()=>rizqChooseCategory(rizqCategory.id),className:`rounded-2xl border border-white/10 bg-white/5 p-5 text-right transition hover:border-rizq-gold/60 hover:bg-white/10`,children:[(0,z.jsx)(B,{name:rizqCategory.icon||`box`,size:24,className:`mb-3 text-rizq-gold`}),(0,z.jsx)(`strong`,{className:`block`,children:rizqCategory.name_ar}),(0,z.jsxs)(`span`,{className:`text-sm text-rizq-light/50`,children:[r.filter(rizqProduct=>rizqProduct.category_id===rizqCategory.id).length,` صنف`]})]},rizqCategory.id)),(0,z.jsxs)(`button`,{type:`button`,onClick:()=>rizqChooseCategory(`*`),className:`rounded-2xl border border-white/10 bg-white/5 p-5 text-right transition hover:border-rizq-gold/60`,children:[(0,z.jsx)(`strong`,{className:`block`,children:`كل الأصناف`}),(0,z.jsxs)(`span`,{className:`text-sm text-rizq-light/50`,children:[r.length,` صنف`]})]})]})]}):(0,z.jsxs)(`div`,{className:`flex items-center gap-3`,children:[(0,z.jsx)(`button`,{type:`button`,onClick:()=>rizqChooseCategory(null),className:`rounded-lg border border-white/15 px-3 py-2 text-sm hover:border-rizq-gold/60`,children:`← كل الفئات`}),(0,z.jsx)(`h2`,{className:`text-lg font-bold`,children:rizqSelectedCategory===`*`?`كل الأصناف`:p(rizqSelectedCategory)})]}),rizqSelectedCategory!==null&&(0,z.jsx)(rn,{children:(0,z.jsx)(`div`,{className:`overflow-x-auto`"
)
replace_once("`tbody`,{children:r.map(e=>", "`tbody`,{children:r.filter(e=>rizqSelectedCategory===`*`||e.category_id===rizqSelectedCategory).map(e=>")
replace_once("IB,{product:a,categories:i,onClose:", "IB,{product:a,categories:i,defaultCategory:rizqSelectedCategory===`*`?null:rizqSelectedCategory,onClose:")
replace_once("RB,{categories:i,onClose:()=>f(!1),onChange:t}", "RB,{categories:i,onClose:()=>f(!1),onChange:()=>{rizqChooseCategory(null),t()}}")

new_source = source[:start] + section + source[end:]
old = "function IB({product:e,categories:t,onClose:n,onSaved:r})"
new = "function IB({product:e,categories:t,defaultCategory:rizqDefaultCategory,onClose:n,onSaved:r})"
if new_source.count(old) != 1:
    raise SystemExit("Could not safely locate product editor.")
new_source = new_source.replace(old, new, 1)
old = "category_id:e?.category_id??t[0]?.id??``"
new = "category_id:e?.category_id??rizqDefaultCategory??t[0]?.id??``"
if new_source.count(old) != 1:
    raise SystemExit("Could not safely locate product category default.")
new_source = new_source.replace(old, new, 1)

stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
backup = bundle.with_name(bundle.name + ".before-inventory-cards-" + stamp + ".bak")
shutil.copy2(bundle, backup)
bundle.write_text(new_source, encoding="utf-8")
print(f"Applied inventory cards; backup: {backup}")
