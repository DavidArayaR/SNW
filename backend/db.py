import os
import sys
import traceback
from pathlib import Path

import pymysql
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def log_error(contexto: str, exc: BaseException | None = None) -> None:
    """Registra en consola (stderr) cualquier error, con su traza completa.

    Se usa en todos los bloques try/except del backend para que ningún
    fallo quede silenciado, aunque el flujo continúe."""
    print(f"[ERROR] {contexto}: {exc!r}" if exc is not None else f"[ERROR] {contexto}",
          file=sys.stderr, flush=True)
    if exc is not None and exc.__traceback__ is not None:
        traceback.print_exception(type(exc), exc, exc.__traceback__)
        sys.stderr.flush()

AMBIENTES = {"desarrollo", "produccion"}
TABLAS_PACIENTES = {"desarrollo": "pacientes_dev", "produccion": "pacientes_prod"}


# ---------------------------------------------------------------------------
# TODA la configuración vive en la tabla `configuracion` (clave/valor) y se
# edita desde la página de Configuración. En .env solo quedan las credenciales
# de la BASE DE DATOS (DB_*), porque se necesitan para conectarse a la base
# donde vive la tabla. La primera vez que arranca el backend, la tabla se
# siembra con estos valores por defecto.
# ---------------------------------------------------------------------------
CONFIG_DEFAULTS = {
    # App / envío
    "entorno": "desarrollo",
    "metodo_envio": "simulado",
    "numeros_prueba_dev": "",
    "numeros_prueba_prod": "",
    "intervalo_ms": "1000",
    "url_base": "",
    # Call center: uno o varios números (solo dígitos, con código de país,
    # separados por coma) a los que lleva el botón de las plantillas de call
    # center, y segundos de espera antes del envío automático cuando un paciente
    # muestra interés (0 = desactivado).
    "call_center_numeros": "",
    "call_center_auto_segundos": "10",
    # Correo (confirmación de envíos en producción)
    "smtp_host": "",
    "smtp_port": "587",
    "smtp_user": "",
    "smtp_pass": "",
    "smtp_tls": "true",
    "correo_emisor": "",
    "correo_destino": "",
    # WhatsApp Business Cloud API (Meta)
    "wa_token": "",
    "wa_phone_id": "",
    "wa_business_account_id": "",
    "wa_verify_token": "",
    "wa_template_nombre": "",
    "wa_template_lang": "es",
    "wa_webhook_path": "/api/whatsapp/webhook",
    "wa_graph_version": "v26.0",
    "wa_moneda": "USD",
}

_config_cache: dict | None = None


def asegurar_tabla_config() -> None:
    """Crea la tabla `configuracion` si no existe y la siembra una sola vez."""
    global _config_cache
    try:
        with conectar() as conn, conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS configuracion ("
                "  clave VARCHAR(60) PRIMARY KEY,"
                "  valor TEXT,"
                "  actualizada DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"
                ") CHARACTER SET utf8mb4"
            )
            for clave, valor in CONFIG_DEFAULTS.items():
                cur.execute(
                    "INSERT IGNORE INTO configuracion (clave, valor) VALUES (%s, %s)",
                    (clave, valor),
                )
            # Rate card de WhatsApp (tarifas por mensaje descargadas de Meta).
            cur.execute(
                "CREATE TABLE IF NOT EXISTS tarifas_whatsapp ("
                "  id INT AUTO_INCREMENT PRIMARY KEY,"
                "  pais VARCHAR(60) NOT NULL DEFAULT 'Chile',"
                "  moneda VARCHAR(8) DEFAULT 'USD',"
                "  marketing DECIMAL(12,6) NULL,"
                "  utility DECIMAL(12,6) NULL,"
                "  authentication DECIMAL(12,6) NULL,"
                "  service DECIMAL(12,6) NULL,"
                "  efectiva_desde DATE NULL,"
                "  hash CHAR(64) NOT NULL,"
                "  fuente VARCHAR(255) NULL,"
                "  csv_texto MEDIUMTEXT NULL,"
                "  descargada DATETIME DEFAULT CURRENT_TIMESTAMP,"
                "  UNIQUE KEY uq_hash (hash)"
                ") CHARACTER SET utf8mb4"
            )
            # Corrección manual de la respuesta del paciente: gana sobre la
            # señal automática 'pegajosa'. NULL = sin corrección.
            # `interesado`: el paciente mostró interés real ("me interesa", "quiero
            # agendar"…). Se marca solo por webhook o a mano; sobreescribe una baja.
            for tp in ("pacientes_dev", "pacientes_prod"):
                cur.execute(
                    "SELECT"
                    "  (SELECT COUNT(*) FROM information_schema.TABLES"
                    "     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s) AS tabla,"
                    "  (SELECT COUNT(*) FROM information_schema.COLUMNS"
                    "     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s"
                    "       AND COLUMN_NAME = 'respuesta_manual') AS col,"
                    "  (SELECT COUNT(*) FROM information_schema.COLUMNS"
                    "     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s"
                    "       AND COLUMN_NAME = 'interesado') AS col_int",
                    (tp, tp, tp),
                )
                fila = cur.fetchone() or {}
                if not fila.get("tabla"):
                    continue
                if not fila.get("col"):
                    cur.execute(f"ALTER TABLE {tp} ADD COLUMN respuesta_manual VARCHAR(12) NULL")
                    # Traspasa las correcciones manuales antiguas (que antes se
                    # guardaban como filas log 'ajuste_manual') a la nueva columna.
                    cur.execute(
                        f"UPDATE {tp} p SET p.respuesta_manual = ("
                        "  SELECT le.respuesta FROM log_envios le"
                        "  WHERE le.paciente_id = p.id AND le.plantilla_clave = 'ajuste_manual'"
                        "  ORDER BY le.id DESC LIMIT 1)"
                        " WHERE EXISTS (SELECT 1 FROM log_envios le2"
                        "  WHERE le2.paciente_id = p.id AND le2.plantilla_clave = 'ajuste_manual')"
                    )
                if not fila.get("col_int"):
                    cur.execute(f"ALTER TABLE {tp} ADD COLUMN interesado TINYINT(1) NOT NULL DEFAULT 0")
                cur.execute(f"UPDATE {tp} SET respuesta_manual = 'respondio' WHERE respuesta_manual = 'click'")

            # El tipo de respuesta 'click' se eliminó: quita el valor del ENUM
            # de log_envios.respuesta (una sola vez) y migra las filas antiguas.
            cur.execute(
                "SELECT COLUMN_TYPE AS t FROM information_schema.COLUMNS"
                " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'log_envios'"
                "   AND COLUMN_NAME = 'respuesta'"
            )
            ct = (cur.fetchone() or {}).get("t", "")
            if "'click'" in ct:
                cur.execute("UPDATE log_envios SET respuesta = 'respondio' WHERE respuesta = 'click'")
                cur.execute(
                    "ALTER TABLE log_envios MODIFY COLUMN respuesta"
                    " ENUM('pendiente','respondio','baja') DEFAULT 'pendiente'"
                )

            # Un envío rechazado por el supervisor queda en el historial con su
            # comentario, en vez de borrarse.
            cur.execute(
                "SELECT"
                "  (SELECT COUNT(*) FROM information_schema.TABLES"
                "     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'envios') AS tabla,"
                "  (SELECT COLUMN_TYPE FROM information_schema.COLUMNS"
                "     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'envios' AND COLUMN_NAME = 'estado') AS estado_tipo,"
                "  (SELECT COUNT(*) FROM information_schema.COLUMNS"
                "     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'envios' AND COLUMN_NAME = 'comentario') AS tiene_com"
            )
            e = cur.fetchone() or {}
            if e.get("tabla"):
                if "'rechazado'" not in (e.get("estado_tipo") or ""):
                    cur.execute(
                        "ALTER TABLE envios MODIFY COLUMN estado"
                        " ENUM('completado','cancelado','rechazado') NOT NULL DEFAULT 'completado'"
                    )
                if not e.get("tiene_com"):
                    cur.execute("ALTER TABLE envios ADD COLUMN comentario VARCHAR(255) NULL")

            # `call_center_numero` (un solo número) pasó a `call_center_numeros`
            # (uno o varios, separados por coma). Se traspasa una vez.
            cur.execute("SELECT clave, valor FROM configuracion WHERE clave IN ('call_center_numero', 'call_center_numeros')")
            cc = {r["clave"]: (r["valor"] or "") for r in cur.fetchall()}
            if cc.get("call_center_numero") and not cc.get("call_center_numeros"):
                cur.execute(
                    "INSERT INTO configuracion (clave, valor) VALUES ('call_center_numeros', %s)"
                    " ON DUPLICATE KEY UPDATE valor = VALUES(valor)",
                    (cc["call_center_numero"],),
                )
            cur.execute("DELETE FROM configuracion WHERE clave = 'call_center_numero'")

            conn.commit()
    except Exception as e:
        log_error("asegurar_tabla_config", e)
    _config_cache = None
    _columnas_cache.clear()


def _cargar_config() -> tuple[dict, bool]:
    """Devuelve (valores, ok). ok=False si no se pudo leer la tabla (BD caída),
    para no cachear valores por defecto de forma permanente."""
    valores = dict(CONFIG_DEFAULTS)
    try:
        with conectar() as conn, conn.cursor() as cur:
            cur.execute("SELECT clave, valor FROM configuracion")
            for row in cur.fetchall():
                valores[row["clave"]] = "" if row["valor"] is None else row["valor"]
        return valores, True
    except Exception as e:
        # 1146 = la tabla no existe todavía (primer arranque): se usan los
        # valores por defecto y asegurar_tabla_config() la creará enseguida.
        if getattr(e, "args", [None])[0] != 1146:
            log_error("_cargar_config (se usan los valores por defecto)", e)
        return valores, False


def config_all() -> dict:
    global _config_cache
    if _config_cache is None:
        valores, ok = _cargar_config()
        # Solo se cachea una lectura real; si la BD falló se reintenta luego.
        if ok:
            _config_cache = valores
        return dict(valores)
    return dict(_config_cache)


def config_get(clave: str, default: str = "") -> str:
    return config_all().get(clave, default)


def config_set(cambios: dict) -> None:
    """Upsert de una o varias claves en la tabla `configuracion`."""
    global _config_cache
    with conectar() as conn, conn.cursor() as cur:
        for clave, valor in cambios.items():
            cur.execute(
                "INSERT INTO configuracion (clave, valor) VALUES (%s, %s)"
                " ON DUPLICATE KEY UPDATE valor = VALUES(valor)",
                (clave, "" if valor is None else str(valor)),
            )
        conn.commit()
    _config_cache = None


def entorno_valido(entorno: str | None = None) -> str:
    ent = (entorno or config_get("entorno", "desarrollo")).strip().lower()
    if ent not in AMBIENTES:
        raise ValueError(f"Entorno desconocido: '{ent}'")
    return ent


def tabla_pacientes(entorno: str | None = None) -> str:
    """Nombre de la tabla de pacientes según el entorno (una única base)."""
    return TABLAS_PACIENTES[entorno_valido(entorno)]


def conectar(entorno: str | None = None):
    return pymysql.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PUERTO", "3306")),
        user=os.getenv("DB_USUARIO", "root"),
        password=os.getenv("DB_CONTRASENA", ""),
        database=os.getenv("DB_NOMBRE", "snw_base"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def nombre_base(entorno: str | None = None) -> str:
    # Identificador de entorno usado como 'base_datos' en envios y config:
    # devuelve la tabla de pacientes ('pacientes_dev' / 'pacientes_prod'),
    # que es lo que permite al frontend distinguir desarrollo de producción.
    return tabla_pacientes(entorno)


_columnas_cache: dict = {}


def columnas_tabla(tabla: str, entorno: str | None = None) -> set:
    """Devuelve el conjunto de columnas reales de una tabla (con caché).

    Permite que el backend se adapte a bases con esquema mínimo, evitando
    fallos al referenciar columnas que no existen.
    """
    clave = f"{entorno_valido(entorno)}:{tabla}"
    if clave not in _columnas_cache:
        try:
            with conectar(entorno) as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT COLUMN_NAME FROM information_schema.COLUMNS"
                    " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
                    (tabla,),
                )
                cols = {f["COLUMN_NAME"] for f in cur.fetchall()}
            # Solo se cachea un resultado real. Si la consulta falla (BD caída)
            # o la tabla aún no existe, se reintenta en la próxima llamada en
            # vez de dejar la caché envenenada con un conjunto vacío.
            if cols:
                _columnas_cache[clave] = cols
            return cols
        except Exception as e:
            log_error(f"columnas_tabla({tabla!r}, {entorno!r})", e)
            return set()
    return _columnas_cache[clave]


def columna_existe(tabla: str, columna: str, entorno: str | None = None) -> bool:
    """Devuelve True si la columna existe en la tabla (cacheado)."""
    return columna in columnas_tabla(tabla, entorno)
