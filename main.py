import json
import os
import re
import threading
import time
import unicodedata
import uuid
from pathlib import Path

import pymysql
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from db import conectar
from motor_envio import obtener_canal

load_dotenv()

ROOT = Path(__file__).parent
DATA_FILE = ROOT / "data" / "plantillas.json"


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


class PlantillaIn(BaseModel):
    nombre: str
    texto: str
    clave: str | None = None


class EnvioIn(BaseModel):
    pacientes: list[int]
    plantilla_id: int


class ConfigIn(BaseModel):
    entorno: str | None = None
    metodo_envio: str | None = None
    numeros_prueba: list[str] | None = None
    intervalo_ms: int | None = None


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


@app.get("/")
def raiz():
    return RedirectResponse("/pacientes.html")


@app.get("/api/pacientes")
def listar_pacientes(q: str | None = Query(None)):
    sql = (
        "SELECT id, nombre, apellido, telefono, info_extra, estado, "
        "fecha_actualizacion FROM pacientes"
    )
    args: list = []
    if q and q.strip():
        like = f"%{q.strip()}%"
        sql += " WHERE nombre LIKE %s OR telefono LIKE %s OR info_extra LIKE %s"
        args = [like, like, like]
    sql += " ORDER BY id"

    with conectar() as conn, conn.cursor() as cur:
        cur.execute(sql, tuple(args) or None)
        filas = cur.fetchall()

    for f in filas:
        f["actualizado"] = f.pop("fecha_actualizacion").strftime("%d-%m-%Y %H:%M")
    return filas


@app.get("/api/plantillas")
def listar_plantillas():
    return sorted(leer_plantillas(), key=lambda p: p.get("actualizada", 0), reverse=True)


@app.post("/api/plantillas", status_code=201)
def crear_plantilla(body: PlantillaIn):
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
def actualizar_plantilla(plantilla_id: int, body: PlantillaIn):
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
def eliminar_plantilla(plantilla_id: int):
    plantillas = leer_plantillas()
    restantes = [p for p in plantillas if p["id"] != plantilla_id]
    if len(restantes) == len(plantillas):
        raise HTTPException(404, detail="Plantilla no encontrada")

    escribir_plantillas(restantes)
    return {"ok": True}


def leer_config() -> dict:
    return {
        "entorno": os.getenv("SNW_ENTORNO", "desarrollo"),
        "metodo_envio": os.getenv("SNW_METODO_ENVIO", "simulado"),
        "numeros_prueba": [
            n.strip() for n in os.getenv("SNW_NUMEROS_PRUEBA", "").split(",") if n.strip()
        ],
        "intervalo_ms": int(os.getenv("SNW_INTERVALO_MS", "1000")),
    }


@app.get("/api/configuracion")
def obtener_configuracion():
    return leer_config()


@app.put("/api/configuracion")
def actualizar_configuracion(body: ConfigIn):
    if body.entorno is not None:
        if body.entorno not in ("desarrollo", "produccion"):
            raise HTTPException(400, detail="Entorno inválido")
        os.environ["SNW_ENTORNO"] = body.entorno

    if body.metodo_envio is not None:
        if body.metodo_envio not in ("simulado", "whatsapp_web", "api_oficial"):
            raise HTTPException(400, detail="Método de envío inválido")
        os.environ["SNW_METODO_ENVIO"] = body.metodo_envio

    if body.numeros_prueba is not None:
        os.environ["SNW_NUMEROS_PRUEBA"] = ",".join(n.strip() for n in body.numeros_prueba if n.strip())

    if body.intervalo_ms is not None:
        os.environ["SNW_INTERVALO_MS"] = str(max(0, int(body.intervalo_ms)))

    return leer_config()


JOBS: dict = {}
JOB_LOCK = threading.Lock()


def renderizar_mensaje(texto: str, paciente: dict) -> str:
    ahora = time.localtime()
    reemplazos = {
        "{nombre}": paciente.get("nombre") or "",
        "{apellido}": paciente.get("apellido") or "",
        "{info_extra}": paciente.get("info_extra") or "",
        "{fecha}": time.strftime("%d-%m-%Y", ahora),
        "{hora}": time.strftime("%H:%M", ahora),
    }
    for clave, valor in reemplazos.items():
        texto = texto.replace(clave, valor)
    return texto


def actualizar_estado_paciente(paciente_id: int, estado: str) -> None:
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("UPDATE pacientes SET estado = %s WHERE id = %s", (estado, paciente_id))
        conn.commit()


def actualizar_telefono(paciente_id: int, telefono: str) -> None:
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("UPDATE pacientes SET telefono = %s WHERE id = %s", (telefono, paciente_id))
        conn.commit()


def registrar_historial(paciente_id, nombre, telefono, clave_plantilla, mensaje, estado, error=None) -> None:
    try:
        with conectar() as conn, conn.cursor() as cur:
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
    cfg = leer_config()
    canal = obtener_canal(cfg)
    clave = job.get("plantilla", {}).get("clave")

    if canal is None or not canal.disponible():
        for d in job["destinatarios"]:
            actualizar_estado_paciente(d["id"], "error")
            job["fallidos"] += 1
            registrar_historial(d["id"], d["nombre"], d["telefono"], clave,
                                d["mensaje"], "error", f"Canal no disponible: {canal.nombre if canal else 'desconocido'}")
        job["estado"] = "error"
        job["detalle"] = f"Canal de envío no disponible ({cfg.get('metodo_envio')})"
        return

    intervalo = max(0, int(cfg.get("intervalo_ms", 1000))) / 1000
    total = len(job["destinatarios"])

    for i, d in enumerate(job["destinatarios"]):
        job["actual"] = d["nombre"]
        ok, error = canal.enviar(d["telefono"], d["mensaje"])
        actualizar_estado_paciente(d["id"], "enviado" if ok else "error")

        if ok:
            job["enviados"] += 1
        else:
            job["fallidos"] += 1
            job["errores"].append({"id": d["id"], "telefono": d["telefono"], "detalle": error})

        registrar_historial(d["id"], d["nombre"], d["telefono"], clave,
                            d["mensaje"], "enviado" if ok else "error", error)

        if i < total - 1 and intervalo > 0:
            time.sleep(intervalo)

    job["actual"] = ""
    job["estado"] = "completado"


@app.post("/api/notificaciones/enviar", status_code=202)
def iniciar_envio(body: EnvioIn, background_tasks: BackgroundTasks):
    if not body.pacientes:
        raise HTTPException(400, detail="No se seleccionaron pacientes")

    cfg = leer_config()
    en_desarrollo = cfg.get("entorno") == "desarrollo"
    autorizados = set(cfg.get("numeros_prueba", []))

    plantilla = next((p for p in leer_plantillas() if p["id"] == body.plantilla_id), None)
    if plantilla is None:
        raise HTTPException(404, detail="Plantilla no encontrada")

    placeholders = ", ".join("%s" for _ in body.pacientes)
    with conectar() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT id, nombre, apellido, telefono, info_extra FROM pacientes WHERE id IN ({placeholders})",
            tuple(body.pacientes),
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
            actualizar_estado_paciente(p["id"], "error")
            registrar_historial(p["id"], nombre_completo, telefono_crudo, plantilla["clave"],
                                "", "numero_invalido", f"Formato de teléfono inválido: '{telefono_crudo}'")
            continue

        if telefono != telefono_crudo:
            actualizar_telefono(p["id"], telefono)

        if en_desarrollo and telefono not in autorizados:
            rechazados.append({"id": p["id"], "nombre": nombre_completo, "telefono": telefono,
                               "motivo": "No está en la lista de números de prueba (entorno desarrollo)"})
            registrar_historial(p["id"], nombre_completo, telefono, plantilla["clave"],
                                "", "error", "Número no autorizado en entorno desarrollo")
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
        "plantilla": {"id": plantilla["id"], "clave": plantilla["clave"]},
        "destinatarios": destinatarios,
    }

    background_tasks.add_task(procesar_job, job_id)
    return {"iniciado": True, "job_id": job_id, "total": len(destinatarios), "rechazados": rechazados}


@app.get("/api/notificaciones/jobs/{job_id}")
def estado_job(job_id: str):
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, detail="Envío no encontrado")

    return {k: v for k, v in job.items() if k != "destinatarios"}


@app.get("/api/notificaciones/historial")
def listar_historial(q: str | None = Query(None), estado: str | None = Query(None)):
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

    with conectar() as conn, conn.cursor() as cur:
        cur.execute(sql, tuple(args) or None)
        filas = cur.fetchall()

    for f in filas:
        f["fecha"] = f.pop("fecha_hora").strftime("%d-%m-%Y %H:%M")
    return filas


app.mount("/", StaticFiles(directory=ROOT, html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="localhost", port=8000)
