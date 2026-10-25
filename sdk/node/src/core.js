// Framework-neutral core: batching, blocklist, enforcement. The adapters (Express, Next.js)
// describe each request as a plain object and call before() and finish().
import { randomUUID } from 'node:crypto';
import { detectSession, parseCookies, newDeviceId, DEVICE_COOKIE } from './identity.js';
import { REPORTED_METHODS, normalizeTemplate } from './routes.js';

export const MAX_BATCH = 200;
export const BLOCKED_BODY = '{"error":"blocked"}';
// Added to the 403 body only when the engine says it sent the user an unlock email (self-unlock by email).
export const SELF_UNLOCK_MESSAGE = 'Detectamos un acceso inusual. Te enviamos un correo para confirmar que fuiste tú.';
export const MAX_EMAIL = 254;

/** The 403 body: exactly `{"error":"blocked"}`, plus `message` when the unlock email was sent. */
export function blockedBody(selfUnlock = false) {
  return selfUnlock ? JSON.stringify({ error: 'blocked', message: SELF_UNLOCK_MESSAGE }) : BLOCKED_BODY;
}
export const MAX_REPORTED_ROUTES = 1000;
export const MAX_ROUTE_TEXT = 2000;

export class Veridian {
  /** @param {ReturnType<import('./config.js').resolveConfig>} config */
  constructor(config) {
    this.config = config;
    this.queue = []; // events; drops the oldest when full
    this.blocklistVersion = null;
    this.blocked = { users: new Set(), sessions: new Set(), ips: new Set() };
    this.selfUnlockSessions = new Set(); // blocked sessions whose user got the unlock email
    this.timers = [];
    this.flushing = false;
    this.critical = new Set(); // "METHOD route template"; last known
    this.routesSource = typeof config.routes === 'function' ? config.routes : Array.isArray(config.routes) ? () => config.routes : null;
    this.reporting = false;
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

  /** The user's email from `userEmail`, or null: nothing, not an address, or an error (never throws). */
  async emailOf(req) {
    const value = await this.identity(this.config.userEmail, req);
    const email = value?.trim();
    return email && email.includes('@') && email.length <= MAX_EMAIL ? email : null;
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
   * `selfUnlock` is true on a block when the engine says the user was sent the unlock email.
   * @returns {Promise<{excluded?: true, blocked: boolean, selfUnlock?: boolean, deviceId?: string | null, newDevice?: boolean, eventId?: string}>}
   */
  async before(info) {
    if (this.isExcluded(info.path)) return { excluded: true, blocked: false };
    const cookieDevice = parseCookies(info.headers.cookie)[DEVICE_COOKIE] ?? null;
    const user = await this.identity(this.config.user, info.req);
    const session = await this.sessionOf(info);
    if (this.isBlocked({ ip: info.ip, session, user })) {
      // The device cookie is only read here, never issued to a blocked client.
      const selfUnlock = Boolean(session && this.selfUnlockSessions.has(session));
      await this.record(info, cookieDevice, 403, Buffer.byteLength(blockedBody(selfUnlock)), { route: info.routeBeforeRun() });
      return { blocked: true, selfUnlock, deviceId: cookieDevice, newDevice: false };
    }
    const newDevice = cookieDevice === null;
    const deviceId = cookieDevice ?? newDeviceId();
    const eventId = 'e_' + randomUUID().replaceAll('-', '');
    if (this.critical.size) {
      const route = info.routeBeforeRun();
      const verdict = this.critical.has(`${info.method.toUpperCase()} ${route}`) ? await this.checkBlocks(info, deviceId, route, eventId) : null;
      if (verdict) {
        // Same id as the pre-check. The engine keeps the first write of an id, so the 403
        // only reaches it when the pre-check never got through; no cookie either.
        const selfUnlock = verdict.self_unlock === true;
        await this.record(info, deviceId, 403, Buffer.byteLength(blockedBody(selfUnlock)), { route, eventId });
        return { blocked: true, selfUnlock, deviceId, newDevice: false };
      }
    }
    return { blocked: false, deviceId, newDevice, eventId };
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
    const email = this.config.userEmail ? await this.emailOf(info.req) : null;
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
      ...(email ? { user_email: email } : {}),
    };
  }

  async record(info, deviceId, status, bytes, extra) {
    try {
      const event = await this.buildEvent(info, deviceId, status, bytes, extra);
      // The engine requires a session (contract 1): an event without one would only be rejected.
      if (event && event.session_id) this.enqueue(event);
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
      this.selfUnlockSessions = Array.isArray(body.self_unlock_sessions) ? new Set(body.self_unlock_sessions.map(String)) : new Set();
      this.blocklistVersion = version;
    } catch (err) {
      this.config.logger.warn(`veridian: blocklist refresh failed: ${err?.message ?? err}`);
    }
  }

  // --- critical routes (contract 8) and the pre-check (contract 9) ---------------------

  /** Read the route classification once. Any failure keeps the last known set. */
  async refreshRoutes() {
    try {
      const { status, body } = await this.http('GET', '/v1/routes');
      if (status !== 200 || !body || !Array.isArray(body.routes)) return;
      const critical = new Set();
      for (const row of body.routes) {
        if (typeof row?.method !== 'string' || typeof row.route !== 'string') throw new TypeError('bad route row');
        if (row.class === 'critical') critical.add(`${row.method.toUpperCase()} ${row.route}`);
      }
      this.critical = critical;
    } catch (err) {
      this.config.logger.warn(`veridian: routes refresh failed: ${err?.message ?? err}`);
    }
  }

  /**
   * Ask the engine whether this critical request must be refused. The engine's answer on an
   * explicit `block`, else null; every failure answers null (fail-open).
   *
   * The engine stores the first event it sees for an id, so the pre-check carries the id the
   * batched event will have, status 200 (the engine accepts 100-599 only; the app has not run)
   * and bytes 0. The engine needs a user and a session to judge, so without them, or without
   * an IP, the check is skipped.
   */
  async checkBlocks(info, deviceId, route, eventId) {
    try {
      const event = await this.buildEvent(info, deviceId, 200, 0, { route, eventId });
      if (!event || !event.session_id || !event.ip) return null;
      const { status, body } = await this.http('POST', '/v1/check', { json: { event }, timeout: this.config.checkTimeout });
      return status === 200 && body !== null && typeof body === 'object' && body.verdict === 'block' ? body : null;
    } catch (err) {
      this.config.logger.warn(`veridian: check failed, letting the request through: ${err?.message ?? err}`);
      return null;
    }
  }

  // --- route report (contract 16) ------------------------------------------------------

  /** Where the app's routes come from (the Express adapter plugs its router in here). */
  setRoutesSource(source) {
    if (this.routesSource) return; // an explicit `routes` option wins
    this.routesSource = source;
    this.startReport();
  }

  /** Method, route and text of every route; only what the contract accepts. */
  async reportedRoutes() {
    const raw = this.routesSource ? await this.routesSource() : null;
    if (!Array.isArray(raw)) return [];
    const seen = new Set();
    const routes = [];
    for (const r of raw) {
      const method = String(r?.method ?? '').toUpperCase();
      const route = r?.route === undefined ? '' : normalizeTemplate(r.route);
      if (!REPORTED_METHODS.includes(method) || !route.startsWith('/') || this.isExcluded(route)) continue;
      if (seen.has(method + ' ' + route)) continue;
      seen.add(method + ' ' + route);
      routes.push({
        method, route,
        summary: String(r.summary ?? '').slice(0, MAX_ROUTE_TEXT),
        description: String(r.description ?? '').slice(0, MAX_ROUTE_TEXT),
      });
    }
    return routes.slice(0, MAX_REPORTED_ROUTES);
  }

  /** Send the app's routes once. True when done (or nothing to send); never throws. */
  async reportRoutes() {
    try {
      const routes = await this.reportedRoutes();
      if (routes.length === 0) return true;
      const { status } = await this.http('PUT', '/v1/routes/catalog', { json: { routes } });
      return status === 200;
    } catch (err) {
      this.config.logger.warn(`veridian: route report failed: ${err?.message ?? err}`);
      return false;
    }
  }

  startReport() {
    if (!this.config.background || !this.config.reportRoutes || !this.routesSource || this.reporting || !this.timers.length) return;
    this.reporting = true;
    const timer = setInterval(async () => {
      if (await this.reportRoutes()) clearInterval(timer);
    }, this.config.routesPollInterval);
    timer.unref();
    this.timers.push(timer);
    this.reportRoutes().then((done) => { if (done) clearInterval(timer); });
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
    this.loop(() => this.refreshRoutes(), this.config.routesPollInterval);
    this.startReport();
  }

  close() {
    for (const t of this.timers) clearInterval(t);
    this.timers = [];
    this.reporting = false;
  }
}
