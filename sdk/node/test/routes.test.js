import { test } from 'node:test';
import assert from 'node:assert/strict';
import express from 'express';
import express4 from 'express4';
import { normalizeTemplate, routeMatcher, expressRoutes } from '../src/routes.js';

test('route templates use {name} for parameters, like the Python SDK and the engine', () => {
  assert.equal(normalizeTemplate('/invoices/:id'), '/invoices/{id}');
  assert.equal(normalizeTemplate('/a/:x/b/:y?'), '/a/{x}/b/{y}');
  assert.equal(normalizeTemplate('/blog/[slug]'), '/blog/{slug}');
  assert.equal(normalizeTemplate('/docs/[...path]'), '/docs/{path}');
  assert.equal(normalizeTemplate('/plain'), '/plain');
});

test('the matcher finds the template of a path without running anything', () => {
  const match = routeMatcher([
    { method: 'GET', route: '/invoices/export' }, { method: 'GET', route: '/invoices/{id}' }, { method: 'POST', route: '/invoices/{id}/pay' },
  ]);
  assert.equal(match('GET', '/invoices/export'), '/invoices/export');
  assert.equal(match('GET', '/invoices/42'), '/invoices/{id}');
  assert.equal(match('post', '/invoices/42/pay'), '/invoices/{id}/pay');
  assert.equal(match('GET', '/invoices/42/pay'), null);
  assert.equal(match('DELETE', '/invoices/42'), null);
  assert.equal(match('GET', '/invoices/42/'), '/invoices/{id}');
});

for (const [name, lib] of [['express 5', express], ['express 4', express4]]) {
  test(`${name}: top-level routes are found with their methods and templates`, () => {
    const app = lib();
    app.get('/invoices', () => {});
    app.get('/invoices/:id', () => {});
    app.post('/payouts/bank-account', () => {});
    app.put('/x', () => {});
    app.delete('/x', () => {});
    app.patch('/x', () => {});
    app.head('/ignored', () => {});
    app.use((req, res, next) => next());
    assert.deepEqual(expressRoutes(app).map((r) => `${r.method} ${r.route}`).sort(), [
      'DELETE /x', 'GET /invoices', 'GET /invoices/{id}', 'PATCH /x', 'POST /payouts/bank-account', 'PUT /x',
    ]);
  });

  test(`${name}: app.all expands to the reported methods`, () => {
    const app = lib();
    app.all('/any', () => {});
    assert.deepEqual(expressRoutes(app).map((r) => r.method).sort(), ['DELETE', 'GET', 'PATCH', 'POST', 'PUT']);
  });

  test(`${name}: a router mounted at the root is walked`, () => {
    const app = lib();
    const router = lib.Router();
    router.get('/inner/:id', () => {});
    app.use(router);
    assert.deepEqual(expressRoutes(app).map((r) => r.route), ['/inner/{id}']);
  });

  test(`${name}: an app with no routes gives an empty list`, () => {
    assert.deepEqual(expressRoutes(lib()), []);
  });
}

test('express 4: routers mounted on a path, with or without parameters, get their prefix', () => {
  const app = express4();
  const router = express4.Router();
  router.get('/items/:id', () => {});
  app.use('/api', router);
  app.use('/orgs/:orgId', router);
  assert.deepEqual(expressRoutes(app).map((r) => r.route).sort(), ['/api/items/{id}', '/orgs/{orgId}/items/{id}']);
});

test('express 5: a router mounted on a path cannot be read, so it is left out instead of guessed', () => {
  const app = express();
  const router = express.Router();
  router.get('/items', () => {});
  app.use('/api', router);
  app.get('/top', () => {});
  assert.deepEqual(expressRoutes(app).map((r) => r.route), ['/top']);
});

test('things that are not an app give an empty list', () => {
  assert.deepEqual(expressRoutes(undefined), []);
  assert.deepEqual(expressRoutes({}), []);
});
