"""Triage: decide what to do with an owner's request BEFORE any tool runs.

  answer    a skill covers it (sales, VAT, inventory, profit, top products, zakat)
  repair    investigate / fix data problems (derived data only, human confirms)
  build     a data question no skill covers: needs a model to write a maker + checker query
  escalate  a human must act (money on an issued invoice, refunds, lost orders)
  refuse    destructive, secret-seeking, edits money to a value, prompt injection, or out of scope

`hard` refusals (the five explicit dangerous checks) are refused before any model is called. The last fallback
("not understood") is NOT hard: with a model brain, the model gets a chance to understand it.

Order matters: the dangerous checks run first, so "delete all orders" can never reach a skill or a repair.
The agent's tools enforce the same limits again (read-only SQL, no money edits), so a wrong triage cannot cause damage.
"""
from __future__ import annotations

import re

from . import skills

ANSWER, REPAIR, BUILD, ESCALATE, REFUSE = "answer", "repair", "build", "escalate", "refuse"
ACTIONS = (ANSWER, REPAIR, BUILD, ESCALATE, REFUSE)

_DESTRUCTIVE = r"\b(delete|remove|drop|truncate|wipe|erase)\b|احذف|امسح|مسح|شيل|ازل|أزل|امحي"
_INJECTION = r"ignore (your|all|previous|the)|admin mode|jailbreak|developer mode|تجاهل|تخطى القواعد"
_SECRETS = r"\bpins?\b|password|passcode|token|api key|رقم سري|الرقم السري|الرموز السرية|[اأ]رقام ?(ال)?سري|رمز ?(ال)?سري|باسوورد|باسورد|كلمة السر|كلمة المرور"
_EDIT_VALUE = (r"(set|change|make|edit|عدّل|عدل|غير|غيّر|خل|خلّ).{0,45}"
               r"(price|total|vat|amount|سعر|مجموع|ضريب|مبلغ)")
_EDIT_TO = r"\bto\s*\d|الى\s*\d|إلى\s*\d|صفر|مجان|\bfree\b|\bzero\b"
_MAKE_FREE = r"(\b(make|set)\b|خل|خلّ|خلي|اجعل|سو|سوي).{0,30}(مجان|\bfree\b|\bzero\b|بصفر|ب ?0\b)"
_RAW_WRITE = r"\b(update|insert|alter|replace|create)\b.{0,40}\b(set|into|table|index)\b"
_OUT_OF_SCOPE = r"weather|طقس|قصيدة|poem|joke|نكتة|football|كورة|recipe|وصفة"
_ESCALATE = r"refund|استرجاع|استرداد|ضاع|ضايع|ما انحفظ|مفقود|\blost\b|missing order|chargeback"
_WRONG = r"wrong|غلط|مو صح|مب صح|مو مضبوط|incorrect|not (correct|right)|مخطئ"
_DOC = r"invoice|فاتورة|الفاتورة|ضريب|vat|\bord_[a-z0-9_]+"
_REPAIR = (r"\bfix\b|repair|sort (it|this) out|صلح|اصلح|أصلح|إصلاح|رجّع|رجع.{0,8}صح|\bscan\b|"
           r"check .{0,30}(error|problem|issue)|errors?\b|problems?\b|issues?\b|مشكل|أخطاء|اخطاء|افحص|فحص|تطابق|غير مضبوط|مو مضبوط")
_GROUPING = r"\bby\b|\bper\b|حسب|بحسب|break ?down|لكل"
_DATA_QUESTION = r"كم|how many|average|avg|متوسط|عدد|\bper\b|\bby\b|حسب|break ?down|show me|list|اعرض"


def classify(text: str) -> dict:
    """Return {"action": one of ACTIONS, "skill": name|None, "why": short reason}."""
    t = (text or "").strip()
    low = t.lower()

    def out(action, why, skill=None, hard=False):
        return {"action": action, "skill": skill, "why": why, "hard": hard}

    if re.search(_INJECTION, low):
        return out(REFUSE, "prompt-injection attempt", hard=True)
    if re.search(_SECRETS, low):
        return out(REFUSE, "asks for secrets (PINs, passwords, tokens)", hard=True)
    if re.search(_DESTRUCTIVE, low) or re.search(_RAW_WRITE, low):
        return out(REFUSE, "destructive request: the agent never deletes data", hard=True)
    if (re.search(_EDIT_VALUE, low) and re.search(_EDIT_TO, low)) or re.search(_MAKE_FREE, low):
        return out(REFUSE, "asks to overwrite money or prices with a value", hard=True)
    if re.search(_OUT_OF_SCOPE, low):
        return out(REFUSE, "outside the restaurant's data", hard=True)
    if re.search(_ESCALATE, low) or (re.search(_WRONG, low) and re.search(_DOC, low)):
        return out(ESCALATE, "money / invoice / lost order: a human acts, the agent hands over evidence")
    if re.search(_REPAIR, low):
        return out(REPAIR, "investigate and repair derived data")
    if re.search(_GROUPING, low):
        return out(BUILD, "a breakdown no built-in skill gives: write maker + checker queries")
    sk = skills.match(t)
    if sk:
        return out(ANSWER, f"skill {sk}", sk)
    if re.search(_DATA_QUESTION, low):
        return out(BUILD, "data question with no skill: write maker + checker queries")
    return out(REFUSE, "not understood / outside scope")
