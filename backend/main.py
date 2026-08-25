import json
import hashlib
import os
import re
import threading
import time
import unicodedata
import uuid
from pathlib import Path

import pymysql
from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from db import conectar, entorno_valido, nombre_base
from motor_envio import obtener_canal
"""
from backend.db import conectar
from backend.motor_envio import obtener_canal"""

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DATA_FILE = BASE_DIR / "data" / "plantillas.json"
FRONTEND_DIR = BASE_DIR / "frontend"


def normalizar_telefono(crudo: str) -> str | None:
    """Corrige variantes comunes de números móviles chilenos al formato +569XXXXXXXX."""
    limpio = re.sub(r"[^\d+]", "", crudo.strip())
    digitos = re.sub(r"\D", "", limpio)

    if limpio.startswith("+"):
        if len(digitos) == 11 and digitos.startswith("569"):
            return "+" + digitos
        if len(digitos) == 10 and digitos.startswith("56"):
            return "+569" + digitos[2:]
        if len(digitos) == 9 and digitos.startswith("9"):
            return "+56" + digitos
        return None

    if len(digitos) == 11 and digitos.startswith("569"):
        return "+" + digitos
    if len(digitos) == 10 and digitos.startswith("56"):
        return "+569" + digitos[2:]
    if len(digitos) == 9 and digitos.startswith("9"):
        return "+56" + digitos
    if len(digitos) == 8 and digitos.startswith("9"):
        return "+569" + digitos
    return None

app = FastAPI(title="SNW - API de Notificaciones WhatsApp")


@app.middleware("http")
async def sin_cache(request, call_next):
    respuesta = await call_next(request)
    respuesta.headers["Cache-Control"] = "no-store"
    return respuesta


class PlantillaIn(BaseModel):
    nombre: str
    texto: str
    clave: str | None = None


class EnvioIn(BaseModel):
    pacientes: list[int] | None = None
    plantilla_id: int
    ambiente: str | None = None


class ConfigIn(BaseModel):
    entorno: str | None = None
    metodo_envio: str | None = None
    numeros_prueba_dev: list[str] | None = None
    numeros_prueba_prod: list[str] | None = None
    intervalo_ms: int | None = None


class LoginIn(BaseModel):
    usuario: str
    clave: str


USUARIOS_FILE = BASE_DIR / "data" / "usuarios.json"
SESIONES_FILE = BASE_DIR / "data" / "sesiones.json"


def _cargar_sesiones() -> dict[str, dict]:
    try:
        datos = json.loads(SESIONES_FILE.read_text(encoding="utf-8"))
        return datos if isinstance(datos, dict) else {}
    except FileNotFoundError:
        return {}


def guardar_sesiones() -> None:
    SESIONES_FILE.write_text(
        json.dumps(SESIONES, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _actualizar_env(clave: str, valor: str) -> None:
    env_path = BASE_DIR / ".env"
    try:
        texto = env_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        texto = ""
    lineas = texto.splitlines()
    nueva = f"{clave}={valor}"
    encontrada = False
    for i, lin in enumerate(lineas):
        if lin.strip().startswith(f"{clave}="):
            lineas[i] = nueva
            encontrada = True
            break
    if not encontrada:
        lineas.append(nueva)
    env_path.write_text("\n".join(lineas) + "\n", encoding="utf-8")


SESIONES: dict[str, dict] = _cargar_sesiones()


def cargar_usuarios() -> list:
    try:
        datos = json.loads(USUARIOS_FILE.read_text(encoding="utf-8"))
        return datos if isinstance(datos, list) else []
    except FileNotFoundError:
        return []


def sesion_actual(request: Request) -> dict:
    authz = request.headers.get("Authorization", "")
    token = authz[7:] if authz.startswith("Bearer ") else ""
    sesion = SESIONES.get(token)
    if not sesion:
        raise HTTPException(401, detail="Sesión no válida. Inicia sesión nuevamente.")
    return sesion


def solo_admin(sesion: dict = Depends(sesion_actual)) -> dict:
    if sesion.get("rol") != "administrador":
        raise HTTPException(403, detail="Esta sección requiere rol administrador")
    return sesion


@app.post("/api/auth/login")
def login(body: LoginIn):
    usuarios = cargar_usuarios()
    clave_hash = hashlib.sha256(body.clave.encode("utf-8")).hexdigest()
    usuario = next(
        (
            u
            for u in usuarios
            if str(u.get("usuario", "")).lower() == body.usuario.strip().lower()
            and u.get("clave_hash") == clave_hash
        ),
        None,
    )
    if usuario is None:
        raise HTTPException(401, detail="Usuario o contraseña incorrectos")

    token = uuid.uuid4().hex
    SESIONES[token] = {
        "rol": usuario.get("rol", "usuario"),
        "nombre": usuario.get("nombre", body.usuario),
    }
    guardar_sesiones()
    return {"token": token, "rol": SESIONES[token]["rol"], "nombre": SESIONES[token]["nombre"]}


@app.post("/api/auth/logout")
def logout(request: Request):
    authz = request.headers.get("Authorization", "")
    token = authz[7:] if authz.startswith("Bearer ") else ""
    SESIONES.pop(token, None)
    guardar_sesiones()
    return {"ok": True}


def leer_plantillas() -> list:
    try:
        datos = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        return datos if isinstance(datos, list) else []
    except FileNotFoundError:
        return []


def escribir_plantillas(datos: list) -> None:
    DATA_FILE.write_text(
        json.dumps(datos, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def slug(texto: str) -> str:
    t = unicodedata.normalize("NFD", texto.strip().lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = re.sub(r"[^a-z0-9]+", "_", t).strip("_")
    return t[:40] or "plantilla"


@app.get("/api/pacientes")
def listar_pacientes(q: str | None = Query(None), ambiente: str = Query("produccion"),
                     sesion: dict = Depends(solo_admin)):
    sql = (
        "SELECT id, nombre, apellido, telefono,"
        " COALESCE(NULLIF(estado, ''), 'pendiente') AS estado,"
        " COALESCE(info_extra, '') AS info_extra,"
        " fecha_actualizacion FROM pacientes"
    )
    args: list = []
    if q and q.strip():
        like = f"%{q.strip()}%"
        sql += " WHERE nombre LIKE %s OR telefono LIKE %s OR info_extra LIKE %s"
        args = [like, like, like]
    sql += " ORDER BY id"

    with conectar(ambiente) as conn, conn.cursor() as cur:
        cur.execute(sql, tuple(args) or None)
        filas = cur.fetchall()

    for f in filas:
        f["actualizado"] = f.pop("fecha_actualizacion").strftime("%d-%m-%Y %H:%M")
    return filas


class EstadoPacienteIn(BaseModel):
    estado: str


@app.put("/api/pacientes/{paciente_id}")
def actualizar_paciente(paciente_id: int, body: EstadoPacienteIn,
                        ambiente: str = Query("produccion"),
                        sesion: dict = Depends(solo_admin)):
    if body.estado not in ("pendiente", "enviado", "error"):
        raise HTTPException(400, detail="Estado inválido. Use: pendiente, enviado o error")
    with conectar(ambiente) as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM pacientes WHERE id = %s", (paciente_id,))
        if not cur.fetchone():
            raise HTTPException(404, detail="Paciente no encontrado")
        cur.execute("UPDATE pacientes SET estado = %s WHERE id = %s", (body.estado, paciente_id))
        conn.commit()
        cur.execute(
            "SELECT id, nombre, apellido, telefono,"
            " COALESCE(NULLIF(estado, ''), 'pendiente') AS estado,"
            " COALESCE(info_extra, '') AS info_extra,"
            " fecha_actualizacion FROM pacientes WHERE id = %s",
            (paciente_id,),
        )
        fila = cur.fetchone()
        fila["actualizado"] = fila.pop("fecha_actualizacion").strftime("%d-%m-%Y %H:%M")
        return fila


@app.get("/api/plantillas")
def listar_plantillas(sesion: dict = Depends(sesion_actual)):
    return sorted(leer_plantillas(), key=lambda p: p.get("actualizada", 0), reverse=True)


@app.post("/api/plantillas", status_code=201)
def crear_plantilla(body: PlantillaIn, sesion: dict = Depends(sesion_actual)):
    if not body.nombre.strip() or not body.texto.strip():
        raise HTTPException(400, detail="Nombre y mensaje son obligatorios")

    plantillas = leer_plantillas()
    clave = slug(body.clave or body.nombre)

    if any(p["clave"] == clave for p in plantillas):
        raise HTTPException(409, detail="Ya existe una plantilla con esa clave")

    nueva = {
        "id": max((p["id"] for p in plantillas), default=0) + 1,
        "clave": clave,
        "nombre": body.nombre.strip(),
        "texto": body.texto,
        "actualizada": int(time.time() * 1000),
    }
    plantillas.append(nueva)
    escribir_plantillas(plantillas)
    return nueva


@app.put("/api/plantillas/{plantilla_id}")
def actualizar_plantilla(plantilla_id: int, body: PlantillaIn, sesion: dict = Depends(sesion_actual)):
    if not body.nombre.strip() or not body.texto.strip():
        raise HTTPException(400, detail="Nombre y mensaje son obligatorios")

    plantillas = leer_plantillas()
    for p in plantillas:
        if p["id"] == plantilla_id:
            p["nombre"] = body.nombre.strip()
            p["texto"] = body.texto
            p["actualizada"] = int(time.time() * 1000)
            escribir_plantillas(plantillas)
            return p

    raise HTTPException(404, detail="Plantilla no encontrada")


@app.delete("/api/plantillas/{plantilla_id}")
def eliminar_plantilla(plantilla_id: int, sesion: dict = Depends(sesion_actual)):
    plantillas = leer_plantillas()
    restantes = [p for p in plantillas if p["id"] != plantilla_id]
    if len(restantes) == len(plantillas):
        raise HTTPException(404, detail="Plantilla no encontrada")

    escribir_plantillas(restantes)
    return {"ok": True}


def leer_config(ambiente: str | None = None) -> dict:
    # Recargar .env para que ediciones manuales surtan efecto sin reiniciar
    load_dotenv(BASE_DIR / ".env", override=True)
    ent = entorno_valido(ambiente)
    var_numeros = ("SNW_NUMEROS_PRUEBA_DEV" if ent == "desarrollo"
                   else "SNW_NUMEROS_PRUEBA_PROD")

    def lista(var: str) -> list:
        return [n.strip() for n in os.getenv(var, "").split(",") if n.strip()]

    return {
        "entorno": ent,
        "base_datos": nombre_base(ent),
        "numeros_autorizados": lista(var_numeros),
        "metodo_envio": os.getenv("SNW_METODO_ENVIO", "simulado"),
        "wa_api": {
            "configurada": bool(
                os.getenv("SNW_WA_TOKEN", "").strip()
                and os.getenv("SNW_WA_PHONE_ID", "").strip()
            ),
            "version_graph": "v21.0",
        },
        "intervalo_ms": int(os.getenv("SNW_INTERVALO_MS", "1000")),
    }


@app.get("/api/configuracion")
def obtener_configuracion(ambiente: str | None = Query(None),
                          sesion: dict = Depends(sesion_actual)):
    try:
        return leer_config(ambiente)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))


@app.put("/api/configuracion")
def actualizar_configuracion(body: ConfigIn, sesion: dict = Depends(solo_admin)):
    if body.entorno is not None:
        if body.entorno not in ("desarrollo", "produccion"):
            raise HTTPException(400, detail="Entorno inválido")
        os.environ["SNW_ENTORNO"] = body.entorno
        _actualizar_env("SNW_ENTORNO", body.entorno)

    if body.metodo_envio is not None:
        if body.metodo_envio not in ("simulado", "whatsapp_web", "api_oficial"):
            raise HTTPException(400, detail="Método de envío inválido")
        os.environ["SNW_METODO_ENVIO"] = body.metodo_envio
        _actualizar_env("SNW_METODO_ENVIO", body.metodo_envio)

    if body.numeros_prueba_dev is not None:
        val = ",".join(n.strip() for n in body.numeros_prueba_dev if n.strip())
        os.environ["SNW_NUMEROS_PRUEBA_DEV"] = val
        _actualizar_env("SNW_NUMEROS_PRUEBA_DEV", val)

    if body.numeros_prueba_prod is not None:
        val = ",".join(n.strip() for n in body.numeros_prueba_prod if n.strip())
        os.environ["SNW_NUMEROS_PRUEBA_PROD"] = val
        _actualizar_env("SNW_NUMEROS_PRUEBA_PROD", val)

    if body.intervalo_ms is not None:
        val = str(max(0, int(body.intervalo_ms)))
        os.environ["SNW_INTERVALO_MS"] = val
        _actualizar_env("SNW_INTERVALO_MS", val)

    return leer_config()


class PruebaWAIn(BaseModel):
    telefono: str
    mensaje: str = "Mensaje de prueba del sistema SNW"


@app.post("/api/notificaciones/prueba-wa")
def probar_api_wa(body: PruebaWAIn, sesion: dict = Depends(solo_admin)):
    """Envía un mensaje real vía la API oficial para validar las credenciales de .env."""
    cfg = leer_config()
    if cfg["metodo_envio"] != "api_oficial":
        raise HTTPException(400, detail="SNW_METODO_ENVIO no está en api_oficial")

    canal = obtener_canal(cfg)
    if canal is None or not canal.disponible():
        raise HTTPException(
            400,
            detail="Faltan SNW_WA_TOKEN o SNW_WA_PHONE_ID en el archivo .env",
        )

    telefono = normalizar_telefono(body.telefono)
    if telefono is None:
        raise HTTPException(400, detail=f"Formato de teléfono inválido: '{body.telefono}'")

    ok, error = canal.enviar(telefono, body.mensaje)
    return {"ok": ok, "telefono": telefono, "error": error}


JOBS: dict = {}
JOB_LOCK = threading.Lock()


def renderizar_mensaje(texto: str, paciente: dict) -> str:
    reemplazos = {
        "{nombre}": paciente.get("nombre") or "",
        "{apellido}": paciente.get("apellido") or "",
        "{info_extra}": paciente.get("info_extra") or "",
    }
    for clave, valor in reemplazos.items():
        texto = texto.replace(clave, valor)
    return texto


def actualizar_estado_paciente(paciente_id: int, estado: str, ambiente: str) -> None:
    with conectar(ambiente) as conn, conn.cursor() as cur:
        cur.execute("UPDATE pacientes SET estado = %s WHERE id = %s", (estado, paciente_id))
        conn.commit()


def actualizar_telefono(paciente_id: int, telefono: str, ambiente: str) -> None:
    with conectar(ambiente) as conn, conn.cursor() as cur:
        cur.execute("UPDATE pacientes SET telefono = %s WHERE id = %s", (telefono, paciente_id))
        conn.commit()


def registrar_historial(paciente_id, nombre, telefono, clave_plantilla, mensaje, estado, error=None,
                        ambiente="produccion") -> None:
    try:
        with conectar(ambiente) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO log_envios (paciente_id, nombre_paciente, numero_telefono, mensaje,"
                " plantilla_clave, estado_envio, descripcion_error) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (paciente_id, nombre, telefono, mensaje, clave_plantilla, estado, error),
            )
            conn.commit()
    except Exception as e:
        print(f"[HISTORIAL] No se pudo registrar: {e}")


def procesar_job(job_id: str) -> None:
    job = JOBS[job_id]
    amb = job["ambiente"]
    cfg = leer_config()
    canal = obtener_canal(cfg)
    clave = job.get("plantilla", {}).get("clave")

    if canal is None or not canal.disponible():
        for d in job["destinatarios"]:
            actualizar_estado_paciente(d["id"], "error", amb)
            job["fallidos"] += 1
            registrar_historial(d["id"], d["nombre"], d["telefono"], clave,
                                d["mensaje"], "error",
                                f"Canal no disponible: {canal.nombre if canal else 'desconocido'}",
                                ambiente=amb)
        job["estado"] = "error"
        job["detalle"] = f"Canal de envío no disponible ({cfg.get('metodo_envio')})"
        return

    intervalo = max(0, int(cfg.get("intervalo_ms", 1000))) / 1000
    total = len(job["destinatarios"])

    for i, d in enumerate(job["destinatarios"]):
        job["actual"] = d["nombre"]
        ok, error = canal.enviar(d["telefono"], d["mensaje"])
        actualizar_estado_paciente(d["id"], "enviado" if ok else "error", amb)

        if ok:
            job["enviados"] += 1
        else:
            job["fallidos"] += 1
            job["errores"].append({"id": d["id"], "telefono": d["telefono"], "detalle": error})

        registrar_historial(d["id"], d["nombre"], d["telefono"], clave,
                            d["mensaje"], "enviado" if ok else "error", error,
                            ambiente=amb)

        if i < total - 1 and intervalo > 0:
            time.sleep(intervalo)

    job["actual"] = ""
    job["estado"] = "completado"


@app.post("/api/notificaciones/enviar", status_code=202)
def iniciar_envio(body: EnvioIn, background_tasks: BackgroundTasks,
                  sesion: dict = Depends(sesion_actual)):
    try:
        amb = entorno_valido(body.ambiente)
    except ValueError:
        raise HTTPException(400, detail=f"Entorno inválido: '{body.ambiente}'")

    if not body.pacientes and body.pacientes is not None:
        raise HTTPException(400, detail="No se seleccionaron pacientes")

    cfg = leer_config(amb)
    lista_autorizados = cfg.get("numeros_autorizados", [])
    autorizados = set(lista_autorizados)

    plantilla = next((p for p in leer_plantillas() if p["id"] == body.plantilla_id), None)
    if plantilla is None:
        raise HTTPException(404, detail="Plantilla no encontrada")

    with conectar(amb) as conn, conn.cursor() as cur:
        if body.pacientes:
            placeholders = ", ".join("%s" for _ in body.pacientes)
            cur.execute(
                f"SELECT id, nombre, apellido, telefono, info_extra FROM pacientes WHERE id IN ({placeholders})",
                tuple(body.pacientes),
            )
        else:
            cur.execute(
                "SELECT id, nombre, apellido, telefono, info_extra FROM pacientes"
                " WHERE estado = 'pendiente' ORDER BY id"
            )
        filas = cur.fetchall()

    rechazados: list[dict] = []
    destinatarios: list[dict] = []

    for p in filas:
        telefono_crudo = (p.get("telefono") or "").strip()
        nombre_completo = " ".join(x for x in [p.get("nombre"), p.get("apellido")] if x)

        telefono = normalizar_telefono(telefono_crudo)
        if telefono is None:
            rechazados.append({"id": p["id"], "nombre": nombre_completo, "telefono": telefono_crudo,
                               "motivo": "Formato de teléfono inválido"})
            actualizar_estado_paciente(p["id"], "error", amb)
            registrar_historial(p["id"], nombre_completo, telefono_crudo, plantilla["clave"],
                                "", "numero_invalido", f"Formato de teléfono inválido: '{telefono_crudo}'",
                                ambiente=amb)
            continue

        if telefono != telefono_crudo:
            actualizar_telefono(p["id"], telefono, amb)

        if amb == "desarrollo" and telefono not in autorizados:
            rechazados.append({"id": p["id"], "nombre": nombre_completo, "telefono": telefono,
                               "motivo": f"No está en los números autorizados de la base {amb}"})
            registrar_historial(p["id"], nombre_completo, telefono, plantilla["clave"],
                                "", "error", f"Número no autorizado en base {amb}",
                                ambiente=amb)
            continue

        destinatarios.append({
            "id": p["id"],
            "nombre": nombre_completo,
            "telefono": telefono,
            "mensaje": renderizar_mensaje(plantilla["texto"], p),
        })

    if not destinatarios:
        return {"iniciado": False, "total": 0, "rechazados": rechazados}

    job_id = uuid.uuid4().hex[:8]
    JOBS[job_id] = {
        "estado": "en_proceso",
        "total": len(destinatarios),
        "enviados": 0,
        "fallidos": 0,
        "actual": "",
        "errores": [],
        "detalle": "",
        "ambiente": amb,
        "plantilla": {"id": plantilla["id"], "clave": plantilla["clave"]},
        "destinatarios": destinatarios,
    }

    background_tasks.add_task(procesar_job, job_id)
    return {"iniciado": True, "job_id": job_id, "total": len(destinatarios),
            "ambiente": amb, "rechazados": rechazados}


class DestinosIn(BaseModel):
    ambiente: str = "produccion"


@app.post("/api/notificaciones/destinatarios")
def contar_destinatarios(body: DestinosIn, sesion: dict = Depends(sesion_actual)):
    try:
        amb = entorno_valido(body.ambiente)
    except ValueError:
        raise HTTPException(400, detail=f"Entorno inválido: '{body.ambiente}'")

    with conectar(amb) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS total,"
            " SUM(estado = 'pendiente') AS pendientes"
            " FROM pacientes"
        )
        fila = cur.fetchone()

    return {
        "total": int(fila["total"] or 0),
        "pendientes": int(fila["pendientes"] or 0),
        "base_datos": nombre_base(amb),
    }


@app.get("/api/notificaciones/jobs/{job_id}")
def estado_job(job_id: str, sesion: dict = Depends(sesion_actual)):
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, detail="Envío no encontrado")

    return {k: v for k, v in job.items() if k != "destinatarios"}


@app.get("/api/notificaciones/historial")
def listar_historial(q: str | None = Query(None), estado: str | None = Query(None),
                     ambiente: str = Query("produccion"), sesion: dict = Depends(sesion_actual)):
    sql = ("SELECT id, paciente_id, nombre_paciente, numero_telefono, plantilla_clave,"
           " estado_envio, descripcion_error, fecha_hora, mensaje FROM log_envios")
    condiciones: list[str] = []
    args: list = []

    if q and q.strip():
        like = f"%{q.strip()}%"
        condiciones.append(
            "(nombre_paciente LIKE %s OR numero_telefono LIKE %s OR plantilla_clave LIKE %s"
            " OR descripcion_error LIKE %s)"
        )
        args += [like, like, like, like]

    if estado in ("enviado", "error", "numero_invalido"):
        condiciones.append("estado_envio = %s")
        args.append(estado)

    if condiciones:
        sql += " WHERE " + " AND ".join(condiciones)
    sql += " ORDER BY id DESC LIMIT 300"

    with conectar(ambiente) as conn, conn.cursor() as cur:
        cur.execute(sql, tuple(args) or None)
        filas = cur.fetchall()

    for f in filas:
        f["fecha"] = f.pop("fecha_hora").strftime("%d-%m-%Y %H:%M")
    return filas


app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="localhost", port=8000)
