// Self-unlock by email: the optional `userEmail`, and the block message that says an email was sent, only when the
// engine's signal says so (`self_unlock_sessions` in the blocklist, `self_unlock` in the check answer).
import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import express from 'express';
import { Veridian } from '../src/core.js';
import { resolveConfig, VeridianConfigError } from '../src/config.js';
import { veridian } from '../src/index.js';
import { createVeridian } from '../src/next.js';
import { sessionHash } from '../src/identity.js';
import { startEngine } from './helpers/engine.js';

const MESSAGE = 'Detectamos un acceso inusual. Te enviamos un correo para confirmar que fuiste tú.';
const GENERIC = '{"error":"blocked"}';
const WITH_MESSAGE = { error: 'blocked', message: MESSAGE };
const EMAIL = 'ana@example.com';
const logs = [];
const logger = { warn: (m) => logs.push(m), error: (m) => logs.push(m) };

let engine;
beforeEach(async () => {
  logs.length = 0;
  engine = await startEngine({ routes: [{ method: 'POST', route: '/payouts/bank-account', class: 'critical' }] });
});
afterEach(async () => { await engine.close(); });

function make(options = {}) {
  return new Veridian(resolveConfig({
    apiKey: 'test-key', baseUrl: engine.url, user: (req) => req.uid ?? null, background: false, logger, ...options,
  }, {}));
}
function info(over = {}) {
  return {
    method: 'GET', path: '/items/1', ip: '198.51.100.1', req: { uid: 'u_42', email: EMAIL },
    headers: { cookie: 'sessionid=bad', 'user-agent': 'UA' },
    routeBeforeRun: () => '/items/{id}', routeAfterRun: () => '/items/{id}', ...over,
  };
}
const sent = async (v) => { await v.flush(); return engine.of('POST', '/v1/events').flatMap((r) => r.body.events); };

// --- userEmail in events and check ---

test('userEmail is sent in the event when given', async () => {
  const v = make({ userEmail: (req) => req.email });
  await v.finish(info(), await v.before(info()), 200, 0);
  assert.equal((await sent(v))[0].user_email, EMAIL);
});

test('without the option events carry no user_email', async () => {
  const v = make();
  await v.finish(info(), await v.before(info()), 200, 0);
  assert.ok(!('user_email' in (await sent(v))[0]));
});

test('a missing email is null, not an error: no key and nothing logged', async () => {
  for (const email of [undefined, null, '', false]) {
    const v = make({ userEmail: (req) => req.email });
    const i = info({ req: { uid: 'u_42', email } });
    await v.finish(i, await v.before(i), 200, 0);
    assert.ok(!('user_email' in (await sent(v)).at(-1)));
  }
  const v = make({ userEmail: (req) => req.user.email }); // req.user is undefined: a TypeError
  await v.finish(info(), await v.before(info()), 200, 0);
  assert.ok(!('user_email' in (await sent(v)).at(-1)));
  assert.deepEqual(logs, []);
});

test('something that is not an address is not sent', async () => {
  const v = make({ userEmail: () => 'not-an-address' });
  await v.finish(info(), await v.before(info()), 200, 0);
  assert.ok(!('user_email' in (await sent(v))[0]));
});

test('an unexpected error in userEmail is logged and the event is still reported', async () => {
  const v = make({ userEmail: () => { throw new Error('boom'); } });
  await v.finish(info(), await v.before(info()), 200, 0);
  const [e] = await sent(v);
  assert.equal(e.user_id, 'u_42');
  assert.ok(!('user_email' in e) && logs.some((m) => m.includes('boom')));
});

test('the pre-check event carries the email', async () => {
  const v = make({ userEmail: (req) => req.email });
  await v.refreshRoutes();
  await v.before(info({ method: 'POST', path: '/payouts/bank-account', routeBeforeRun: () => '/payouts/bank-account' }));
  assert.equal(engine.of('POST', '/v1/check')[0].body.event.user_email, EMAIL);
});

test('userEmail must be a function', () => {
  assert.throws(() => resolveConfig({ apiKey: 'k', user: () => 'u', userEmail: EMAIL }, {}), (e) => e instanceof VeridianConfigError && /userEmail/.test(e.message));
});

// --- the block message (core) ---

async function blockedBy(list, extra = {}) {
  engine.blocklist = { users: [], sessions: [sessionHash('bad')], ips: [], ...extra };
  if (list !== undefined) engine.blocklist.self_unlock_sessions = list;
  const v = make();
  await v.refreshBlocklist();
  return v.before(info());
}

test('a session listed in self_unlock_sessions is blocked with the unlock message flag', async () => {
  const ctx = await blockedBy([sessionHash('bad')]);
  assert.deepEqual([ctx.blocked, ctx.selfUnlock], [true, true]);
});

test('without the signal, or with another session listed, the flag is off', async () => {
  assert.equal((await blockedBy(undefined)).selfUnlock, false);
  assert.equal((await blockedBy([sessionHash('other')])).selfUnlock, false);
  assert.equal((await blockedBy('not-a-list')).selfUnlock, false);
});

test('the signal goes away when the engine stops sending it', async () => {
  engine.blocklist = { users: [], sessions: [sessionHash('bad')], ips: [], self_unlock_sessions: [sessionHash('bad')] };
  const v = make();
  await v.refreshBlocklist();
  engine.version = 2;
  engine.blocklist = { users: [], sessions: [sessionHash('bad')], ips: [] };
  await v.refreshBlocklist();
  assert.equal((await v.before(info())).selfUnlock, false);
});

test('a check block with self_unlock true carries the flag; anything else does not', async () => {
  const post = info({ method: 'POST', path: '/payouts/bank-account', routeBeforeRun: () => '/payouts/bank-account' });
  for (const [extra, expected] of [[{ self_unlock: true }, true], [{}, false], [{ self_unlock: false }, false], [{ self_unlock: 'yes' }, false]]) {
    engine.verdict = 'block';
    engine.checkExtra = extra;
    const v = make();
    await v.refreshRoutes();
    const ctx = await v.before(post);
    assert.deepEqual([ctx.blocked, ctx.selfUnlock], [true, expected], JSON.stringify(extra));
  }
});

// --- the 403 body in the adapters ---

async function serve(app) {
  const server = http.createServer(app);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  return { base: `http://127.0.0.1:${server.address().port}`, close: () => { server.closeAllConnections?.(); return new Promise((r) => server.close(r)); } };
}

test('express: the 403 says an email was sent only with the signal; otherwise the old body exactly', async () => {
  engine.blocklist = { users: [], sessions: [sessionHash('bad'), sessionHash('plain')], ips: [], self_unlock_sessions: [sessionHash('bad')] };
  const app = express();
  const mw = veridian({ apiKey: 'test-key', baseUrl: engine.url, user: () => 'u_1', background: false, logger });
  app.use(mw);
  app.get('/items/:id', (req, res) => res.json({ ok: true }));
  await mw.veridian.refreshBlocklist();
  const { base, close } = await serve(app);
  try {
    const listed = await fetch(`${base}/items/1`, { headers: { cookie: 'sessionid=bad' } });
    assert.equal(listed.status, 403);
    assert.deepEqual(await listed.json(), WITH_MESSAGE);
    const plain = await fetch(`${base}/items/1`, { headers: { cookie: 'sessionid=plain' } });
    assert.equal(plain.status, 403);
    assert.equal(await plain.text(), GENERIC);
  } finally { await close(); }
});

test('next: the 403 says an email was sent only with the signal; otherwise the old body exactly', async () => {
  engine.blocklist = { users: [], sessions: [sessionHash('bad'), sessionHash('plain')], ips: [], self_unlock_sessions: [sessionHash('bad')] };
  const protect = createVeridian({ apiKey: 'test-key', baseUrl: engine.url, user: () => 'u_1', background: false, logger });
  await protect.veridian.refreshBlocklist();
  const handler = protect(async () => new Response('x'));
  const listed = await handler(new Request('http://app.test/a', { headers: { cookie: 'sessionid=bad' } }));
  assert.equal(listed.status, 403);
  assert.deepEqual(await listed.json(), WITH_MESSAGE);
  const plain = await handler(new Request('http://app.test/a', { headers: { cookie: 'sessionid=plain' } }));
  assert.equal(await plain.text(), GENERIC);
});

test('next: a critical-route check block with self_unlock says the email was sent', async () => {
  engine.verdict = 'block';
  engine.checkExtra = { self_unlock: true };
  const protect = createVeridian({ apiKey: 'test-key', baseUrl: engine.url, user: () => 'u_1', trustedProxies: 1, background: false, logger });
  await protect.veridian.refreshRoutes();
  let called = false;
  const handler = protect(async () => { called = true; return new Response('x'); }, { route: '/payouts/bank-account' });
  const res = await handler(new Request('http://app.test/payouts/bank-account', { method: 'POST', headers: { cookie: 'sessionid=s1', 'x-forwarded-for': '203.0.113.9' } }));
  assert.equal(res.status, 403);
  assert.deepEqual(await res.json(), WITH_MESSAGE);
  assert.equal(called, false);
});
