// SessionBell backend on Cloudflare Workers + D1.
// Same wire contract as the Vercel version; storage is a namespaced KV table
// (strongly consistent — none of the blob never-overwrite workarounds).
//
// Multi-tenant by construction: sha256(secret) → namespace. No user table.

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
const kvList = (env, n, prefix) =>
  env.DB.prepare("SELECT k, v, ts FROM kv WHERE ns=? AND k LIKE ? ESCAPE '\\'")
    .bind(n, prefix.replaceAll('_', '\\_').replaceAll('%', '\\%') + '%').all()
    .then((r) => r.results || []);

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

/// 往 demo 命名空间写一组"活的"模拟任务:状态按 5 分钟相位轮转,
/// 计时器起点每次重算,审核员任何时候打开 App 都像正撞上一场真实工作。
async function seedDemo(env) {
  const n = await demoNs(env);
  if (!n) return;
  const now = Math.floor(Date.now() / 1000);
  const phase = Math.floor(now / 300) % 2;
  const mbp = {
    host: 'MacBook Pro', ts: now, awake: true, hook_v: 'demo',
    projects: ['/Users/demo/checkout', '/Users/demo/web-app'],
    sessions: {
      'demo-checkout': {
        status: phase === 0 ? 'waiting' : 'running', since: now - (phase === 0 ? 95 : 340),
        project: 'checkout', detail: phase === 0 ? '等待你确认数据库迁移方案' : '正在重构支付回调',
        agents: 0, cwd: '/Users/demo/checkout',
      },
      'demo-web': {
        status: 'running', since: now - 820,
        project: 'web-app', detail: '按设计稿实现结算页,新增 12 个组件',
        agents: 2, cwd: '/Users/demo/web-app',
      },
    },
    usage: {
      today_out: 1.8e6, week_out: 2.4e7, official_total_pct: 34,
      reset_ts: now + 3.2 * 86400,
      week_fable: 5.6e6, official_pct: 41, premium_name: 'Fable',
    },
  };
  const studio = {
    host: 'Mac Studio', ts: now, awake: false, hook_v: 'demo',
    projects: ['/Users/demo/docs-site'],
    sessions: {
      'demo-docs': {
        status: 'done', since: now - 150,
        project: 'docs-site', detail: '部署完成:38 个页面全部通过校验',
        agents: 0, cwd: '/Users/demo/docs-site',
      },
    },
  };
  await kvPut(env, n, 'state/' + encodeURIComponent(mbp.host), JSON.stringify(mbp));
  await kvPut(env, n, 'state/' + encodeURIComponent(studio.host), JSON.stringify(studio));
  await kvPut(env, n, 'capture/demo-checkout',
    ['$ claude "迁移 orders 表到新 schema"', '',
     '⏺ 分析了 14 个引用点,迁移脚本已生成:',
     '  migrations/2026_08_orders_v2.sql', '',
     '  执行前需要你确认:线上表有 210 万行,',
     '  预计锁表 40 秒。现在执行还是等低峰?', '',
     '❯ 待输入…'].join('\n'));
}

// ---------- onboarding ----------

async function handleSignup(req, env, url) {
  if (req.method !== 'POST') return json({ error: 'POST only' }, 405);
  const b = await readBody(req);
  const invite = (b.invite || '').trim();
  // App Review 演示租户:专用邀请码进入预置了模拟数据的固定命名空间,
  // 不产生新租户。数据由 seedDemo 维持新鲜。
  if (env.REVIEW_CODE && env.DEMO_SECRET && invite === env.REVIEW_CODE) {
    await seedDemo(env);
    return json({
      pairing_code: btoa(JSON.stringify({ u: url.origin, s: env.DEMO_SECRET })),
      demo: true,
    });
  }
  if (!env.INVITE_CODE || invite !== env.INVITE_CODE) {
    return json({ error: 'bad invite code' }, 403);
  }
  const raw = crypto.getRandomValues(new Uint8Array(24));
  const secret = [...raw].map((x) => x.toString(16).padStart(2, '0')).join('');
  const uid = (await sha256hex(secret)).slice(0, 16);
  await kvPut(env, `u/${uid}`, 'meta/user', JSON.stringify({ created: Date.now() }));
  const origin = url.origin;
  const pairingCode = btoa(JSON.stringify({ u: origin, s: secret }));
  return json({
    pairing_code: pairingCode,
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

// ---------- router ----------

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    const path = url.pathname;

    if (path === '/api/installer') return handleInstaller(url);
    if (path === '/api/signup') return handleSignup(req, env, url);

    if (path.startsWith('/api/')) {
      const n = await ns(req);
      if (!n) return json({ error: 'unauthorized' }, 401);
      // 演示租户读状态时懒播种:即使 cron 停了,审核员看到的也永远新鲜。
      if (path === '/api/state' && req.method === 'GET' && n === await demoNs(env)) {
        await seedDemo(env);
      }
      if (path === '/api/token') return handleToken(req, env, n);
      if (path === '/api/state') return handleState(req, env, n);
      if (path === '/api/command') return handleCommand(req, env, n, url);
      if (path === '/api/capture') return handleCapture(req, env, n, url);
      if (path === '/api/decision') return handleDecision(req, env, n, url);
      if (path === '/api/push') return handlePush(req, env, n);
      return json({ error: 'not found' }, 404);
    }
    return env.ASSETS.fetch(req);
  },

  // 每 5 分钟刷新演示租户,保证锁屏/面板计时和状态轮转是"活"的。
  async scheduled(event, env, ctx) {
    ctx.waitUntil(seedDemo(env));
  },
};
