import express from 'express';
import { veridian } from 'veridian-sdk';

const app = express();

app.use(
  veridian({
    // Id de tu usuario, o null si nadie inició sesión
    user: (req) => req.user.id,
    // Proxies tuyos delante de la app (0 si no hay)
    trustedProxies: 0,
  }),
);

// ...aquí van tus rutas...

app.listen(3000);
