# Hallazgos

Lo que investigamos y medimos. Cada cifra tiene su fuente; lo no confirmado está marcado.

## Cifras de impacto

| Cifra | Fuente | Estado |
|---|---|---|
| 22 % de las brechas empiezan con credenciales robadas (primera vía de entrada) | [Verizon DBIR 2025](https://www.verizon.com/business/resources/articles/credential-stuffing-attacks-2025-dbir-research/) | Confirmado |
| 241 días en promedio para detectar y contener una brecha (181 + 60) | [IBM 2025, comunicado de prensa](https://newsroom.ibm.com/2025-07-30-ibm-report-13-of-organizations-reported-breaches-of-ai-models-or-applications,-97-of-which-reported-lacking-proper-ai-access-controls) | Confirmado |
| USD 4,99 M costo promedio de una brecha, récord, +12 % | [IBM 2026](https://www.ibm.com/reports/data-breach) | Confirmado |
| USD 4,67 M y ~292 días en brechas por credenciales robadas | IBM 2025 citado por [SpyCloud](https://spycloud.com/blog/6-takeaways-from-ibm-data-breach-report-2025/) | No confirmado en la fuente original; no usar |
| ~USD 160 por registro | IBM 2025 citado por blogs | No confirmado; no usar |

## Casos reales

| Caso | Qué pasó | Pérdida | Fuentes |
|---|---|---|---|
| **Snowflake 2024** | Credenciales de infostealers, sin MFA. Datos de ~165 empresas (Ticketmaster, AT&T), más de 100 M de personas. | — | [NBC](https://www.nbcbayarea.com/news/business/money-report/atts-massive-data-breach-deepens-crisis-for-snowflake-seven-weeks-after-hack-was-disclosed/3591351/) |
| **Change Healthcare 2024** | Credencial robada de un portal Citrix sin MFA. Movimiento lateral, robo de datos (~1/3 de la población de EE. UU.) y ransomware unos 9 días después. | Hasta USD 2.450 M; USD 22 M de rescate | [TechCrunch](https://techcrunch.com/2025/01/27/how-the-ransomware-attack-at-change-healthcare-went-down-a-timeline/), [The Stack](https://thestack.technology/single-ransomware-attack-has-2-45-billion-impact-with-direct-response-costs-hitting-776-million), [testimonio ante el Congreso](https://docs.house.gov/meetings/IF/IF02/20240501/117242/HHRG-118-IF02-Wstate-WittyS-20240501-U5.pdf) |
| **Colonial Pipeline 2021** | Contraseña filtrada de una cuenta de VPN inactiva, sin MFA. Cierre del oleoducto. | USD 4,4 M de rescate | [Bank Info Security](https://www.bankinfosecurity.com/hackers-used-compromised-password-to-get-into-colonial-pipeline-a-16819) |

Notas:
- Los "9 días" de Change Healthcare vienen de una sola fuente secundaria.
- Colonial ("cuenta dormida que se activa") lo detecta una regla simple, sin IA: no sirve para mostrar el valor de la IA.
- **Por qué las herramientas enterprise no vieron Snowflake:** el atacante se conectó directo a Snowflake, sin pasar por la red de AT&T o Ticketmaster. Solo Snowflake tenía los registros, y no vigilaba cómo se usaban las credenciales de sus clientes. Nuestro cliente está en la posición de Snowflake: da acceso a sus usuarios y necesita saber cuándo una credencial ya no la usa su dueño.

## Competencia

| Producto | Qué hace | Por qué no le sirve a una startup |
|---|---|---|
| Akamai (Neosec), Traceable, Salt | Perfil de comportamiento por usuario de API | Enterprise, con proyecto de integración |
| Cloudflare API Shield | Recomienda límites de volumen por sesión | Por sesión solo en plan Enterprise; mide volumen, no comportamiento ([docs](https://developers.cloudflare.com/api-shield/security/volumetric-abuse-detection)) |
| AWS GuardDuty | Detecta claves de AWS comprometidas | Solo claves de AWS; avisa, no revoca ([docs](https://docs.aws.eu/guardduty/latest/ug/guardduty_finding-types-iam.html)) |
| Okta Identity Threat Protection | Cierra sesiones riesgosas | Solo usuarios de Okta ([producto](https://www.okta.com/products/identity-threat-protection/)) |
| Dropzone AI, Prophet Security, Radiant | IA que investiga alertas | Suponen SIEM, EDR y equipo de seguridad ([Prophet, USD 30 M](https://www.businesswire.com/news/home/20250729681026/en/Prophet-Security-Raises-$30M-Series-A-Announces-Industrys-Most-Comprehensive-Agentic-AI-SOC-Platform-to-Transform-Security-Operations)) |

## Validación de la idea (v1)

Evaluación offline en el repo de Centinela (`evals/key_judgment/`, resultados en `docs/evals/key-judgment.md`).

- **Casos:** 27 sintéticos, en 9 familias. 5 familias de uso legítimo pero raro (viaje, integración nueva, cierre de mes, cambio de infraestructura, guardia de fin de semana) y 4 de robo (descarga masiva, robo lento, imitador, scraping de madrugada).
- **Método:** la IA (Claude Sonnet 5.5, esfuerzo bajo) corre 3 veces por caso y se compara con una regla: 2 o más desviaciones → bloquear.

| | Regla | IA |
|---|---|---|
| Robos bloqueados | 8/12 | 21/36 |
| Robos que pasaron sin aviso | 4/12 | 0/36 |
| Usos legítimos bloqueados por error | 10/15 (67 %) | 8/45 (18 %, todos en casos difíciles) |
| Enviados a revisión humana | 0 | 44/81 |
| Misma decisión en las 3 corridas | — | 25/27 casos |

**Lectura:** la IA es mucho más precisa que la regla simple, pero pregunta demasiado. En los viajes nunca bloqueó: siempre preguntó.

**Debilidades del método:**
- Es circular: una IA escribió los casos, sus respuestas correctas, y otra IA de la misma familia juzga.
- Son casos sintéticos y pocos.
- La regla simple es un competidor débil.

**Revisión del equipo:**
- #6 (conector de BI que descarga clientes) y #15 (incidente de pagos de madrugada) aceptan las dos respuestas.
- #24 y #27 son robos desde la red del dueño sin señal distinguible: quedan como dudosos.
- #18 (malware en la laptop del dueño) es distinguible si se agrega el dispositivo, porque el malware usa un script, no el navegador.

**v2 en curso** (repo de Centinela, rama `feat/key-judgment-device`):
- se agregan dispositivo y programa (user agent y huella TLS) y señales de tiempo;
- modo sin "preguntar a un humano";
- dos competidores serios: un puntaje con pesos fijados a priori y una regresión logística con validación dejando uno afuera.

## Validación del motor real (2026-10-25)

Medido con `engine/scripts/behavior_eval.py`: el motor de `main` corriendo en local con la misma IA que producción (Opus en segundo plano, Haiku en la consulta síncrona), un proyecto de prueba, 3 corridas por caso, cada una con un usuario nuevo y 14 días de historial (red, dispositivo y horario fijos). "Bloqueado" quiere decir que la sesión o la IP terminaron en la blocklist, o que `POST /v1/check` respondió `block`.

| Caso | Esperado | Bloqueado | Decisión | Tiempo |
|---|---|---|---|---|
| L1 Viaje: otra red, su dispositivo, mira facturas | no | 0/3 | `alert` 3/3 | IA: 6–9 s |
| L1b Viaje: cambia la cuenta bancaria por primera vez | no | **3/3** | regla crítica | 0,0 s |
| L2 Dispositivo nuevo en casa, mira facturas | no | 0/3 | `alert` 3/3 | IA: 6–9 s |
| L2b Dispositivo nuevo en casa, cambia la cuenta | no | **3/3** | IA `block` 3/3 | síncrona: 1,5–1,8 s |
| L3 De noche, red y dispositivo de siempre | no | 0/3 | sin desvío | — |
| L4 VPN (IP de centro de datos), su dispositivo | no | 0/3 | `alert` 3/3 | IA: 3–9 s |
| L5 IP que rota entre 3 redes en minutos | no | 0/3 | `alert` 3/3 | IA: 6 s |
| A1 Otra red y dispositivo, a la cuenta bancaria | sí | 3/3 | regla crítica | 0,0 s |
| A2 Descarga masiva (46 pedidos en 1 min) | sí | 3/3 | regla de ritmo | 0,0–0,2 s |
| A3 Robo lento (8 pedidos en 8 min, termina exportando) | sí | 0/3 → **3/3** | IA `step_up` 3/3 | IA: 6–9 s |
| A4 Script en la red y laptop del dueño (60/min) | sí | 3/3 | regla de ritmo (solo sesión) | 0,0–0,2 s |
| A5 Sesión viva del dueño usada desde otra red | sí | 3/3 | regla crítica | 0,0 s |

**Lectura:**
- Los usos raros pero legítimos que solo leen (viaje, dispositivo nuevo, VPN, IP que rota) nunca se bloquearon: la IA respondió `alert` las 15 veces, con una explicación que nombra lo que coincide con el dueño.
- Las decisiones fueron iguales en las 3 corridas de cada caso.
- **Corregido:** el robo lento recibía `step_up` 3 de 3 veces y nada lo aplicaba (el middleware no pide segundo factor). Ahora un `step_up` en segundo plano se aplica como bloqueo, como ya hacía la consulta síncrona (test `test_a_background_step_up_is_enforced_as_a_block`).
- **Corregido antes, en producción:** el dueño desde su red y su dispositivo cambiando la cuenta por primera vez quedaba bloqueado 3 de 3; ya no es un desvío (test `test_owner_first_use_of_a_critical_route_from_known_network_and_device_is_allowed_without_ai`).
- **Sin resolver, por decisión de producto:** L1b y L2b se bloquean (ver `PENDIENTES.md`, Riesgos).
- **Límites del método:** casos sintéticos, historial perfecto de 14 días y una sola app. El robo lento se probó en 8 minutos, no en días.

## Señales de comportamiento

| Señal | Qué delata |
|---|---|
| Red (IP pública) | Acceso desde un lugar nuevo |
| Dispositivo y programa (user agent, huella TLS, cookie de dispositivo) | Un script o una máquina distinta, aunque use la red del dueño |
| Horario | Uso fuera del patrón |
| Endpoints y datos que toca | Acciones o datos que el dueño nunca usa |
| Volumen | Descargas masivas |
| Recorrido de navegación | El atacante va directo a lo valioso (login → cuenta bancaria) |
| Ritmo | Un script es rápido y regular (4,0 s, 4,0 s…); una persona es irregular |

En cuentas de personas, el ritmo de máquina es sospechoso. En claves de integraciones lo normal es ser un script; ahí lo raro es que cambie su ritmo habitual.

## Críticas recibidas y respuestas

| Crítica | Respuesta |
|---|---|
| "¿Qué tipo de ataques protegen?" (organizador) | Uso de credenciales robadas. Una sola respuesta. |
| "Eso se puede hacer sin IA" / "la IA está forzada" (equipo) | En parte es cierto: las señales son estadística. La IA aporta en entender el sistema (OpenAPI), explicar y, si se demuestra en la v2, en el juicio. |
| "Si ya existe para enterprise, ¿por qué no lo usaron?" | Snowflake fue un punto ciego del proveedor (ver arriba). |
| "¿Por qué no MFA y listo?" | El MFA protege el login de personas, pero las claves de API no lo tienen, y el MFA se puede saltar con robo de sesión o fatiga de notificaciones (Uber 2022). |
| "Si le roban el SSH a un dev, no se puede auditar" | Sí se puede: el servidor registra la huella de la llave, la IP y la hora. Es otra puerta (otra fuente de logs); el cerebro es el mismo. Fuera de alcance para el hackathon. |
