"""Carga pacientes ficticios en una tabla de pacientes (por defecto,
`pacientes_dev`, mínimo 100). Idempotente: omite teléfonos ya existentes.

Uso:
    python scripts/cargar_pacientes_prueba.py [--cantidad 100]
        [--tabla pacientes_dev]

Requiere MySQL arriba y las credenciales DB_* del .env local. No toca
`pacientes_prod` salvo que se indique otra tabla válida explícitamente.
"""

import argparse
import os
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

NOMBRES = ["Camila", "Javier", "Fernanda", "Matías", "Valentina", "Diego",
           "Antonia", "Benjamín", "Sofía", "Lucas", "Martina", "Tomás",
           "Emilia", "Joaquín", "Isidora", "Facundo", "Florencia", "Vicente",
           "Catalina", "Sebastián", "Constanza", "Nicolás", "Antonella",
           "Cristóbal", "Javiera", "Maximiliano", "Trinidad", "Agustín",
           "Josefa", "Felipe"]
APELLIDOS = ["Paredes", "Soto", "Vargas", "Contreras", "Ríos", "Fuentes",
             "Morales", "Tapia", "Herrera", "Olivares", "Muñoz", "Rojas",
             "Díaz", "Pérez", "González", "Fernández", "López", "Martínez",
             "Sánchez", "Ramírez", "Cruz", "Flores", "Gómez", "Mendoza",
             "Aguilar", "Castillo", "Ortiz", "Chávez", "Romero", "Navarro"]


def conectar():
    import pymysql

    env_file = BASE_DIR / ".env"
    if env_file.exists():
        for linea in env_file.read_text(encoding="utf-8").splitlines():
            linea = linea.strip()
            if linea and not linea.startswith("#") and "=" in linea:
                k, v = linea.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    return pymysql.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PUERTO", "3306") or 3306),
        user=os.getenv("DB_USUARIO", "root"),
        password=os.getenv("DB_CONTRASENA", ""),
        database=os.getenv("DB_NOMBRE", "snw_base"), charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Carga pacientes ficticios.")
    ap.add_argument("--cantidad", type=int, default=100)
    ap.add_argument("--tabla", default="pacientes_dev")
    args = ap.parse_args()

    if args.cantidad < 1:
        print("La cantidad debe ser mayor a 0.")
        return 2
    if not re.fullmatch(r"pacientes_[a-z0-9_]{1,54}", args.tabla or ""):
        print("Tabla inválida.")
        return 2

    conn = conectar()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS n FROM information_schema.TABLES"
                " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
                (args.tabla,),
            )
            if not (cur.fetchone() or {}).get("n"):
                print(f"No existe la tabla {args.tabla}.")
                return 1
            cur.execute(f"SELECT telefono FROM {args.tabla}")
            existentes = {r["telefono"] for r in cur.fetchall()}

            insertados = omitidos = 0
            i = intentos = 0
            while insertados < args.cantidad and intentos < args.cantidad * 20:
                intentos += 1
                nombre = NOMBRES[i % len(NOMBRES)]
                apellido = APELLIDOS[(i * 7) % len(APELLIDOS)]
                if i % 3 == 0:
                    apellido += " " + APELLIDOS[(i * 13) % len(APELLIDOS)]
                telefono = f"+5699{(i * 37) % 10000000:07d}"
                i += 1
                if telefono in existentes:
                    omitidos += 1
                    continue
                cur.execute(
                    f"INSERT INTO {args.tabla} (nombre, apellido, telefono, estado)"
                    " VALUES (%s, %s, %s, 'pendiente')",
                    (nombre, apellido, telefono),
                )
                existentes.add(telefono)
                insertados += 1
            conn.commit()
            cur.execute(f"SELECT COUNT(*) AS n FROM {args.tabla}")
            total = (cur.fetchone() or {}).get("n", 0)
    finally:
        conn.close()
    print(f"Insertados: {insertados} | Omitidos (duplicados): {omitidos} | Total en {args.tabla}: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
