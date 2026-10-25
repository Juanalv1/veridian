import { test } from 'node:test';
import assert from 'node:assert/strict';
import { resolveConfig, VeridianConfigError, DEFAULT_BASE_URL } from '../src/config.js';

const user = () => null;
const cfg = (options = {}, env = {}) => resolveConfig({ user, ...options }, env);

test('the key and URL come from the environment', () => {
  const c = cfg({}, { VERIDIAN_API_KEY: 'from-env', VERIDIAN_URL: 'https://engine.example/' });
  assert.equal(c.apiKey, 'from-env');
  assert.equal(c.baseUrl, 'https://engine.example');
});

test('without a URL the production engine is used', () => {
  assert.equal(cfg({}, { VERIDIAN_API_KEY: 'k' }).baseUrl, DEFAULT_BASE_URL);
  assert.equal(DEFAULT_BASE_URL, 'https://veridian-engine.onrender.com');
});

test('options win over the environment', () => {
  const c = cfg({ apiKey: 'arg', baseUrl: 'http://arg.example' }, { VERIDIAN_API_KEY: 'e', VERIDIAN_URL: 'https://e.example' });
  assert.deepEqual([c.apiKey, c.baseUrl], ['arg', 'http://arg.example']);
});

for (const key of [undefined, '', '   ']) {
  test(`a missing key (${JSON.stringify(key)}) is a clear error naming the variable`, () => {
    assert.throws(
      () => cfg({ apiKey: key }, {}),
      (err) => err instanceof VeridianConfigError && err.message.includes('VERIDIAN_API_KEY') && err.message.includes('panel'),
    );
  });
}

for (const url of ['veridian-engine.onrender.com', 'ftp://x', 'http://']) {
  test(`a bad URL (${url}) is a clear error naming the variable`, () => {
    assert.throws(() => cfg({ apiKey: 'k', baseUrl: url }, {}), (err) => err.message.includes('VERIDIAN_URL'));
  });
}

test('the config error is an Error with its own name', () => {
  const err = new VeridianConfigError('x');
  assert.ok(err instanceof Error);
  assert.equal(err.name, 'VeridianConfigError');
});

test('user and session must be functions', () => {
  assert.throws(() => cfg({ apiKey: 'k', user: 'u_1' }, {}), (e) => e instanceof VeridianConfigError && e.message.includes('`user`'));
  assert.throws(() => cfg({ apiKey: 'k', session: 5 }, {}), (e) => e.message.includes('`session`'));
});

test('without user it warns that nothing will be reported', () => {
  const warnings = [];
  resolveConfig({ apiKey: 'k', logger: { warn: (m) => warnings.push(m) } }, {});
  assert.equal(warnings.length, 1);
  assert.match(warnings[0], /no `user` given/);
});

test('defaults match the Python SDK', () => {
  const c = cfg({ apiKey: 'k' }, {});
  assert.equal(c.trustedProxies, 0);
  assert.deepEqual(c.excludePaths, ['/static']);
  assert.equal(c.flushInterval, 2000);
  assert.equal(c.pollInterval, 2000);
  assert.equal(c.routesPollInterval, 30000);
  assert.equal(c.checkTimeout, 12000);
  assert.equal(c.reportRoutes, true);
  assert.equal(c.maxQueue, 1000);
});

test('trustedProxies must be a non-negative integer', () => {
  for (const bad of [-1, 1.5, '1', NaN]) {
    assert.throws(() => cfg({ apiKey: 'k', trustedProxies: bad }, {}), (e) => e.message.includes('trustedProxies'));
  }
  assert.equal(cfg({ apiKey: 'k', trustedProxies: 2 }, {}).trustedProxies, 2);
});

test('excludePaths drops trailing slashes', () => {
  assert.deepEqual(cfg({ apiKey: 'k', excludePaths: ['/assets/', '/health'] }, {}).excludePaths, ['/assets', '/health']);
});
