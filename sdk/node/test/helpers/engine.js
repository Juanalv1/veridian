// A local HTTP server that plays the Veridian engine (contracts 1, 2, 8, 9 and 16).
import http from 'node:http';

export async function startEngine(overrides = {}) {
  const engine = {
    requests: [], // {method, path, query, headers, body}
    key: 'test-key',
    version: 1,
    blocklist: { users: [], sessions: [], ips: [] },
    routes: [], // [{method, route, class}]
    verdict: 'allow',
    checkExtra: {}, // extra fields of the check answer, e.g. { self_unlock: true }
    // Replace any of these to force a status: {status, body}
    forced: {}, // keyed by "METHOD /path"
    delay: {}, // keyed by "METHOD /path" -> ms
    ...overrides,
  };
  const server = http.createServer((req, res) => {
    const chunks = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', async () => {
      const url = new URL(req.url, 'http://engine');
      const raw = Buffer.concat(chunks).toString();
      const record = {
        method: req.method, path: url.pathname, query: Object.fromEntries(url.searchParams),
        headers: req.headers, body: raw ? JSON.parse(raw) : undefined,
      };
      engine.requests.push(record);
      const send = (status, body) => {
        res.writeHead(status, { 'content-type': 'application/json' });
        res.end(body === undefined ? '' : JSON.stringify(body));
      };
      const id = `${req.method} ${url.pathname}`;
      if (engine.delay[id]) await new Promise((r) => setTimeout(r, engine.delay[id]));
      if (req.headers.authorization !== `Bearer ${engine.key}`) return send(401, { error: 'unauthorized' });
      if (engine.forced[id]) return send(engine.forced[id].status, engine.forced[id].body);
      if (id === 'POST /v1/events') return send(202, { accepted: record.body.events.length });
      if (id === 'GET /v1/blocklist') {
        if (record.query.since !== undefined && Number(record.query.since) === engine.version) return send(304);
        return send(200, { version: engine.version, ...engine.blocklist });
      }
      if (id === 'GET /v1/routes') return send(200, { routes: engine.routes, default: 'normal' });
      if (id === 'POST /v1/check') return send(200, { verdict: engine.verdict, decision_id: 'd_1', ...engine.checkExtra });
      if (id === 'PUT /v1/routes/catalog') return send(200, { received: record.body.routes.length, to_classify: 0 });
      return send(404, { error: 'not_found' });
    });
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  engine.url = `http://127.0.0.1:${server.address().port}`;
  engine.close = () => new Promise((resolve) => { server.closeAllConnections?.(); server.close(resolve); });
  engine.of = (method, path) => engine.requests.filter((r) => r.method === method && r.path === path);
  return engine;
}

export const silentLogger = { warn() {}, error() {}, log() {} };
