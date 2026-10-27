import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';

// Load the actual client; replace only Vite's build-time base path.
const source = (await fs.readFile(new URL('../src/api.js', import.meta.url), 'utf8'))
  .replace('import.meta.env.BASE_URL', JSON.stringify('/bank/'));
globalThis.sessionStorage = {getItem: () => ''};
globalThis.localStorage = {getItem: () => ''};
const {api, ApiError} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));

test('invalid read payload is a recoverable error', async () => {
  globalThis.fetch = async () => new Response('<html>offline</html>', {status:200});
  await assert.rejects(api.get('/api/test'), error => error instanceof ApiError && error.code === 'INVALID_SERVER_RESPONSE');
});

test('unknown write outcome never retries a transaction', async () => {
  let calls = 0;
  globalThis.fetch = async () => { calls++; throw new Error('connection lost after request'); };
  await assert.rejects(api.post('/api/test', {}), error => error.code === 'TRANSACTION_RESULT_UNKNOWN');
  assert.equal(calls, 1);
});

test('truncated chat stream cannot be treated as successful completion', async () => {
  let calls = 0;
  globalThis.fetch = async () => { calls++; return new Response('event: turn.started\ndata: {}\n\n', {headers:{'Content-Type':'text/event-stream'}}); };
  await assert.rejects(api.send('session', {}, () => {}), error => error.code === 'TRANSACTION_RESULT_UNKNOWN');
  assert.equal(calls, 1);
});

test('successful chat completion is delivered exactly once', async () => {
  globalThis.fetch = async () => new Response('event: turn.started\ndata: {}\n\nevent: turn.completed\ndata: {"revision":2}\n\n', {headers:{'Content-Type':'text/event-stream'}});
  const events = [];
  assert.deepEqual(await api.send('session', {}, event => events.push(event)), {revision:2});
  assert.deepEqual(events, ['turn.started', 'turn.completed']);
});

test('explicit server rejection preserves the rejection code', async () => {
  globalThis.fetch = async () => new Response('event: turn.failed\ndata: {"code":"CHAT_STALE"}\n\n', {headers:{'Content-Type':'text/event-stream'}});
  await assert.rejects(api.send('session', {}, () => {}), error => error.code === 'CHAT_STALE');
});
