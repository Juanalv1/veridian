// Veridian for Express (and anything Connect-compatible): app.use(veridian({ user: (req) => ... }))
import { Veridian, BLOCKED_BODY } from './core.js';
import { resolveConfig, VeridianConfigError } from './config.js';
import { resolveClientIp, deviceCookieHeader, detectSession } from './identity.js';
import { expressRoutes, routeMatcher, normalizeTemplate } from './routes.js';

export { VeridianConfigError, detectSession, resolveClientIp, expressRoutes };

const MATCHER_TTL = 30_000;

function appendHeader(res, name, value) {
  if (typeof res.append === 'function') return res.append(name, value);
  const current = res.getHeader(name);
  res.setHeader(name, current === undefined ? value : [].concat(current, value));
}

function pathOf(req) {
  return (req.originalUrl || req.url || '/').split('?')[0];
}

/**
 * Express middleware. Add it before your routes: `app.use(veridian({ user: (req) => req.user?.id }))`.
 * Throws VeridianConfigError at startup when the key or URL is missing or wrong.
 * The returned function also carries `.veridian` (the core, for tests) and `.close()`.
 * @param {object} [options] see the README table
 */
export function veridian(options = {}) {
  const core = new Veridian(resolveConfig(options));
  core.start();

  let appSeen = false;
  let cache = { app: null, at: 0, match: () => null };
  // Template of a path matched against the app's router, without running any handler.
  const matcherFor = (app) => {
    if (cache.app !== app || Date.now() - cache.at > MATCHER_TTL) {
      const explicit = Array.isArray(core.config.routes) ? core.config.routes.map((r) => ({ method: r.method, route: normalizeTemplate(r.route) })) : [];
      cache = { app, at: Date.now(), match: routeMatcher([...explicit, ...expressRoutes(app)]) };
    }
    return cache.match;
  };

  async function handle(req, res, next) {
    if (!appSeen && req.app) {
      appSeen = true;
      const app = req.app;
      core.setRoutesSource(() => expressRoutes(app));
    }
    const path = pathOf(req);
    const method = req.method;
    const match = () => (req.app ? matcherFor(req.app)(method, path) : null);
    const routeBeforeRun = () => match() ?? path;
    const routeAfterRun = () => {
      const own = req.route?.path;
      if (typeof own === 'string' && !req.baseUrl) return normalizeTemplate(own);
      const matched = match();
      if (matched) return matched;
      if (typeof own === 'string') return normalizeTemplate(req.baseUrl + (own === '/' ? '' : own));
      return path;
    };
    const info = {
      method, path, req, headers: req.headers,
      ip: resolveClientIp(req.socket?.remoteAddress, req.headers['x-forwarded-for'], core.config.trustedProxies),
      routeBeforeRun, routeAfterRun,
    };

    let ctx;
    try {
      ctx = await core.before(info);
    } catch (err) {
      core.config.logger.error(`veridian: ${err?.message ?? err}`); // fail-open
      return next();
    }
    if (ctx.blocked) {
      res.status(403).type('json').send(BLOCKED_BODY);
      return undefined;
    }
    if (ctx.excluded) return next();
    if (ctx.newDevice) appendHeader(res, 'Set-Cookie', deviceCookieHeader(ctx.deviceId));
    res.once('finish', () => {
      const bytes = Number(res.getHeader('content-length')) || 0;
      core.finish(info, ctx, res.statusCode, bytes).catch(() => {});
    });
    return next();
  }

  const middleware = function veridianMiddleware(req, res, next) {
    handle(req, res, next).catch((err) => {
      core.config.logger.error(`veridian: ${err?.message ?? err}`);
      if (!res.headersSent) next();
    });
  };
  middleware.veridian = core;
  /** Stop the background tasks and send what is queued. */
  middleware.close = async () => { core.close(); await core.flush(); };
  return middleware;
}

export default veridian;
