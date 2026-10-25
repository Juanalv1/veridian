// Who is making a request: client IP, device cookie and session. Pure functions on plain
// values, shared by the Express and Next.js adapters.
import { createHash, randomUUID } from 'node:crypto';

export const DEVICE_COOKIE = 'vrd_did';
export const DEVICE_COOKIE_MAX_AGE = 60 * 60 * 24 * 365;
// Session cookies whose value stays the same for the whole session. A cookie called just
// `session` (cookie-session, Starlette) is left out on purpose: it is re-signed on every response.
export const SESSION_COOKIES = ['sessionid', 'connect.sid', 'sid', 'session_id', 'PHPSESSID', 'JSESSIONID'];

/** What the engine sees as a detected session: a hash, never the secret itself. */
export function sessionHash(value) {
  return 's_' + createHash('sha256').update(value).digest('hex').slice(0, 16);
}

/**
 * Parse a Cookie header into an object. Values are kept as sent (not URL-decoded, so the
 * hash is the same one the Python SDK computes); surrounding double quotes are removed.
 * @param {string | undefined | null} header
 * @returns {Record<string, string>}
 */
export function parseCookies(header) {
  const jar = {};
  if (!header) return jar;
  for (const part of String(header).split(';')) {
    const eq = part.indexOf('=');
    if (eq < 1) continue;
    const name = part.slice(0, eq).trim();
    let value = part.slice(eq + 1).trim();
    if (value.length >= 2 && value.startsWith('"') && value.endsWith('"')) value = value.slice(1, -1);
    if (name && !(name in jar)) jar[name] = value;
  }
  return jar;
}

/**
 * Session of a request when the integrator gave none: a known session cookie first, then a
 * Bearer token; only a hash leaves the app.
 * @param {{cookie?: string | null, authorization?: string | null}} headers
 * @returns {string | null}
 */
export function detectSession({ cookie, authorization } = {}) {
  const jar = parseCookies(cookie);
  for (const name of SESSION_COOKIES) {
    if (jar[name]) return sessionHash(jar[name]);
  }
  const match = /^(\S+)\s+(.*)$/.exec((authorization || '').trim());
  if (match && match[1].toLowerCase() === 'bearer' && match[2].trim()) return sessionHash(match[2].trim());
  return null;
}

/**
 * Client IP that the client cannot choose (resolve_client_ip in the Python SDK).
 *
 * With 0 trusted proxies the socket peer is used and X-Forwarded-For is ignored (anyone can
 * write it). With N, the Nth value from the right is used: the one appended by our outermost
 * proxy. Fewer values than N falls back to the first value.
 * @param {string | undefined} peer socket address, if the runtime exposes it
 * @param {string | undefined} forwardedFor value of X-Forwarded-For (repeated headers joined with ",")
 * @param {number} trustedProxies
 */
export function resolveClientIp(peer, forwardedFor, trustedProxies = 0) {
  const socketIp = (peer || '').replace(/^::ffff:(?=\d+\.\d+\.\d+\.\d+$)/i, '');
  if (trustedProxies <= 0) return socketIp;
  const values = String(forwardedFor || '').split(',').map((v) => v.trim()).filter(Boolean);
  if (values.length === 0) return socketIp;
  if (values.length < trustedProxies) return values[0];
  return values[values.length - trustedProxies];
}

export function newDeviceId() {
  return 'd_' + randomUUID().replaceAll('-', '').slice(0, 12);
}

export function deviceCookieHeader(deviceId) {
  return `${DEVICE_COOKIE}=${deviceId}; Path=/; HttpOnly; SameSite=Lax; Max-Age=${DEVICE_COOKIE_MAX_AGE}`;
}
