# Veridian

**Detecta cuándo otra persona usa tus credenciales y las bloquea.**

Una regla ve lo raro; la IA entiende si es un robo.

## Problema

Los atacantes entran a sistemas con credenciales robadas. Si la credencial es válida y no hay otro factor de autenticación, nada los frena: pueden hacer todo lo que ese usuario tenga permitido, como descargar la base de clientes, cambiar la cuenta bancaria de los pagos o crear otros usuarios.

- **22 %** de las filtraciones de datos empiezan con una credencial robada ([Verizon DBIR 2025](https://www.verizon.com/business/resources/articles/credential-stuffing-attacks-2025-dbir-research/)).
- **241 días** tarda en promedio una empresa en detectar y contener una filtración ([IBM 2025](https://newsroom.ibm.com/2025-07-30-ibm-report-13-of-organizations-reported-breaches-of-ai-models-or-applications,-97-of-which-reported-lacking-proper-ai-access-controls)).

## Solución

Veridian aprende cómo se usa cada credencial y detecta cuándo quien la usa no es su dueño.

```
Tu app (middleware)  ── registros ──►  Veridian (nube)
   anota y bloquea   ◄── bloqueos ───   aprende y decide
```

1. Un middleware en tu app reporta cada acceso: usuario, desde dónde, a qué hora y a qué parte del sistema.
2. El servidor aprende un perfil determinista del comportamiento normal de cada usuario.
3. Una IA compara cada acceso con ese perfil y juzga si la desviación es un robo.
4. Si lo es, el middleware bloquea la siguiente petición.

## Instalación

```bash
pip install veridian-sdk
```

## Para quién

Akamai, Traceable o Salt resuelven esto para grandes empresas. Veridian es para startups sin equipo de seguridad, y se instala en minutos.

## Equipo

Juan, Maikol, Christian, José Miguel y Carlos.
