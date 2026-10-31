"""The local bot answers everyday discount wording ("سويلي خصم", "ابغى خصم") itself, instead
of sending it to the AI model, which was slow and once never answered."""
from pathlib import Path
import datetime
import shutil

bundle = Path(__file__).resolve().parents[4] / "rizq-pos/assets/index-c3uFjRpt.js"
source = bundle.read_text(encoding="utf-8")
OLD = "function Zn(e,t){if(!Mn(e,`كود خصم`,`انشئ كود`,`اضف كود`,`سوي كود`,`كوبون`))return null;"
NEW = ("function Zn(e,t){if(!Mn(e,`كود خصم`,`انشئ كود`,`اضف كود`,`سوي كود`,`كوبون`,`سوي خصم`,`سويلي خصم`,`سوي لي خصم`,"
       "`اعمل خصم`,`ابي خصم`,`ابغى خصم`,`ابغي خصم`,`خصم جديد`,`اضف خصم`,`انشئ خصم`))return null;")
if "`سويلي خصم`" in source:
    print("Discount phrases are already present.")
    raise SystemExit(0)
if source.count(OLD) != 1:
    raise SystemExit(f"Expected one target, found {source.count(OLD)}")
backup = bundle.with_name(bundle.name + ".before-discount-phrases-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + ".bak")
shutil.copy2(bundle, backup)
bundle.write_text(source.replace(OLD, NEW, 1), encoding="utf-8")
print(f"Discount phrases added; backup: {backup}")
