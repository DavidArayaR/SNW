"""Catálogo y validación de los cuatro emisores WhatsApp de SNW."""

import re
from contextvars import ContextVar

CENTROS = {
    "SMPT": "Salud Mental Para Todos",
    "MS": "Centro Mentalidad Saludable",
    "PS": "PROSALUD",
    "CP": "Centro Psiquiatrico Huerfanos",
}

# Contexto aislado por solicitud de webhook; los callbacks diferidos copian el
# contexto antes de arrancar su hilo para conservar el emisor de la respuesta.
EMISOR_WEBHOOK: ContextVar[dict | None] = ContextVar("emisor_webhook", default=None)


def emisor_webhook_actual() -> dict | None:
    return EMISOR_WEBHOOK.get()


def claves_centro(codigo: str) -> tuple[str, str]:
    if codigo not in CENTROS:
        raise ValueError("Centro emisor desconocido")
    return f"{codigo}_WSP_PHONE_NUMBER", f"{codigo}_WSP_PHONE_NUMBER_ID"


def listar_canales(config: dict | None = None) -> list[dict]:
    from db import config_all
    valores = config if config is not None else config_all()
    canales = []
    for codigo, nombre in CENTROS.items():
        clave_numero, clave_id = claves_centro(codigo)
        numero = str(valores.get(clave_numero) or "").strip()
        phone_id = str(valores.get(clave_id) or "").strip()
        razones = []
        if not numero:
            razones.append("Falta número visible")
        elif not re.fullmatch(r"\+?[1-9]\d{7,14}", numero):
            razones.append("Número visible inválido")
        if not phone_id:
            razones.append("Falta Phone Number ID")
        elif not re.fullmatch(r"[1-9]\d{4,30}", phone_id):
            razones.append("Phone Number ID inválido")
        canales.append({"codigo": codigo, "nombre": nombre, "phoneNumber": numero or None,
                        "phoneNumberId": phone_id or None, "configured": not razones,
                        "problemas": razones})
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
