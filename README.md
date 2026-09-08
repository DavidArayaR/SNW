# SNW — Sistema de Notificaciones WhatsApp

Módulo web para gestionar y enviar notificaciones de WhatsApp a pacientes. Backend en
**Python (FastAPI)**, una única base de datos **MySQL/MariaDB** con tablas separadas para
desarrollo y producción, plantillas y usuarios en **archivos JSON**, e integración directa
con la **WhatsApp Business Cloud API** de Meta (v26.0).

## Stack

| Componente | Tecnología |
|---|---|
| Frontend | HTML5, CSS, JavaScript vanilla (sin build step) |
| Backend / API | Python + FastAPI + Uvicorn |
| Base de datos | MySQL / MariaDB (XAMPP, PyMySQL) — una sola base, `snw_base` |
| Plantillas | Archivo JSON (`data/plantillas.json`) |
| Usuarios / sesiones | Archivos JSON (`data/usuarios.json` con SHA-256, `data/sesiones.json`) |
| WhatsApp | WhatsApp Business Cloud API (Meta Graph API v26.0) o motor simulado |
| Correo | SMTP (configurable en la tabla `configuracion`) — confirmación de envíos en producción |
| Configuración | Tabla `configuracion` en MySQL (todo salvo credenciales de BD, que van en `.env`) |

## Estructura

```
snw/
├── INICIAR_SNW.bat          Arranque en un clic (Windows): valida MySQL/deps, inicializa
│                             la BD solo la primera vez, abre el navegador y levanta uvicorn
├── iniciar_snw.sh           Mismo arranque para Linux/macOS (chmod +x la primera vez)
├── backend/
│   ├── main.py               API FastAPI: rutas, jobs de envío en background, confirmación
│   │                          por correo, plantillas, pacientes, historial, configuración
│   ├── db.py                 Conexión MySQL (PyMySQL), entorno dev/prod, columnas dinámicas,
│   │                          log_error() (todo error queda en consola)
│   ├── whatsapp_service.py   Cliente Graph API + WhatsAppService (envío, templates,
│   │                          webhook handler) — capa de integración con Meta
│   ├── whatsapp_webhook.py   Router del webhook (verificación GET + recepción POST)
│   └── motor_envio.py        Motor de envío intercambiable: simulado | api_oficial
├── frontend/
│   ├── index.html             Landing con navegación por rol
│   ├── login.html             Inicio de sesión
│   ├── mensajeria.html        Editor de plantillas, vista previa estilo WhatsApp, envío
│   ├── pacientes.html         Base de datos de pacientes + envío masivo (solo admin)
│   ├── historial.html         Historial de envíos (batch + detalle por paciente)
│   ├── estadisticas.html      Contador mensual de mensajes, desgloses y costos WhatsApp (admin)
│   ├── configuracion.html     Editor de todos los ajustes por secciones (solo admin)
│   ├── css/                   styles.css (compartido), layout.css (sidebar), pacientes.css, estadisticas.css, configuracion.css
│   ├── js/                    layout.js (sidebar/sesión, común), app.js, pacientes.js, historial.js, estadisticas.js, configuracion.js
│   └── vendor/bootstrap/     Bootstrap 5.3.3 (CSS + bundle JS) servido localmente
├── data/
│   ├── plantillas.json        Plantillas de mensajes + metadata del template en Meta
│   ├── usuarios.json          Credenciales (admin / usuario), clave en SHA-256
│   └── sesiones.json          Tokens de sesión activos
├── sql/
│   └── snw_base.sql           Crea la base snw_base, sus 7 tablas y siembra los 2
│                                números autorizados (idempotente: IF NOT EXISTS / INSERT IGNORE)
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
2. **Solo la primera vez** (si `snw_base.pacientes_prod` todavía no existe) cargan
   `sql/snw_base.sql` para crear la base, las tablas y los 2 números autorizados. En
   arranques posteriores omiten este paso.
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
siembra en el primer arranque (tomando lo que hubiera en un `.env` antiguo). Se edita desde
la página **Configuración** (`configuracion.html`, solo admin) — que muestra TODAS las claves
por secciones (Aplicación, URL pública, Correo/SMTP, WhatsApp) — o con `UPDATE configuracion`
(requiere reiniciar por la caché). Los cambios de la página se aplican sin reiniciar (salvo
el `entorno` activo, que las demás pantallas leen al cargar).

| Grupo | Claves |
|---|---|
| App / envío | `entorno` (`desarrollo`/`produccion`), `metodo_envio` (`simulado`/`api_oficial`), `numeros_prueba_dev`, `numeros_prueba_prod`, `intervalo_ms`, `url_base` |
| Correo | `smtp_host`, `smtp_port`, `smtp_user`, `smtp_pass`, `smtp_tls`, `correo_emisor`, `correo_destino` |
| WhatsApp / Meta | `wa_token`, `wa_phone_id`, `wa_business_account_id`, `wa_verify_token`, `wa_template_nombre`, `wa_template_lang`, `wa_webhook_path`, `wa_graph_version` (por defecto `v26.0`), `wa_moneda` (moneda de facturación de la cuenta, se autodetecta desde Meta al actualizar tarifas — por defecto `USD`) |

## Base de datos

**Una sola base MySQL, `snw_base`**, con 7 tablas (ver `sql/snw_base.sql`):

**`pacientes_dev` / `pacientes_prod`** — mismo esquema, una tabla por entorno

| Columna | Tipo | Uso |
|---|---|---|
| `id` | INT PK | Identificador |
| `nombre`, `apellido` | VARCHAR | Nombre del paciente |
| `telefono` | VARCHAR(20) | Formato `+569XXXXXXXXX` |
| `info_extra` | VARCHAR(255) | Dato libre para el comodín `{info_extra}` |
| `estado` | ENUM | `pendiente` / `enviado` / `error` |
| `whatsapp_opt_out` | TINYINT(1) | 1 si el paciente pidió no recibir más mensajes |
| `respuesta_manual` | VARCHAR(12) | Corrección manual de la respuesta (NULL = sin corrección); gana sobre la señal automática |
| `fecha_actualizacion` | DATETIME | Última actualización |

**`envios`** — un registro por cada "Iniciar envío" (batch-level, sin nombres de pacientes)

| Columna | Uso |
|---|---|
| `base_datos` | `pacientes_dev` o `pacientes_prod` |
| `plantilla_clave`, `plantilla_nombre` | Plantilla usada |
| `total_pacientes`, `enviados`, `fallidos`, `invalidos` | Contadores del batch |
| `estado` | `completado` / `cancelado` |
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
Se usa en la sección **Costos** de Estadísticas (solo admin) para estimar el gasto por
día / mes / año aplicando a cada mensaje enviado la tarifa vigente en su fecha según la
categoría de su plantilla.

Las tablas de pacientes comparten `log_envios`, así que el backend siempre ubica el
"último log" de un paciente con un `LEFT JOIN` correlacionado por `paciente_id`.

## Plantillas de mensajes

Viven en `data/plantillas.json` (no en MySQL). Cada plantilla tiene comodines
`{nombre}`, `{apellido}`, `{info_extra}` que se reemplazan al enviar, y la vista previa en
**Mensajería** interpreta además el formato de WhatsApp: `*negrita*`, `_cursiva_`,
`~tachado~` y `` ```monoespaciado``` ``.

Reglas del editor:

- **El nombre de la plantilla es permanente**: una vez creada no se puede editar (solo
  eliminarla y crear otra). Evita romper la `clave` interna, que se deriva del nombre.
- **El nombre del template de Meta se genera solo**: es siempre el `slug` del nombre de
  la plantilla (minúsculas, números y `_`), el campo no es editable a mano.
- Idioma y categoría del template quedan bloqueados una vez que la plantilla ya tiene un
  template registrado en Meta (no se pueden cambiar sin crear uno nuevo).

## API

### Autenticación

| Método | Endpoint | Descripción |
|---|---|---|
| POST | `/api/auth/login` | `{usuario, clave}` → `{token, rol, nombre}` |
| POST | `/api/auth/logout` | Invalida el token actual |

### Pacientes (solo admin)

| Método | Endpoint | Descripción |
|---|---|---|
| GET | `/api/pacientes?q=&ambiente=` | Lista con respuesta y error del último `log_envios` |
| PUT | `/api/pacientes/{id}?ambiente=` | Cambiar `estado` (`pendiente`/`enviado`/`error`) |
| PUT | `/api/pacientes/{id}/respuesta?ambiente=` | Ajuste manual de la respuesta (`pendiente`/`respondio`/`baja`); `baja` activa el opt-out |

### Plantillas

| Método | Endpoint | Descripción |
|---|---|---|
| GET | `/api/plantillas` | Lista de plantillas |
| POST | `/api/plantillas` | Crear `{nombre, texto, whatsapp_template_lang, whatsapp_template_categoria}` |
| PUT | `/api/plantillas/{id}` | Actualizar `{nombre, texto, ...}` — rechaza (400) si `nombre` cambió |
| DELETE | `/api/plantillas/{id}` | Eliminar |
| GET | `/api/plantillas/{id}/estado-meta` | Consulta en Meta el estado real de un template |
| POST | `/api/plantillas/estado-meta/actualizar` | Refresca el estado de todas las plantillas con template |
| POST | `/api/plantillas/sincronizar-meta` | Lee los templates que existen en Meta: actualiza estado/id de los conocidos e **importa como plantilla nueva** los que falten (no crea/edita nada en Meta, solo lee) |

### Envíos

| Método | Endpoint | Descripción |
|---|---|---|
| POST | `/api/notificaciones/enviar` | Inicia el envío `{pacientes: [ids] \| null, plantilla_id, ambiente}`. `pacientes: null` = todos los elegibles (usado desde Mensajería) |
| POST | `/api/notificaciones/destinatarios` | Cuenta pacientes totales/pendientes de un ambiente |
| GET | `/api/notificaciones/jobs/{job_id}` | Progreso en vivo del envío en curso |
| POST | `/api/notificaciones/jobs/{job_id}/pausa` \| `/reanudar` \| `/cancelar` | Control del job en curso |
| POST | `/api/notificaciones/prueba-wa` | (solo admin) Envía un mensaje de prueba real vía API oficial |

### Confirmación / rechazo por correo (producción)

| Método | Endpoint | Descripción |
|---|---|---|
| GET | `/api/notificaciones/solicitud/{token}` | Estado de una solicitud pendiente (usado por polling del frontend) |
| GET / POST | `/api/notificaciones/confirmar/{token}` | Confirmar envío (link del correo) |
| GET / POST | `/api/notificaciones/rechazar/{token}` | Formulario y envío del rechazo, con comentario opcional |

### Historial

| Método | Endpoint | Descripción |
|---|---|---|
| GET | `/api/notificaciones/historial?ambiente=todos` | Envíos batch (`ambiente=todos` junta ambas bases) |
| GET | `/api/notificaciones/historial/{id}/detalle?ambiente=` | Pacientes individuales de un envío |
| PUT | `/api/notificaciones/historial/{id}/respuesta?ambiente=` | Corregir la respuesta de un registro |

### Estadísticas

| Método | Endpoint | Descripción |
|---|---|---|
| GET | `/api/estadisticas` | Resumen para Estadísticas (**solo envíos de producción**): mensajes `enviado` del mes, desglose, totales, `pacientes_por_respuesta` (cuántos pacientes de producción respondieron / se dieron de baja / no han respondido) y `webhook` (cuándo llegó el último evento de Meta — sirve para detectar que el webhook dejó de recibir) |
| GET | `/api/estadisticas/envios?granularidad=dia\|mes\|anio` | Mensajes enviados de producción agrupados por periodo, para el gráfico de barras (día = últimos 30, mes = últimos 12, año = últimos 6) |
| GET | `/api/estadisticas/costos?granularidad=dia\|mes\|anio` | (solo admin) Costo estimado agrupado por periodo. **Solo cuenta los mensajes de plantilla iniciados por la empresa** (la plantilla tiene un template Meta configurado y categoría Marketing / Utility / Authentication), aplicando la tarifa de `tarifas_whatsapp` vigente en su fecha. Los envíos de texto libre (respuestas dentro de la ventana de 24 h) son gratuitos y se devuelven aparte en `excluidos` |
| GET | `/api/tarifas` | (solo admin) Tarifas guardadas: `vigente`, `proxima` (tarifa futura ya publicada por Meta), `usd_vigente`, `historial`, moneda de la cuenta y fecha de la última descarga |
| POST | `/api/tarifas/actualizar` | (solo admin) Descarga la página de precios de Meta y sus CSV, guarda los rate cards nuevos de Chile (`INSERT IGNORE` por hash), autodetecta la moneda de facturación (`GET {waba}?fields=currency` → `wa_moneda`) y devuelve si hubo cambio |
| GET | `/api/tarifas/chile.csv` | (solo admin) Descarga el CSV original del rate card de Chile (prefiere la moneda de la cuenta, si no USD) |

### Configuración

| Método | Endpoint | Descripción |
|---|---|---|
| GET | `/api/configuracion?ambiente=` | Vista **segura** de la config para el resto de páginas (no devuelve el token de Meta ni la clave SMTP en claro) |
| PUT | `/api/configuracion` | (solo admin) Guarda claves sueltas de `configuracion` sin reiniciar |
| GET | `/api/configuracion/todo` | (solo admin) TODAS las claves con su **valor real** (incluye secretos) + metadata de secciones, para la página Configuración |
| PUT | `/api/configuracion/todo` | (solo admin) `{cambios: {clave: valor, …}}` — valida clave conocida, enums (`entorno`, `metodo_envio`) y enteros (`intervalo_ms`, `smtp_port`); persiste con `config_set` |

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
  todavía una plantilla local asociada (el texto se extrae del componente `BODY`; los
  `{{1}}`, `{{2}}` quedan tal cual porque no se sabe a qué comodín corresponden).

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

**Ajuste manual:** en Pacientes, hacer click en el badge de la columna **Respuesta** abre
un selector para marcar a mano `respondió` / `se dio de baja` / etc. — útil si el webhook
no llegó o el paciente avisó por otro canal. `PUT /api/pacientes/{id}/respuesta` (solo
admin) guarda la corrección en la columna `respuesta_manual` de la tabla de pacientes;
`baja` activa el opt-out y las demás lo revierten. Esa corrección **gana** sobre la señal
automática y se limpia sola si más tarde llega una respuesta real por el webhook.

### Quién respondió / se dio de baja

La "respuesta efectiva" de un paciente se calcula con esta prioridad:

1. **opt-out activo** → `baja`
2. **corrección manual** (`respuesta_manual`), si existe
3. **señal automática 'pegajosa'**: la más fuerte que haya tenido alguna vez
   (`baja` > `respondió`); un envío posterior no la borra
4. `pendiente`

Se calcula igual en:

- **`GET /api/pacientes`** → campo `respuesta` (columna y filtros en la página Pacientes: *quiénes*).
- **`GET /api/estadisticas`** → `pacientes_por_respuesta` con los totales por estado (panel
  "Respuestas de pacientes" en Estadísticas: *cuántos*). Solo cuenta pacientes de producción
  con `estado = 'enviado'` — los que aún están `pendiente` o `error` no entran.

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

- **Mensajería** (`mensajeria.html`): al editar una plantilla existente aparece
  "Enviar mensaje" → envía esa plantilla a **todos los pacientes elegibles** del ambiente
  elegido (`pacientes: null`).
- **Pacientes** (`pacientes.html`, solo admin): selecciona pacientes puntuales con
  checkboxes/filtros → elige plantilla → "Iniciar envío" (`pacientes: [ids]`). Los
  seleccionados que no pueden recibir (dados de baja, teléfono inválido, no autorizados
  en dev) se listan como **rechazados** con el motivo, y el intento **igual queda en el
  Historial** (0 enviados, N inválidos) aunque no salga ningún mensaje.
- Ambos flujos terminan en el mismo `POST /api/notificaciones/enviar` y comparten
  confirmación, job y progreso.
- **Producción**: si quien envía **no** es administrador, se genera un correo de
  confirmación al supervisor con botones **Confirmar** y **Rechazar** (con comentario);
  el envío no arranca hasta que se confirma. Un administrador en producción envía
  directo, sin correo.
- **Desarrollo**: envío directo, restringido a los números de `numeros_prueba_dev`;
  un usuario no-admin con `entorno = desarrollo` nunca puede apuntar a producción,
  aunque lo pida en la petición.
- **Motor intercambiable** (`metodo_envio`): `simulado` (no envía nada real, solo
  registra en consola) o `api_oficial` (WhatsApp Business Cloud API).
- **Cola en background**: cada envío corre como `BackgroundTask` de FastAPI con
  progreso en vivo (`GET /jobs/{id}`), y se puede pausar/reanudar/cancelar a mitad de
  camino.
- **Historial batch**: cada envío se registra en `envios` (una fila por "Iniciar envío"),
  y cada mensaje individual en `log_envios`.

## Usuarios

| Usuario | Contraseña | Rol | Acceso |
|---|---|---|---|
| `admin` | `admin123` | administrador | Todo: Pacientes, Mensajería, Historial, configuración, links de prueba en el correo de confirmación, envío directo en producción |
| `usuario` | `usuario123` | usuario | Mensajería (crear/editar plantillas, ver estado y sincronizar con Meta), Historial y Estadísticas; sin acceso a Pacientes ni a Configuración; en producción sus envíos requieren confirmación del supervisor |

Las credenciales viven en `data/usuarios.json` (clave en SHA-256); las sesiones activas
en `data/sesiones.json` (token → `{rol, nombre}`, sin expiración automática).

## Pantallas

Todas las pantallas comparten una **barra lateral** (sidebar) construida por `js/layout.js`:
marca el enlace activo, oculta *Base de datos* si no eres admin y gestiona el cierre de
sesión. En escritorio se pliega a modo icono con el botón «‹‹» de la propia sidebar (la
preferencia se recuerda en `localStorage`); en pantallas angostas se convierte en un cajón
que abre la «hamburguesa» de la barra superior. El estilo usa **Bootstrap 5.3** (servido
desde `frontend/vendor/bootstrap/`) más `css/layout.css`.

- **Landing** (`index.html`): sin sesión muestra la portada; con sesión, la sidebar y el
  contenido informativo (Pacientes solo visible si eres admin).
- **Login** (`login.html`): formulario de acceso, redirige a Pacientes (admin) o
  Mensajería (usuario) según el rol.
- **Mensajería** (`mensajeria.html`): editor de plantillas con vista previa estilo
  WhatsApp (formato `*negrita*`/`_cursiva_`/`~tachado~`), nombre y template de Meta
  permanentes, botón **Sincronizar** con Meta, y envío directo a todos los pendientes.
- **Pacientes** (`pacientes.html`, solo admin): tabla con estado editable en línea,
  columna **Error** (motivo del último fallo), columna **Respuesta** con la señal de
  WhatsApp (Respondió / Se dio de baja / Sin respuesta) y su fecha, filtros por
  estado/respuesta, selección múltiple y envío masivo. Aquí se ve **quiénes** respondieron
  o se dieron de baja.
- **Historial** (`historial.html`): envíos de ambas bases (o filtrado por una), detalle
  individual por paciente con estado, respuesta y error de cada mensaje.
- **Estadísticas** (`estadisticas.html`, **solo cuenta envíos de producción**): mensajes
  enviados en el mes con su desglose; panel **"Respuestas de pacientes por WhatsApp"** con
  botones de filtro (Todos / No han respondido / Respondieron / Se dieron
  de baja) sobre una **comparación en barras** de los pacientes de producción por estado;
  un **gráfico de barras** de mensajes enviados conmutable por día / mes / año y los
  totales históricos. Solo el admin ve además el panel **"Costos de mensajes de WhatsApp"**:
  tarifas vigentes de Meta para Chile por categoría, aviso cuando hay un cambio o una
  tarifa futura, descarga del CSV de Chile y el mismo gráfico de barras aplicado al costo
  estimado por día / mes / año (solo mensajes de plantilla facturables; los de texto libre
  de la ventana de 24 h se excluyen y se indican bajo el total).
- **Configuración** (`configuracion.html`, solo admin): edita **todas** las claves de la
  tabla `configuracion` por secciones (Aplicación, URL pública, Correo/SMTP, WhatsApp).
  Muestra el valor real (los secretos con botón de ojo), marca los campos modificados,
  guarda solo lo cambiado con una barra flotante y avisa si sales con cambios sin guardar.
  La sección WhatsApp incluye un campo calculado de solo lectura con la **URL del webhook**
  (URL base + ruta), con botón de copiar y enlace al panel de Webhooks de Meta.
