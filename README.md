# SNW — Sistema de Notificaciones WhatsApp

Prototipo web del módulo de notificaciones WhatsApp para pacientes, integrado a la plataforma de la empresa. Fase actual: **frontend + APIs PHP sobre XAMPP** (Apache + MySQL), como base antes de migrar el backend a Python/FastAPI.

Documentación de diseño: ver `context.md` y `features.md` en el repositorio.

## Stack

| Componente | Tecnología |
|---|---|
| Frontend | HTML5, CSS, JavaScript vanilla |
| Backend (prototipo) | PHP (APIs REST) |
| Base de datos | MySQL / MariaDB (`snw_pacientes`) |
| Servidor local | XAMPP (Apache puerto 80, MySQL puerto 3306) |
| Backend (futuro) | Python + FastAPI (ver `requirements.txt`) |

## Estructura

```
snw/
├── pacientes.html        Lista de pacientes
├── plantillas.html       Editor de plantillas (CRUD)
├── api/
│   ├── db.php            Conexión MySQL y helpers JSON
│   ├── pacientes.php     GET lista de pacientes (?q= busca)
│   └── plantillas.php    GET/POST/PUT/DELETE plantillas
├── css/
│   ├── styles.css        Estilos compartidos
│   └── pacientes.css     Estilos de la tabla de pacientes
├── js/
│   ├── app.js            Lógica del editor de plantillas
│   └── pacientes.js      Lógica del listado de pacientes
└── sql/
    ├── setup.sql         Columna nombre en plantillas
    └── paciente_prueba.sql  Alta de paciente de prueba
```

## Puesta en marcha

1. Instalar XAMPP con Apache y MySQL activos.
2. Copiar esta carpeta en `htdocs` (accesible en `http://localhost/snw/`).
3. Crear la base de datos `snw_pacientes` e importar los scripts de `sql/`.
4. Revisar credenciales en `api/db.php` (por defecto: root sin contraseña).
5. Abrir:
   - Pacientes: http://localhost/snw/pacientes.html
   - Plantillas: http://localhost/snw/plantillas.html

## Base de datos

Base: `snw_pacientes`

**pacientes**: id, nombre, apellido, telefono, info_extra, estado (pendiente/enviado/error), plantilla, fechas de auditoría.

**plantillas**: id, clave (única), nombre, texto, fechas de auditoría.

Los teléfonos se almacenan en formato internacional (`+569XXXXXXXXX`).

## API

| Método | Endpoint | Descripción |
|---|---|---|
| GET | `/snw/api/pacientes.php?q=` | Lista de pacientes (con búsqueda opcional) |
| GET | `/snw/api/plantillas.php` | Lista de plantillas |
| POST | `/snw/api/plantillas.php` | Crear plantilla `{nombre, texto}` |
| PUT | `/snw/api/plantillas.php?id=N` | Actualizar plantilla `{nombre, texto}` |
| DELETE | `/snw/api/plantillas.php?id=N` | Eliminar plantilla |

## Funcionalidades actuales

- Editor de plantillas: crear, editar, eliminar, con comodines `{nombre}`, `{info_extra}`, `{fecha}`, `{hora}`.
- Persistencia real en MySQL.
- Listado de pacientes con búsqueda, estados y contadores.
- Navegación unificada entre módulos.

## Pendientes (siguiente etapa)

- Backend Python/FastAPI reemplazando PHP (mismos endpoints).
- Motor de envío WhatsApp (WhatsApp Web o API Oficial Business).
- Envío individual/masivo con cola asíncrona y validación de teléfonos.
- Historial de envíos, logs y autenticación SSO por roles.
