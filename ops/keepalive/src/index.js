// Pings the Render services every 10 minutes so the free-plan ones (demo and
// panel) never fall asleep before the demo. The engine and the landing do not
// sleep; they are pinged only to show up in the logs.
const URLS = [
  "https://veridian-demo.onrender.com/login",
  "https://veridian-panel.onrender.com/",
  "https://veridian-engine.onrender.com/health",
  "https://veridian-platanus.onrender.com/",
];

async function ping(url) {
  const started = Date.now();
  try {
    const r = await fetch(url, { signal: AbortSignal.timeout(90_000) });
    return `${r.status} ${Date.now() - started}ms ${url}`;
  } catch (err) {
    return `ERR ${err.name} ${Date.now() - started}ms ${url}`;
  }
}

export default {
  async scheduled(_event, _env, ctx) {
    ctx.waitUntil(
      Promise.all(URLS.map(ping)).then((lines) => lines.forEach((l) => console.log(l))),
    );
  },
};
