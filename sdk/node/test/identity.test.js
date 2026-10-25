import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import {
  DEVICE_COOKIE, parseCookies, detectSession, sessionHash, resolveClientIp, newDeviceId, deviceCookieHeader,
} from '../src/identity.js';

const hashed = (v) => 's_' + createHash('sha256').update(v).digest('hex').slice(0, 16);

// --- client IP rule (same as resolve_client_ip in the Python SDK) ---

test('zero trusted proxies ignores a spoofed X-Forwarded-For', () => {
  assert.equal(resolveClientIp('198.51.100.1', '190.202.14.7', 0), '198.51.100.1');
});

test('one trusted proxy takes the rightmost value', () => {
  assert.equal(resolveClientIp('198.51.100.1', '190.202.14.7, 203.0.113.9', 1), '203.0.113.9');
});

test('two trusted proxies take the second from the right', () => {
  assert.equal(resolveClientIp('198.51.100.1', '1.1.1.1, 203.0.113.9, 10.0.0.2', 2), '203.0.113.9');
});

test('fewer values than proxies falls back to the first value', () => {
  assert.equal(resolveClientIp('198.51.100.1', '203.0.113.9, 10.0.0.2', 3), '203.0.113.9');
});

test('trusted proxies without the header uses the peer', () => {
  assert.equal(resolveClientIp('198.51.100.1', undefined, 1), '198.51.100.1');
  assert.equal(resolveClientIp('198.51.100.1', ' , ', 1), '198.51.100.1');
});

test('an IPv4-mapped IPv6 peer is reported as plain IPv4', () => {
  assert.equal(resolveClientIp('::ffff:198.51.100.1', undefined, 0), '198.51.100.1');
});

test('no peer and no trusted proxy gives an empty string', () => {
  assert.equal(resolveClientIp(undefined, undefined, 0), '');
});

// --- cookies and device id ---

test('parseCookies reads a cookie header, unquotes values and survives garbage', () => {
  assert.deepEqual(parseCookies('a=1; b="two"; c'), { a: '1', b: 'two' });
  assert.deepEqual(parseCookies(undefined), {});
  assert.deepEqual(parseCookies(''), {});
});

test('the device id looks like the Python one and the cookie has the same attributes', () => {
  assert.match(newDeviceId(), /^d_[0-9a-f]{12}$/);
  assert.equal(DEVICE_COOKIE, 'vrd_did');
  assert.equal(
    deviceCookieHeader('d_abc'),
    'vrd_did=d_abc; Path=/; HttpOnly; SameSite=Lax; Max-Age=31536000',
  );
});

// --- session detection ---

test('a known session cookie is detected and only its hash is returned', () => {
  for (const name of ['sessionid', 'connect.sid', 'sid', 'session_id', 'PHPSESSID', 'JSESSIONID']) {
    assert.equal(detectSession({ cookie: `${name}=secret` }), hashed('secret'), name);
  }
  assert.equal(sessionHash('secret'), hashed('secret'));
});

test('a generic cookie named session is ignored', () => {
  assert.equal(detectSession({ cookie: 'session=abc' }), null);
});

test('a Bearer token is the session when there is no cookie', () => {
  assert.equal(detectSession({ authorization: 'Bearer tok123' }), hashed('tok123'));
  assert.equal(detectSession({ authorization: 'bearer  tok123 ' }), hashed('tok123'));
});

test('Basic auth is never a session', () => {
  assert.equal(detectSession({ authorization: 'Basic dXNlcjpwYXNz' }), null);
  assert.equal(detectSession({}), null);
});

test('a cookie wins over the Bearer token', () => {
  assert.equal(detectSession({ cookie: 'sid=c', authorization: 'Bearer t' }), hashed('c'));
});
