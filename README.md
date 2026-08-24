# SNW — Sistema de Notificaciones WhatsApp

Prototipo web del módulo de notificaciones WhatsApp para pacientes. Backend en **Python (FastAPI)**: pacientes desde **MySQL** y plantillas en **archivo JSON**.

## Stack

| Componente | Tecnología |
|---|---|
| Frontend | HTML5, CSS, JavaScript vanilla |
| Backend / API | Python + FastAPI + Uvicorn |
| Base de datos | MySQL / MariaDB (`snw_pacientes`, vía PyMySQL) |
| Plantillas | Archivo JSON (`data/plantillas.json`) |

## Estructura

```
snw/
├── main.py                  Servidor FastAPI (API + archivos estáticos)
├── db.py                    Conexión MySQL (PyMySQL)
├── motor_envio.py           Motor de envío intercambiable (simulado/whatsapp_web/api_oficial)
├── data/
│   ├── plantillas.json      Almacenamiento de plantillas
├── .env                     Configuración local (no se sube al repositorio)
├── .env.example             Plantilla de configuración
├── pacientes.html           Pacientes + módulo de envío integrado
├── plantillas.html          Editor de plantillas (CRUD)
├── historial.html           Historial de mensajes enviados
├── css/
│   ├── styles.css           Estilos compartidos
│   └── pacientes.css        Estilos de la tabla y del envío
├── js/
│   ├── app.js               Lógica del editor de plantillas
│   └── pacientes.js         Lógica de listado y envío
├── sql/
│   ├── setup.sql            Columna nombre en plantillas (histórico)
│   └── paciente_prueba.sql  Alta de paciente de prueba
├── requirements.txt         Dependencias Python
└── README.md
```

## Puesta en marcha

1. Instalar dependencias:
   ```
   pip install -r requirements.txt
   ```
2. MySQL activo (XAMPP) con la base `snw_pacientes`.
3. Copiar `.env.example` como `.env` y completar credenciales de MySQL y parámetros del sistema:
   - `SNW_ENTORNO`: `desarrollo` o `produccion`
   - `SNW_METODO_ENVIO`: `simulado`, `whatsapp_web` o `api_oficial`
   - `SNW_NUMEROS_PRUEBA`: números autorizados separados por coma
   - `SNW_INTERVALO_MS`: pausa entre mensajes
   - `SNW_WA_TOKEN` y `SNW_WA_PHONE_ID`: credenciales de la API oficial de Meta (solo si `SNW_METODO_ENVIO=api_oficial`)
   - `DB_HOST`, `DB_PUERTO`, `DB_USUARIO`, `DB_CONTRASENA`, `DB_NOMBRE`
4. Iniciar el servidor (puerto 8000):
   ```
   python main.py
   ```
5. Abrir:
   - Pacientes: http://localhost:8000/pacientes.html
   - Plantillas: http://localhost:8000/plantillas.html
   - Documentación interactiva de la API: http://localhost:8000/docs

## Base de datos

Base: `snw_pacientes`

**pacientes** (MySQL): id, nombre, apellido, telefono, info_extra, estado (pendiente/enviado/error), plantilla, fechas de auditoría.

Los teléfonos se almacenan en formato internacional (`+569XXXXXXXXX`).

## Almacenamiento de plantillas

Las plantillas **no** se guardan en MySQL: viven en `data/plantillas.json` con esta estructura:

```json
{
  "id": 1,
  "clave": "recordatorio_cita",
  "nombre": "Recordatorio cita",
  "texto": "Hola {nombre}, ...",
  "actualizada": 1787565579000
}
```

Cada creación, edición o eliminación desde la interfaz reescribe el archivo.

## API

| Método | Endpoint | Descripción |
|---|---|---|
| GET | `/api/pacientes?q=` | Lista de pacientes desde MySQL (búsqueda opcional) |
| GET | `/api/plantillas` | Lista de plantillas desde JSON |
| POST | `/api/plantillas` | Crear plantilla `{nombre, texto}` |
| PUT | `/api/plantillas/{id}` | Actualizar plantilla `{nombre, texto}` |
| DELETE | `/api/plantillas/{id}` | Eliminar plantilla |
| POST | `/api/notificaciones/enviar` | Iniciar envío `{pacientes: [ids], plantilla_id}` |
| GET | `/api/notificaciones/jobs/{job_id}` | Progreso del envío en curso |
| GET | `/api/notificaciones/historial?q=&estado=` | Historial de mensajes |
| GET | `/api/configuracion` | Configuración actual |
| PUT | `/api/configuracion` | Actualizar configuración |

## Módulo de envío

Integrado en la página de Pacientes: se marcan pacientes con checkboxes (o "seleccionar todos" sobre los filtrados) y se presiona "Iniciar envío".

- **Cola asíncrona:** el envío se procesa en segundo plano; un modal muestra progreso en vivo mediante polling.
- **Validaciones previas:** existencia del paciente y de la plantilla, formato internacional `+569XXXXXXXXX`, disponibilidad del canal.
- **Descartes sin interrumpir la cola:** teléfonos inválidos o no autorizados se reportan como rechazados con su motivo.
- **Filtro de entorno desarrollo:** solo salen mensajes hacia los números autorizados en `SNW_NUMEROS_PRUEBA` (archivo `.env`).
- **Motor intercambiable** (`motor_envio.py`): `whatsapp_web` (abre `wa.me`/WhatsApp Web por destinatario), `simulado` y `api_oficial` (**implementado**: envía vía Graph API de Meta con `SNW_WA_TOKEN` y `SNW_WA_PHONE_ID`); el método se elige en `.env`, con intervalo entre mensajes configurable.
- **Estados por paciente:** cada envío actualiza `pacientes.estado` (pendiente / enviado / error).
- **Historial:** cada intento (enviado, error, nº inválido) se registra en la tabla `log_envios` con fecha, paciente, teléfono, plantilla y detalle del fallo; consultable en `historial.html` con búsqueda y filtros.

## Funcionalidades actuales

- Editor de plantillas: crear, editar, eliminar, con vista previa en vivo y comodines `{nombre}`, `{apellido}`, `{info_extra}`.
- Persistencia de plantillas en archivo JSON.
- Listado de pacientes desde MySQL con búsqueda, filtro por estado y contadores clicables.
- Módulo de envío en 3 pasos con cola asíncrona, validaciones y progreso en vivo.
- Navegación unificada entre módulos.

## Pendientes (siguiente etapa)

- Motor de envío real vía API Oficial Business (envío sin intervención del usuario).
- Logs del sistema y autenticación SSO por roles.
