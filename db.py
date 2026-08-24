import os

import pymysql
from dotenv import load_dotenv

load_dotenv()


def _valor(clave: str, por_defecto: str = "") -> str:
    return os.getenv(clave, por_defecto)


def conectar():
    return pymysql.connect(
        host=_valor("DB_HOST", "127.0.0.1"),
        port=int(_valor("DB_PUERTO", "3306")),
        user=_valor("DB_USUARIO", "root"),
        password=_valor("DB_CONTRASENA", ""),
        database=_valor("DB_NOMBRE", "snw_pacientes"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )
