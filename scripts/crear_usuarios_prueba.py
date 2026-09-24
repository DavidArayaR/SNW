"""Crea cuentas de prueba en SNW en lote.

Por cada correo: invita por API (`POST /api/usuarios/invitar`), rescata el
token de `account_invites` en MySQL (los correos son ficticios, no hay bandeja)
y activa la cuenta (`POST /api/auth/activar`) con la clave de prueba indicada.
Al final verifica cada cuenta con un login.

Uso:
    python scripts/crear_usuarios_prueba.py [--admin admin]
        [--api http://127.0.0.1:8000] [--password Prueba123*]
        [--correos a@correo.com,b@correo.com]

La clave del admin se pide por teclado (o variable de entorno SNW_ADMIN_CLAVE).
Requiere el servidor corriendo y las credenciales DB_* del .env local.
"""

import argparse
import getpass
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "backend"))

CORREOS_DEFECTO = [
    "camila.paredes@correo.com",
    "javier.soto@correo.com",
    "fernanda.vargas@correo.com",
    "matias.contreras@correo.com",
    "valentina.rios@correo.com",
    "diego.fuentes@correo.com",
    "antonia.morales@correo.com",
    "benjamin.tapia@correo.com",
    "sofia.herrera@correo.com",
    "lucas.olivares@correo.com",
]


def clave_segura(clave: str) -> str | None:
    if len(clave) < 8:
        return "mínimo 8 caracteres"
    if not re.search(r"[a-z]", clave):
        return "falta minúscula"
    if not re.search(r"[A-Z]", clave):
        return "falta mayúscula"
    if not re.search(r"\d", clave):
        return "falta número"
    return None


def api(base: str, metodo: str, ruta: str, cuerpo: dict | None = None,
        token: str | None = None) -> tuple[int, dict]:
    datos = json.dumps(cuerpo or {}).encode("utf-8")
    req = urllib.request.Request(base + ruta, data=datos, method=metodo,
                                 headers={"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            try:
                return res.status, json.loads(res.read().decode("utf-8") or "{}")
            except ValueError:
                return res.status, {}
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8") or "{}")
        except ValueError:
            return e.code, {}


def token_invitacion(correo: str) -> str | None:
    import pymysql

    def env(clave: str, defecto: str = "") -> str:
        return os.getenv(clave, defecto)

    # Lee DB_* del .env local (mismo formato que usa backend/db.py).
    env_file = BASE_DIR / ".env"
    if env_file.exists():
        for linea in env_file.read_text(encoding="utf-8").splitlines():
            linea = linea.strip()
            if linea and not linea.startswith("#") and "=" in linea:
                k, v = linea.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    conn = pymysql.connect(
        host=env("DB_HOST", "127.0.0.1"), port=int(env("DB_PUERTO", "3306") or 3306),
        user=env("DB_USUARIO", "root"), password=env("DB_CONTRASENA", ""),
        database=env("DB_NOMBRE", "snw_base"), charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT token FROM account_invites"
                " WHERE correo = %s AND usado = 0"
                " ORDER BY creado DESC LIMIT 1",
                (correo.strip().lower(),),
            )
            fila = cur.fetchone()
            return fila["token"] if fila else None
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="Crea cuentas de prueba en SNW.")
    ap.add_argument("--admin", default="admin")
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    ap.add_argument("--password", default="Prueba123*",
                    help="Clave inicial de las cuentas de prueba")
    ap.add_argument("--correos", default=",".join(CORREOS_DEFECTO))
    args = ap.parse_args()

    err = clave_segura(args.password)
    if err:
        print(f"La clave de prueba no es segura: {err}.")
        return 2
    correos = [c.strip().lower() for c in args.correos.split(",") if c.strip()]
    if not correos:
        print("Sin correos que procesar.")
        return 2

    clave_admin = os.getenv("SNW_ADMIN_CLAVE") or getpass.getpass(f"Clave del admin ({args.admin}): ")
    estado, datos = api(args.api, "POST", "/api/auth/login",
                        {"usuario": args.admin, "clave": clave_admin})
    if estado != 200 or not datos.get("token"):
        print(f"No se pudo iniciar sesión como {args.admin} (HTTP {estado}). "
              "Revisa que el servidor esté arriba y la clave sea correcta.")
        return 1
    token_admin = datos["token"]
    print(f"Sesión iniciada como {args.admin} (rol {datos.get('rol')}).\n")

    creadas, existentes, fallidas = [], [], []
    for correo in correos:
        estado, datos = api(args.api, "POST", "/api/usuarios/invitar",
                            {"correo": correo}, token_admin)
        if estado == 409:
            print(f"· {correo}: ya existe la cuenta, se omite.")
            existentes.append(correo)
            continue
        if estado not in (200, 201):
            print(f"· {correo}: no se pudo invitar (HTTP {estado}: {datos}).")
            fallidas.append(correo)
            continue
        if not datos.get("correo_enviado"):
            print(f"· {correo}: invitación creada (el correo no se pudo enviar; se activa por token).")
        tok = token_invitacion(correo)
        if not tok:
            print(f"· {correo}: invitada pero sin token vigente, se omite la activación.")
            fallidas.append(correo)
            continue
        estado_a, datos_a = api(args.api, "POST", "/api/auth/activar",
                                {"token": tok, "clave": args.password})
        if estado_a not in (200, 201) or not datos_a.get("token"):
            print(f"· {correo}: no se pudo activar (HTTP {estado_a}: {datos_a}).")
            fallidas.append(correo)
            continue
        estado_l, _ = api(args.api, "POST", "/api/auth/login",
                          {"usuario": correo, "clave": args.password})
        if estado_l == 200:
            print(f"· {correo}: creada y verificada con login.")
            creadas.append(correo)
        else:
            print(f"· {correo}: activada pero el login de verificación falló.")
            fallidas.append(correo)

    print(f"\nResumen: {len(creadas)} creadas, {len(existentes)} ya existían, {len(fallidas)} con fallo.")
    if creadas:
        print(f"Clave inicial de las cuentas nuevas: {args.password} (cámbiala desde Usuarios o que cada uno la cambie en «Mi cuenta»).")
    if fallidas:
        print("Con fallo: " + ", ".join(fallidas))
    return 0 if not fallidas else 1


if __name__ == "__main__":
    sys.exit(main())
