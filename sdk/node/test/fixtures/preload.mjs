// Lets the README quickstart run as-is in a test: when the snippet calls app.listen(3000),
// add the app's own login and a route after Veridian's middleware and listen on a free port.
import express from 'express';

const listen = express.application.listen;
express.application.listen = function patchedListen() {
  const app = this;
  app.use((req, res, next) => { req.user = req.headers['x-login'] ? { id: req.headers['x-login'] } : undefined; next(); });
  app.get('/facturas', (req, res) => res.json({ ok: true }));
  const server = listen.call(app, 0, '127.0.0.1', () => console.log(`LISTENING ${server.address().port}`));
  return server;
};
