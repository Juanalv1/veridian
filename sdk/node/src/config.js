// Settings of the middleware: defaults, environment variables and startup errors.

export const DEFAULT_BASE_URL = 'https://veridian-engine.onrender.com';
export const API_KEY_ENV = 'VERIDIAN_API_KEY';
export const URL_ENV = 'VERIDIAN_URL';

/** The middleware cannot start: a setting is missing or wrong. */
export class VeridianConfigError extends Error {
  constructor(message) {
    super(message);
    this.name = 'VeridianConfigError';
  }
}

function resolveKey(apiKey, env) {
  const key = String(apiKey !== undefined && apiKey !== null ? apiKey : env[API_KEY_ENV] || '').trim();
  if (!key) {
    throw new VeridianConfigError(
      `veridian: no API key. Create one in the Veridian panel (Claves de API) and set it in the ${API_KEY_ENV} environment variable, or pass apiKey: ...`,
    );
  }
  return key;
}

function resolveUrl(baseUrl, env) {
  const url = String(baseUrl !== undefined && baseUrl !== null ? baseUrl : env[URL_ENV] || DEFAULT_BASE_URL).trim();
  const match = /^(https?):\/\/(.*)$/i.exec(url);
  if (!match || !match[2].replace(/^\/+|\/+$/g, '')) {
    throw new VeridianConfigError(
      `veridian: the engine URL '${url}' is not valid. Set ${URL_ENV} to a full http(s) URL, or leave it unset to use ${DEFAULT_BASE_URL}`,
    );
  }
  return url.replace(/\/+$/, '');
}

function checkFunction(value, name) {
  if (value !== undefined && value !== null && typeof value !== 'function') {
    throw new VeridianConfigError(
      `veridian: \`${name}\` must be a function that takes the request, e.g. ${name}: (req) => ...`,
    );
  }
  return value ?? undefined;
}

/**
 * Validate the options and fill in the defaults. Throws VeridianConfigError.
 * Intervals and timeouts are in milliseconds.
 * @param {object} options see the README table
 * @param {Record<string, string | undefined>} env
 */
export function resolveConfig(options = {}, env = process.env) {
  const logger = options.logger ?? console;
  const apiKey = resolveKey(options.apiKey, env);
  const baseUrl = resolveUrl(options.baseUrl, env);
  const user = checkFunction(options.user, 'user');
  const session = checkFunction(options.session, 'session');
  const userEmail = checkFunction(options.userEmail, 'userEmail');
  const trustedProxies = options.trustedProxies ?? 0;
  if (!Number.isInteger(trustedProxies) || trustedProxies < 0) {
    throw new VeridianConfigError('veridian: `trustedProxies` must be a whole number of proxies, 0 or more');
  }
  if (!user) {
    logger.warn(
      'veridian: no `user` given, so no request will be reported. Pass user: (req) => <your user\'s id or null>',
    );
  }
  return {
    apiKey,
    baseUrl,
    user,
    userEmail,
    session,
    logger,
    trustedProxies,
    excludePaths: (options.excludePaths ?? ['/static']).map((p) => p.replace(/\/+$/, '')),
    flushInterval: options.flushInterval ?? 2000,
    pollInterval: options.pollInterval ?? 2000,
    routesPollInterval: options.routesPollInterval ?? 30000,
    checkTimeout: options.checkTimeout ?? 12000,
    requestTimeout: options.requestTimeout ?? 2000,
    maxQueue: options.maxQueue ?? 1000,
    reportRoutes: options.reportRoutes ?? true,
    background: options.background ?? true,
    routes: options.routes,
    fetch: options.fetch ?? globalThis.fetch,
  };
}
