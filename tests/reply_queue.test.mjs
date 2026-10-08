// P1: phone replies are idempotent by client id, ordered, claim-once, and
// carry an explicit status the phone can show while the Mac is offline.
import assert from 'node:assert/strict';
import test from 'node:test';
import { fakeD1, caller, coldIsolate } from './fake_d1.mjs';
import { settleAllReplies } from '../backend-cf/src/worker.js';

const ID = (n) => `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
async function setup(opts) {
  const env = { DB: fakeD1(opts) };
  return { env, call: caller(await coldIsolate(), env) };
}
const j = async (p) => (await p).json();

test('resending the same id never creates a second reply', async () => {
  const { call } = await setup();
  const a = await j(call('/api/reply', { id: ID(1), session_id: 's1', text: 'continue' }));
  const b = await j(call('/api/reply', { id: ID(1), session_id: 's1', text: 'continue' }));
  assert.equal(a.duplicate, false);
  assert.equal(b.duplicate, true);
  const list = await j(call('/api/reply?sid=s1'));
  assert.equal(list.replies.length, 1);
});

test('two quick replies are both kept, oldest first (the legacy slot kept only the last)', async () => {
  const { call } = await setup();
  await call('/api/reply', { id: ID(1), session_id: 's1', text: 'first' });
  await call('/api/reply', { id: ID(2), session_id: 's1', text: 'second' });
  const r = await j(call('/api/command?wait=0&since=0&reply_since=0'));
  assert.deepEqual(r.replies.map((x) => x.text), ['first', 'second']);
  assert.deepEqual(r.commands, {});
});

test('claim is exclusive; only the holder acks; terminal states are sticky', async () => {
  const { call } = await setup();
  await call('/api/reply', { id: ID(1), session_id: 's1', text: 'go' });
  const claim = await j(call('/api/reply/claim', { id: ID(1) }));
  assert.equal(claim.claimed, true);
  assert.ok(claim.claim_id);
  assert.equal((await j(call('/api/reply/claim', { id: ID(1) }))).claimed, false);
  assert.equal((await j(call('/api/reply/cancel', { id: ID(1) }))).cancelled, false, 'too late to take back');
  assert.equal((await call('/api/reply/ack', { id: ID(1), status: 'delivered' })).status, 409);
  assert.equal((await call('/api/reply/ack', { id: ID(1), status: 'delivered', claim_id: 'someone-else' })).status, 409);
  await call('/api/reply/ack', { id: ID(1), status: 'delivered', claim_id: claim.claim_id });
  await call('/api/reply/ack', { id: ID(1), status: 'queued', claim_id: claim.claim_id });
  assert.equal((await j(call(`/api/reply?id=${ID(1)}`))).reply.status, 'delivered');
});

test('a failed injection hands the reply back to the queue', async () => {
  const { call } = await setup();
  await call('/api/reply', { id: ID(1), session_id: 's1', text: 'go' });
  const { claim_id } = await j(call('/api/reply/claim', { id: ID(1) }));
  await call('/api/reply/ack', { id: ID(1), status: 'queued', message: 'pane gone', claim_id });
  assert.equal((await j(call('/api/reply/claim', { id: ID(1) }))).claimed, true);
});

test('queued replies can be cancelled and are never handed out afterwards', async () => {
  const { call } = await setup();
  await call('/api/reply', { id: ID(1), session_id: 's1', text: 'oops' });
  assert.equal((await j(call('/api/reply/cancel', { id: ID(1) }))).cancelled, true);
  assert.equal((await j(call('/api/reply/claim', { id: ID(1) }))).claimed, false);
  assert.equal((await j(call(`/api/reply?id=${ID(1)}`))).reply.status, 'cancelled');
});

test('rev lets a consumer catch up on exactly what it missed', async () => {
  const { call } = await setup();
  await call('/api/reply', { id: ID(1), session_id: 's1', text: 'a' });
  const first = await j(call('/api/command?wait=0&since=0&reply_since=0'));
  await call('/api/reply', { id: ID(2), session_id: 's1', text: 'b' });
  const next = await j(call(`/api/command?wait=0&since=0&reply_since=${first.reply_rev}`));
  assert.deepEqual(next.replies.map((x) => x.text), ['b']);
  const none = await j(call(`/api/command?wait=0&since=0&reply_since=${next.reply_rev}`));
  assert.deepEqual(none.replies, []);
});

test('a claim that is never acked comes back to the queue and is visible past the cursor', async () => {
  const { env, call } = await setup();
  await call('/api/reply', { id: ID(1), session_id: 's1', text: 'go' });
  await call('/api/reply/claim', { id: ID(1) });
  const seen = await j(call('/api/command?wait=0&since=0&reply_since=0'));
  const row = [...env.DB.rows.values()].find((r) => r.k === `reply/${ID(1)}`);
  const e = JSON.parse(row.v); e.claimed_at -= 3 * 60e3; row.v = JSON.stringify(e);
  await settleAllReplies(env);   // the hourly cron (a listing triggered by any change does it too)
  const back = await j(call(`/api/command?wait=0&since=0&reply_since=${seen.reply_rev}`));
  assert.deepEqual(back.replies.map((x) => x.status), ['queued']);
  assert.equal((await j(call('/api/reply/claim', { id: ID(1) }))).claimed, true, 'claimable again');
});

test('a reply nobody claimed within the TTL reads as expired and cannot be claimed', async () => {
  const { env, call } = await setup();
  await call('/api/reply', { id: ID(1), session_id: 's1', text: 'late' });
  const row = [...env.DB.rows.values()].find((r) => r.k === `reply/${ID(1)}`);
  const e = JSON.parse(row.v); e.expires_at = Date.now() - 1; row.v = JSON.stringify(e);
  assert.equal((await j(call(`/api/reply?id=${ID(1)}`))).reply.status, 'expired');
  assert.equal((await j(call('/api/reply/claim', { id: ID(1) }))).claimed, false);
});

test('older hooks see the exact legacy response shape', async () => {
  const { call } = await setup();
  await call('/api/reply', { id: ID(1), session_id: 's1', text: 'new path' });
  await call('/api/command', { session_id: 's1', text: 'legacy' });
  const all = await j(call('/api/command?wait=0&since=0'));
  assert.deepEqual(Object.keys(all), ['commands']);
  const one = await j(call('/api/command?id=s1&wait=0&since=0'));
  assert.deepEqual(Object.keys(one), ['command']);
});

test('state carries the Mac capability list (added field only)', async () => {
  const { call } = await setup();
  await call('/api/state', { host: 'mac', sessions: {}, caps: ['reply-queue'] });
  await call('/api/state', { host: 'old', sessions: {} });
  const s = await j(call('/api/state'));
  assert.deepEqual(s.mac.caps, ['reply-queue']);
  assert.deepEqual(s.old.caps, []);
  for (const k of ['ts', 'sessions', 'usage', 'codex_usage', 'codex', 'awake', 'projects', 'hook_v']) {
    assert.ok(k in s.mac, `existing field ${k} kept`);
  }
});

test('bad input is rejected', async () => {
  const { call } = await setup();
  assert.equal((await call('/api/reply', { id: 'nope', session_id: 's1', text: 'x' })).status, 400);
  assert.equal((await call('/api/reply', { id: ID(1), session_id: '../x', text: 'x' })).status, 400);
  assert.equal((await call('/api/reply', { id: ID(1), session_id: 's1', text: ' ' })).status, 400);
  assert.equal((await call('/api/reply/ack', { id: ID(9), status: 'delivered' })).status, 404);
  assert.equal((await call('/api/reply', { id: ID(1), session_id: 's1', text: 'x' }, '')).status, 401);
});

test('concurrent replies are never skipped by a consumer following rev', async () => {
  // Statements are delayed 1–15 ms and interleave like production; the
  // review reproduced 40 of 60 lost with separate bump and write.
  const { call } = await setup({ jitter: true });
  const seen = new Set();
  let cursor = 0, done = false;
  const consumer = (async () => {
    while (!done || seen.size < 60) {
      const r = await j(call(`/api/command?wait=0&since=0&reply_since=${cursor}`));
      for (const x of r.replies) seen.add(x.id);
      cursor = r.reply_rev;
      if (done && seen.size < 60 && r.replies.length === 0) break;
    }
  })();
  await Promise.all(Array.from({ length: 60 }, (_, i) =>
    call('/api/reply', { id: ID(i + 1), session_id: 's1', text: `r${i}` })));
  done = true;
  await consumer;
  assert.equal(seen.size, 60);
});

test('a poll whose rev has not moved does not list the queue', async () => {
  const { env, call } = await setup();
  await call('/api/reply', { id: ID(1), session_id: 's1', text: 'a' });
  const first = await j(call('/api/command?wait=0&since=0&reply_since=0'));
  const real = env.DB.prepare.bind(env.DB);
  let lists = 0;
  env.DB.prepare = (sql) => { if (sql.startsWith('SELECT k, v, ts') && true) lists++; return real(sql); };
  await call(`/api/command?id=s1&wait=0&since=0&reply_since=${first.reply_rev}`);
  const before = lists;
  await call(`/api/reply?sid=s1&since=${first.reply_rev}`);
  assert.equal(before, 0, 'session poll: point read only');
  assert.equal(lists, 0, 'phone conditional read: point read only');
});

test('the phone can ask "anything new?" without downloading the list', async () => {
  const { call } = await setup();
  await call('/api/reply', { id: ID(1), session_id: 's1', text: 'a' });
  const full = await j(call('/api/reply?sid=s1'));
  assert.equal(full.replies.length, 1);
  assert.deepEqual(await j(call(`/api/reply?sid=s1&since=${full.rev}`)), { rev: full.rev, unchanged: true });
  await call('/api/reply/cancel', { id: ID(1) });
  const after = await j(call(`/api/reply?sid=s1&since=${full.rev}`));
  assert.equal(after.replies[0].status, 'cancelled');
});

test('listings never leak the claim id', async () => {
  const { call } = await setup();
  await call('/api/reply', { id: ID(1), session_id: 's1', text: 'a' });
  await call('/api/reply/claim', { id: ID(1) });
  const r = await j(call('/api/command?wait=0&since=0&reply_since=0'));
  assert.equal('claim_id' in r.replies[0], false);
  assert.equal('claim_id' in (await j(call(`/api/reply?id=${ID(1)}`))).reply, false);
});
