// App Store Connect API client for the usage board (read-only).
//
// Config, first match wins:
//   env ASC_KEY_ID / ASC_ISSUER_ID / ASC_KEY_PATH / ASC_VENDOR
//   ~/.sessionbell/asc.json  { "key_id", "issuer_id", "p8_path", "vendor_number" }
//
// The key must be an App Store Connect API key (ASC → Users and Access →
// Integrations → Team Keys), not an APNs key from the developer portal.
// vendor_number is optional; without it the daily-download chart is skipped
// (it comes from Sales Reports, which are keyed by vendor). Find it at
// ASC → Payments and Financial Reports, top-left under the team name.

import { createSign } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { gunzipSync } from 'node:zlib';

export const APP_ID = '6801045681';
const BASE = 'https://api.appstoreconnect.apple.com';

export function loadConfig() {
  const env = process.env;
  if (env.ASC_KEY_ID && env.ASC_ISSUER_ID && env.ASC_KEY_PATH) {
    return { keyId: env.ASC_KEY_ID, issuerId: env.ASC_ISSUER_ID,
      p8Path: env.ASC_KEY_PATH, vendor: env.ASC_VENDOR || null };
  }
  const p = join(homedir(), '.sessionbell', 'asc.json');
  if (!existsSync(p)) return null;
  const j = JSON.parse(readFileSync(p, 'utf8'));
  if (!j.key_id || !j.issuer_id || !j.p8_path) return null;
  return { keyId: j.key_id, issuerId: j.issuer_id,
    p8Path: j.p8_path.replace(/^~/, homedir()), vendor: j.vendor_number || null };
}

const b64url = (buf) => Buffer.from(buf).toString('base64url');

function jwt(cfg) {
  const now = Math.floor(Date.now() / 1000);
  const header = b64url(JSON.stringify({ alg: 'ES256', kid: cfg.keyId, typ: 'JWT' }));
  const claims = b64url(JSON.stringify({
    iss: cfg.issuerId, iat: now, exp: now + 15 * 60, aud: 'appstoreconnect-v1',
  }));
  const signer = createSign('SHA256');
  signer.update(`${header}.${claims}`);
  const sig = signer.sign({ key: readFileSync(cfg.p8Path, 'utf8'), dsaEncoding: 'ieee-p1363' });
  return `${header}.${claims}.${b64url(sig)}`;
}

async function get(cfg, path, { raw = false } = {}) {
  const r = await fetch(BASE + path, { headers: {
    Authorization: `Bearer ${jwt(cfg)}`, Accept: raw ? 'application/a-gzip' : 'application/json',
  } });
  if (!r.ok) {
    const body = await r.text().catch(() => '');
    const err = new Error(`ASC ${r.status} ${path}: ${body.slice(0, 300)}`);
    err.status = r.status;
    throw err;
  }
  return raw ? Buffer.from(await r.arrayBuffer()) : r.json();
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
 * Daily first-time downloads and updates for the last `days` days, from the
 * Sales Reports (SALES / SUMMARY / DAILY). Apple publishes a day's report the
 * following morning Pacific time, so today is usually missing and yesterday
 * may 404 until ~9am PT; missing days are returned as null.
 */
export async function dailyUnits(cfg, days = 7) {
  if (!cfg.vendor) return null;
  const out = [];
  for (let i = days; i >= 1; i--) {
    const d = new Date(Date.now() - i * 86400e3).toISOString().slice(0, 10);
    let downloads = null, updates = null, byCountry = null;
    try {
      const gz = await get(cfg, '/v1/salesReports?filter[frequency]=DAILY&filter[reportType]=SALES' +
        `&filter[reportSubType]=SUMMARY&filter[reportDate]=${d}&filter[vendorNumber]=${cfg.vendor}`, { raw: true });
      const tsv = gunzipSync(gz).toString('utf8');
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
      // 404 = report not yet available (or no sales that day); anything else is real.
      if (e.status !== 404) throw e;
    }
    out.push({ day: d, downloads, updates, byCountry });
  }
  return out;
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

/** Everything the board needs, tolerating partial failures. */
export async function snapshot() {
  const cfg = loadConfig();
  if (!cfg) return { configured: false };
  const res = { configured: true, errors: [] };
  const tasks = { versions, latestBuild, reviews, dailyUnits };
  await Promise.all(Object.entries(tasks).map(async ([k, fn]) => {
    try { res[k] = await fn(cfg); } catch (e) { res.errors.push(`${k}: ${e.message}`); res[k] = null; }
  }));
  res.hasVendor = !!cfg.vendor;
  return res;
}
