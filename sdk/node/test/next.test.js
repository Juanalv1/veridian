import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { createVeridian } from '../src/next.js';
import { sessionHash } from '../src/identity.js';
import { startEngine } from './helpers/engine.js';

const logger = { warn() {}, error() {} };
let engine;
beforeEach(async () => { engine = await startEngine({ routes: [{ method: 'POST', route: '/api/payouts', class: 'critical' }] }); });
afterEach(async () => { await engine.close(); });

function make(options = {}) {
  return createVeridian({
    apiKey: 'test-key', baseUrl: engine.url, user: (request) => request.headers.get('x-login'), background: false, logger, ...options,
  });
}
const req = (path, init = {}) => new Request(`http://app.test${path}`, init);
const events = async (protect) => { await protect.veridian.flush(); return engine.of('POST', '/v1/events').flatMap((r) => r.body.events); };

test('a wrapped handler runs and the request is reported with the route given', async () => {
  const protect = make({ trustedProxies: 1 });
  const handler = protect(async () => new Response('{"ok":true}', { headers: { 'content-length': '11' } }), { route: '/api/items/[id]' });
  const res = await handler(req('/api/items/7', { headers: { 'x-login': 'u_1', cookie: 'sessionid=abc', 'x-forwarded-for': '203.0.113.9' } }));
  assert.equal(res.status, 200);
  const [e] = await events(protect);
  assert.deepEqual([e.user_id, e.route, e.status, e.ip, e.method], ['u_1', '/api/items/{id}', 200, '203.0.113.9', 'GET']);
  assert.equal(e.bytes, 11); // from Content-Length: a Web Response has no other cheap size
});

test('without a route option the pathname is the route', async () => {
  const protect = make();
  await protect(async () => new Response('x'))(req('/api/ping', { headers: { 'x-login': 'u_1', cookie: 'sessionid=abc' } }));
  assert.equal((await events(protect))[0].route, '/api/ping');
});

test('the device cookie is added to the response once, and not when the client already has it', async () => {
  const protect = make();
  const handler = protect(async () => new Response('x'));
  const first = await handler(req('/a', { headers: { 'x-login': 'u_1' } }));
  assert.match(first.headers.get('set-cookie'), /^vrd_did=d_[0-9a-f]{12};/);
  const second = await handler(req('/a', { headers: { 'x-login': 'u_1', cookie: 'vrd_did=d_abc' } }));
  assert.equal(second.headers.get('set-cookie'), null);
});

test('an immutable response still gets the cookie', async () => {
  const protect = make();
  const frozen = Response.redirect('http://app.test/elsewhere', 302); // headers are immutable
  const res = await protect(async () => frozen)(req('/a', { headers: { 'x-login': 'u_1' } }));
  assert.equal(res.status, 302);
  assert.match(res.headers.get('set-cookie'), /^vrd_did=/);
  assert.equal(res.headers.get('location'), 'http://app.test/elsewhere');
});

test('a blocked request gets 403 {"error":"blocked"} and the handler is never called', async () => {
  engine.blocklist.sessions = [sessionHash('bad')];
  const protect = make();
  await protect.veridian.refreshBlocklist();
  let called = false;
  const res = await protect(async () => { called = true; return new Response('x'); })(req('/a', { headers: { cookie: 'sessionid=bad' } }));
  assert.equal(res.status, 403);
  assert.deepEqual(await res.json(), { error: 'blocked' });
  assert.equal(res.headers.get('content-type'), 'application/json');
  assert.equal(called, false);
});

test('without trusted proxies the IP cannot be known, so X-Forwarded-For is ignored and critical routes are not checked', async () => {
  engine.verdict = 'block';
  const protect = make();
  await protect.veridian.refreshRoutes();
  const res = await protect(async () => new Response('x'), { route: '/api/payouts' })(
    req('/api/payouts', { method: 'POST', headers: { 'x-login': 'u_1', cookie: 'sessionid=abc', 'x-forwarded-for': '203.0.113.9' } }));
  assert.equal(res.status, 200);
  assert.equal(engine.of('POST', '/v1/check').length, 0);
});

test('an ip option can say where the client address is', async () => {
  const protect = make({ ip: (request) => request.headers.get('x-real-ip') });
  await protect(async () => new Response('x'))(req('/a', { headers: { 'x-login': 'u_1', cookie: 'sessionid=abc', 'x-real-ip': '198.51.100.5' } }));
  assert.equal((await events(protect))[0].ip, '198.51.100.5');
});

test('a critical request is checked and refused before the handler runs', async () => {
  engine.verdict = 'block';
  const protect = make({ trustedProxies: 1 });
  await protect.veridian.refreshRoutes();
  let called = false;
  const res = await protect(async () => { called = true; return new Response('x'); }, { route: '/api/payouts' })(
    req('/api/payouts', { method: 'POST', headers: { 'x-login': 'u_1', cookie: 'sessionid=abc', 'x-forwarded-for': '203.0.113.9' } }));
  assert.equal(res.status, 403);
  assert.equal(called, false);
  assert.equal(res.headers.get('set-cookie'), null);
});

test('a handler that throws is reported as a 500 and the error still propagates', async () => {
  const protect = make();
  await assert.rejects(protect(async () => { throw new Error('app bug'); })(req('/a', { headers: { 'x-login': 'u_1', cookie: 'sessionid=abc' } })), /app bug/);
  assert.equal((await events(protect))[0].status, 500);
});

test('an internal failure of the SDK never breaks the handler', async () => {
  const protect = make();
  protect.veridian.before = async () => { throw new Error('boom'); };
  const res = await protect(async () => new Response('fine'))(req('/a'));
  assert.equal(await res.text(), 'fine');
});

test('the handler receives the arguments Next passes (request and context)', async () => {
  const protect = make();
  const seen = [];
  await protect(async (request, ctx) => { seen.push(ctx); return new Response('x'); })(req('/a'), { params: { id: '1' } });
  assert.deepEqual(seen, [{ params: { id: '1' } }]);
});
