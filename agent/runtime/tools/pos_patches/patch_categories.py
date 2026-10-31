"""Re-runnable surgical patch for the compiled Rizq POS bundle: category ordering + integrity.

The Rizq POS React source is not available; only the compiled bundle is. These are
exact-string replacements of unique SQL literals and one unique handler expression in
`rizq-pos/assets/index-*.js`. Each patch is idempotent: it detects the patched form and
skips. A timestamped backup is written before the first change.

WHY each patch (root causes found in the bundle's data layer):
  P1 nt()  products list for the "إدارة المخزون" screen was `ORDER BY created_at DESC`,
           so the items table was newest-first with NO grouping by category, and every
           seeded item shares one created_at second so ties ordered arbitrarily.
  P2 Qe()  categories list was `ORDER BY created_at`; there is no sort column, so the
           sections could not be ordered at all. Feeds the cashier menu chips, the
           "إدارة الفئات" modal and the new-item category dropdown.
  P3 et()  `DELETE FROM categories` had no guard and the bundle never sets
           `PRAGMA foreign_keys=ON`, so sql.js left FK enforcement off: deleting a
           category silently orphaned its products (category shows "—", item belongs
           to no menu chip).
  P4       the category-delete click handler called notify("deleted") unconditionally,
           so a refused delete would still report success. Wrap in try/catch.

Canonical section order is keyed on category *id*, not name, so renaming a section in
the POS does not drop it out of order. Owner-created sections (cat_<random>) sort after
the five built-ins, by created_at then name.

Proper fix when the React source returns: a `sort_order` column on categories, owner-
drag reordering, and `PRAGMA foreign_keys=ON` at init. Tracked as RIZQ-CAT-1.
"""
from __future__ import annotations

import datetime
import glob
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
POS = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", "rizq-pos"))

# Canonical restaurant menu order, by stable category id.
RANK = "CASE c.id WHEN 'cat_app' THEN 1 WHEN 'cat_grill' THEN 2 WHEN 'cat_meal' THEN 3 WHEN 'cat_drink' THEN 4 WHEN 'cat_sweet' THEN 5 ELSE 90 END"
RANK_SELF = RANK.replace("c.id", "id")

P1_OLD = "SELECT * FROM products ORDER BY created_at DESC"
P1_NEW = (
    "SELECT p.* FROM products p LEFT JOIN categories c ON c.id=p.category_id "
    "ORDER BY CASE WHEN c.id IS NULL THEN 99 ELSE 0 END, "
    f"{RANK}, c.created_at, c.name_ar, p.name_ar"
)

P2_OLD = "SELECT * FROM categories ORDER BY created_at"
P2_NEW = f"SELECT * FROM categories ORDER BY {RANK_SELF}, created_at, name_ar"

# et(): refuse to delete a section that still holds items, instead of orphaning them.
P3_OLD = "var et=e=>ge(`DELETE FROM categories WHERE id=$id`,{$id:e})"
P3_NEW = (
    "var et=e=>{let rizqN=he(`SELECT COUNT(*) AS n FROM products WHERE category_id=$id`,{$id:e});"
    "if((rizqN&&rizqN.n||0)>0)throw Error(`ما نقدر نحذف القسم: فيه `+rizqN.n+` صنف. انقل الأصناف لقسم ثاني أول.`);"
    "return ge(`DELETE FROM categories WHERE id=$id`,{$id:e})}"
)

# The delete handler reported success even when the delete did nothing.
P4_OLD = "confirm(`حذف فئة \"${t}\"؟`)&&(et(e),r(`تم حذف الفئة`,`success`),n())"
P4_NEW = (
    "confirm(`حذف فئة \"${t}\"؟`)&&(()=>{try{et(e),r(`تم حذف الفئة`,`success`),n()}"
    "catch(rizqE){r(rizqE.message||`تعذر حذف الفئة`,`error`)}})()"
)

PATCHES = [
    ("P1 inventory list grouped by section", P1_OLD, P1_NEW),
    ("P2 sections in canonical menu order", P2_OLD, P2_NEW),
    ("P3 refuse deleting a section that has items", P3_OLD, P3_NEW),
    ("P4 surface a refused section delete", P4_OLD, P4_NEW),
]


def bundle_path() -> str:
    hits = [p for p in glob.glob(os.path.join(POS, "assets", "index-*.js"))]
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
    backup = f"{path}.before-categories-patch-{stamp}.bak"
    shutil.copy2(path, backup)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f"\nbackup: {backup}")
    print(f"written: {path}  ({applied} applied, {already} already in place)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(check_only="--check" in sys.argv))
