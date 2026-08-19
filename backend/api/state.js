import { put, del } from '@vercel/blob';
import { authed, listAll } from '../lib/util.js';

export default async function handler(req, res) {
  const P = authed(req, res);
  if (!P) return;

  if (req.method === 'POST') {
    const { host, ts, sessions } = req.body || {};
    if (typeof host !== 'string' || !host || typeof sessions !== 'object') {
      return res.status(400).json({ error: 'bad request' });
    }
    const key = encodeURIComponent(host);
    await put(`${P}/state/${key}/${Date.now()}.json`,
      JSON.stringify({ host, ts: ts || Math.floor(Date.now() / 1000), sessions,
                       usage: req.body.usage || null,
                       awake: req.body.awake === true,
                       projects: Array.isArray(req.body.projects) ? req.body.projects.slice(0, 12) : [],
                       hook_v: req.body.hook_v || null }),
      { access: 'public', addRandomSuffix: false, contentType: 'application/json' });
    const blobs = (await listAll(`${P}/state/${key}/`))
      .sort((a, b) => b.pathname.localeCompare(a.pathname));
    if (blobs.length > 3) await del(blobs.slice(3).map((b) => b.url));
    return res.json({ ok: true });
  }

  const blobs = await listAll(`${P}/state/`);
  const newestPerHost = new Map();
  for (const b of blobs) {
    const m = b.pathname.match(/\/state\/([^/]+)\/(\d+)\.json$/);
    if (!m) continue;
    const cur = newestPerHost.get(m[1]);
    if (!cur || Number(m[2]) > cur.n) newestPerHost.set(m[1], { n: Number(m[2]), url: b.url });
  }
  const out = {};
  await Promise.all([...newestPerHost.entries()].map(async ([, v]) => {
    const r = await fetch(v.url, { cache: 'no-store' });
    if (!r.ok) return;
    const data = await r.json();
    if (data && data.host) {
      out[data.host] = { ts: data.ts, sessions: data.sessions,
                         usage: data.usage || null, awake: data.awake === true,
                         projects: data.projects || [], hook_v: data.hook_v || null };
    }
  }));
  res.json(out);
}
