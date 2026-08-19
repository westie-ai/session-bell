import crypto from 'node:crypto';
import { put } from '@vercel/blob';

// Self-serve onboarding: POST {invite} → a fresh tenant secret + all the
// links a new user needs. Gate with the INVITE_CODE env var.
export default async function handler(req, res) {
  if (req.method !== 'POST') return res.status(405).json({ error: 'POST only' });
  const invite = (req.body?.invite || '').trim();
  if (!process.env.INVITE_CODE || invite !== process.env.INVITE_CODE) {
    return res.status(403).json({ error: 'bad invite code' });
  }
  const secret = crypto.randomBytes(24).toString('hex');
  const uid = crypto.createHash('sha256').update(secret).digest('hex').slice(0, 16);
  await put(`meta/users/${uid}`, JSON.stringify({ created: Date.now() }),
    { access: 'public', addRandomSuffix: false });
  const origin = `https://${req.headers.host}`;
  const pairingCode = Buffer.from(JSON.stringify({ u: origin, s: secret })).toString('base64');
  res.json({
    pairing_code: pairingCode,
    installer_url: `${origin}/api/installer?code=${encodeURIComponent(pairingCode)}`,
  });
}
