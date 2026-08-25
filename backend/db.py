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
