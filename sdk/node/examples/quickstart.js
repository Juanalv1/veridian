// 1. Instala el SDK:            npm install veridian-sdk
// 2. Pon tu clave en el entorno (nunca en el código), la que creaste en el panel:
//                               VERIDIAN_API_KEY=vrd_...
import express from 'express';
import { veridian } from 'veridian-sdk';

const app = express();

// Si tu login (Passport, express-session...) pone al usuario en req.user, regístralo
// antes que Veridian: así ya sabe quién es cuando llega la petición.
app.use(
  veridian({
    // Quién hace la petición: el id de tu usuario, o null si nadie inició sesión.
    // Si nadie inició sesión y req.user no existe, cuenta como null, sin errores.
    user: (req) => req.user.id,
    // Cuántos proxies tuyos hay delante de la app (un balanceador, Render, Heroku: 1).
    // Con 0 usa la IP de la conexión e ignora X-Forwarded-For, que cualquiera puede escribir.
    trustedProxies: 0,
  }),
);

// ...aquí van tus rutas...

// La sesión se detecta sola: cookie sessionid, connect.sid, sid, session_id, PHPSESSID
// o JSESSIONID, o el token Bearer. Veridian solo recibe un hash, nunca el valor.
// Si Veridian no responde, tu app sigue funcionando: deja pasar todas las peticiones.
// Para comprobar la conexión: entra a tu app con un usuario y, en unos segundos,
// ese acceso aparece en Actividad, en el panel de Veridian.
app.listen(3000);
