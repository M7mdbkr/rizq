"""Run the patched POS bundle's own payment and Telegram code under Node.

The React source is unavailable, so these tests cut the exact functions out of the
compiled bundle and run them against a real SQLite database (node:sqlite) and a fake
Telegram API. What is tested is the code the cashier actually runs.
"""
import json
import os
import pathlib
import shutil
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[3]
BUNDLE = ROOT / "rizq-pos/assets/index-c3uFjRpt.js"

HARNESS = r"""
const fs = require('fs');
const {DatabaseSync} = require('node:sqlite');
const src = fs.readFileSync(process.argv[process.argv.length - 1], 'utf8');
const cut = (a, b) => { const i = src.indexOf(a); const j = src.indexOf(b, i); if (i < 0 || j < 0) throw new Error('missing ' + a); return src.slice(i, j); };
const out = {};

// ---------------------------------------------------------------- payment methods
const ueSrc = cut('function ue(e){', 'function de(e){');
const store = {};
const localStorage = {getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: k => { delete store[k]; }};
const ue = new Function('localStorage', ueSrc + '; return ue;')(localStorage);
const wrap = db => ({
  exec(sql) { const rows = db.prepare(sql).all(); return rows.length ? [{values: rows.map(r => Object.values(r))}] : []; },
  run(sql, params) { db.prepare(sql).run(...(params || [])); },
});
const schema = "CREATE TABLE payment_methods (id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT DEFAULT 'delivery', active INTEGER DEFAULT 1, sort INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)";
const list = db => db.prepare('SELECT id, name, kind, sort FROM payment_methods ORDER BY sort').all().map(r => ({...r}));

const fresh = new DatabaseSync(':memory:'); fresh.exec(schema);
ue(wrap(fresh)); out.fresh = list(fresh);

const old = new DatabaseSync(':memory:'); old.exec(schema);
[['cash','نقدًا','cash',0],['card','شبكة (مدى)','card',1],['akita','كيتا','delivery',2],['merzool','مرسول السريع','delivery',3],['ninja','نينجا','delivery',4],['pm_x','عميل شركات','cash',5]]
  .forEach(r => old.prepare('INSERT INTO payment_methods (id,name,kind,sort) VALUES (?,?,?,?)').run(...r));
ue(wrap(old)); out.upgraded = list(old);
old.prepare("DELETE FROM payment_methods WHERE id='visa'").run();
ue(wrap(old)); out.afterDelete = list(old).map(r => r.id);

// ---------------------------------------------------------------- telegram
const tgSrc = cut('var er=(e,t)=>`https://api.telegram.org', 'function _r(e,t=1)');
let now = new Date(2026, 9, 31, 10, 0, 0).getTime();
class FakeDate extends Date { constructor(...a) { a.length ? super(...a) : super(now); } static now() { return now; } }
const calls = []; let replies = [];
const fetch = async (url, opts) => { const body = JSON.parse(opts.body); calls.push({method: url.split('/').pop(), body}); const r = replies.length ? replies.shift() : {ok: true, result: []}; if (r instanceof Error) throw r; return {json: async () => r}; };
const settings = {name: 'برجر & كو', telegramToken: '1:x', telegramChatId: '42', telegramAutoDaily: true};
let lnThrows = false;
const stubs = {
  Ne: e => Object.assign(settings, e), R: () => settings, Ln: t => { if (lnThrows) throw new Error('boom'); return {reply: 'رد: ' + t}; },
  _t: () => ({sales: 100, net: 87, profit: 40, orders: 3, discount: 0, vat: 13}),
  yt: () => [{payment_method: 'visa', total: 60, count: 2}, {payment_method: 'hungerstation', total: 40, count: 1}],
  bt: (...a) => { out.btArgs = a; return [{name_ar: 'شاورما <دبل>', revenue: 50}]; },
  it: () => [], I: v => v.toFixed(2) + ' ر.س', be: v => v + '%', Ue: id => ({visa: 'فيزا', hungerstation: 'هنقرستيشن'})[id] || id,
  localStorage, fetch, Date: FakeDate, setTimeout: (f, ms) => { out.loopRescheduled = (out.loopRescheduled || 0) + 1; return 1; }, clearTimeout: () => {},
};
const names = Object.keys(stubs);
const tg = new Function(...names, tgSrc + '; return {nr, pr, fr, mr, hr, ir, rr};')(...names.map(n => stubs[n]));

(async () => {
  replies = [{ok: false, error_code: 400, description: "Bad Request: can't parse entities"}, {ok: true}];
  out.fallbackOk = await tg.nr('<b>برجر & كو</b>');
  out.fallbackCalls = calls.splice(0).map(c => ({parse_mode: c.body.parse_mode || null, text: c.body.text}));

  replies = []; await tg.pr(); out.morningCalls = calls.splice(0).length;            // 10:00 -> nothing

  now = new Date(2026, 9, 31, 23, 30, 0).getTime();
  replies = [{ok: false, error_code: 500, description: 'down'}];
  await tg.pr(); out.failedMarked = store['rizq-tg-daily'] || null; calls.splice(0);
  await tg.pr(); out.retryTooSoon = calls.splice(0).length;                           // throttled
  now += 61000; replies = [{ok: true}];
  await tg.pr(); const sent = calls.splice(0); out.reportSent = sent.length; out.reportText = sent[0] && sent[0].body.text;
  out.reportKey = store['rizq-tg-daily'];
  await tg.pr(); out.secondSameDay = calls.splice(0).length;

  replies = [{ok: true, result: [{update_id: 7, message: {text: 'كم المبيعات', chat: {id: 99}}}, {update_id: 8, message: {text: 'كم المبيعات', chat: {id: 42}}}]}, {ok: true}];
  await tg.fr(); const fr = calls.splice(0); out.pollReplies = fr.filter(c => c.method === 'sendMessage').map(c => c.body.chat_id);
  out.offset = store['rizq-tg-offset'];

  lnThrows = true; out.loopRescheduled = 0;
  replies = [{ok: true, result: [{update_id: 9, message: {text: 'x', chat: {id: 42}}}]}];
  tg.mr(); await new Promise(r => globalThis.setTimeout(r, 20)); tg.hr();

  // pairing: a stranger who messages the bot is not bound; the chat that sends the code is
  lnThrows = false; settings.telegramChatId = ''; delete store['rizq-tg-pair']; calls.splice(0);
  replies = [{ok: true, result: {username: 'RizqAiAgent_bot'}}, {ok: true, result: [{update_id: 30, message: {text: 'hi', chat: {id: 5, type: 'private'}}}]}];
  const first = await tg.rr(); out.pairFirst = first; out.strangerBound = settings.telegramChatId;
  replies = [{ok: true, result: {username: 'RizqAiAgent_bot'}},
             {ok: true, result: [{update_id: 31, message: {text: '000000', chat: {id: 5, type: 'private'}}},
                                 {update_id: 32, message: {text: first.code, chat: {id: 77, type: 'private'}}}]}, {ok: true}];
  const second = await tg.rr(); tg.hr();
  out.pairSecond = second; out.boundChat = settings.telegramChatId; out.pairOffset = store['rizq-tg-offset'];
  out.welcomeTo = (calls.find(c => c.method === 'sendMessage') || {body: {}}).body.chat_id;
  console.log(JSON.stringify(out));
})().catch(e => { console.log(JSON.stringify({error: String(e.stack || e)})); });
"""


@unittest.skipUnless(shutil.which("node"), "node is required to run the compiled POS code")
class PosBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        run = subprocess.run([shutil.which("node"), "--no-warnings", "-e", HARNESS, "--", str(BUNDLE)],
                             capture_output=True, text=True, timeout=60, env={**os.environ, "TZ": "America/Los_Angeles"})
        lines = [line for line in run.stdout.splitlines() if line.startswith("{")]
        if not lines:
            raise AssertionError(run.stdout + run.stderr)
        cls.out = json.loads(lines[-1])
        if "error" in cls.out:
            raise AssertionError(cls.out["error"])

    def test_new_database_offers_every_card_and_delivery_app(self):
        rows = {r["id"]: r for r in self.out["fresh"]}
        for method, kind in [("cash", "cash"), ("card", "card"), ("visa", "card"), ("mastercard", "card"),
                             ("hungerstation", "delivery"), ("jahez", "delivery"), ("akita", "delivery"),
                             ("merzool", "delivery"), ("ninja", "delivery"), ("toyou", "delivery")]:
            self.assertEqual(rows[method]["kind"], kind, method)
        self.assertEqual(rows["visa"]["name"], "فيزا")
        self.assertEqual(rows["mastercard"]["name"], "ماستركارد")
        self.assertEqual([r["sort"] for r in self.out["fresh"]], list(range(len(rows))))

    def test_existing_database_is_topped_up_without_touching_owner_choices(self):
        rows = {r["id"]: r for r in self.out["upgraded"]}
        self.assertEqual(rows["merzool"]["name"], "مرسول السريع")      # owner's rename kept
        self.assertIn("pm_x", rows)                                     # owner's own method kept
        self.assertIn("visa", rows)
        self.assertGreater(rows["visa"]["sort"], rows["pm_x"]["sort"])  # new ones after the owner's
        self.assertEqual(len(rows), 6 + 8)

    def test_a_deleted_method_stays_deleted(self):
        self.assertNotIn("visa", self.out["afterDelete"])

    def test_rejected_html_is_resent_as_plain_text(self):
        self.assertTrue(self.out["fallbackOk"])
        first, second = self.out["fallbackCalls"]
        self.assertEqual(first["parse_mode"], "HTML")
        self.assertIsNone(second["parse_mode"])
        self.assertEqual(second["text"], "برجر & كو")

    def test_daily_report_waits_for_closing_and_uses_the_local_date(self):
        self.assertEqual(self.out["morningCalls"], 0)
        self.assertIsNone(self.out["failedMarked"])          # a failed send is not marked done
        self.assertEqual(self.out["retryTooSoon"], 0)        # and is not retried every 4 s
        self.assertEqual(self.out["reportSent"], 1)
        self.assertEqual(self.out["reportKey"], "2026-10-31")  # local day, not the UTC day (Nov 1)
        self.assertEqual(self.out["secondSameDay"], 0)

    def test_daily_report_lists_payment_methods_and_todays_top_sellers(self):
        text = self.out["reportText"]
        self.assertIn("فيزا", text)
        self.assertIn("هنقرستيشن", text)
        self.assertEqual(self.out["btArgs"], [3, True])

    def test_bot_answers_only_the_owner_chat(self):
        self.assertEqual(self.out["pollReplies"], ["42"])
        self.assertEqual(self.out["offset"], "9")

    def test_telegram_pairs_only_the_chat_that_sends_the_code(self):
        first = self.out["pairFirst"]
        self.assertFalse(first["ok"])
        self.assertRegex(first["code"], r"^\d{6}$")
        self.assertIn(first["code"], first["error"])
        self.assertIn("@RizqAiAgent_bot", first["error"])
        self.assertEqual(self.out["strangerBound"], "")         # "hi" from a stranger binds nothing
        self.assertTrue(self.out["pairSecond"]["ok"])
        self.assertEqual(self.out["boundChat"], "77")
        self.assertEqual(self.out["welcomeTo"], "77")
        self.assertEqual(self.out["pairOffset"], "33")          # the code is not answered as a command

    def test_one_failure_does_not_stop_the_bot(self):
        self.assertEqual(self.out["loopRescheduled"], 1)


if __name__ == "__main__":
    unittest.main()
