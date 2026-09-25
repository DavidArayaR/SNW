"""Especialidades y roles dinámicos (Fase 1 multi-especialidad).

Reglas de esta fase:
- Una sola tabla de pacientes por especialidad: ``pacientes_<slug>`` (SIN
  sufijo de entorno ``_dev`` / ``_prod``).
- El ``slug`` se genera del nombre visible: minúsculas, sin acentos, sin
  espacios ni caracteres no alfanuméricos (``Kinesiología`` -> ``kinesiologia``).
- Nombre visible duplicado: el backend NO crea nada solo; devuelve las
  coincidencias para que la UI pregunte «utilizar existente / crear nueva».
  Con ``modo="nueva"`` se genera ``pacientes_<slug><n>`` con el primer sufijo
  numérico libre (si existen base, 2 y 4, la nueva usa 3).
- Cada especialidad tiene su propio rol (``roles_especialidad``) y los usuarios
  se vinculan en ``usuario_especialidad_roles``. El rol global de `usuarios`
  (usuario/administrador/desarrollador) NO se toca en esta fase.
"""

import csv as _csv
import io as _io
import re
import unicodedata

from db import conectar, log_error
from telefono import normalizar_telefono

# Nombre físico válido de tabla de especialidad. Los nombres legacy
# (`pacientes_dev`, `pacientes_prod`) también calzan el patrón, por eso una
# tabla solo cuenta como especialidad si está registrada en `especialidades`.
TABLA_RE = re.compile(r"^pacientes_[a-z0-9_]{1,54}$")

# La subida de CSV se rechaza si supera este tamaño (en bytes).
CSV_MAX_BYTES = 5 * 1024 * 1024

# Esquema de las tablas dinámicas: el mismo de pacientes_dev/prod incluyendo
# las columnas que hoy agregan las migraciones, para no depender de ALTERs.
_ESQUEMA_PACIENTES = (
    "CREATE TABLE IF NOT EXISTS {tabla} ("
    "  id INT AUTO_INCREMENT PRIMARY KEY,"
    "  nombre VARCHAR(150) NOT NULL,"
    "  apellido VARCHAR(150) NOT NULL DEFAULT '',"
    "  telefono VARCHAR(20) NOT NULL,"
    "  estado ENUM('pendiente','enviado','error') NOT NULL DEFAULT 'pendiente',"
    "  whatsapp_opt_out TINYINT(1) NOT NULL DEFAULT 0,"
    "  respuesta_manual VARCHAR(12) DEFAULT NULL,"
    "  interesado TINYINT(1) NOT NULL DEFAULT 0,"
    "  opt_out_explicito TINYINT(1) NOT NULL DEFAULT 0,"
    "  no_interesado TINYINT(1) NOT NULL DEFAULT 0,"
    "  interes_plantilla_clave VARCHAR(50) DEFAULT NULL,"
    "  interes_fecha DATETIME NULL,"
    "  ultimo_reintegro DATETIME NULL,"
    "  fecha_actualizacion DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,"
    "  INDEX idx_telefono (telefono)"
    ") CHARACTER SET utf8mb4"
)


def asegurar_tablas_especialidades(cur) -> None:
    """Crea las tablas del modelo multi-especialidad (idempotente).

    Recibe el cursor de la transacción abierta de `asegurar_tabla_config`."""
    cur.execute(
        "CREATE TABLE IF NOT EXISTS especialidades ("
        "  id INT AUTO_INCREMENT PRIMARY KEY,"
        "  nombre_visible VARCHAR(150) NOT NULL,"
        "  nombre_tabla_base VARCHAR(64) NOT NULL,"
        "  fecha_creacion DATETIME DEFAULT CURRENT_TIMESTAMP,"
        "  UNIQUE KEY uq_tabla_base (nombre_tabla_base)"
        ") CHARACTER SET utf8mb4"
    )
    cur.execute(
        "CREATE TABLE IF NOT EXISTS roles_especialidad ("
        "  id INT AUTO_INCREMENT PRIMARY KEY,"
        "  especialidad_id INT NOT NULL,"
        "  nombre VARCHAR(150) NOT NULL,"
        "  descripcion VARCHAR(255) NOT NULL DEFAULT '',"
        "  UNIQUE KEY uq_especialidad (especialidad_id),"
        "  INDEX idx_rol_nombre (nombre)"
        ") CHARACTER SET utf8mb4"
    )
    cur.execute(
        "CREATE TABLE IF NOT EXISTS usuario_especialidad_roles ("
        "  usuario_id INT NOT NULL,"
        "  rol_id INT NOT NULL,"
        "  creado DATETIME DEFAULT CURRENT_TIMESTAMP,"
        "  PRIMARY KEY (usuario_id, rol_id),"
        "  INDEX idx_uer_rol (rol_id)"
        ") CHARACTER SET utf8mb4"
    )


def slug_base(nombre_visible: str) -> str:
    """`Kinesiología Sede Maipú` -> `kinesiologiasedemaipu`."""
    n = unicodedata.normalize("NFKD", nombre_visible or "")
    n = n.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-zA-Z0-9]", "", n).lower()[:54]


def tabla_valida(nombre: str) -> bool:
    return bool(TABLA_RE.match(nombre or ""))


def normalizar_texto(t: str) -> str:
    """Nombre/apellido tipo "David Araya Rodriguez": sin espacios de más y
    en tipo Título (primera letra de cada palabra en mayúscula)."""
    return " ".join((t or "").split()).title()


def _tabla_fisica_existe(cur, tabla: str) -> bool:
    cur.execute(
        "SELECT COUNT(*) AS n FROM information_schema.TABLES"
        " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
        (tabla,),
    )
    return bool((cur.fetchone() or {}).get("n"))


def tabla_fisica_existe(tabla: str) -> bool:
    """True si la tabla física existe en `snw_base`."""
    if not tabla_valida(tabla):
        return False
    try:
        with conectar() as conn, conn.cursor() as cur:
            return _tabla_fisica_existe(cur, tabla)
    except Exception as e:
        log_error("tabla_fisica_existe", e)
        return False


def listar_especialidades() -> list[dict]:
    try:
        with conectar() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT e.id, e.nombre_visible, e.nombre_tabla_base, e.fecha_creacion,"
                "       r.id AS rol_id, r.nombre AS rol_nombre"
                " FROM especialidades e"
                " LEFT JOIN roles_especialidad r ON r.especialidad_id = e.id"
                " ORDER BY e.nombre_visible"
            )
            return cur.fetchall()
    except Exception as e:
        log_error("listar_especialidades", e)
        return []


def obtener_especialidad(especialidad_id: int) -> dict | None:
    try:
        with conectar() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT e.id, e.nombre_visible, e.nombre_tabla_base,"
                "       r.id AS rol_id, r.nombre AS rol_nombre"
                " FROM especialidades e"
                " LEFT JOIN roles_especialidad r ON r.especialidad_id = e.id"
                " WHERE e.id = %s",
                (int(especialidad_id),),
            )
            esp = cur.fetchone()
            if esp and tabla_valida(esp["nombre_tabla_base"]) \
                    and _tabla_fisica_existe(cur, esp["nombre_tabla_base"]):
                cur.execute(f"SELECT COUNT(*) AS n FROM {esp['nombre_tabla_base']}")
                esp["total_pacientes"] = int((cur.fetchone() or {}).get("n", 0))
            elif esp:
                esp["total_pacientes"] = 0
            return esp
    except Exception as e:
        log_error("obtener_especialidad", e)
        return None


def preparar_especialidad(nombre_visible: str, modo: str = "preguntar") -> dict:
    """Decide qué hacer ante un pedido de creación.

    Devuelve `{"decision": "preguntar"|"reutilizar"|"crear", ...}`. Con
    `decision == "preguntar"` el backend no crea nada: la UI debe mostrar las
    coincidencias y pedir «utilizar existente / crear nueva».
    """
    nombre = (nombre_visible or "").strip()
    if not nombre:
        raise ValueError("nombre_vacio")
    if len(nombre) > 150:
        raise ValueError("nombre_largo")
    slug = slug_base(nombre)
    if not slug:
        raise ValueError("slug_vacio")
    modo = (modo or "preguntar").strip().lower()
    if modo not in ("preguntar", "reutilizar", "nueva"):
        raise ValueError("modo_invalido")
    with conectar() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT e.id, e.nombre_visible, e.nombre_tabla_base,"
            "       r.id AS rol_id, r.nombre AS rol_nombre"
            " FROM especialidades e"
            " LEFT JOIN roles_especialidad r ON r.especialidad_id = e.id"
            " WHERE LOWER(e.nombre_visible) = LOWER(%s) ORDER BY e.id",
            (nombre,),
        )
        existentes = cur.fetchall()
    if existentes and modo == "preguntar":
        return {"decision": "preguntar", "existentes": existentes}
    if existentes and modo == "reutilizar":
        return {"decision": "reutilizar", "especialidad": existentes[0]}
    return {"decision": "crear", "nombre": nombre, "slug": slug,
            "aviso_duplicada": bool(existentes)}


def crear_especialidad(nombre: str, slug: str) -> dict:
    """Crea la especialidad, su tabla `pacientes_<slug>` y su rol."""
    base = f"pacientes_{slug}"
    if not tabla_valida(base):
        raise ValueError("slug_invalido")
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("SELECT nombre_tabla_base FROM especialidades")
        en_uso = {r["nombre_tabla_base"] for r in cur.fetchall()}
        tabla = base
        sufijo = 0
        if base in en_uso or _tabla_fisica_existe(cur, base):
            # Primer sufijo numérico libre: con base, 2 y 4 ocupados, usa 3.
            n = 2
            while True:
                cand = f"{base}{n}"
                if not tabla_valida(cand):
                    raise ValueError("tabla_larga")
                if cand not in en_uso and not _tabla_fisica_existe(cur, cand):
                    tabla = cand
                    sufijo = n
                    break
                n += 1
                if n > 9999:
                    raise ValueError("sin_sufijo_libre")
        cur.execute(_ESQUEMA_PACIENTES.format(tabla=tabla))
        visible = f"{nombre} {sufijo}" if sufijo else nombre
        cur.execute(
            "INSERT INTO especialidades (nombre_visible, nombre_tabla_base)"
            " VALUES (%s, %s)",
            (visible, tabla),
        )
        esp_id = cur.lastrowid
        cur.execute(
            "INSERT INTO roles_especialidad (especialidad_id, nombre, descripcion)"
            " VALUES (%s, %s, %s)",
            (esp_id, visible, f"Acceso a la especialidad {visible}"),
        )
        conn.commit()
    return {"id": esp_id, "nombre_visible": visible,
            "nombre_tabla_base": tabla, "rol": visible}


def renombrar_especialidad(especialidad_id: int, nuevo_visible: str) -> dict:
    """Cambia el nombre visible (y el del rol). La tabla física NO cambia."""
    nombre = (nuevo_visible or "").strip()
    if not nombre:
        raise ValueError("nombre_vacio")
    if len(nombre) > 150:
        raise ValueError("nombre_largo")
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM especialidades WHERE id = %s", (int(especialidad_id),))
        if not cur.fetchone():
            raise ValueError("no_existe")
        cur.execute(
            "SELECT id FROM especialidades WHERE LOWER(nombre_visible) = LOWER(%s)"
            " AND id <> %s",
            (nombre, int(especialidad_id)),
        )
        if cur.fetchone():
            raise ValueError("nombre_duplicado")
        cur.execute(
            "UPDATE especialidades SET nombre_visible = %s WHERE id = %s",
            (nombre, int(especialidad_id)),
        )
        cur.execute(
            "UPDATE roles_especialidad SET nombre = %s, descripcion = %s"
            " WHERE especialidad_id = %s",
            (nombre, f"Acceso a la especialidad {nombre}", int(especialidad_id)),
        )
        conn.commit()
    return obtener_especialidad(int(especialidad_id)) or {}


def usuario_id_por_correo(correo: str) -> int | None:
    correo = (correo or "").strip().lower()
    if not correo:
        return None
    try:
        with conectar() as conn, conn.cursor() as cur:
            cur.execute("SELECT id FROM usuarios WHERE usuario = %s", (correo,))
            fila = cur.fetchone()
            return int(fila["id"]) if fila else None
    except Exception as e:
        log_error("usuario_id_por_correo", e)
        return None


def especialidades_de_usuario(usuario_id: int) -> list[dict]:
    try:
        with conectar() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT e.id, e.nombre_visible, e.nombre_tabla_base,"
                "       r.id AS rol_id, r.nombre AS rol_nombre"
                " FROM usuario_especialidad_roles uer"
                " JOIN roles_especialidad r ON r.id = uer.rol_id"
                " JOIN especialidades e ON e.id = r.especialidad_id"
                " WHERE uer.usuario_id = %s"
                " ORDER BY e.nombre_visible",
                (int(usuario_id),),
            )
            return cur.fetchall()
    except Exception as e:
        log_error("especialidades_de_usuario", e)
        return []


def puede_acceder_especialidad(es_privilegiado: bool, usuario_id: int | None,
                               especialidad_id: int) -> bool:
    """Admin/dev acceden a todo; el resto solo a sus especialidades asignadas."""
    if es_privilegiado:
        return True
    if not usuario_id:
        return False
    return any(e["id"] == int(especialidad_id)
               for e in especialidades_de_usuario(usuario_id))


def asignar_rol_especialidad(especialidad_id: int, usuario_id: int) -> dict:
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, nombre FROM roles_especialidad WHERE especialidad_id = %s",
                    (int(especialidad_id),))
        rol = cur.fetchone()
        if not rol:
            raise ValueError("no_existe")
        cur.execute(
            "INSERT IGNORE INTO usuario_especialidad_roles (usuario_id, rol_id)"
            " VALUES (%s, %s)",
            (int(usuario_id), int(rol["id"])),
        )
        conn.commit()
    return {"rol_id": int(rol["id"]), "rol": rol["nombre"],
            "especialidad_id": int(especialidad_id)}


def retirar_rol_especialidad(especialidad_id: int, usuario_id: int) -> bool:
    with conectar() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE uer FROM usuario_especialidad_roles uer"
            " JOIN roles_especialidad r ON r.id = uer.rol_id"
            " WHERE uer.usuario_id = %s AND r.especialidad_id = %s",
            (int(usuario_id), int(especialidad_id)),
        )
        conn.commit()
        return (cur.rowcount or 0) > 0


def eliminar_paciente_especialidad(especialidad_id: int, paciente_id: int) -> bool:
    """Borra UN registro de la tabla de la especialidad (solo admin/dev desde
    el endpoint). También borra sus filas de log propias (aisladas por tabla);
    el resto del historial no se toca."""
    esp = obtener_especialidad(int(especialidad_id))
    if not esp:
        raise ValueError("no_existe")
    tabla = esp["nombre_tabla_base"]
    if not tabla_valida(tabla):
        raise ValueError("tabla_invalida")
    with conectar() as conn, conn.cursor() as cur:
        if not _tabla_fisica_existe(cur, tabla):
            raise ValueError("no_existe")
        cur.execute(f"DELETE FROM {tabla} WHERE id = %s", (int(paciente_id),))
        borrado = (cur.rowcount or 0) > 0
        if borrado:
            cur.execute(
                "DELETE FROM log_envios WHERE paciente_id = %s AND tabla_pacientes = %s",
                (int(paciente_id), tabla),
            )
        conn.commit()
        return borrado


def eliminar_tabla_especialidad(especialidad_id: int) -> dict:
    """Elimina la especialidad entera (solo admin/dev desde el endpoint):
    tabla física, rol, asignaciones y fila de especialidad. El historial de
    envíos (`envios`/`log_envios`) se conserva como trazabilidad."""
    esp = obtener_especialidad(int(especialidad_id))
    if not esp:
        raise ValueError("no_existe")
    tabla = esp["nombre_tabla_base"]
    if not tabla_valida(tabla):
        raise ValueError("tabla_invalida")
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM roles_especialidad WHERE especialidad_id = %s",
                    (int(especialidad_id),))
        rol = cur.fetchone()
        if rol:
            cur.execute("DELETE FROM usuario_especialidad_roles WHERE rol_id = %s",
                        (int(rol["id"]),))
            cur.execute("DELETE FROM roles_especialidad WHERE id = %s", (int(rol["id"]),))
        if _tabla_fisica_existe(cur, tabla):
            cur.execute(f"DROP TABLE {tabla}")
        cur.execute("DELETE FROM especialidades WHERE id = %s", (int(especialidad_id),))
        conn.commit()
    return {"id": int(especialidad_id), "nombre_visible": esp["nombre_visible"],
            "nombre_tabla_base": tabla}


def importar_pacientes_csv(especialidad_id: int, datos: bytes) -> dict:
    """Valida un CSV y lo inserta en la tabla de la especialidad.

    Columnas obligatorias: `nombre`, `apellido`, `telefono` (UTF-8).
    Teléfonos normalizados con `telefono.py` (`+569XXXXXXXX`); los campos del
    esquema SNW se inicializan (`pendiente`, sin opt-out ni interés).
    """
    esp = obtener_especialidad(int(especialidad_id))
    if not esp:
        raise ValueError("no_existe")
    tabla = esp["nombre_tabla_base"]
    if not tabla_valida(tabla):
        raise ValueError("tabla_invalida")
    try:
        texto = (datos or b"").decode("utf-8-sig")
    except (UnicodeDecodeError, ValueError):
        raise ValueError("codificacion")
    lector = _csv.DictReader(_io.StringIO(texto))
    if not lector.fieldnames:
        raise ValueError("csv_vacio")
    cols = {(c or "").strip().lower(): c for c in lector.fieldnames}
    faltan = [c for c in ("nombre", "apellido", "telefono") if c not in cols]
    if faltan:
        raise ValueError("columnas:" + ",".join(faltan))

    with conectar() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT telefono FROM {tabla}")
        existentes = {r["telefono"] for r in cur.fetchall()}

        procesados = insertados = duplicados = 0
        errores: list[dict] = []
        vistos_archivo: set[str] = set()
        for nro, fila in enumerate(lector, start=2):
            nombre = normalizar_texto(fila.get(cols["nombre"]))
            apellido = normalizar_texto(fila.get(cols["apellido"]))
            telefono_crudo = (fila.get(cols["telefono"]) or "").strip()
            if not nombre and not apellido and not telefono_crudo:
                continue
            procesados += 1
            if not nombre:
                errores.append({"fila": nro, "motivo": "Falta el nombre"})
                continue
            telefono = normalizar_telefono(telefono_crudo)
            if telefono is None:
                errores.append({"fila": nro,
                                "motivo": f"Formato de teléfono inválido: '{telefono_crudo}'"})
                continue
            if telefono in existentes or telefono in vistos_archivo:
                duplicados += 1
                continue
            vistos_archivo.add(telefono)
            cur.execute(
                f"INSERT INTO {tabla} (nombre, apellido, telefono, estado,"
                " whatsapp_opt_out, interesado, opt_out_explicito)"
                " VALUES (%s, %s, %s, 'pendiente', 0, 0, 0)",
                (nombre[:150], apellido[:150], telefono),
            )
            insertados += 1
        conn.commit()

    return {"procesados": procesados, "insertados": insertados,
            "duplicados": duplicados, "rechazados": len(errores),
            "errores": errores}


def listar_tablas_especialidades() -> list[dict]:
    """[{id, nombre_tabla_base}] para construir listas blancas (webhook, etc.)."""
    try:
        with conectar() as conn, conn.cursor() as cur:
            cur.execute("SELECT id, nombre_tabla_base FROM especialidades ORDER BY id")
            return cur.fetchall()
    except Exception as e:
        log_error("listar_tablas_especialidades", e)
        return []


def ids_de_especialidades() -> list[int]:
    return [int(r["id"]) for r in listar_tablas_especialidades()]


def especialidad_por_tabla(tabla: str) -> dict | None:
    """Especialidad registrada para una tabla física (None si no es de
    especialidad: tablas legacy u otras)."""
    if not tabla_valida(tabla):
        return None
    try:
        with conectar() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT e.id, e.nombre_visible, e.nombre_tabla_base,"
                "       r.id AS rol_id, r.nombre AS rol_nombre"
                " FROM especialidades e"
                " LEFT JOIN roles_especialidad r ON r.especialidad_id = e.id"
                " WHERE e.nombre_tabla_base = %s",
                (tabla,),
            )
            return cur.fetchone()
    except Exception as e:
        log_error("especialidad_por_tabla", e)
        return None


def especialidades_ids_de_usuario(usuario_id: int) -> list[int]:
    return [int(e["id"]) for e in especialidades_de_usuario(usuario_id)]


def especialidades_por_usuarios() -> dict:
    """{login: [{id, nombre_visible}]} para enriquecer el listado de cuentas."""
    try:
        with conectar() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT u.usuario, e.id, e.nombre_visible"
                " FROM usuario_especialidad_roles uer"
                " JOIN roles_especialidad r ON r.id = uer.rol_id"
                " JOIN especialidades e ON e.id = r.especialidad_id"
                " JOIN usuarios u ON u.id = uer.usuario_id"
                " ORDER BY u.usuario, e.nombre_visible"
            )
            out: dict = {}
            for r in cur.fetchall():
                out.setdefault(r["usuario"], []).append(
                    {"id": int(r["id"]), "nombre_visible": r["nombre_visible"]})
            return out
    except Exception as e:
        log_error("especialidades_por_usuarios", e)
        return {}


def correos_supervisores_especialidad(especialidad_id: int) -> list[dict]:
    """Supervisores activos asignados a la especialidad que tienen un correo
    contactable (login si es correo, si no el de recuperación). Se usa para
    avisarles las solicitudes de envío en producción de su especialidad."""
    try:
        with conectar() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT u.usuario, u.nombre, u.correo_recuperacion"
                " FROM usuario_especialidad_roles uer"
                " JOIN roles_especialidad r ON r.id = uer.rol_id"
                " JOIN usuarios u ON u.id = uer.usuario_id"
                " WHERE r.especialidad_id = %s AND u.rol = 'supervisor' AND u.activo = 1",
                (int(especialidad_id),),
            )
            out = []
            for row in cur.fetchall():
                correo = (row.get("correo_recuperacion") or "").strip().lower()
                login = (row.get("usuario") or "").strip().lower()
                if "@" not in correo and "@" in login:
                    correo = login
                if "@" in correo and correo not in [o["correo"] for o in out]:
                    out.append({"usuario": login, "nombre": row.get("nombre") or login,
                                "correo": correo})
            return out
    except Exception as e:
        log_error("correos_supervisores_especialidad", e)
        return []
