// P0-0: APNs provider token shared across Worker isolates.
import assert from 'node:assert/strict';
import test from 'node:test';
import { fakeD1, caller, coldIsolate, apnsTestKey, stubApns } from './fake_d1.mjs';

const TOKEN = 'ab'.repeat(32);

async function setup() {
  const env = { DB: fakeD1(), APNS_KEY: await apnsTestKey(), APNS_KEY_ID: 'KEY1', APNS_TEAM_ID: 'TEAM1' };
  const first = await coldIsolate();
  await caller(first, env)('/api/token', { device_token: TOKEN });
  return env;
}

const push = (worker, env) => caller(worker, env)('/api/push', {
  device_token: TOKEN, topic: 'dev.test', payload: { aps: { alert: 'hi' } }, environment: 'sandbox',
});

test('cold isolates reuse one provider token instead of minting their own', async () => {
  const env = await setup();
  const apns = stubApns();
  try {
    for (let i = 0; i < 3; i++) {
      const r = await (await push(await coldIsolate(), env)).json();
      assert.equal(r.status, 200);
    }
    const auths = new Set(apns.calls.map((c) => c.headers.authorization));
    assert.equal(apns.calls.length, 3);
    assert.equal(auths.size, 1, 'every isolate must present the same provider token');
  } finally { apns.restore(); }
});

test('a token past 50 minutes is replaced, once, for everyone', async () => {
  const env = await setup();
  const apns = stubApns();
  try {
    await push(await coldIsolate(), env);
    const row = env.DB.rows.get('sys|apns/jwt');
    const doc = JSON.parse(row.v);
    doc.iat -= 51 * 60;
    env.DB.rows.set('sys|apns/jwt', { ...row, v: JSON.stringify(doc), ts: row.ts - 1 });
    await push(await coldIsolate(), env);
    await push(await coldIsolate(), env);
    const [a, b, c] = apns.calls.map((x) => x.headers.authorization);
    assert.notEqual(a, b);
    assert.equal(b, c);
  } finally { apns.restore(); }
});

test('429 TooManyProviderTokenUpdates retries once with the shared token', async () => {
  const env = await setup();
  const apns = stubApns((call, n) => n === 1
    ? { status: 429, body: '{"reason":"TooManyProviderTokenUpdates"}' }
    : { status: 200, body: '' });
  try {
    const r = await (await push(await coldIsolate(), env)).json();
    assert.equal(r.status, 200);
    assert.equal(apns.calls.length, 2);
  } finally { apns.restore(); }
});
