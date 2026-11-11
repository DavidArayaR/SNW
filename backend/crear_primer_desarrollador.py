"""Crea una cuenta de arranque desde la consola del servidor."""

import argparse
import re
from getpass import getpass

from credenciales import hash_clave
from db import MAX_DESARROLLADORES, asegurar_tabla_config, conectar


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("correo")
    args = parser.parse_args()
    correo = args.correo.strip().lower()
    if len(correo) > 150 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", correo):
        parser.error("Indica un correo válido.")
    clave = getpass("Contraseña nueva (16 a 128 caracteres): ")
    if not 16 <= len(clave) <= 128 or clave != getpass("Repite la contraseña: "):
        parser.error("La longitud o la confirmación de la contraseña no son válidas.")
    guardado = hash_clave(clave)
    asegurar_tabla_config()
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("SELECT GET_LOCK('snw-primer-desarrollador', 5) AS adquirido")
        if not cur.fetchone()["adquirido"]:
            raise SystemExit("Otra operación de arranque está en curso.")
        cur.execute("SELECT 1 FROM usuarios WHERE activo=1 AND rol='desarrollador' LIMIT 1")
        if cur.fetchone():
            raise SystemExit("Ya existe un desarrollador activo. Usa la gestión de usuarios de SNW.")
        cur.execute("SELECT COUNT(*) AS total FROM usuarios WHERE rol='desarrollador'")
        if cur.fetchone()["total"] >= MAX_DESARROLLADORES:
            raise SystemExit("Se alcanzó el máximo de cuentas de desarrollador.")
        cur.execute("SELECT 1 FROM usuarios WHERE usuario=%s", (correo,))
        if cur.fetchone():
            raise SystemExit("El correo ya pertenece a otra cuenta. Usa un correo distinto.")
        cur.execute(
            "INSERT INTO usuarios (usuario,nombre,rol,clave_hash,correo_recuperacion)"
            " VALUES (%s,%s,'desarrollador',%s,%s)",
            (correo, correo.split("@", 1)[0], guardado, correo),
        )
        conn.commit()
    print("Cuenta de desarrollador creada.")


if __name__ == "__main__":
    main()
