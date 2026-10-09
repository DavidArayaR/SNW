"""Representación compartida de una plantilla para vista previa y envío."""

import re
from urllib.parse import urlparse

from fastapi import HTTPException

from plantilla_media import MAX_MEDIA_BYTES, obtener_media, ruta_media


EJEMPLOS = {"nombre": "David", "apellido": "Araya"}
MAX_CUERPO = 1024
BOTONES_FIJOS = ["Me interesa", "No me interesa", "Dar de baja"]
_VARIABLE = re.compile(r"\{([^{}]+)\}")


def variables_del_cuerpo(texto: str) -> list[str]:
    """Orden único de variables, idéntico al orden de parámetros para Meta."""
    return list(dict.fromkeys(m.group(1).strip() for m in _VARIABLE.finditer(texto or "")))


def _url_valida(url: str) -> bool:
    try:
        valor = urlparse(url)
        return valor.scheme in ("http", "https") and bool(valor.hostname) and not any(
            c.isspace() for c in url)
    except ValueError:
        return False


def componentes_para_vista(componentes: list | None) -> dict:
    """Extrae solo componentes visibles, sin handles ni datos internos de Meta."""
    resultado = {"botones": None, "encabezado_texto": None,
                 "pie_texto": None, "whatsapp_header_format": None}
    for componente in componentes or []:
        tipo = (componente.get("type") or "").upper()
        if tipo == "HEADER":
            formato = (componente.get("format") or "").upper()
            resultado["whatsapp_header_format"] = formato
            if formato == "TEXT":
                resultado["encabezado_texto"] = componente.get("text") or ""
        elif tipo == "FOOTER":
            resultado["pie_texto"] = componente.get("text") or ""
        elif tipo == "BUTTONS":
            botones = []
            for boton in componente.get("buttons") or []:
                tipo_boton = (boton.get("type") or "").upper()
                botones.append({
                    "tipo": {"QUICK_REPLY": "respuesta", "URL": "url",
                             "PHONE_NUMBER": "telefono"}.get(tipo_boton, "no_compatible"),
                    "texto": boton.get("text") or "",
                    "url": boton.get("url") if tipo_boton == "URL" else None,
                })
            resultado["botones"] = botones
    return resultado


def presentar_plantilla(plantilla: dict, ejemplos: dict | None = None) -> dict:
    """Normaliza el contenido que el envío real consume sin enviar nada.

    Los ejemplos no se guardan en la plantilla ni se mezclan con pacientes.
    Si se pasa un diccionario explícito, los campos omitidos cuentan como
    faltantes; sin diccionario se usan los ejemplos generales del editor.
    """
    texto = plantilla.get("texto") or ""
    valores = EJEMPLOS.copy() if ejemplos is None else {
        k: str(v or "").strip() for k, v in ejemplos.items() if k in EJEMPLOS}
    errores: list[str] = []
    avisos: list[str] = []
    variables = variables_del_cuerpo(texto)
    resto = _VARIABLE.sub("", texto)
    if "{" in resto or "}" in resto:
        errores.append("Hay llaves incompletas o variables con formato inválido.")
    for variable in variables:
        if variable not in EJEMPLOS:
            errores.append(f"La variable {{{variable}}} no está admitida. Usa {{nombre}} o {{apellido}}.")
        elif not valores.get(variable):
            errores.append(f"Falta un ejemplo para {{{variable}}}.")
    if not texto.strip():
        errores.append("El cuerpo de la plantilla está vacío.")
    if len(texto) > MAX_CUERPO:
        errores.append(f"El cuerpo supera el máximo de {MAX_CUERPO} caracteres.")
    cuerpo = _VARIABLE.sub(
        lambda m: valores.get(m.group(1).strip(), f"⟦{m.group(1).strip()}: sin ejemplo⟧"), texto)

    botones = plantilla.get("botones")
    cta = plantilla.get("cta")
    if cta and botones is None:
        botones = [{"tipo": "url", "texto": cta.get("texto") or "Abrir",
                    "url": cta.get("url") or ""}]
    if botones is None and plantilla.get("whatsapp_template") == "hello_world":
        botones = []
    if botones is None:
        botones = [{"tipo": "respuesta", "texto": nombre} for nombre in BOTONES_FIJOS]
    elif not isinstance(botones, list):
        errores.append("La configuración de botones no es válida.")
        botones = []
    botones_normalizados = []
    for boton in botones:
        if not isinstance(boton, dict):
            errores.append("Hay un botón inválido.")
            continue
        tipo = (boton.get("tipo") or "").lower()
        etiqueta = (boton.get("texto") or "").strip()
        if tipo not in ("respuesta", "url", "telefono") or not etiqueta:
            errores.append("Hay un botón sin texto o de un tipo no compatible con la vista previa.")
            continue
        if tipo == "url":
            url = (boton.get("url") or "").strip()
            if "{{" in url or "}}" in url:
                errores.append(f"El enlace dinámico del botón «{etiqueta}» necesita parámetros no configurados.")
            elif not _url_valida(url):
                errores.append(f"El enlace del botón «{etiqueta}» no es válido.")
        botones_normalizados.append({"tipo": tipo, "texto": etiqueta,
                                     "url": boton.get("url") if tipo == "url" else None})
    media_id = plantilla.get("encabezado_media_id")
    encabezado = None
    formato_esperado = (plantilla.get("whatsapp_header_format") or "").upper()
    if formato_esperado in ("IMAGE", "VIDEO") and not media_id:
        errores.append("La plantilla de Meta tiene encabezado multimedia, pero falta el archivo local.")
    if formato_esperado and formato_esperado not in ("TEXT", "IMAGE", "VIDEO"):
        errores.append("El formato de encabezado de Meta no es compatible con esta vista previa.")
    if plantilla.get("encabezado_texto") and not media_id:
        encabezado = {"tipo": "texto", "texto": plantilla["encabezado_texto"]}
        if "{{" in plantilla["encabezado_texto"]:
            errores.append("El encabezado de texto tiene variables de Meta sin ejemplo local.")
    if media_id:
        try:
            media = obtener_media(media_id)
            if ruta_media(media, vista=True).stat().st_size > MAX_MEDIA_BYTES or \
                    ruta_media(media).stat().st_size > MAX_MEDIA_BYTES:
                errores.append("El encabezado supera el límite de 3,5 MB.")
            if media.get("formato_meta") not in ("IMAGE", "VIDEO"):
                errores.append("El formato del encabezado no es compatible con esta plantilla.")
            if formato_esperado and formato_esperado != media.get("formato_meta"):
                errores.append("El formato del archivo no coincide con el encabezado aprobado en Meta.")
            encabezado = {"tipo": media.get("tipo"), "formato_meta": media.get("formato_meta"),
                          "media_id": media_id, "url": f"/api/plantillas/media/{media_id}"}
        except (HTTPException, OSError):
            errores.append("No se encontró el archivo del encabezado. Vuelve a subirlo.")
    if media_id and not plantilla.get("encabezado_meta_registrado"):
        avisos.append("El encabezado aún debe registrarse y aprobarse en Meta antes del envío.")

    return {"cuerpo": cuerpo, "pie": plantilla.get("pie_texto") or "",
            "variables": variables, "ejemplos": valores,
            "encabezado": encabezado, "botones": botones_normalizados,
            "errores": list(dict.fromkeys(errores)), "avisos": avisos}
