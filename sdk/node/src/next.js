// Veridian for Next.js route handlers (App Router), Node.js runtime on a long-running server.
//
//   // lib/veridian.js
//   export const protect = createVeridian({ user: async (request) => ..., trustedProxies: 1 });
//   // app/api/payouts/route.js
//   export const POST = protect(async (request) => Response.json({ ok: true }), { route: '/api/payouts' });
import { Veridian, blockedBody } from './core.js';
import { resolveConfig } from './config.js';
import { resolveClientIp, deviceCookieHeader } from './identity.js';
import { normalizeTemplate } from './routes.js';

export { VeridianConfigError } from './config.js';

function withCookie(response, cookie) {
  try {
    response.headers.append('set-cookie', cookie);
    return response;
  } catch { // headers of a fetch() or Response.redirect() response are immutable: copy it
    const copy = new Response(response.body, response);
    copy.headers.append('set-cookie', cookie);
    return copy;
  }
}

/**
 * Build the wrapper for route handlers. One instance per process: create it in a shared module.
 * Extra options: `ip: (request) => string` to say where the client address is.
 * @param {object} [options] same as the Express options, plus `ip`
 */
export function createVeridian(options = {}) {
  const core = new Veridian(resolveConfig(options));
  core.start();

  /**
   * Wrap a route handler `(request, context) => Response`.
   * @param {Function} handler
   * @param {{route?: string}} [wrapOptions] `route` is the route template, e.g. '/api/items/[id]'
   */
  function protect(handler, { route } = {}) {
    return async function veridianHandler(request, ...rest) {
      const url = new URL(request.url);
      const template = route ? normalizeTemplate(route) : url.pathname;
      const headers = Object.fromEntries(request.headers);
      const info = {
        method: request.method, path: url.pathname, req: request, headers,
        // Next.js gives a route handler no socket: the address can only come from the proxy's header.
        ip: options.ip
          ? String(options.ip(request) ?? '')
          : resolveClientIp(request.ip, headers['x-forwarded-for'], core.config.trustedProxies),
        routeBeforeRun: () => template, routeAfterRun: () => template,
      };
      let ctx;
      try {
        ctx = await core.before(info);
      } catch (err) {
        core.config.logger.error(`veridian: ${err?.message ?? err}`); // fail-open
        return handler(request, ...rest);
      }
      if (ctx.blocked) {
        return new Response(blockedBody(ctx.selfUnlock), { status: 403, headers: { 'content-type': 'application/json' } });
      }
      if (ctx.excluded) return handler(request, ...rest);
      let response;
      try {
        response = await handler(request, ...rest);
      } catch (err) {
        await core.finish(info, ctx, 500, 0).catch(() => {});
        throw err;
      }
      await core.finish(info, ctx, response.status, Number(response.headers.get('content-length')) || 0).catch(() => {});
      return ctx.newDevice ? withCookie(response, deviceCookieHeader(ctx.deviceId)) : response;
    };
  }
  protect.veridian = core;
  protect.close = async () => { core.close(); await core.flush(); };
  return protect;
}

export default createVeridian;
