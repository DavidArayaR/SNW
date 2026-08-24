import json
import re
import time
import unicodedata
from pathlib import Path

import pymysql
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from db import conectar

ROOT = Path(__file__).parent
DATA_FILE = ROOT / "data" / "plantillas.json"

app = FastAPI(title="SNW - API de Notificaciones WhatsApp")


class PlantillaIn(BaseModel):
    nombre: str
    texto: str
    clave: str | None = None


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


app.mount("/", StaticFiles(directory=ROOT, html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="localhost", port=8000)
