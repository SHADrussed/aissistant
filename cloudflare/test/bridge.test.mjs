import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Miniflare, Log, LogLevel, Response } from 'miniflare';

const secret = 'synthetic_webhook_secret';
const token = '12345:synthetic_token';
const bindings = { TELEGRAM_BOT_TOKEN: token, TELEGRAM_WEBHOOK_SECRET: secret,
  TELEGRAM_OWNER_ID: '100', TELEGRAM_ALLOWED_CHAT_IDS: '200' };
const message = (id = 1) => ({ update_id: id, business_message: {
  business_connection_id: 'connection-1', date: 1700000000, message_id: id,
  chat: { id: 200, type: 'private', first_name: 'DO_NOT_STORE_NAME' },
  text: 'DO_NOT_STORE_TEXT', caption: 'DO_NOT_STORE_CAPTION',
} });
const connectionEvent = (id = 1) => ({ update_id: id, business_connection: {
  id: 'connection-1', date: 1700000000, user: { id: 100 }, is_enabled: true,
} });
const migration = await readFile(new URL('../migrations/0001_metadata.sql', import.meta.url), 'utf8');

async function fixture(t, options = {}) {
  const path = await mkdtemp(join(tmpdir(), 'bridge-'));
  let calls = 0;
  const capturedLogs = [];
  const unexpectedRequests = [];
  const state = { owner: 100, enabled: true, apiStatus: 200, invalid: false, fail: false, ...options };
  const create = () => new Miniflare({
    cf: false, modules: true, scriptPath: new URL('../dist/index.js', import.meta.url).pathname,
    compatibilityDate: '2026-07-30', d1Databases: { DB: 'test-db' }, d1Persist: path,
    bindings: { ...bindings, ...options.bindings },
    log: new class extends Log { log(message) { capturedLogs.push(message); } }(LogLevel.DEBUG),
    // All outbound requests are intercepted. Unexpected methods/hosts fail tests.
    outboundService: async request => {
      calls++;
      try {
        assert.equal(request.url, `https://api.telegram.org/bot${token}/getBusinessConnection`);
        assert.equal(request.method, 'POST');
        assert.deepEqual(await request.json(), { business_connection_id: 'connection-1' });
      } catch (error) { unexpectedRequests.push(error); return new Response('', { status: 500 }); }
      if (state.fail) throw new Error('synthetic upstream failure');
      if (state.apiStatus !== 200) return new Response('upstream sensitive diagnostic', { status: state.apiStatus });
      return Response.json(state.invalid ? {} : { ok: true, result: {
        id: state.connectionId ?? 'connection-1', user: { id: state.owner }, is_enabled: state.enabled,
      } });
    },
  });
  let mf = create();
  t.after(async () => { await mf.dispose(); await rm(path, { recursive: true, force: true });
    assert.deepEqual(unexpectedRequests, [], 'Unexpected outbound request');
    for (const sensitive of [secret, token, 'DO_NOT_STORE']) assert.ok(!capturedLogs.join('\n').includes(sensitive));
  });
  let db = await mf.getD1Database('DB');
  await db.exec(migration.replace(/--[^\n]*/g, '').replace(/\n/g, ' '));
  return {
    state, calls: () => calls,
    request: (body, { secretToken = secret, method = 'POST', path = '/telegram/webhook' } = {}) =>
      mf.dispatchFetch(`https://bridge.example${path}`, { method,
        headers: secretToken === null ? {} : { 'X-Telegram-Bot-Api-Secret-Token': secretToken },
        ...(method === 'POST' ? { body: typeof body === 'string' ? body : JSON.stringify(body) } : {}),
      }),
    rows: async () => (await db.prepare('SELECT * FROM events ORDER BY update_id').all()).results,
    db: () => db,
    logs: () => capturedLogs.join('\n'),
    restart: async () => { await mf.dispose(); mf = create(); db = await mf.getD1Database('DB'); },
  };
}

test('rejects missing/forged webhook before parsing, API or D1 writes', async t => {
  const f = await fixture(t);
  for (const secretToken of [null, 'forged']) {
    assert.equal((await f.request('not JSON', { secretToken })).status, 401);
  }
  assert.equal(f.calls(), 0); assert.deepEqual(await f.rows(), []);
});

test('never trusts webhook owner; foreign connection stores nothing', async t => {
  const f = await fixture(t, { owner: 999 });
  assert.equal((await f.request(connectionEvent())).status, 200);
  assert.equal((await f.request(message(2))).status, 200);
  assert.equal(f.calls(), 2); assert.deepEqual(await f.rows(), []);
});

test('stores only metadata, and verifies each connection against Telegram', async t => {
  const f = await fixture(t);
  assert.equal((await f.request(connectionEvent())).status, 200);
  assert.equal((await f.request(message(2))).status, 200);
  const rows = await f.rows();
  assert.equal(rows.length, 2); assert.equal(f.calls(), 2);
  assert.deepEqual(Object.keys(rows[1]).sort(), ['bot_id', 'update_id', 'event_type', 'connection_id',
    'event_time', 'received_at', 'chat_id', 'message_id', 'connection_enabled'].sort());
  assert.equal(rows[1].chat_id, '200'); assert.equal(rows[1].message_id, 2);
  assert.equal(rows[1].event_time, 1700000000); assert.ok(rows[1].received_at > 0);
  assert.equal(rows[0].chat_id, null); assert.equal(rows[0].message_id, null);
  assert.equal(rows[0].connection_enabled, 1);
  for (const forbidden of ['DO_NOT_STORE', secret, token]) {
    assert.ok(!JSON.stringify(rows).includes(forbidden)); assert.ok(!f.logs().includes(forbidden));
  }
});

test('only explicitly allowed private chats; empty allowlist denies all', async t => {
  const f = await fixture(t);
  for (const type of ['group', 'supergroup', 'channel']) {
    const payload = message(); payload.business_message.chat.type = type;
    assert.equal((await f.request(payload)).status, 200);
  }
  const payload = message(); payload.business_message.chat.id = 999;
  assert.equal((await f.request(payload)).status, 200);
  assert.equal(f.calls(), 0); assert.deepEqual(await f.rows(), []);
  const empty = await fixture(t, { bindings: { TELEGRAM_ALLOWED_CHAT_IDS: '' } });
  await empty.request(message()); assert.equal(empty.calls(), 0); assert.deepEqual(await empty.rows(), []);
});

test('disabled connection and revocation are checked on every message', async t => {
  const f = await fixture(t);
  await f.request(message()); f.state.enabled = false;
  await f.request(message(2)); await f.request(connectionEvent(3));
  const rows = await f.rows(); assert.equal(rows.length, 2);
  assert.equal(rows[1].connection_enabled, 0); assert.equal(f.calls(), 3);
});

test('duplicate deliveries and concurrent inserts are deduplicated by D1', async t => {
  const f = await fixture(t);
  const responses = await Promise.all(Array.from({ length: 8 }, () => f.request(message())));
  assert.ok(responses.every(r => r.status === 200));
  const differentUpdate = message(); differentUpdate.update_id = 100;
  assert.equal((await f.request(differentUpdate)).status, 200);
  assert.equal((await f.rows()).length, 1);
});

test('database and duplicate suppression survive full Miniflare restart', async t => {
  const f = await fixture(t);
  await f.request(message()); const before = await f.rows(); assert.equal(before.length, 1);
  await f.restart(); assert.deepEqual(await f.rows(), before);
  await f.request(message()); assert.deepEqual(await f.rows(), before);
  await f.request(message(2)); assert.equal((await f.rows()).length, 2);
});

test('Telegram failures retry without persisting or exposing diagnostics', async t => {
  const f = await fixture(t, { apiStatus: 429 });
  for (const apiStatus of [302, 400, 401, 429, 500]) {
    f.state.apiStatus = apiStatus;
    const r = await f.request(message()); assert.equal(r.status, 503); assert.equal(await r.text(), 'Rejected');
  }
  f.state.apiStatus = 200; f.state.invalid = true;
  assert.equal((await f.request(message())).status, 503);
  assert.deepEqual(await f.rows(), []);
  f.state.invalid = false; await f.request(message()); assert.equal((await f.rows()).length, 1);
});

test('D1 failure returns retry status; never acknowledge data that was not saved', async t => {
  const f = await fixture(t); await f.db().exec('DROP TABLE events');
  const r = await f.request(message()); assert.equal(r.status, 503); assert.equal(await r.text(), 'Rejected');
  assert.ok(!f.logs().includes(token));
});

test('no archive, health payload, MCP, or other public read endpoints', async t => {
  const f = await fixture(t);
  for (const path of ['/', '/archive', '/events', '/search', '/mcp', '/telegram/webhook']) {
    assert.equal((await f.request(null, { path, method: 'GET' })).status, 404);
  }
  assert.equal(f.calls(), 0);
});

test('invalid or oversized JSON is rejected, unsupported updates ignored', async t => {
  const f = await fixture(t);
  for (const payload of ['{', '[]', JSON.stringify({ update_id: -1 }), ' '.repeat(262145)]) {
    assert.equal((await f.request(payload)).status, 400);
  }
  for (const payload of [{ update_id: 1, message: { text: 'ignored' } },
    { update_id: 2, edited_business_message: message().business_message },
    { update_id: 3, deleted_business_messages: {} }]) {
    assert.equal((await f.request(payload)).status, 200);
  }
  assert.equal(f.calls(), 0); assert.deepEqual(await f.rows(), []);
});

test('missing secrets and malformed allowlist fail closed', async t => {
  for (const overrides of [{ TELEGRAM_BOT_TOKEN: '' }, { TELEGRAM_OWNER_ID: 'not-id' },
    { TELEGRAM_WEBHOOK_SECRET: '' }, { TELEGRAM_ALLOWED_CHAT_IDS: '200,typo' }]) {
    const f = await fixture(t, { bindings: overrides });
    assert.equal((await f.request(message())).status, 503); assert.equal(f.calls(), 0);
  }
});


test('wrong connection identity and malformed owner never authorize a message', async t => {
  const f = await fixture(t, { connectionId: 'other-connection' });
  assert.equal((await f.request(message())).status, 503);
  f.state.connectionId = 'connection-1'; f.state.owner = '100';
  assert.equal((await f.request(message())).status, 503);
  assert.deepEqual(await f.rows(), []);
});

test('a changed owner is rejected even after a previously authorized message', async t => {
  const f = await fixture(t);
  await f.request(message()); f.state.owner = 999;
  await f.request(message(2)); assert.equal((await f.rows()).length, 1);
});

test('duplicate connection events do not overwrite persisted metadata', async t => {
  const f = await fixture(t);
  await f.request(connectionEvent()); const before = await f.rows();
  await f.request(connectionEvent()); assert.deepEqual(await f.rows(), before);
  assert.equal(before.length, 1);
});
