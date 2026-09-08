#!/usr/bin/env node
// SessionBell usage board.
//
// Pulls aggregate usage out of the production D1 database and renders a
// single self-contained HTML page. Only counts, timestamps and short account
// ids leave the database — never `detail`, `capture` or host names of other
// users — so the page is safe to share.
//
//   node backend-cf/scripts/board.mjs            → build/board.html
//   node backend-cf/scripts/board.mjs out.html   → custom path
//
// Needs a logged-in wrangler (`npx wrangler whoami`) and a terminal with
// network access (the sandboxed Claude shell reports "fetch failed").

import { execFileSync } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { snapshot as ascSnapshot, countryTotals, APP_ID as APP_ID_LABEL } from './asc.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const backend = resolve(here, '..');
const out = resolve(process.argv[2] || resolve(backend, '..', 'build', 'board.html'));

const DAY = 86400e3;
const now = Date.now();
const since7d = now - 7 * DAY;

function d1(sql) {
  const raw = execFileSync('npx', ['wrangler', 'd1', 'execute', 'sessionbell',
    '--remote', '--json', '--command', sql], { cwd: backend, encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'inherit'], maxBuffer: 64 * 1024 * 1024 });
  const parsed = JSON.parse(raw);
  if (parsed.error) throw new Error(parsed.error.text || JSON.stringify(parsed.error));
  return parsed[0].results;
}

// ---------- queries ----------

const accounts = d1(`
  SELECT ns,
    SUM(k LIKE 'devices/%')                      AS iphones,
    SUM(k LIKE 'devices/%' AND ts > ${since7d})  AS iphones_7d,
    SUM(k LIKE 'state/%')                        AS macs,
    SUM(k LIKE 'state/%' AND ts > ${now - DAY})  AS macs_24h,
    SUM(k LIKE 'pts/%')                          AS pts,
    SUM(k LIKE 'dash/%' OR k LIKE 'dashended/%') AS live_activities,
    SUM(k LIKE 'command/%')                      AS commands,
    SUM(k LIKE 'decision/%')                     AS decisions,
    SUM(k LIKE 'capture/%')                      AS captures,
    MIN(ts) AS first_seen, MAX(ts) AS last_seen
  FROM kv WHERE ns LIKE 'u/%' GROUP BY ns ORDER BY MAX(ts) DESC`);

const daily = d1(`
  SELECT date(ts/1000,'unixepoch') AS day,
    SUM(k LIKE 'dash/%' OR k LIKE 'dashended/%') AS live_activities,
    SUM(k LIKE 'command/%')  AS commands,
    SUM(k LIKE 'decision/%') AS decisions,
    SUM(k LIKE 'capture/%')  AS captures
  FROM kv WHERE ns LIKE 'u/%' AND ts > ${now - 8 * DAY} GROUP BY day`);

// Session status snapshot: parse the state docs locally and keep only counts.
const stateRows = d1(`SELECT ns, v, ts FROM kv WHERE k LIKE 'state/%'`);
const sessionStatus = { running: 0, waiting: 0, done: 0, other: 0 };
let sessionsTotal = 0;
const macsOnlineByNs = new Map();
for (const r of stateRows) {
  let doc;
  try { doc = JSON.parse(r.v); } catch { continue; }
  const sessions = Object.values(doc.sessions || {});
  if (r.ts > now - DAY) {
    sessionsTotal += sessions.length;
    for (const s of sessions) {
      const key = ['running', 'waiting', 'done'].includes(s.status) ? s.status : 'other';
      sessionStatus[key]++;
    }
    macsOnlineByNs.set(r.ns, (macsOnlineByNs.get(r.ns) || 0) + 1);
  }
}

// App 内反馈(sys 命名空间,/api/feedback 写入),最近 30 条。
const feedbackRows = d1(`SELECT k, v, ts FROM kv WHERE ns='sys' AND k>='feedback/' AND k<'feedback/\uffff' ORDER BY ts DESC LIMIT 30`);
const feedback = feedbackRows.map((r) => { try { return { ...JSON.parse(r.v), ts: r.ts }; } catch { return null; } }).filter(Boolean);

// App Store Connect(可选:没配 ~/.sessionbell/asc.json 就渲染接入说明)。
const asc = await ascSnapshot();
if (asc.errors?.length) console.error('ASC:', asc.errors.join(' | '));

// ---------- derive ----------

function classify(a) {
  const stale = a.last_seen < since7d;
  if (a.macs > 0 && a.iphones > 0) return stale ? 'idle' : 'paired';
  if (a.iphones > 0) return stale ? 'idle' : 'phone-only';
  return 'signup-only';
}
const LABEL = {
  paired: '配对完成', 'phone-only': '未接 Mac', 'signup-only': '仅注册', idle: '沉寂',
};
for (const a of accounts) { a.kind = classify(a); a.id = a.ns.slice(2, 8); }

const count = (kind) => accounts.filter((a) => a.kind === kind).length;
// Each step is a subset of the one before it, so the bars only ever shrink.
const steps = [
  ['注册账号', () => true, '有 meta/user 记录'],
  ['iPhone 已登录', (a) => a.iphones > 0, '上报过推送 token'],
  ['Mac 已接入', (a) => a.macs > 0, 'hook 上报过状态'],
  ['用过 Live Activity', (a) => a.live_activities > 0, '近 7 天，且已接 Mac'],
  ['在手机上审批过', (a) => a.decisions > 0, '近 7 天'],
];
const funnel = [];
let pool = accounts;
for (const [label, pred, hint] of steps) {
  pool = pool.filter(pred);
  funnel.push({ label, n: pool.length, hint });
}

const active7d = accounts.filter((a) => a.last_seen > since7d).length;
const macsOnline = accounts.reduce((s, a) => s + a.macs_24h, 0);
const iphones7d = accounts.reduce((s, a) => s + a.iphones_7d, 0);

const days = [];
for (let i = 6; i >= 0; i--) {
  const d = new Date(now - i * DAY).toISOString().slice(0, 10);
  const row = daily.find((r) => r.day === d) || {};
  days.push({ day: d, live_activities: row.live_activities || 0,
    commands: row.commands || 0, decisions: row.decisions || 0, captures: row.captures || 0 });
}

// ---------- render ----------

const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const fmtDate = (ms) => new Date(ms).toISOString().slice(0, 10);
const ago = (ms) => {
  const h = (now - ms) / 3600e3;
  if (h < 1) return `${Math.max(1, Math.round(h * 60))} 分钟前`;
  if (h < 48) return `${Math.round(h)} 小时前`;
  return `${Math.round(h / 24)} 天前`;
};
const genAt = new Date(now).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false });

function miniChart(title, key, note) {
  const W = 280, H = 96, padL = 4, padB = 22, padT = 18;
  const max = Math.max(1, ...days.map((d) => d[key]));
  const bw = (W - padL * 2) / 7;
  const bars = days.map((d, i) => {
    const h = (d[key] / max) * (H - padT - padB);
    const x = padL + i * bw + 5, y = H - padB - h;
    const last = i === 6;
    return `<g class="bar" tabindex="0" aria-label="${d.day}：${d[key]}">
      <rect class="hit" x="${padL + i * bw}" y="0" width="${bw}" height="${H}" fill="transparent"/>
      <rect class="mark${last ? ' last' : ''}" x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${(bw - 10).toFixed(1)}" height="${h.toFixed(1)}" rx="3"/>
      <text class="val" x="${(x + (bw - 10) / 2).toFixed(1)}" y="${(y - 4).toFixed(1)}" text-anchor="middle">${d[key]}</text>
      <text class="tick" x="${(padL + i * bw + bw / 2).toFixed(1)}" y="${H - 6}" text-anchor="middle">${d.day.slice(5).replace('-', '/')}</text>
    </g>`;
  }).join('');
  const total = days.reduce((s, d) => s + d[key], 0);
  return `<figure class="mini">
    <figcaption><span class="mini-title">${title}</span><span class="mini-total">${total}<small>/7d</small></span></figcaption>
    <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${title}，最近 7 天每日次数">
      <line class="base" x1="${padL}" x2="${W - padL}" y1="${H - padB + 0.5}" y2="${H - padB + 0.5}"/>
      ${bars}
    </svg>
    ${note ? `<p class="mini-note">${note}</p>` : ''}
  </figure>`;
}

const accountRows = accounts.map((a) => `<tr class="k-${a.kind}">
  <td class="mono">${a.id}</td>
  <td><span class="pill ${a.kind}">${LABEL[a.kind]}</span></td>
  <td class="num">${a.iphones}</td>
  <td class="num">${a.macs}${a.macs_24h ? `<span class="online" title="24 小时内在线 ${a.macs_24h} 台"></span>` : ''}</td>
  <td class="num">${a.live_activities || '·'}</td>
  <td class="num">${a.commands || '·'}</td>
  <td class="num">${a.decisions || '·'}</td>
  <td class="mono dim">${fmtDate(a.first_seen)}</td>
  <td class="mono" title="${new Date(a.last_seen).toISOString()}">${ago(a.last_seen)}</td>
</tr>`).join('');

const funnelMax = funnel[0].n || 1;
const funnelHtml = funnel.map((f, i) => {
  const prev = i ? funnel[i - 1].n : f.n;
  const drop = prev - f.n;
  return `<li>
    <div class="f-head"><span class="f-label">${f.label}</span><span class="f-n">${f.n}</span></div>
    <div class="f-track"><div class="f-fill" style="width:${(f.n / funnelMax * 100).toFixed(1)}%"></div></div>
    <div class="f-foot"><span>${f.hint}</span>${i ? `<span class="${drop ? 'drop' : ''}">${drop ? `−${drop}` : '无流失'}</span>` : ''}</div>
  </li>`;
}).join('');

const STATE_ZH = {
  READY_FOR_SALE: ['已上架', 'good'], READY_FOR_DISTRIBUTION: ['已上架', 'good'],
  IN_REVIEW: ['审核中', 'warn'], WAITING_FOR_REVIEW: ['等待审核', 'warn'],
  PENDING_DEVELOPER_RELEASE: ['待手动发布', 'warn'], PREPARE_FOR_SUBMISSION: ['准备提交', 'mute'],
  REJECTED: ['被拒', 'crit'], DEVELOPER_REJECTED: ['开发者撤回', 'mute'], METADATA_REJECTED: ['元数据被拒', 'crit'],
  PROCESSING_FOR_DISTRIBUTION: ['处理中', 'warn'], REPLACED_WITH_NEW_VERSION: ['已被新版本替代', 'mute'],
  DEVELOPER_REMOVED_FROM_SALE: ['已下架', 'mute'], REMOVED_FROM_SALE: ['已下架', 'crit'],
};
const stateChip = (st) => { const [t, c] = STATE_ZH[st] || [st, 'mute']; return `<span class="chip ${c}">${esc(t)}</span>`; };
const stars = (n) => '★'.repeat(n) + '☆'.repeat(5 - n);

function ascSection() {
  if (!asc.configured) {
    return `<div class="panel setup">
      <p><b>还没接 App Store Connect API。</b>接上后这里显示版本审核状态、最新构建、评分评论和每日下载量。</p>
      <ol>
        <li>App Store Connect → 用户和访问 → 集成 → App Store Connect API → 团队密钥,新建一个 <b>Developer</b> 或 <b>Finance</b> 角色的密钥(要看下载量需要 Finance / Sales 权限),下载 .p8。</li>
        <li>把 Key ID、Issuer ID、.p8 路径写到 <code>~/.sessionbell/asc.json</code>:<br>
          <code>{ "key_id": "XXXXXXXXXX", "issuer_id": "xxxxxxxx-xxxx-…", "p8_path": "~/.sessionbell/AuthKey_XXXXXXXXXX.p8", "vendor_number": "8xxxxxxx" }</code></li>
        <li>vendor_number 在 App Store Connect → 付款和财务报告 左上角;不填就只跳过下载量。</li>
      </ol>
    </div>`;
  }
  const vers = (asc.versions || []).slice(0, 3).map((v) =>
    `<li><span class="mono">${esc(v.version)}</span>${stateChip(v.state)}<span class="dim">${fmtDate(Date.parse(v.created))}</span></li>`).join('');
  const build = asc.latestBuild
    ? `<li><span class="mono">build ${esc(asc.latestBuild.build)}</span><span class="chip mute">${esc(asc.latestBuild.state)}</span><span class="dim">${ago(Date.parse(asc.latestBuild.uploaded))}上传</span></li>`
    : '';
  const rv = asc.reviews || [];
  const avg = rv.length ? (rv.reduce((s, r) => s + r.rating, 0) / rv.length).toFixed(1) : null;
  const reviewList = rv.slice(0, 5).map((r) => `<li>
      <div class="rv-head"><span class="rv-stars">${stars(r.rating)}</span><span class="rv-title">${esc(r.title)}</span><span class="dim">${esc(r.territory)} · ${fmtDate(Date.parse(r.created))}</span></div>
      ${r.body ? `<p class="rv-body">${esc(r.body.slice(0, 240))}${r.body.length > 240 ? '…' : ''}</p>` : ''}
    </li>`).join('');
  let units = '';
  if (asc.dailyUnits) {
    const rows = asc.dailyUnits;
    const max = Math.max(1, ...rows.map((r) => (r.downloads ?? 0) + (r.updates ?? 0)));
    const W = 280, H = 96, padL = 4, padB = 22, padT = 18, bw = (W - padL * 2) / rows.length;
    const bars = rows.map((r, i) => {
      const x = padL + i * bw + 5, w = bw - 10;
      const tick = `<text class="tick" x="${(padL + i * bw + bw / 2).toFixed(1)}" y="${H - 6}" text-anchor="middle">${r.day.slice(5).replace('-', '/')}</text>`;
      if (r.downloads == null) return `<g><text class="tick" x="${(x + w / 2).toFixed(1)}" y="${H - padB - 4}" text-anchor="middle">–</text>${tick}</g>`;
      const hD = (r.downloads / max) * (H - padT - padB), hU = (r.updates / max) * (H - padT - padB);
      const yD = H - padB - hD, yU = yD - hU - (hU ? 2 : 0);
      return `<g class="bar" tabindex="0" aria-label="${r.day}:下载 ${r.downloads},更新 ${r.updates}">
        <rect class="hit" x="${padL + i * bw}" y="0" width="${bw}" height="${H}" fill="transparent"/>
        <rect class="mark${i === rows.length - 1 ? ' last' : ''}" x="${x.toFixed(1)}" y="${yD.toFixed(1)}" width="${w.toFixed(1)}" height="${hD.toFixed(1)}" rx="3"/>
        <rect class="mark upd" x="${x.toFixed(1)}" y="${yU.toFixed(1)}" width="${w.toFixed(1)}" height="${hU.toFixed(1)}" rx="3"/>
        <text class="val" x="${(x + w / 2).toFixed(1)}" y="${(yU - 4).toFixed(1)}" text-anchor="middle">${r.downloads}</text>
        ${tick}
      </g>`;
    }).join('');
    const tot = rows.reduce((s, r) => s + (r.downloads || 0), 0);
    units = `<figure class="mini">
      <figcaption><span class="mini-title">首次下载</span><span class="mini-total">${tot}<small>/7d</small></span></figcaption>
      <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="每日首次下载与更新"><line class="base" x1="${padL}" x2="${W - padL}" y1="${H - padB + 0.5}" y2="${H - padB + 0.5}"/>${bars}</svg>
      <p class="mini-note">实心 = 首次下载,浅色叠加 = 更新。"–" = Apple 没出那天的报告:当天零下载,或昨天的还没生成(次日太平洋时间上午才出)。</p>
    </figure>`;
  } else if (!asc.hasVendor) {
    units = `<p class="mini-note">asc.json 里没有 vendor_number,跳过下载量。</p>`;
  }
  // 按地区:近 7 天首次下载,营销投放看这个。
  let regions = '';
  if (asc.dailyUnits) {
    const tot = countryTotals(asc.dailyUnits);
    const sum = tot.reduce((s, r) => s + r.downloads, 0) || 1;
    const max = Math.max(1, ...tot.map((r) => r.downloads));
    const NAME = { US: '美国', CN: '中国大陆', TW: '台湾', HK: '香港', KR: '韩国', JP: '日本', GB: '英国', TR: '土耳其',
      DE: '德国', FR: '法国', SG: '新加坡', CA: '加拿大', AU: '澳大利亚', IN: '印度', MY: '马来西亚', TH: '泰国',
      VN: '越南', ID: '印尼', NL: '荷兰', BR: '巴西', RU: '俄罗斯', MO: '澳门' };
    const flag = (cc) => /^[A-Z]{2}$/.test(cc) ? String.fromCodePoint(...[...cc].map((c) => 0x1F1E6 + c.charCodeAt(0) - 65)) : '';
    const rows = tot.map((r) => `<li>
      <span class="rg-name">${flag(r.cc)} ${esc(NAME[r.cc] || r.cc)}<span class="dim mono"> ${esc(r.cc)}</span></span>
      <span class="rg-track"><span class="rg-fill" style="width:${(r.downloads / max * 100).toFixed(1)}%"></span></span>
      <span class="rg-n">${r.downloads}<small>${(r.downloads / sum * 100).toFixed(0)}%</small></span>
      <span class="rg-upd dim">${r.updates ? `更新 ${r.updates}` : ''}</span>
    </li>`).join('');
    const daysWithData = asc.dailyUnits.filter((d) => d.downloads != null).map((d) => d.day.slice(5).replace('-', '/'));
    regions = `<div class="panel regions">
      <div class="panel-h">首次下载 · 按地区 <span class="dim">近 7 天有报告的日子:${daysWithData.join('、') || '无'}</span></div>
      ${tot.length ? `<ol class="rg">${rows}</ol>` : '<p class="mini-note">这几天没有下载记录。</p>'}
      <p class="mini-note">地区 = 用户 Apple ID 所属商店,不是 IP 所在地。</p>
    </div>`;
  }
  return `<div class="asc-grid">
    <div class="panel">
      <div class="panel-h">版本与构建</div>
      <ul class="kv">${vers}${build}</ul>
    </div>
    <div class="panel">
      <div class="panel-h">评分与评论 <span class="dim">${avg ? `平均 ${avg} · ${rv.length} 条` : '还没有评论'}</span></div>
      <ul class="reviews">${reviewList}</ul>
    </div>
    ${units}
  </div>
  ${regions}
  ${asc.errors?.length ? `<p class="mini-note">部分请求失败:${asc.errors.map(esc).join(' · ')}</p>` : ''}`;
}

const KIND_ZH = { bug: ['问题', 'crit'], idea: ['建议', 'good'], feedback: ['反馈', 'mute'] };
function feedbackSection() {
  if (!feedback.length) return `<div class="panel setup"><p>还没有收到反馈。App 设置页 → 发送反馈 会写到这里,并转发到 <span class="mono">begin1314@gmail.com</span>。</p></div>`;
  return `<ul class="fb">${feedback.map((f) => {
    const [t, c] = KIND_ZH[f.kind] || KIND_ZH.feedback;
    return `<li class="panel">
      <div class="fb-head"><span class="chip ${c}">${t}</span><span class="mono">${esc((f.ns || '').slice(2, 8))}</span><span class="dim">${esc(f.app_version || '')} (${esc(f.build || '')}) · ${esc(f.os || '')} · ${esc(f.device || '')}</span><span class="dim fb-time" title="${new Date(f.ts).toISOString()}">${ago(f.ts)}</span></div>
      <p class="fb-text">${esc(f.text)}</p>
      ${f.contact ? `<p class="fb-contact">联系:<span class="mono">${esc(f.contact)}</span></p>` : ''}
    </li>`;
  }).join('')}</ul>`;
}

const html = `<title>SessionBell Pulse</title>
<meta name="description" content="SessionBell 线上用量看板">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans+Condensed:wght@500;600&display=swap">
<style>
:root{
  --bg:#F2F3F6;--surface:#FFFFFF;--surface-2:#F7F8FA;--line:#E1E4EA;--line-strong:#C9CED8;
  --ink:#171A22;--ink-2:#4B5261;--ink-3:#7C8494;
  --accent:#A87A1F;--accent-ink:#7A5712;--accent-soft:#F4EBD6;
  --good:#2F8F5B;--good-soft:#E2F2E8;--warn:#C26D12;--warn-soft:#FBEBD8;--crit:#B9443C;--crit-soft:#F8E3E1;--mute-soft:#ECEEF2;
  --mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,monospace;
  --sans:"IBM Plex Sans","PingFang SC","Hiragino Sans GB","Microsoft YaHei",system-ui,sans-serif;
  --cond:"IBM Plex Sans Condensed","IBM Plex Sans","PingFang SC",system-ui,sans-serif;
}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){
  --bg:#111318;--surface:#1A1D25;--surface-2:#20242E;--line:#2B303C;--line-strong:#3C4352;
  --ink:#E8EAF0;--ink-2:#AEB4C2;--ink-3:#7F8798;
  --accent:#D9A93F;--accent-ink:#E6BC5C;--accent-soft:#2E2816;
  --good:#5CBF87;--good-soft:#17301F;--warn:#E5964A;--warn-soft:#332413;--crit:#E07068;--crit-soft:#3A1E1C;--mute-soft:#262A34;
}}
:root[data-theme="dark"]{
  --bg:#111318;--surface:#1A1D25;--surface-2:#20242E;--line:#2B303C;--line-strong:#3C4352;
  --ink:#E8EAF0;--ink-2:#AEB4C2;--ink-3:#7F8798;
  --accent:#D9A93F;--accent-ink:#E6BC5C;--accent-soft:#2E2816;
  --good:#5CBF87;--good-soft:#17301F;--warn:#E5964A;--warn-soft:#332413;--crit:#E07068;--crit-soft:#3A1E1C;--mute-soft:#262A34;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 var(--sans);-webkit-font-smoothing:antialiased}
.wrap{max-width:1080px;margin:0 auto;padding:36px 28px 64px}
header{display:flex;flex-wrap:wrap;align-items:flex-end;justify-content:space-between;gap:12px 24px;margin-bottom:28px}
h1{margin:0;font:600 26px/1.1 var(--cond);letter-spacing:-.01em;display:flex;align-items:center;gap:10px}
h1 .bell{width:22px;height:22px;color:var(--accent)}
.sub{color:var(--ink-3);font-size:13px;margin-top:6px}
.sub .mono{color:var(--ink-2)}
.stamp{font:13px var(--mono);color:var(--ink-3);text-align:right}
.stamp b{display:block;color:var(--ink-2);font-weight:500}
h2{font:600 12px var(--sans);letter-spacing:.08em;text-transform:uppercase;color:var(--ink-3);margin:32px 0 12px}
h2 small{font-weight:400;letter-spacing:0;text-transform:none;margin-left:8px}
.mono{font-family:var(--mono)}
.num{font-family:var(--mono);font-variant-numeric:tabular-nums;text-align:right}
.dim{color:var(--ink-3)}

.tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--line);border:1px solid var(--line);border-radius:8px;overflow:hidden}
.tile{background:var(--surface);padding:16px 18px 14px}
.tile .l{font-size:12px;color:var(--ink-3);letter-spacing:.02em}
.tile .v{font:500 34px/1.1 var(--mono);font-variant-numeric:tabular-nums;margin:6px 0 4px}
.tile .v small{font-size:15px;color:var(--ink-3);margin-left:2px}
.tile .c{font-size:12px;color:var(--ink-2)}
.tile.hero .v{color:var(--accent-ink)}

.two{display:grid;grid-template-columns:minmax(0,5fr) minmax(0,7fr);gap:20px;align-items:start}
.panel{background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:18px 20px}
.funnel{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:14px}
.f-head{display:flex;justify-content:space-between;align-items:baseline}
.f-label{font-weight:500}
.f-n{font:500 18px var(--mono);font-variant-numeric:tabular-nums}
.f-track{height:8px;background:var(--mute-soft);border-radius:4px;margin:6px 0 4px;overflow:hidden}
.f-fill{height:100%;background:var(--accent);border-radius:4px;min-width:2px}
.f-foot{display:flex;justify-content:space-between;font-size:12px;color:var(--ink-3)}
.f-foot .drop{color:var(--warn);font-family:var(--mono)}

.status{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:8px}
.st{border:1px solid var(--line);border-radius:6px;padding:10px 12px;background:var(--surface-2)}
.st .l{font-size:12px;color:var(--ink-3);display:flex;align-items:center;gap:6px}
.st .dot{width:8px;height:8px;border-radius:50%;display:inline-block}
.st .v{font:500 22px var(--mono);font-variant-numeric:tabular-nums;margin-top:2px}
.dot.running{background:var(--good)}.dot.waiting{background:var(--warn)}.dot.done{background:var(--ink-3)}
.status-note{font-size:12px;color:var(--ink-3);margin:10px 0 0}

.minis{display:grid;grid-template-columns:repeat(3,1fr);gap:20px}
.mini{margin:0;background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:14px 16px 10px}
.mini figcaption{display:flex;justify-content:space-between;align-items:baseline;gap:8px;margin-bottom:6px;white-space:nowrap}
.mini-title{overflow:hidden;text-overflow:ellipsis}
.mini-title{font-weight:500}
.mini-total{font:500 18px var(--mono);font-variant-numeric:tabular-nums}
.mini-total small{font-size:11px;color:var(--ink-3);font-family:var(--sans)}
.mini svg{width:100%;height:auto;display:block;overflow:visible}
.mini .base{stroke:var(--line-strong);stroke-width:1}
.mini .mark{fill:var(--accent);opacity:.55}
.mini .mark.last{opacity:1}
.mini .bar:hover .mark,.mini .bar:focus .mark{opacity:1}
.mini .bar:focus{outline:none}
.mini .val{font:11px var(--mono);fill:var(--ink-2);opacity:0}
.mini .bar:hover .val,.mini .bar:focus .val,.mini .bar:last-child .val{opacity:1}
.mini .tick{font:10px var(--mono);fill:var(--ink-3)}
.mini-note{font-size:11.5px;color:var(--ink-3);margin:6px 0 0}

.tablewrap{overflow-x:auto;background:var(--surface);border:1px solid var(--line);border-radius:8px}
table{border-collapse:collapse;width:100%;min-width:720px}
th,td{padding:9px 12px;border-bottom:1px solid var(--line);white-space:nowrap}
th{font-size:11.5px;font-weight:500;color:var(--ink-3);letter-spacing:.04em;text-align:left;background:var(--surface-2)}
th.num{text-align:right}
tr:last-child td{border-bottom:0}
tbody tr:hover td{background:var(--surface-2)}
.k-idle td,.k-signup-only td{color:var(--ink-3)}
.pill{display:inline-block;font-size:11.5px;padding:2px 8px;border-radius:99px;font-weight:500;line-height:1.5}
.pill.paired{background:var(--good-soft);color:var(--good)}
.pill.phone-only{background:var(--warn-soft);color:var(--warn)}
.pill.signup-only{background:var(--mute-soft);color:var(--ink-2)}
.pill.idle{background:var(--mute-soft);color:var(--ink-3)}
.online{display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--good);margin-left:6px;vertical-align:middle}


.chip{display:inline-block;font-size:11.5px;padding:1px 8px;border-radius:99px;font-weight:500;margin:0 8px;vertical-align:middle}
.chip.good{background:var(--good-soft);color:var(--good)}.chip.warn{background:var(--warn-soft);color:var(--warn)}
.chip.crit{background:var(--crit-soft);color:var(--crit)}.chip.mute{background:var(--mute-soft);color:var(--ink-2)}
.panel-h{font-weight:500;margin-bottom:10px;display:flex;justify-content:space-between;gap:8px}
.panel-h .dim{font-weight:400;font-size:12px}
.asc-grid{display:grid;grid-template-columns:minmax(0,4fr) minmax(0,5fr) minmax(0,4fr);gap:20px;align-items:start}
.kv{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:8px}
.kv li{display:flex;align-items:center}
.kv .dim{margin-left:auto;font-size:12px;font-family:var(--mono)}
.reviews{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:12px}
.rv-head{display:flex;flex-wrap:wrap;gap:8px;align-items:baseline;font-size:13px}
.rv-stars{color:var(--accent);letter-spacing:1px;font-size:12px}
.rv-title{font-weight:500}
.rv-head .dim{font-size:12px;margin-left:auto}
.rv-body{margin:4px 0 0;font-size:13px;color:var(--ink-2);line-height:1.5}
.mini .mark.upd{opacity:.25}
.setup{font-size:13px;color:var(--ink-2)}
.setup p{margin:0 0 8px}.setup ol{margin:0;padding-left:20px;display:flex;flex-direction:column;gap:6px}
.setup code{font-family:var(--mono);background:var(--mute-soft);padding:1px 5px;border-radius:3px;font-size:12px;word-break:break-all}
.regions{margin-top:20px}
.rg{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:minmax(120px,auto) 1fr auto auto;gap:8px 14px;align-items:center}
.rg li{display:contents}
.rg-name{white-space:nowrap}
.rg-track{height:8px;background:var(--mute-soft);border-radius:4px;overflow:hidden;min-width:80px}
.rg-fill{display:block;height:100%;background:var(--accent);border-radius:4px;min-width:2px}
.rg-n{font:500 15px var(--mono);font-variant-numeric:tabular-nums;text-align:right;min-width:60px}
.rg-n small{font-size:11px;color:var(--ink-3);margin-left:6px;font-family:var(--sans)}
.rg-upd{font-size:12px;min-width:48px}
.fb{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:12px}
.fb-head{display:flex;flex-wrap:wrap;align-items:center;gap:10px;font-size:12px;margin-bottom:8px}
.fb-head .chip{margin:0}
.fb-time{margin-left:auto}
.fb-text{margin:0;white-space:pre-wrap;line-height:1.6;max-width:65ch}
.fb-contact{margin:8px 0 0;font-size:12px;color:var(--ink-3)}
@media (max-width:820px){.asc-grid{grid-template-columns:1fr}}
footer{margin-top:28px;font-size:12px;color:var(--ink-3);line-height:1.7}
footer code{font-family:var(--mono);background:var(--mute-soft);padding:1px 5px;border-radius:3px;color:var(--ink-2)}
@media (max-width:820px){
  .tiles{grid-template-columns:repeat(2,1fr)}.two,.minis{grid-template-columns:1fr}.wrap{padding:24px 16px 48px}
}
@media (prefers-reduced-motion:no-preference){.f-fill{transition:width .4s ease}}
</style>
<div class="wrap">
<header>
  <div>
    <h1><svg class="bell" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 9a6 6 0 0 1 12 0v4l1.6 2.6c.4.7-.1 1.4-.9 1.4H5.3c-.8 0-1.3-.7-.9-1.4L6 13z"/><path d="M10 20a2 2 0 0 0 4 0"/></svg>SessionBell Pulse</h1>
    <div class="sub">线上 D1 <span class="mono">sessionbell</span> 的用量快照 · 只含计数与账号短 id，不含任何会话内容</div>
  </div>
  <div class="stamp"><b>${esc(genAt)}</b>北京时间生成</div>
</header>

<section class="tiles" aria-label="概览">
  <div class="tile hero"><div class="l">近 7 天活跃账号</div><div class="v">${active7d}<small>/ ${accounts.length}</small></div><div class="c">7 天内有任何写入</div></div>
  <div class="tile"><div class="l">配对完成的账号</div><div class="v">${count('paired')}</div><div class="c">iPhone + Mac 都接上且 7 天内活跃</div></div>
  <div class="tile"><div class="l">24 小时内在线 Mac</div><div class="v">${macsOnline}</div><div class="c">hook 在一天内上报过状态</div></div>
  <div class="tile"><div class="l">iPhone 推送 token</div><div class="v">${iphones7d}<small>/ ${accounts.reduce((s, a) => s + a.iphones, 0)}</small></div><div class="c">7 天内刷新过 / 总数</div></div>
</section>

<div class="two">
  <div>
    <h2>接入漏斗<small>各步骤累计账号数</small></h2>
    <div class="panel"><ol class="funnel">${funnelHtml}</ol></div>
  </div>
  <div>
    <h2>此刻的 Claude Code 会话<small>来自 24 小时内在线的 ${macsOnline} 台 Mac</small></h2>
    <div class="panel">
      <div class="status">
        <div class="st"><div class="l"><span class="dot running"></span>running</div><div class="v">${sessionStatus.running}</div></div>
        <div class="st"><div class="l"><span class="dot waiting"></span>waiting</div><div class="v">${sessionStatus.waiting}</div></div>
        <div class="st"><div class="l"><span class="dot done"></span>done</div><div class="v">${sessionStatus.done}</div></div>
      </div>
      <p class="status-note">共 ${sessionsTotal} 个会话${sessionStatus.other ? `，另有 ${sessionStatus.other} 个状态未知` : ''}。waiting 就是铃该响的那种：Claude 停下来等人回复。</p>
    </div>
    <h2>近 7 天每日活动<small>写入 D1 的记录数，7 天前的记录已被 cron 清理</small></h2>
    <div class="minis">
      ${miniChart('Live Activity', 'live_activities', '灵动岛 / 锁屏上出现一次会话卡片')}
      ${miniChart('手机下发命令', 'commands')}
      ${miniChart('手机上审批', 'decisions', '权限请求在手机上被允许或拒绝')}
    </div>
  </div>
</div>

<h2>App Store<small>App Store Connect API · Apple ID ${APP_ID_LABEL}</small></h2>
${ascSection()}

<h2>用户反馈<small>App 内「发送反馈」· 最近 ${feedback.length} 条</small></h2>
${feedbackSection()}

<h2>账号明细<small>按最后活跃时间排序 · 计数类字段为近 7 天</small></h2>
<div class="tablewrap">
<table>
  <thead><tr>
    <th>账号</th><th>状态</th><th class="num">iPhone</th><th class="num">Mac</th>
    <th class="num">Live Act.</th><th class="num">命令</th><th class="num">审批</th><th>首次出现</th><th>最后活跃</th>
  </tr></thead>
  <tbody>${accountRows}</tbody>
</table>
</div>

<footer>
  状态口径：<b>配对完成</b> = 有 iPhone token 且 Mac 上报过状态；<b>未接 Mac</b> = 只装了 App；<b>仅注册</b> = 只有账号记录；<b>沉寂</b> = 超过 7 天没有任何写入。
  Mac 列的绿点表示该账号 24 小时内有 Mac 在线。<br>
  刷新：在有网络的终端里运行 <code>node backend-cf/scripts/board.mjs</code>，再重新发布生成的 <code>build/board.html</code>。
</footer>
</div>
`;

mkdirSync(dirname(out), { recursive: true });
writeFileSync(out, html);
console.log(`board → ${out}  (${accounts.length} accounts, ${stateRows.length} state rows)`);
