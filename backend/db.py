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
# TODA la configuración vive en la tabla `configuracion` (clave/valor).
# En .env solo quedan las credenciales de la BASE DE DATOS (DB_*), porque se
# necesitan para conectarse a la base donde vive la tabla. La primera vez que
# arranca el backend, la tabla se siembra con lo que hubiera en .env.
# ---------------------------------------------------------------------------
CONFIG_DEFAULTS = {
    # App / envío
    "entorno": "desarrollo",
    "metodo_envio": "simulado",
    "numeros_prueba_dev": "",
    "numeros_prueba_prod": "",
    "intervalo_ms": "1000",
    "url_base": "",
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
}
# Clave en la tabla -> variable de .env de la que se migra la primera vez.
_CONFIG_ENV_LEGACY = {
    "entorno": "SNW_ENTORNO",
    "metodo_envio": "SNW_METODO_ENVIO",
    "numeros_prueba_dev": "SNW_NUMEROS_PRUEBA_DEV",
    "numeros_prueba_prod": "SNW_NUMEROS_PRUEBA_PROD",
    "intervalo_ms": "SNW_INTERVALO_MS",
    "url_base": "SNW_URL_BASE",
    "smtp_host": "SMTP_HOST",
    "smtp_port": "SMTP_PORT",
    "smtp_user": "SMTP_USER",
    "smtp_pass": "SMTP_PASS",
    "smtp_tls": "SMTP_TLS",
    "correo_emisor": "DIRECCION_CORREO_EMISOR",
    "correo_destino": "DIRECCION_CORREO_DESTINO",
    "wa_token": "SNW_WA_TOKEN",
    "wa_phone_id": "SNW_WA_PHONE_ID",
    "wa_business_account_id": "SNW_WA_BUSINESS_ACCOUNT_ID",
    "wa_verify_token": "SNW_WA_VERIFY_TOKEN",
    "wa_template_nombre": "SNW_WA_TEMPLATE_NOMBRE",
    "wa_template_lang": "SNW_WA_TEMPLATE_LANG",
    "wa_webhook_path": "SNW_WHATSAPP_WEBHOOK_PATH",
    "wa_graph_version": "SNW_WA_GRAPH_VERSION",
}

_config_cache: dict | None = None


def _config_semilla() -> dict:
    """Valores por defecto mezclados con lo que haya en .env (compatibilidad)."""
    valores = dict(CONFIG_DEFAULTS)
    for clave, env in _CONFIG_ENV_LEGACY.items():
        v = os.getenv(env)
        if v is not None and v.strip() != "":
            valores[clave] = v.strip()
    return valores


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
            for clave, valor in _config_semilla().items():
                cur.execute(
                    "INSERT IGNORE INTO configuracion (clave, valor) VALUES (%s, %s)",
                    (clave, valor),
                )
            conn.commit()
    except Exception as e:
        log_error("asegurar_tabla_config", e)
    _config_cache = None


def _cargar_config() -> dict:
    valores = _config_semilla()
    try:
        with conectar() as conn, conn.cursor() as cur:
            cur.execute("SELECT clave, valor FROM configuracion")
            for row in cur.fetchall():
                valores[row["clave"]] = "" if row["valor"] is None else row["valor"]
    except Exception as e:
        # 1146 = la tabla no existe todavía (primer arranque): se usa .env
        # como respaldo y asegurar_tabla_config() la creará enseguida.
        if getattr(e, "args", [None])[0] != 1146:
            log_error("_cargar_config (se usa .env como respaldo)", e)
    return valores


def config_all() -> dict:
    global _config_cache
    if _config_cache is None:
        _config_cache = _cargar_config()
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
                _columnas_cache[clave] = {f["COLUMN_NAME"] for f in cur.fetchall()}
        except Exception as e:
            log_error(f"columnas_tabla({tabla!r}, {entorno!r})", e)
            _columnas_cache[clave] = set()
    return _columnas_cache[clave]


def columna_existe(tabla: str, columna: str, entorno: str | None = None) -> bool:
    """Devuelve True si la columna existe en la tabla (cacheado)."""
    return columna in columnas_tabla(tabla, entorno)
