import { put, del } from '@vercel/blob';
import { authed, listAll } from '../lib/util.js';

const SID = /^[A-Za-z0-9._-]{1,64}$/;

export default async function handler(req, res) {
  const P = authed(req, res);
  if (!P) return;

  if (req.method === 'POST') {
    const { session_id: sid, text } = req.body || {};
    if (!SID.test(sid || '') || typeof text !== 'string') {
      return res.status(400).json({ error: 'bad request' });
    }
    await put(`${P}/capture/${sid}/${Date.now()}`, text.slice(0, 16000),
      { access: 'public', addRandomSuffix: false, contentType: 'text/plain' });
    const blobs = (await listAll(`${P}/capture/${sid}/`))
      .sort((a, b) => b.pathname.localeCompare(a.pathname));
    if (blobs.length > 2) await del(blobs.slice(2).map((b) => b.url));
    return res.json({ ok: true });
  }

  const sid = req.query.id;
  if (!SID.test(sid || '')) return res.status(400).json({ error: 'bad id' });
  const blobs = await listAll(`${P}/capture/${sid}/`);
  let newest = null;
  for (const b of blobs) {
    const m = b.pathname.match(/\/(\d+)$/);
    if (m && (!newest || Number(m[1]) > newest.ts)) newest = { ts: Number(m[1]), url: b.url };
  }
  if (!newest) return res.json({ capture: null });
  const r = await fetch(newest.url, { cache: 'no-store' });
  res.json({ capture: { ts: newest.ts, text: await r.text() } });
}
