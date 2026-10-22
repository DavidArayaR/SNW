"""Catálogo de emisores WhatsApp, incluidos centros añadidos en Configuración."""

import json
import re
from contextvars import ContextVar

CENTROS = {
    "SMPT": "Salud Mental Para Todos",
    "MS": "Centro Mentalidad Saludable",
    "PS": "PROSALUD",
    "CP": "Centro Psiquiatrico Huerfanos",
    "VS": "VIDA Y SALUD",
}

# Contexto aislado por solicitud de webhook; los callbacks diferidos copian el
# contexto antes de arrancar su hilo para conservar el emisor de la respuesta.
EMISOR_WEBHOOK: ContextVar[dict | None] = ContextVar("emisor_webhook", default=None)


def emisor_webhook_actual() -> dict | None:
    return EMISOR_WEBHOOK.get()


def claves_centro(codigo: str) -> tuple[str, str]:
    if not re.fullmatch(r"[A-Z][A-Z0-9]{1,7}", codigo or ""):
        raise ValueError("Centro emisor desconocido")
    return f"{codigo}_WSP_PHONE_NUMBER", f"{codigo}_WSP_PHONE_NUMBER_ID"


def catalogo_centros(config: dict | None = None, *, incluir_inactivas: bool = False,
                    incluir_desactivadas: bool = False) -> dict[str, str]:
    from db import config_all
    valores = config if config is not None else config_all()
    centros = dict(CENTROS)
    try:
        adicionales = json.loads(valores.get("wa_clinicas_adicionales") or "[]")
    except (TypeError, ValueError):
        adicionales = []
    if isinstance(adicionales, list):
        for item in adicionales:
            if not isinstance(item, dict):
                continue
            codigo = str(item.get("codigo") or "")
            visible = (item.get("activo") is not False or incluir_inactivas or
                       (incluir_desactivadas and item.get("eliminada") is False))
            if codigo in CENTROS:
                if not visible:
                    centros.pop(codigo, None)
                continue
            if not re.fullmatch(r"C[A-F0-9]{7}", codigo):
                continue
            if not visible:
                continue
            nombre = str(item.get("nombre") or "").strip()
            if nombre and len(nombre) <= 100:
                centros[codigo] = nombre
    return centros


def credenciales_centro(phone_id: str, config: dict | None = None) -> dict:
    """Credenciales del emisor, sin enviarlas a endpoints públicos."""
    from db import config_all
    valores = config if config is not None else config_all()
    canal = resolver_phone_id(phone_id, valores)
    if not canal:
        return {"token": "", "waba_id": ""}
    codigo = canal["codigo"]
    return {
        "token": str(valores.get(f"{codigo}_WSP_ACCESS_TOKEN") or "").strip(),
        "waba_id": str(valores.get(f"{codigo}_WSP_WABA_ID") or "").strip(),
    }


def listar_canales(config: dict | None = None, *, incluir_inactivas: bool = False) -> list[dict]:
    from db import config_all
    valores = config if config is not None else config_all()
    canales = []
    activos = catalogo_centros(valores)
    for codigo, nombre in catalogo_centros(valores, incluir_inactivas=incluir_inactivas).items():
        clave_numero, clave_id = claves_centro(codigo)
        numero = str(valores.get(clave_numero) or "").strip()
        phone_id = str(valores.get(clave_id) or "").strip()
        waba_id = str(valores.get(f"{codigo}_WSP_WABA_ID") or "").strip()
        crm_numero = str(valores.get(f"{codigo}_CRM_PHONE_NUMBER") or "").strip()
        razones = []
        if not numero:
            razones.append("Falta número visible")
        elif not re.fullmatch(r"\+?[1-9]\d{7,14}", numero):
            razones.append("Número visible inválido")
        if not phone_id:
            razones.append("Falta Phone Number ID")
        elif not re.fullmatch(r"[1-9]\d{4,30}", phone_id):
            razones.append("Phone Number ID inválido")
        if not waba_id:
            razones.append("Falta WABA ID")
        elif not re.fullmatch(r"[1-9]\d{4,30}", waba_id):
            razones.append("WABA ID inválido")
        if not str(valores.get(f"{codigo}_WSP_ACCESS_TOKEN") or "").strip():
            razones.append("Falta access token de la clínica")
        avisos = []
        if not crm_numero:
            avisos.append("Número CRM pendiente")
        elif not re.fullmatch(r"\+?[1-9]\d{7,14}", crm_numero):
            avisos.append("Número CRM inválido")
        canales.append({"codigo": codigo, "nombre": nombre, "activa": codigo in activos,
                        "phoneNumber": numero or None,
                        "phoneNumberId": phone_id or None, "wabaId": waba_id or None,
                        "crmPhoneNumber": crm_numero or None,
                        "configured": not razones, "problemas": razones, "avisos": avisos})
    for campo, etiqueta in (("phoneNumber", "Número visible"), ("phoneNumberId", "Phone Number ID")):
        usados = {}
        for canal in canales:
            valor = canal[campo]
            if not valor:
                continue
            comparable = valor.lstrip("+") if campo == "phoneNumber" else valor
            if comparable in usados:
                motivo = f"{etiqueta} duplicado con {usados[comparable]}"
                canal["problemas"].append(motivo)
                next(c for c in canales if c["codigo"] == usados[comparable])["problemas"].append(
                    f"{etiqueta} duplicado con {canal['codigo']}")
            else:
                usados[comparable] = canal["codigo"]
    for canal in canales:
        canal["configured"] = not canal["problemas"]
    return canales


def resolver_centro(codigo: str, config: dict | None = None) -> dict:
    canal = next((c for c in listar_canales(config) if c["codigo"] == codigo), None)
    if canal is None:
        raise ValueError("Centro emisor desconocido")
    return canal


def resolver_phone_id(phone_id: str, config: dict | None = None) -> dict | None:
    coincidencias = [c for c in listar_canales(config) if c["phoneNumberId"] == str(phone_id or "").strip()]
    return coincidencias[0] if len(coincidencias) == 1 else None
