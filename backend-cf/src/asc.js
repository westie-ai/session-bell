// App Store Connect API client (read-only) — runs in Workers and in Node ≥ 18.
// Only WebCrypto, fetch and DecompressionStream; no node: imports.
//
// cfg = { keyId, issuerId, pem, vendor? }
//   keyId / issuerId : ASC → Users and Access → Integrations → Team Keys
//   pem              : contents of the AuthKey_<keyId>.p8 file
//   vendor           : ASC → Payments and Financial Reports (top-left); optional,
//                      without it the sales-report based download counts are skipped.

export const APP_ID = '6801045681';
const BASE = 'https://api.appstoreconnect.apple.com';

const b64url = (bytes) => btoa(String.fromCharCode(...new Uint8Array(bytes)))
  .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
const utf8 = (s) => new TextEncoder().encode(s);

async function jwt(cfg) {
  const der = Uint8Array.from(atob(cfg.pem.replace(/-----[^-]+-----/g, '').replace(/\s+/g, '')),
    (c) => c.charCodeAt(0));
  const key = await crypto.subtle.importKey('pkcs8', der, { name: 'ECDSA', namedCurve: 'P-256' }, false, ['sign']);
  const now = Math.floor(Date.now() / 1000);
  const header = b64url(utf8(JSON.stringify({ alg: 'ES256', kid: cfg.keyId, typ: 'JWT' })));
  const claims = b64url(utf8(JSON.stringify({
    iss: cfg.issuerId, iat: now, exp: now + 15 * 60, aud: 'appstoreconnect-v1',
  })));
  const sig = await crypto.subtle.sign({ name: 'ECDSA', hash: 'SHA-256' }, key, utf8(`${header}.${claims}`));
  return `${header}.${claims}.${b64url(sig)}`;
}

async function get(cfg, path, { gzip = false } = {}) {
  const r = await fetch(BASE + path, { headers: {
    Authorization: `Bearer ${await jwt(cfg)}`, Accept: gzip ? 'application/a-gzip' : 'application/json',
  } });
  if (!r.ok) {
    const body = await r.text().catch(() => '');
    const err = new Error(`ASC ${r.status} ${path.split('?')[0]}: ${body.slice(0, 200)}`);
    err.status = r.status;
    throw err;
  }
  if (!gzip) return r.json();
  return new Response(r.body.pipeThrough(new DecompressionStream('gzip'))).text();
}

/** Latest App Store versions with their review state. */
export async function versions(cfg) {
  const j = await get(cfg, `/v1/apps/${APP_ID}/appStoreVersions?limit=3` +
    '&fields[appStoreVersions]=versionString,appStoreState,appVersionState,createdDate,platform');
  return j.data.map((v) => ({
    version: v.attributes.versionString,
    state: v.attributes.appVersionState || v.attributes.appStoreState,
    created: v.attributes.createdDate,
  }));
}

/** Most recent TestFlight / App Store build. */
export async function latestBuild(cfg) {
  const j = await get(cfg, `/v1/builds?filter[app]=${APP_ID}&sort=-uploadedDate&limit=1` +
    '&fields[builds]=version,uploadedDate,processingState,expired');
  const b = j.data[0];
  return b ? { build: b.attributes.version, uploaded: b.attributes.uploadedDate,
    state: b.attributes.processingState } : null;
}

/** Customer reviews, newest first (public content). */
export async function reviews(cfg, limit = 20) {
  const j = await get(cfg, `/v1/apps/${APP_ID}/customerReviews?sort=-createdDate&limit=${limit}`);
  return j.data.map((r) => ({
    rating: r.attributes.rating, title: r.attributes.title || '', body: r.attributes.body || '',
    territory: r.attributes.territory, created: r.attributes.createdDate,
    nick: r.attributes.reviewerNickname || '',
  }));
}

/**
 * Daily first-time downloads and updates for the last `days` days, with a
 * per-storefront-country split, from the Sales Reports (SALES/SUMMARY/DAILY).
 * Apple publishes a day's report the next morning Pacific time and publishes
 * nothing at all for days with zero units, so missing days come back as null.
 */
export async function dailyUnits(cfg, days = 7) {
  if (!cfg.vendor) return null;
  const dates = [];
  for (let i = days; i >= 1; i--) dates.push(new Date(Date.now() - i * 86400e3).toISOString().slice(0, 10));
  return Promise.all(dates.map(async (d) => {
    let downloads = null, updates = null, byCountry = null;
    try {
      const tsv = await get(cfg, '/v1/salesReports?filter[frequency]=DAILY&filter[reportType]=SALES' +
        `&filter[reportSubType]=SUMMARY&filter[reportDate]=${d}&filter[vendorNumber]=${cfg.vendor}`, { gzip: true });
      const [head, ...rows] = tsv.trim().split('\n').map((l) => l.split('\t'));
      const col = (name) => head.indexOf(name);
      const iType = col('Product Type Identifier'), iUnits = col('Units'), iApp = col('Apple Identifier');
      const iCountry = col('Country Code');
      downloads = 0; updates = 0; byCountry = {};
      for (const r of rows) {
        if (iApp >= 0 && r[iApp] !== APP_ID) continue;
        const t = r[iType] || '', u = Number(r[iUnits]) || 0;
        const cc = (iCountry >= 0 && r[iCountry]) || '??';
        const kind = /^(1|1F|1T|1E|1EP|1EU|F1)$/.test(t) ? 'downloads'
          : /^(7|7F|7T|F7)$/.test(t) ? 'updates' : null;
        if (!kind) continue;
        if (kind === 'downloads') downloads += u; else updates += u;
        byCountry[cc] ??= { downloads: 0, updates: 0 };
        byCountry[cc][kind] += u;
      }
    } catch (e) {
      if (e.status !== 404) throw e;
    }
    return { day: d, downloads, updates, byCountry };
  }));
}

/** Sum a dailyUnits result per country, sorted by first-time downloads. */
export function countryTotals(days) {
  const acc = {};
  for (const d of days || []) {
    for (const [cc, v] of Object.entries(d.byCountry || {})) {
      acc[cc] ??= { downloads: 0, updates: 0 };
      acc[cc].downloads += v.downloads; acc[cc].updates += v.updates;
    }
  }
  return Object.entries(acc).map(([cc, v]) => ({ cc, ...v }))
    .sort((a, b) => b.downloads - a.downloads || b.updates - a.updates);
}

/** Everything the board needs, tolerating partial failures. cfg null → not configured. */
export async function snapshot(cfg) {
  if (!cfg) return { configured: false };
  const res = { configured: true, errors: [], fetchedAt: Date.now() };
  const tasks = { versions, latestBuild, reviews, dailyUnits };
  await Promise.all(Object.entries(tasks).map(async ([k, fn]) => {
    try { res[k] = await fn(cfg); } catch (e) { res.errors.push(`${k}: ${e.message}`); res[k] = null; }
  }));
  res.hasVendor = !!cfg.vendor;
  return res;
}

/** Build a cfg from Worker env (secret ASC_KEY = .p8 contents; vars for the rest). */
export function configFromEnv(env) {
  if (!env.ASC_KEY || !env.ASC_KEY_ID || !env.ASC_ISSUER_ID) return null;
  return { keyId: env.ASC_KEY_ID, issuerId: env.ASC_ISSUER_ID, pem: env.ASC_KEY, vendor: env.ASC_VENDOR || null };
}
