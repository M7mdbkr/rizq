"""Products sheet import: .xlsx and .csv, validation, and owner-only access."""
import base64
import io
import unittest
import zipfile
from unittest import mock

from rizq_agent import importer, permissions, web


def xlsx(rows):
    strings, sheet = [], []
    for r, row in enumerate(rows, start=1):
        cells = []
        for c, value in enumerate(row):
            ref = chr(65 + c) + str(r)
            if isinstance(value, str):
                strings.append(value)
                cells.append(f'<c r="{ref}" t="s"><v>{len(strings) - 1}</v></c>')
            else:
                cells.append(f'<c r="{ref}"><v>{value}</v></c>')
        sheet.append(f'<row r="{r}">{"".join(cells)}</row>')
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/sharedStrings.xml", f'<sst {ns}>' + "".join(f"<si><t>{s}</t></si>" for s in strings) + "</sst>")
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet {ns}><sheetData>{"".join(sheet)}</sheetData></worksheet>')
    return buf.getvalue()


class ImporterTests(unittest.TestCase):
    def test_excel_rows_become_products(self):
        raw = xlsx([["اسم الصنف", "السعر", "الكمية", "القسم"], ["عصير مانجو", 18, 40, "المشروبات"], ["كنافة", "25.5", 10, ""]])
        out = importer.products("menu.xlsx", raw)
        self.assertEqual([i["name"] for i in out["items"]], ["عصير مانجو", "كنافة"])
        self.assertEqual(out["items"][0], {"name": "عصير مانجو", "price": 18.0, "cost": 0.0, "stock": 40, "min_stock": 5, "category": "المشروبات"})

    def test_csv_with_english_headers_and_bad_rows(self):
        raw = "name,price,qty\nTea,5,100\n,9,1\nCake,abc,2\nTea,5,1\nCoffee,-3,1\n".encode()
        out = importer.products("items.csv", raw)
        self.assertEqual([i["name"] for i in out["items"]], ["Tea"])
        self.assertEqual([s["reason"] for s in out["skipped"]], ["بدون اسم", "رقم غير مفهوم", "مكرر في الملف", "سعر غير صحيح"])

    def test_missing_columns_and_bad_files_are_explained(self):
        with self.assertRaises(ValueError):
            importer.products("x.csv", "الاسم,الكمية\nشاي,3\n".encode())          # no price column
        with self.assertRaises(ValueError):
            importer.products("x.xlsx", b"not a zip")
        with self.assertRaises(ValueError):
            importer.products("x.xls", b"old format")
        with self.assertRaises(ValueError):
            importer.products("x.csv", b"")

    def test_only_the_owner_can_import(self):
        handler = web.Handler.__new__(web.Handler)
        handler.path = "/api/import/products"
        handler.read_json = lambda: {"filename": "a.csv", "file_base64": base64.b64encode(b"name,price\nTea,5\n").decode()}
        handler.guard = lambda _: None
        handler.send_json = mock.Mock()
        handler.actor = mock.Mock(side_effect=permissions.PermissionDenied("owner only"))
        handler.do_POST()
        self.assertEqual(handler.send_json.call_args.args[1], 403)
        handler.actor = mock.Mock(return_value={"user_id": "o", "role": "owner"})
        with mock.patch.object(permissions, "audit"):
            handler.do_POST()
        self.assertEqual(handler.send_json.call_args.args[0]["items"][0]["name"], "Tea")


if __name__ == "__main__":
    unittest.main()
