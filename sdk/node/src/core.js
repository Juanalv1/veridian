// Framework-neutral core: batching, blocklist, enforcement. The adapters (Express, Next.js)
// describe each request as a plain object and call before() and finish().
import { randomUUID } from 'node:crypto';
import { detectSession, parseCookies, newDeviceId, DEVICE_COOKIE } from './identity.js';

export const MAX_BATCH = 200;
export const BLOCKED_BODY = '{"error":"blocked"}';

export class Veridian {
  /** @param {ReturnType<import('./config.js').resolveConfig>} config */
  constructor(config) {
    this.config = config;
    this.queue = []; // events; drops the oldest when full
    this.blocklistVersion = null;
    this.blocked = { users: new Set(), sessions: new Set(), ips: new Set() };
    this.timers = [];
    this.flushing = false;
  }

  // --- HTTP to the engine (never throws out of the background tasks) -----------------

  async http(method, path, { json, query, timeout } = {}) {
    const url = new URL(this.config.baseUrl + path);
    for (const [k, v] of Object.entries(query ?? {})) url.searchParams.set(k, String(v));
    const response = await this.config.fetch(url, {
      method,
      headers: {
        authorization: `Bearer ${this.config.apiKey}`,
        ...(json !== undefined ? { 'content-type': 'application/json' } : {}),
      },
      body: json !== undefined ? JSON.stringify(json) : undefined,
      signal: AbortSignal.timeout(timeout ?? this.config.requestTimeout),
    });
    const text = await response.text();
    let body;
    try { body = text ? JSON.parse(text) : undefined; } catch { body = undefined; }
    return { status: response.status, body };
  }

  // --- identity ------------------------------------------------------------------------

  /** Id from the integrator's function: a string, or null. Never throws. */
  async identity(fn, req) {
    if (!fn) return null;
    try {
      const value = await fn(req);
      if (value === null || value === undefined || value === false || value === '') return null;
      return String(value); // ids are often numbers; the engine takes strings
    } catch (err) {
      if (err instanceof TypeError) return null; // like `req.user.id` with nobody logged in
      this.config.logger.warn(`veridian: identity function failed: ${err?.message ?? err}`);
      return null;
    }
  }

  async sessionOf(info) {
    if (this.config.session) return this.identity(this.config.session, info.req);
    return detectSession(info.headers);
  }

  isExcluded(path) {
    return this.config.excludePaths.some((p) => path === p || path.startsWith(p + '/'));
  }

  isBlocked({ ip, session, user }) {
    const b = this.blocked;
    if (ip && b.ips.has(ip)) return true;
    if (session && b.sessions.has(session)) return true;
    return Boolean(user && b.users.has(user));
  }

  // --- per request ---------------------------------------------------------------------

  /**
   * Decide what to do with a request before the app runs.
   * @returns {Promise<{excluded?: true, blocked: boolean, deviceId?: string | null, newDevice?: boolean, eventId?: string}>}
   */
  async before(info) {
    if (this.isExcluded(info.path)) return { excluded: true, blocked: false };
    const cookieDevice = parseCookies(info.headers.cookie)[DEVICE_COOKIE] ?? null;
    const user = await this.identity(this.config.user, info.req);
    const session = await this.sessionOf(info);
    if (this.isBlocked({ ip: info.ip, session, user })) {
      // The device cookie is only read here, never issued to a blocked client.
      await this.record(info, cookieDevice, 403, BLOCKED_BODY.length, { route: info.routeBeforeRun() });
      return { blocked: true, deviceId: cookieDevice, newDevice: false };
    }
    const newDevice = cookieDevice === null;
    return {
      blocked: false,
      deviceId: cookieDevice ?? newDeviceId(),
      newDevice,
      eventId: 'e_' + randomUUID().replaceAll('-', ''),
    };
  }

  /** The app has answered: report the request. */
  async finish(info, ctx, status, bytes) {
    if (ctx.excluded || ctx.blocked) return;
    await this.record(info, ctx.deviceId, status, bytes, { eventId: ctx.eventId });
  }

  // --- events --------------------------------------------------------------------------

  /** One contract 1 event, or null when there is no user to attribute it to. */
  async buildEvent(info, deviceId, status, bytes, { route, eventId } = {}) {
    const user = await this.identity(this.config.user, info.req);
    if (!user) return null;
    return {
      id: eventId ?? 'e_' + randomUUID().replaceAll('-', ''),
      ts: new Date().toISOString().slice(0, 19) + 'Z',
      user_id: user,
      session_id: await this.sessionOf(info),
      ip: info.ip,
      user_agent: String(info.headers['user-agent'] ?? '').slice(0, 200),
      device_id: deviceId,
      method: info.method,
      route: route ?? info.routeAfterRun(),
      status,
      bytes,
    };
  }

  async record(info, deviceId, status, bytes, extra) {
    try {
      const event = await this.buildEvent(info, deviceId, status, bytes, extra);
      if (event) this.enqueue(event);
    } catch (err) {
      this.config.logger.error(`veridian: could not record event: ${err?.message ?? err}`);
    }
  }

  enqueue(event) {
    this.queue.push(event);
    if (this.queue.length > this.config.maxQueue) this.queue.splice(0, this.queue.length - this.config.maxQueue);
  }

  /** Send queued events once. On failure they stay queued (ids make the retry idempotent). */
  async flush() {
    if (this.flushing || this.queue.length === 0) return;
    this.flushing = true;
    try {
      const batch = this.queue.slice(0, MAX_BATCH);
      const { status } = await this.http('POST', '/v1/events', { json: { events: batch } });
      if (status !== 202) return;
      const sent = new Set(batch.map((e) => e.id));
      this.queue = this.queue.filter((e) => !sent.has(e.id));
    } catch (err) {
      this.config.logger.warn(`veridian: event flush failed: ${err?.message ?? err}`);
    } finally {
      this.flushing = false;
    }
  }

  // --- blocklist -----------------------------------------------------------------------

  /** Poll the engine once. Any failure keeps the last known list. */
  async refreshBlocklist() {
    try {
      const query = this.blocklistVersion !== null ? { since: this.blocklistVersion } : undefined;
      const { status, body } = await this.http('GET', '/v1/blocklist', { query });
      if (status !== 200 || !body || typeof body !== 'object') return;
      const version = Number(body.version);
      if (!Number.isInteger(version)) return;
      const list = (name) => {
        if (body[name] !== undefined && !Array.isArray(body[name])) throw new TypeError(`bad ${name}`);
        return new Set((body[name] ?? []).map(String));
      };
      this.blocked = { users: list('users'), sessions: list('sessions'), ips: list('ips') };
      this.blocklistVersion = version;
    } catch (err) {
      this.config.logger.warn(`veridian: blocklist refresh failed: ${err?.message ?? err}`);
    }
  }

  // --- background ----------------------------------------------------------------------

  /** Run `fn` now and every `ms`, never overlapping itself; the timer does not keep the process alive. */
  loop(fn, ms) {
    let running = false;
    const tick = async () => {
      if (running) return;
      running = true;
      try { await fn(); } catch (err) { this.config.logger.error(`veridian: background task error: ${err?.message ?? err}`); } finally { running = false; }
    };
    tick();
    const timer = setInterval(tick, ms);
    timer.unref();
    this.timers.push(timer);
  }

  start() {
    if (!this.config.background || this.timers.length) return;
    this.loop(() => this.flush(), this.config.flushInterval);
    this.loop(() => this.refreshBlocklist(), this.config.pollInterval);
  }

  close() {
    for (const t of this.timers) clearInterval(t);
    this.timers = [];
  }
}
