# Veridian

**Detecta cuándo otra persona usa tus credenciales y las bloquea.**

Una regla ve lo raro; la IA entiende si es un robo.

## El problema

Con una credencial robada, nada frena al atacante. Si la clave es válida y no hay otro factor, puede hacer todo lo que ese usuario tenga permitido: descargar tus clientes, cambiar la cuenta bancaria de tus pagos o crear otros usuarios.

- **22 %** de los robos empiezan con credenciales robadas, la causa número uno ([Verizon DBIR 2025](https://www.verizon.com/business/resources/articles/credential-stuffing-attacks-2025-dbir-research/)).
- **241 días** tarda en promedio detectarlos y contenerlos ([IBM 2025](https://newsroom.ibm.com/2025-07-30-ibm-report-13-of-organizations-reported-breaches-of-ai-models-or-applications,-97-of-which-reported-lacking-proper-ai-access-controls)).
- **USD 4,99 M** cuesta cada uno en promedio ([IBM 2026](https://www.ibm.com/reports/data-breach)).

Las soluciones que existen son para grandes empresas. Veridian es para startups sin equipo de seguridad.

## Cómo funciona

1. **Tu app anota.** Un middleware, después de tu login, reporta cada acceso en segundo plano: quién, desde dónde, a qué hora y a qué parte del sistema. Nunca cuerpos ni contraseñas, y si Veridian se cae, tu app sigue funcionando.
2. **Veridian aprende.** Código determinista arma un perfil por usuario: redes, dispositivos, horarios, rutas y ritmo. Nunca aprende de lo marcado como sospechoso.
3. **La IA decide.** Solo ante un desvío, un modelo juzga si es un uso raro o un robo, sabiendo qué rutas son críticas, y explica por qué.
4. **Tu app bloquea.** El middleware descarga la lista de bloqueos y corta la sesión y la IP del atacante, no al dueño.

```python
from veridian import VeridianMiddleware

app.add_middleware(
    VeridianMiddleware,
    token=os.environ["VERIDIAN_TOKEN"],
    user_id=lambda request: request.state.user.id,
)
```

## Qué funciona hoy

- **Motor** en la nube: recibe accesos, aprende perfiles, juzga desvíos con IA y publica la lista de bloqueos.
- **Middleware** para FastAPI: reporta en segundo plano, toma la IP real detrás de proxies sin dejarse engañar por encabezados falsos y aplica los bloqueos.
- **App de ejemplo** (una startup de facturas) protegida por el middleware.
- **Panel** con la actividad en vivo, cada decisión con su explicación y la comparación entre lo normal para el usuario y el acceso sospechoso.

En la demo, alguien entra con la contraseña de otra persona desde otra red y va directo a cambiar la cuenta bancaria: Veridian lo detecta y su siguiente petición queda bloqueada en segundos.

## Enlaces

- Landing: <https://veridian-landing.onrender.com>
- App de ejemplo: <https://veridian-demo.onrender.com>
- Panel: <https://veridian-panel.onrender.com>
