# SNW — Sistema de Notificaciones WhatsApp

Módulo web integrado para gestión y envío de notificaciones WhatsApp a pacientes. Backend en **Python (FastAPI)**, pacientes en **MySQL** (dual DB: desarrollo y producción), plantillas en **archivo JSON**.

## Stack

| Componente | Tecnología |
|---|---|
| Frontend | HTML5, CSS, JavaScript vanilla |
| Backend / API | Python + FastAPI + Uvicorn |
| Base de datos | MySQL / MariaDB (XAMPP, PyMySQL) |
| Plantillas | Archivo JSON (`data/plantillas.json`) |
| Usuarios | Archivo JSON (`data/usuarios.json`, SHA-256) |
| Correo | SMTP (Gmail u otro, configurable en `.env`) |

## Estructura

```
snw/
├── backend/
│   ├── main.py              API FastAPI (endpoints + init SQL + archivos estáticos)
│   ├── db.py                Conexión MySQL (PyMySQL)
│   └── motor_envio.py       Motor de envío intercambiable
├── frontend/
│   ├── index.html           Landing con navegación por roles
│   ├── login.html           Inicio de sesión
│   ├── pacientes.html       Gestión de pacientes + envío integrado (solo admin)
│   ├── mensajeria.html      Editor de plantillas + confirmación de envío
│   ├── historial.html       Historial de envíos (batch-level)
│   ├── css/                 Estilos
│   └── js/                  Lógica de cada página
├── data/
│   ├── plantillas.json      Plantillas de mensajes
│   ├── usuarios.json        Credenciales (admin / usuario)
│   └── sesiones.json        Tokens de sesión
├── sql/
│   ├── snw_pacientes.sql        Init DB desarrollo (tablas + 100 pacientes)
│   └── snw_pacientes_prod.sql   Init DB producción (tablas + 100 pacientes)
├── .env                     Configuración local
├── .env.example             Plantilla de configuración
├── requirements.txt         Dependencias Python
└── README.md
```

## Puesta en marcha

1. Instalar dependencias:
   ```
   pip install -r requirements.txt
   ```
2. Asegurar MySQL activo (XAMPP).
3. Copiar `.env.example` como `.env` y completar:
   - `SNW_ENTORNO`: `desarrollo` o `produccion`
   - `SNW_METODO_ENVIO`: `simulado`, `whatsapp_web` o `api_oficial`
   - `SNW_NUMEROS_PRUEBA_DEV`: números autorizados en desarrollo (separados por coma)
   - `SNW_INTERVALO_MS`: pausa entre mensajes
   - `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `SMTP_TLS`: correo de confirmación
   - `DIRECCION_CORREO_EMISOR`, `DIRECCION_CORREO_DESTINO`: destinatarios del correo
   - `DB_DEV_*` / `DB_PROD_*`: credenciales de ambas bases
4. Iniciar:
   ```
   python -m uvicorn main:app --app-dir backend --host 127.0.0.1 --port 8000
   ```
   El sistema ejecuta automáticamente los scripts SQL al iniciar, creando las bases, tablas y 100 pacientes de prueba en cada una.
5. Abrir http://localhost:8000

## Base de datos

Dos bases idénticas (solo el nombre cambia):

- `snw_pacientes` — desarrollo
- `snw_pacientes_prod` — producción

Se crean automáticamente al iniciar el backend. Los scripts SQL en `sql/` usan `IF NOT EXISTS` e `INSERT IGNORE`, por lo que son seguros en cada reinicio.

### Tablas

**`pacientes`** — Pacientes de la clínica

| Columna | Tipo | Uso |
|---|---|---|
| `id` | INT PK | Identificador |
| `nombre` | VARCHAR(150) | Nombre |
| `apellido` | VARCHAR(150) | Apellido |
| `telefono` | VARCHAR(20) | Teléfono formato `+569XXXXXXXXX` |
| `info_extra` | VARCHAR(255) | Dato extra para plantillas |
| `estado` | ENUM | `pendiente` / `enviado` / `error` |
| `fecha_actualizacion` | DATETIME | Última actualización |

**`envios`** — Registro de cada "Iniciar envío" (batch-level)

| Columna | Tipo | Uso |
|---|---|---|
| `id` | INT PK | ID del envío |
| `base_datos` | VARCHAR(50) | `snw_pacientes` o `snw_pacientes_prod` |
| `plantilla_clave` | VARCHAR(50) | Clave de plantilla usada |
| `plantilla_nombre` | VARCHAR(100) | Nombre de la plantilla |
| `total_pacientes` | INT | Total destinatarios |
| `enviados` | INT | Mensajes enviados OK |
| `fallidos` | INT | Mensajes con error |
| `invalidos` | INT | Teléfonos inválidos |
| `fecha_hora` | DATETIME | Fecha del envío |

**`log_envios`** — Mensaje individual por paciente

| Columna | Tipo | Uso |
|---|---|---|
| `id` | INT PK | ID del registro |
| `envio_id` | INT FK | Enlaza al batch (`envios.id`) |
| `paciente_id` | INT | ID del paciente |
| `nombre_paciente` | VARCHAR(150) | Nombre al momento del envío |
| `numero_telefono` | VARCHAR(20) | Teléfono usado |
| `mensaje` | TEXT | Mensaje enviado |
| `plantilla_clave` | VARCHAR(50) | Plantilla usada |
| `estado_envio` | ENUM | `enviado` / `error` / `numero_invalido` |
| `respuesta` | ENUM | `pendiente` / `click` / `respondio` / `baja` |
| `descripcion_error` | VARCHAR(255) | Detalle del error |
| `fecha_hora` | DATETIME | Fecha del registro |

## API

### Autenticación

| Método | Endpoint | Descripción |
|---|---|---|
| POST | `/api/auth/login` | Iniciar sesión `{usuario, clave}` → token |
| POST | `/api/auth/logout` | Cerrar sesión |

### Pacientes (solo admin)

| Método | Endpoint | Descripción |
|---|---|---|
| GET | `/api/pacientes?q=&ambiente=` | Lista con respuesta del último log |
| PUT | `/api/pacientes/{id}?ambiente=` | Cambiar estado del paciente |
| PUT | `/api/pacientes/{id}/respuesta?ambiente=` | Cambiar respuesta (click/respondio/baja) |

### Plantillas

| Método | Endpoint | Descripción |
|---|---|---|
| GET | `/api/plantillas` | Lista de plantillas |
| POST | `/api/plantillas` | Crear `{nombre, texto}` |
| PUT | `/api/plantillas/{id}` | Actualizar `{nombre, texto}` |
| DELETE | `/api/plantillas/{id}` | Eliminar |

### Envíos

| Método | Endpoint | Descripción |
|---|---|---|
| POST | `/api/notificaciones/enviar` | Iniciar envío `{pacientes: [ids], plantilla_id, ambiente}` |
| GET | `/api/notificaciones/jobs/{job_id}` | Progreso en vivo |
| GET | `/api/notificaciones/solicitud/{token}` | Estado de solicitud pendiente |

### Confirmación / Rechazo (correo)

| Método | Endpoint | Descripción |
|---|---|---|
| GET | `/api/notificaciones/confirmar/{token}` | Confirmar envío (desde email) |
| GET | `/api/notificaciones/rechazar/{token}` | Rechazar envío (desde email) |

### Historial

| Método | Endpoint | Descripción |
|---|---|---|
| GET | `/api/notificaciones/historial?ambiente=todos` | Envíos batch de ambas DBs |
| GET | `/api/notificaciones/historial/{id}/detalle?ambiente=` | Pacientes individuales de un envío |
| PUT | `/api/notificaciones/historial/{id}/respuesta?ambiente=` | Actualizar respuesta |

### Configuración

| Método | Endpoint | Descripción |
|---|---|---|
| GET | `/api/configuracion?ambiente=` | Config actual |
| PUT | `/api/configuracion` | Actualizar `.env` en caliente |
| GET | `/api/configuracion/stats?ambiente=` | Estadísticas (total, pendientes) |

### Webhook de WhatsApp (Meta)

| Método | Endpoint | Descripción |
|---|---|---|
| GET | `/api/whatsapp/webhook` | Verificación inicial del webhook (`hub.verify_token`) |
| POST | `/api/whatsapp/webhook` | Recepción de eventos (estados y mensajes) |

## Integración con WhatsApp Business API

La capa de integración vive en `backend/whatsapp_service.py` (servicio + cliente API +
procesador de eventos) y `backend/whatsapp_webhook.py` (router). Está separada de la
lógica de negocio del módulo de mensajería.

### Envío de mensajes

- Si la plantilla del sistema tiene `whatsapp_template` (template aprobado de Meta), se
  envía como `type: "template"` con idioma y variables.
- Si no, se envía como texto libre (`type: "text"`), que **solo es válido dentro de la
  ventana de 24 h** en la que el cliente escribió al negocio.

### Estados de WhatsApp

Al enviar se guarda el `whatsapp_message_id` en `log_envios`. Los cambios de estado
(`sent` → `delivered` → `read` → `failed`) llegan por webhook y actualizan la columna
`estado_whatsapp` del mismo registro. No hay sistema de seguimiento paralelo.

### Respuestas entrantes

Cuando un cliente responde, el webhook busca al paciente por teléfono, guarda la respuesta
en `log_envios` con `respuesta = 'respondio'` y actualiza la última interacción.

### Detección de "no respondió"

WhatsApp **no entrega** un evento `NO_RESPONDIÓ`. El sistema lo calcula así:

1. Al enviar se guarda la **fecha de envío** (`log_envios.fecha_hora`).
2. Se define una **fecha límite** (p. ej. 24/48 h) como plazo de respuesta.
3. Si existe una **respuesta** del paciente dentro del plazo → `respondió`.
4. Si no existe respuesta y el plazo ya venció → `no respondió`.
5. Se usa el historial de interacciones para no contar mensajes salientes como respuestas.

### Sistema de baja (opt-out)

Cuando un cliente responde "BAJA" (o palabras como `no`, `stop`, `baja`, `cancelar`),
el webhook:
- Marca `pacientes.whatsapp_opt_out = 1`.
- Registra `respuesta = 'baja'` en `log_envios`.
- A partir de ese momento el sistema **excluye al paciente** de cualquier envío.

La detección es por contenido del mensaje; Meta no entrega un evento explícito de baja.

### Diferencia técnica

| Situación | Cómo se detecta |
|---|---|
| Cliente responde BAJA | Mensaje entrante con keyword de opt-out → `whatsapp_opt_out=1` |
| Cliente no responde | Ausencia de respuesta tras la fecha límite |
| Mensaje fallido | Evento de estado `failed` de Meta |
| Número inválido | Validación de formato al enviar (`numero_invalido`) |
| Cliente bloquea la cuenta | Evento de error/no entregado de Meta (no hay evento explícito de bloqueo) |
| Mensaje no entregado | Evento de estado `failed`/`undelivered` de Meta |

### Idempotencia del webhook

Meta puede reenviar eventos. Cada payload se guarda en `whatsapp_eventos` con una clave
única (hash de `entry + timestamp`). Si la clave ya existe, el evento se omite.

### Configuración de Meta (paso a paso)

1. **Meta for Developers** → crea una App tipo **Business**.
2. Agrega el producto **WhatsApp** → se crea la **WhatsApp Business Account (WABA)**.
3. Añade y verifica el **número de teléfono** del negocio.
4. En **API Setup** copia:
   - `Phone number ID` → `SNW_WA_PHONE_ID`
   - `WhatsApp Business Account ID` → `SNW_WA_BUSINESS_ACCOUNT_ID`
   - `Access Token` → `SNW_WA_TOKEN`
5. Crea un **template de mensaje** en **Message Templates** y deja tu propia palabra
   como verify token → `SNW_WA_VERIFY_TOKEN`.
6. Configura el **Webhook** en la App con la URL pública:
   `https://TU-DOMINIO/api/whatsapp/webhook` y el **Verify Token**.
7. Suscribe el webhook al evento **`messages`** (mensajes) y **`message_template_status_update`**
   si necesitas saber el estado de tus templates.
8. Pon `SNW_METODO_ENVIO=api_oficial` en `.env`.

> **Nota:** Meta exige que el webhook esté en una URL **HTTPS pública** y accesible desde
> internet. En local, usa un túnel (ngrok) para probarlo.

### Opciones de "no respondió" (config adicional)

Puedes ampliar la detección con un job programado que recorra los envíos sin respuesta
y marque `no_respondio` pasadas N horas (ej. 24 h). Eso depende de la lógica de negocio,
no de Meta.

## Módulo de envío

- **Pacientes** (`pacientes.html`): seleccionar pacientes con checkboxes → elegir plantilla → "Iniciar envío"
- **Mensajería** (`mensajeria.html`): editar plantilla → "Enviar a todos los pendientes"
- **Producción**: antes de enviar, se envía correo de confirmación al supervisor con botones **Confirmar** y **Rechazar**
- **Desarrollo**: envío directo, restringido a números en `SNW_NUMEROS_PRUEBA_DEV`
- **Motor intercambiable**: `simulado`, `whatsapp_web` (abre wa.me), `api_oficial` (Graph API Meta)
- **Capa de integración**: `whatsapp_service.py` + `whatsapp_webhook.py` (envío, estados, respuestas, opt-out, idempotencia)
- **Cola asíncrona**: envío en segundo plano con progreso en vivo
- **Historial batch**: cada envío se registra en `envios` (una fila por "Iniciar envío"); sin nombres de pacientes

## Usuarios

| Usuario | Contraseña | Rol | Acceso |
|---|---|---|---|
| `admin` | `admin123` | administrador | Todo: pacientes, mensajería, historial, links de prueba en confirmación |
| `usuario` | `usuario123` | usuario | Mensajería, historial (sin acceso a pacientes, sin links de prueba) |

## Pantallas

- **Landing** (`index.html`): navegación según rol
- **Login** (`login.html`): formulario de acceso
- **Pacientes** (`pacientes.html`): solo admin — tabla con respuesta inline, envío integrado
- **Mensajería** (`mensajeria.html`): editor de plantillas + envío a todos los pendientes
- **Historial** (`historial.html`): envíos de ambas bases, detalle individual, respuesta por paciente
