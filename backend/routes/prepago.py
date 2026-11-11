"""API administrativa de Extras → Recordatorio prepago."""

import datetime as dt
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

import prepago
from db import auditoria_registrar


class LineaIn(BaseModel):
    nombre: str = Field(min_length=1, max_length=80)
    telefono: str = Field("", max_length=24)
    operador: str = Field(min_length=1, max_length=32)
    sistema: Literal["SNW", "CRM", "Personal", "Otro"] = "Otro"
    clinica: str = Field("", max_length=8)
    ultima_recarga: dt.date | None = None
    activo: bool = True
    avisar: bool = True


class EditarLineaIn(LineaIn):
    version: int = Field(ge=1)


class RecargaIn(BaseModel):
    fecha: dt.date
    observacion: str = Field("", max_length=500)
    version: int = Field(ge=1)


class OperadorIn(BaseModel):
    codigo: str = Field(min_length=1, max_length=32)
    nombre: str = Field(min_length=1, max_length=80)
    dominio: str = Field("", max_length=253)
    url_recarga: str = Field("", max_length=500)
    verificado: bool = False


class DestinoIn(BaseModel):
    correo: str = Field("", max_length=254)


class PlazoIn(BaseModel):
    dias: int = Field(strict=True, ge=1, le=prepago.MAX_DIAS)


def registrar(solo_admin, solo_dev):
    router = APIRouter(prefix="/api/extras/prepago", tags=["Extras"])

    def ejecutar(funcion, *args):
        try:
            return funcion(*args)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("")
    def estado(_sesion: dict = Depends(solo_admin)):
        return prepago.estado()

    @router.post("/lineas", status_code=201)
    def crear_linea(body: LineaIn, sesion: dict = Depends(solo_admin)):
        actor = sesion["usuario"]
        linea_id = ejecutar(prepago.guardar_linea, body.model_dump(), actor)
        auditoria_registrar(actor, "prepago_crear", str(linea_id))
        return {"id": linea_id}

    @router.put("/lineas/{linea_id}")
    def editar_linea(linea_id: int, body: EditarLineaIn, sesion: dict = Depends(solo_admin)):
        actor = sesion["usuario"]
        ejecutar(prepago.guardar_linea, body.model_dump(), actor, linea_id)
        auditoria_registrar(actor, "prepago_editar", str(linea_id),
                            "Línea activa" if body.activo else "Línea desactivada")
        return {"id": linea_id}

    @router.post("/lineas/{linea_id}/recargas", status_code=201)
    def recargar(linea_id: int, body: RecargaIn, sesion: dict = Depends(solo_admin)):
        actor = sesion["usuario"]
        ejecutar(prepago.registrar_recarga, linea_id, body.fecha, body.observacion, actor, body.version)
        auditoria_registrar(actor, "prepago_recarga", str(linea_id), body.fecha.isoformat())
        return {"id": linea_id}

    @router.get("/lineas/{linea_id}/historial")
    def historial(linea_id: int, pagina: int = Query(1, ge=1, le=100000),
                  _sesion: dict = Depends(solo_admin)):
        return ejecutar(prepago.historial, linea_id, pagina)

    @router.put("/operadores")
    def operador(body: OperadorIn, sesion: dict = Depends(solo_dev)):
        codigo = ejecutar(prepago.guardar_operador, body.model_dump())
        auditoria_registrar(sesion["usuario"], "prepago_operador", codigo)
        return {"codigo": codigo}

    @router.put("/destino")
    def destino(body: DestinoIn, sesion: dict = Depends(solo_admin)):
        ejecutar(prepago.guardar_destino, body.correo)
        auditoria_registrar(sesion["usuario"], "prepago_destino", "recordatorios")
        return {"ok": True}

    @router.put("/plazo")
    def plazo(body: PlazoIn, sesion: dict = Depends(solo_admin)):
        prepago.guardar_plazo(body.dias)
        auditoria_registrar(sesion["usuario"], "prepago_plazo", "recordatorios", f"{body.dias} días")
        return {"recordatorio_dias": body.dias}

    return router
