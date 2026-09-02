import os
from pathlib import Path

import pymysql
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

AMBIENTES = {"desarrollo", "produccion"}
TABLAS_PACIENTES = {"desarrollo": "pacientes_dev", "produccion": "pacientes_prod"}


def entorno_valido(entorno: str | None = None) -> str:
    ent = (entorno or os.getenv("SNW_ENTORNO", "desarrollo")).strip().lower()
    if ent not in AMBIENTES:
        raise ValueError(f"Entorno desconocido: '{ent}'")
    return ent


def tabla_pacientes(entorno: str | None = None) -> str:
    """Nombre de la tabla de pacientes según el entorno (una única base)."""
    return TABLAS_PACIENTES[entorno_valido(entorno)]


def conectar(entorno: str | None = None):
    # Una única base de datos para todo el sistema (snw_base).
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
        except Exception:
            _columnas_cache[clave] = set()
    return _columnas_cache[clave]


def columna_existe(tabla: str, columna: str, entorno: str | None = None) -> bool:
    """Devuelve True si la columna existe en la tabla (cacheado)."""
    return columna in columnas_tabla(tabla, entorno)
