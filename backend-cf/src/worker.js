// SessionBell backend on Cloudflare Workers + D1.
// Same wire contract as the Vercel version; storage is a namespaced KV table
// (strongly consistent — none of the blob never-overwrite workarounds).
//
// Multi-tenant by construction: sha256(secret) → namespace. No user table.

import { renderBoard } from './board.js';
import { snapshot as ascSnapshot, configFromEnv as ascConfig } from './asc.js';

const HEX = /^[0-9a-f]{16,400}$/;
const SID = /^[A-Za-z0-9._-]{1,64}$/;
const RID = /^[A-Za-z0-9._-]{1,128}$/;

const APNS_HOSTS = {
  production: 'https://api.push.apple.com',
  sandbox: 'https://api.sandbox.push.apple.com',
};

// ---------- helpers ----------

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status, headers: { 'Content-Type': 'application/json' },
  });
}

async function sha256hex(s) {
  const d = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(s));
  return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

async function ns(req) {
  const secret = req.headers.get('x-sb-secret') || '';
  if (!secret || secret.length < 16) return null;
  return 'u/' + (await sha256hex(secret)).slice(0, 16);
}

const kvGet = (env, n, k) =>
  env.DB.prepare('SELECT v, ts FROM kv WHERE ns=? AND k=?').bind(n, k).first();
const kvPut = (env, n, k, v, ts) =>
  env.DB.prepare(
    'INSERT INTO kv (ns,k,v,ts) VALUES (?,?,?,?) ' +
    'ON CONFLICT(ns,k) DO UPDATE SET v=excluded.v, ts=excluded.ts')
    .bind(n, k, v, ts ?? Date.now()).run();
// Range scan instead of LIKE: LIKE is case-insensitive by default, so SQLite
// cannot use the (ns, k) primary key for it and scans every row of the
// namespace on each call (that alone burnt ~4.5M rows_read/day on D1's
// 5M free tier). `k >= p AND k < p||'\uffff'` walks only the matching rows.
const kvList = (env, n, prefix) =>
  env.DB.prepare('SELECT k, v, ts FROM kv WHERE ns=? AND k>=? AND k<?')
    .bind(n, prefix, prefix + '\uffff').all()
    .then((r) => r.results || []);

// Housekeeping, run from the cron: rows that are only ever appended
// (Live Activity tokens, captures, commands, decisions) would otherwise grow
// forever and make every kvList/handleToken read slower and costlier.
async function gc(env) {
  const now = Date.now();
  const day = 86400e3;
  await env.DB.batch([
    // A dashboard token marked ended (for over an hour) is dead: drop the
    // registration itself, then the tombstone once it's a week old.
    env.DB.prepare(
      'DELETE FROM kv WHERE k>=? AND k<? AND EXISTS (SELECT 1 FROM kv e ' +
      "WHERE e.ns=kv.ns AND e.k='dashended/'||substr(kv.k,6) AND e.ts<?)")
      .bind('dash/', 'dash/\uffff', now - 3600e3),
    env.DB.prepare('DELETE FROM kv WHERE k>=? AND k<? AND ts<?')
      .bind('dashended/', 'dashended/\uffff', now - 7 * day),
    env.DB.prepare('DELETE FROM kv WHERE k>=? AND k<? AND ts<?')
      .bind('capture/', 'capture/\uffff', now - 7 * day),
    env.DB.prepare('DELETE FROM kv WHERE k>=? AND k<? AND ts<?')
      .bind('command/', 'command/\uffff', now - 7 * day),
    env.DB.prepare('DELETE FROM kv WHERE k>=? AND k<? AND ts<?')
      .bind('decision/', 'decision/\uffff', now - 7 * day),
    env.DB.prepare('DELETE FROM kv WHERE ns=? AND k>=? AND k<? AND ts<?')
      .bind('sys', 'rl/', 'rl/\uffff', now - day),
    env.DB.prepare('DELETE FROM kv WHERE ns=? AND k>=? AND k<? AND ts<?')
      .bind('sys', 'pair/', 'pair/\uffff', now - 3600e3),
  ]);
}

async function readBody(req) {
  try { return await req.json(); } catch { return {}; }
}

// ---------- endpoints ----------

async function handleToken(req, env, n) {
  if (req.method === 'POST') {
    const b = await readBody(req);
    const now = Date.now();
    if (b.pts_token && HEX.test(b.pts_token)) await kvPut(env, n, `pts/${b.pts_token}`, '1', now);
    if (b.update_token && HEX.test(b.update_token)) await kvPut(env, n, `dash/${b.update_token}`, '1', now);
    if (b.ended_token && HEX.test(b.ended_token)) await kvPut(env, n, `dashended/${b.ended_token}`, '1', now);
    if (b.device_token && HEX.test(b.device_token)) await kvPut(env, n, `devices/${b.device_token}`, '1', now);
    if (b.reset_dashboard) {
      for (const row of await kvList(env, n, 'dash/')) {
        await kvPut(env, n, 'dashended/' + row.k.slice(5), '1', now);
      }
    }
    return json({ ok: true });
  }
  const [pts, dash, ended, devices] = await Promise.all([
    kvList(env, n, 'pts/'), kvList(env, n, 'dash/'),
    kvList(env, n, 'dashended/'), kvList(env, n, 'devices/'),
  ]);
  const endedSet = new Set(ended.map((r) => r.k.slice('dashended/'.length)));
  let dashboard = null;
  for (const r of dash) {
    const tok = r.k.slice(5);
    if (!endedSet.has(tok) && (!dashboard || r.ts > dashboard.ts)) {
      dashboard = { ts: r.ts, token: tok };
    }
  }
  return json({
    pts: pts.map((r) => r.k.slice(4)).filter((t) => HEX.test(t)),
    devices: devices.map((r) => r.k.slice(8)).filter((t) => HEX.test(t)),
    dashboard,
  });
}

async function handleState(req, env, n) {
  if (req.method === 'POST') {
    const b = await readBody(req);
    if (typeof b.host !== 'string' || !b.host || typeof b.sessions !== 'object') {
      return json({ error: 'bad request' }, 400);
    }
    const doc = {
      host: b.host, ts: b.ts || Math.floor(Date.now() / 1000), sessions: b.sessions,
      usage: b.usage || null, awake: b.awake === true,
      projects: Array.isArray(b.projects) ? b.projects.slice(0, 12) : [],
      hook_v: b.hook_v || null,
    };
    await kvPut(env, n, 'state/' + encodeURIComponent(b.host), JSON.stringify(doc));
    return json({ ok: true });
  }
  const out = {};
  for (const r of await kvList(env, n, 'state/')) {
    try {
      const d = JSON.parse(r.v);
      if (d && d.host) {
        out[d.host] = { ts: d.ts, sessions: d.sessions, usage: d.usage || null,
                        awake: d.awake === true, projects: d.projects || [],
                        hook_v: d.hook_v || null };
      }
    } catch {}
  }
  return json(out);
}

async function handleCommand(req, env, n, url) {
  if (req.method === 'POST') {
    const b = await readBody(req);
    const sid = b.session_id;
    if (!SID.test(sid || '') || typeof b.text !== 'string' || !b.text.trim()
        || b.text.length > 4000) {
      return json({ error: 'bad request' }, 400);
    }
    await kvPut(env, n, `command/${sid}`, b.text.trim());
    return json({ ok: true });
  }
  const sid = url.searchParams.get('id');
  if (sid) {
    if (!SID.test(sid)) return json({ error: 'bad id' }, 400);
    const row = await kvGet(env, n, `command/${sid}`);
    return json({ command: row ? { ts: row.ts, text: row.v } : null });
  }
  const out = {};
  for (const r of await kvList(env, n, 'command/')) {
    out[r.k.slice('command/'.length)] = { ts: r.ts, text: r.v };
  }
  return json({ commands: out });
}

async function handleCapture(req, env, n, url) {
  if (req.method === 'POST') {
    const b = await readBody(req);
    if (!SID.test(b.session_id || '') || typeof b.text !== 'string') {
      return json({ error: 'bad request' }, 400);
    }
    await kvPut(env, n, `capture/${b.session_id}`, b.text.slice(0, 16000));
    return json({ ok: true });
  }
  const sid = url.searchParams.get('id');
  if (!SID.test(sid || '')) return json({ error: 'bad id' }, 400);
  const row = await kvGet(env, n, `capture/${sid}`);
  return json({ capture: row ? { ts: row.ts, text: row.v } : null });
}

async function handleDecision(req, env, n, url) {
  if (req.method === 'POST') {
    const b = await readBody(req);
    if (!RID.test(b.request_id || '') || !['allow', 'deny'].includes(b.decision)) {
      return json({ error: 'bad request' }, 400);
    }
    await kvPut(env, n, `decision/${b.request_id}`, b.decision);
    return json({ ok: true });
  }
  const rid = url.searchParams.get('id');
  if (!RID.test(rid || '')) return json({ error: 'bad id' }, 400);
  const row = await kvGet(env, n, `decision/${rid}`);
  if (row && (row.v === 'allow' || row.v === 'deny')) return json({ decision: row.v });
  return json({ decision: null }, 404);
}

// ---------- APNs gateway ----------

let jwtCache = { token: null, iat: 0, kid: null };

function b64url(bytes) {
  let s = typeof bytes === 'string' ? bytes
    : String.fromCharCode(...new Uint8Array(bytes));
  return btoa(s).replaceAll('+', '-').replaceAll('/', '_').replace(/=+$/, '');
}

async function apnsJwt(env) {
  const now = Math.floor(Date.now() / 1000);
  if (jwtCache.token && jwtCache.kid === env.APNS_KEY_ID && now - jwtCache.iat < 2700) {
    return jwtCache.token;
  }
  const pem = env.APNS_KEY.replace(/-----[^-]+-----/g, '').replace(/\s+/g, '');
  const der = Uint8Array.from(atob(pem), (c) => c.charCodeAt(0));
  const key = await crypto.subtle.importKey(
    'pkcs8', der, { name: 'ECDSA', namedCurve: 'P-256' }, false, ['sign']);
  const header = b64url(JSON.stringify({ alg: 'ES256', kid: env.APNS_KEY_ID }));
  const claims = b64url(JSON.stringify({ iss: env.APNS_TEAM_ID, iat: now }));
  const sig = await crypto.subtle.sign(
    { name: 'ECDSA', hash: 'SHA-256' }, key,
    new TextEncoder().encode(`${header}.${claims}`));
  jwtCache = { token: `${header}.${claims}.${b64url(sig)}`, iat: now, kid: env.APNS_KEY_ID };
  return jwtCache.token;
}

async function handlePush(req, env, n) {
  if (req.method !== 'POST') return json({ error: 'POST only' }, 405);
  if (!env.APNS_KEY) return json({ error: 'gateway not configured' }, 503);
  const b = await readBody(req);
  if (!HEX.test(b.device_token || '') || !b.topic || !b.payload) {
    return json({ error: 'bad request' }, 400);
  }
  // Abuse guard: only tokens registered in the caller's own namespace.
  const known = new Set();
  for (const prefix of ['devices/', 'pts/', 'dash/']) {
    for (const r of await kvList(env, n, prefix)) {
      known.add(r.k.split('/').pop());
    }
  }
  if (!known.has(b.device_token)) {
    return json({ error: 'device not registered in your namespace' }, 403);
  }
  const send = async (envName) =>
    fetch(`${APNS_HOSTS[envName]}/3/device/${b.device_token}`, {
      method: 'POST',
      headers: {
        authorization: `bearer ${await apnsJwt(env)}`,
        'apns-topic': b.topic,
        'apns-push-type': b.push_type || 'alert',
        'apns-priority': String(b.priority || 10),
      },
      body: JSON.stringify(b.payload),
    });
  try {
    // 双环境兜底:Xcode 直装的设备是 sandbox token,TestFlight/App Store
    // 是 production;发错环境 APNs 返 400 BadDeviceToken,换边重试。
    let envName = APNS_HOSTS[b.environment] ? b.environment : 'production';
    let resp = await send(envName);
    let text = await resp.text();
    if (resp.status === 400 && text.includes('BadDeviceToken')) {
      resp = await send(envName === 'production' ? 'sandbox' : 'production');
      text = await resp.text();
    }
    return json({ status: resp.status, body: text });
  } catch (e) {
    return json({ status: 0, body: String(e) });
  }
}

// ---------- App Review 演示租户 ----------

const demoNs = async (env) =>
  env.DEMO_SECRET ? 'u/' + (await sha256hex(env.DEMO_SECRET)).slice(0, 16) : null;

/// 演示数据分中英两套:App 请求带 Accept-Language,审核员和英文用户看英文,
/// 中文设备看中文。cron 默认英文。
const DEMO_TEXT = {
  en: {
    checkoutWait: 'Waiting for you to confirm the database migration plan',
    checkoutRun: 'Refactoring the payment callback',
    web: 'Implementing the checkout page from the design, 12 new components',
    docs: 'Deploy finished: all 38 pages passed validation',
    capture: ['$ claude "migrate the orders table to the new schema"', '',
      '⏺ Analyzed 14 references, migration script generated:',
      '  migrations/2026_08_orders_v2.sql', '',
      '  Needs your confirmation before running: the live table',
      '  has 2.1M rows, expected lock time ~40 s.',
      '  Run now or wait for off-peak?', '',
      '❯ waiting for input…'],
  },
  zh: {
    checkoutWait: '等待你确认数据库迁移方案',
    checkoutRun: '正在重构支付回调',
    web: '按设计稿实现结算页,新增 12 个组件',
    docs: '部署完成:38 个页面全部通过校验',
    capture: ['$ claude "迁移 orders 表到新 schema"', '',
      '⏺ 分析了 14 个引用点,迁移脚本已生成:',
      '  migrations/2026_08_orders_v2.sql', '',
      '  执行前需要你确认:线上表有 210 万行,',
      '  预计锁表 40 秒。现在执行还是等低峰?', '',
      '❯ 待输入…'],
  },
};

const demoLang = (req) =>
  /^zh/i.test((req?.headers.get('accept-language') || '').trim()) ? 'zh' : 'en';

/// 往 demo 命名空间写一组"活的"模拟任务:状态按 5 分钟相位轮转,
/// 计时器起点每次重算,审核员任何时候打开 App 都像正撞上一场真实工作。
async function seedDemo(env, lang = 'en') {
  const n = await demoNs(env);
  if (!n) return;
  const T = DEMO_TEXT[lang] || DEMO_TEXT.en;
  const now = Math.floor(Date.now() / 1000);
  const phase = Math.floor(now / 300) % 2;
  const mbp = {
    host: 'MacBook Pro', ts: now, awake: true, hook_v: 'demo',
    projects: ['/Users/demo/checkout', '/Users/demo/web-app'],
    sessions: {
      'demo-checkout': {
        status: phase === 0 ? 'waiting' : 'running', since: now - (phase === 0 ? 95 : 340),
        project: 'checkout', detail: phase === 0 ? T.checkoutWait : T.checkoutRun,
        agents: 0, cwd: '/Users/demo/checkout',
      },
      'demo-web': {
        status: 'running', since: now - 820,
        project: 'web-app', detail: T.web,
        agents: 2, cwd: '/Users/demo/web-app',
      },
    },
    usage: {
      today_out: 1.8e6, week_out: 2.4e7, official_total_pct: 34,
      reset_ts: now + 3.2 * 86400,
      official_session_pct: 58, session_reset_ts: now + 1.4 * 3600,
      week_fable: 5.6e6, official_pct: 41, premium_name: 'Fable',
    },
  };
  const studio = {
    host: 'Mac Studio', ts: now, awake: false, hook_v: 'demo',
    projects: ['/Users/demo/docs-site'],
    sessions: {
      'demo-docs': {
        status: 'done', since: now - 150,
        project: 'docs-site', detail: T.docs,
        agents: 0, cwd: '/Users/demo/docs-site',
      },
    },
  };
  await kvPut(env, n, 'state/' + encodeURIComponent(mbp.host), JSON.stringify(mbp));
  await kvPut(env, n, 'state/' + encodeURIComponent(studio.host), JSON.stringify(studio));
  await kvPut(env, n, 'capture/demo-checkout', T.capture.join('\n'));
}

// ---------- feedback ----------

/// App 内反馈:落 D1(sys 命名空间,board 脚本会读),再尽力发一封邮件到
/// FEEDBACK_TO。邮件通道按可用性依次尝试:Cloudflare Email Sending binding
/// (env.EMAIL)→ Resend(RESEND_API_KEY)。都没配也返回 ok,反馈不丢。
async function sendFeedbackMail(env, subject, text, replyTo) {
  const to = env.FEEDBACK_TO;
  if (!to) return 'stored';
  if (env.EMAIL && typeof env.EMAIL.send === 'function') {
    try {
      await env.EMAIL.send({
        to, from: { email: env.FEEDBACK_FROM || 'sessionbell@westie.ai', name: 'SessionBell' },
        subject, text, ...(replyTo ? { replyTo } : {}),
      });
      return 'cloudflare';
    } catch (e) { console.log('feedback: cloudflare email failed', String(e)); }
  }
  if (env.RESEND_API_KEY) {
    const r = await fetch('https://api.resend.com/emails', {
      method: 'POST',
      headers: { Authorization: `Bearer ${env.RESEND_API_KEY}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({
        from: env.FEEDBACK_FROM || 'SessionBell <onboarding@resend.dev>',
        to: [to], subject, text, ...(replyTo ? { reply_to: replyTo } : {}),
      }),
    });
    if (r.ok) return 'resend';
    console.log('feedback: resend failed', r.status, await r.text());
  }
  return 'stored';
}

async function handleFeedback(req, env, n) {
  if (req.method !== 'POST') return json({ error: 'POST only' }, 405);
  const b = await readBody(req);
  const text = typeof b.text === 'string' ? b.text.trim().slice(0, 4000) : '';
  if (text.length < 2) return json({ error: 'empty' }, 400);
  if (!(await rateLimit(env, 'feedback/' + n, 10, 86400))) {
    return json({ error: 'rate limited' }, 429);
  }
  const contact = typeof b.contact === 'string' ? b.contact.trim().slice(0, 200) : '';
  const meta = {};
  for (const k of ['app_version', 'build', 'os', 'device', 'locale', 'kind']) {
    if (typeof b[k] === 'string' && b[k]) meta[k] = b[k].slice(0, 80);
  }
  const now = Date.now();
  const id = `${now}-${crypto.randomUUID().slice(0, 8)}`;
  const doc = { ns: n, text, contact, ...meta, ts: now };
  await kvPut(env, 'sys', `feedback/${id}`, JSON.stringify(doc), now);

  const acct = n.slice(2, 8);
  const subject = `[SessionBell 反馈] ${meta.kind || 'feedback'} · ${acct} · ${text.slice(0, 40).replace(/\s+/g, ' ')}`;
  const body = [
    text, '',
    '——',
    `账号: ${acct}`,
    contact ? `联系方式: ${contact}` : '联系方式: (未留)',
    `App: ${meta.app_version || '?'} (${meta.build || '?'})  iOS ${meta.os || '?'}  ${meta.device || '?'}  ${meta.locale || ''}`,
    `时间: ${new Date(now).toISOString()}`,
    `id: ${id}`,
  ].join('\n');
  const replyTo = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(contact) ? contact : undefined;
  const via = await sendFeedbackMail(env, subject, body, replyTo);
  return json({ ok: true, id, via });
}

// ---------- short pairing codes ----------
//
// A 6-digit code stands in for the 150-char base64 pairing code for 15 minutes,
// single use, so nothing has to travel between phone and Mac by clipboard.
//   phone-first: App mints a code, Mac runs `curl …/i | bash -s 483920`
//   Mac-first:   install script signs up + mints, phone scans /p/483920
// Brute force: 10^6 space, 15-min window, per-IP rate limit on redeem/status.

const PAIR_TTL = 15 * 60e3;
const SHORT = /^[0-9]{6}$/;

async function mintShortCode(env, pairingCode) {
  for (let i = 0; i < 6; i++) {
    const code = String(crypto.getRandomValues(new Uint32Array(1))[0] % 1000000).padStart(6, '0');
    const row = await kvGet(env, 'sys', 'pair/' + code);
    if (row && Date.now() - row.ts < PAIR_TTL) continue;
    await kvPut(env, 'sys', 'pair/' + code, JSON.stringify({ pc: pairingCode, redeemed: 0 }));
    return code;
  }
  return null;
}

async function readPair(env, code) {
  if (!SHORT.test(code)) return null;
  const row = await kvGet(env, 'sys', 'pair/' + code);
  if (!row || Date.now() - row.ts > PAIR_TTL) return null;
  try { return { ...JSON.parse(row.v), ts: row.ts }; } catch { return null; }
}

function secretOf(pairingCode) {
  try { return JSON.parse(atob(pairingCode)).s || ''; } catch { return ''; }
}

/// POST /api/pair-code {pairing_code} — authed; the caller re-presents its own
/// pairing code (the server only stores the hash of the secret) and gets a
/// fresh short code for it. Used by the App's "I'm at my Mac" screen.
async function handlePairCode(req, env, n) {
  if (req.method !== 'POST') return json({ error: 'POST only' }, 405);
  const b = await readBody(req);
  const pc = typeof b.pairing_code === 'string' ? b.pairing_code : '';
  const secret = secretOf(pc);
  if (!secret || 'u/' + (await sha256hex(secret)).slice(0, 16) !== n) {
    return json({ error: 'pairing code does not match this tenant' }, 400);
  }
  if (!(await rateLimit(env, 'paircode/' + n, 30, 3600))) return json({ error: 'rate limited' }, 429);
  const code = await mintShortCode(env, pc);
  if (!code) return json({ error: 'try again' }, 503);
  return json({ short_code: code, expires_in: PAIR_TTL / 1000 });
}

/// GET /api/pair/<code>        → { pairing_code }  (single use)
/// GET /api/pair/<code>/status → { redeemed, macs, phones }  (for the QR page)
async function handlePair(req, env, url, code, sub) {
  const ip = req.headers.get('cf-connecting-ip') || 'unknown';
  if (!(await rateLimit(env, 'pair/' + ip, 60, 600))) return json({ error: 'rate limited' }, 429);
  const pair = await readPair(env, code);
  if (!pair) return json({ error: 'code expired or unknown' }, 404);
  if (sub === 'status') {
    const n = 'u/' + (await sha256hex(secretOf(pair.pc))).slice(0, 16);
    const [macs, phones] = await Promise.all([kvList(env, n, 'state/'), kvList(env, n, 'devices/')]);
    return json({ redeemed: !!pair.redeemed, macs: macs.length, phones: phones.length,
      expires_in: Math.max(0, Math.round((pair.ts + PAIR_TTL - Date.now()) / 1000)) });
  }
  if (pair.redeemed) return json({ error: 'code already used' }, 410);
  await kvPut(env, 'sys', 'pair/' + code, JSON.stringify({ ...pair, redeemed: Date.now() }), pair.ts);
  return json({ pairing_code: pair.pc });
}

/// POST /api/event {name} — onboarding milestones, one row per name per tenant,
/// so the board funnel can tell "saw the connect screen" from "copied the
/// command" from "paired". Names are a fixed allow-list.
const EVENTS = new Set(['connect_seen', 'connect_later', 'command_copied', 'code_entered', 'paired', 'demo_seen']);
async function handleEvent(req, env, n) {
  if (req.method !== 'POST') return json({ error: 'POST only' }, 405);
  const b = await readBody(req);
  if (!EVENTS.has(b.name)) return json({ error: 'unknown event' }, 400);
  await kvPut(env, n, `onb/${b.name}`, '1');
  return json({ ok: true });
}

// ---------- web: one-line installer, QR page, universal links ----------

async function serveInstaller(req, env) {
  const r = await env.ASSETS.fetch(new Request(new URL('/install.sh', req.url), { method: 'GET' }));
  return new Response(r.body, { status: r.status, headers: {
    'Content-Type': 'text/x-shellscript; charset=utf-8', 'Cache-Control': 'no-store',
  } });
}

function aasa() {
  const appID = '27Z3Z38H3M.dev.yuesun.SessionBell';
  return new Response(JSON.stringify({
    applinks: { apps: [], details: [{ appID, paths: ['/p/*'], components: [{ '/': '/p/*' }] }] },
  }), { headers: { 'Content-Type': 'application/json', 'Cache-Control': 'public, max-age=3600' } });
}

const STORE_URL = 'https://apps.apple.com/app/id6801045681';

/// /p/<code> — what the Mac's browser opens after the one-liner, and what the
/// iPhone lands on when it scans the QR without the App installed.
function pairPage(code, origin, lang) {
  const zh = lang !== 'en';
  const t = zh ? {
    title: 'SessionBell · 用 iPhone 扫一下',
    h: 'Mac 这边好了，现在拿起 iPhone',
    scan: '用 iPhone 的相机对准这个码',
    or: '或者在 App 里输入这 6 位数字',
    exp: '有效 15 分钟',
    phoneH: '在这台 iPhone 上',
    phoneStore: '第 1 步 · 装 SessionBell',
    phoneCode: '第 2 步 · 装好后回来再扫一次这个码，会自动配好。或者打开 App → 「我现在就在 Mac 前」→ 页面底部输入',
    done: 'iPhone 已连上 🎉',
    doneSub: '可以关掉这个页面了。下次 Claude Code 停下来等你时，锁屏上会出现一张卡片。',
    expired: '这个码过期了。回到 Mac 终端重新跑一遍那行命令，会给你一个新码。',
  } : {
    title: 'SessionBell · Scan with your iPhone',
    h: 'Mac is ready. Now pick up your iPhone',
    scan: 'Point the iPhone camera at this code',
    or: 'or type these 6 digits in the app',
    exp: 'valid for 15 minutes',
    phoneH: 'On this iPhone',
    phoneStore: 'Step 1 · Install SessionBell',
    phoneCode: 'Step 2 · Come back and scan this code again — it pairs by itself. Or open the app → "I\'m at my Mac now" → enter at the bottom:',
    done: 'iPhone connected 🎉',
    doneSub: 'You can close this page. Next time Claude Code stops for you, a card appears on your Lock Screen.',
    expired: 'This code expired. Run the command on the Mac again for a fresh one.',
  };
  const link = `${origin}/p/${code}`;
  const pretty = code.slice(0, 3) + ' ' + code.slice(3);
  return `<!doctype html><html lang="${zh ? 'zh-CN' : 'en'}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>${t.title}</title>
<meta name="apple-itunes-app" content="app-id=6801045681">
<style>
:root{color-scheme:light dark;--ground:#FFF8E6;--ink:#2B2723;--muted:#8A7F66;--accent:#FECE23;--deep:#B27E00;--ok:#2F8F5B}
@media(prefers-color-scheme:dark){:root{--ground:#1a1a18;--ink:#F1EEE4;--muted:#A79E88}}
*{box-sizing:border-box;margin:0}body{background:var(--ground);color:var(--ink);font:17px/1.6 -apple-system,"PingFang SC",system-ui,sans-serif;-webkit-font-smoothing:antialiased;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px}
.card{max-width:440px;width:100%;text-align:center}
h1{font-size:24px;line-height:1.25;margin-bottom:22px;text-wrap:balance}
#qr{width:240px;height:240px;margin:0 auto 18px;padding:14px;background:#fff;border-radius:18px;box-shadow:0 8px 30px rgba(0,0,0,.08)}
#qr img,#qr canvas{width:100%!important;height:100%!important;display:block}
.code{font:600 44px/1 ui-monospace,"SF Mono",Menlo,monospace;letter-spacing:.08em;margin:6px 0 4px}
.muted{color:var(--muted);font-size:15px}
.mac,.phone,.done,.expired{display:none}.show{display:block}
.btn{display:inline-block;background:var(--ink);color:var(--ground);padding:14px 22px;border-radius:14px;text-decoration:none;font-weight:600;margin:10px 0 22px}
.done h1{color:var(--ok)}
.tick{font-size:64px;line-height:1;margin-bottom:12px}
</style></head><body><div class="card">
<section class="mac"><h1>${t.h}</h1><div id="qr"></div><p class="muted">${t.scan}</p><p class="muted" style="margin-top:14px">${t.or}</p><div class="code">${pretty}</div><p class="muted">${t.exp}</p></section>
<section class="phone"><p class="muted">${t.phoneH}</p><h1>${t.phoneStore}</h1><a class="btn" href="${STORE_URL}">App Store</a><p class="muted">${t.phoneCode}</p><div class="code">${pretty}</div><p class="muted">${t.exp}</p></section>
<section class="done"><div class="tick">✅</div><h1>${t.done}</h1><p class="muted">${t.doneSub}</p></section>
<section class="expired"><h1>⌛</h1><p class="muted">${t.expired}</p></section>
</div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js"></script>
<script>
(function(){
  var isPhone=/iPhone|iPad|iPod/.test(navigator.userAgent);
  var mac=document.querySelector('.mac'),phone=document.querySelector('.phone'),done=document.querySelector('.done'),exp=document.querySelector('.expired');
  (isPhone?phone:mac).classList.add('show');
  if(!isPhone&&window.QRCode){new QRCode(document.getElementById('qr'),{text:${JSON.stringify(link)},width:212,height:212,correctLevel:QRCode.CorrectLevel.M});}
  function show(el){[mac,phone,done,exp].forEach(function(e){e.classList.remove('show')});el.classList.add('show');}
  function tick(){
    fetch('/api/pair/${code}/status',{cache:'no-store'}).then(function(r){return r.ok?r.json():{gone:true}}).then(function(j){
      if(j.gone){show(exp);return;}
      if(j.phones>0&&(j.redeemed||j.macs>0)){show(done);return;}
      setTimeout(tick,3000);
    }).catch(function(){setTimeout(tick,5000)});
  }
  tick();
})();
</script></body></html>`;
}

// ---------- onboarding ----------

/// 简单滑动窗口限流(D1 的 sys 命名空间):同一 key 在 windowSec 内最多 limit 次。
async function rateLimit(env, key, limit, windowSec) {
  const now = Math.floor(Date.now() / 1000);
  let n = 0, start = now;
  const row = await kvGet(env, 'sys', 'rl/' + key);
  if (row) {
    try {
      const o = JSON.parse(row.v);
      if (now - o.start < windowSec) { n = o.n; start = o.start; }
    } catch {}
  }
  if (n >= limit) return false;
  await kvPut(env, 'sys', 'rl/' + key, JSON.stringify({ n: n + 1, start }));
  return true;
}

/// 开放注册:不需要邀请码,点一下就开一个空租户。滥用靠每 IP 每小时限流兜底
/// (租户只是一个 KV 前缀,开一个几乎零成本)。
/// { demo: true } 或旧的审核码 → 进预置模拟数据的固定 demo 命名空间,给
/// 还没连 Mac 的新用户和 App Review 看,不产生新租户。
async function handleSignup(req, env, url) {
  if (req.method !== 'POST') return json({ error: 'POST only' }, 405);
  const b = await readBody(req);
  const invite = (b.invite || '').trim();
  const wantsDemo = b.demo === true || (env.REVIEW_CODE && invite && invite === env.REVIEW_CODE);
  if (wantsDemo) {
    if (!env.DEMO_SECRET) return json({ error: 'demo unavailable' }, 503);
    await seedDemo(env, demoLang(req));
    return json({
      pairing_code: btoa(JSON.stringify({ u: url.origin, s: env.DEMO_SECRET })),
      demo: true,
    });
  }
  const ip = req.headers.get('cf-connecting-ip') || 'unknown';
  if (!(await rateLimit(env, 'signup/' + ip, 10, 3600))) {
    return json({ error: 'rate limited' }, 429);
  }
  const raw = crypto.getRandomValues(new Uint8Array(24));
  const secret = [...raw].map((x) => x.toString(16).padStart(2, '0')).join('');
  const uid = (await sha256hex(secret)).slice(0, 16);
  await kvPut(env, `u/${uid}`, 'meta/user', JSON.stringify({ created: Date.now() }));
  const origin = url.origin;
  const pairingCode = btoa(JSON.stringify({ u: origin, s: secret }));
  return json({
    pairing_code: pairingCode,
    short_code: await mintShortCode(env, pairingCode),
    installer_url: `${origin}/api/installer?code=${encodeURIComponent(pairingCode)}`,
  });
}

function handleInstaller(url) {
  const code = url.searchParams.get('code') || '';
  if (!/^[A-Za-z0-9+/=_-]{16,512}$/.test(code)) {
    return new Response('bad code', { status: 400 });
  }
  const script = `#!/bin/bash
# SessionBell Mac 安装器(双击运行)
clear
echo "🔔 SessionBell 接入中……"
curl -fsSL "${url.origin}/install.sh" | bash -s -- "${code}" \\
  && echo "" && echo "✅ 完成!可以关闭这个窗口了。" \\
  || { echo ""; echo "❌ 出错了,把上面的输出截图发给管理员。"; }
read -n 1 -s -r -p "按任意键关闭…"
`;
  return new Response(script, {
    headers: {
      'Content-Type': 'application/x-shellscript; charset=utf-8',
      'Content-Disposition':
        "attachment; filename*=UTF-8''SessionBell%E5%AE%89%E8%A3%85%E5%99%A8.command",
    },
  });
}

// ---------- usage board ----------

const ASC_TTL_MIN = 30;

/// 运营看板:/board?k=<BOARD_KEY>。每次打开从 D1 现算;App Store Connect 的
/// 数据(7 次销售报告 + 3 个接口)在 sys 命名空间缓存 30 分钟。没配 BOARD_KEY
/// 时整个路由不存在,避免把账号列表和反馈内容暴露出去。
async function handleBoard(req, env, url) {
  if (!env.BOARD_KEY || url.searchParams.get('k') !== env.BOARD_KEY) {
    return new Response('not found', { status: 404 });
  }
  const now = Date.now();
  let asc = null;
  const cached = await kvGet(env, 'sys', 'cache/asc');
  if (cached && now - cached.ts < ASC_TTL_MIN * 60e3 && !url.searchParams.has('fresh')) {
    try { asc = JSON.parse(cached.v); } catch {}
  }
  if (!asc) {
    asc = await ascSnapshot(ascConfig(env));
    if (asc.configured && !asc.errors.length) {
      await kvPut(env, 'sys', 'cache/asc', JSON.stringify(asc), now);
    }
  }
  const db = (sql) => env.DB.prepare(sql).all().then((r) => r.results || []);
  const html = await renderBoard({ db, asc, mode: 'live', ascTtlMin: ASC_TTL_MIN, now });
  return new Response(html, { headers: {
    'Content-Type': 'text/html; charset=utf-8',
    'Cache-Control': 'no-store',
    'X-Robots-Tag': 'noindex',
  } });
}

// ---------- router ----------

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    const path = url.pathname;

    if (path === '/board') return handleBoard(req, env, url);
    if (path === '/i' || path === '/i/') return serveInstaller(req, env);
    if (path === '/.well-known/apple-app-site-association') return aasa();
    {
      const m = path.match(/^\/p\/([0-9]{6})$/);
      if (m) {
        return new Response(pairPage(m[1], url.origin, demoLang(req)), {
          headers: { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' },
        });
      }
      const a = path.match(/^\/api\/pair\/([0-9]{6})(?:\/(status))?$/);
      if (a) return handlePair(req, env, url, a[1], a[2]);
    }
    if (path === '/api/installer') return handleInstaller(url);
    if (path === '/api/signup') return handleSignup(req, env, url);

    if (path.startsWith('/api/')) {
      const n = await ns(req);
      if (!n) return json({ error: 'unauthorized' }, 401);
      // 演示租户读状态时懒播种:即使 cron 停了,审核员看到的也永远新鲜。
      if (path === '/api/state' && req.method === 'GET' && n === await demoNs(env)) {
        await seedDemo(env, demoLang(req));
      }
      if (path === '/api/token') return handleToken(req, env, n);
      if (path === '/api/state') return handleState(req, env, n);
      if (path === '/api/command') return handleCommand(req, env, n, url);
      if (path === '/api/capture') return handleCapture(req, env, n, url);
      if (path === '/api/decision') return handleDecision(req, env, n, url);
      if (path === '/api/push') return handlePush(req, env, n);
      if (path === '/api/feedback') return handleFeedback(req, env, n);
      if (path === '/api/pair-code') return handlePairCode(req, env, n);
      if (path === '/api/event') return handleEvent(req, env, n);
      return json({ error: 'not found' }, 404);
    }
    return env.ASSETS.fetch(req);
  },

  // 每 5 分钟刷新演示租户,保证锁屏/面板计时和状态轮转是"活"的;顺便清理过期行。
  async scheduled(event, env, ctx) {
    ctx.waitUntil(Promise.all([seedDemo(env), gc(env)]));
  },
};
