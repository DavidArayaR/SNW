"""Especialidades y roles dinámicos (Fase 1 multi-especialidad).

Reglas de esta fase:
- Una sola tabla de pacientes por especialidad: ``pacientes_<slug>`` (SIN
  sufijo de entorno ``_dev`` / ``_prod``).
- El ``slug`` se genera del nombre visible: minúsculas, sin acentos, sin
  espacios ni caracteres no alfanuméricos (``Kinesiología`` -> ``kinesiologia``).
- Nombre visible duplicado: el backend NO crea nada solo; devuelve las
  coincidencias para que la UI pregunte ┬½utilizar existente / crear nueva┬╗.
  Con ``modo="nueva"`` se genera ``pacientes_<slug><n>`` con el primer sufijo
  numérico libre (si existen base, 2 y 4, la nueva usa 3).
- Cada especialidad tiene su propio rol (``roles_area``) y los usuarios
  se vinculan en ``usuario_area_roles``. El rol global de `usuarios`
  (usuario/administrador/desarrollador) NO se toca en esta fase.
"""

import csv as _csv
import io as _io
import re
import unicodedata

from db import conectar, log_error, columna_existe, tabla_pacientes
from telefono import normalizar_telefono

# Nombre físico válido de tabla de especialidad. Los nombres legacy
# (`pacientes_dev`, `pacientes_prod`) también calzan el patrón, por eso una
# tabla solo cuenta como especialidad si está registrada en `areas`.
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


def migrar_tablas_areas(cur) -> None:
    """Renombra las tablas del modelo a áreas: especialidades -> areas,
    roles_especialidad -> roles_area, usuario_especialidad_roles ->
    usuario_area_roles. RENAME conserva datos e índices. Idempotente: si la
    tabla nueva ya existe no hace nada."""
    for vieja, nueva in (("especialidades", "areas"),
                         ("roles_especialidad", "roles_area"),
                         ("usuario_especialidad_roles", "usuario_area_roles")):
        cur.execute(
            "SELECT TABLE_NAME FROM information_schema.TABLES"
            " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s, %s)",
            (vieja, nueva),
        )
        hay = {r["TABLE_NAME"] for r in cur.fetchall()}
        if vieja in hay and nueva not in hay:
            cur.execute(f"RENAME TABLE {vieja} TO {nueva}")
        elif vieja in hay and nueva in hay:
            log_error(f"migrar_tablas_areas: existen {vieja} y {nueva}; se conserva {nueva}")
    # Columna especialidad_id -> area_id en roles_area (conserva datos).
    cur.execute(
        "SELECT COLUMN_NAME FROM information_schema.COLUMNS"
        " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'roles_area'"
        "   AND COLUMN_NAME IN ('especialidad_id', 'area_id')"
    )
    cols = {r["COLUMN_NAME"] for r in cur.fetchall()}
    if "especialidad_id" in cols and "area_id" not in cols:
        cur.execute("ALTER TABLE roles_area CHANGE COLUMN especialidad_id area_id INT NOT NULL")
    cur.execute(
        "SELECT COUNT(*) AS n FROM information_schema.STATISTICS"
        " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'roles_area'"
        "   AND INDEX_NAME = 'uq_especialidad'"
    )
    if (cur.fetchone() or {}).get("n"):
        cur.execute("ALTER TABLE roles_area DROP INDEX uq_especialidad")
    cur.execute(
        "SELECT COUNT(*) AS n FROM information_schema.STATISTICS"
        " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'roles_area'"
        "   AND INDEX_NAME = 'uq_area'"
    )
    if not (cur.fetchone() or {}).get("n"):
        cur.execute("ALTER TABLE roles_area ADD UNIQUE KEY uq_area (area_id)")


def asegurar_tablas_areas(cur) -> None:
    """Crea las tablas del modelo multi-especialidad (idempotente).

    Recibe el cursor de la transacción abierta de `asegurar_tabla_config`."""
    cur.execute(
        "CREATE TABLE IF NOT EXISTS areas ("
        "  id INT AUTO_INCREMENT PRIMARY KEY,"
        "  nombre_visible VARCHAR(150) NOT NULL,"
        "  nombre_tabla_base VARCHAR(64) NOT NULL,"
        "  fecha_creacion DATETIME DEFAULT CURRENT_TIMESTAMP,"
        "  UNIQUE KEY uq_tabla_base (nombre_tabla_base)"
        ") CHARACTER SET utf8mb4"
    )
    cur.execute(
        "CREATE TABLE IF NOT EXISTS roles_area ("
        "  id INT AUTO_INCREMENT PRIMARY KEY,"
        "  area_id INT NOT NULL,"
        "  nombre VARCHAR(150) NOT NULL,"
        "  descripcion VARCHAR(255) NOT NULL DEFAULT '',"
        "  UNIQUE KEY uq_area (area_id),"
        "  INDEX idx_rol_nombre (nombre)"
        ") CHARACTER SET utf8mb4"
    )
    cur.execute(
        "CREATE TABLE IF NOT EXISTS usuario_area_roles ("
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


def listar_areas() -> list[dict]:
    try:
        with conectar() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT e.id, e.nombre_visible, e.nombre_tabla_base, e.fecha_creacion,"
                "       r.id AS rol_id, r.nombre AS rol_nombre"
                " FROM areas e"
                " LEFT JOIN roles_area r ON r.area_id = e.id"
                " ORDER BY e.nombre_visible"
            )
            filas = cur.fetchall()
            for esp in filas:
                if tabla_valida(esp["nombre_tabla_base"]) \
                        and _tabla_fisica_existe(cur, esp["nombre_tabla_base"]):
                    cur.execute(f"SELECT COUNT(*) AS n FROM {esp['nombre_tabla_base']}")
                    esp["total_pacientes"] = int((cur.fetchone() or {}).get("n", 0))
                else:
                    esp["total_pacientes"] = 0
            return filas
    except Exception as e:
        log_error("listar_areas", e)
        return []


def obtener_area(area_id: int) -> dict | None:
    try:
        with conectar() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT e.id, e.nombre_visible, e.nombre_tabla_base,"
                "       r.id AS rol_id, r.nombre AS rol_nombre"
                " FROM areas e"
                " LEFT JOIN roles_area r ON r.area_id = e.id"
                " WHERE e.id = %s",
                (int(area_id),),
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
        log_error("obtener_area", e)
        return None


def preparar_area(nombre_visible: str, modo: str = "preguntar") -> dict:
    """Decide qué hacer ante un pedido de creación.

    Devuelve `{"decision": "preguntar"|"reutilizar"|"crear", ...}`. Con
    `decision == "preguntar"` el backend no crea nada: la UI debe mostrar las
    coincidencias y pedir ┬½utilizar existente / crear nueva┬╗.
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
            " FROM areas e"
            " LEFT JOIN roles_area r ON r.area_id = e.id"
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


def crear_area(nombre: str, slug: str) -> dict:
    """Crea la especialidad, su tabla `pacientes_<slug>` y su rol."""
    base = f"pacientes_{slug}"
    if not tabla_valida(base):
        raise ValueError("slug_invalido")
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("SELECT nombre_tabla_base FROM areas")
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
            "INSERT INTO areas (nombre_visible, nombre_tabla_base)"
            " VALUES (%s, %s)",
            (visible, tabla),
        )
        esp_id = cur.lastrowid
        cur.execute(
            "INSERT INTO roles_area (area_id, nombre, descripcion)"
            " VALUES (%s, %s, %s)",
            (esp_id, visible, f"Acceso a la especialidad {visible}"),
        )
        conn.commit()
    return {"id": esp_id, "nombre_visible": visible,
            "nombre_tabla_base": tabla, "rol": visible}


def renombrar_area(area_id: int, nuevo_visible: str) -> dict:
    """Cambia el nombre visible (y el del rol). La tabla física NO cambia."""
    nombre = (nuevo_visible or "").strip()
    if not nombre:
        raise ValueError("nombre_vacio")
    if len(nombre) > 150:
        raise ValueError("nombre_largo")
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM areas WHERE id = %s", (int(area_id),))
        if not cur.fetchone():
            raise ValueError("no_existe")
        cur.execute(
            "SELECT id FROM areas WHERE LOWER(nombre_visible) = LOWER(%s)"
            " AND id <> %s",
            (nombre, int(area_id)),
        )
        if cur.fetchone():
            raise ValueError("nombre_duplicado")
        cur.execute(
            "UPDATE areas SET nombre_visible = %s WHERE id = %s",
            (nombre, int(area_id)),
        )
        cur.execute(
            "UPDATE roles_area SET nombre = %s, descripcion = %s"
            " WHERE area_id = %s",
            (nombre, f"Acceso a la especialidad {nombre}", int(area_id)),
        )
        conn.commit()
    return obtener_area(int(area_id)) or {}


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


def areas_de_usuario(usuario_id: int) -> list[dict]:
    try:
        with conectar() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT e.id, e.nombre_visible, e.nombre_tabla_base,"
                "       r.id AS rol_id, r.nombre AS rol_nombre"
                " FROM usuario_area_roles uer"
                " JOIN roles_area r ON r.id = uer.rol_id"
                " JOIN areas e ON e.id = r.area_id"
                " WHERE uer.usuario_id = %s"
                " ORDER BY e.nombre_visible",
                (int(usuario_id),),
            )
            filas = cur.fetchall()
            for esp in filas:
                if tabla_valida(esp["nombre_tabla_base"]) \
                        and _tabla_fisica_existe(cur, esp["nombre_tabla_base"]):
                    cur.execute(f"SELECT COUNT(*) AS n FROM {esp['nombre_tabla_base']}")
                    esp["total_pacientes"] = int((cur.fetchone() or {}).get("n", 0))
                else:
                    esp["total_pacientes"] = 0
            return filas
    except Exception as e:
        log_error("areas_de_usuario", e)
        return []


def puede_acceder_area(es_privilegiado: bool, usuario_id: int | None,
                               area_id: int) -> bool:
    """Admin/dev acceden a todo; el resto solo a sus areas asignadas."""
    if es_privilegiado:
        return True
    if not usuario_id:
        return False
    return any(e["id"] == int(area_id)
               for e in areas_de_usuario(usuario_id))


def asignar_rol_area(area_id: int, usuario_id: int) -> dict:
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, nombre FROM roles_area WHERE area_id = %s",
                    (int(area_id),))
        rol = cur.fetchone()
        if not rol:
            raise ValueError("no_existe")
        cur.execute(
            "INSERT IGNORE INTO usuario_area_roles (usuario_id, rol_id)"
            " VALUES (%s, %s)",
            (int(usuario_id), int(rol["id"])),
        )
        asignado = (cur.rowcount or 0) > 0
        conn.commit()
    return {"rol_id": int(rol["id"]), "rol": rol["nombre"],
            "area_id": int(area_id), "asignado": asignado}


def retirar_rol_area(area_id: int, usuario_id: int) -> bool:
    with conectar() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE uer FROM usuario_area_roles uer"
            " JOIN roles_area r ON r.id = uer.rol_id"
            " WHERE uer.usuario_id = %s AND r.area_id = %s",
            (int(usuario_id), int(area_id)),
        )
        conn.commit()
        return (cur.rowcount or 0) > 0


def eliminar_paciente_area(area_id: int, paciente_id: int) -> bool:
    """Borra UN registro de la tabla de la especialidad (solo admin/dev desde
    el endpoint). También borra sus filas de log propias (aisladas por tabla);
    el resto del historial no se toca."""
    esp = obtener_area(int(area_id))
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
                "DELETE FROM paciente_csv_accesos WHERE tabla_pacientes = %s AND paciente_id = %s",
                (tabla, int(paciente_id)),
            )
            cur.execute(
                "DELETE FROM log_envios WHERE paciente_id = %s AND tabla_pacientes = %s",
                (int(paciente_id), tabla),
            )
        conn.commit()
        return borrado


def eliminar_tabla_area(area_id: int) -> dict:
    """Elimina la especialidad entera (solo admin/dev desde el endpoint):
    tabla física, rol, asignaciones y fila de especialidad. El historial de
    envíos (`envios`/`log_envios`) se conserva como trazabilidad."""
    esp = obtener_area(int(area_id))
    if not esp:
        raise ValueError("no_existe")
    tabla = esp["nombre_tabla_base"]
    if not tabla_valida(tabla):
        raise ValueError("tabla_invalida")
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM roles_area WHERE area_id = %s",
                    (int(area_id),))
        rol = cur.fetchone()
        if rol:
            cur.execute("DELETE FROM usuario_area_roles WHERE rol_id = %s",
                        (int(rol["id"]),))
            cur.execute("DELETE FROM roles_area WHERE id = %s", (int(rol["id"]),))
        cur.execute("DELETE FROM paciente_csv_accesos WHERE tabla_pacientes = %s", (tabla,))
        if _tabla_fisica_existe(cur, tabla):
            cur.execute(f"DROP TABLE {tabla}")
        cur.execute("DELETE FROM areas WHERE id = %s", (int(area_id),))
        conn.commit()
    return {"id": int(area_id), "nombre_visible": esp["nombre_visible"],
            "nombre_tabla_base": tabla}


def vaciar_tabla_area(area_id: int) -> dict:
    """Borra SOLO los pacientes de la tabla de la area (los datos que se
    cargaron), sin eliminar la tabla física, el rol, las asignaciones ni el
    registro de la area. Como el borrado de un paciente, también limpia el
    log de esa tabla (IDs huérfanos); la trazabilidad masiva (`envios`) se
    conserva."""
    esp = obtener_area(int(area_id))
    if not esp:
        raise ValueError("no_existe")
    tabla = esp["nombre_tabla_base"]
    if not tabla_valida(tabla):
        raise ValueError("tabla_invalida")
    with conectar() as conn, conn.cursor() as cur:
        if not _tabla_fisica_existe(cur, tabla):
            raise ValueError("no_existe")
        cur.execute(f"DELETE FROM {tabla}")
        vaciados = (cur.rowcount or 0)
        cur.execute("DELETE FROM paciente_csv_accesos WHERE tabla_pacientes = %s", (tabla,))
        cur.execute("DELETE FROM log_envios WHERE tabla_pacientes = %s", (tabla,))
        conn.commit()
    return {"id": int(area_id), "nombre_visible": esp["nombre_visible"],
            "nombre_tabla_base": tabla, "pacientes_eliminados": vaciados}


# --- El mismo paciente en varias bases --------------------------------------
# El numero es lo que identifica a una persona: el mismo telefono puede estar
# en `pacientes_dev`, en `pacientes_prod` y en la tabla de cada area (el mismo
# paciente asiste a dos areas). Por eso una baja, o lo que llega en un CSV,
# se propaga a TODAS las bases donde aparezca ese numero normalizado: si no, el
# sistema seguiria mandando mensajes a alguien que ya dijo que no, solo que
# estuviera cargado en otra base.

# Valores canonicos de `respuesta` (ver expr_respuesta_efectiva en main.py):
# 'baja' = Se dio de baja, 'respondio' = Respondio, 'pendiente' = Sin respuesta.
RESPUESTAS = ("baja", "respondio", "pendiente")
ESTADOS = ("pendiente", "enviado")

# Acepta como viene en un CSV ("Se dio de baja", "respondió", "sin respuesta").
_ALIAS_RESPUESTA = {
    "baja": "baja", "se dio de baja": "baja", "dado de baja": "baja",
    "dio de baja": "baja", "baja explicita": "baja", "opt out": "baja", "optout": "baja",
    "respondio": "respondio", "con respuesta": "respondio",
    "pendiente": "pendiente", "sin respuesta": "pendiente", "sin respuestas": "pendiente",
    "nunca respondio": "pendiente", "no respondio": "pendiente",
}
_ALIAS_ESTADO = {
    "pendiente": "pendiente", "enviado": "enviado",
}


def _sin_acentos(t: str) -> str:
    n = unicodedata.normalize("NFD", (t or "").strip().lower())
    n = "".join(c for c in n if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", n)).strip()


def normalizar_respuesta_csv(bruto: str | None) -> str | None:
    """Texto de la columna `respuesta` de un CSV -> 'baja' | 'respondio' |
    'pendiente'. None si el texto no corresponde a ninguno (se rechaza la fila)."""
    if bruto is None:
        return None
    return _ALIAS_RESPUESTA.get(_sin_acentos(bruto))


def normalizar_estado_csv(bruto: str | None) -> str | None:
    """Texto de la columna `estado` de un CSV -> 'pendiente' | 'enviado' |
    None si el texto no corresponde a ninguno."""
    if bruto is None:
        return None
    return _ALIAS_ESTADO.get(_sin_acentos(bruto))


def tablas_de_pacientes() -> list[str]:
    """Todas las bases de pacientes del sistema: desarrollo, produccion y la
    tabla de cada area, sin repetir."""
    return list(dict.fromkeys(
        (tabla_pacientes("desarrollo"), tabla_pacientes("produccion"))
        + tuple(r["nombre_tabla_base"] for r in listar_tablas_areas())
    ))


def indice_pacientes_por_telefono(cur, ambiente: str | None = None) -> dict[str, dict[str, list[int]]]:
    """Índice de pacientes de TODAS las tablas por teléfono canónico.

    Normaliza también el valor guardado en la tabla, no solo el del CSV: así
    se emparejan números con espacios, guiones, paréntesis o prefijo distinto.
    Se puede reutilizar el índice durante toda una importación CSV.
    """
    indice: dict[str, dict[str, list[int]]] = {}
    for tabla in tablas_de_pacientes():
        if not tabla_valida(tabla):
            continue
        try:
            cur.execute(f"SELECT id, telefono FROM {tabla}")
            filas = cur.fetchall()
        except Exception:
            continue  # tabla aún no creada o sin el esquema esperado
        for fila in filas:
            telefono = normalizar_telefono(fila.get("telefono") or "")
            if telefono:
                indice.setdefault(telefono, {}).setdefault(tabla, []).append(fila["id"])
    return indice


def _cond_log_tabla(tabla: str, ambiente: str | None = None) -> tuple[str, tuple]:
    """Aisla las filas de log_envios por tabla de origen (las legacy no la
    tienen siempre puesta)."""
    if not columna_existe("log_envios", "tabla_pacientes", ambiente):
        return "", ()
    if tabla in ("pacientes_dev", "pacientes_prod"):
        return "AND COALESCE(tabla_pacientes, '') IN ('', %s)", (tabla,)
    return "AND tabla_pacientes = %s", (tabla,)


def _baja_activa(cur, tabla: str, ids: list[int], ambiente: str | None = None) -> bool:
    """True si alguna copia del paciente está actualmente dada de baja."""
    if not ids or not columna_existe(tabla, "whatsapp_opt_out", ambiente):
        return False
    cur.execute(
        f"SELECT id FROM {tabla} WHERE id IN ({', '.join('%s' for _ in ids)})"
        " AND whatsapp_opt_out = 1",
        tuple(ids),
    )
    return bool(cur.fetchall())


def _baja_con_candado(cur, tabla: str, ids: list[int], ambiente: str | None = None) -> bool:
    """True si alguna de esas filas pidió la baja con sus propias palabras por
    WhatsApp: esa respuesta no se toca a mano ni con un CSV."""
    if not ids or not columna_existe(tabla, "opt_out_explicito", ambiente) \
            or not columna_existe(tabla, "whatsapp_opt_out", ambiente):
        return False
    cur.execute(
        f"SELECT id FROM {tabla} WHERE id IN ({', '.join('%s' for _ in ids)})"
        " AND whatsapp_opt_out = 1 AND opt_out_explicito = 1",
        tuple(ids),
    )
    return bool(cur.fetchall())


def aplicar_en_tabla(cur, tabla: str, ids: list[int], respuesta: str | None = None,
                     estado: str | None = None, ambiente: str | None = None,
                     candado: bool = False) -> int:
    """Escribe respuesta/estado en las filas dadas de una tabla y devuelve
    cuantas quedaron cambiadas. No toca el candado: el bloqueo de una baja
    pedida por el paciente lo decide el webhook, no este ajuste."""
    if not ids or (not respuesta and not estado):
        return 0
    sets: list[str] = []
    params: list = []
    if estado:
        if columna_existe(tabla, "estado", ambiente):
            sets.append("estado = %s")
            params.append(estado)
    if respuesta in RESPUESTAS:
        if columna_existe(tabla, "whatsapp_opt_out", ambiente):
            sets.append("whatsapp_opt_out = %s")
            params.append(1 if respuesta == "baja" else 0)
        if columna_existe(tabla, "respuesta_manual", ambiente):
            sets.append("respuesta_manual = %s")
            params.append(respuesta)
        if respuesta == "baja":
            # La baja se lleva tambien el interes que tuviera.
            for col in ("interesado", "no_interesado"):
                if columna_existe(tabla, col, ambiente):
                    sets.append(f"{col} = 0")
            if candado and columna_existe(tabla, "opt_out_explicito", ambiente):
                sets.append("opt_out_explicito = 1")
        if respuesta == "respondio" and columna_existe(tabla, "ultimo_reintegro", ambiente):
            # Volvio a escribir: queda registrada la fecha del regreso.
            sets.append("ultimo_reintegro = NOW()")
    if not sets:
        return 0
    ph = ", ".join("%s" for _ in ids)
    cur.execute(f"UPDATE {tabla} SET {', '.join(sets)} WHERE id IN ({ph})",
                (*params, *ids))
    # Al volver a 'Sin respuesta' se borra la señal 'baja' que el webhook dejó
    # pegada en el ultimo envio (mismo criterio que el ajuste manual).
    if respuesta == "pendiente" and columna_existe("log_envios", "respuesta", ambiente):
        cond, args = _cond_log_tabla(tabla, ambiente)
        cur.execute(
            f"UPDATE log_envios SET respuesta = 'pendiente' WHERE paciente_id IN ({ph})"
            f" AND respuesta = 'baja' {cond}",
            (*ids, *args),
        )
    return len(ids)


def propagar_a_todas_las_bases(cur, telefono: str, respuesta: str | None = None,
                               estado: str | None = None, excluir: str | None = None,
                               ambiente: str | None = None,
                               indice: dict[str, dict[str, list[int]]] | None = None,
                               candado: bool = False) -> dict:
    """Aplica respuesta/estado al paciente con ese numero en TODAS las bases
    donde aparezca, menos `excluir` (la que el llamador ya actualizó).

    Devuelve el detalle por base (para poder informarlo) y cuantos quedaron
    afuera porque su baja tiene candado.
    """
    detalle: list[dict] = []
    omitidos = 0
    if not respuesta and not estado:
        return {"bases": detalle, "omitidos": omitidos}
    indice = indice if indice is not None else indice_pacientes_por_telefono(cur, ambiente)
    numero = normalizar_telefono(telefono or "")
    if not numero:
        return {"bases": detalle, "omitidos": omitidos}
    coincidencias = indice.get(numero, {})

    # El candado pertenece al paciente, no a una copia de su ficha. Si alguna
    # base conserva una baja explícita, no se la sobreescribe en ninguna otra;
    # el estado (pendiente/enviado) sí se puede sincronizar.
    bloqueo_global = False
    ids_bloqueados = 0
    if respuesta in ("pendiente", "respondio"):
        for t, ids in coincidencias.items():
            if _baja_con_candado(cur, t, ids, ambiente):
                bloqueo_global = True
                ids_bloqueados += len(ids)
        if bloqueo_global:
            omitidos = ids_bloqueados
            respuesta = None

    for t, ids in coincidencias.items():
        if t == excluir:
            continue
        if not ids:
            continue
        try:
            n = aplicar_en_tabla(cur, t, ids, respuesta, estado, ambiente, candado)
        except Exception as e:
            log_error(f"propagar_a_todas_las_bases({t})", e)
            continue
        if n:
            detalle.append({"tabla": t, "actualizados": n})
    return {"bases": detalle, "omitidos": omitidos}


def nombre_de_tabla(tabla: str) -> str:
    """Nombre visible de la base (área) o la tabla si es legacy."""
    esp = area_por_tabla(tabla)
    return esp["nombre_visible"] if esp else tabla


def importar_pacientes_csv(area_id: int, datos: bytes, usuario_id: int) -> dict:
    """Valida un CSV y lo carga en la tabla de la especialidad.

    Columnas obligatorias: `nombre`, `apellido`, `telefono` (UTF-8).
    Opcionales: `respuesta` (Se dio de baja / Respondió / Sin respuesta) y
    `estado` (pendiente / enviado). Teléfonos normalizados con
    `telefono.py` (`+569XXXXXXXX`).

    Los pacientes que ya estaban en la base se actualizan con la respuesta y el
    estado del CSV, y lo mismo se aplica en TODAS las demás bases donde exista
    ese número (misma persona, otra especialidad): así una baja no deja al
    paciente con respuestas distintas según dónde se mire.
    """
    esp = obtener_area(int(area_id))
    if not esp:
        raise ValueError("no_existe")
    tabla = esp["nombre_tabla_base"]
    if not tabla_valida(tabla):
        raise ValueError("tabla_invalida")
    if not usuario_id:
        raise ValueError("usuario_invalido")
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
    col_respuesta = next((c for c in ("respuesta", "respuesta_paciente", "estado_respuesta")
                          if c in cols), None)
    col_estado = next((c for c in ("estado", "estado_paciente", "estado_envio")
                       if c in cols), None)

    with conectar() as conn, conn.cursor() as cur:
        indice = indice_pacientes_por_telefono(cur)
        existentes = {
            tel: por_tabla.get(tabla, [])
            for tel, por_tabla in indice.items()
            if por_tabla.get(tabla)
        }

        procesados = insertados = duplicados = actualizados = 0
        propagados = 0
        omitidos = 0
        errores: list[dict] = []
        bases: dict[str, int] = {}
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
            # Respuesta y estado del CSV (si vienen). Sin esas columnas no se
            # toca nada: el import es solo una carga de pacientes.
            respuesta = estado = None
            if col_respuesta:
                texto = (fila.get(col_respuesta) or "").strip()
                if texto:
                    respuesta = normalizar_respuesta_csv(texto)
                    if respuesta is None:
                        errores.append({"fila": nro, "motivo":
                                        f"Respuesta inválida: '{texto}'. Use: Se dio de baja, "
                                        f"Respondió o Sin respuesta"})
                        continue
            if col_estado:
                texto = (fila.get(col_estado) or "").strip()
                if texto:
                    estado = normalizar_estado_csv(texto)
                    if estado is None:
                        errores.append({"fila": nro, "motivo":
                                        f"Estado inválido: '{texto}'. Use: pendiente, "
                                        f"enviado"})
                        continue

            # Un número repetido dentro del archivo se procesa una sola vez.
            if telefono in vistos_archivo:
                duplicados += 1
                continue

            ids_objetivo = existentes.get(telefono, [])
            # Si otra copia ya está de baja, esa copia es la fuente de verdad:
            # una baja manual se replica sin candado y una baja explícita se
            # replica con candado, aunque el CSV no traiga columna respuesta.
            respuesta_aplicar = respuesta
            candado_aplicar = False
            copias_baja = [
                (t, ids) for t, ids in indice.get(telefono, {}).items()
                if _baja_activa(cur, t, ids)
            ]
            if copias_baja:
                candado_ids = sum(
                    len(ids) for t, ids in copias_baja
                    if _baja_con_candado(cur, t, ids)
                )
                candado_aplicar = candado_ids > 0
                if candado_ids and respuesta != "baja":
                    omitidos += candado_ids
                respuesta_aplicar = "baja"

            if ids_objetivo:
                if aplicar_en_tabla(cur, tabla, ids_objetivo, respuesta_aplicar, estado,
                                    candado=candado_aplicar):
                    actualizados += len(ids_objetivo)
                duplicados += 1
            else:
                cur.execute(
                    f"INSERT INTO {tabla} (nombre, apellido, telefono, estado,"
                    " whatsapp_opt_out, interesado, opt_out_explicito)"
                    " VALUES (%s, %s, %s, 'pendiente', 0, 0, 0)",
                    (nombre[:150], apellido[:150], telefono),
                )
                pid = cur.lastrowid
                ids_objetivo = [pid]
                existentes[telefono] = ids_objetivo
                indice.setdefault(telefono, {})[tabla] = ids_objetivo
                aplicar_en_tabla(cur, tabla, ids_objetivo, respuesta_aplicar, estado,
                                 candado=candado_aplicar)
                insertados += 1
            cur.executemany(
                "INSERT IGNORE INTO paciente_csv_accesos"
                " (tabla_pacientes, paciente_id, usuario_id) VALUES (%s, %s, %s)",
                [(tabla, pid, int(usuario_id)) for pid in ids_objetivo],
            )
            vistos_archivo.add(telefono)
            # Y lo mismo en las demás bases donde esté el número.
            if respuesta_aplicar or estado:
                res = propagar_a_todas_las_bases(
                    cur, telefono, respuesta_aplicar, estado, excluir=tabla, indice=indice,
                    candado=candado_aplicar)
                for b in res["bases"]:
                    bases[b["tabla"]] = bases.get(b["tabla"], 0) + b["actualizados"]
                    propagados += b["actualizados"]
                omitidos += res["omitidos"]
        conn.commit()

    detalle = [{"tabla": t, "nombre": nombre_de_tabla(t), "actualizados": n}
               for t, n in sorted(bases.items())]
    return {"procesados": procesados, "insertados": insertados,
            "duplicados": duplicados, "actualizados": actualizados,
            "propagados": propagados, "omitidos_candado": omitidos,
            "bases": detalle,
            "rechazados": len(errores), "errores": errores}


def listar_tablas_areas() -> list[dict]:
    """[{id, nombre_tabla_base}] para construir listas blancas (webhook, etc.)."""
    try:
        with conectar() as conn, conn.cursor() as cur:
            cur.execute("SELECT id, nombre_tabla_base FROM areas ORDER BY id")
            return cur.fetchall()
    except Exception as e:
        log_error("listar_tablas_areas", e)
        return []


def ids_de_areas() -> list[int]:
    return [int(r["id"]) for r in listar_tablas_areas()]


def area_por_tabla(tabla: str) -> dict | None:
    """Especialidad registrada para una tabla física (None si no es de
    especialidad: tablas legacy u otras)."""
    if not tabla_valida(tabla):
        return None
    try:
        with conectar() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT e.id, e.nombre_visible, e.nombre_tabla_base,"
                "       r.id AS rol_id, r.nombre AS rol_nombre"
                " FROM areas e"
                " LEFT JOIN roles_area r ON r.area_id = e.id"
                " WHERE e.nombre_tabla_base = %s",
                (tabla,),
            )
            return cur.fetchone()
    except Exception as e:
        log_error("area_por_tabla", e)
        return None


def areas_ids_de_usuario(usuario_id: int) -> list[int]:
    return [int(e["id"]) for e in areas_de_usuario(usuario_id)]


def areas_por_usuarios() -> dict:
    """{login: [{id, nombre_visible}]} para enriquecer el listado de cuentas."""
    try:
        with conectar() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT u.usuario, e.id, e.nombre_visible"
                " FROM usuario_area_roles uer"
                " JOIN roles_area r ON r.id = uer.rol_id"
                " JOIN areas e ON e.id = r.area_id"
                " JOIN usuarios u ON u.id = uer.usuario_id"
                " ORDER BY u.usuario, e.nombre_visible"
            )
            out: dict = {}
            for r in cur.fetchall():
                out.setdefault(r["usuario"], []).append(
                    {"id": int(r["id"]), "nombre_visible": r["nombre_visible"]})
            return out
    except Exception as e:
        log_error("areas_por_usuarios", e)
        return {}


def correos_supervisores_area(area_id: int) -> list[dict]:
    """Supervisores activos asignados a la especialidad que tienen un correo
    contactable (login si es correo, si no el de recuperación). Se usa para
    avisarles las solicitudes de envío en producción de su especialidad."""
    try:
        with conectar() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT u.usuario, u.nombre, u.correo_recuperacion"
                " FROM usuario_area_roles uer"
                " JOIN roles_area r ON r.id = uer.rol_id"
                " JOIN usuarios u ON u.id = uer.usuario_id"
                " WHERE r.area_id = %s AND u.rol = 'supervisor' AND u.activo = 1",
                (int(area_id),),
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
        log_error("correos_supervisores_area", e)
        return []
