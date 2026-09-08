#!/usr/bin/env node
// Local snapshot of the usage board → build/board.html.
// The live version is served by the Worker at /board?k=<BOARD_KEY>; this
// script exists for offline copies and for working on the layout.
//
//   node backend-cf/scripts/board.mjs            → build/board.html
//   node backend-cf/scripts/board.mjs out.html   → custom path
//
// Needs a logged-in wrangler and a terminal with network access (the
// sandboxed Claude shell reports "fetch failed"). ASC credentials come from
// ~/.sessionbell/asc.json { key_id, issuer_id, p8_path, vendor_number }.

import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { snapshot } from '../src/asc.js';
import { renderBoard } from '../src/board.js';

const here = dirname(fileURLToPath(import.meta.url));
const backend = resolve(here, '..');
const out = resolve(process.argv[2] || resolve(backend, '..', 'build', 'board.html'));

async function db(sql) {
  const raw = execFileSync('npx', ['wrangler', 'd1', 'execute', 'sessionbell',
    '--remote', '--json', '--command', sql], { cwd: backend, encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'inherit'], maxBuffer: 64 * 1024 * 1024 });
  const parsed = JSON.parse(raw);
  if (parsed.error) throw new Error(parsed.error.text || JSON.stringify(parsed.error));
  return parsed[0].results;
}

function localAscConfig() {
  const p = join(homedir(), '.sessionbell', 'asc.json');
  if (!existsSync(p)) return null;
  const j = JSON.parse(readFileSync(p, 'utf8'));
  if (!j.key_id || !j.issuer_id || !j.p8_path) return null;
  return { keyId: j.key_id, issuerId: j.issuer_id, vendor: j.vendor_number || null,
    pem: readFileSync(j.p8_path.replace(/^~/, homedir()), 'utf8') };
}

const asc = await snapshot(localAscConfig());
if (asc.errors?.length) console.error('ASC:', asc.errors.join(' | '));
const html = await renderBoard({ db, asc, mode: 'file' });
mkdirSync(dirname(out), { recursive: true });
writeFileSync(out, html);
console.log(`board → ${out}`);
