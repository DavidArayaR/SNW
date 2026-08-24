# SNW — Sistema de Notificaciones WhatsApp

Prototipo web del módulo de notificaciones WhatsApp para pacientes. Backend en **Python (FastAPI)**: pacientes desde **MySQL** y plantillas en **archivo JSON**.

Documentación de diseño: ver `context.md` y `features.md` en el repositorio.

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
├── data/
│   └── plantillas.json      Almacenamiento de plantillas
├── pacientes.html           Lista de pacientes
├── plantillas.html          Editor de plantillas (CRUD)
├── css/
│   ├── styles.css           Estilos compartidos
│   └── pacientes.css        Estilos de la tabla de pacientes
├── js/
│   ├── app.js               Lógica del editor de plantillas
│   └── pacientes.js         Lógica del listado de pacientes
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
2. MySQL activo (XAMPP) con la base `snw_pacientes`. Revisar credenciales en `db.py`.
3. Iniciar el servidor (puerto 8000):
   ```
   python main.py
   ```
4. Abrir:
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

## Funcionalidades actuales

- Editor de plantillas: crear, editar, eliminar, con comodines `{nombre}`, `{info_extra}`, `{fecha}`, `{hora}`.
- Persistencia de plantillas en archivo JSON.
- Listado de pacientes desde MySQL con búsqueda, filtro por estado y contadores clicables.
- Navegación unificada entre módulos.

## Pendientes (siguiente etapa)

- Motor de envío WhatsApp (WhatsApp Web o API Oficial Business).
- Envío individual/masivo con cola asíncrona y validación de teléfonos.
- Historial de envíos, logs y autenticación SSO por roles.
