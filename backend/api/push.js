import http2 from 'node:http2';
import crypto from 'node:crypto';
import { authed, listAll } from '../lib/util.js';

// APNs auth key lives ONLY here (Vercel env) — Macs never touch the .p8.
const HOSTS = {
  production: 'https://api.push.apple.com',
  sandbox: 'https://api.sandbox.push.apple.com',
};

let jwtCache = { token: null, iat: 0 };

function b64url(buf) {
  return Buffer.from(buf).toString('base64url');
}

function apnsJwt() {
  const now = Math.floor(Date.now() / 1000);
  if (jwtCache.token && now - jwtCache.iat < 2700) return jwtCache.token;
  const header = b64url(JSON.stringify({ alg: 'ES256', kid: process.env.APNS_KEY_ID }));
  const claims = b64url(JSON.stringify({ iss: process.env.APNS_TEAM_ID, iat: now }));
  const signer = crypto.createSign('SHA256');
  signer.update(`${header}.${claims}`);
  const sig = signer.sign({ key: process.env.APNS_KEY, dsaEncoding: 'ieee-p1363' });
  jwtCache = { token: `${header}.${claims}.${b64url(sig)}`, iat: now };
  return jwtCache.token;
}

function apnsSend({ host, deviceToken, topic, pushType, priority, payload }) {
  return new Promise((resolve) => {
    const client = http2.connect(host);
    client.on('error', (e) => resolve({ status: 0, body: String(e) }));
    const req = client.request({
      ':method': 'POST',
      ':path': `/3/device/${deviceToken}`,
      authorization: `bearer ${apnsJwt()}`,
      'apns-topic': topic,
      'apns-push-type': pushType,
      'apns-priority': String(priority || 10),
    });
    let body = '';
    let status = 0;
    req.on('response', (h) => { status = h[':status']; });
    req.on('data', (c) => { body += c; });
    req.on('end', () => { client.close(); resolve({ status, body }); });
    req.on('error', (e) => { client.close(); resolve({ status: 0, body: String(e) }); });
    req.end(JSON.stringify(payload));
  });
}

export default async function handler(req, res) {
  const P = authed(req, res);
  if (!P) return;
  if (req.method !== 'POST') return res.status(405).json({ error: 'POST only' });
  if (!process.env.APNS_KEY) return res.status(503).json({ error: 'gateway not configured' });

  const { device_token: deviceToken, topic, push_type: pushType,
          priority, payload, environment } = req.body || {};
  if (!/^[0-9a-f]{16,400}$/.test(deviceToken || '') || !topic || !payload) {
    return res.status(400).json({ error: 'bad request' });
  }

  // Abuse guard: only tokens registered in the caller's own namespace.
  const known = new Set();
  for (const prefix of ['devices', 'pts']) {
    for (const b of await listAll(`${P}/${prefix}/`)) {
      known.add(b.pathname.split('/').pop());
    }
  }
  for (const b of await listAll(`${P}/dash/`)) {
    const m = b.pathname.match(/\/dash\/\d+-([0-9a-f]+)$/);
    if (m) known.add(m[1]);
  }
  if (!known.has(deviceToken)) {
    return res.status(403).json({ error: 'device not registered in your namespace' });
  }
  const host = HOSTS[environment] || HOSTS.production;
  const result = await apnsSend({
    host, deviceToken, topic,
    pushType: pushType || 'alert', priority, payload,
  });
  res.json(result);
}
