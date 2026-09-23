"""Utilidad interna para registrar manejadores sin acoplarlos a la app."""

from fastapi import APIRouter


def crear_router(etiqueta: str, handlers: dict, rutas: tuple) -> APIRouter:
    router = APIRouter(tags=[etiqueta])
    for ruta, metodo, nombre, estado in rutas:
        opciones = {"methods": [metodo]}
        if estado is not None:
            opciones["status_code"] = estado
        router.add_api_route(ruta, handlers[nombre], **opciones)
    return router
