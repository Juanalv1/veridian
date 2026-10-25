import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { Veridian } from '../src/core.js';
import { resolveConfig } from '../src/config.js';
import { startEngine } from './helpers/engine.js';

let engine;
beforeEach(async () => { engine = await startEngine({ routes: [{ method: 'POST', route: '/payouts/bank-account', class: 'critical' }, { method: 'GET', route: '/invoices/export', class: 'sensitive' }] }); });
afterEach(async () => { await engine.close(); });

const logger = { warn() {}, error() {} };
function make(options = {}) {
  return new Veridian(resolveConfig({
    apiKey: 'test-key', baseUrl: engine.url, user: (req) => req.uid ?? null, background: false, logger, ...options,
  }, {}));
}
function info(over = {}) {
  return {
    method: 'POST', path: '/payouts/bank-account', ip: '198.51.100.1', req: { uid: 'u_42' },
    headers: { cookie: 'sessionid=abc', 'user-agent': 'UA' },
    routeBeforeRun: () => '/payouts/bank-account', routeAfterRun: () => '/payouts/bank-account', ...over,
  };
}
const loaded = async (options) => { const v = make(options); await v.refreshRoutes(); return v; };

// --- critical routes ---

test('only the routes classified critical are kept, as METHOD + template', async () => {
  const v = await loaded();
  assert.deepEqual([...v.critical], ['POST /payouts/bank-account']);
  assert.equal(engine.of('GET', '/v1/routes')[0].headers.authorization, 'Bearer test-key');
});

test('a route refresh picks up a newly critical route and keeps the last set on errors and garbage', async () => {
  const v = await loaded();
  engine.routes.push({ method: 'delete', route: '/account', class: 'critical' });
  await v.refreshRoutes();
  assert.deepEqual([...v.critical].sort(), ['DELETE /account', 'POST /payouts/bank-account']);
  for (const forced of [{ status: 500, body: {} }, { status: 401, body: { error: 'unauthorized' } }, { status: 429, body: { error: 'rate_limited' } },
    { status: 200, body: { nope: 1 } }, { status: 200, body: { routes: [{ method: 'GET' }] } }]) {
    engine.forced['GET /v1/routes'] = forced;
    await v.refreshRoutes();
    assert.equal(v.critical.size, 2, JSON.stringify(forced));
  }
});

// --- pre-check (contract 9) ---

test('a block verdict refuses the request, with the same id as the batched event and no device cookie', async () => {
  engine.verdict = 'block';
  const v = await loaded();
  const ctx = await v.before(info());
  assert.deepEqual([ctx.blocked, ctx.newDevice], [true, false]);
  const [check] = engine.of('POST', '/v1/check');
  assert.deepEqual([check.body.event.status, check.body.event.bytes, check.body.event.route], [200, 0, '/payouts/bank-account']);
  await v.flush();
  const [event] = engine.of('POST', '/v1/events')[0].body.events;
  assert.equal(event.id, check.body.event.id);
  assert.equal(event.status, 403);
});

test('an allow verdict lets the request run with the id the final event will have', async () => {
  const v = await loaded();
  const ctx = await v.before(info());
  assert.equal(ctx.blocked, false);
  await v.finish(info(), ctx, 200, 10);
  await v.flush();
  const [check] = engine.of('POST', '/v1/check');
  assert.equal(check.body.event.id, engine.of('POST', '/v1/events')[0].body.events[0].id);
  assert.equal(check.body.event.device_id, ctx.deviceId);
});

test('garbage, non-200, an unknown verdict, 401 and 429 all let the request through', async () => {
  const v = await loaded();
  for (const forced of [{ status: 500, body: {} }, { status: 401, body: { error: 'unauthorized' } }, { status: 429, body: { error: 'rate_limited' } },
    { status: 200, body: { verdict: 'maybe' } }, { status: 200, body: [] }, { status: 200, body: undefined }]) {
    engine.forced['POST /v1/check'] = forced;
    assert.equal((await v.before(info())).blocked, false, JSON.stringify(forced));
  }
  assert.equal(engine.of('POST', '/v1/check').length, 6); // no retries
});

test('an engine that is down lets the request through', async () => {
  const v = await loaded();
  v.config.baseUrl = 'http://127.0.0.1:9';
  assert.equal((await v.before(info())).blocked, false);
});

test('a check slower than the timeout lets the request through', async () => {
  engine.verdict = 'block';
  engine.delay['POST /v1/check'] = 600;
  const v = await loaded({ checkTimeout: 100 });
  const started = Date.now();
  assert.equal((await v.before(info())).blocked, false);
  assert.ok(Date.now() - started < 500);
});

test('the check timeout is 12 seconds by default', () => {
  assert.equal(make().config.checkTimeout, 12000);
});

test('non-critical routes, other methods and unloaded routes are not checked', async () => {
  const v = await loaded();
  await v.before(info({ method: 'GET', routeBeforeRun: () => '/payouts/bank-account' }));
  await v.before(info({ method: 'GET', path: '/invoices', routeBeforeRun: () => '/invoices' }));
  const fresh = make(); // routes never loaded
  await fresh.before(info());
  assert.equal(engine.of('POST', '/v1/check').length, 0);
});

test('a critical request without a user, a session or an IP is not checked', async () => {
  engine.verdict = 'block';
  const v = await loaded();
  for (const over of [{ req: {} }, { headers: { 'user-agent': 'UA' } }, { ip: '' }]) {
    assert.equal((await v.before(info(over))).blocked, false);
  }
  assert.equal(engine.of('POST', '/v1/check').length, 0);
});

test('a blocklisted request is refused without asking the engine', async () => {
  engine.blocklist.ips = ['198.51.100.1'];
  const v = await loaded();
  await v.refreshBlocklist();
  assert.equal((await v.before(info())).blocked, true);
  assert.equal(engine.of('POST', '/v1/check').length, 0);
});

test('the background loads the routes at startup', async () => {
  const v = make({ background: true, flushInterval: 10000, pollInterval: 10000, routesPollInterval: 10000, reportRoutes: false });
  v.start();
  await new Promise((r) => setTimeout(r, 100));
  v.close();
  assert.equal(v.critical.size, 1);
});

// --- route report (contract 16) ---

const some = [{ method: 'get', route: '/invoices/{id}', summary: 'One invoice' }, { method: 'POST', route: '/transfers', description: 'Moves funds out.' }];

test('the report sends method, route and text only', async () => {
  const v = make({ routes: some });
  assert.equal(await v.reportRoutes(), true);
  const [put] = engine.of('PUT', '/v1/routes/catalog');
  assert.deepEqual(put.body, { routes: [
    { method: 'GET', route: '/invoices/{id}', summary: 'One invoice', description: '' },
    { method: 'POST', route: '/transfers', summary: '', description: 'Moves funds out.' },
  ] });
});

test('the report skips excluded paths, bad methods, bad routes and repeats, and caps size', async () => {
  const long = 'x'.repeat(3000);
  const v = make({ routes: [
    { method: 'GET', route: '/static/app.js' }, { method: 'TRACE', route: '/t' }, { method: 'GET', route: 'no-slash' },
    { method: 'GET', route: '/a', summary: long }, { method: 'GET', route: '/a' }, null, 'junk',
  ] });
  await v.reportRoutes();
  const { routes } = engine.of('PUT', '/v1/routes/catalog')[0].body;
  assert.equal(routes.length, 1);
  assert.equal(routes[0].summary.length, 2000);
  const many = make({ routes: Array.from({ length: 1200 }, (_, n) => ({ method: 'GET', route: `/r${n}` })) });
  await many.reportRoutes();
  assert.equal(engine.of('PUT', '/v1/routes/catalog')[1].body.routes.length, 1000);
});

test('the routes may come from a function; with none, nothing is sent', async () => {
  assert.equal(await make({ routes: () => some }).reportRoutes(), true);
  assert.equal(engine.of('PUT', '/v1/routes/catalog').length, 1);
  assert.equal(await make().reportRoutes(), true);
  assert.equal(await make({ routes: () => [] }).reportRoutes(), true);
  assert.equal(engine.of('PUT', '/v1/routes/catalog').length, 1);
});

test('a failed report returns false and never throws', async () => {
  const v = make({ routes: some });
  engine.forced['PUT /v1/routes/catalog'] = { status: 500, body: {} };
  assert.equal(await v.reportRoutes(), false);
  v.config.baseUrl = 'http://127.0.0.1:9';
  assert.equal(await v.reportRoutes(), false);
});

test('the report runs in the background until the engine accepts it, then stops', async () => {
  engine.forced['PUT /v1/routes/catalog'] = { status: 500, body: {} };
  const v = make({ routes: some, background: true, flushInterval: 10000, pollInterval: 10000, routesPollInterval: 40 });
  v.start();
  await new Promise((r) => setTimeout(r, 120));
  delete engine.forced['PUT /v1/routes/catalog'];
  await new Promise((r) => setTimeout(r, 120));
  const before = engine.of('PUT', '/v1/routes/catalog').length;
  await new Promise((r) => setTimeout(r, 120));
  v.close();
  assert.ok(before >= 3);
  assert.equal(engine.of('PUT', '/v1/routes/catalog').length, before);
});

test('the report can be turned off', async () => {
  const v = make({ routes: some, reportRoutes: false, background: true, routesPollInterval: 10000 });
  v.start();
  await new Promise((r) => setTimeout(r, 80));
  v.close();
  assert.equal(engine.of('PUT', '/v1/routes/catalog').length, 0);
});
