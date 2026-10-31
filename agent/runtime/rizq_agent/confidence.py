"""Score how much an answer can be trusted, and refuse to approve a weak one.

The rule Mohammad set: if the solution is not at least 95%, do not approve it.

Where the number comes from matters. Asking a model "how confident are you?" produces a
number that sounds precise and means nothing -- language models are badly calibrated
about their own correctness, and a wrong answer is usually delivered confidently. So
nothing here asks the model anything. Every point is earned from a signal that can be
checked independently:

  * did a second, independently written query agree with the first (the strongest signal
    this system has, and the reason skills exist at all)
  * was the question matched to a capability, or did it fall through to a default
  * were the parameters the owner's, or ours
  * is there enough data behind the figure to mean anything
  * for a model answer: did it ground itself in a verified tool, or narrate

An answer below the bar is not shown as fact. It escalates: first to a larger local model
if one is installed, and if that still does not clear the bar, to a human -- with the
reason written down.
"""
from __future__ import annotations

import os

APPROVAL_THRESHOLD = float(os.environ.get("RIZQ_MIN_CONFIDENCE", "95"))

# Larger models to retry with, best first. Only used when the cheap path is not
# confident enough, because they cost seconds per question.
ESCALATION_MODELS = ("qwen3.6:27b", "gpt-oss:20b", "qwen3:30b-a3b-instruct-2507-q4_K_M",
                     "qwen3.5:9b", "qwen3:8b")

MIN_ROWS_FOR_A_TREND = 20          # below this, a period figure is thin evidence

# Skills whose answer is an aggregate over a time window, so the number of underlying
# orders is the sample size. Everything else returns a count that IS the answer.
PERIOD_SKILLS = {"sales_period", "profit_estimate", "top_products", "vat_collected"}


class Verdict:
    """A score, whether it may be shown as fact, and why."""

    def __init__(self, score: float, reasons: list, penalties: list):
        self.score = max(0.0, min(100.0, round(score, 1)))
        self.reasons = reasons
        self.penalties = penalties

    @property
    def approved(self) -> bool:
        return self.score >= APPROVAL_THRESHOLD

    def as_dict(self) -> dict:
        return {"score": self.score, "approved": self.approved,
                "threshold": APPROVAL_THRESHOLD,
                "supports": self.reasons, "doubts": self.penalties}

    def note_ar(self) -> str:
        """One line for the owner. Silent when the answer is solid."""
        if self.approved:
            return ""
        why = "؛ ".join(self.penalties) or "الأدلة غير كافية"
        return f"⚠️ ثقتي في هذه الإجابة {self.score:.0f}% فقط — {why}."


def score_skill(result: dict, matched_exactly: bool = True,
                owner_params: dict | None = None) -> Verdict:
    """Score a built-in skill result. `result` is what skills.run returned."""
    status = result.get("status")
    reasons: list = []
    penalties: list = []

    if status == "checker_disagrees":
        return Verdict(0, [], ["المدقّق المستقل ما وافق على الرقم"])
    if status == "needs_input":
        return Verdict(0, [], ["ناقص معلومات لازم تعطيني إياها"])
    if status != "ok":
        return Verdict(0, [], ["المهارة ما رجّعت نتيجة"])

    # The independent checker agreed. This is the whole point of the design.
    score = 90.0
    reasons.append("مدقّق مستقل وصل لنفس الرقم")

    if matched_exactly:
        score += 7
        reasons.append("السؤال مطابق لقدرة معروفة")
    else:
        penalties.append("ربطت السؤال بأقرب قدرة، مو مطابقة تمامًا")

    params = result.get("params") or {}
    owner_params = owner_params or {}
    defaulted = [k for k in params if k not in owner_params]
    if params and defaulted and not owner_params:
        score -= 2
        penalties.append(f"استخدمت قيمًا افتراضية ({'، '.join(defaulted)})")
    else:
        score += 3
        reasons.append("استخدمت ما حدّدته أنت")

    value = result.get("value")
    if isinstance(value, dict):
        # Sample size only means something for a figure aggregated over a period. For
        # low_stock or inventory_value the count IS the answer -- "4 items are low" is
        # not weak evidence, it is the finding. Read `orders` explicitly: `a or b`
        # treats a legitimate 0 as missing, which hid the empty-period case entirely.
        rows = value.get("orders") if result.get("skill") in PERIOD_SKILLS else None
        if isinstance(rows, int):
            if rows == 0:
                score -= 60
                penalties.append("ما فيه أي طلبات في هذه الفترة")
            elif rows < MIN_ROWS_FOR_A_TREND:
                score -= 6
                penalties.append(f"البيانات قليلة ({rows} طلب فقط)")

    if result.get("assumptions"):
        # An estimate that states its assumptions is honest, but it is still an estimate.
        if result.get("skill") == "zakat_estimate":
            score -= 10
            penalties.append("تقدير زكاة مبني على افتراضات، مو إقرار رسمي")

    return Verdict(score, reasons, penalties)


def score_model_answer(answer: str, tool_log: list) -> Verdict:
    """Score an answer the model produced, from how it got there -- not from its tone."""
    reasons: list = []
    penalties: list = []
    names = [t.get("tool") for t in (tool_log or [])]
    ok_calls = [t for t in (tool_log or []) if t.get("ok")]
    failed = [t for t in (tool_log or []) if not t.get("ok")]

    if not names:
        return Verdict(30, [], ["النموذج جاوب بدون ما يفتح البيانات"])

    score = 55.0
    reasons.append(f"رجع للبيانات عبر {len(ok_calls)} أداة")

    verified = {"run_skill", "sales_summary", "scan_pos", "get_order"}
    if any(n in verified for n in names):
        score += 25
        reasons.append("استخدم مهارة أو قراءة متحقَّقة")
    if "run_sql" in names and not any(n in verified for n in names):
        score -= 10
        penalties.append("اعتمد على استعلام حر بدل مهارة متحقَّقة")
    if failed:
        score -= 12 * len(failed)
        penalties.append(f"{len(failed)} استدعاء فشل قبل الإجابة")
    if not any(ch.isdigit() for ch in (answer or "")):
        score -= 15
        penalties.append("الإجابة بدون أرقام من البيانات")
    if len(names) > 8:
        score -= 8
        penalties.append("احتاج محاولات كثيرة للوصول للإجابة")

    return Verdict(score, reasons, penalties)


def bigger_model(current: str | None, installed: list) -> str | None:
    """The best installed model above `current`. Never a smaller one: when the biggest
    model already failed, the next step is a human, not a weaker model."""
    ladder = ESCALATION_MODELS
    if current in ladder:
        ladder = ladder[:ladder.index(current)]
    for name in ladder:
        if name in installed and name != current:
            return name
    return None


def installed_models(base: str = "http://localhost:11434") -> list:
    import json
    import urllib.request
    try:
        with urllib.request.urlopen(base + "/api/tags", timeout=3) as r:
            return [m["name"] for m in json.loads(r.read()).get("models", [])]
    except Exception:
        return []
