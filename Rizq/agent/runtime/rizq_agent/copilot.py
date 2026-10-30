"""Rizq Agent: an AI agent with two sides.

  BRAIN  - any LLM: Claude API (online), any OpenAI-compatible server (Ollama / LM Studio = local & offline,
           or Groq / OpenRouter online), or the built-in rules brain when no model is available.
  HANDS  - whitelisted tools that act on the REAL Rizq POS database: scan, read orders, read-only SQL,
           sales summary, plan fixes, apply fixes (human must type y), escalate, save a fixed copy.

The LLM decides which tools to call; the tools enforce the safety rules, not the prompt:
  - the original DB file is opened read-only; all work happens on a copy
  - SQL tool is SELECT-only (enforced with PRAGMA query_only)
  - writes only through apply_fixes -> derived data only, and only after the human confirms
  - money on an issued tax invoice is never edited (ZATCA credit/debit note -> escalate)
  - bounded: max tool steps per question

Usage:
  python3 -m rizq_agent chat [db.sqlite] [--llm auto|claude|ollama|openai|rules] [--model M] [--ask "question"]
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import urllib.request
from collections import Counter
from dataclasses import asdict

from . import rizq, skills, triage
from .llm import _load_key

MAX_STEPS = 10

SYSTEM = """You are Rizq Agent, the operations agent of Rizq, a Saudi restaurant POS (VAT 15%, ZATCA receipts).
You can act on the real POS database ONLY through your tools. Rules:
- Never claim something about the data without checking it with a tool first. Cite order ids and exact numbers.
- To find problems call scan_pos. To fix: plan_fixes, then apply_fixes (the human must confirm), then scan_pos again to verify.
- Only derived data can be fixed automatically (line totals, stock movements, customer totals). Money on an issued invoice
  (VAT, charged total), lost orders, duplicates and unknown payment methods must be escalated with escalate, never edited.
- For owner questions (sales, VAT, inventory value, profit, zakat...) call list_skills then run_skill. A skill only answers when its
  independent checker agrees; if status is checker_disagrees, do NOT give a number, escalate. If status is needs_input, ask the
  owner exactly those questions. For a capability no skill covers, write TWO differently written SELECTs and call build_skill.
- For questions about parked roadmap items or known Rizq problems, call list_features or list_known_problems first. You may build
  only verified read-only report skills; never claim that you wrote or enabled production POS code.
- Zakat is an ESTIMATE: always show the assumptions returned by the skill and say to confirm with ZATCA or an accountant.
- If you are not sure, escalate. Never invent tools, ids or numbers.
- Reply in the user's language. If the user writes Arabic, reply in Gulf Arabic and keep technical terms in English. Be short."""

TOOLS = [
    {"name": "scan_pos", "description": "Check every business rule on the POS database (totals, VAT, stock, customers, duplicates, payment methods, timestamps). Returns findings with handling auto|escalate.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_order", "description": "Read one order with its items.",
     "input_schema": {"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]}},
    {"name": "run_sql", "description": "Run ONE read-only SELECT on the POS database (tables: orders, order_items, products, customers, stock_transactions, payment_methods, discount_codes, users, categories). Max 50 rows.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "sales_summary", "description": "Sales, VAT, discounts and payment-method breakdown per day for the last N days of data.",
     "input_schema": {"type": "object", "properties": {"days": {"type": "integer", "default": 7}}}},
    {"name": "plan_fixes", "description": "Dry-run: list the exact SQL the agent would run to repair derived data. Changes nothing.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "apply_fixes", "description": "Apply the planned derived-data repairs to the working copy. The human is asked to confirm; returns verification.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "escalate", "description": "Hand a problem to the restaurant owner with evidence and the action they must take.",
     "input_schema": {"type": "object", "properties": {"entity": {"type": "string"}, "kind": {"type": "string"}, "note": {"type": "string"}},
                      "required": ["entity", "note"]}},
    {"name": "list_skills", "description": "List the skills the agent already has (built-in and learned).",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "run_skill", "description": "Run a built-in skill (sales_period, vat_collected, top_products, inventory_value, profit_estimate, zakat_estimate) or a learned one. Maker + independent checker; read-only.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "params": {"type": "object"}}, "required": ["name"]}},
    {"name": "build_skill", "description": "Create a NEW read-only skill: `sql` and an independently written `check_sql` must return identical rows. Installed only after the human confirms.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "description": {"type": "string"},
                                                       "sql": {"type": "string"}, "check_sql": {"type": "string"}},
                      "required": ["name", "description", "sql", "check_sql"]}},
    {"name": "list_features", "description": "Read the Rizq feature backlog. Show which features are parked and which read-only reports the agent may build.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "list_known_problems", "description": "Read the documented Rizq problem catalog and its reproduction/fix status.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "save_fixed_copy", "description": "Save the repaired working copy as a NEW file next to the results (the original is never overwritten).",
     "input_schema": {"type": "object", "properties": {}}},
]
TOOL_NAMES = {t["name"] for t in TOOLS}


class PosTools:
    """The agent's hands. Every write path is here, and every rule is enforced here (not in the prompt)."""

    def __init__(self, db_path, out_dir: str, confirm=None):
        self.src = db_path
        self.db = rizq.open_copy(db_path)
        self.out_dir = out_dir
        self.confirm = confirm or (lambda q: input(q).strip().lower() in ("y", "yes", "ن", "نعم"))
        self.findings: list | None = None
        self.escalations: list[dict] = []
        self.log: list[dict] = []
        self.outputs: list[tuple] = []

    def call(self, name: str, args: dict) -> dict:
        if name not in TOOL_NAMES:
            out = {"error": f"tool '{name}' does not exist"}
        else:
            try:
                out = getattr(self, f"t_{name}")(**(args or {}))
            except TypeError as e:
                out = {"error": f"bad arguments: {e}"}
            except sqlite3.Error as e:
                out = {"error": f"database refused: {e}"}
        self.log.append({"tool": name, "args": args, "ok": "error" not in out})
        self.outputs.append((name, args or {}, out))
        return out

    def t_scan_pos(self) -> dict:
        self.findings = rizq.scan(self.db)
        kinds = Counter(f.kind for f in self.findings)
        return {"total_findings": len(self.findings), "by_kind": dict(kinds),
                "auto_fixable": sum(f.handling == "auto" for f in self.findings),
                "must_escalate": sum(f.handling == "escalate" for f in self.findings),
                "findings": [{"kind": f.kind, "entity": f.entity, "handling": f.handling, "detail": f.detail,
                              "action_en": f.human_action, "action_ar": f.human_action_ar} for f in self.findings[:15]]}

    def t_get_order(self, order_id: str) -> dict:
        o = self.db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        items = [dict(r) for r in self.db.execute(
            "SELECT oi.*, COALESCE(oi.name, p.name_ar) AS product FROM order_items oi LEFT JOIN products p ON p.id=oi.product_id WHERE order_id=?",
            (order_id,))]
        if o is None and not items:
            return {"error": f"order {order_id} not found"}
        return {"order": dict(o) if o else None, "items": items, "order_row_missing": o is None}

    def t_run_sql(self, query: str) -> dict:
        q = query.strip().rstrip(";")
        if ";" in q or not re.match(r"(?is)^\s*(select|with)\b", q):
            return {"error": "only ONE read-only SELECT is allowed"}
        if re.search(r"(?i)\b(users|pin|password|passcode|token|secret)\b", q):
            return {"error": "user credentials and secrets are not available to the agent"}
        self.db.execute("PRAGMA query_only=ON")
        try:
            cur = self.db.execute(q)
            rows = cur.fetchmany(50)
            return {"columns": [d[0] for d in cur.description or []], "rows": [list(r) for r in rows], "truncated_at": 50}
        finally:
            self.db.execute("PRAGMA query_only=OFF")

    def t_sales_summary(self, days: int = 7) -> dict:
        days = max(1, min(int(days), 60))
        rows = self.db.execute("""
            SELECT date(created_at) d, COUNT(*) orders, ROUND(SUM(final_total),2) sales, ROUND(SUM(vat_amount),2) vat,
                   ROUND(SUM(discount_amount),2) discounts
            FROM orders WHERE status='completed' AND date(created_at) > (SELECT date(MAX(created_at), ?) FROM orders)
            GROUP BY d ORDER BY d""", (f"-{days} days",)).fetchall()
        pay = self.db.execute("""SELECT payment_method, COUNT(*) n, ROUND(SUM(final_total),2) total FROM orders
            WHERE status='completed' AND date(created_at) > (SELECT date(MAX(created_at), ?) FROM orders) GROUP BY 1""",
                              (f"-{days} days",)).fetchall()
        return {"per_day": [dict(r) for r in rows], "by_payment_method": [dict(r) for r in pay]}

    def t_plan_fixes(self) -> dict:
        if self.findings is None:
            self.t_scan_pos()
        plan = rizq.repair(self.db, self.findings, dry_run=True)
        return {"statements": len(plan), "plan": plan[:20],
                "not_fixable_automatically": [f"{f.kind}:{f.entity}" for f in self.findings if f.handling == "escalate"]}

    def t_apply_fixes(self) -> dict:
        if self.findings is None:
            self.t_scan_pos()
        auto = [f for f in self.findings if f.handling == "auto"]
        if not auto:
            return {"applied": 0, "note": "nothing can be fixed automatically"}
        if not self.confirm(f"\n  [Rizq Agent] Apply {len(auto)} derived-data fix(es) to the working copy? (original untouched) [y/N]: "):
            return {"applied": 0, "note": "the human declined - nothing was changed"}
        before = {(f.kind, f.entity) for f in self.findings}
        audit = rizq.repair(self.db, self.findings)
        after = rizq.scan(self.db)
        self.findings = after
        return {"applied": len(audit), "verification": {
            "auto_findings_left": sum(f.handling == "auto" for f in after),
            "new_findings": [f"{f.kind}:{f.entity}" for f in after if (f.kind, f.entity) not in before],
            "still_escalated": [f"{f.kind}:{f.entity}" for f in after if f.handling == "escalate"]}}

    def t_escalate(self, entity: str, note: str, kind: str = "") -> dict:
        e = {"entity": entity, "kind": kind, "note": note}
        self.escalations.append(e)
        os.makedirs(self.out_dir, exist_ok=True)
        with open(os.path.join(self.out_dir, "escalations.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")
        return {"escalated": e, "file": "results/escalations.jsonl"}

    @property
    def learned_path(self) -> str:
        return os.path.join(self.out_dir, "skills_learned.json")

    def t_list_skills(self) -> dict:
        return {"built_in": {n: k.title_ar for n, k in skills.SKILLS.items()},
                "learned": {n: v["description"] for n, v in skills.load_learned(self.learned_path).items()}}

    def t_run_skill(self, name: str, params: dict | None = None) -> dict:
        if name in skills.load_learned(self.learned_path):
            return skills.run_learned(self.db, self.learned_path, name)
        return skills.run(self.db, name, params or {})

    def t_build_skill(self, name: str, description: str, sql: str, check_sql: str) -> dict:
        return skills.build_learned_skill(self.db, self.learned_path, name, description, sql, check_sql, self.confirm)

    def t_list_features(self) -> dict:
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "features", "parked.json")
        with open(path, encoding="utf-8") as fh:
            result = json.load(fh)
        with open(os.path.join(os.path.dirname(path), "capabilities.json"), encoding="utf-8") as fh:
            result["agent_capabilities"] = json.load(fh)
        return result

    def t_list_known_problems(self) -> dict:
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "problems", "catalog.json")
        with open(path, encoding="utf-8") as fh:
            result = json.load(fh)
        with open(os.path.join(os.path.dirname(path), "scanner.json"), encoding="utf-8") as fh:
            result["scanner_findings"] = json.load(fh)["problems"]
        with open(os.path.join(os.path.dirname(path), "challenges.json"), encoding="utf-8") as fh:
            result["major_challenges"] = json.load(fh)["challenges"]
        return result

    def t_save_fixed_copy(self) -> dict:
        os.makedirs(self.out_dir, exist_ok=True)
        base = "pos" if not isinstance(self.src, str) else os.path.splitext(os.path.basename(self.src))[0]
        path = os.path.join(self.out_dir, f"{base}.copilot_fixed.sqlite")
        rizq.save_db(self.db, path)
        return {"saved": path}


# ----------------------------------------------------------------------------------------------- brains
class ClaudeBrain:
    name = "claude"

    def __init__(self, model: str | None = None):
        self.model = model or os.environ.get("RIZQ_CLAUDE_MODEL", "claude-sonnet-5-5")
        self.key = _load_key()
        self.msgs: list = []

    def user(self, text: str) -> None:
        self.msgs.append({"role": "user", "content": text})

    def step(self) -> dict:
        body = {"model": self.model, "max_tokens": 1500, "system": SYSTEM, "tools": TOOLS, "messages": self.msgs}
        base = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
        req = urllib.request.Request(base.rstrip("/") + "/v1/messages", data=json.dumps(body).encode(),
                                     headers={"x-api-key": self.key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            resp = json.loads(r.read())
        content = resp.get("content", [])
        self.msgs.append({"role": "assistant", "content": content})
        return {"text": "".join(b.get("text", "") for b in content if b.get("type") == "text"),
                "calls": [{"id": b["id"], "name": b["name"], "input": b.get("input", {})} for b in content if b.get("type") == "tool_use"]}

    def results(self, pairs: list[tuple[dict, dict]]) -> None:
        self.msgs.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": c["id"], "content": json.dumps(out, ensure_ascii=False, default=str)[:12000]}
            for c, out in pairs]})


class OpenAICompatBrain:
    """Ollama / LM Studio (local, offline) or any OpenAI-compatible endpoint (Groq, OpenRouter...)."""
    name = "openai-compatible"

    def __init__(self, base_url: str, model: str, key: str = "local"):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model, self.key = model, key
        self.msgs: list = [{"role": "system", "content": SYSTEM}]
        self.tools = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                         "parameters": t["input_schema"]}} for t in TOOLS]

    def user(self, text: str) -> None:
        self.msgs.append({"role": "user", "content": text})

    def step(self) -> dict:
        body = {"model": self.model, "messages": self.msgs, "tools": self.tools, "temperature": 0}
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(),
                                     headers={"Authorization": f"Bearer {self.key}", "content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as r:
            msg = json.loads(r.read())["choices"][0]["message"]
        self.msgs.append({k: v for k, v in msg.items() if k in ("role", "content", "tool_calls")})
        calls = []
        for c in msg.get("tool_calls") or []:
            args = c["function"].get("arguments") or "{}"
            try:
                args = json.loads(args) if isinstance(args, str) else args
            except json.JSONDecodeError:
                args = {}
            calls.append({"id": c.get("id", c["function"]["name"]), "name": c["function"]["name"], "input": args})
        return {"text": msg.get("content") or "", "calls": calls}

    def results(self, pairs) -> None:
        for c, out in pairs:
            self.msgs.append({"role": "tool", "tool_call_id": c["id"], "content": json.dumps(out, ensure_ascii=False, default=str)[:12000]})


def refusal_text(verdict: dict, question: str) -> str:
    ar = bool(re.search(r"[؀-ۿ]", question or ""))
    why_ar = {"prompt-injection attempt": "محاولة لتجاوز قواعدي",
              "asks for secrets (PINs, passwords, tokens)": "الطلب يطلب أسرار (أرقام سرية أو كلمات مرور أو توكن)",
              "destructive request: the agent never deletes data": "حذف بيانات، وأنا ما أحذف أي بيانات أبدًا",
              "asks to overwrite money or prices with a value": "تعديل مبالغ أو أسعار مباشرة، وهذا قرار لصاحب المطعم من داخل رزق",
              "outside the restaurant's data": "خارج بيانات المطعم",
              "not understood / outside scope": "ما فهمت الطلب أو هو خارج نطاقي"}
    if ar:
        return "ما أقدر أنفّذ هذا الطلب: " + why_ar.get(verdict["why"], verdict["why"])
    return "I can't do that: " + verdict["why"]


class RulesBrain:
    """No model available: a deterministic brain that maps intents (EN/AR) to the same tools."""
    name = "rules (offline, no model)"

    def __init__(self):
        self.queue: list = []
        self.last_user = ""
        self.done = False

    def user(self, text: str) -> None:
        self.last_user, self.done, t = text, False, text.lower()
        m = re.search(r"\bord_[a-z0-9_]+", t)
        self.verdict = triage.classify(text)
        self.outs = []
        if self.verdict["action"] in (triage.REFUSE, triage.BUILD):
            self.queue = []                      # no tool runs at all: refuse, or explain that a model is needed
            return
        if self.verdict["action"] == triage.ESCALATE:
            self.queue = [{"id": "e0", "name": "scan_pos", "input": {}},
                          {"id": "e1", "name": "escalate", "input": {"entity": m.group(0) if m else "owner_request", "kind": "owner_request", "note": text}}]
            return
        calls = []
        fixing = re.search(r"fix|repair|صلح|اصلح|إصلاح|عدل", t)
        sk = None if fixing else skills.match(text)
        if m:
            calls.append(("get_order", {"order_id": m.group(0)}))
        if sk:
            calls.append(("run_skill", {"name": sk, "params": skills.extract_params(text)}))
        elif re.search(r"sales|مبيع|دخل|revenue", t):
            calls.append(("sales_summary", {"days": 7}))
        if fixing:
            calls += [("scan_pos", {}), ("plan_fixes", {}), ("apply_fixes", {}), ("scan_pos", {})]
        elif re.search(r"scan|check|problem|error|wrong|افحص|فحص|مشاكل|أخطاء|اخطاء|غلط|مشكلة", t) or not calls:
            calls.append(("scan_pos", {}))
        if re.search(r"save|احفظ|حفظ", t):
            calls.append(("save_fixed_copy", {}))
        self.queue = [{"id": f"r{i}", "name": n, "input": a} for i, (n, a) in enumerate(calls)]
        self.outs: list = []

    def step(self) -> dict:
        if self.queue:
            return {"text": "", "calls": [self.queue.pop(0)]}
        return {"text": self._summarize(), "calls": []}

    def results(self, pairs) -> None:
        self.outs += pairs

    def _summarize(self) -> str:
        ar = bool(re.search(r"[؀-ۿ]", self.last_user))
        v = getattr(self, "verdict", None)
        if v and v["action"] == triage.REFUSE:
            return refusal_text(v, self.last_user)
        if v and v["action"] == triage.BUILD:
            return ("هذا السؤال ما له مهارة جاهزة. أحتاج نموذج ذكاء اصطناعي يكتب له استعلامين ويتحقق منهما (شغّل مع --llm claude وبمفتاح API)."
                    if ar else "No built-in skill covers this. A model must write a maker and a checker query (run with --llm claude and an API key).")
        lines = []
        for c, out in self.outs:
            if "error" in out:
                lines.append(f"- {c['name']}: {out['error']}")
            elif c["name"] == "scan_pos":
                lines.append((f"- الفحص: {out['total_findings']} ملاحظة ({out['auto_fixable']} تنصلح تلقائي، {out['must_escalate']} لازم لها قرارك)"
                              if ar else f"- scan: {out['total_findings']} findings ({out['auto_fixable']} auto-fixable, {out['must_escalate']} need you)"))
                for f in out["findings"][:6]:
                    lines.append(f"  • {f['kind']} {f['entity']}: {f['action_ar'] if ar else f['action_en']}")
            elif c["name"] == "run_skill":
                lines.append(_skill_line_impl(out, ar))
            elif c["name"] == "apply_fixes":
                lines.append(f"- apply_fixes: {json.dumps(out, ensure_ascii=False)[:300]}")
            elif c["name"] == "get_order":
                o = out["order"] or {}
                lines.append(f"- {c['input']['order_id']}: total={o.get('total')} vat={o.get('vat_amount')} final={o.get('final_total')} "
                             f"items={len(out['items'])} missing_row={out['order_row_missing']}")
            elif c["name"] == "sales_summary":
                for d in out["per_day"][-3:]:
                    lines.append(f"  {d['d']}: {d['orders']} orders, {d['sales']} SAR (VAT {d['vat']})")
            else:
                lines.append(f"- {c['name']}: {json.dumps(out, ensure_ascii=False)[:200]}")
        return "\n".join(lines)


def _skill_line_impl(out: dict, ar: bool) -> str:
    st = out.get("status")
    if st == "needs_input":
        qs = "؛ ".join(q["question_ar"] for q in out["questions"]) if ar else "; ".join(q["param"] for q in out["questions"])
        return ("- أحتاج منك: " if ar else "- I need from you: ") + qs
    if st == "checker_disagrees":
        return ("- ما أقدر أعطيك رقم: المدقّق ما وافق، الأرقام المخزنة ما تتطابق. صعّدتها لك: " if ar
                else "- No answer: the checker disagrees, stored numbers do not reconcile. Escalated: ") + json.dumps(
            {"maker": out.get("maker"), "checker": out.get("checker")}, ensure_ascii=False)[:300]
    if st == "ok":
        notes = "\n".join("  * " + a for a in out.get("assumptions", []))
        return f"- {out.get('title_ar') or out['skill']}: {json.dumps(out['value'], ensure_ascii=False)}" + ("\n" + notes if notes else "")
    return f"- {json.dumps(out, ensure_ascii=False)[:300]}"


LOCAL_PREFERENCE = ("qwen3.5:0.8b", "qwen3:8b", "qwen3.5:9b", "qwen2.5:7b", "qwen3.5:4b", "llama3.1:8b", "qwen3.5:2b")


def local_model(base: str = "http://localhost:11434", installed: list | None = None) -> str | None:
    """Prefer the light local model already installed; RIZQ_OLLAMA_MODEL overrides."""
    if os.environ.get("RIZQ_OLLAMA_MODEL"):
        return os.environ["RIZQ_OLLAMA_MODEL"]
    if installed is None:
        try:
            with urllib.request.urlopen(base + "/api/tags", timeout=2) as r:
                installed = [m["name"] for m in json.loads(r.read()).get("models", [])]
        except Exception:
            return None
    for name in LOCAL_PREFERENCE:
        if name in installed:
            return name
    return None


def pick_brain(kind: str = "auto", model: str | None = None):
    def ollama_up(url="http://localhost:11434"):
        try:
            urllib.request.urlopen(url + "/api/tags", timeout=2)
            return True
        except Exception:
            return False
    if kind == "claude" or (kind == "auto" and _load_key()):
        return ClaudeBrain(model)
    if kind == "ollama" or (kind == "auto" and ollama_up()):
        chosen = model or local_model()
        if chosen:
            return OpenAICompatBrain("http://localhost:11434/v1", chosen, "ollama")
    if kind == "auto":
        if os.environ.get("RIZQ_LLM_BASE_URL") and (os.environ.get("RIZQ_LLM_MODEL") or model):
            kind = "compatible"
        elif os.environ.get("OPENAI_API_KEY"):
            kind = "openai"
    if kind in ("openai", "compatible"):
        default_url = "https://api.openai.com/v1" if kind == "openai" else "http://localhost:11434/v1"
        base_url = os.environ.get("RIZQ_LLM_BASE_URL") or os.environ.get("OPENAI_BASE_URL", default_url)
        api_key = os.environ.get("RIZQ_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
        default_model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini") if kind == "openai" else os.environ.get("RIZQ_LLM_MODEL", "")
        if kind == "compatible" and not (model or default_model):
            raise ValueError("Set RIZQ_LLM_MODEL in agent/runtime/.env for the compatible LLM endpoint")
        return OpenAICompatBrain(base_url, model or default_model, api_key)
    return RulesBrain()


def ask(brain, tools: PosTools, question: str, on_event=print) -> str:
    """One user turn: the brain thinks, calls tools, sees results, repeats (bounded), then answers.
    First wall: a hard refusal from triage (delete, secrets, injection, overwrite money, out of scope) is answered
    here without calling the model or any tool. Second wall: the tools themselves (read-only SQL, human `y`, no money edits)."""
    verdict = triage.classify(question)
    if verdict["action"] == triage.REFUSE and verdict.get("hard"):
        on_event(f"  ⛔ refused before the model: {verdict['why']}")
        return refusal_text(verdict, question)
    brain.user(question)
    for _ in range(MAX_STEPS):
        r = brain.step()
        if not r["calls"]:
            return r["text"]
        pairs = []
        for c in r["calls"]:
            on_event(f"  ⚙ {c['name']}({json.dumps(c['input'], ensure_ascii=False)})")
            pairs.append((c, tools.call(c["name"], c["input"])))
        brain.results(pairs)
    return "(stopped: step budget reached - escalating instead of looping)"
