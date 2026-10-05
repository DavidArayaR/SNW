# SNW — Sistema de Notificaciones WhatsApp

Módulo web para gestionar y enviar notificaciones de WhatsApp a pacientes. Backend en
**Python (FastAPI)**, una única base de datos **MySQL/MariaDB** con tablas separadas para
desarrollo y producción, plantillas en un **archivo JSON**, e integración directa
con la **WhatsApp Business Cloud API** de Meta (v26.0).

## Stack

| Componente | Tecnología |
|---|---|
| Frontend | HTML5, CSS, JavaScript vanilla (sin build step) |
| Backend / API | Python + FastAPI + Uvicorn |
| Base de datos | MySQL / MariaDB (XAMPP, PyMySQL) — una sola base, `snw_base` |
| Plantillas | Archivo JSON (`data/plantillas.json`) |
| Usuarios | Tabla `usuarios` en `snw_base` (rol + permisos, clave SHA-256, correo de recuperación). Sesiones en `data/sesiones.json` |
| WhatsApp | WhatsApp Business Cloud API (Meta Graph API v26.0) o motor simulado |
| Correo | SMTP (configurable en la tabla `configuracion`) — confirmación de envíos en producción y recuperación de contraseña |
| Configuración | Tabla `configuracion` en MySQL (todo salvo credenciales de BD, que van en `.env`) |

## Estructura

```
snw/
├── INICIAR_SNW.bat          Arranque en un clic (Windows): valida MySQL/deps, inicializa
│                             la BD solo la primera vez, abre el navegador y levanta uvicorn
├── iniciar_snw.sh           Mismo arranque para Linux/macOS (chmod +x la primera vez)
├── backend/
│   ├── main.py               API FastAPI: manejadores (envíos en background, confirmación por
│   │                          correo, plantillas, pacientes, historial, configuración,
│   │                          tarifas/costos, call center, webhook)
│   ├── db.py                 Conexión MySQL (PyMySQL), entorno dev/prod, columnas dinámicas,
│   │                          CONFIG_DEFAULTS, cuentas/invites/resets/auditoría,
│   │                          log_error() (todo error queda en consola)
│   ├── schemas.py            Modelos Pydantic de las peticiones (PlantillaIn, EnvioIn, ConfigIn…)
│   ├── config_service.py     Vista de config para pantallas/motor (leer_config, url_base) y SMTP
│   │                          (enviar_correo)
│   ├── servicio_areas.py     Áreas, tablas de pacientes e importación CSV
│   ├── telefono.py           Normalización/validación de teléfonos chilenos → `+56 9 …`
│   ├── routes/               Routers por dominio, cada uno llama a los handlers de main.py:
│   │                          auth, usuarios, pacientes, plantillas, notificaciones,
│   │                          estadisticas, configuracion, areas (+ _registry)
│   ├── whatsapp_service.py   Cliente Graph API + WhatsAppService (envío, templates,
│   │                          webhook handler) — capa de integración con Meta
│   ├── wa_rate_limit.py      Gobernador de límites de Meta: cuota de la Graph API,
│   │                          throughput (msg/s) y clasificación de errores de límite
│   ├── whatsapp_webhook.py   Router del webhook (verificación GET + recepción POST)
│   └── motor_envio.py        Motor de envío intercambiable: simulado | api_oficial
├── frontend/
│   ├── index.html             Landing con navegación por rol
│   ├── login.html             Inicio de sesión + «¿Olvidaste tu contraseña?»
│   ├── registro.html          Activar cuenta invitada: elige contraseña con el token del correo
│   ├── reset.html             Restablecer contraseña con el token del correo
│   ├── mensajeria.html        Plantillas, envíos manuales y programados
│   ├── historial.html         Historial de envíos (batch + detalle por paciente)
│   ├── pacientes.html         Tablas de pacientes e importación CSV
│   ├── usuarios.html          Gestión de cuentas
│   ├── areas.html             Gestión de áreas
│   ├── estadisticas.html      Resumen, gráficos y costos
│   ├── configuracion.html     Configuración (solo desarrollador)
│   ├── administracion.html    Redirige a Usuarios por compatibilidad
│   ├── css/                   tema.css (paleta claro/oscuro), styles.css (compartido), layout.css (sidebar), componentes.css (tablas/paginador/badges compartidos), pacientes.css (solo Base de datos), estadisticas.css, configuracion.css, usuarios.css
│   ├── js/                    tema.js (modo claro/oscuro), layout.js (sidebar/sesión/permisos, común), app.js, pacientes.js, historial.js, estadisticas.js, configuracion.js, usuarios.js, pass-toggle.js (ojito en campos de contraseña)
│   └── vendor/bootstrap/     Bootstrap 5.3.3 (CSS + bundle JS) servido localmente
├── data/
│   ├── plantillas.json        Plantillas de mensajes + metadata del template en Meta
│   └── sesiones.json          Tokens de sesión activos
│                                (las cuentas viven en la tabla `usuarios`);
│                                un `usuarios.json` antiguo se migra a la tabla y
│                                se archiva como `usuarios.json.migrado`
├── sql/
│   └── snw_base.sql           Crea la base snw_base y sus tablas iniciales. El backend
│                                crea/migra tablas auxiliares al arrancar, incluidas las
│                                de áreas, programados y acceso a pacientes por CSV
├── documentacion/             CONTEXT.md (contexto, arquitectura y decisiones) y
│                                FEATURES.md (funcionalidades y flujos del sistema)
├── backups/                   Volcados manuales (mysqldump) antes de operaciones destructivas
├── .env                       Solo credenciales de la BD (DB_*). No versionado.
├── .env.example                Plantilla del .env (solo DB_*)
├── requirements.txt            Dependencias Python
└── README.md
```

## Puesta en marcha

**Opción recomendada:** `INICIAR_SNW.bat` (Windows, doble clic) o `./iniciar_snw.sh`
(Linux/macOS — la primera vez: `chmod +x iniciar_snw.sh`). Ambos scripts hacen lo mismo:

1. Verifican que Python y MySQL (XAMPP/LAMPP) estén disponibles.
2. Base de datos: **primera vez**, cargan `sql/snw_base.sql` tal cual. Si ya fue
   inicializada, preguntan: **[1] backup e iniciar desde cero** (respalda
   `snw_base` a `backups/` y la borra/recrea; si el respaldo falla no se sigue),
   **[2] backup y seguir como está** (respalda igual, no toca nada),
   **[3] seguir sin backup** o **[4] elegir un backup** de `backups/` e iniciar
   con ese backup (primero respalda la actual, pide confirmación, borra la base actual).
3. Instalan las dependencias de `requirements.txt` si faltan.
4. Abren `http://127.0.0.1:8000` en el navegador y levantan `uvicorn`.

**Manual (cualquier SO):**

1. Instalar dependencias:
   ```
   pip install -r requirements.txt
   ```
2. Asegurar MySQL activo (XAMPP) y cargar el esquema una vez:
   ```
   mysql -u root -h 127.0.0.1 < sql/snw_base.sql
   ```
3. Copiar `.env.example` como `.env` y completar solo las variables `DB_*`.
4. Iniciar:
   ```
   python -m uvicorn main:app --app-dir backend --host 127.0.0.1 --port 8000
   ```
5. Abrir http://localhost:8000
6. Cargar la configuración real (token de Meta, SMTP, números autorizados, etc.) con
   `PUT /api/configuracion` o con `UPDATE configuracion SET valor='…' WHERE clave='…'`.

### Configuración

**`.env` solo tiene las credenciales de la base de datos** (`DB_HOST`, `DB_PUERTO`,
`DB_USUARIO`, `DB_CONTRASENA`, `DB_NOMBRE`) — se necesitan para llegar a la base donde
vive el resto.

**Todo lo demás vive en la tabla `configuracion`** (clave/valor). El backend la crea y la
siembra con los valores por defecto en el primer arranque. Se edita desde
la pestaña **Configuración** de `administracion.html` (**solo rol desarrollador**) — que muestra TODAS las claves
por secciones (Aplicación, URL pública, Correo/SMTP, WhatsApp) — o con `UPDATE configuracion`
(requiere reiniciar por la caché). Los cambios de la página se aplican sin reiniciar (salvo
el `entorno` activo, que las demás pantallas leen al cargar).

| Grupo | Claves |
|---|---|
| App / envío | `entorno` (`desarrollo`/`produccion`), `metodo_envio` (`simulado`/`api_oficial`), `numeros_prueba_dev`, `numeros_prueba_prod`, `intervalo_ms`, `sesion_expira_horas` (horas de inactividad antes de cerrar una sesión sola; 0 = no expiran; por defecto 5), `url_base` |
| Call center | `call_center_url` (servicio que devuelve un número de call center; se consulta en cada respuesta y ese servicio reparte la carga), `call_center_numeros` (respaldo manual, uno o varios separados por coma, solo si la URL no responde), `call_center_boton_mensaje` / `call_center_boton_mensaje_oferta` (texto que autocompleta el botón; el de oferta se usa si la última plantilla enviada mencionaba un descuento o precio especial). El mensaje de call center y su espera (1s fija) no son configurables, ver [Mensaje de call center](#mensaje-de-call-center) |
| Correo | `smtp_host`, `smtp_port`, `smtp_user`, `smtp_pass`, `smtp_tls`, `correo_emisor`, `correo_destino` |
| WhatsApp / Meta | `wa_token`, `wa_phone_id`, `wa_business_account_id`, `wa_verify_token`, `wa_template_nombre`, `wa_template_lang`, `wa_webhook_path`, `wa_graph_version` (por defecto `v26.0`), `wa_moneda` (moneda de facturación de la cuenta, se autodetecta desde Meta al actualizar tarifas — por defecto `USD`), `plantillas_revision_minutos` (cada cuántos minutos se revisa sola en Meta el estado de las plantillas pendientes; 0 = desactivado; por defecto 2), `plantillas_badge_aprobada_minutos` (cuánto se muestra el aviso «Aprobada recientemente»; 0 = nunca; por defecto 10) |
| Límites de envío Meta | `wa_rate_limit_activo` (frenado proactivo on/off), `wa_rate_limit_umbral_pct` (% de cuota a partir del cual se espera, 80), `wa_rate_limit_pausa_max_s` (espera entre mensajes al 100 % de cuota, 30), `wa_rate_limit_espera_defecto_s` (espera tras un 429 sin dato, 60), `wa_rate_limit_reintentos` (reintentos de una llamada tras un 429, 3), `wa_throughput_mps` (ritmo máximo de salida hacia Meta, msg/s; 0 = sin límite; 10), `wa_messaging_limit_24h` (usuarios únicos que se pueden contactar en 24 h antes de bloquear el envío masivo; 0 = ilimitado; 2000) |

## Base de datos

**Una sola base MySQL, `snw_base`**, con 10 tablas iniciales creadas por `sql/snw_base.sql`
(pacientes_dev, pacientes_prod, envios, log_envios, whatsapp_eventos, configuracion,
tarifas_whatsapp, call_center_log, usuarios, password_resets). Al arrancar, el backend
crea o migra las tablas auxiliares necesarias, entre ellas `account_invites`,
`usuarios_auditoria`, las de áreas y envíos programados, y `paciente_csv_accesos`.
La tabla **`usuarios`** guarda las cuentas de la app (`usuario` correo, `nombre`, `rol`,
`permisos` CSV, `clave_hash` SHA-256, `correo_recuperacion`) y **`password_resets`** los
enlaces de «Olvidé mi contraseña» (token de 2 h, un solo uso). Las demás:

**`pacientes_dev` / `pacientes_prod`** — mismo esquema, una tabla por entorno

| Columna | Tipo | Uso |
|---|---|---|
| `id` | INT PK | Identificador |
| `nombre`, `apellido` | VARCHAR | Nombre del paciente |
| `telefono` | VARCHAR(20) | Formato `+569XXXXXXXXX` |
| `estado` | ENUM | `pendiente` / `enviado` / `error` |
| `whatsapp_opt_out` | TINYINT(1) | 1 si el paciente pidió no recibir más mensajes |
| `opt_out_explicito` | TINYINT(1) | 1 si esa baja la pidió el propio paciente con sus palabras por WhatsApp (la detectó el webhook). Con esto en 1, la respuesta queda **bloqueada para edición manual** (panel de Pacientes, uno o en bloque) hasta que el paciente se retracte (vuelva a escribir mostrando interés) — eso la pone en 0 solo. Una baja puesta **a mano** (panel, sin que el paciente lo haya pedido así) deja esto en 0 y sigue editable siempre |
| `respuesta_manual` | VARCHAR(12) | Corrección manual de la respuesta (NULL = sin corrección); gana sobre la señal automática |
| `interesado` | TINYINT(1) | 1 si el paciente mostró interés real. Lo marca **solo el webhook** (no se puede a mano); marcarlo revierte una baja previa; darse de baja lo pone en 0 |
| `fecha_actualizacion` | DATETIME | Última actualización |

**`envios`** — un registro por cada "Iniciar envío" (batch-level, sin nombres de pacientes)

| Columna | Uso |
|---|---|
| `base_datos` | `pacientes_dev` o `pacientes_prod` |
| `plantilla_clave`, `plantilla_nombre` | Plantilla usada |
| `total_pacientes`, `enviados`, `fallidos`, `invalidos` | Contadores del batch |
| `estado` | `completado` / `cancelado` / `rechazado` (por el supervisor) |
| `comentario` | Motivo del rechazo escrito por el supervisor (NULL si no aplica) |
| `usuario` | Cuenta (login) que inició el envío; NULL en los anteriores a esta columna. Fuente de «Envíos realizados» en Usuarios (`GET /api/usuarios/{correo}/envios`) |
| `fecha_hora` | Fecha del envío |

**`log_envios`** — un registro por mensaje individual (fuente de la columna **Error** en Pacientes y del detalle en Historial)

| Columna | Uso |
|---|---|
| `envio_id`, `paciente_id` | Enlaza al batch y al paciente |
| `nombre_paciente`, `numero_telefono`, `mensaje` | Snapshot al momento del envío |
| `estado_envio` | `enviado` / `error` / `numero_invalido` |
| `respuesta` | `pendiente` / `respondio` / `baja` |
| `whatsapp_message_id`, `estado_whatsapp` | ID del mensaje en Meta y su estado de entrega (`sent`/`delivered`/`read`/`failed`) |
| `descripcion_error` | Detalle del error (de Meta o del sistema) |

**`whatsapp_eventos`** — idempotencia del webhook: guarda un hash de cada payload recibido
(`entry + timestamp`) para no reprocesar un evento que Meta reenvíe.

**`configuracion`** — clave/valor con **toda** la configuración de la app (envío, correo
SMTP, credenciales y datos de Meta, URL pública, webhook). Lo único que NO está aquí son
las credenciales de la propia base de datos (`.env`). El backend crea y siembra esta tabla
en el primer arranque; a partir de ahí es la fuente de verdad y se edita con
`PUT /api/configuracion`. Ver la lista de claves en **Configuración** más arriba.

**`tarifas_whatsapp`** — rate card de Meta: el precio por mensaje (Marketing / Utility /
Authentication / Service) para Chile, en la moneda de facturación de la cuenta y en USD.
Cada rate card distinto se guarda una sola vez (`UNIQUE KEY uq_hash`, hash de las tarifas),
con su `efectiva_desde` y el CSV original (`csv_texto`). Lo llena `POST /api/tarifas/actualizar`,
que descarga la [página de precios de Meta](https://developers.facebook.com/docs/whatsapp/pricing/),
baja los CSV de rate card, extrae la fila «Chile» y detecta si hay tarifas nuevas o futuras.
Se usa en la sección **Costos** de Estadísticas (permiso `tarifas_editar`) para estimar el gasto por
día / mes / año aplicando a cada mensaje enviado la tarifa vigente en su fecha según la
categoría de su plantilla. Los mensajes de servicio (respuestas automáticas de call center,
texto libre en ventana de 24 h) siempre se muestran y calculan a la tarifa `service`, o a la
de Utility como respaldo cuando el card no trae `service` propia (regla de Meta, sin tramos
por volumen; rige desde el 01-10-2026: Chile CLP 17.6584 por mensaje, igual que
Utility/Authentication; antes eran gratuitos y se excluyen). Solo se cuenta lo enviado por
`api_oficial` (con `whatsapp_message_id`): lo simulado se informa aparte como `simulados`
y no se factura.

**`call_center_log`** — una fila por cada respuesta de call center enviada a un paciente
interesado, con el `numero_call_center` que le asignó el servicio de `call_center_url`, si
fue `automatico` o manual y el `estado` (`enviado`/`error`). El contador de usos por número
sale de aquí (`GROUP BY numero_call_center`). Se ve en el panel **«Registro de respuestas de
call center»** del Historial (permiso `call_center_registro`).

**`usuarios`** — cuentas de la app. `usuario` (correo o nombre corto para las semilla, único),
`nombre`, `rol` (`usuario`/`supervisor`/`administrador`/`desarrollador`), `permisos` (lista CSV, solo
cuenta para los roles `usuario` y `supervisor`), `clave_hash` (SHA-256), `correo_recuperacion` (a dónde llega
el enlace de «Olvidé mi contraseña»; se rellena solo si el `usuario` ya es un correo).

**`password_resets`** — enlaces de «Olvidé mi contraseña»: `token` (64 hex), `usuario`,
`creado`, `expira` (2 h), `usado`. Un token activo por cuenta; al usarlo se marca `usado` y
se cierran las sesiones de esa cuenta. Las filas viejas se limpian en cada arranque.

**`account_invites`** — invitaciones para crear cuenta: `token` (64 hex), `correo`,
`invitado_por` (quién la mandó), `creado`, `expira` (48 h), `usado`. Un enlace activo por
correo; los vencidos/usados se limpian en cada arranque. Creada por el backend en el primer
arranque (no está en `snw_base.sql`).

**`usuarios_auditoria`** — trazabilidad de lo que un admin/dev hizo a cada cuenta (tabla
`usuarios_auditoria`): `actor`, `accion` (`invito`, `permisos`, `rol`, `activo`,
`correo_recuperacion`, `activar_cambio_clave`, `elimino`…), `objetivo` y `detalle`. Alimenta
la sección «Actividad» del panel de una cuenta en Usuarios. También la crea el backend en el
primer arranque.

**`paciente_csv_accesos`** — registra qué cuenta incorporó cada paciente mediante un CSV,
identificado por tabla, ID de paciente y usuario. Si varias cuentas importan el mismo
paciente, cada una obtiene acceso a sus datos completos. El rol de administrador o
desarrollador **no** sustituye esta relación: quien no subió ese paciente solo ve
iniciales y teléfono oculto (`+569 **** *123`), y no puede abrir sus mensajes. Los
pacientes anteriores a esta trazabilidad no reciben un dueño supuesto: sus datos quedan
ocultos. En una tabla de área, volver a cargar un CSV con esos pacientes registra el
acceso de la cuenta que lo sube; en las tablas legacy no hay una carga CSV equivalente
en la interfaz. Al borrar pacientes o una tabla de área se eliminan también sus accesos
asociados.

Las tablas de pacientes comparten `log_envios`, así que el backend siempre ubica el
"último log" de un paciente con un `LEFT JOIN` correlacionado por `paciente_id`.

## Plantillas de mensajes

Viven en `data/plantillas.json` (no en MySQL). Cada plantilla tiene comodines
`{nombre}` y `{apellido}` que se reemplazan al enviar, y la vista previa en
**Mensajería** interpreta además el formato de WhatsApp: `*negrita*`, `_cursiva_`,
`~tachado~` y `` ```monoespaciado``` ``.

Reglas del editor:

- **El nombre de la plantilla es permanente**: una vez creada no se puede editar (solo
  eliminarla y crear otra). Evita romper la `clave` interna, que se deriva del nombre.
- **El nombre del template de Meta se genera solo** (el `slug` del nombre de la plantilla:
  minúsculas, números y `_`) **y la categoría es siempre `MARKETING`**: ninguno de los dos
  se le pide al usuario — el único campo de template visible es el **idioma**. Ambos se
  siguen guardando y enviándose a Meta igual que antes, solo que por detrás.
- Idioma y categoría del template quedan bloqueados una vez que la plantilla tiene un
  template **registrado** en Meta (`whatsapp_template_id`); mientras el registro no haya
  prosperado se pueden seguir corrigiendo.
- **Solo una plantilla `APPROVED` en Meta se puede usar para enviar mensajes.** En la lista
  de Mensajería aparecen agrupadas en tres categorías con franja de color: **«Plantillas
  aprobadas por Meta»**, **«Plantillas rechazadas por Meta»** y **«Plantillas pendientes de
  aprobación por Meta»** (esta última: recién creadas sin revisar todavía, o en estado
  `PENDING`).
  - **Aprobada**: todo disponible, como siempre.
  - **Rechazada**: se puede editar y guardar (para corregirla y volver a mandarla a
    revisión) o eliminar, pero **no** enviar.
  - **Pendiente de revisión**: de solo lectura — no se puede editar, guardar, eliminar ni
    enviar; no aparece ningún botón de acción, solo el aviso del estado. Así no se toca algo
    que Meta está evaluando en ese momento.

- **Aprobación interna previa a Meta** (campo `aprobacion_estado`): lo que crea un `usuario`
  nace `pendiente` (badge «⏳ Por aprobar») y **no se registra en Meta** —ni siquiera el cron
  la toca— hasta que un admin/desarrollador/supervisor la aprueba (botón «Aprobar y enviar a
  Meta», con modal de confirmación) o la rechaza con motivo (modal con campo de motivo, lo ve
  quien la creó). Corregir una rechazada la devuelve a `pendiente`. Sin aprobación interna no
  se puede enviar (400) ni consultar su estado Meta. Lo creado por admin/dev/supervisor nace
  `aprobada` y va a Meta de inmediato. Además **solo el creador edita o elimina sus
  plantillas** (admin/dev, cualquiera; 403 si no) y el **usuario normal no puede crear
  plantillas globales** (422): siempre una de sus áreas asignadas (si no tiene
  ninguna, el editor le indica que pida una a un administrador).

  Esto se valida también en el servidor (`PUT`/`DELETE /api/plantillas/{id}` devuelven 400
  si el estado no es `APPROVED` ni `REJECTED`; `POST /api/notificaciones/enviar` devuelve 400
  si no es `APPROVED`), así que no se puede saltar desde la API.
- **La aprobación (o el rechazo) se detecta sola, sin que nadie tenga que consultarla a
  mano**: un cron en el backend (`_revisar_plantillas_pendientes`, cada
  `plantillas_revision_minutos` — por defecto **2**, `0` lo desactiva) revisa en Meta las
  plantillas que todavía no están `APPROVED` (incluye las `REJECTED`, por si se reenvían a
  revisión) y actualiza su estado. Al arrancar el servidor el primer chequeo sale a los
  **20 s**, no espera el intervalo completo. La pantalla de Mensajería, a su vez, refresca
  la lista sola cada 30 s mientras está abierta y avisa con un **toast** apenas detecta el
  cambio: «✨ … fue aprobada por Meta» o «⚠️ … fue rechazada por Meta». Durante los
  `plantillas_badge_aprobada_minutos` siguientes (config; por defecto **10**, `0` lo apaga)
  a una aprobación esa plantilla muestra además un aviso «✨ Aprobada recientemente» (en la
  lista y en el editor); después se deja de mostrar solo (no hay que borrar nada, es por
  tiempo: se compara `whatsapp_template_aprobada_en` contra el reloj en el navegador — el
  valor del umbral llega vía `GET /api/configuracion`). El botón manual
  «Consultar estado» se quitó por quedar redundante; «Actualizar estados» / «Sincronizar»
  siguen disponibles para forzar un refresco o traer templates nuevos desde Meta.

## API

### Autenticación

| Método | Endpoint | Descripción |
|---|---|---|
| POST | `/api/auth/login` | `{usuario, clave}` → `{token, rol, nombre, permisos}`. 403 si la cuenta está desactivada (`activo=false`) |
| GET | `/api/auth/invitacion/{token}` | Verifica un enlace de invitación (48 h) → `{ok, correo}`. Usado por `registro.html` para mostrar el correo al que se le manda la invitación |
| POST | `/api/auth/activar` | Último paso de una invitación `{token, clave}`. `clave` ≥ 8 con minúscula, mayúscula y número. Nace con rol `usuario` y permisos básicos (`mensajeria`, `historial`) |
| GET | `/api/auth/me` | Rol, permisos, especialidades asignadas y `correo_recuperacion` vigentes de la sesión (el frontend lo usa para refrescarse si un admin cambió los permisos) |
| PUT | `/api/auth/clave` | Cambiar **la propia** contraseña estando dentro: `{clave_actual, clave_nueva}`. Valida la actual y la fuerza de la nueva (botón «Mi cuenta» de la barra lateral); si la cuenta tiene correo de recuperación, le manda un aviso de confirmación |
| PUT | `/api/auth/correo-recuperacion` | Define a qué correo llega el enlace de «Olvidé mi contraseña» y el aviso de cambio de contraseña: `{correo}`. **Sin campo en la interfaz** (se quitó de «Mi cuenta»); solo queda como endpoint. 409 si el correo es el usuario de otra cuenta |
| POST | `/api/auth/olvide` | **Pública.** `{correo}` → si hay una cuenta con ese correo (login o de recuperación) se le manda un enlace con un token de **2 horas** desde `correo_emisor`. Responde siempre `{ok: true}` (no revela si existe) |
| GET | `/api/auth/reset/{token}` | **Pública.** Valida el token → 200 si sirve, 400 con el motivo si no (inexistente / usado / expirado) |
| POST | `/api/auth/reset` | **Pública.** `{token, clave_nueva}` → cambia la contraseña, marca el token usado y cierra las sesiones de esa cuenta |
| POST | `/api/auth/logout` | Invalida el token actual |

### Usuarios (rol `administrador` o `desarrollador`)

| Método | Endpoint | Descripción |
|---|---|---|
| GET | `/api/usuarios` | Cuentas con `rol`, `permisos`, `especialidades` asignadas, `editable`/`motivo_bloqueo` según quién pregunta, más `desarrolladores`/`max_desarrolladores` |
| POST | `/api/usuarios/invitar` | `{correo}`. Manda un correo de invitación (enlace de 48 h a `registro.html?token=`) para que la persona cree su propia cuenta con contraseña propia; 409 si ya existe una cuenta con ese correo |
| PUT | `/api/usuarios/{correo}` | `{permisos?, nombre?, rol?, activo?}`. `rol`: un desarrollador lo cambia a cualquier valor (promover a `desarrollador` da 409 si ya hay 4); un administrador gestiona cuentas `usuario` y `supervisor` y puede darles rol `usuario`, `supervisor` o `administrador` (nunca `desarrollador`). `activo=false` desactiva la cuenta (no puede iniciar sesión) y cierra sus sesiones abiertas al instante; `activo=true` la reactiva. Nadie modifica su propia cuenta; un administrador solo toca cuentas de rol `usuario` o `supervisor`. Si `permisos` incluye `tarifas_editar`, se agrega `estadisticas` automáticamente |
| DELETE | `/api/usuarios/{correo}` | Elimina la cuenta y cierra sus sesiones (mismas reglas que PUT) |
| PUT | `/api/usuarios/{correo}/correo-recuperacion` | `{correo}`. Asigna o cambia el correo de recuperación de una cuenta que se gestiona (mismas reglas de quién puede tocar a quién que PUT). 409 si ese correo ya es el usuario o el correo de recuperación de otra cuenta |
| POST | `/api/usuarios/{correo}/enviar-cambio-clave` | Le manda a la cuenta el mismo enlace de «Olvidé mi contraseña» (2 h) a su correo de recuperación (o al propio `usuario` si ya es un correo). 400 si la cuenta todavía no tiene ningún correo asignado |
| GET | `/api/usuarios/{correo}/envios` | Envíos masivos que inició esa cuenta (tabla `envios`, últimos 200): fecha, plantilla, estado (`completado`/`cancelado`/`rechazado`), `total_pacientes` y `costo` aproximado (mismo cálculo que en el resto del sistema). De solo lectura: cualquier admin/dev puede consultarlo para cualquier cuenta, sin las restricciones de «quién gestiona a quién» (esas son solo para editar) |
| GET | `/api/usuarios/{correo}/auditoria` | Trazabilidad de la cuenta (tabla `usuarios_auditoria`, últimos 200): quién la invitó, quién le cambió rol/permisos/acceso, quién le asignó un correo de recuperación o le activó el cambio de contraseña, y quién la eliminó (si ya no existe, el registro se conserva). De solo lectura, mismas reglas que `/envios` |

**Nadie cambia la contraseña de otra cuenta directamente**; solo puede activarle el enlace de
cambio (arriba) para que la persona elija una nueva. Si alguien la olvida usa «¿Olvidaste tu
contraseña?» en el login (`/api/auth/olvide`) — mismo mecanismo, pero autoservicio.

### Pacientes

La base de desarrollo/producción requiere el permiso `pacientes`; las bases de área son
visibles para las cuentas asignadas. En ambos casos, ver una base **no** concede acceso a
los datos personales completos de todos sus pacientes: rige la autoría del CSV descrita
en `paciente_csv_accesos`.

El **estado y la respuesta solo se pueden editar manualmente en números de prueba**:
`numeros_prueba_dev` para `pacientes_dev` y `numeros_prueba_prod` para
`pacientes_prod` y las bases de área. Los demás registros son de solo lectura para
estos campos, incluso para admin/dev. Una petición individual o masiva que incluya
un número no autorizado devuelve 403 sin modificar ningún registro. Esto no impide
las actualizaciones automáticas del motor de envío o del webhook ni el borrado
autorizado de registros en una base de área. Una baja **manual** solo se replica
en otras bases si ese mismo número también figura como prueba allí.

| Método | Endpoint | Descripción |
|---|---|---|
| GET | `/api/pacientes?q=&ambiente=&area_id=` | Lista de la base indicada; con `area_id` lee `pacientes_<slug>` (403 si no está asignada). Cada fila indica `datos_completos` y `editable` (si su número está en la lista de prueba de la base). Para quien no importó al paciente, devuelve solo iniciales y teléfono oculto, sin último mensaje ni error. La búsqueda `q` se aplica a esos valores visibles |
| PUT | `/api/pacientes/{id}?ambiente=&area_id=` | Cambiar `estado` (`pendiente`/`enviado`/`error`) de **un** paciente |
| PUT | `/api/pacientes/estado-masivo?ambiente=` | `{pacientes: [ids], estado}` — igual que arriba pero para **varios** pacientes a la vez (selección en la pestaña Pacientes) |
| PUT | `/api/pacientes/{id}/respuesta?ambiente=` | Ajuste manual de la respuesta (`pendiente`/`baja`) de **un** paciente de prueba; `baja` activa el opt-out. 409 si el paciente pidió la baja explícitamente por WhatsApp y se intenta poner algo distinto de `baja` (ver `opt_out_explicito`) |
| PUT | `/api/pacientes/respuesta-masiva?ambiente=` | `{pacientes: [ids], respuesta}` — igual que arriba pero para **varios** pacientes a la vez. Los que tengan la baja bloqueada se saltan (no fallan los demás); responde `{actualizados, bloqueados}`; 409 solo si **todos** los seleccionados están bloqueados |
| GET | `/api/pacientes/{id}/mensajes?ambiente=&area_id=` | Hilo completo de mensajes entrantes y salientes; marca `interes: true` los entrantes que suenan a interés. **Solo** la cuenta que incorporó a ese paciente por CSV puede consultarlo (403 para las demás, incluso admin/dev). Es lo que muestra «Ver mensajes» en Historial |

### Plantillas

| Método | Endpoint | Descripción |
|---|---|---|
| GET | `/api/plantillas` | Lista de plantillas (incluye las de call center; el frontend de Mensajería las filtra). Cada cuenta recibe solo su alcance: el usuario normal ve solo las que creó y las de sus especialidades |
| POST | `/api/plantillas` | Crear `{nombre, texto, whatsapp_template_lang, whatsapp_template_categoria, area_id?}`. `whatsapp_template_categoria` es obligatoria (`UTILITY` / `MARKETING` / `AUTHENTICATION`); sin ella → 400. `area_id` asocia la plantilla (404 si no existe, 403 si no está asignada). **El nombre no se puede repetir** (409 si ya existe) **ni reutilizar el de una eliminada hasta pasados 30 días** (409 con los días restantes; política de Meta, registro en `data/plantillas_eliminadas.json`). **El usuario normal no puede crear plantillas globales** (422): siempre una de sus especialidades. **Aprobación interna**: lo creado por admin/dev/supervisor nace `aprobada` y va a Meta de inmediato; lo creado por un `usuario` nace `pendiente` y **no se registra en Meta** hasta que se aprueba |
| POST | `/api/plantillas/{id}/aprobar` | (admin/dev/supervisor) Aprueba una pendiente y la registra en Meta |
| POST | `/api/plantillas/{id}/rechazar` | (admin/dev/supervisor) `{motivo?}` Rechaza una pendiente (no va a Meta); su creador puede corregirla y vuelve a pendiente |
| PUT | `/api/plantillas/{id}` | Actualizar `{nombre, texto, ...}` — rechaza (400) si `nombre` cambió, si falta `whatsapp_template_categoria`, si es una plantilla de call center, o si el estado en Meta no es `APPROVED` ni `REJECTED` (pendiente de revisión). **Solo el creador** (admin/dev, cualquiera; 403 si no). Acepta cambiar `area_id` con la misma validación que al crear; si **solo** cambia el área (valor local), no se reenvía a Meta ni corre el enfriamiento de 24 h |
| DELETE | `/api/plantillas/{id}` | Eliminar. **Solo el creador** (admin/dev, cualquiera; 403 si no). Borra también el template en Meta (`DELETE /{waba_id}/message_templates?name=…`); si Meta falla la plantilla local se borra igual y la respuesta trae `meta_advertencia`. Rechaza (400) las de call center o las que no estén `APPROVED` ni `REJECTED` en Meta |
| GET | `/api/plantillas/{id}/estado-meta` | Consulta en Meta el estado real de un template |
| POST | `/api/plantillas/estado-meta/actualizar` | Refresca el estado de todas las plantillas con template |
| POST | `/api/plantillas/sincronizar-meta` | Lee los templates que existen en Meta: actualiza estado/id de los conocidos e **importa como plantilla nueva** los que falten (no crea/edita nada en Meta, solo lee). Cada cuenta solo recibe en la respuesta las plantillas de su alcance |

#### Mensaje de call center

El mensaje que recibe un paciente que responde que **le interesa** es **fijo**, definido en
el backend (`CALL_CENTER_PLANTILLA_FIJA` en `main.py`) — no es editable desde la app ni
tiene template de Meta (va como texto libre, ventana de 24 h). No hay gestión de
"plantillas" de call center: solo queda su registro de envíos (ver abajo).

- **Envío automático:** cuando el webhook detecta interés (mensaje de texto o botón «Me
  interesa» de una plantilla normal), 1 segundo después se manda el mensaje fijo de call
  center (`CALL_CENTER_AUTO_SEGUNDOS` en `main.py`; no es configurable). Se manda **una vez
  por cada plantilla enviada al paciente**: si desde el último call center hubo un nuevo
  envío de plantilla y el paciente vuelve a mostrar interés, se le manda otro; si ya lo
  recibió después del último envío de plantilla, no se repite (aunque mande varios mensajes
  de interés seguidos — hay además un guard por número mientras hay un envío programado). No
  hay envío manual. Si el paciente venía de baja y se reactiva con ese mismo interés, esta es
  la **única** respuesta que recibe: no se suma el aviso de «¡Bienvenido/a de vuelta!» (ver
  «Avisos automáticos» en «Sistema de baja»).
- **Botón:** el mensaje incluye un botón CTA que abre el chat del call center
  (`https://wa.me/<número>`, mensaje interactivo `cta_url`). El número se pide en cada
  respuesta a **`call_center_url`** (configurado en **Configuración → Call center**), un
  servicio que devuelve un número —solo dígitos, con código de país— y ya reparte la carga
  entre los teléfonos por su cuenta. Si esa URL no responde, se usa el respaldo manual
  `call_center_numeros` (el menos usado según `call_center_log`). El botón autocompleta un
  mensaje en el chat (`?text=`): `call_center_boton_mensaje_oferta` si la última plantilla
  enviada al paciente mencionaba un descuento / oferta / precio especial (detección por
  palabras clave sobre `log_envios.mensaje`), o `call_center_boton_mensaje` en cualquier otro
  caso (ambos en **Configuración → Call center**; vacíos = sin autocompletar).
- **Registro:** cada envío queda en `call_center_log` con el número asignado. El panel
  **«Registro de respuestas de call center»** del Historial (permiso propio
  `call_center_registro`) lo muestra: fecha, nombre y número del paciente (ocultos para
  quien no lo incorporó por CSV), número de call
  center, origen y estado, más los usos por número. Admin y desarrollador lo ven por
  defecto; a un `usuario` se le puede asignar ese permiso.

| Método | Endpoint | Descripción |
|---|---|---|
| GET | `/api/call-center/log` | (permiso `call_center_registro`) `{entradas, contadores}` — últimas 200 respuestas de call center + usos por número. Es el panel *Registro de respuestas de call center* del Historial. Nombre, número y error del paciente se ocultan si la cuenta no lo importó por CSV, independientemente de su rol. Cuentas no privilegiadas solo ven filas de sus especialidades |

### Envíos

| Método | Endpoint | Descripción |
|---|---|---|
| POST | `/api/notificaciones/enviar` | Inicia el envío `{pacientes: [ids] \| null, plantilla_id, ambiente, limite?, area_id?}`. `pacientes: null` = todos los elegibles (usado desde Mensajería). `limite` (solo producción y especialidades) recorta cuántos pendientes entran en esta tanda; el resto quedan pendientes. Con `area_id` envía a la tabla `pacientes_<slug>` (403 si no está asignada; una plantilla de otra especialidad da 400). El rol `usuario` solo puede enviar a sus especialidades o a desarrollo (403 a producción legacy). Rechaza (400) si la plantilla no está `APPROVED` en Meta; 409 si en ese momento hay **otro envío en curso en esa misma base** |
| POST | `/api/notificaciones/destinatarios` | Cuenta pacientes totales, pendientes y libres de una base (los libres descuentan los reservados por programados). Con `plantilla_id`, incluye el costo aproximado solo para supervisor/admin/dev si hay tarifa disponible. Con fecha, informa el cupo diario de Meta para esa fecha cuando corresponde a `api_oficial` en producción |
| GET | `/api/notificaciones/jobs/{job_id}` | Progreso en vivo del envío en curso |
| GET | `/api/notificaciones/envio-en-curso` | Envíos masivos activos (en proceso o pausados, sin destinatarios): Mensajería muestra un banner para retomarlos aunque se haya cerrado el modal |
| POST | `/api/notificaciones/jobs/{job_id}/pausa` \| `/reanudar` \| `/cancelar` | Control del job en curso |
| POST | `/api/notificaciones/prueba-wa` | (solo desarrollador) Envía un mensaje de prueba real vía API oficial |

### Envíos programados

Al programar se reserva una lista de destinatarios libres; un paciente ya reservado por
otro programado activo no vuelve a contarse como disponible. La fecha y hora deben estar
al menos un minuto en el futuro. El cupo de Meta para el día elegido solo se aplica en
producción con `api_oficial`. Las solicitudes que requieren aprobación quedan pendientes
hasta que un administrador o supervisor autorizado las decida.

| Método | Endpoint | Descripción |
|---|---|---|
| POST | `/api/notificaciones/programados` | Crea un envío con plantilla, base, fecha/hora y cantidad obligatoria; preelige destinatarios sin exceder los libres ni, cuando aplique, el cupo diario de Meta |
| GET | `/api/notificaciones/programados` | Lista los programados visibles para la cuenta, con base, estado y cantidad preelegida |
| GET | `/api/notificaciones/programados/resumen` | Estado resumido para actualizar la lista sin descargar todos los detalles |
| GET | `/api/notificaciones/programados/{id}/destinatarios` | Lista preelegida **solo de la base de desarrollo** (403 para las demás); oculta nombre y teléfono de pacientes que la cuenta no importó por CSV |
| POST | `/api/notificaciones/programados/{id}/aprobar` \| `/rechazar` \| `/cancelar` | Decide o cancela un programado según los permisos de la cuenta |

### Confirmación / rechazo por correo (producción)

| Método | Endpoint | Descripción |
|---|---|---|
| GET | `/api/notificaciones/solicitud/{token}` | Estado de una solicitud pendiente (usado por polling del frontend) |
| GET | `/api/notificaciones/confirmar/{token}` | Confirmar envío (link del correo). 409 y página HTML si para entonces ya hay un envío en curso en **esa misma base**: la solicitud queda `pendiente` hasta que haya lugar |
| GET / POST | `/api/notificaciones/rechazar/{token}` | Formulario y envío del rechazo, con comentario opcional (máximo 255 caracteres; 422 si se pasa) |

### Historial

| Método | Endpoint | Descripción |
|---|---|---|
| GET | `/api/notificaciones/historial?ambiente=todos&area_id=` | Envíos batch (`ambiente=todos` junta ambas bases). Cuentas no privilegiadas solo ven sus especialidades (las filas legacy las ven solo admin/dev); con `area_id` filtra (403 si no está asignada) |
| GET | `/api/notificaciones/historial/{id}/detalle?ambiente=` | Pacientes de un envío y campo `datos_completos` por fila. Si quien consulta no importó a ese paciente por CSV, devuelve iniciales, teléfono oculto y omite el mensaje de respuesta y el error, aunque sea admin/dev. Envíos de especialidad: 403 si no está asignada; legacy: solo admin/dev |
| PUT | `/api/notificaciones/historial/{id}/respuesta?ambiente=` | Corregir la respuesta de un registro |

### Estadísticas (solo `administrador` / `desarrollador`)

Las cuentas `usuario` y `supervisor` reciben 403 en estos endpoints (y la migración les retira los permisos `estadisticas`/`tarifas_editar` si los tenían).

| Método | Endpoint | Descripción |
|---|---|---|
| GET | `/api/estadisticas` | Resumen para Estadísticas (**solo envíos de producción**): mensajes `enviado` del mes, desglose, totales, `pacientes_por_respuesta` y `pacientes_por_interes` (interesados, no interesados y sin clasificar entre quienes ya recibieron un envío), más `webhook` (último evento de Meta). Con `?area_id=` filtra a esa especialidad |
| GET | `/api/estadisticas/envios?granularidad=dia\|mes\|anio` | Mensajes enviados de producción agrupados por periodo, para el gráfico de barras (día = últimos 30, mes = últimos 12, año = últimos 6). Acepta `&area_id=` |
| GET | `/api/estadisticas/costos?granularidad=dia\|mes\|anio` | (permiso `tarifas_editar`) Costo estimado agrupado por periodo. Acepta `&area_id` y `&categoria=marketing\|utility\|authentication\|service` (filtra; lo no coincidente no se cuenta). Factura cada categoría a su tarifa de `tarifas_whatsapp` vigente en su fecha; servicio usa la tarifa `service` o la de Utility como respaldo (rigen desde el 01-10-2026). Sin categoría facturable o servicio gratuito en ese periodo va a `excluidos` |
| GET | `/api/tarifas` | (permiso `tarifas_editar`) Tarifas guardadas: `vigente`, `proxima` (tarifa futura ya publicada por Meta), `usd_vigente`, `historial`, moneda de la cuenta y fecha de la última descarga |
| POST | `/api/tarifas/actualizar` | (permiso `tarifas_editar`) Descarga la página de precios de Meta y sus CSV, guarda los rate cards nuevos de Chile (`INSERT IGNORE` por hash), autodetecta la moneda de facturación (`GET {waba}?fields=currency` → `wa_moneda`) y devuelve si hubo cambio |
| GET | `/api/tarifas/chile.csv` | (permiso `tarifas_editar`) Descarga el CSV original del rate card de Chile (prefiere la moneda de la cuenta, si no USD) |

### Configuración (solo rol `desarrollador`)

| Método | Endpoint | Descripción |
|---|---|---|
| GET | `/api/configuracion?ambiente=` | Vista mínima para las pantallas y el motor de envío (`entorno`, `base_datos`, `numeros_autorizados`, `metodo_envio`, `intervalo_ms`) — cualquier sesión. Los valores completos y los secretos van por `/api/configuracion/todo` |
| PUT | `/api/configuracion` | Guarda claves sueltas de `configuracion` sin reiniciar |
| GET | `/api/configuracion/todo` | TODAS las claves con su **valor real** (incluye secretos) + metadata de secciones, para la página Configuración |
| PUT | `/api/configuracion/todo` | `{cambios: {clave: valor, …}}` — valida clave conocida, enums (`entorno`, `metodo_envio`) y enteros (`intervalo_ms`, `smtp_port`, `wa_rate_limit_*`, `wa_throughput_mps`, `wa_messaging_limit_24h`); `call_center_numeros` (respaldo) se normaliza a lista de solo-dígitos separada por coma; persiste con `config_set` |
| GET | `/api/whatsapp/rate-limit` | (solo `desarrollador`) Consumo de cuota de la Graph API visto en la última respuesta de Meta y la espera que el sistema aplica: `{activo, uso_pct, bloqueado, bloqueado_segundos, pausa_sugerida_s, throughput_mps, ultimo_motivo, cabecera_hace_s}` |
| GET | `/api/whatsapp/messaging-limit` | (admin / dev) `{tier, usados_24h, disponibles, ventana_horas}` — usuarios únicos contactados (mensajes iniciados por el negocio) en las últimas 24 h frente al `wa_messaging_limit_24h` |
| PUT | `/api/whatsapp/messaging-limit` | (admin / dev) `{limite: int}` — actualiza `wa_messaging_limit_24h` (0 = ilimitado) para ajustarlo a lo que indique el dashboard de Meta; devuelve el estado actualizado |

### Áreas — Fase 1 (múltiples áreas)

Cada especialidad vive en **una sola tabla** `pacientes_<slug>` dentro de `snw_base` (**sin** sufijo `_dev`/`_prod`). El slug se genera del nombre visible en minúsculas, sin acentos, espacios ni caracteres especiales (`Kinesiología Sede Maipú` → `pacientes_kinesiologiasedemaipu`). El rol global de `usuarios` no se toca: el acceso por área va en `roles_area` / `usuario_area_roles` (tablas `areas`).

| Método | Endpoint | Descripción |
|---|---|---|
| GET | `/api/areas` | (admin / dev) Todas las especialidades con su tabla y rol |
| GET | `/api/areas/mias` | Especialidades visibles para la sesión (todas si es admin/dev, solo asignadas si no) |
| POST | `/api/areas` | (admin / dev) `{nombre, modo}` — `preguntar` (por defecto): si el nombre visible ya existe responde 409 con las coincidencias sin crear nada; `reutilizar`: usa la existente; `nueva`: crea `pacientes_<slug><n>` con el primer sufijo libre (con base, 2 y 4 ocupados usa 3) |
| PUT | `/api/areas/{id}` | (admin / dev) `{nombre_visible}` — renombra especialidad y rol; la tabla física **no** cambia |
| POST | `/api/areas/{id}/roles` | (admin / dev) `{usuario}` — asigna a la cuenta el rol de la especialidad |
| DELETE | `/api/areas/{id}/roles/{usuario}` | (admin / dev) — retira el rol de la especialidad |
| POST | `/api/areas/{id}/pacientes/csv` | Carga CSV (`nombre, apellido, telefono`, UTF-8, máx. 5 MB) en la tabla de la especialidad; normaliza a `+569XXXXXXXX` e informa `{procesados, insertados, duplicados, rechazados, errores}`. Registra el acceso de quien lo subió también para los pacientes duplicados que ya existían. Admin/dev o cuentas con permiso de mensajería y el rol asignado |
| DELETE | `/api/areas/{id}/pacientes/{pid}` | (admin / dev) Borra UN registro de la tabla (más sus filas de log propias) |
| DELETE | `/api/areas/{id}/tabla` | (admin / dev) Elimina la especialidad entera: tabla, rol, asignaciones y registro. El historial de envíos se conserva. Las tablas legacy (`pacientes_dev`/`pacientes_prod`) no se pueden borrar |

### Áreas — Fase 2 (autorización en backend)

- **Sesión enriquecida**: `login`, `/api/auth/me` y `/api/auth/activar` devuelven `especialidades` (todas si es admin/dev, solo asignadas si no); cada petición refresca `area_ids` junto a rol y permisos.
- **Rol global `supervisor`**: como `usuario` pero pensado para aprobar envíos de sus áreas (el enrutado de la confirmación por área llega en Fase 3). El administrador gestiona cuentas `usuario` y `supervisor`.
- **Pacientes/envíos/historial/plantillas** aceptan `area_id`: 404 si no existe, **403** si no está asignada. Las plantillas pueden asociarse a una especialidad y solo se envían en ella.
- **`envios` y `log_envios`** guardan `area_id` + `tabla_pacientes` (NULL = fila legacy); los historiales no mezclan tablas con IDs coincidentes.
- **Estadísticas y tarifas**: solo admin/dev (403 para el resto).
- **Webhook**: busca pacientes también en `pacientes_<slug>`, etiqueta sus filas de log y el call center automático también responde en especialidades (anti flip-flop siempre activo ahí).

### Áreas — Fase 3 (frontend + supervisión)

- **Pestaña Áreas** (administración, admin/dev): crear (con flujo duplicado: usar existente o crear `Kinesiología 2`), renombrar (la tabla no cambia), asignar/retirar roles por cuenta y **eliminar la tabla completa** (zona de peligro, con confirmación; el historial se conserva).
- **Usuarios** muestra el rol `supervisor` y las áreas asignadas de cada cuenta; «Envíos realizados» y «Actividad» paginan de a 10.
- **Pacientes**: **selector único de base de datos** (desarrollo/producción/áreas, siempre con el nombre físico de la tabla), **carga CSV** en la tabla de la área (informe de insertados/duplicados/rechazados) y **paginación** (10 a 100 por página, se recuerda).
- **Mensajería**: área por plantilla (badge en la lista + campo en el editor), **filtro «Filtrar por área»** (Todas/Globales/cada una) y selector de base de datos en los envíos manuales y programados. Se elige la base antes de definir la cantidad; las plantillas de área fuerzan su base y «Hello World» solo admite desarrollo. La cantidad se elige expresamente como porcentaje o número de pacientes. Modales de confirmación al aprobar y de motivo al rechazar.
- **Historial**: selector de base (Todas/Desarrollo/Producción/áreas según permisos), con indicador de carga en la tabla al cambiarlo. El detalle muestra iniciales; «Ver mensajes» solo aparece para los pacientes que la cuenta importó por CSV.
- **Estadísticas** (admin/dev): selector de área o «Todas» en resumen, gráfico y costos.
- **Confirmación de supervisor**: el correo de solicitud llega también a los supervisores activos del área (con su nombre en el mensaje); los enlaces por token sirven para cualquiera de los destinatarios.
- **Estilo unificado de selects**: todos los desplegables de todas las vistas comparten el verde pastel corporativo (distinto del sólido de los botones).

### Webhook de WhatsApp (Meta)

| Método | Endpoint | Descripción |
|---|---|---|
| GET | `/api/whatsapp/webhook` | Verificación inicial (`hub.mode`, `hub.verify_token`, `hub.challenge`) |
| POST | `/api/whatsapp/webhook` | Recepción de eventos: mensajes entrantes y cambios de estado |

## Integración con WhatsApp Business Cloud API

La capa de integración vive en `backend/whatsapp_service.py` (cliente Graph API +
`WhatsAppService` + `WebhookHandler`) y `backend/whatsapp_webhook.py` (router). Está
separada de la lógica de negocio de mensajería. Usa la **Graph API v26.0** por defecto
(configurable en la clave `wa_graph_version` de la tabla `configuracion`).

### Envío de mensajes

- Si la plantilla tiene `whatsapp_template` (su nombre se genera solo desde el nombre de
  la plantilla), se envía como `type: "template"`, con los comodines convertidos a
  `{{1}}`, `{{2}}`... en el orden en que aparecen en el texto.
- Si no, se envía como texto libre (`type: "text"`), válido **solo dentro de la ventana
  de 24 h** en la que el cliente escribió al negocio.

### Templates: cuenta con permiso limitado

Algunas cuentas de WhatsApp Business no tienen permiso para **crear ni editar**
templates vía API (Meta responde con un error de permisos al intentarlo); en ese caso los
templates se gestionan a mano desde **Meta Business Suite → WhatsApp Manager**. El botón
**"Sincronizar"** en Mensajería (`POST /api/plantillas/sincronizar-meta`) resuelve esto
leyendo los templates existentes en Meta (solo `GET`, nunca crea/edita) y:

- actualiza estado, id y categoría de las plantillas que ya conocen su template, y
- **importa como plantilla nueva** cualquier template que exista en Meta y no tenga
  todavía una plantilla local asociada. El texto se extrae del componente `BODY` y sus
  placeholders `{{1}}`, `{{2}}`... se traducen a los comodines internos (`{nombre}`,
  `{apellido}`) usando los valores de ejemplo del template (`example.body_text` — los
  mismos que manda esta app al crear un template, ver `WhatsAppService.COMODINES`), así que
  no importa el orden en que aparezcan. Sin ejemplo (templates hechos a mano en Meta) se
  asume el orden habitual de esta app (`{{1}}` = nombre, `{{2}}` = apellido); cualquier
  posición que no se pueda identificar queda como `{variable}` (`_texto_desde_componentes`
  en `main.py`).

### Límites de envío de Meta

`backend/wa_rate_limit.py` es un gobernador en memoria que cubre las tres capas de límites
de Meta. Toda llamada a la Graph API pasa por `WhatsAppApiClient._peticion`.

**1 · [Rate limits de la Graph API](https://developers.facebook.com/docs/graph-api/overview/rate-limiting/)**
— antes de cada llamada espera lo que sugiera el gobernador, que lee de cada respuesta las
cabeceras `X-App-Usage` y `X-Business-Use-Case-Usage` (`call_count`, `total_cputime`,
`total_time`) y se queda con el % de cuota más alto. Por debajo de `wa_rate_limit_umbral_pct`
(80 %) no espera; entre el umbral y el 100 % interpola hasta `wa_rate_limit_pausa_max_s`
(30 s). *(La Cloud API no manda estas cabeceras en `/messages`; sí en plantillas / management.)*

**2 · [Throughput de la Cloud API](https://developers.facebook.com/documentation/business-messaging/whatsapp/throughput)**
— 80 msg/s por número (entrantes + salientes). Antes de cada `POST /messages` se reserva un
turno para no pasar de `wa_throughput_mps` (10; `0` lo desactiva); varios hilos enviando a la
vez se turnan. Si aun así Meta devuelve `130429`, backoff como en el punto 3.

**3 · Errores de límite** — se clasifican en dos:

- **Global** (`429`, `4`, `17`, `32`, `613`, `80007`, `80008`, `130429`, `131048`, `131057`,
  `368`, subcódigo `2446079`): se respeta `estimated_time_to_regain_access` (minutos) o el
  `Retry-After`; si no hay dato, `wa_rate_limit_espera_defecto_s` (60 s) o el mínimo por
  código (throughput 2 s, upgrade 60 s, spam 900 s), tope 15 min. Bloquea **todas** las
  llamadas hasta esa hora y reintenta la misma llamada hasta `wa_rate_limit_reintentos` (3).
- **Por destinatario** (`131056` pair rate limit, `131049` / `130497` frecuencia por
  usuario): **no** frena al resto ni reintenta; falla solo ese mensaje (queda `error` en el
  historial y se puede reenviar en otra tanda).

**4 · [Messaging limit](https://developers.facebook.com/documentation/business-messaging/whatsapp/messaging-limits)**
— el límite configurado en `wa_messaging_limit_24h` se aplica a los envíos de
**producción con motor `api_oficial`**; no limita los envíos simulados ni la base de
desarrollo. Para un envío manual se consulta el cupo actual de la ventana de 24 h. Para
uno programado se calcula el cupo del día elegido descontando los destinatarios ya
contactados ese día y los reservados por otros programados activos para la misma fecha.
La interfaz de programados muestra por separado los pacientes libres en la base (paso 2)
y el límite disponible de Meta para la fecha (paso 4); impide indicar una cantidad mayor que
cualquiera de ambos. Admin/dev pueden ajustar el límite configurado mediante
`PUT /api/whatsapp/messaging-limit`.

En el envío masivo (`_procesar_job`), si hay que esperar ≥ 1 s el job muestra «Esperando por
el límite de la API de Meta (~N s)» y la espera es cancelable. `GET /api/whatsapp/rate-limit`
(dev) y `GET /api/whatsapp/messaging-limit` (admin/dev) muestran el estado.
`wa_rate_limit_activo` apaga solo el frenado **proactivo** por cuota; el backoff ante un
error de límite y el throughput se aplican siempre.

### Ruteo del webhook

Meta manda **todo** bajo `field: "messages"`; el backend distingue por el contenido de
`value`:

- `value.messages[]` → mensaje entrante del cliente → `_procesar_mensajes`
- `value.statuses[]` → cambio de estado de entrega → `_procesar_estados`
- `field: "message_template_status_update"` → aprobación/rechazo de un template (se ignora)

El `wa_id` de Meta viene **sin `+`** (`56993921740`); la comparación con `pacientes.telefono`
(`+56993921740`) ignora el `+` y los espacios.

### Estados de entrega

Al enviar se guarda el `whatsapp_message_id` en `log_envios`. Los cambios de estado
(`sent` → `delivered` → `read` → `failed`) llegan por webhook y actualizan
`estado_whatsapp` (y, si es `failed`, también `estado_envio` y `descripcion_error` con el
motivo que reporta Meta — se ve en la columna **Error** de Pacientes).

### Respuestas entrantes

Cuando un cliente escribe de vuelta, el webhook marca su `estado = 'enviado'` e inserta una
fila en `log_envios` (`plantilla_clave = 'respuesta'`, `respuesta = 'respondio'`) con el
texto del mensaje. En **Pacientes** aparece el badge "Respondió" con la fecha (y el texto
al pasar el mouse); en **Estadísticas** se cuenta en "Respuestas de pacientes".

### Sistema de baja (opt-out)

Si el mensaje entrante coincide con una expresión de baja, el webhook marca
`whatsapp_opt_out = 1` en el paciente (en ambas tablas si el número está en las dos) y
`respuesta = 'baja'` en su `log_envios`. Un paciente así queda **excluido de cualquier
envío futuro** (se filtra en `POST /api/notificaciones/enviar` y se deshabilita su checkbox
en Pacientes).

La detección (`_es_baja` en `whatsapp_service.py`) reconoce: palabras sueltas (`baja`,
`stop`, `cancelar`, `no`…), frases (`darme de baja`, `no quiero recibir`, `dejar de
recibir`, `no molestar`, `borrame`…) y el botón nativo de Meta en templates de marketing
(`Detener promociones` / `Stop promotions`, incluido su `payload`). Meta no manda un evento
explícito de baja.

**Avisos automáticos.** El webhook responde al paciente con dos mensajes fijos de texto
libre, dentro de la ventana de 24 h (ver `_enviar_mensaje_directo` en `main.py`):
«Lamentamos que te vayas…» al darse de baja, y «¡Bienvenido/a de vuelta! Ya reactivamos tus
notificaciones» al reactivarse. Ambos se deciden **al cierre del evento**
(`_procesar_mensajes` en `whatsapp_service.py`), con el estado opt-out con que ese evento
termina, no mensaje a mensaje:

- **El evento termina de baja habiéndolo estado al inicio** (el paciente ya estaba de baja y
  vuelve a escribir que se quiere dar de baja — incluye una reactivación seguida de una baja
  en el mismo evento): **ninguna respuesta**. Evita el «bienvenido + lamentamos» seguido.
- **El evento termina de baja sin estarlo al inicio** (baja nueva): «Lamentamos que te
  vayas…», **una sola vez**. El aviso se manda a una única base (producción, y solo como
  respaldo desarrollo), así un paciente que existe en ambas no lo recibe doble.
- **El evento termina reactivado** por un mensaje sin interés (una baja puesta a mano por un
  admin/dev, y el paciente escribe «hola», «quiero volver»…): «¡Bienvenido/a de vuelta!».
  Si ese mensaje expresa **interés**, no se suma el aviso de reactivación: la única respuesta
  es la de call center (ver «Mensaje de call center»).

Un aviso nunca se reenvía por un webhook repetido ni por un segundo mensaje del paciente que
no cambie el estado final.

**Anti flip-flop (baja ↔ reintegración).** Para que un paciente no juegue con los botones, un
paciente que **se dio de baja y se reintegra** no puede volver a darse de baja hasta que pasen
**24 h** desde la reintegración: solo se permite un ciclo baja → reintegración cada 24 h. Al
reactivarse (por retractación o por interés) se guarda la fecha en `pacientes.ultimo_reintegro`;
si intenta darse de baja otra vez dentro de esas 24 h, la baja **se ignora** (no se marca el
opt-out ni se manda la despedida). Un interés de alguien que nunca estuvo de baja no cuenta
como reintegración, así que no le impide darse de baja después. La restricción está **siempre
activa en producción**; en **desarrollo** se controla con la opción Configuración → **Bajas y
reactivaciones** → *«Aplicar el límite también en desarrollo»* (`anti_flip_flop_dev`; por
defecto activada). Desactívala para probar el flujo de baja/reintegración sin límite en
desarrollo.

**Mensajes de interés:** el texto literal de cada respuesta del paciente se guarda en
`log_envios.mensaje`. En el **detalle de un envío del Historial** aparece bajo la respuesta
de cada paciente, y se resalta **en verde** cuando el mensaje muestra intención positiva
(`me interesa`, `estoy bien interesado`, `sí`, `quiero agendar`, `confirmo`… o cualquier
forma del verbo *interesar*). La detección es `es_mensaje_interes` en `whatsapp_service.py`.
**Una negación anula el interés:** si el mensaje contiene `no` (o `nooo`, `nunca`, `jamás`,
`tampoco`…) como palabra suelta, no se marca como interés aunque diga «interesa». Una baja
tampoco cuenta como interés.

Cuando el webhook detecta interés marca `pacientes.interesado = 1` y **revierte cualquier
baja previa** (pone `whatsapp_opt_out = 0` y convierte las filas `respuesta = 'baja'` del
paciente a `'respondio'`). Al revés, cuando el paciente **se da de baja** se le quita la
marca de interés (`interesado = 0`), tanto por webhook como por el ajuste manual. Así, si
escribió "quiero darme de baja" y más tarde "en realidad me interesa", la badge de
*interesado* desaparece y vuelve a aparecer con cada cambio. En el **detalle de un envío del
Historial** hay una columna *Detalle* con un botón **«Ver mensajes»** solo si la cuenta
importó a ese paciente por CSV. Abre el hilo completo; ahí se ve el estado de interés (de solo
lectura — **no se puede marcar/desmarcar a mano**, solo lo detecta el webhook) y, con el
permiso `call_center`, si está interesado se puede enviarle el mensaje de call center.
El título del detalle muestra solo iniciales y el encabezado del hilo oculta el teléfono
como `+569 **** *123`, incluso para la cuenta que tiene acceso a los mensajes.

**Ajuste manual:** en Pacientes, hacer click en el badge de la columna **Respuesta** abre
un selector para marcar a mano `respondió` / `se dio de baja` / etc. — útil si el webhook
no llegó o el paciente avisó por otro canal. `PUT /api/pacientes/{id}/respuesta` (solo
admin) guarda la corrección en la columna `respuesta_manual` de la tabla de pacientes;
`baja` activa el opt-out y las demás lo revierten. Esa corrección **gana** sobre la señal
automática y se limpia sola si más tarde llega una respuesta real por el webhook.

**Baja explícita (bloqueada para edición manual):** si la baja la detectó el webhook a
partir de las propias palabras del paciente, la columna `opt_out_explicito` queda en 1 y
esa respuesta **no se puede cambiar a mano** — ni desde el selector de Pacientes ni por la
edición en bloque — mientras siga en `baja`. Solo se libera si el **propio paciente**
vuelve a escribir mostrando interés (eso limpia `opt_out_explicito` solo, vía
`_registrar_interes`). Una baja puesta **a mano** por un admin/dev (el paciente avisó por
otro canal, o fue un error) no activa este bloqueo: `opt_out_explicito` queda en 0 y esa
respuesta se puede seguir corrigiendo libremente, como cualquier otro ajuste manual.

### Quién respondió / se dio de baja

La "respuesta efectiva" de un paciente se calcula con esta prioridad:

1. **opt-out activo** → `baja`
2. **corrección manual** (`respuesta_manual`), si existe
3. **señal automática 'pegajosa'**: la más fuerte que haya tenido alguna vez
   (`baja` > `respondió`); un envío posterior no la borra
4. `pendiente`

Un mensaje de **interés** posterior a una baja rompe la prioridad 1 y 3: limpia el opt-out
y reescribe las filas `baja` del paciente, de modo que la respuesta efectiva pasa a `respondió`.

Se calcula igual en:

- **`GET /api/pacientes`** → campo `respuesta` (columna y filtros en la página Pacientes: *quiénes*).
- **`GET /api/estadisticas`** → `pacientes_por_respuesta` con los totales por estado (panel
  "Respuestas de pacientes" en Estadísticas: *cuántos*). Solo cuenta pacientes de producción
  con `estado = 'enviado'` — los que aún están `pendiente` o `error` no entran.
- El mismo endpoint devuelve `pacientes_por_interes`: interés explícito, rechazo explícito
  de la oferta y pacientes sin ninguna de esas dos señales. El filtro de Estadísticas
  permite alternar entre esta clasificación y la de respuestas.

### Idempotencia del webhook

Meta puede reenviar eventos. Cada payload se guarda en `whatsapp_eventos` con una clave
única (hash de `entry + timestamp`); si la clave ya existe, el evento se descarta sin
volver a procesarlo.

### Configuración de Meta (paso a paso)

1. **Meta for Developers** → crea una App tipo **Business**.
2. Agrega el producto **WhatsApp** → se crea la **WhatsApp Business Account (WABA)**.
3. Añade y verifica el **número de teléfono** del negocio.
4. En **API Setup** copia a la tabla `configuracion` (con `PUT /api/configuracion` o `UPDATE`):
   - `Phone number ID` → `wa_phone_id`
   - `WhatsApp Business Account ID` → `wa_business_account_id`
   - `Access Token` → `wa_token` (usa uno de *System User* si necesitas que no
     caduque; los tokens temporales de API Setup expiran en 24 h)
5. Define tu propio **Verify Token** → `wa_verify_token`, y configura el **Webhook**
   de la App con `https://TU-DOMINIO/api/whatsapp/webhook` más ese token. Suscríbelo al
   menos a `messages`.
6. Pon `metodo_envio` en `api_oficial`.
7. Verifica que el `wa_token` sea de la **misma** WABA que pusiste en
   `wa_business_account_id` (un token válido de otra WABA falla con *"Object with ID
   '...' does not exist, cannot be loaded due to missing permissions"*).

> **Nota:** Meta exige que el webhook esté en una URL **HTTPS pública** y accesible desde
> internet. En local, usa un túnel (ngrok) para probarlo.

## Manejo de errores

Todo error del backend queda en consola (stderr), incluso el que no rompe el flujo:
`db.log_error(contexto, excepcion)` imprime `[ERROR] <contexto>: <excepción>` con su
traza completa, y se usa en cada `try/except` del sistema (envíos, webhook, correo,
integración con Meta, lectura de los JSON). El middleware HTTP de `main.py` además
captura y loguea cualquier excepción no controlada de una ruta antes de dejarla
propagar como error 500.

## Módulo de envío

- **Enviar mensajes solo se hace desde Mensajería** (`mensajeria.html`): se selecciona
  plantilla, base y cantidad antes de iniciar un envío manual, o se añade fecha y hora
  para programarlo. La cantidad puede definirse por porcentaje o por número exacto;
  no puede superar los pacientes libres de la base ni el cupo aplicable de Meta. Los
  que no pueden recibir (dados de baja, teléfono inválido, no autorizados en desarrollo)
  se listan como **rechazados** con el motivo, y el intento **igual queda en el Historial**
  (0 enviados, N inválidos) aunque no salga ningún mensaje.
- **Pacientes** (`pacientes.html`) **no envía
  mensajes** — ni siquiera admin/desarrollador: es solo para gestionar los registros (ver,
  buscar, filtrar, corregir estado/respuesta, uno por uno o en bloque con la selección — ver
  más abajo).
  `POST /api/notificaciones/enviar` sigue existiendo como el único punto de envío, usado
  por Mensajería, y comparte confirmación, job y progreso con el resto de ese flujo.
- **Producción**: si quien envía **no** tiene el permiso `envio_produccion`, se genera un
  correo de confirmación al supervisor con el **costo aproximado del envío en grande y rojo**
  (nº de mensajes × tarifa vigente de Meta para la categoría de la plantilla, **total
  redondeado hacia arriba**) y botones **Confirmar** y **Rechazar** (con comentario); el
  envío no arranca hasta que se confirma. El asunto y el cuerpo del correo indican el
  nombre y el correo de la cuenta que solicitó el envío. Un envío rechazado queda en el **Historial**
  con estado `rechazado` y el comentario del supervisor (ya no se borra). Con el permiso
  `envio_produccion` (implícito para admin/dev) el envío en producción sale directo, sin correo.
- **Desarrollo**: envío directo, restringido a los números de `numeros_prueba_dev`;
  sin el permiso `envio_produccion`, con `entorno = desarrollo` la petición nunca
  puede apuntar a producción aunque lo pida.
- **Motor intercambiable** (`metodo_envio`): `simulado` (no envía nada real, solo
  registra en consola) o `api_oficial` (WhatsApp Business Cloud API).
- **Cola en background**: cada envío corre como `BackgroundTask` de FastAPI con
  progreso en vivo (`GET /jobs/{id}`), y se puede pausar/reanudar/cancelar a mitad de
  camino.
- **Un solo envío a la vez por base de datos**: mientras hay un envío en curso
  (`en_proceso` o `pausado`) sobre una base, iniciar otro en **esa misma base** responde
  **409** — da igual quién lo haya iniciado; el mensaje indica quién lo inició, con qué
  plantilla y en qué base (`JOBS_LOCK` + `_envio_en_curso(ambiente)` en `main.py`). Producción
  y desarrollo son independientes: un envío activo en una no bloquea a la otra, y pueden
  correr en paralelo (cada uno respeta su propio `wa_rate_limit`). Se valida con el candado
  antes de encolar y también al confirmar por correo (ver `/api/notificaciones/enviar` y
  `/confirmar/{token}` más arriba).
- **Historial batch**: cada envío se registra en `envios` (una fila por "Iniciar envío"),
  y cada mensaje individual en `log_envios`.

## Usuarios, roles y permisos

Las cuentas viven en la tabla **`usuarios`** de `snw_base` (`usuario` = correo,
`clave_hash` SHA-256, `rol`, `permisos` como lista CSV, `correo_recuperacion`). No hay alta
pública: un administrador o desarrollador invita a una persona desde la pestaña **Usuarios** de
**`administracion.html`** («Crear usuario», solo el correo); le llega un correo con un enlace de
48 h a **`registro.html?token=`** donde elige su propia contraseña. La cuenta nace con rol
`usuario` y acceso básico (Mensajería, Historial y edición de plantillas). Un administrador o desarrollador ajusta nombre, permisos y rol desde
esa misma pestaña (una cuenta por vez, elegida en un desplegable).

**Roles:**

| Rol | Alcance | Puede gestionar |
|---|---|---|
| `usuario` | Solo lo que tenga en `permisos`, acotado a sus áreas asignadas. Solo crea/edita/elimina las plantillas que él creó y solo en sus áreas (sin globales) | — |
| `supervisor` | Como `usuario`, más aprobar plantillas y confirmar envíos en producción de sus áreas. Sin Estadísticas | — |
| `administrador` | Todo **salvo la página Configuración** | Permisos, activar/desactivar y rol (`usuario`/`supervisor`/`administrador`) en cuentas de rol `usuario` o `supervisor` (nunca toca otro admin/dev ni la suya) |
| `desarrollador` | Acceso total, **incluida Configuración** | Rol (cualquiera), permisos y activar/desactivar de cualquier cuenta salvo la suya |

**Activar / desactivar cuentas.** Además de editar permisos y rol, un admin/dev puede desmarcar
«Cuenta activa» en el panel de una cuenta (mismas reglas de quién puede gestionar a quién): la
cuenta desactivada no puede iniciar sesión (`403` en el login) y sus sesiones abiertas se cierran
al instante. Se reactiva marcando la casilla de nuevo.

**Activar el cambio de contraseña de una cuenta.** En el panel, sección «Contraseña»: si la
cuenta no tiene un correo de recuperación asignado, primero se le escribe uno y se presiona
«Guardar correo» (se valida que ese correo no esté ya en uso en otra cuenta). Con el correo ya
guardado, el botón «Activar cambio de contraseña» se habilita; al presionarlo pide confirmar el
correo mostrado (`confirm()` del navegador) antes de mandar el enlace, igual que «¿Olvidaste tu
contraseña?» pero disparado por un admin/dev en vez de por la propia cuenta.

**Envíos realizados.** Al final del panel de cada cuenta se lista lo que esa cuenta envió (tabla
`envios`, columna `usuario`, agregada cuando se sumó esta sección — los envíos anteriores quedan
con ese campo vacío y no aparecen aquí): fecha, plantilla, estado (Aprobado/Rechazado/Cancelado),
cantidad de pacientes y costo aproximado. Es de solo lectura y no depende de si la cuenta se puede
editar o no. Muestra **10 registros por página** con botones Anterior/Siguiente.

**Actividad (trazabilidad).** Debajo de «Envíos realizados», la sección «Actividad» muestra el
historial de acciones que un admin/dev hizo sobre esa cuenta (tabla `usuarios_auditoria`): quién
la invitó y cuándo se activó (con el correo de quien mandó la invitación), cada cambio de rol o
permisos (detalle tipo «Permisos: +tarifas_editar; -pacientes»), activar/desactivar el acceso,
asignar un correo de recuperación, activar un cambio de contraseña, y la eliminación de la cuenta
(el registro se conserva aunque la cuenta ya no exista). Igual que «Envíos realizados», es de solo
lectura y no depende de si la cuenta se puede editar. También pagina de a **10 registros**.

**Permisos** (campo `permisos` de la tabla; los roles privilegiados los tienen todos de forma implícita):

- Páginas: `mensajeria`, `historial`
- Acciones: `plantillas_editar`, `envio_produccion` (enviar en producción sin confirmación del supervisor), `call_center_registro` (ver el registro de respuestas de call center en el Historial)

**Pacientes**, **Estadísticas** y **Configuración** ya no son permisos asignables: las tres
pestañas viven dentro de `administracion.html`, exclusiva de los roles
`administrador`/`desarrollador` (`Configuración`, además, exclusiva de `desarrollador`). Los
permisos `pacientes`, `estadisticas` y `tarifas_editar` siguen existiendo en el backend
(endpoints `exigir(...)`) por compatibilidad, pero ya no se pueden otorgar desde la pestaña
Usuarios.

El backend revalida rol y permisos desde la tabla en **cada** petición, así que un cambio
surte efecto de inmediato (la página se recarga sola vía `GET /api/auth/me`) y una cuenta
eliminada pierde la sesión.

**Contraseñas.**

- Estando dentro, cada cuenta cambia la suya desde **«Mi cuenta»** (barra lateral),
  indicando la actual. Al cambiarla, si la cuenta tiene un correo de recuperación guardado,
  le llega un **aviso de confirmación** ahí mismo (`[SNW] Tu contraseña cambió`) — «Mi
  cuenta» ya no tiene un campo para editar ese correo; ver más abajo.
- Si la olvida, **«¿Olvidaste tu contraseña?»** en el login pide el correo; llega un enlace
  (`{url_base}/reset.html?token=…`) con un token de **2 horas** al correo de recuperación de
  la cuenta, desde `correo_emisor` (p. ej. `no-reply@somosprosalud.cl`). El enlace es de un
  solo uso y, al usarlo, cierra las sesiones de esa cuenta.
- Las cuentas registradas con correo lo tienen como correo de recuperación por defecto (se
  usa el mismo para el aviso de cambio de contraseña y para «Olvidé mi contraseña»).
  `admin` y `dev` entran con un nombre corto y **no tienen forma en la interfaz** de cargar
  un correo de recuperación (se quitó el campo de «Mi cuenta» a pedido); solo queda
  disponible el endpoint `PUT /api/auth/correo-recuperacion` para fijarlo a mano si hiciera
  falta. Sin correo, ninguna de las dos cosas (aviso de cambio, «Olvidé mi contraseña») les
  llega — la cuenta `dev` igual no se pierde: el sistema la garantiza en cada arranque.
- **Ningún rol (ni el desarrollador) puede cambiar la contraseña de otra cuenta.**
- Los tokens viven en la tabla `password_resets`.

**Cuenta de desarrollador:** el sistema garantiza `dev` / `dev123` (rol `desarrollador`)
en cada arranque si no existe. **Solo puede haber 4 cuentas `desarrollador`**; al intentar
promover una quinta el backend responde 409.

| Cuenta semilla | Contraseña | Rol |
|---|---|---|
| `admin` | `admin123` | administrador |
| `usuario` | `usuario123` | usuario (`mensajeria`, `historial`) |
| `dev` | `dev123` | desarrollador |

`sql/snw_base.sql` crea la tabla y siembra estas tres cuentas. Si al primer arranque
existía un `data/usuarios.json` antiguo, el backend lo importa a la tabla y lo archiva como
`usuarios.json.migrado`. Las sesiones activas siguen en `data/sesiones.json`
(token → `{usuario, rol, nombre, permisos, creada, actividad}`).

**Expiración por inactividad:** una sesión se cierra sola si pasan `sesion_expira_horas`
(config; por defecto **5**) sin que esa cuenta haga ninguna petición autenticada; cualquier
acción en la app renueva el plazo (`sesion_actual` en `main.py` actualiza `actividad` en cada
petición). `0` desactiva la expiración. Al arrancar, `_purgar_sesiones_expiradas()` descarta
las que ya estaban vencidas; las sesiones guardadas de antes de esta función (sin `actividad`)
no se cierran de golpe, el plazo arranca a contar desde ese momento. El respaldo en disco de
`actividad` se actualiza como mucho una vez por minuto por sesión (no en cada petición), para
no escribir el archivo constantemente durante un uso activo.

## Pantallas

Todas las pantallas comparten una **barra lateral** (sidebar) construida por `js/layout.js`:
marca el enlace activo, muestra solo las páginas permitidas para la cuenta (`window.snwPuede`)
y gestiona el cierre de
sesión. En escritorio se pliega a modo icono con el botón «‹‹» de la propia sidebar (la
preferencia se recuerda en `localStorage`); en pantallas angostas se convierte en un cajón
que abre la «hamburguesa» de la barra superior. El estilo usa **Bootstrap 5.3** (servido
desde `frontend/vendor/bootstrap/`) más `css/layout.css`.

**Tema claro / oscuro.** La paleta (tonos pastel en ambos modos) vive en `css/tema.css`
como variables CSS: `:root` para claro y `:root[data-tema="oscuro"]` para oscuro. `js/tema.js`
—cargado en el `<head>` de todas las páginas— aplica el tema guardado (`localStorage.snw_tema`)
antes del primer render para que no haya parpadeo. Se cambia con el botón **Modo oscuro /
claro** de la sidebar, o con el botón flotante en la portada y el login (páginas sin sidebar).

- **Landing** (`index.html`): sin sesión muestra la portada con el botón **Iniciar
  sesión**; con sesión, la sidebar y el contenido informativo. El menú
  lateral solo muestra las páginas permitidas para la cuenta.
- **Login** (`login.html`): acceso + «¿Olvidaste tu contraseña?» (pide el correo y envía un
  enlace de recuperación); ya no hay enlace de alta pública. **Registro** (`registro.html`):
  activar una cuenta invitada, con el correo precargado desde el token y solo pidiendo
  la contraseña. **Reset** (`reset.html`): pantalla de contraseña nueva a la que lleva
  el enlace del correo. Tras entrar, redirige a Pacientes (admin/dev) o Mensajería según los permisos.
- **«Mi cuenta»** (modal de la barra lateral, todas las páginas): cambiar la propia
  contraseña (pide la actual); si la cuenta tiene correo de recuperación, avisa el cambio
  ahí. No tiene campo para fijar ese correo (ver «Contraseñas» más arriba).
- **Mensajería y plantillas** (`mensajeria.html`, permiso `mensajeria`): tres pestañas,
  **Plantillas**, **Envío manual** y **Envíos programados**. El editor ofrece vista previa
  estilo WhatsApp (formato `*negrita*`/`_cursiva_`/`~tachado~`), campo y filtro de área,
  y aprobación interna. **Sincronizar**, **Actualizar estados** y **+ Nueva** solo se
  muestran en Plantillas. Sin `plantillas_editar`, el editor es de solo lectura; las
  plantillas ajenas también (salvo admin/dev). «Hello World» solo se puede enviar a la
  base de desarrollo. Los envíos manuales y programados comparten pasos para elegir
  plantilla, base y cantidad (porcentaje o número); el programado añade fecha y hora.
  La base muestra cuántos pacientes quedan libres y el cupo diario de Meta aparece en
  el paso de cantidad solo en producción con `api_oficial`. El costo aproximado se muestra solo a
  supervisor/admin/dev después de elegir cómo definir la cantidad, y cambia con la
  cantidad elegida. La lista de programados indica la base junto al nombre de la
  plantilla; **Ver preelegidos** aparece únicamente para envíos de desarrollo.
- **Cargar base de datos** (panel en `pacientes.html`, permiso `mensajeria`): cualquier cuenta sube un CSV
  (`nombre, apellido, telefono`, UTF-8, 5 MB) a una de sus áreas asignadas, con
  informe de insertados/duplicados/rechazados. Sin áreas se le indica pedir una.
- **Administración** (rol admin/desarrollador): ya no es una página con pestañas — son
  5 páginas propias (`usuarios.html`, `pacientes.html`, `areas.html`,
  `estadisticas.html`, `configuracion.html`; `administracion.html` solo redirige a
  Usuarios por compatibilidad). Al entrar, la **sidebar cambia** a los botones de
  navegación de administración (con separador «Administración»; Configuración solo
  para desarrollador, con redirección si no lo es). Estilos compartidos en
  `css/admin.css`. La página **Usuarios** (admin/desarrollador): arriba, **«Crear usuario»** manda
  la invitación por correo (solo el correo, sin permisos ni rol — esos se ajustan después de
  que la cuenta exista); abajo se elige una cuenta en un desplegable y el panel muestra sus
  datos: nombre, permisos, rol (solo el desarrollador), envíos realizados, actividad
  (trazabilidad) y eliminar. Máximo 4 desarrolladores. Aquí no se cambian contraseñas — cada
  cuenta usa «Mi cuenta» o «¿Olvidaste tu contraseña?».
- **Pacientes** (`pacientes.html`, para admin/dev y cuentas asignadas a un área): **selector único
  de base de datos** (desarrollo/producción/áreas con su tabla), tabla con estado
  editable en línea **solo para números de prueba**, columna **Error** (motivo del último fallo), columna **Respuesta** con
  la señal de WhatsApp (Respondió / Se dio de baja / Sin respuesta) y su fecha, filtros por
  estado/respuesta, **paginación** (10 a 100 por página) y **carga CSV** en el área elegida, y selección múltiple para editar **estado o respuesta de varios
  pacientes de prueba a la vez** (barra «Con los seleccionados», aparece al marcar alguno; pide
  confirmación con la cantidad antes de aplicar). En tablas de áreas hay además
  borrado por fila (×) y en bloque («Eliminar»). Un badge de «Se dio de baja» con
  &#128274; no se puede editar (ni uno por uno ni en bloque): esa baja la pidió el propio
  paciente por WhatsApp (ver «Baja explícita» en «Sistema de baja»). Aquí se ve **quiénes**
  respondieron o se dieron de baja — **no se envían mensajes desde esta página** (ver
  «Módulo de envío»). Los datos completos (incluidos nombre, teléfono y mensajes)
  solo los ve la cuenta que subió a cada paciente por CSV; para las demás cuentas se
  muestran iniciales y teléfono oculto, incluso si son admin/dev.
- **Historial** (`historial.html`): selector de base según los permisos de la cuenta.
  Al cambiar la base, la tabla muestra «Actualizando historial…» mientras llegan los
  nuevos resultados. «Ver detalle» presenta las iniciales de cada paciente. Solo la
  cuenta que lo importó por CSV puede usar «Ver mensajes» y consultar el hilo; el
  título del hilo mantiene las iniciales y el teléfono oculto (`+569 **** *123`).
- **Estadísticas** (`estadisticas.html`, exclusiva admin/dev, **solo cuenta
  envíos de producción**): mensajes enviados en el mes con su desglose; panel de pacientes
  con filtro **Respuesta / Interés** y comparación en barras. En Respuesta muestra cuántos
  no han respondido, respondieron o se dieron de baja; en Interés, cuántos están
  interesados, no interesados o sin clasificar. Un **gráfico de barras** de mensajes
  enviados es conmutable por día / mes / año y muestra los totales históricos. Con el
  permiso `tarifas_editar` (implícito en admin/dev) se ve además el panel
  **"Costos de mensajes de WhatsApp"**:
  tarifas vigentes de Meta para Chile por categoría, aviso cuando hay un cambio o una
  tarifa futura, descarga del CSV de Chile y el mismo gráfico de barras aplicado al costo
   estimado por día / mes / año (solo mensajes de plantilla facturables y mensajes de
   servicio con tarifa vigente; el resto del texto libre de la ventana de 24 h
   se excluye y se indica bajo el total).
  La página **Configuración** (`configuracion.html`, **solo rol desarrollador** —
  no aparece en la navegación de un administrador): edita **todas** las claves de la
  tabla `configuracion` por secciones (Aplicación, URL pública, Correo/SMTP, WhatsApp).
  Muestra el valor real (los secretos con botón de ojo), marca los campos modificados,
  guarda solo lo cambiado con una barra flotante y avisa si sales con cambios sin guardar.
  La sección WhatsApp incluye un campo calculado de solo lectura con la **URL del webhook**
  (URL base + ruta), con botón de copiar y enlace al panel de Webhooks de Meta.
