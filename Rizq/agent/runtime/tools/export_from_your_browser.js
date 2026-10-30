// Export YOUR Rizq database from the browser you use Rizq in (read-only, nothing is changed):
// 1) open Rizq (e.g. http://localhost:4173)  2) press F12 -> Console  3) paste this whole file and press Enter.
// A file "rizq.sqlite" downloads. Then: python3 -m rizq_agent rizq scan rizq.sqlite
(async () => {
  const db = await new Promise((res, rej) => { const r = indexedDB.open('rizq-pos', 1); r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error); });
  const bytes = await new Promise((res, rej) => { const r = db.transaction('sqlite', 'readonly').objectStore('sqlite').get('db'); r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error); });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([bytes], { type: 'application/x-sqlite3' }));
  a.download = 'rizq.sqlite'; a.click();
})();
