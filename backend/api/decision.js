import { put } from '@vercel/blob';
import { authed, listAll } from '../lib/util.js';

const RID = /^[A-Za-z0-9._-]{1,128}$/;

export default async function handler(req, res) {
  const P = authed(req, res);
  if (!P) return;

  if (req.method === 'POST') {
    const { request_id: rid, decision } = req.body || {};
    if (!RID.test(rid || '') || !['allow', 'deny'].includes(decision)) {
      return res.status(400).json({ error: 'bad request' });
    }
    await put(`${P}/decision/${rid}/${decision}`, '1', { access: 'public', addRandomSuffix: false, allowOverwrite: true });
    return res.json({ ok: true });
  }

  const rid = req.query.id;
  if (!RID.test(rid || '')) return res.status(400).json({ error: 'bad id' });
  const blobs = await listAll(`${P}/decision/${rid}/`);
  for (const b of blobs) {
    const decision = b.pathname.split('/').pop();
    if (decision === 'allow' || decision === 'deny') return res.json({ decision });
  }
  res.status(404).json({ decision: null });
}
