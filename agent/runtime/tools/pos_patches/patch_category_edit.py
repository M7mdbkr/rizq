"""Add inline category rename to the compiled inventory category manager."""
from pathlib import Path
import datetime
import shutil

bundle = Path(__file__).resolve().parents[4] / "rizq-pos/assets/index-c3uFjRpt.js"
source = bundle.read_text(encoding="utf-8")
if "rizqEditingCategory" in source:
    print("Category editing is already present.")
    raise SystemExit(0)
start, end = source.index("function RB({categories:"), source.index("var zB={};")
part = source[start:end]

def replace_once(old, new):
    global part
    if part.count(old) != 1:
        raise SystemExit(f"Expected one category patch target, found {part.count(old)}: {old[:80]}")
    part = part.replace(old, new, 1)

replace_once("[c,l]=(0,v.useState)(NB[0]);function u()", "[c,l]=(0,v.useState)(NB[0]),[rizqEditingCategory,rizqEditCategory]=(0,v.useState)(null),[rizqCategoryName,rizqSetCategoryName]=(0,v.useState)(``);function u()")
old = "(0,z.jsxs)(`span`,{className:`flex items-center gap-2`,children:[(0,z.jsx)(`span`,{className:`h-3 w-3 rounded-full`,style:{background:e.color}}),e.name_ar]}),(0,z.jsx)(`button`,{onClick:()=>d(e.id,e.name_ar),className:`text-rizq-light/40 hover:text-rizq-danger`,children:(0,z.jsx)(B,{name:`trash`,size:16})})"
new = "rizqEditingCategory===e.id?(0,z.jsx)(`input`,{value:rizqCategoryName,onChange:rizqEv=>rizqSetCategoryName(rizqEv.target.value),className:`min-w-0 flex-1 rounded-lg border border-white/20 bg-rizq-black px-2 py-1`,'aria-label':`اسم الفئة`}):(0,z.jsxs)(`span`,{className:`flex items-center gap-2`,children:[(0,z.jsx)(`span`,{className:`h-3 w-3 rounded-full`,style:{background:e.color}}),e.name_ar]}),(0,z.jsxs)(`div`,{className:`flex items-center gap-3`,children:[rizqEditingCategory===e.id?(0,z.jsxs)(z.Fragment,{children:[(0,z.jsx)(`button`,{type:`button`,onClick:()=>{let rizqName=rizqCategoryName.trim();if(!rizqName)return r(`أدخل اسم الفئة`,`error`);tt(e.id,rizqName),rizqEditCategory(null),n(),r(`تم تعديل الفئة`,`success`)},className:`text-rizq-gold`,children:`حفظ`}),(0,z.jsx)(`button`,{type:`button`,onClick:()=>rizqEditCategory(null),children:`إلغاء`})]}):(0,z.jsx)(`button`,{type:`button`,onClick:()=>{rizqEditCategory(e.id),rizqSetCategoryName(e.name_ar)},className:`text-rizq-light/60 hover:text-rizq-gold`,children:`تعديل`}),(0,z.jsx)(`button`,{type:`button`,onClick:()=>d(e.id,e.name_ar),className:`text-rizq-light/40 hover:text-rizq-danger`,'aria-label':`حذف الفئة`,children:(0,z.jsx)(B,{name:`trash`,size:16})})]})"
replace_once(old, new)
updated = source[:start] + part + source[end:]
backup = bundle.with_name(bundle.name + ".before-category-edit-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + ".bak")
shutil.copy2(bundle, backup)
bundle.write_text(updated, encoding="utf-8")
print(f"Category editing added; backup: {backup}")
