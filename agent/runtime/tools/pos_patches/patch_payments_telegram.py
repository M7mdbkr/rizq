"""Payment methods (mada, Visa, Mastercard, delivery apps) and Telegram fixes in the compiled POS.

Payments: a new database is seeded with cash, mada, Visa, Mastercard, Apple Pay, STC Pay and
the main Saudi delivery apps. An existing database gets the missing ones added once (a marker
row in `rizq_meta`), so a method the owner later deletes stays deleted.

Telegram:
- The daily report was sent the first time the POS opened each (UTC) day, so it reported a
  day that had barely started. It now goes out once after the closing hour (default 23:00
  local), with that day's figures, and is retried if Telegram did not accept it.
- "Top sellers" in that report was all-time; it is now today's.
- Messages use HTML; a store or item name containing < or & made Telegram reject the whole
  message silently. A rejected message is now resent as plain text.
- One failed poll could stop the polling loop for good; errors are now contained.

Re-runnable: exits early when already applied. `--check` reports without writing.
"""
from pathlib import Path
import datetime
import shutil
import sys

MARK = "rizq-payments-v2"
bundle = Path(__file__).resolve().parents[4] / "rizq-pos/assets/index-c3uFjRpt.js"
source = bundle.read_text(encoding="utf-8")
if MARK in source:
    print("Payment methods and Telegram fixes are already present.")
    raise SystemExit(0)

METHODS = [
    ("cash", "n.cash||`نقدًا`", "cash"),
    ("card", "`شبكة (مدى)`", "card"),
    ("visa", "`فيزا`", "card"),
    ("mastercard", "`ماستركارد`", "card"),
    ("applepay", "`Apple Pay`", "card"),
    ("stcpay", "`STC Pay`", "card"),
    ("hungerstation", "`هنقرستيشن`", "delivery"),
    ("jahez", "`جاهز`", "delivery"),
    ("akita", "n.akita||`كيتا`", "delivery"),
    ("merzool", "n.merzool||`مرسول`", "delivery"),
    ("ninja", "n.ninja||`نينجا`", "delivery"),
    ("toyou", "`ToYou`", "delivery"),
    ("thechefz", "`ذا شفز`", "delivery"),
]
seed = ",".join(f"[`{i}`,{name},`{kind}`]" for i, name, kind in METHODS)

replacements = [
    # 1. Seed every method on a new database; top up an existing one exactly once.
    ("function ue(e){let t=e.exec(`SELECT COUNT(*) AS c FROM payment_methods`);if((t.length?Number(t[0].values[0][0]):0)>0)return;let n={};",
     "function ue(e){e.run(`CREATE TABLE IF NOT EXISTS rizq_meta (key TEXT PRIMARY KEY, value TEXT)`);"
     "if(e.exec(`SELECT 1 FROM rizq_meta WHERE key='" + MARK + "'`).length)return;"
     "let t=e.exec(`SELECT COALESCE(MAX(sort),-1)+1 FROM payment_methods`),rizqStart=t.length?Number(t[0].values[0][0]):0;let n={};"),
    ("[[`cash`,n.cash||`نقدًا`,`cash`],[`card`,`شبكة (مدى)`,`card`],[`akita`,n.akita||`كيتا`,`delivery`],[`merzool`,n.merzool||`مرسول`,`delivery`],[`ninja`,n.ninja||`نينجا`,`delivery`]].forEach(([t,n,r],i)=>{e.run(`INSERT INTO payment_methods (id, name, kind, active, sort) VALUES (?,?,?,1,?)`,[t,n,r,i])})}",
     "[" + seed + "].forEach(([t,n,r],i)=>{e.run(`INSERT OR IGNORE INTO payment_methods (id, name, kind, active, sort) VALUES (?,?,?,1,?)`,[t,n,r,rizqStart+i])}),"
     "e.run(`INSERT OR REPLACE INTO rizq_meta (key,value) VALUES ('" + MARK + "','1')`)}"),
    # 2. Resend as plain text when Telegram rejects the HTML.
    ("async function nr(e,t){let n=R(),r=t||n.telegramChatId;return!n.telegramToken||!r?!1:(await tr(n.telegramToken,`sendMessage`,{chat_id:r,text:e,parse_mode:`HTML`})).ok}",
     "async function nr(e,t){let n=R(),r=t||n.telegramChatId;if(!n.telegramToken||!r)return!1;"
     "let i=await tr(n.telegramToken,`sendMessage`,{chat_id:r,text:e,parse_mode:`HTML`});"
     "return!i.ok&&i.error_code===400&&(i=await tr(n.telegramToken,`sendMessage`,{chat_id:r,text:e.replace(/<\\/?b>/g,``)})),!!i.ok}"),
    # 3. Daily report after closing, with the local date, retried at most once a minute.
    ("async function pr(){let e=R();if(!e.telegramAutoDaily||!e.telegramToken||!e.telegramChatId)return;let t=new Date().toISOString().slice(0,10);localStorage.getItem(lr)!==t&&(localStorage.setItem(lr,t),await nr(ir()))}",
     "var rizqTgTry=0;async function pr(){let e=R();if(!e.telegramAutoDaily||!e.telegramToken||!e.telegramChatId)return;"
     "let d=new Date();if(d.getHours()<(Number(e.telegramDailyHour)||23))return;"
     "let t=d.getFullYear()+`-`+String(d.getMonth()+1).padStart(2,`0`)+`-`+String(d.getDate()).padStart(2,`0`);"
     "if(localStorage.getItem(lr)===t||Date.now()-rizqTgTry<6e4)return;rizqTgTry=Date.now();"
     "await nr(ir())&&localStorage.setItem(lr,t)}"),
    # 4. Check the report on every poll, and never let one failure stop the loop.
    ("let t=async()=>{ur&&(await fr(),dr=setTimeout(t,4e3))};t()}",
     "let t=async()=>{if(!ur)return;try{await fr(),await pr()}catch(e){}dr=setTimeout(t,4e3)};t()}"),
    # 5. Today's top sellers in the daily report.
    ("function bt(e=8){return me(`SELECT oi.product_id, p.name_ar,",
     "function bt(e=8,rizqToday=!1){return me(`SELECT oi.product_id, p.name_ar,"),
    ("     JOIN orders o ON o.id = oi.order_id\n     WHERE o.status='completed'\n     GROUP BY oi.product_id ORDER BY revenue DESC LIMIT $l`",
     "     JOIN orders o ON o.id = oi.order_id\n     WHERE o.status='completed'${rizqToday?` AND date(o.created_at)=date('now')`:``}\n     GROUP BY oi.product_id ORDER BY revenue DESC LIMIT $l`"),
    ("r=bt(3),i=it(),a=R()", "r=bt(3,!0),i=it(),a=R()"),
]

updated = source
for old, new in replacements:
    count = updated.count(old)
    if count != 1:
        raise SystemExit(f"Expected one patch target, found {count}: {old[:90]}")
    updated = updated.replace(old, new, 1)

if "--check" in sys.argv:
    print(f"All {len(replacements)} targets found; nothing written.")
    raise SystemExit(0)
backup = bundle.with_name(bundle.name + ".before-payments-telegram-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + ".bak")
shutil.copy2(bundle, backup)
bundle.write_text(updated, encoding="utf-8")
print(f"Payment methods and Telegram fixes applied; backup: {backup}")
