// Requires: the Rizq build served at http://localhost:4173 and Playwright. Logs in as cashier and places real orders
// through the REAL checkout UI. Usage: node tools/rizq_real_checkouts.js <n_orders> <out.sqlite> <seed>
// Drives Rizq's REAL cashier UI: N checkouts with random items, payment methods, order types, phones and discount codes.
// Then exports the app's own SQLite database. Usage: node checkout.js <n> <out.sqlite> <seed>
const { chromium } = require('playwright');
const fs = require('fs');
let s = +(process.argv[4] || 1); const rnd = () => (s = (s * 16807) % 2147483647) / 2147483647;
const pick = a => a[Math.floor(rnd() * a.length)];
const opt = async f => { try { await f(); } catch (e) { } };
(async () => {
  const N = +(process.argv[2] || 5), out = process.argv[3];
  const b = await chromium.launch(); const p = await b.newPage({ viewport: { width: 1280, height: 800 } });
  await p.goto('http://localhost:4173/', { waitUntil: 'networkidle' }); await p.waitForTimeout(1500);
  await p.getByText('الكاشير', { exact: true }).click(); await p.waitForTimeout(500);
  for (const d of '11112222') { await p.locator('button', { hasText: new RegExp('^' + d + '$') }).first().click(); await p.waitForTimeout(50); }
  await p.waitForTimeout(1200);
  const products = ['كبسة دجاج', 'حمص', 'مندي لحم', 'مشاوي مشكلة', 'فتوش', 'كنافة', 'لقيمات', 'برياني دجاج', 'عصير برتقال طازج', 'مياه معدنية', 'أم علي', 'شيش طاووق'];
  const pays = ['نقدًا', 'نقدًا', 'شبكة (مدى)', 'كيتا', 'مرسول', 'نينجا'];
  const types = ['محلي', 'محلي', 'سفري', 'توصيل'];
  let done = 0;
  for (let i = 0; i < N; i++) {
    const k = 1 + Math.floor(rnd() * 3);
    for (let j = 0; j < k; j++) {
      const name = pick(products), q = 1 + Math.floor(rnd() * 2);
      for (let r = 0; r < q; r++) await p.getByRole('button', { name: new RegExp('^' + name) }).first().click();
    }
    await opt(() => p.getByRole('button', { name: pick(types), exact: true }).click({ timeout: 1500 }));
    if (rnd() < 0.5) {
      await opt(() => p.locator('input[placeholder*="اسم"]').first().fill(pick(['محمد', 'نورة', 'فهد', 'ريم', 'خالد']), { timeout: 1500 }));
      await opt(() => p.locator('input[placeholder*="جوال"]').first().fill('05' + String(10000000 + Math.floor(rnd() * 89999999)), { timeout: 1500 }));
    }
    if (rnd() < 0.2) await opt(async () => { await p.locator('input[placeholder*="الخصم"]').first().fill(pick(['RIZQ10', 'WELCOME', 'VIP15']), { timeout: 1500 }); await p.getByRole('button', { name: 'تطبيق' }).click({ timeout: 1500 }); await p.waitForTimeout(150); });
    await p.getByRole('button', { name: pick(pays), exact: true }).click();
    await p.getByRole('button', { name: /تأكيد الطلب/ }).click();
    await p.waitForTimeout(400);
    if (i === 0) await p.screenshot({ path: '/home/claude/tools/after_confirm.png' });
    await p.keyboard.press('Escape'); await p.waitForTimeout(150);
    for (const t of ['طلب جديد', 'إغلاق', 'تم']) { const bt = p.getByRole('button', { name: t, exact: true }); if (await bt.count()) { await bt.first().click(); break; } }
    await p.waitForTimeout(200); done++;
  }
  await p.waitForTimeout(800);
  const b64 = await p.evaluate(async () => {
    const db = await new Promise((res, rej) => { const r = indexedDB.open('rizq-pos', 1); r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error); });
    const blob = await new Promise(res => { const r = db.transaction('sqlite', 'readonly').objectStore('sqlite').get('db'); r.onsuccess = () => res(r.result); });
    return btoa(Array.from(new Uint8Array(blob)).map(c => String.fromCharCode(c)).join(''));
  });
  fs.writeFileSync(out, Buffer.from(b64, 'base64'));
  console.log('checkouts', done);
  await b.close();
})();
