import { put, del } from '@vercel/blob';
import { authed, listAll } from '../lib/util.js';

const SID = /^[A-Za-z0-9._-]{1,64}$/;

export default async function handler(req, res) {
  const P = authed(req, res);
  if (!P) return;

  if (req.method === 'POST') {
    const { session_id: sid, text } = req.body || {};
    if (!SID.test(sid || '') || typeof text !== 'string' || !text.trim() || text.length > 4000) {
      return res.status(400).json({ error: 'bad request' });
    }
    await put(`${P}/command/${sid}/${Date.now()}`, text.trim(),
      { access: 'public', addRandomSuffix: false, contentType: 'text/plain' });
    const blobs = (await listAll(`${P}/command/${sid}/`))
      .sort((a, b) => b.pathname.localeCompare(a.pathname));
    if (blobs.length > 2) await del(blobs.slice(2).map((b) => b.url));
    return res.json({ ok: true });
  }

  const sid = req.query.id;
  if (sid) {
    if (!SID.test(sid)) return res.status(400).json({ error: 'bad id' });
    const blobs = await listAll(`${P}/command/${sid}/`);
    let newest = null;
    for (const b of blobs) {
      const m = b.pathname.match(/\/(\d+)$/);
      if (m && (!newest || Number(m[1]) > newest.ts)) newest = { ts: Number(m[1]), url: b.url };
    }
    if (!newest) return res.json({ command: null });
    const r = await fetch(newest.url, { cache: 'no-store' });
    return res.json({ command: { ts: newest.ts, text: await r.text() } });
  }

  const blobs = await listAll(`${P}/command/`);
  const newestPer = {};
  for (const b of blobs) {
    const m = b.pathname.match(/\/command\/([^/]+)\/(\d+)$/);
    if (m && (!newestPer[m[1]] || Number(m[2]) > newestPer[m[1]].ts)) {
      newestPer[m[1]] = { ts: Number(m[2]), url: b.url };
    }
  }
  const out = {};
  await Promise.all(Object.entries(newestPer).map(async ([id, v]) => {
    const r = await fetch(v.url, { cache: 'no-store' });
    if (r.ok) out[id] = { ts: v.ts, text: await r.text() };
  }));
  res.json({ commands: out });
}
