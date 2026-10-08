"""
Router del webhook oficial de WhatsApp Business Platform.

- GET /api/whatsapp/webhook : verificación inicial solicitada por Meta.
- POST /api/whatsapp/webhook : recepción de eventos (estados y mensajes).
"""

import hashlib
import hmac
import re

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from db import config_get, log_error
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
    secreto = (config_get("wa_app_secret") or "").strip()
    if not secreto:
        log_error("webhook: falta wa_app_secret en configuración; evento no procesado")
        return JSONResponse({"detail": "Webhook no configurado"}, status_code=503)

    cuerpo = await request.body()
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

    try:
        acciones = handler.procesar(payload)
    except Exception as e:
        # No romper el sistema; Meta reintentaría y volvería a fallar.
        log_error("webhook: fallo procesando evento", e)
        return JSONResponse({"status": "ok", "acciones": []}, status_code=200)

    return JSONResponse({"status": "ok", "acciones": acciones}, status_code=200)
