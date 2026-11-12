"""Composición y trazabilidad de respuestas manuales de Historial."""

from dataclasses import dataclass
from urllib.parse import quote


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


def contexto_original(texto, tipo=None):
    if tipo == "audio":
        return "[El paciente envió un audio.]"
    return texto if texto and texto.strip() else "[El paciente envió un mensaje sin texto.]"


def preparar_texto(introduccion, original, cierre=""):
    contexto = f'{introduccion.strip()}\n\nMensaje original:\n«{original}»'
    return f"{contexto}\n\n{cierre}" if cierre else contexto


def cabe_en_enlace(numero, preparado):
    return len(f"https://wa.me/{numero}?text={quote(preparado, safe='')}") <= 2000
