import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import express5 from 'express';
import express4 from 'express4';
import { veridian, VeridianConfigError } from '../src/index.js';
import { startEngine } from './helpers/engine.js';

const logger = { warn() {}, error() {} };
let engine;
let servers;
beforeEach(async () => {
  servers = [];
  engine = await startEngine({ routes: [{ method: 'POST', route: '/payouts/bank-account', class: 'critical' }] });
});
afterEach(async () => {
  for (const s of servers) { s.closeAllConnections?.(); await new Promise((r) => s.close(r)); }
  await engine.close();
});

async function serve(app) {
  const server = http.createServer(app);
  servers.push(server);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  return `http://127.0.0.1:${server.address().port}`;
}

for (const [name, express] of [['express 5', express5], ['express 4', express4]]) {
  // The app of the tests: Veridian first, then the app's own login (sets req.user from a header), then routes.
  function build(options = {}, { calls = [] } = {}) {
    const app = express();
    const mw = veridian({ apiKey: 'test-key', baseUrl: engine.url, user: (req) => req.user?.id, background: false, logger, ...options });
    app.use(mw);
    app.use((req, res, next) => { if (req.headers['x-login']) req.user = { id: req.headers['x-login'] }; next(); });
    app.get('/items/:id', (req, res) => { calls.push(req.originalUrl); res.json({ id: req.params.id }); });
    app.post('/payouts/bank-account', (req, res) => { calls.push('payout'); res.json({ ok: true }); });
    app.get('/static/app.js', (req, res) => res.send('js'));
    const api = express.Router();
    api.get('/ping/:n', (req, res) => res.json({ n: req.params.n }));
    app.use('/api', api);
    return { app, mw };
  }
  const flushed = async (mw) => { await mw.veridian.flush(); return engine.of('POST', '/v1/events').flatMap((r) => r.body.events); };

  test(`${name}: a request is reported with its route template, user, session and status`, async () => {
    const { app, mw } = build();
    const base = await serve(app);
    const r = await fetch(`${base}/items/7?x=1`, { headers: { 'x-login': 'u_42', cookie: 'sessionid=abc' } });
    assert.equal(r.status, 200);
    const [e] = await flushed(mw);
    assert.deepEqual([e.user_id, e.route, e.method, e.status], ['u_42', '/items/{id}', 'GET', 200]);
    assert.match(e.session_id, /^s_[0-9a-f]{16}$/);
    assert.equal(e.ip, '127.0.0.1');
    assert.ok(e.bytes > 0);
  });

  test(`${name}: routes inside a mounted router keep their full template`, async () => {
    const { app, mw } = build();
    const base = await serve(app);
    await fetch(`${base}/api/ping/3`, { headers: { 'x-login': 'u_1', cookie: 'sessionid=abc' } });
    const [e] = await flushed(mw);
    assert.equal(e.route, '/api/ping/{n}');
  });

  test(`${name}: the device cookie is set once and reused`, async () => {
    const { app, mw } = build();
    const base = await serve(app);
    const first = await fetch(`${base}/items/1`, { headers: { 'x-login': 'u_1', cookie: 'sessionid=abc' } });
    const cookie = first.headers.get('set-cookie');
    assert.match(cookie, /^vrd_did=d_[0-9a-f]{12}; Path=\/; HttpOnly; SameSite=Lax; Max-Age=31536000$/);
    const id = cookie.split(';')[0].split('=')[1];
    const second = await fetch(`${base}/items/2`, { headers: { 'x-login': 'u_1', cookie: `sessionid=abc; vrd_did=${id}` } });
    assert.equal(second.headers.get('set-cookie'), null);
    const events = await flushed(mw);
    assert.deepEqual(events.map((e) => e.device_id), [id, id]);
  });

  test(`${name}: the device cookie does not clobber the app's own cookies`, async () => {
    const { app } = build();
    app.get('/login', (req, res) => { res.cookie('sid', 'xyz'); res.send('ok'); });
    const base = await serve(app);
    const r = await fetch(`${base}/login`);
    const cookies = r.headers.getSetCookie();
    assert.equal(cookies.length, 2);
    assert.ok(cookies.some((c) => c.startsWith('sid=')) && cookies.some((c) => c.startsWith('vrd_did=')));
  });

  test(`${name}: a blocked request gets 403 {"error":"blocked"}, the app is not called and no cookie is issued`, async () => {
    engine.blocklist.ips = ['203.0.113.9'];
    const calls = [];
    const { app, mw } = build({ trustedProxies: 1 }, { calls });
    await mw.veridian.refreshBlocklist();
    const base = await serve(app);
    const r = await fetch(`${base}/items/7`, { headers: { 'x-forwarded-for': '203.0.113.9' } });
    assert.equal(r.status, 403);
    assert.deepEqual(await r.json(), { error: 'blocked' });
    assert.equal(r.headers.get('set-cookie'), null);
    assert.deepEqual(calls, []);
    const ok = await fetch(`${base}/items/7`, { headers: { 'x-forwarded-for': '203.0.113.10' } });
    assert.equal(ok.status, 200);
  });

  test(`${name}: a blocked session is refused, and X-Forwarded-For is ignored with 0 trusted proxies`, async () => {
    const { sessionHash } = await import('../src/identity.js');
    engine.blocklist = { users: [], ips: ['203.0.113.9'], sessions: [sessionHash('bad')] };
    const { app, mw } = build();
    await mw.veridian.refreshBlocklist();
    const base = await serve(app);
    assert.equal((await fetch(`${base}/items/1`, { headers: { cookie: 'sessionid=bad' } })).status, 403);
    assert.equal((await fetch(`${base}/items/1`, { headers: { 'x-forwarded-for': '203.0.113.9' } })).status, 200);
  });

  test(`${name}: a blocked request is reported as a 403 event with its route template`, async () => {
    engine.blocklist.sessions = [(await import('../src/identity.js')).sessionHash('bad')];
    const { app, mw } = build({ user: (req) => req.headers['x-login'] });
    await mw.veridian.refreshBlocklist();
    const base = await serve(app);
    await fetch(`${base}/items/1`, { headers: { 'x-login': 'u_1' } }); // first request lets the adapter learn the routes
    await fetch(`${base}/items/99`, { headers: { 'x-login': 'u_1', cookie: 'sessionid=bad' } });
    const events = await flushed(mw);
    const blocked = events.find((e) => e.status === 403);
    assert.deepEqual([blocked.route, blocked.user_id], ['/items/{id}', 'u_1']);
  });

  test(`${name}: a critical request is checked first and stopped before the app runs`, async () => {
    engine.verdict = 'block';
    const calls = [];
    const { app, mw } = build({ user: (req) => req.headers['x-login'] }, { calls });
    await mw.veridian.refreshRoutes();
    const base = await serve(app);
    const r = await fetch(`${base}/payouts/bank-account`, { method: 'POST', headers: { 'x-login': 'u_1', cookie: 'sessionid=abc' } });
    assert.equal(r.status, 403);
    assert.deepEqual(await r.json(), { error: 'blocked' });
    assert.equal(r.headers.get('set-cookie'), null);
    assert.deepEqual(calls, []);
    assert.equal(engine.of('POST', '/v1/check')[0].body.event.route, '/payouts/bank-account');
    const events = await flushed(mw);
    assert.equal(events[0].id, engine.of('POST', '/v1/check')[0].body.event.id);
  });

  test(`${name}: a critical request that the engine allows runs, and non-critical ones are never checked`, async () => {
    const calls = [];
    const { app, mw } = build({ user: (req) => req.headers['x-login'] }, { calls });
    await mw.veridian.refreshRoutes();
    const base = await serve(app);
    const h = { 'x-login': 'u_1', cookie: 'sessionid=abc' };
    assert.equal((await fetch(`${base}/payouts/bank-account`, { method: 'POST', headers: h })).status, 200);
    await fetch(`${base}/items/1`, { headers: h });
    assert.deepEqual(calls, ['payout', '/items/1']);
    assert.equal(engine.of('POST', '/v1/check').length, 1);
  });

  test(`${name}: with the engine down every request still works`, async () => {
    const { app } = build({ baseUrl: 'http://127.0.0.1:9' });
    const base = await serve(app);
    const r = await fetch(`${base}/items/7`, { headers: { 'x-login': 'u_1' } });
    assert.equal(r.status, 200);
  });

  test(`${name}: an internal failure of the SDK never breaks the app`, async () => {
    const { app, mw } = build();
    mw.veridian.before = async () => { throw new Error('boom'); };
    const base = await serve(app);
    assert.equal((await fetch(`${base}/items/7`)).status, 200);
  });

  test(`${name}: excluded paths pass straight through with no event and no cookie`, async () => {
    const { app, mw } = build();
    const base = await serve(app);
    const r = await fetch(`${base}/static/app.js`, { headers: { 'x-login': 'u_1' } });
    assert.equal(r.status, 200);
    assert.equal(r.headers.get('set-cookie'), null);
    assert.deepEqual(await flushed(mw), []);
  });

  test(`${name}: the app's routes are reported once the app has them, from its own router`, async () => {
    const { app } = build({ background: true, routesPollInterval: 50, flushInterval: 10000, pollInterval: 10000 });
    const base = await serve(app);
    await fetch(`${base}/items/1`);
    await new Promise((r) => setTimeout(r, 200));
    const put = engine.of('PUT', '/v1/routes/catalog')[0];
    const reported = put.body.routes.map((r) => `${r.method} ${r.route}`);
    assert.ok(reported.includes('GET /items/{id}') && reported.includes('POST /payouts/bank-account'), reported.join());
    assert.ok(!reported.some((r) => r.includes('/static')));
  });

  test(`${name}: an explicit routes option is reported instead, even before any request`, async () => {
    build({ background: true, routes: [{ method: 'POST', route: '/transfers', summary: 'Send money' }], routesPollInterval: 50, flushInterval: 10000, pollInterval: 10000 });
    await new Promise((r) => setTimeout(r, 150));
    assert.deepEqual(engine.of('PUT', '/v1/routes/catalog')[0].body.routes, [{ method: 'POST', route: '/transfers', summary: 'Send money', description: '' }]);
  });

  test(`${name}: close() stops the timers and sends what is queued`, async () => {
    const { app, mw } = build({ background: true, flushInterval: 10000, pollInterval: 10000, routesPollInterval: 10000, reportRoutes: false });
    const base = await serve(app);
    await fetch(`${base}/items/1`, { headers: { 'x-login': 'u_1', cookie: 'sessionid=abc' } });
    await new Promise((r) => setTimeout(r, 30));
    await mw.close();
    assert.equal(engine.of('POST', '/v1/events').flatMap((r) => r.body.events).length, 1);
    assert.deepEqual(mw.veridian.timers, []);
  });
}

test('startup: a missing key fails when veridian() is called, naming the variable', () => {
  const saved = process.env.VERIDIAN_API_KEY;
  delete process.env.VERIDIAN_API_KEY;
  try {
    assert.throws(() => veridian({ user: () => null }), (e) => e instanceof VeridianConfigError && e.message.includes('VERIDIAN_API_KEY'));
  } finally { if (saved !== undefined) process.env.VERIDIAN_API_KEY = saved; }
});

test('startup: the key and URL come from the environment', async () => {
  process.env.VERIDIAN_API_KEY = 'test-key';
  process.env.VERIDIAN_URL = engine.url;
  try {
    const mw = veridian({ user: (req) => req.u, background: false, logger });
    assert.equal(mw.veridian.config.baseUrl, engine.url);
    await mw.veridian.refreshBlocklist();
    assert.equal(engine.of('GET', '/v1/blocklist')[0].headers.authorization, 'Bearer test-key');
  } finally { delete process.env.VERIDIAN_API_KEY; delete process.env.VERIDIAN_URL; }
});
