// In-memory stand-in for the D1 `kv` table, covering the statement shapes the
// Worker uses. Unknown SQL throws, so a new query can't silently pass a test.
/// `jitter: true` delays every statement 1–15 ms and runs each batch as one
/// uninterruptible unit (a D1 batch is a transaction), so tests can race
/// concurrent requests the way production interleaves them.
export function fakeD1({ jitter = false } = {}) {
  let queue = Promise.resolve();
  const locked = (fn) => { const p = queue.then(fn); queue = p.catch(() => {}); return p; };
  const pause = () => (jitter ? new Promise((r) => setTimeout(r, 1 + Math.random() * 14)) : null);
  const rows = new Map();
  const key = (ns, k) => `${ns}|${k}`;
  const inRange = (r, ns, lo, hi) => r.ns === ns && r.k >= lo && r.k < hi;
  function exec(sql, args) {
    if (sql.startsWith('SELECT v, ts FROM kv WHERE ns=? AND k=?')) {
      return { first: rows.get(key(args[0], args[1])) || null };
    }
    if (sql.startsWith('SELECT k, v, ts FROM kv WHERE ns=? AND k>=? AND k<?')) {
      return { all: [...rows.values()].filter((r) => inRange(r, ...args)) };
    }
    if (sql.includes("VALUES (?,'meta/rev'")) {
      const [ns, ts] = args;
      const r = rows.get(key(ns, 'meta/rev'));
      const v = String(r ? Number(r.v) + 1 : 1);
      rows.set(key(ns, 'meta/rev'), { ns, k: 'meta/rev', v, ts });
      return { first: { v }, changes: 1 };
    }
    const revOf = (ns) => Number((rows.get(key(ns, 'meta/rev')) || { v: 0 }).v);
    if (sql.startsWith('INSERT OR IGNORE INTO kv') && sql.includes("k='meta/rev'")) {
      const [ns, k, v, revNs] = args;
      if (rows.has(key(ns, k))) return { changes: 0 };
      rows.set(key(ns, k), { ns, k, v, ts: revOf(revNs) });
      return { changes: 1 };
    }
    if (sql.startsWith('UPDATE kv SET v=?, ts=(SELECT')) {
      const [v, revNs, ns, k, old] = args;
      const r = rows.get(key(ns, k));
      if (!r || r.v !== old) return { changes: 0 };
      rows.set(key(ns, k), { ...r, v, ts: revOf(revNs) });
      return { changes: 1 };
    }
    if (sql.startsWith('SELECT ns, k, v, ts FROM kv WHERE k>=? AND k<?')) {
      const status = (r) => { try { return JSON.parse(r.v).status; } catch { return ''; } };
      return { all: [...rows.values()].filter((r) => r.k >= args[0] && r.k < args[1]
        && ['queued', 'delivering'].includes(status(r))) };
    }
    if (sql.includes("'meta/rev'") && sql.includes('RETURNING v')) {
      const [ns, ts] = args;
      const r = rows.get(key(ns, 'meta/rev'));
      const v = String(r ? Number(r.v) + 1 : 1);
      rows.set(key(ns, 'meta/rev'), { ns, k: 'meta/rev', v, ts });
      return { first: { v }, changes: 1 };
    }
    if (sql === 'UPDATE kv SET v=? WHERE ns=? AND k=? AND v=?') {
      const [v, ns, k, old] = args;
      const r = rows.get(key(ns, k));
      if (!r || r.v !== old) return { changes: 0 };
      rows.set(key(ns, k), { ...r, v });
      return { changes: 1 };
    }
    if (sql.includes("CASE WHEN kv.v='1'")) {
      const [ns, k, ts] = args;
      const r = rows.get(key(ns, k));
      rows.set(key(ns, k), { ns, k, v: r && r.v !== '1' ? r.v : '1', ts });
      return { changes: 1 };
    }
    if (sql.startsWith('INSERT INTO kv') && sql.includes('ON CONFLICT')) {
      const [ns, k, v, ts] = args;
      rows.set(key(ns, k), { ns, k, v, ts });
      return { changes: 1 };
    }
    if (sql.startsWith('INSERT OR IGNORE INTO kv')) {
      const [ns, k, v, ts] = args;
      if (rows.has(key(ns, k))) return { changes: 0 };
      rows.set(key(ns, k), { ns, k, v, ts });
      return { changes: 1 };
    }
    if (sql.startsWith('UPDATE kv SET v=?, ts=? WHERE ns=? AND k=? AND ts=?')) {
      const [v, ts, ns, k, old] = args;
      const r = rows.get(key(ns, k));
      if (!r || r.ts !== old) return { changes: 0 };
      rows.set(key(ns, k), { ns, k, v, ts });
      return { changes: 1 };
    }
    if (sql === 'DELETE FROM kv WHERE ns=? AND k=?') {
      return { changes: rows.delete(key(args[0], args[1])) ? 1 : 0 };
    }
    if (sql === 'DELETE FROM kv WHERE ns=? AND k=? AND ts<=?') {
      const r = rows.get(key(args[0], args[1]));
      if (!r || r.ts > args[2]) return { changes: 0 };
      rows.delete(key(args[0], args[1]));
      return { changes: 1 };
    }
    if (sql === 'DELETE FROM kv WHERE ns=? AND k=? AND ts=?') {
      const r = rows.get(key(args[0], args[1]));
      if (!r || r.ts !== args[2]) return { changes: 0 };
      rows.delete(key(args[0], args[1]));
      return { changes: 1 };
    }
    if (sql === 'DELETE FROM kv WHERE ns=? AND k>=? AND k<? AND v=? AND k<>?') {
      let n = 0;
      for (const [rk, r] of rows) {
        if (inRange(r, args[0], args[1], args[2]) && r.v === args[3] && r.k !== args[4]) { rows.delete(rk); n++; }
      }
      return { changes: n };
    }
    if (sql === 'DELETE FROM kv WHERE ns=? AND k>=? AND k<?') {
      let n = 0;
      for (const [rk, r] of rows) if (inRange(r, ...args)) { rows.delete(rk); n++; }
      return { changes: n };
    }
    throw new Error('fakeD1: unsupported SQL: ' + sql);
  }
  const one = async (sql, args) => { await pause(); return exec(sql, args); };
  const db = {
    rows,
    prepare(sql) {
      return {
        bind(...args) {
          return {
            sql, args,
            async first() { return locked(() => one(sql, args)).then((r) => r.first ?? null); },
            async all() { return locked(() => one(sql, args)).then((r) => ({ results: r.all ?? [] })); },
            async run() { return locked(() => one(sql, args)).then((r) => ({ success: true, meta: { changes: r.changes ?? 0 } })); },
          };
        },
      };
    },
    async batch(stmts) {
      return locked(async () => {
        const out = [];
        for (const st of stmts) {
          const r = await one(st.sql, st.args);
          out.push({ success: true, meta: { changes: r.changes ?? 0 } });
        }
        return out;
      });
    },
  };
  return db;
}

export const SECRET = 'sessionbell-test-secret';

export function caller(worker, env) {
  return (path, body, token = SECRET) => worker.fetch(new Request('https://test.invalid' + path, {
    method: body ? 'POST' : 'GET',
    headers: { 'x-sb-secret': token, 'Content-Type': 'application/json' },
    ...(body ? { body: JSON.stringify(body) } : {}),
  }), env);
}

/// A fresh module instance = a cold Worker isolate with empty module memory.
let isolateSeq = 0;
export async function coldIsolate() {
  return (await import(`../backend-cf/src/worker.js?isolate=${++isolateSeq}`)).default;
}

export async function apnsTestKey() {
  const kp = await crypto.subtle.generateKey({ name: 'ECDSA', namedCurve: 'P-256' }, true, ['sign']);
  const der = new Uint8Array(await crypto.subtle.exportKey('pkcs8', kp.privateKey));
  const b64 = btoa(String.fromCharCode(...der));
  return `-----BEGIN PRIVATE KEY-----\n${b64}\n-----END PRIVATE KEY-----`;
}

/// Replace global fetch with an APNs stub; `respond(call)` returns {status, body}.
export function stubApns(respond = () => ({ status: 200, body: '' })) {
  const calls = [];
  const real = globalThis.fetch;
  globalThis.fetch = async (url, init) => {
    const call = { url: String(url), headers: init.headers, body: init.body };
    calls.push(call);
    const r = respond(call, calls.length);
    return new Response(r.body ?? '', { status: r.status });
  };
  return { calls, restore() { globalThis.fetch = real; } };
}
