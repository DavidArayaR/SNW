import os
from pathlib import Path

import pymysql
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def conectar():
    return pymysql.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PUERTO", "3306")),
        user=os.getenv("DB_USUARIO", "root"),
        password=os.getenv("DB_CONTRASENA", ""),
        database=os.getenv("DB_NOMBRE", "snw_pacientes"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )
