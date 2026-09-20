import assert from 'node:assert/strict';
import test from 'node:test';
import worker from '../backend-cf/src/worker.js';

function database() {
  const rows = new Map();
  return { prepare(sql) { return { bind(...args) { return {
    async first() { return rows.get(args.slice(0, 2).join('|')) || null; },
    async run() {
      const [ns, k, v, ts] = args, key = `${ns}|${k}`;
      if (!sql.startsWith('INSERT OR IGNORE') || !rows.has(key)) rows.set(key, { ns, k, v, ts });
      return { success: true };
    },
    async all() { return { results: [...rows.values()].filter(r => r.ns === args[0] && r.k >= args[1] && r.k < args[2]) }; }
  }; } }; } };
}
const secret = 'sessionbell-test-secret';
function call(env, path, body, token = secret) {
  return worker.fetch(new Request('https://test.invalid' + path, {
    method: body ? 'POST' : 'GET', headers: { 'x-sb-secret': token, 'Content-Type': 'application/json' },
    ...(body ? { body: JSON.stringify(body) } : {})
  }), env);
}
const command = { command_id: '00000000-0000-4000-8000-000000000001', host: 'mac', action: 'spawn', text: 'Do work', cwd: '/tmp/project' };

test('merged main retains separate Markdown progress and terminal captures', async () => {
  const env = { DB: database() };
  await call(env, '/api/capture', { session_id: 'claude-1', text: 'terminal frame' });
  await call(env, '/api/capture', { session_id: 'claude-1', kind: 'md', text: 'm'.repeat(25000) });
  const terminal = await (await call(env, '/api/capture?id=claude-1')).json();
  const progress = await (await call(env, '/api/capture?id=claude-1&kind=md')).json();
  assert.equal(terminal.capture.text, 'terminal frame');
  assert.equal(progress.capture.text.length, 24000);
});

test('duplicate upload cannot create a second task or change the first prompt', async () => {
  const env = { DB: database() };
  assert.equal((await call(env, '/api/codex', command)).status, 200);
  await call(env, '/api/codex', { ...command, text: 'different prompt' });
  const data = await (await call(env, '/api/codex?host=mac')).json();
  assert.equal(data.commands.length, 1);
  assert.equal(data.commands[0].text, 'Do work');
  const mailbox = await (await call(env, '/api/command')).json();
  assert.ok(mailbox.commands['_codex-mac']);
});
test('command queue preserves multiple messages and host/tenant isolation', async () => {
  const env = { DB: database() };
  await call(env, '/api/codex', command);
  await call(env, '/api/codex', { ...command, command_id: '00000000-0000-4000-8000-000000000002' });
  assert.equal((await (await call(env, '/api/codex?host=mac')).json()).commands.length, 2);
  assert.deepEqual((await (await call(env, '/api/codex?host=anothermac')).json()).commands, []);
  assert.deepEqual((await (await call(env, '/api/codex?host=mac', undefined, 'another-test-secret')).json()).commands, []);
});
test('a delivered command cannot be put back into dispatching', async () => {
  const env = { DB: database() };
  await call(env, '/api/codex', command);
  await call(env, '/api/codex', { ...command, action: 'ack', status: 'delivered', session_id: 'thread-1' });
  await call(env, '/api/codex', { ...command, action: 'ack', status: 'dispatching' });
  const entry = (await (await call(env, `/api/codex?host=mac&id=${command.command_id}`)).json()).command;
  assert.equal(entry.status, 'delivered');
  assert.equal(entry.session_id, 'thread-1');
});
test('invalid commands and unauthenticated callers are rejected', async () => {
  const env = { DB: database() };
  assert.equal((await call(env, '/api/codex', command, '')).status, 401);
  assert.equal((await call(env, '/api/codex', { ...command, text: 'x'.repeat(4001) })).status, 400);
  assert.equal((await call(env, '/api/codex', { ...command, host: '../other' })).status, 400);
  assert.equal((await call(env, '/api/codex', { ...command, action: 'answer', session_id: 't' })).status, 400);
});
