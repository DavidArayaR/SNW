"""Catálogo y composición de respuestas de call center."""

import re
from dataclasses import dataclass
from urllib.parse import quote

from snw.whatsapp.intenciones_call_center import clasificar, es_pregunta_de_precio, normalizar


@dataclass(frozen=True)
class RespuestaPreparada:
    grupo: str
    etiqueta: str
    aviso: str
    introduccion: str


CATEGORIAS = {
    "horas": RespuestaPreparada(
        "Agenda y atención", "Horas y agendamiento",
        "Gracias por escribirnos. Usa el botón para consultar la disponibilidad de horas "
        "o pedir ayuda para agendar o cambiar una cita.",
        "Hola, quisiera consultar por las horas y el agendamiento en {clinica}.",
    ),
    "profesionales": RespuestaPreparada(
        "Agenda y atención", "Profesionales y especialidades",
        "Gracias por escribirnos. El equipo de atención puede orientarte sobre los profesionales "
        "y especialidades disponibles. Usa el botón para enviarle tu consulta.",
        "Hola, quisiera consultar por un profesional o una especialidad en {clinica}.",
    ),
    "precios": RespuestaPreparada(
        "Precios y cobertura", "Valores y cobertura Fonasa",
        "Gracias por escribirnos. El equipo de atención puede orientarte sobre los valores vigentes "
        "y las opciones de cobertura que correspondan. Usa el botón para consultar.",
        "Hola, quisiera consultar por valores o cobertura en {clinica}.",
    ),
    "precio_especial": RespuestaPreparada(
        "Precios y cobertura", "Preguntas sobre el precio especial",
        "¡Hola! Gracias por escribirnos. En nuestro call center pueden orientarte sobre los valores especiales "
        "disponibles y sus condiciones. Usa el botón para enviarles tu consulta.",
        "Hola, quisiera consultar por los precios especiales disponibles en {clinica}.",
    ),
    "precio_terapia": RespuestaPreparada(
        "Precios y cobertura", "Preguntas sobre el precio de terapia",
        "¡Hola! Gracias por contactarnos. En nuestro call center pueden orientarte sobre los valores "
        "actuales de las sesiones de terapia y las opciones de atención. Usa el botón para enviarles tu consulta.",
        "Hola, quisiera consultar por el precio de las sesiones de terapia en {clinica}.",
    ),
    "contacto": RespuestaPreparada(
        "Consultas y contacto", "Hablar con el equipo",
        "Gracias por contactarnos. Usa el botón para abrir un chat con nuestro equipo de atención "
        "y enviarle tu solicitud.",
        "Hola, quisiera hablar con el equipo de atención de {clinica}.",
    ),
    "informacion": RespuestaPreparada(
        "Consultas y contacto", "Información y trámites",
        "Gracias por escribirnos. Nuestro equipo de atención puede orientarte sobre los servicios "
        "o ayudarte a revisar una solicitud. Usa el botón para continuar.",
        "Hola, quisiera pedir información o revisar una solicitud con el equipo de {clinica}.",
    ),
    "consulta_por_aclarar": RespuestaPreparada(
        "Consultas y contacto", "Consulta por aclarar",
        "Claro, podemos orientarte. Usa el botón y cuéntale al equipo en un solo mensaje qué necesitas "
        "para que pueda ayudarte.",
        "Hola, quisiera explicar mi consulta al equipo de atención de {clinica}.",
    ),
}


MOTIVOS_CATEGORIA = {
    "CONSULTA_PRECIO_ESPECIAL": "precio_especial",
    "CONSULTA_PRECIO_TERAPIA": "precio_terapia",
    "CONSULTA_PRECIOS": "precios",
    "CONSULTA_HORAS": "horas",
    "CONSULTA_MEDICO": "profesionales",
    "SOLICITA_CONTACTO": "contacto",
    "SOLICITA_INFORMACION": "informacion",
}


def motivo_respuesta_automatica(texto, catalogo):
    """Elige el tipo preparado para una consulta que ya pasó el filtro de envío."""
    t = normalizar(texto)
    motivos = clasificar(texto, catalogo).motivos
    if re.search(r"\b(?:precios? especial\w*|descuentos?|beneficios? especiales|promociones?|promocion)\b", t):
        return "CONSULTA_PRECIO_ESPECIAL"
    literal = max(
        ((len(frase), motivo) for motivo, frases in catalogo.items()
         if motivo in MOTIVOS_CATEGORIA
         for frase in frases if f" {frase} " in f" {t} "),
        key=lambda coincidencia: coincidencia[0], default=(0, None),
    )[1]
    if es_pregunta_de_precio(texto) or literal == "CONSULTA_PRECIOS":
        if re.search(r"\b(?:terapias?|psicoterapias?|psicolog\w*)\b", t):
            return "CONSULTA_PRECIO_TERAPIA"
        return "CONSULTA_PRECIOS"
    if literal:
        return literal
    if re.search(r"\b(?:fonasa|isapre|cobertura)\b", t) and "CONSULTA_HORAS" not in motivos:
        return "CONSULTA_PRECIOS"
    return next((motivo for motivo in MOTIVOS_CATEGORIA if motivo in motivos and motivo != "CONSULTA_PRECIOS"),
                "SOLICITA_INFORMACION")


def contexto_original(texto, tipo=None):
    if tipo == "audio":
        return "[El paciente envió un audio.]"
    return texto if texto and texto.strip() else "[El paciente envió un mensaje sin texto.]"


def preparar_texto(introduccion, original, cierre=""):
    contexto = f'{introduccion.strip()}\n\nMensaje original:\n«{original}»'
    return f"{contexto}\n\n{cierre}" if cierre else contexto


def cabe_en_enlace(numero, preparado):
    return len(f"https://wa.me/{numero}?text={quote(preparado, safe='')}") <= 2000
