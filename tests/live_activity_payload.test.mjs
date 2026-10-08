// P0-2: the gateway only forwards Live Activity payloads the card can render.
import assert from 'node:assert/strict';
import test from 'node:test';
import { fakeD1, caller, coldIsolate, apnsTestKey, stubApns } from './fake_d1.mjs';

const TOKEN = 'cd'.repeat(32);
async function setup() {
  const env = { DB: fakeD1(), APNS_KEY: await apnsTestKey(), APNS_KEY_ID: 'K', APNS_TEAM_ID: 'T' };
  const call = caller(await coldIsolate(), env);
  await call('/api/token', { update_token: TOKEN });
  return call;
}
const la = (call, aps) => call('/api/push', {
  device_token: TOKEN, topic: 'dev.test.push-type.liveactivity', push_type: 'liveactivity',
  payload: { aps }, environment: 'sandbox' });
const task = (status) => ({ project: 'p', host: 'mac', status, since: 1 });

test('known states with stale/dismissal dates are forwarded', async () => {
  const call = await setup();
  const apns = stubApns();
  try {
    for (const aps of [
      { event: 'update', 'content-state': { tasks: [task('running'), task('failed')] }, 'stale-date': 2 },
      { event: 'end', 'content-state': { tasks: [task('done')] }, 'dismissal-date': 2 },
      { event: 'end', 'content-state': { tasks: [] }, 'dismissal-date': 2 },
      // Codex Desktop observer writes `unknown`; older hooks ship it on the card.
      { event: 'update', 'content-state': { tasks: [task('unknown')] }, 'stale-date': 2 },
    ]) assert.equal((await la(call, aps)).status, 200);
    assert.equal(apns.calls.length, 4);
  } finally { apns.restore(); }
});

test('unknown state, missing stale-date or missing dismissal-date is rejected', async () => {
  const call = await setup();
  const apns = stubApns();
  try {
    for (const aps of [
      { event: 'update', 'content-state': { tasks: [task('finished-probably')] }, 'stale-date': 2 },
      { event: 'update', 'content-state': { tasks: [task('running')] } },
      { event: 'end', 'content-state': { tasks: [task('done')] } },
      { event: 'update', 'content-state': {}, 'stale-date': 2 },
    ]) assert.equal((await la(call, aps)).status, 400);
    assert.equal(apns.calls.length, 0);
  } finally { apns.restore(); }
});
