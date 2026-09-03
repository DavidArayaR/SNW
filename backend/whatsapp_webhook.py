"""
Router del webhook oficial de WhatsApp Business Platform.

- GET /api/whatsapp/webhook : verificación inicial solicitada por Meta.
- POST /api/whatsapp/webhook : recepción de eventos (estados y mensajes).
"""

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from db import log_error
from whatsapp_service import WebhookHandler

router = APIRouter(prefix="/api/whatsapp", tags=["whatsapp"])
handler = WebhookHandler()


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
    """Recibe eventos de Meta. Retorna 200 aunque un evento falle para no reintentar."""
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
