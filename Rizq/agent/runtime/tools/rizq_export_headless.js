// Requires: the Rizq build served at http://localhost:4173 (e.g. `python3 -m http.server 4173` inside rizq-pos/) and Playwright.
// Usage: node tools/rizq_export_headless.js out.sqlite
// Opens the real Rizq app headless, lets it initialise + seed its DB, then exports the IndexedDB SQLite blob.
const { chromium } = require('playwright');
const fs = require('fs');
(async () => {
  const out = process.argv[2];
  const browser = await chromium.launch();
  const page = await browser.newPage();
  page.on('pageerror', e => console.log('pageerror', e.message));
  await page.goto('http://localhost:4173/', { waitUntil: 'networkidle' });
  await page.waitForTimeout(3000);
  const info = await page.evaluate(async () => {
    const db = await new Promise((res, rej) => { const r = indexedDB.open('rizq-pos', 1); r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error); });
    const blob = await new Promise((res, rej) => { const r = db.transaction('sqlite', 'readonly').objectStore('sqlite').get('db'); r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error); });
    if (!blob) return { size: 0 };
    return { size: blob.length || blob.byteLength, b64: btoa(Array.from(new Uint8Array(blob)).map(c => String.fromCharCode(c)).join('')),
             settings: localStorage.getItem('rizq-business') };
  });
  console.log('db bytes', info.size, 'settings', info.settings);
  if (info.b64) fs.writeFileSync(out, Buffer.from(info.b64, 'base64'));
  await page.screenshot({ path: out.replace('.sqlite', '.png') });
  await browser.close();
})();
