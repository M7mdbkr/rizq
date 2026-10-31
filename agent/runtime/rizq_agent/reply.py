"""The last wall between a model's words and the owner.

The local model (Qwen3 through Ollama) sometimes returned its hidden reasoning, drifted into
Chinese, wrapped answers in markdown and tool markup, repeated a line many times, or wrote a
figure no tool had returned. A prompt asks it not to; this module makes sure, in code:

1. clean: remove <think> blocks, tool/control markup, markdown symbols and emoji; drop repeated
   lines; cap the length at a sentence boundary.
2. check: the text must be in the asker's language, use no foreign script (CJK, Cyrillic...),
   and every significant number in it must appear in a tool result from this turn.
3. fall back: anything that fails is discarded. The owner gets the verified skill answer from
   this turn if there is one, otherwise a short honest templated reply. Each discard is logged.
"""
from __future__ import annotations

import datetime
import json
import os
import re

MAX_CHARS = 1200

_THINK = re.compile(r"<think>.*?(?:</think>|\Z)", re.S | re.I)
_CONTROL = re.compile(r"<\|[^|>]{0,40}\|>|</?(?:tool_call|tool_response|function_call|tools?|answer|response|reasoning)[^>]{0,40}>", re.I)
_TOOL_JSON = re.compile(r'\{\s*"(?:name|tool|function|arguments|tool_calls)"\s*:[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', re.S)
_TAGS = re.compile(r"</?[A-Za-z][A-Za-z0-9_-]{0,20}(?:\s[^<>]{0,80})?/?>")
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍⃣]")
_ALLOWED = re.compile(r"[\s\x20-\x7E -ÿ؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿"
                      r"ﹰ-﻿ -⁯₠-⃏←-⇿−]")
_AR = re.compile(r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]")
_LATIN = re.compile(r"[A-Za-z]")
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫", "01234567890123456789.")
_NUMBER = re.compile(r"\d[\d,٬]*(?:\.\d+)?")
_OFFER = re.compile(r"^(?:هل (?:ترغب|تريد|تحب|تود|تبغى|تبي)|تبي أ|تبغى أ|إذا (?:تحب|تبي|ترغب)|لو (?:تحب|تبي)|"
                    r"would you like|do you want|let me know if|if you(?:'d| would) like|feel free)", re.I)

# Figures that are rules, not data: VAT rate, zakat rate, percent base.
CONSTANTS = {15.0, 2.5, 100.0, 0.15, 0.025}


def language(text: str) -> str:
    """'ar', 'en', or 'any' when the text has no words to judge by (an order id, a number)."""
    if _AR.search(text or ""):
        return "ar"
    words = [w for w in re.findall(r"[A-Za-z0-9_]+", text or "") if not re.search(r"[\d_]", w)]
    return "en" if words else "any"


def clean(text: str) -> str:
    text = text or ""
    if "</think>" in text.lower() and "<think>" not in text.lower():
        text = text[text.lower().rindex("</think>") + len("</think>"):]   # opening tag eaten by the server
    text = _THINK.sub("", text)
    text = _CONTROL.sub("", text)
    text = _TOOL_JSON.sub("", text)
    text = _TAGS.sub("", text)
    text = _EMOJI.sub("", text)
    out, seen = [], {}
    for line in text.splitlines():
        line = re.sub(r"^\s{0,3}#{1,6}\s*", "", line)                   # headings
        line = re.sub(r"^\s*(?:[-*+•]|\d+[.)])\s+", "", line)           # list markers
        if re.fullmatch(r"\s*\|?[\s:|-]{3,}\|?\s*", line):                # table rulers
            continue
        line = line.replace("**", "").replace("__", "").replace("`", "").replace("|", " ")
        line = re.sub(r"[ \t]{2,}", " ", line).strip()
        key = line.casefold()
        if key:
            seen[key] = seen.get(key, 0) + 1
            if seen[key] > 1:                                             # a repeated line ends the answer
                if seen[key] > 2:
                    break
                continue
        out.append(line)
    while out and (not out[-1] or _OFFER.match(out[-1])):              # unasked follow-up offers
        out.pop()
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
    if len(text) > MAX_CHARS:
        cut = text[:MAX_CHARS]
        end = max(cut.rfind(p) for p in (".", "؟", "?", "!", "\n", "،"))
        text = cut[:end + 1].strip() if end > MAX_CHARS // 2 else cut.strip() + "…"
    return text


def _numbers_in(value, found: set) -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        found.add(round(float(value), 2))
    elif isinstance(value, str):
        for n in _NUMBER.findall(value.translate(_DIGITS)):
            try:
                found.add(round(float(n.replace(",", "").replace("٬", "")), 2))
            except ValueError:
                pass
    elif isinstance(value, dict):
        for v in value.values():
            _numbers_in(v, found)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _numbers_in(v, found)


def ungrounded_numbers(text: str, outputs) -> list:
    """Significant figures in `text` that no tool returned this turn. Small whole numbers
    (days, counts under 100) and the fixed rates are not treated as data claims."""
    from . import phrase
    found: set = set()
    for name, _args, out in outputs or []:
        _numbers_in(out, found)
        # Figures phrase.py derives from a verified result (a margin, a VAT share) are
        # deterministic, so they count as grounded too.
        if name == "run_skill" and isinstance(out, dict) and out.get("status") == "ok":
            try:
                _numbers_in(phrase.skill_answer(out), found)
            except Exception:
                pass
    known = found | {round(v, 0) for v in found} | {round(v, 1) for v in found} | CONSTANTS
    missing = []
    for raw in _NUMBER.findall((text or "").translate(_DIGITS)):
        try:
            value = round(float(raw.replace(",", "").replace("٬", "")), 2)
        except ValueError:
            continue
        if value < 100 and value == int(value):
            continue
        if value not in known:
            missing.append(raw)
    return missing


def problems(text: str, question: str, outputs) -> list:
    if not text.strip():
        return ["empty"]
    issues = []
    if any(not _ALLOWED.match(ch) for ch in text):
        issues.append("foreign_script")
    ar, latin = len(_AR.findall(text)), len(_LATIN.findall(text))
    ratio = ar / (ar + latin) if ar + latin else (1.0 if language(question) != "en" else 0.0)
    if language(question) == "ar" and ratio < 0.35:
        issues.append("wrong_language")
    if language(question) == "en" and ratio > 0.6:
        issues.append("wrong_language")
    if ungrounded_numbers(text, outputs):
        issues.append("ungrounded_numbers")
    return issues


def _verified_answer(question: str, outputs) -> str | None:
    """The last skill result from this turn that both checks agreed on."""
    from . import phrase
    for name, _args, out in reversed(outputs or []):
        if name == "run_skill" and isinstance(out, dict) and out.get("status") == "ok":
            if language(question) != "en":
                return phrase.skill_answer(out)
            value = out.get("value")
            title = out.get("title_en") or str(out.get("skill", "Result")).replace("_", " ")
            phraser = ENGLISH.get(out.get("skill"))
            if phraser and isinstance(value, dict):
                try:
                    return phraser(value)
                except (KeyError, TypeError, ValueError):
                    pass
            if isinstance(value, dict):
                parts = [f"{k.replace('_', ' ')}: {v}" for k, v in value.items() if not isinstance(v, (dict, list))]
                return f"Here is the verified figure for {title}: " + "; ".join(parts) + "."
            return f"Here is the verified figure for {title}: {value}."
    return None


ENGLISH = {
    "sales_period": lambda v: (f"Sales for the last {v['days']} day{'s' if v['days'] != 1 else ''}: {v['sales']:,.2f} SAR "
                               f"from {v['orders']} orders, including {v['vat']:,.2f} SAR VAT."),
    "vat_collected": lambda v: f"VAT collected in {v['month']}: {v['vat_collected']:,.2f} SAR.",
}


def fallback(question: str, outputs) -> str:
    verified = _verified_answer(question, outputs)
    if verified:
        return verified
    if language(question) != "en":
        return ("أبشر، بس هالمرة ما طلع معي جواب واضح أقدر أوثق فيه. "
                "جرّب تسألني بطريقة ثانية، مثلًا عن المبيعات أو الضريبة أو المخزون.")
    return ("Sorry, I couldn't put together a clear answer I can stand behind this time. "
            "Try asking another way, for example about sales, VAT or stock.")


_GREETING = re.compile(r"^\s*(هلا|هلا والله|اهلا|أهلا|مرحبا|مرحبًا|السلام عليكم|سلام|صباح الخير|مساء الخير|hi|hello|hey|good (morning|evening))"
                       r"[\s!.,،؟?]*(كيف حالك|كيفك|شلونك|how are you)?[\s!.,،؟?]*$", re.I)
_HOW = re.compile(r"^\s*(كيف حالك|كيفك|شلونك|وش اخبارك|how are you)[\s!.,،؟?]*$", re.I)
_THANKS = re.compile(r"^\s*(شكرا|شكرًا|مشكور|يعطيك العافية|تسلم|thanks|thank you)[\s!.,،؟?]*$", re.I)


def smalltalk(message: str) -> str | None:
    """Greetings and thanks get a warm, instant reply: no model, nothing to get wrong."""
    en = language(message) == "en"
    if _THANKS.match(message):
        return "You're welcome! Anything else?" if en else "العفو، حاضرين! تبي شيء ثاني؟"
    if _GREETING.match(message) or _HOW.match(message):
        if en:
            return "Hi! I'm doing well. Ask me about today's sales, stock or discounts, or use the quick actions above."
        hello = "وعليكم السلام! " if "السلام" in message else "هلا والله! "
        return hello + "تمام الحمد لله. اسألني عن مبيعات اليوم أو المخزون أو الخصومات، أو استخدم الاختيارات السريعة فوق."
    return None


def too_slow(question: str, tools=None) -> str:
    """The model took too long: give whatever was verified so far, or say so plainly."""
    verified = _verified_answer(question, getattr(tools, "outputs", None) or [])
    if verified:
        return verified
    if language(question) == "en":
        return "That took too long, so I stopped instead of keeping you waiting. Try a shorter question or use the quick actions."
    return "أخذ الطلب وقت طويل فوقفت بدل ما أخليك تنتظر. جرّب سؤال أقصر أو استخدم الاختيارات السريعة."


def step_budget(question: str) -> str:
    if language(question) == "ar":
        return "وقفت لأن الطلب احتاج خطوات كثيرة بدون نتيجة واضحة، بدل ما أدوّر بدون نهاية. اسألني بشكل أدق وأساعدك."
    return "I stopped after too many steps (step budget reached) instead of looping. Ask me again more specifically."


def finish(raw: str, question: str, tools=None, on_event=None) -> str:
    """Clean the model's final text; return it, or a safe fallback if it fails a check."""
    outputs = getattr(tools, "outputs", None) or []
    text = clean(raw)
    issues = problems(text, question, outputs)
    if not issues:
        return text
    if on_event:
        on_event(f"  ⚠ reply discarded: {', '.join(issues)}")
    out_dir = getattr(tools, "out_dir", None)
    if out_dir:
        try:
            with open(os.path.join(out_dir, "reply_guard.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps({"at": datetime.datetime.now().isoformat(timespec="seconds"),
                                    "issues": issues, "question": question[:300], "raw": (raw or "")[:800]},
                                   ensure_ascii=False) + "\n")
        except OSError:
            pass
    return fallback(question, outputs)
