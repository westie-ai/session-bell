// Blob pathnames are never overwritten — the CDN caches aggressively, so every
// write gets a unique path and readers pick the newest. Small payloads ride in
// the pathname itself where possible (zero content fetches, list() is authoritative).
//
// Multi-tenant by construction: the caller's secret hashes to their namespace
// prefix, so isolation needs no user table and no lookup — you can only ever
// see the namespace your own secret derives.
import crypto from 'node:crypto';
import { list } from '@vercel/blob';

export function authed(req, res) {
  const secret = req.headers['x-sb-secret'] || '';
  if (!secret || secret.length < 16) {
    res.status(401).json({ error: 'unauthorized' });
    return null;
  }
  if (process.env.SB_SECRET && secret === process.env.SB_SECRET) {
    return 'sb'; // legacy root namespace (pre-multi-tenant data lives here)
  }
  const uid = crypto.createHash('sha256').update(secret).digest('hex').slice(0, 16);
  return `u/${uid}/sb`;
}

export async function listAll(prefix) {
  const out = [];
  let cursor;
  do {
    const page = await list({ prefix, cursor });
    out.push(...page.blobs);
    cursor = page.hasMore ? page.cursor : undefined;
  } while (cursor);
  return out;
}
