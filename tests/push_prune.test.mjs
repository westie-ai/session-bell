// P0-1a: tokens Apple declares dead are forgotten; healthy ones are kept.
import assert from 'node:assert/strict';
import test from 'node:test';
import { fakeD1, caller, coldIsolate, apnsTestKey, stubApns } from './fake_d1.mjs';

const DEAD = 'de'.repeat(32), LIVE = 'a1'.repeat(32);

async function setup() {
  const env = { DB: fakeD1(), APNS_KEY: await apnsTestKey(), APNS_KEY_ID: 'K', APNS_TEAM_ID: 'T' };
  const worker = await coldIsolate();
  const call = caller(worker, env);
  await call('/api/token', { device_token: DEAD, pts_token: DEAD });
  await call('/api/token', { device_token: LIVE });
  return { env, call };
}
const push = (call, token) => call('/api/push', {
  device_token: token, topic: 'dev.test', payload: { aps: {} }, environment: 'production' });
const devices = async (call) => (await (await call('/api/token')).json());

test('410 Unregistered drops the token from every registry', async () => {
  const { call } = await setup();
  const apns = stubApns((c) => c.url.includes(DEAD)
    ? { status: 410, body: '{"reason":"Unregistered"}' } : { status: 200 });
  try {
    const r = await (await push(call, DEAD)).json();
    assert.equal(r.status, 410);
    assert.equal(r.pruned, true);
    const reg = await devices(call);
    assert.deepEqual(reg.devices, [LIVE]);
    assert.deepEqual(reg.pts, []);
    assert.equal((await push(call, DEAD)).status, 403, 'a forgotten token is no longer pushable');
  } finally { apns.restore(); }
});

test('BadDeviceToken in both environments drops it; in one environment keeps it', async () => {
  const { call } = await setup();
  const apns = stubApns((c) => c.url.includes('sandbox') && c.url.includes(LIVE)
    ? { status: 200 } : { status: 400, body: '{"reason":"BadDeviceToken"}' });
  try {
    assert.equal((await (await push(call, LIVE)).json()).status, 200);   // sandbox fallback works
    assert.equal((await (await push(call, DEAD)).json()).pruned, true);  // bad everywhere
    assert.deepEqual((await devices(call)).devices, [LIVE]);
  } finally { apns.restore(); }
});

test('signing / topic errors never cost the user their registration', async () => {
  const { call } = await setup();
  const apns = stubApns(() => ({ status: 403, body: '{"reason":"InvalidProviderToken"}' }));
  try {
    await push(call, LIVE);
    assert.deepEqual((await devices(call)).devices.sort(), [DEAD, LIVE].sort());
  } finally { apns.restore(); }
});

test('a late 410 does not delete a token registered again after it', async () => {
  const { env, call } = await setup();
  const regTs = [...env.DB.rows.values()].find((r) => r.k === `devices/${DEAD}`).ts;
  const apns = stubApns(() => ({ status: 410, body: JSON.stringify({ reason: 'Unregistered', timestamp: regTs - 1000 }) }));
  try {
    const r = await (await push(call, DEAD)).json();
    assert.equal(r.status, 410);
    assert.ok((await devices(call)).devices.includes(DEAD), 'registered after the 410 verdict: keep');
  } finally { apns.restore(); }
});

test('a failing prune still reports the real APNs status', async () => {
  const { env, call } = await setup();
  const real = env.DB.batch;
  env.DB.batch = async () => { throw new Error('D1 down'); };
  const apns = stubApns(() => ({ status: 410, body: '{"reason":"Unregistered"}' }));
  try {
    const r = await (await push(call, DEAD)).json();
    assert.equal(r.status, 410);
  } finally { apns.restore(); env.DB.batch = real; }
});
