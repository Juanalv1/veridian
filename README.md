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

Así se instalaría en una app con FastAPI (propuesta):

```python
# pip install veridian
from veridian import VeridianMiddleware

app.add_middleware(
    VeridianMiddleware,
    token=os.environ["VERIDIAN_TOKEN"],             # tu cuenta en Veridian
    user_id=lambda request: request.state.user.id,  # el usuario que tu app ya autenticó
)
```

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

## Origen

Veridian nace del proyecto anterior del equipo, [Centinela](https://github.com/Juanalv1/centinela) (respuesta a incidentes con IA sin permisos). De ahí vienen la ingesta de eventos, el perfil por credencial y la evaluación de la IA, que sirven de referencia.
