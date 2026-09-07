# SNW — Sistema de Notificaciones WhatsApp

Módulo web para gestionar y enviar notificaciones de WhatsApp a pacientes. Backend en
**Python (FastAPI)**, una única base de datos **MySQL/MariaDB** con tablas separadas para
desarrollo y producción, plantillas y usuarios en **archivos JSON**, e integración directa
con la **WhatsApp Business Cloud API** de Meta (v21.0).

## Stack

| Componente | Tecnología |
|---|---|
| Frontend | HTML5, CSS, JavaScript vanilla (sin build step) |
| Backend / API | Python + FastAPI + Uvicorn |
| Base de datos | MySQL / MariaDB (XAMPP, PyMySQL) — una sola base, `snw_base` |
| Plantillas | Archivo JSON (`data/plantillas.json`) |
| Usuarios / sesiones | Archivos JSON (`data/usuarios.json` con SHA-256, `data/sesiones.json`) |
| WhatsApp | WhatsApp Business Cloud API (Meta Graph API v21.0) o motor simulado |
| Correo | SMTP (Gmail u otro, configurable en `.env`) — confirmación de envíos en producción |

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
│   ├── css/                   Estilos (styles.css compartido, pacientes.css)
│   └── js/                    app.js (mensajería), pacientes.js, historial.js
├── data/
│   ├── plantillas.json        Plantillas de mensajes + metadata del template en Meta
│   ├── usuarios.json          Credenciales (admin / usuario), clave en SHA-256
│   └── sesiones.json          Tokens de sesión activos
├── sql/
│   └── snw_base.sql           Crea la base snw_base, sus 5 tablas y siembra los 2
│                                números autorizados (idempotente: IF NOT EXISTS / INSERT IGNORE)
├── backups/                   Volcados manuales (mysqldump) antes de operaciones destructivas
├── .env                       Configuración local (no versionado)
├── .env.example                Plantilla de configuración
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
3. Copiar `.env.example` como `.env` y completar (ver tabla abajo).
4. Iniciar:
   ```
   python -m uvicorn main:app --app-dir backend --host 127.0.0.1 --port 8000
   ```
5. Abrir http://localhost:8000

### Variables de entorno principales (`.env`)

| Variable | Uso |
|---|---|
| `SNW_ENTORNO` | `desarrollo` \| `produccion` — entorno activo por defecto del sistema |
| `SNW_URL_BASE` | URL pública para los enlaces de confirmación/rechazo del correo (vacío = `http://localhost:8000`) |
| `SNW_METODO_ENVIO` | `simulado` (no envía nada real) \| `api_oficial` (WhatsApp Cloud API) |
| `SNW_NUMEROS_PRUEBA_DEV` / `SNW_NUMEROS_PRUEBA_PROD` | Números autorizados por entorno, separados por coma |
| `SNW_INTERVALO_MS` | Pausa entre mensajes de un mismo envío |
| `SMTP_*`, `DIRECCION_CORREO_EMISOR`, `DIRECCION_CORREO_DESTINO` | Correo de confirmación de envíos en producción |
| `SNW_WA_TOKEN`, `SNW_WA_PHONE_ID`, `SNW_WA_BUSINESS_ACCOUNT_ID` | Credenciales de la WhatsApp Business Cloud API |
| `SNW_WA_VERIFY_TOKEN` | Token de verificación del webhook (lo defines tú) |
| `SNW_WA_TEMPLATE_LANG` | Idioma por defecto de los templates (código real de Meta, ej. `es`) |
| `DB_HOST`, `DB_PUERTO`, `DB_USUARIO`, `DB_CONTRASENA`, `DB_NOMBRE` | Conexión a la base única `snw_base` |

## Base de datos

**Una sola base MySQL, `snw_base`**, con 5 tablas (ver `sql/snw_base.sql`):

**`pacientes_dev` / `pacientes_prod`** — mismo esquema, una tabla por entorno

| Columna | Tipo | Uso |
|---|---|---|
| `id` | INT PK | Identificador |
| `nombre`, `apellido` | VARCHAR | Nombre del paciente |
| `telefono` | VARCHAR(20) | Formato `+569XXXXXXXXX` |
| `info_extra` | VARCHAR(255) | Dato libre para el comodín `{info_extra}` |
| `estado` | ENUM | `pendiente` / `enviado` / `error` |
| `whatsapp_opt_out` | TINYINT(1) | 1 si el paciente pidió no recibir más mensajes |
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
| `respuesta` | `pendiente` / `click` / `respondio` / `baja` |
| `whatsapp_message_id`, `estado_whatsapp` | ID del mensaje en Meta y su estado de entrega (`sent`/`delivered`/`read`/`failed`) |
| `descripcion_error` | Detalle del error (de Meta o del sistema) |

**`whatsapp_eventos`** — idempotencia del webhook: guarda un hash de cada payload recibido
(`entry + timestamp`) para no reprocesar un evento que Meta reenvíe.

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
| PUT | `/api/pacientes/{id}/respuesta?ambiente=` | Cambiar `respuesta` (`pendiente`/`click`/`respondio`/`baja`) |

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

### Configuración

| Método | Endpoint | Descripción |
|---|---|---|
| GET | `/api/configuracion?ambiente=` | Config activa (números autorizados, método de envío, etc.) |
| PUT | `/api/configuracion` | (solo admin) Actualiza `.env` en caliente, sin reiniciar |

### Webhook de WhatsApp (Meta)

| Método | Endpoint | Descripción |
|---|---|---|
| GET | `/api/whatsapp/webhook` | Verificación inicial (`hub.mode`, `hub.verify_token`, `hub.challenge`) |
| POST | `/api/whatsapp/webhook` | Recepción de eventos: mensajes entrantes y cambios de estado |

## Integración con WhatsApp Business Cloud API

La capa de integración vive en `backend/whatsapp_service.py` (cliente Graph API +
`WhatsAppService` + `WebhookHandler`) y `backend/whatsapp_webhook.py` (router). Está
separada de la lógica de negocio de mensajería, y usa **Graph API v21.0**.

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

### Estados de WhatsApp

Al enviar se guarda el `whatsapp_message_id` en `log_envios`. Los cambios de estado
(`sent` → `delivered` → `read` → `failed`) llegan por webhook y actualizan
`estado_whatsapp` (y, si es `failed`, también `estado_envio` y `descripcion_error` con el
motivo que reporta Meta).

### Respuestas entrantes

Cuando un cliente responde, el webhook busca al paciente por teléfono, marca su
`estado = 'enviado'`, e inserta una fila en `log_envios` con `respuesta = 'respondio'`.

### Sistema de baja (opt-out)

Si el mensaje entrante coincide con una palabra de baja (`no`, `stop`, `baja`,
`cancelar`, `darme de baja`...), el webhook marca `whatsapp_opt_out = 1` en el paciente y
`respuesta = 'baja'` en su último `log_envios`. Un paciente en esa condición queda
**excluido de cualquier envío futuro** (se filtra en `POST /api/notificaciones/enviar` y
se deshabilita su checkbox en Pacientes). La detección es por contenido del mensaje; Meta
no entrega un evento explícito de baja o de bloqueo de cuenta.

### Idempotencia del webhook

Meta puede reenviar eventos. Cada payload se guarda en `whatsapp_eventos` con una clave
única (hash de `entry + timestamp`); si la clave ya existe, el evento se descarta sin
volver a procesarlo.

### Configuración de Meta (paso a paso)

1. **Meta for Developers** → crea una App tipo **Business**.
2. Agrega el producto **WhatsApp** → se crea la **WhatsApp Business Account (WABA)**.
3. Añade y verifica el **número de teléfono** del negocio.
4. En **API Setup** copia:
   - `Phone number ID` → `SNW_WA_PHONE_ID`
   - `WhatsApp Business Account ID` → `SNW_WA_BUSINESS_ACCOUNT_ID`
   - `Access Token` → `SNW_WA_TOKEN` (usa uno de *System User* si necesitas que no
     caduque; los tokens temporales de API Setup expiran en 24 h)
5. Define tu propio **Verify Token** → `SNW_WA_VERIFY_TOKEN`, y configura el **Webhook**
   de la App con `https://TU-DOMINIO/api/whatsapp/webhook` más ese token. Suscríbelo al
   menos a `messages`.
6. Pon `SNW_METODO_ENVIO=api_oficial` en `.env`.
7. Verifica que el `access token` sea de la **misma** WABA que pusiste en
   `SNW_WA_BUSINESS_ACCOUNT_ID` (un token válido de otra WABA falla con *"Object with ID
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
  checkboxes/filtros → elige plantilla → "Iniciar envío" (`pacientes: [ids]`).
- Ambos flujos terminan en el mismo `POST /api/notificaciones/enviar` y comparten
  confirmación, job y progreso.
- **Producción**: si quien envía **no** es administrador, se genera un correo de
  confirmación al supervisor con botones **Confirmar** y **Rechazar** (con comentario);
  el envío no arranca hasta que se confirma. Un administrador en producción envía
  directo, sin correo.
- **Desarrollo**: envío directo, restringido a los números en `SNW_NUMEROS_PRUEBA_DEV`;
  un usuario no-admin con `SNW_ENTORNO=desarrollo` global nunca puede apuntar a
  producción, aunque lo pida en la petición.
- **Motor intercambiable** (`SNW_METODO_ENVIO`): `simulado` (no envía nada real, solo
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
| `usuario` | `usuario123` | usuario | Mensajería (crear/editar plantillas, ver estado y sincronizar con Meta) e Historial; sin acceso a Pacientes ni a Configuración; en producción sus envíos requieren confirmación del supervisor |

Las credenciales viven en `data/usuarios.json` (clave en SHA-256); las sesiones activas
en `data/sesiones.json` (token → `{rol, nombre}`, sin expiración automática).

## Pantallas

- **Landing** (`index.html`): navegación según rol (Pacientes solo visible si eres admin).
- **Login** (`login.html`): formulario de acceso, redirige a Pacientes (admin) o
  Mensajería (usuario) según el rol.
- **Mensajería** (`mensajeria.html`): editor de plantillas con vista previa estilo
  WhatsApp (formato `*negrita*`/`_cursiva_`/`~tachado~`), nombre y template de Meta
  permanentes, botón **Sincronizar** con Meta, y envío directo a todos los pendientes.
- **Pacientes** (`pacientes.html`, solo admin): tabla con estado editable en línea,
  columna **Error** (motivo del último fallo de envío), filtros por estado/respuesta,
  selección múltiple y envío masivo integrado.
- **Historial** (`historial.html`): envíos de ambas bases (o filtrado por una), detalle
  individual por paciente con estado, respuesta y error de cada mensaje.
