// The README quickstart, run as-is against the real fake engine (edge/fake_engine) through a
// recording proxy. Needs `uv` (the edge project's runner); skipped when it is not installed.
//   cd edge && uv sync     (once)      then      cd sdk/node && node --test
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import http from 'node:http';
import net from 'node:net';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { sessionHash } from '../src/identity.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const pkg = path.resolve(here, '..');
const edge = path.resolve(pkg, '../../edge');
const hasUv = spawnSync('uv', ['--version']).status === 0;
const KEY = 'quickstart-key';

const freePort = () => new Promise((resolve) => {
  const s = net.createServer().listen(0, '127.0.0.1', () => { const { port } = s.address(); s.close(() => resolve(port)); });
});
const until = async (fn, ms = 15000) => {
  const end = Date.now() + ms;
  for (;;) {
    const v = await fn();
    if (v) return v;
    if (Date.now() > end) throw new Error('timed out');
    await new Promise((r) => setTimeout(r, 100));
  }
};

let engine; let proxy; let proxyUrl; const seen = []; const children = [];

before(async () => {
  if (!hasUv) return;
  const port = await freePort();
  engine = spawn('uv', ['run', 'uvicorn', 'fake_engine.app:app', '--port', String(port), '--log-level', 'warning'], {
    cwd: edge, env: { ...process.env, VERIDIAN_TOKENS: KEY }, stdio: 'ignore',
  });
  await until(() => fetch(`http://127.0.0.1:${port}/v1/blocklist`, { headers: { authorization: `Bearer ${KEY}` } }).then((r) => r.ok, () => false), 60000);
  // A recording proxy in front of the engine: what the SDK sent and what the engine answered.
  proxy = http.createServer((req, res) => {
    const chunks = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', async () => {
      const body = Buffer.concat(chunks);
      const upstream = await fetch(`http://127.0.0.1:${port}${req.url}`, {
        method: req.method, headers: { authorization: req.headers.authorization ?? '', 'content-type': 'application/json' },
        body: body.length ? body : undefined,
      });
      const text = await upstream.text();
      seen.push({ method: req.method, path: req.url.split('?')[0], status: upstream.status, auth: req.headers.authorization, body: body.length ? JSON.parse(body) : undefined });
      res.writeHead(upstream.status, { 'content-type': 'application/json' });
      res.end(text);
    });
  });
  await new Promise((r) => proxy.listen(0, '127.0.0.1', r));
  proxyUrl = `http://127.0.0.1:${proxy.address().port}`;
});

after(() => {
  for (const c of children) c.kill();
  engine?.kill();
  proxy?.closeAllConnections?.();
  proxy?.close();
});

function runQuickstart(env) {
  const child = spawn(process.execPath, ['--import', './test/fixtures/preload.mjs', './examples/quickstart.js'], {
    cwd: pkg, env: { ...process.env, VERIDIAN_URL: proxyUrl, ...env }, stdio: ['ignore', 'pipe', 'inherit'],
  });
  children.push(child);
  return new Promise((resolve, reject) => {
    let out = '';
    child.stdout.on('data', (d) => { out += d; const m = /LISTENING (\d+)/.exec(out); if (m) resolve(`http://127.0.0.1:${m[1]}`); });
    child.on('exit', (code) => reject(new Error(`quickstart exited with ${code}`)));
  });
}

test('the README shows the quickstart file exactly', () => {
  const readme = fs.readFileSync(path.join(pkg, 'README.md'), 'utf8');
  const blocks = [...readme.matchAll(/```js\n([\s\S]*?)```/g)].map((m) => m[1].trim());
  assert.ok(blocks.includes(fs.readFileSync(path.join(pkg, 'examples/quickstart.js'), 'utf8').trim()));
});

test('the quickstart, as-is, authenticates against the fake engine, sends events and reports its routes', { skip: !hasUv && 'uv is not installed' }, async () => {
  const base = await runQuickstart({ VERIDIAN_API_KEY: KEY });
  const r = await fetch(`${base}/facturas`, { headers: { 'x-login': 'u_42', cookie: 'sessionid=abc' } });
  assert.equal(r.status, 200);
  assert.match(r.headers.get('set-cookie'), /^vrd_did=/);
  const events = await until(() => seen.find((s) => s.method === 'POST' && s.path === '/v1/events' && s.status === 202));
  assert.equal(events.auth, `Bearer ${KEY}`);
  const [event] = events.body.events;
  assert.deepEqual([event.user_id, event.route, event.method, event.status, event.session_id], ['u_42', '/facturas', 'GET', 200, sessionHash('abc')]);
  assert.ok(seen.some((s) => s.method === 'GET' && s.path === '/v1/blocklist' && s.status === 200), 'the blocklist was read with the key');
  assert.ok(seen.some((s) => s.method === 'GET' && s.path === '/v1/routes' && s.status === 200));
  const report = await until(() => seen.find((s) => s.method === 'PUT' && s.path === '/v1/routes/catalog' && s.status === 200));
  assert.ok(report.body.routes.some((x) => x.method === 'GET' && x.route === '/facturas'));
});

test('with a wrong key the engine answers 401 and the app keeps serving', { skip: !hasUv && 'uv is not installed' }, async () => {
  const before = seen.length;
  const base = await runQuickstart({ VERIDIAN_API_KEY: 'wrong-key' });
  const r = await fetch(`${base}/facturas`, { headers: { 'x-login': 'u_7', cookie: 'sessionid=zzz' } });
  assert.equal(r.status, 200);
  await until(() => seen.slice(before).some((s) => s.status === 401));
  assert.equal((await fetch(`${base}/facturas`, { headers: { 'x-login': 'u_7' } })).status, 200);
});

test('without a key the quickstart refuses to start with a clear error', async () => {
  const child = spawnSync(process.execPath, ['--import', './test/fixtures/preload.mjs', './examples/quickstart.js'], {
    cwd: pkg, env: { ...process.env, VERIDIAN_API_KEY: '' }, encoding: 'utf8',
  });
  assert.notEqual(child.status, 0);
  assert.match(child.stderr, /VERIDIAN_API_KEY/);
});

test('a CommonJS app can load the package (require of an ES module, Node 20.19+ / 22.12+)', () => {
  const run = spawnSync(process.execPath, ['-e', "const s = require('veridian-sdk'); console.log(typeof s.veridian)"], { cwd: pkg, encoding: 'utf8' });
  assert.equal(run.stdout.trim(), 'function', run.stderr);
});
