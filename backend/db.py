import os
from pathlib import Path

import pymysql
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

PREFIJOS = {"desarrollo": "DB_DEV_", "produccion": "DB_PROD_"}


def entorno_valido(entorno: str | None) -> str:
    ent = (entorno or os.getenv("SNW_ENTORNO", "desarrollo")).strip().lower()
    if ent not in PREFIJOS:
        raise ValueError(f"Entorno desconocido: '{ent}'")
    return ent


def conectar(entorno: str | None = None):
    prefijo = PREFIJOS[entorno_valido(entorno)]
    return pymysql.connect(
        host=os.getenv(prefijo + "HOST", "127.0.0.1"),
        port=int(os.getenv(prefijo + "PUERTO", "3306")),
        user=os.getenv(prefijo + "USUARIO", "root"),
        password=os.getenv(prefijo + "CONTRASENA", ""),
        database=os.getenv(prefijo + "NOMBRE", ""),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def nombre_base(entorno: str | None = None) -> str:
    prefijo = PREFIJOS[entorno_valido(entorno)]
    return os.getenv(prefijo + "NOMBRE", "")


_columnas_cache: dict = {}


def columnas_tabla(tabla: str, entorno: str | None = None) -> set:
    """Devuelve el conjunto de columnas reales de una tabla (con caché).

    Permite que el backend se adapte a bases con esquema mínimo (p. ej. una
    tabla 'pacientes' con solo nombre, apellido y telefono), evitando fallos
    al referenciar columnas que no existen.
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
        except Exception:
            _columnas_cache[clave] = set()
    return _columnas_cache[clave]


def columna_existe(tabla: str, columna: str, entorno: str | None = None) -> bool:
    """Devuelve True si la columna existe en la tabla (cacheado)."""
    return columna in columnas_tabla(tabla, entorno)
