"""Telegram: pair the owner's chat with a one-time code instead of a hand-typed Chat ID.

Pressing "test" with no Chat ID shows a 6-digit code. The owner sends it to the bot from
their phone and presses test again; the chat that sent exactly that code is bound. A
stranger who merely messages the bot is never bound. Re-runnable; `--check` dry-runs.
"""
from pathlib import Path
import datetime
import shutil
import sys

bundle = Path(__file__).resolve().parents[4] / "rizq-pos/assets/index-c3uFjRpt.js"
source = bundle.read_text(encoding="utf-8")
if "rizq-tg-pair" in source:
    print("Telegram pairing is already present.")
    raise SystemExit(0)
start = source.index("async function rr(){")
end = source.index("}function ir(){", start) + 1
NEW_RR = ("async function rr(){let e=R();if(!e.telegramToken)return{ok:!1,error:`أدخل التوكن`};"
          "let t=await tr(e.telegramToken,`getMe`);if(!t.ok)return{ok:!1,error:`التوكن غير صحيح`};"
          "let bot=`@`+(t.result?.username??``);"
          "if(!e.telegramChatId){let code=localStorage.getItem(`rizq-tg-pair`);"
          "if(!code){code=String(1e5+crypto.getRandomValues(new Uint32Array(1))[0]%9e5);localStorage.setItem(`rizq-tg-pair`,code)}"
          "let u=await tr(e.telegramToken,`getUpdates`,{timeout:0}),list=u.result||[];"
          "let m=list.map(x=>x.message).filter(x=>x&&x.chat&&x.chat.type===`private`&&String(x.text||``).trim()===code).pop();"
          "if(!m)return{ok:!1,code,error:`أرسل الرمز ${code} للبوت ${bot} من جوالك، ثم اضغط اختبار مرة ثانية`};"
          "localStorage.removeItem(`rizq-tg-pair`);"
          "list.length&&localStorage.setItem(`rizq-tg-offset`,String(list[list.length-1].update_id+1));"
          "Ne({telegramChatId:String(m.chat.id)})}"
          "return await nr(`تم ربط رزق بنجاح مع <b>${R().name}</b>\\nبوت: ${bot}\\nأرسل لي أي سؤال مثل: «كم مبيعات اليوم؟»`)"
          "?(gr(),{ok:!0}):{ok:!1,error:`تعذّر الإرسال، تأكد من Chat ID`}}")
updated = source[:start] + NEW_RR + source[end:]
OLD_D = "async function d(){u(),l(!0);let e=await rr();l(!1),"
if updated.count(OLD_D) != 1:
    raise SystemExit("settings test handler not found")
updated = updated.replace(OLD_D, "async function d(){u(),l(!0);let e=await rr();a(R().telegramChatId),l(!1),", 1)
if "--check" in sys.argv:
    print("Targets found; nothing written.")
    raise SystemExit(0)
backup = bundle.with_name(bundle.name + ".before-tg-pairing-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + ".bak")
shutil.copy2(bundle, backup)
bundle.write_text(updated, encoding="utf-8")
print(f"Telegram pairing added; backup: {backup}")
