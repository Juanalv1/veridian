# Veridian

**Detecta cuándo otra persona usa tus credenciales y las bloquea.**

Una regla ve lo raro; la IA entiende si es un robo.

**Agentes: empiecen por [INIT.md](INIT.md).**

Versión visual de esta idea: [docs/idea.html](docs/idea.html) · [artifact](https://claude.ai/artifact/MZkn4qVsPeF57aByu2f94u)

## Problema

Los atacantes pueden entrar a sistemas con credenciales robadas. Si la credencial es válida y no hay otro factor de autenticación, nada los frena: pueden hacer todo lo que ese usuario tenga permitido, como descargar tu base de clientes, cambiar la cuenta bancaria de tus pagos o crear otros usuarios.

## Solución

Veridian aprende cómo se usa cada credencial y detecta cuándo quien la usa no eres tú.

## Cómo funciona

```
Tu app (middleware)  ── registros ──►  Veridian (nube)
   anota y bloquea   ◄── bloqueos ───   aprende y decide
```

Un middleware en tu app le reporta al servidor de Veridian cada acceso: el identificador del usuario, desde dónde, a qué hora y a qué parte del sistema. Los primeros días el servidor aprende cómo se comporta normalmente cada usuario. Después, una IA compara cada acceso con ese comportamiento, y si detecta algo extraño, el middleware bloquea la siguiente petición.

Así se instala en una app con FastAPI:

```python
from fastapi import FastAPI
from veridian_sdk import VeridianMiddleware

app = FastAPI()

app.add_middleware(
    VeridianMiddleware,
    # Id de tu usuario, o None si nadie inició sesión
    user=lambda request: request.state.user.id,
    # Proxies tuyos delante de la app (0 si no hay)
    trusted_proxies=0,
)
```

Y en una app con Express o Next.js (paquete `veridian-sdk` de npm, `sdk/node/`; mismos contratos y comportamiento que el de Python):

```js
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
```

La sesión se detecta sola y Veridian solo recibe un hash, nunca el valor. Si Veridian no responde, tu app sigue funcionando: deja pasar todas las peticiones. `trusted_proxies` (`trustedProxies` en Node) es cuántos proxies tuyos hay delante de la app (un balanceador propio: 1; en Render medimos 2 para el motor y 3 para la app demo, así que mídelo en tu despliegue); con 0 se ignora X-Forwarded-For, que cualquiera puede escribir. Para comprobar la conexión, entra a tu app con un usuario: en unos segundos aparece en Actividad.

## Impacto

Los robos digitales empiezan por contraseñas descuidadas y tardan meses en descubrirse.

- **22 %** de las filtraciones de datos empiezan con una credencial robada: la causa número uno ([Verizon DBIR 2025](https://www.verizon.com/business/resources/articles/credential-stuffing-attacks-2025-dbir-research/)).
- **241 días** tarda en promedio una empresa en detectar y contener una filtración ([IBM 2025](https://newsroom.ibm.com/2025-07-30-ibm-report-13-of-organizations-reported-breaches-of-ai-models-or-applications,-97-of-which-reported-lacking-proper-ai-access-controls)).

Veridian corta el acceso en segundos.

## Ya existe, pero enterprise

Akamai, Traceable o Salt lo hacen para grandes empresas. Nosotros, para startups sin equipo de seguridad, en minutos.

## Documentos

| Documento | Contenido |
|---|---|
| [docs/DECISIONES.md](docs/DECISIONES.md) | Qué decidimos y por qué |
| [docs/HALLAZGOS.md](docs/HALLAZGOS.md) | Competencia, casos reales, cifras, validación y críticas |
| [docs/DEMO.md](docs/DEMO.md) | Estructura del pitch y guion de la demo |
| [docs/GUION.md](docs/GUION.md) | Guion hablado del pitch, slide por slide, y preguntas probables |
| [docs/PENDIENTES.md](docs/PENDIENTES.md) | Qué falta construir, riesgos y preguntas abiertas |
| [docs/REPARTO.md](docs/REPARTO.md) | Reparto del trabajo y contratos entre motor y middleware |
| [landing/install.md](landing/install.md) | Guía de instalación para agentes de código (Claude Code, Cursor, Codex); la landing la sirve como `/install.md` y `/llms.txt`. El panel y la landing tienen el botón «Copiar prompt para tu agente» (la clave nunca va en el prompt) |

## Origen

Veridian nace del proyecto anterior del equipo, [Centinela](https://github.com/Juanalv1/centinela) (respuesta a incidentes con IA sin permisos). De ahí vienen la ingesta de eventos, el perfil por credencial y la evaluación de la IA, que sirven de referencia.
