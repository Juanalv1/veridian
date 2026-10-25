import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { Veridian } from '../src/core.js';
import { resolveConfig } from '../src/config.js';
import { startEngine, silentLogger } from './helpers/engine.js';

let engine;
beforeEach(async () => { engine = await startEngine(); });
afterEach(async () => { await engine.close(); });

const warnings = [];
const logger = { warn: (m) => warnings.push(m), error: (m) => warnings.push(m) };

function make(options = {}) {
  return new Veridian(resolveConfig({
    apiKey: 'test-key', baseUrl: engine.url, user: (req) => req.uid ?? null, background: false, logger, ...options,
  }, {}));
}

// A neutral request description, as the adapters build it.
function info(over = {}) {
  return {
    method: 'GET', path: '/items/1', ip: '198.51.100.1', req: { uid: 'u_42' },
    headers: { cookie: 'sessionid=abc', 'user-agent': 'UA' },
    routeBeforeRun: () => '/items/{id}', routeAfterRun: () => '/items/{id}', ...over,
  };
}

// --- events ---

test('an event carries the contract 1 fields and flush sends it with the key', async () => {
  const v = make();
  const ctx = await v.before(info());
  await v.finish(info(), ctx, 200, 512);
  await v.flush();
  const [post] = engine.of('POST', '/v1/events');
  assert.equal(post.headers.authorization, 'Bearer test-key');
  const [e] = post.body.events;
  assert.deepEqual(Object.keys(e).sort(), ['bytes', 'device_id', 'id', 'ip', 'method', 'route', 'session_id', 'status', 'ts', 'user_agent', 'user_id']);
  assert.match(e.id, /^e_[0-9a-f]{32}$/);
  assert.match(e.ts, /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$/);
  assert.equal(e.user_id, 'u_42');
  assert.match(e.session_id, /^s_[0-9a-f]{16}$/);
  assert.deepEqual([e.ip, e.method, e.route, e.status, e.bytes, e.user_agent, e.device_id], ['198.51.100.1', 'GET', '/items/{id}', 200, 512, 'UA', ctx.deviceId]);
});

test('the user agent is cut at 200 characters and events never carry bodies or secrets', async () => {
  const v = make();
  const i = info({ headers: { cookie: 'sessionid=topsecret', 'user-agent': 'x'.repeat(500) }, req: { uid: 'u_1', body: { password: 'p' } } });
  await v.finish(i, await v.before(i), 200, 0);
  await v.flush();
  const text = JSON.stringify(engine.requests[0].body);
  assert.equal(engine.requests[0].body.events[0].user_agent.length, 200);
  assert.ok(!text.includes('topsecret') && !text.includes('password'));
});

test('events without a user are skipped', async () => {
  const v = make();
  const i = info({ req: {} });
  await v.finish(i, await v.before(i), 200, 0);
  await v.flush();
  assert.equal(engine.of('POST', '/v1/events').length, 0);
});

test('events without a session are skipped, because the engine rejects them', async () => {
  const v = make({ session: () => null });
  const i = info();
  await v.finish(i, await v.before(i), 200, 0);
  assert.equal(v.queue.length, 0);
  await v.flush();
  assert.equal(engine.of('POST', '/v1/events').length, 0);
});

test('ids are turned into strings; null, false and empty mean nobody', async () => {
  const v = make({ user: (r) => r.u });
  for (const [u, expected] of [[42, '42'], [null, null], [false, null], ['', null], [undefined, null]]) {
    assert.equal(await v.identity(v.config.user, { u }), expected);
  }
});

test('a lookup that fails because nobody is logged in is quiet', async () => {
  warnings.length = 0;
  const v = make({ user: (req) => req.user.id }); // req.user is undefined: a TypeError
  assert.equal(await v.identity(v.config.user, {}), null);
  assert.deepEqual(warnings, []);
});

test('an unexpected error in user is logged and never breaks the request', async () => {
  warnings.length = 0;
  const v = make({ user: () => { throw new RangeError('boom'); } });
  assert.equal(await v.identity(v.config.user, {}), null);
  assert.equal(warnings.length, 1);
  assert.match(warnings[0], /identity function failed/);
});

test('user and session may be async', async () => {
  const v = make({ user: async () => 'u_async', session: async () => 's_async' });
  assert.equal(await v.identity(v.config.user, {}), 'u_async');
  const i = info();
  await v.finish(i, await v.before(i), 200, 0);
  await v.flush();
  assert.equal(engine.requests[0].body.events[0].session_id, 's_async');
});

test('event ids are unique', async () => {
  const v = make();
  for (let n = 0; n < 3; n++) await v.finish(info(), await v.before(info()), 200, 0);
  await v.flush();
  assert.equal(new Set(engine.requests[0].body.events.map((e) => e.id)).size, 3);
});

test('the device id is minted once per client and reused from the cookie', async () => {
  const v = make();
  const first = await v.before(info());
  assert.equal(first.newDevice, true);
  assert.match(first.deviceId, /^d_[0-9a-f]{12}$/);
  const again = await v.before(info({ headers: { cookie: `vrd_did=${first.deviceId}` } }));
  assert.deepEqual([again.newDevice, again.deviceId], [false, first.deviceId]);
});

// --- flush ---

test('a failed flush keeps the events and the retry sends the same ids', async () => {
  const v = make();
  await v.finish(info(), await v.before(info()), 200, 0);
  engine.forced['POST /v1/events'] = { status: 500, body: {} };
  await v.flush();
  assert.equal(v.queue.length, 1);
  delete engine.forced['POST /v1/events'];
  await v.flush();
  const [a, b] = engine.of('POST', '/v1/events');
  assert.equal(a.body.events[0].id, b.body.events[0].id);
  assert.equal(v.queue.length, 0);
});

test('events rejected with 401 or 429 stay queued and are sent later', async () => {
  const v = make();
  await v.finish(info(), await v.before(info()), 200, 0);
  for (const status of [401, 429]) {
    engine.forced['POST /v1/events'] = { status, body: { error: 'x' } };
    await v.flush();
    assert.equal(v.queue.length, 1, String(status));
  }
});

test('an engine that is down does not make flush throw', async () => {
  const v = make({ baseUrl: 'http://127.0.0.1:9' });
  await v.finish(info(), await v.before(info()), 200, 0);
  await v.flush();
  assert.equal(v.queue.length, 1);
});

test('a batch has at most 200 events and the queue drops the oldest when full', async () => {
  const v = make({ maxQueue: 250 });
  for (let n = 0; n < 260; n++) v.enqueue({ id: `e_${n}` });
  assert.equal(v.queue.length, 250);
  assert.equal(v.queue[0].id, 'e_10');
  await v.flush();
  assert.equal(engine.requests[0].body.events.length, 200);
  assert.equal(v.queue.length, 50);
});

// --- blocklist ---

test('the blocklist is read without since first, then with it, and 304 keeps the list', async () => {
  engine.blocklist.sessions = ['s_bad'];
  const v = make();
  await v.refreshBlocklist();
  await v.refreshBlocklist();
  const [first, second] = engine.of('GET', '/v1/blocklist');
  assert.deepEqual([first.query.since, second.query.since], [undefined, '1']);
  assert.equal(v.isBlocked({ session: 's_bad' }), true);
});

test('a new blocklist replaces the old one', async () => {
  engine.blocklist.ips = ['203.0.113.9'];
  const v = make();
  await v.refreshBlocklist();
  engine.version = 2;
  engine.blocklist = { users: ['u_9'], sessions: [], ips: [] };
  await v.refreshBlocklist();
  assert.equal(v.isBlocked({ ip: '203.0.113.9' }), false);
  assert.equal(v.isBlocked({ user: 'u_9' }), true);
});

test('blocklist matches by ip, session or user, and not by missing values', async () => {
  engine.blocklist = { users: ['u_1'], sessions: ['s_1'], ips: ['1.2.3.4'] };
  const v = make();
  await v.refreshBlocklist();
  assert.equal(v.isBlocked({ ip: '1.2.3.4' }), true);
  assert.equal(v.isBlocked({ session: 's_1' }), true);
  assert.equal(v.isBlocked({ user: 'u_1' }), true);
  assert.equal(v.isBlocked({ ip: '', session: null, user: undefined }), false);
  assert.equal(v.isBlocked({ ip: '5.6.7.8', session: 's_2', user: 'u_2' }), false);
});

test('failures, 401, 429 and garbage keep the last known list', async () => {
  engine.blocklist.sessions = ['s_bad'];
  const v = make();
  await v.refreshBlocklist();
  engine.version = 2;
  for (const forced of [{ status: 401, body: { error: 'unauthorized' } }, { status: 429, body: { error: 'rate_limited' } },
    { status: 200, body: { nope: true } }, { status: 200, body: { version: 'x', users: 5 } }, { status: 500, body: {} }]) {
    engine.forced['GET /v1/blocklist'] = forced;
    await v.refreshBlocklist();
    assert.equal(v.isBlocked({ session: 's_bad' }), true, JSON.stringify(forced));
  }
});

test('before any load, an engine that rejects the key lets everything through', async () => {
  engine.forced['GET /v1/blocklist'] = { status: 401, body: { error: 'unauthorized' } };
  const v = make();
  await v.refreshBlocklist();
  assert.equal(v.isBlocked({ ip: '1.2.3.4', session: 's', user: 'u' }), false);
});

// --- enforcement ---

test('a blocked request is refused and reported as a 403 event with its route template', async () => {
  engine.blocklist.sessions = [(await (async () => { const { sessionHash } = await import('../src/identity.js'); return sessionHash('abc'); })())];
  const v = make();
  await v.refreshBlocklist();
  const ctx = await v.before(info({ routeBeforeRun: () => '/invoices/{id}' }));
  assert.equal(ctx.blocked, true);
  assert.equal(ctx.newDevice, false); // no device cookie is issued to a blocked client
  await v.flush();
  const [e] = engine.requests.find((r) => r.path === '/v1/events').body.events;
  assert.deepEqual([e.status, e.route, e.user_id], [403, '/invoices/{id}', 'u_42']);
  assert.equal(e.bytes, 19); // {"error":"blocked"}
});

test('a blocked request without identity is not reported', async () => {
  engine.blocklist.ips = ['198.51.100.1'];
  const v = make();
  await v.refreshBlocklist();
  const ctx = await v.before(info({ req: {} }));
  assert.equal(ctx.blocked, true);
  assert.equal(v.queue.length, 0);
});

test('excluded paths pass straight through: no event, no cookie, no blocklist check', async () => {
  engine.blocklist.ips = ['198.51.100.1'];
  const v = make();
  await v.refreshBlocklist();
  for (const path of ['/static', '/static/app.js']) {
    const ctx = await v.before(info({ path }));
    assert.deepEqual(ctx, { excluded: true, blocked: false });
  }
  assert.equal((await v.before(info({ path: '/staticky' }))).blocked, true); // whole segments only
  assert.equal(v.queue.length, 1);
});

// --- background ---

test('the background loops flush events and poll the blocklist on their own, and their timers are unref\'d', async () => {
  engine.blocklist.sessions = ['s_bad'];
  const v = make({ background: true, flushInterval: 30, pollInterval: 30, routesPollInterval: 10000, reportRoutes: false });
  v.start();
  try {
    assert.ok(v.timers.length >= 2 && v.timers.every((t) => t.hasRef() === false));
    await v.finish(info(), await v.before(info()), 200, 0);
    await new Promise((r) => setTimeout(r, 150));
    assert.ok(engine.of('POST', '/v1/events').length >= 1);
    assert.equal(v.isBlocked({ session: 's_bad' }), true);
  } finally { v.close(); }
  assert.deepEqual(v.timers, []);
});

test('a 429 from the engine does not turn the background loops into a hot loop', async () => {
  engine.forced['GET /v1/blocklist'] = { status: 429, body: { error: 'rate_limited' } };
  const v = make({ background: true, flushInterval: 10000, pollInterval: 50, routesPollInterval: 10000, reportRoutes: false });
  v.start();
  await new Promise((r) => setTimeout(r, 230));
  v.close();
  assert.ok(engine.of('GET', '/v1/blocklist').length <= 6);
});

test('background false starts no timers', () => {
  const v = make();
  v.start();
  assert.deepEqual(v.timers, []);
});
