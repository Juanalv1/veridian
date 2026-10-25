// Route templates: normalizing, matching a path without running the app, and reading
// the routes out of an Express app.

export const REPORTED_METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE'];

/** `/invoices/:id`, `/blog/[slug]` and `/docs/[...path]` become `/invoices/{id}` style templates. */
export function normalizeTemplate(path) {
  return String(path)
    .replace(/\[\.\.\.([\w-]+)\]/g, '{$1}')
    .replace(/\[([\w-]+)\]/g, '{$1}')
    .replace(/:([A-Za-z0-9_]+)\??/g, '{$1}');
}

/**
 * Build a function (method, path) => template | null from a list of {method, route}.
 * First declared route wins, as in the frameworks.
 */
export function routeMatcher(routes) {
  const compiled = routes.map(({ method, route }) => ({
    method: String(method).toUpperCase(),
    route,
    regex: new RegExp('^' + route.split(/(\{[^}]+\})/).map((part) => (
      /^\{[^}]+\}$/.test(part) ? '[^/]+' : part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    )).join('') + '/?$', 'i'),
  }));
  return (method, path) => {
    const upper = String(method).toUpperCase();
    return compiled.find((c) => c.method === upper && c.regex.test(path))?.route ?? null;
  };
}

// --- Express ------------------------------------------------------------------------

/** Mount prefix of an Express 4 router layer, or null when it cannot be read. */
function prefixOfExpress4(layer) {
  const regexp = layer.regexp;
  if (!regexp) return null;
  if (regexp.fast_slash) return '';
  let source = regexp.source;
  if (!source.startsWith('^') || !source.endsWith('\\/?(?=\\/|$)')) return null;
  source = source.slice(1, -'\\/?(?=\\/|$)'.length);
  const names = (layer.keys ?? []).map((k) => k.name);
  source = source.replace(/\(\?:\\\/\(\[\^\/\]\+\?\)\)/g, () => `/{${names.shift()}}`);
  source = source.replaceAll('\\/', '/');
  return /^[\w\-./{}~%]*$/.test(source) ? source : null;
}

/** Mount prefix of an Express 5 router layer: only a mount at the root can be read. */
function prefixOfExpress5(layer) {
  try {
    const m = layer.matchers?.[0]?.('/');
    return m && (m.path === '' || m.path === '/') ? '' : null;
  } catch { return null; }
}

function stackOf(app) {
  if (!app) return null;
  if (Array.isArray(app.stack)) return app.stack; // a Router
  if (app._router && Array.isArray(app._router.stack)) return app._router.stack; // Express 4
  try {
    if (app.router && Array.isArray(app.router.stack)) return app.router.stack; // Express 5
  } catch { /* Express 4's `app.router` getter throws */ }
  return null;
}

function walk(stack, prefix, out, depth) {
  for (const layer of stack) {
    if (layer.route) {
      const paths = [].concat(layer.route.path);
      const methods = layer.route.methods ?? {};
      const names = methods._all ? REPORTED_METHODS : Object.keys(methods).map((m) => m.toUpperCase());
      for (const path of paths) {
        if (typeof path !== 'string') continue; // RegExp routes have no template
        for (const method of names) {
          if (REPORTED_METHODS.includes(method)) out.push({ method, route: normalizeTemplate(prefix + (path === '/' && prefix ? '' : path)) });
        }
      }
    } else if (layer.handle && Array.isArray(layer.handle.stack) && depth < 8) {
      const own = 'matchers' in layer ? prefixOfExpress5(layer) : prefixOfExpress4(layer);
      if (own !== null) walk(layer.handle.stack, prefix + own, out, depth + 1);
    }
  }
}

/**
 * The routes of an Express app: method plus route template. Reads the router's own stack,
 * so it sees what is registered at the moment it is called. Routers mounted on a path are
 * read on Express 4 only; on Express 5 they are skipped (pass the `routes` option for them).
 * @returns {{method: string, route: string}[]}
 */
export function expressRoutes(app) {
  const stack = stackOf(app);
  if (!stack) return [];
  const out = [];
  walk(stack, '', out, 0);
  const seen = new Set();
  return out.filter((r) => !seen.has(r.method + ' ' + r.route) && seen.add(r.method + ' ' + r.route));
}
