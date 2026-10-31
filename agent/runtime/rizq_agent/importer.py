"""Read a products sheet (.xlsx or .csv) the owner uploads, from this device or a tablet.

Only parsing and validation happen here. The rows go back to the owner as a preview, and
the cashier adds them only after the owner presses confirm, so a bad file never lands in
the database. Standard library only: an .xlsx is a zip of XML parts.
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
import xml.etree.ElementTree as ET

MAX_BYTES = 5 * 2**20
MAX_ROWS = 2000
HEADERS = {
    "name": ("اسم الصنف", "الاسم", "اسم", "الصنف", "المنتج", "name", "item", "product"),
    "price": ("سعر البيع", "السعر", "سعر", "price"),
    "cost": ("سعر التكلفة", "التكلفة", "تكلفة", "cost"),
    "stock": ("الكمية", "الكميه", "المخزون", "كمية", "stock", "qty", "quantity"),
    "category": ("القسم", "الفئة", "التصنيف", "category"),
    "min_stock": ("الحد الأدنى", "الحد الادنى", "min", "min_stock"),
}


def _tags(element, name: str) -> list:
    """Descendants with this local tag name, whatever the namespace (Python 3.9 iter has no wildcard)."""
    return [e for e in element.iter() if e.tag.rsplit("}", 1)[-1] == name]


def _col(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


def _xlsx(raw: bytes) -> list:
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        if sum(i.file_size for i in z.infolist()) > 40 * 2**20:
            raise ValueError("الملف كبير جدًا بعد فك الضغط.")
        def part(name):
            data = z.read(name)
            if b"<!DOCTYPE" in data[:2000]:
                raise ValueError("ملف Excel غير صالح.")
            return ET.fromstring(data)
        names = z.namelist()
        shared = []
        if "xl/sharedStrings.xml" in names:
            shared = ["".join(t.text or "" for t in _tags(si, "t")) for si in _tags(part("xl/sharedStrings.xml"), "si")]
        sheets = sorted(n for n in names if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n))
        if not sheets:
            raise ValueError("ما لقيت ورقة بيانات داخل الملف.")
        rows = []
        for row in _tags(part(sheets[0]), "row"):
            cells = {}
            for c in _tags(row, "c"):
                kind, v = c.get("t"), next(iter(_tags(c, "v")), None)
                if kind == "s" and v is not None:
                    value = shared[int(v.text)]
                elif kind == "inlineStr":
                    value = "".join(t.text or "" for t in _tags(c, "t"))
                else:
                    value = v.text if v is not None else ""
                cells[_col(c.get("r", "A"))] = value or ""
            if cells:
                rows.append([cells.get(i, "") for i in range(max(cells) + 1)])
        return rows


def read_table(filename: str, raw: bytes) -> list:
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError("الملف فاضي أو أكبر من 5 ميجا.")
    name = filename.lower()
    if name.endswith(".csv"):
        return list(csv.reader(io.StringIO(raw.decode("utf-8-sig", errors="replace"))))
    if name.endswith(".xlsx"):
        try:
            return _xlsx(raw)
        except (zipfile.BadZipFile, KeyError, ET.ParseError):
            raise ValueError("ما قدرت أقرأ ملف Excel. احفظه بصيغة .xlsx أو .csv وجرّب مرة ثانية.") from None
    raise ValueError("الصيغ المدعومة: .xlsx و .csv (ملف .xls القديم احفظه من Excel بصيغة .xlsx).")


def _number(text):
    text = str(text).strip().translate(str.maketrans("٠١٢٣٤٥٦٧٨٩٫", "0123456789.")).replace(",", "").replace("ر.س", "").strip()
    return float(text) if text else None


def products(filename: str, raw: bytes) -> dict:
    rows = [r for r in read_table(filename, raw) if any(str(c).strip() for c in r)]
    if len(rows) < 2:
        raise ValueError("الملف ما فيه صفوف بيانات تحت العناوين.")
    header = [str(h).strip().lower() for h in rows[0]]
    columns = {}
    for key, names in HEADERS.items():
        for i, h in enumerate(header):
            if i not in columns.values() and any(h == n.lower() or n.lower() in h for n in names):
                columns[key] = i
                break
    if "name" not in columns or "price" not in columns:
        raise ValueError("لازم يكون في الملف عمود لاسم الصنف وعمود للسعر (مثل: اسم الصنف، السعر).")
    items, skipped, seen = [], [], set()
    for n, row in enumerate(rows[1:MAX_ROWS + 1], start=2):
        get = lambda k: row[columns[k]] if k in columns and columns[k] < len(row) else ""
        name = str(get("name")).strip()[:80]
        try:
            price = _number(get("price"))
            cost = _number(get("cost")) or 0.0
            stock = int(_number(get("stock")) or 0)
            minimum = int(_number(get("min_stock")) or 5)
        except ValueError:
            skipped.append({"row": n, "reason": "رقم غير مفهوم"})
            continue
        if not name:
            skipped.append({"row": n, "reason": "بدون اسم"})
        elif price is None or price < 0 or price > 100000:
            skipped.append({"row": n, "reason": "سعر غير صحيح"})
        elif stock < 0 or cost < 0:
            skipped.append({"row": n, "reason": "كمية أو تكلفة سالبة"})
        elif name in seen:
            skipped.append({"row": n, "reason": "مكرر في الملف"})
        else:
            seen.add(name)
            items.append({"name": name, "price": round(price, 2), "cost": round(cost, 2), "stock": stock,
                          "min_stock": minimum, "category": str(get("category")).strip()[:40]})
    if len(rows) - 1 > MAX_ROWS:
        skipped.append({"row": MAX_ROWS + 2, "reason": f"أكثر من {MAX_ROWS} صف، الباقي ما انقرأ"})
    return {"items": items, "skipped": skipped, "columns": {k: rows[0][i] for k, i in columns.items()}}
