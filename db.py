import pymysql

DB_CONFIG = {
    "host": "127.0.0.1",
    "user": "root",
    "password": "",
    "database": "snw_pacientes",
    "charset": "utf8mb4",
    "cursorclass": pymysql.cursors.DictCursor,
}


def conectar():
    return pymysql.connect(**DB_CONFIG)
