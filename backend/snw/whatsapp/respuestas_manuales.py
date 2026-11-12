"""Composición y trazabilidad de respuestas manuales de Historial."""

from urllib.parse import quote


CATEGORIAS = {
    "general": ("consulta_callcenter", "Hola, quisiera comunicarme con el call center de {clinica}."),
    "precio_especial": ("manual_precio_especial",
                        "Hola, quisiera consultar por los precios especiales disponibles en {clinica}."),
    "precio_terapia": ("manual_precio_terapia",
                       "Hola, quisiera consultar por el precio de las sesiones de terapia en {clinica}."),
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
