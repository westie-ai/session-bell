// P0-1b: one phone = one alert ticket and one push-to-start ticket.
import assert from 'node:assert/strict';
import test from 'node:test';
import { fakeD1, caller, coldIsolate } from './fake_d1.mjs';

const tok = (c) => c.repeat(64);
const PHONE = '6F9619FF-8B86-D011-B42D-00C04FC964FF';
const IPAD = '11111111-2222-3333-4444-555555555555';

async function setup() {
  const env = { DB: fakeD1() };
  return { env, call: caller(await coldIsolate(), env) };
}
const reg = async (call) => (await (await call('/api/token')).json());

test('re-registering a phone replaces its older alert and push-to-start tokens', async () => {
  const { call } = await setup();
  await call('/api/token', { device_token: tok('a'), pts_token: tok('b'), device_id: PHONE });
  await call('/api/token', { device_token: tok('c'), pts_token: tok('d'), device_id: PHONE });
  const r = await reg(call);
  assert.deepEqual(r.devices, [tok('c')]);
  assert.deepEqual(r.pts, [tok('d')]);
});

test('other phones in the same space keep their own registration', async () => {
  const { call } = await setup();
  await call('/api/token', { device_token: tok('a'), pts_token: tok('b'), device_id: PHONE });
  await call('/api/token', { device_token: tok('e'), pts_token: tok('f'), device_id: IPAD });
  await call('/api/token', { device_token: tok('c'), device_id: PHONE });
  const r = await reg(call);
  assert.deepEqual(r.devices.sort(), [tok('c'), tok('e')].sort());
  assert.deepEqual(r.pts.sort(), [tok('b'), tok('f')].sort());
});

test('existing duplicate rows still yield only the newest token per phone', async () => {
  const { env, call } = await setup();
  await call('/api/token', { device_token: tok('a'), device_id: PHONE });
  const ns = [...env.DB.rows.values()][0].ns;
  env.DB.rows.set(`${ns}|devices/${tok('9')}`, { ns, k: `devices/${tok('9')}`, v: PHONE, ts: 1 });
  assert.deepEqual((await reg(call)).devices, [tok('a')]);
});

test('old apps without device_id keep working as before', async () => {
  const { call } = await setup();
  await call('/api/token', { device_token: tok('a') });
  await call('/api/token', { device_token: tok('c') });
  await call('/api/token', { device_token: tok('e'), device_id: '../bad' });
  assert.deepEqual((await reg(call)).devices.sort(), [tok('a'), tok('c'), tok('e')].sort());
});
