"""Representación compartida de una plantilla para vista previa y envío."""

import hashlib
import json
import re
import unicodedata
from urllib.parse import urlparse

from fastapi import HTTPException

from plantilla_media import MAX_MEDIA_BYTES, obtener_media, ruta_media


EJEMPLOS = {"nombre": "David", "apellido": "Araya"}
MAX_CUERPO = 1024
MAX_ENCABEZADO = 60
MAX_PIE = 60
ERROR_TITULO_META = ("El título del mensaje no puede contener nuevas líneas, "
                     "caracteres con formato, emojis ni asteriscos.")
BOTONES_FIJOS = ["Me interesa", "No me interesa", "Dar de baja"]
ACCIONES_FIJAS = {"Me interesa": "interes", "No me interesa": "desinteres",
                  "Dar de baja": "baja"}
ACCIONES_RESPUESTA = {"ninguna", "interes", "desinteres", "baja", "consulta", "opciones"}
_VARIABLE = re.compile(r"\{([^{}]+)\}")


def _titulo_meta_invalido(texto: str) -> bool:
    """Detecta caracteres que Meta rechaza en encabezados de texto."""
    return any(
        caracter in "*_~`" or
        unicodedata.category(caracter) in ("Cc", "Cf", "Zl", "Zp") or
        0x1F000 <= ord(caracter) <= 0x1FAFF or
        0x2600 <= ord(caracter) <= 0x27BF or
        caracter in "\ufe0f\u20e3"
        for caracter in texto
    )


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


def version_opciones(opciones: list[dict]) -> str:
    """Identifica una versión de la lista sin incluir datos del paciente."""
    contenido = json.dumps(opciones, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(contenido).hexdigest()[:12]


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
                    "telefono": boton.get("phone_number") if tipo_boton == "PHONE_NUMBER" else None,
                    "accion": (ACCIONES_FIJAS.get(boton.get("text") or "", "ninguna")
                               if tipo_boton == "QUICK_REPLY" else None),
                    "opciones": None,
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
        botones = [{"tipo": "respuesta", "texto": nombre,
                    "accion": ACCIONES_FIJAS[nombre]} for nombre in BOTONES_FIJOS]
    elif not isinstance(botones, list):
        errores.append("La configuración de botones no es válida.")
        botones = []
    botones_normalizados = []
    if len(botones) > 3:
        errores.append("Cada plantilla admite como máximo 3 botones.")
    etiquetas_vistas = set()
    total_url = total_telefono = 0
    for boton in botones:
        if not isinstance(boton, dict):
            errores.append("Hay un botón inválido.")
            continue
        if any(valor is not None and not isinstance(valor, str)
               for valor in (boton.get("tipo"), boton.get("texto"), boton.get("accion"),
                             boton.get("url"), boton.get("telefono"))):
            errores.append("Los datos de un botón no son válidos.")
            continue
        tipo = (boton.get("tipo") or "").lower()
        etiqueta = (boton.get("texto") or "").strip()
        if tipo not in ("respuesta", "url", "telefono") or not etiqueta:
            errores.append("Hay un botón sin texto o de un tipo no compatible con la vista previa.")
            continue
        if len(etiqueta) > 20:
            errores.append(f"El texto del botón «{etiqueta}» supera 20 caracteres.")
        if etiqueta.casefold() in etiquetas_vistas:
            errores.append("Los botones deben tener textos distintos.")
        etiquetas_vistas.add(etiqueta.casefold())
        accion = (boton.get("accion") or ACCIONES_FIJAS.get(etiqueta, "ninguna")).lower()
        if tipo == "respuesta" and accion not in ACCIONES_RESPUESTA:
            errores.append(f"La acción del botón «{etiqueta}» no es válida.")
        if tipo == "respuesta" and etiqueta.casefold() == "eliminar":
            errores.append("ELIMINAR está reservado para borrar datos; usa otro texto de botón.")
        accion_reservada = next((valor for nombre, valor in ACCIONES_FIJAS.items()
                                 if nombre.casefold() == etiqueta.casefold()), None)
        if tipo == "respuesta" and accion_reservada and accion != accion_reservada:
            errores.append(f"El botón «{etiqueta}» debe conservar su acción {accion_reservada}.")
        opciones = []
        if tipo == "respuesta" and accion == "opciones":
            crudas = boton.get("opciones")
            if not isinstance(crudas, list) or not 1 <= len(crudas) <= 10:
                errores.append(f"El botón «{etiqueta}» necesita entre 1 y 10 opciones.")
            else:
                titulos = set()
                for opcion in crudas:
                    if not isinstance(opcion, dict) or not all(
                            isinstance(opcion.get(campo), str) for campo in ("texto", "mensaje")):
                        errores.append(f"Una opción de «{etiqueta}» no es válida.")
                        continue
                    titulo = opcion["texto"].strip()
                    mensaje_opcion = opcion["mensaje"].strip()
                    if not titulo or len(titulo) > 24 or not mensaje_opcion or len(mensaje_opcion) > 1024:
                        errores.append(f"Cada opción de «{etiqueta}» requiere título (máx. 24) y mensaje (máx. 1024).")
                    if titulo.casefold() in titulos:
                        errores.append(f"Las opciones de «{etiqueta}» deben tener títulos distintos.")
                    if titulo.casefold() in ("eliminar", "baja", "dar de baja", "stop"):
                        errores.append("La baja y ELIMINAR no se pueden usar como opciones de la lista.")
                    titulos.add(titulo.casefold())
                    opciones.append({"texto": titulo, "mensaje": mensaje_opcion})
        if tipo == "url":
            total_url += 1
            url = (boton.get("url") or "").strip()
            if "{{" in url or "}}" in url:
                errores.append(f"El enlace dinámico del botón «{etiqueta}» necesita parámetros no configurados.")
            elif not _url_valida(url):
                errores.append(f"El enlace del botón «{etiqueta}» no es válido.")
        if tipo == "telefono":
            total_telefono += 1
            numero = (boton.get("telefono") or "").strip()
            if not re.fullmatch(r"\+[1-9]\d{6,14}", numero):
                errores.append(f"El teléfono del botón «{etiqueta}» debe incluir código de país (ej. +56912345678).")
        botones_normalizados.append({"tipo": tipo, "texto": etiqueta,
                                     "url": (boton.get("url") or "").strip() if tipo == "url" else None,
                                     "telefono": (boton.get("telefono") or "").strip() if tipo == "telefono" else None,
                                     "accion": accion if tipo == "respuesta" else None,
                                     "opciones": opciones if accion == "opciones" and tipo == "respuesta" else None})
    if total_url > 2 or total_telefono > 1:
        errores.append("Se permiten como máximo 2 enlaces y 1 botón de llamada.")
    if sum(b["accion"] == "opciones" for b in botones_normalizados) > 1:
        errores.append("Solo se permite un botón que abra una lista de opciones por plantilla.")
    if (plantilla.get("whatsapp_template") != "hello_world"
            and not any(b["tipo"] == "respuesta" and b["accion"] == "baja"
                        for b in botones_normalizados)):
        errores.append("Debes incluir al menos un botón de respuesta rápida con la acción Baja.")
    media_id = plantilla.get("encabezado_media_id")
    encabezado_crudo = plantilla.get("encabezado_texto") or ""
    encabezado_texto = encabezado_crudo.strip()
    pie_texto = (plantilla.get("pie_texto") or "").strip()
    encabezado = None
    formato_esperado = (plantilla.get("whatsapp_header_format") or "").upper()
    if formato_esperado in ("IMAGE", "VIDEO") and not media_id:
        errores.append("La plantilla de Meta tiene encabezado multimedia, pero falta el archivo local.")
    if formato_esperado and formato_esperado not in ("TEXT", "IMAGE", "VIDEO"):
        errores.append("El formato de encabezado de Meta no es compatible con esta vista previa.")
    if len(encabezado_texto) > MAX_ENCABEZADO:
        errores.append(f"El encabezado supera el máximo de {MAX_ENCABEZADO} caracteres.")
    if _titulo_meta_invalido(encabezado_crudo):
        errores.append(ERROR_TITULO_META)
    if len(pie_texto) > MAX_PIE:
        errores.append(f"El pie supera el máximo de {MAX_PIE} caracteres.")
    if media_id and encabezado_texto:
        errores.append("Elige un encabezado de texto o multimedia, no ambos.")
    if any(c in encabezado_texto for c in "{}"):
        errores.append("El encabezado no admite variables.")
    if any(c in pie_texto for c in "{}\r\n"):
        errores.append("El pie no admite variables ni saltos de línea.")
    if encabezado_texto and not media_id:
        encabezado = {"tipo": "texto", "texto": encabezado_texto}
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

    return {"cuerpo": cuerpo, "pie": pie_texto,
            "variables": variables, "ejemplos": valores,
            "encabezado": encabezado, "botones": botones_normalizados,
            "errores": list(dict.fromkeys(errores)), "avisos": avisos}


def botones_para_meta(botones: list[dict]) -> list[dict]:
    """Convierte la configuración validada al componente BUTTONS de Meta."""
    resultado = []
    for boton in botones:
        tipo = boton["tipo"]
        meta = {"respuesta": "QUICK_REPLY", "url": "URL", "telefono": "PHONE_NUMBER"}[tipo]
        item = {"type": meta, "text": boton["texto"]}
        if tipo == "url":
            item["url"] = boton["url"]
        elif tipo == "telefono":
            item["phone_number"] = boton["telefono"]
        resultado.append(item)
    return resultado
