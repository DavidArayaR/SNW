"""
Router del webhook oficial de WhatsApp Business Platform.

- GET /api/whatsapp/webhook : verificación inicial solicitada por Meta.
- POST /api/whatsapp/webhook : recepción de eventos (estados y mensajes).
- GET/POST /api/whatsapp/webhook/{codigo} : callback exclusivo de una clínica.
"""

import hashlib
import hmac
import re

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from starlette.requests import ClientDisconnect

from db import config_all, config_get, log_error
from whatsapp_channels import catalogo_centros, listar_canales
from whatsapp_service import WebhookHandler

router = APIRouter(prefix="/api/whatsapp", tags=["whatsapp"])
handler = WebhookHandler()


def firma_meta_valida(cuerpo: bytes, firma: str, secreto: str) -> bool:
    """Comprueba la firma SHA-256 de Meta sobre el cuerpo HTTP original."""
    if not re.fullmatch(r"sha256=[0-9a-fA-F]{64}", firma or ""):
        return False
    esperada = "sha256=" + hmac.new(
        secreto.encode("utf-8"), cuerpo, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(esperada, firma.lower())


@router.get("/webhook")
def verificar_webhook(
    hub_mode: str | None = Query(None, alias="hub.mode"),
    hub_verify_token: str | None = Query(None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(None, alias="hub.challenge"),
):
    """Verificación inicial: Meta valida que nuestro backend posee el URL."""
    if not hub_mode or not hub_verify_token or hub_challenge is None:
        return PlainTextResponse(
            "Faltan parámetros de verificación", status_code=400
        )
    resultado = handler.verificar(hub_mode, hub_verify_token, hub_challenge)
    if resultado is False:
        return PlainTextResponse(
            "Código de verificación inválido", status_code=403
        )
    return PlainTextResponse(resultado)


@router.post("/webhook")
async def recibir_evento(request: Request):
    """Solo procesa eventos con firma válida; errores internos conservan el 200."""
    return await _recibir_evento(request)


def _canal_webhook(codigo: str) -> tuple[dict, dict] | None:
    """Resuelve una clínica vigente, incluso pausada, pero nunca eliminada."""
    config = config_all(refresh=True)
    if codigo not in catalogo_centros(config, incluir_desactivadas=True):
        return None
    canal = next((c for c in listar_canales(config, incluir_inactivas=True)
                  if c["codigo"] == codigo), None)
    return (config, canal) if canal else None


@router.get("/webhook/{codigo}")
def verificar_webhook_centro(
    codigo: str,
    hub_mode: str | None = Query(None, alias="hub.mode"),
    hub_verify_token: str | None = Query(None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(None, alias="hub.challenge"),
):
    encontrado = _canal_webhook(codigo)
    if not encontrado:
        return PlainTextResponse("Webhook no disponible", status_code=404)
    config, _ = encontrado
    esperado = str(config.get(f"{codigo}_WSP_VERIFY_TOKEN") or "").strip()
    if hub_mode != "subscribe" or not esperado or not hub_verify_token or hub_challenge is None:
        return PlainTextResponse("Verificación inválida", status_code=403)
    if not hmac.compare_digest(hub_verify_token, esperado):
        return PlainTextResponse("Verificación inválida", status_code=403)
    return PlainTextResponse(hub_challenge)


def _evento_corresponde_canal(payload: dict, canal: dict) -> bool:
    """No procesa en una URL eventos de otra WABA o Phone Number ID."""
    waba_id = str(canal.get("wabaId") or "")
    phone_id = str(canal.get("phoneNumberId") or "")
    entradas = payload.get("entry") or []
    if not isinstance(entradas, list):
        return False
    for entrada in entradas:
        if not isinstance(entrada, dict) or str(entrada.get("id") or "") != waba_id:
            return False
        cambios = entrada.get("changes") or []
        if not isinstance(cambios, list):
            return False
        for cambio in cambios:
            if not isinstance(cambio, dict) or not isinstance(cambio.get("value"), dict):
                return False
            metadata = cambio["value"].get("metadata") or {}
            if not isinstance(metadata, dict):
                return False
            id_evento = str(metadata.get("phone_number_id") or "")
            if (cambio.get("field") == "messages" and id_evento != phone_id) or (
                    id_evento and id_evento != phone_id):
                return False
    return True


@router.post("/webhook/{codigo}")
async def recibir_evento_centro(codigo: str, request: Request):
    encontrado = _canal_webhook(codigo)
    if not encontrado:
        return JSONResponse({"detail": "Webhook no disponible"}, status_code=404)
    config, canal = encontrado
    if not canal.get("wabaId") or not canal.get("phoneNumberId"):
        return JSONResponse({"detail": "Centro emisor incompleto"}, status_code=503)
    secreto = (config.get(f"{codigo}_WSP_APP_SECRET") or
               config.get("wa_app_secret") or "").strip()
    return await _recibir_evento(request, secreto=secreto, canal=canal)


async def _recibir_evento(request: Request, *, secreto: str | None = None,
                          canal: dict | None = None):
    if secreto is None:
        secreto = (config_get("wa_app_secret") or "").strip()
    if not secreto:
        log_error("webhook: falta wa_app_secret en configuración; evento no procesado")
        return JSONResponse({"detail": "Webhook no configurado"}, status_code=503)

    try:
        cuerpo = await request.body()
    except ClientDisconnect:
        # El emisor cortó la conexión antes de entregar el cuerpo completo.
        # No verificar una firma parcial ni procesar un evento incompleto.
        return Response(status_code=204)
    if not firma_meta_valida(
        cuerpo, request.headers.get("X-Hub-Signature-256", ""), secreto
    ):
        return JSONResponse({"detail": "Firma del webhook inválida"}, status_code=403)

    try:
        payload = await request.json()
    except Exception as e:
        log_error("webhook: cuerpo de la petición no es JSON válido", e)
        return JSONResponse({"status": "ok", "acciones": []})

    if not isinstance(payload, dict) or not payload.get("entry"):
        return JSONResponse({"status": "ok", "acciones": []}, status_code=200)

    if canal and not _evento_corresponde_canal(payload, canal):
        return JSONResponse({"detail": "Evento de otro centro emisor"}, status_code=403)

    try:
        acciones = handler.procesar(payload)
    except Exception as e:
        # No romper el sistema; Meta reintentaría y volvería a fallar.
        log_error("webhook: fallo procesando evento", e)
        return JSONResponse({"status": "ok", "acciones": []}, status_code=200)

    return JSONResponse({"status": "ok", "acciones": acciones}, status_code=200)
