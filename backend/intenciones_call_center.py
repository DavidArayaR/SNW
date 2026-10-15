"""Clasificación local y conservadora de consultas recibidas por WhatsApp.

No envía contenido a servicios externos. Los patrones adicionales se leen de
`configuracion` y se validan antes de usarlos.
"""

import json
import re
import unicodedata
from dataclasses import dataclass
from difflib import get_close_matches

MOTIVOS = (
    "CONSULTA_HORAS", "CONSULTA_MEDICO", "CONSULTA_PRECIOS",
    "SOLICITA_CONTACTO", "SOLICITA_INFORMACION", "CONSULTA_AMBIGUA",
)

PATRONES = {
    "CONSULTA_HORAS": (
        r"\b(?:tienen|hay|quedan|tendra[n]?|disponib\w*|necesito|busco|quiero|pedir|tomar|reservar|agendar|cambiar|otra|reagendar)\b.{0,35}\b(?:hora[s]?|cupo[s]?|agenda|cita[s]?)\b",
        r"\b(?:hora[s]?|cupo[s]?|agenda|cita[s]?)\b.{0,35}\b(?:para|disponib\w*|manana|hoy|semana|proxima|otra)\b",
        r"\b(?:cuando|a que hora)\b.{0,30}\b(?:atienden|atiende|hay|puedo ir)\b",
    ),
    "CONSULTA_MEDICO": (
        r"\b(?:doctor\w*|medic\w*|especialista[s]?|profesional\w*|psicolog\w*|psiquiatr\w*)\b.{0,45}\b(?:atiende|atendiendo|disponib\w*|hay|tienen|trabaja|hora[s]?)\b",
        r"\b(?:atiende|atendiendo|disponib\w*|hay|tienen|trabaja)\b.{0,45}\b(?:doctor\w*|medic\w*|especialista[s]?|profesional\w*|psicolog\w*|psiquiatr\w*)\b",
        r"\b(?:hay|tienen|disponib\w*)\b.{0,35}\b(?:doctor\w*|medic\w*|especialista[s]?|profesional\w*|psicolog\w*|psiquiatr\w*)\b",
    ),
    "CONSULTA_PRECIOS": (
        r"\b(?:cuanto|cual|que)\b.{0,25}\b(?:cuesta|vale|precio[s]?|valor|arancel|servicio[s]?|especialidad\w*)\b",
        r"\b(?:precio[s]?|valor|arancel|costo[s]?|especialidad\w*|servicio[s]?)\b",
    ),
    "SOLICITA_CONTACTO": (
        r"\b(?:me pueden|pueden|podrian|quiero|necesito|puedo)\b.{0,35}\b(?:llamar|llamen|contactar|hablar|conversar|comunicar)\b",
        r"\b(?:persona|ejecutiv\w*|call center|operador\w*)\b",
    ),
    "SOLICITA_INFORMACION": (
        r"\b(?:quiero|necesito|me gustaria|pueden|podrian|quisiera)\b.{0,40}\b(?:saber|informacion|info|explicar|detalle[s]?)\b",
        r"\b(?:mas|mayor)\s+(?:info|informacion|detalle[s]?)\b",
        r"\b(?:de que se trata|como funciona|me explican|me cuentan)\b",
    ),
    "CONSULTA_AMBIGUA": (
        r"^(?:hola|buenas|holi|una consulta|consulta|necesito ayuda|ayuda|buenos dias|buenas tardes)[ !?.]*$",
        r"\b(?:una consulta|tengo una duda|necesito ayuda|me ayudan)\b",
    ),
}

_NO_DERIVAR = re.compile(
    r"^(?:si|sip|ok|dale|listo|gracias|no|no gracias|confirmo|confirmar|"
    r"confirmado|acepto|me interesa|no me interesa|dar de baja|baja|stop|eliminar)[ !?.]*$"
)
_CANCELACION = re.compile(r"\b(?:no puedo asistir|no podre asistir|cancelo mi hora|cancelar mi hora)\b")
_CONFIRMACION = re.compile(r"\b(?:confirmo|confirmar|confirmado|asistire|voy a ir)\b")

# Señales combinadas: ni la longitud ni los emojis prueban automatización.
_BOT_EXPLICITO = re.compile(r"\b(?:mensaje automatico|respuesta automatica|asistente virtual|soy un bot|nuestro bot)\b")
_BOT_INSTITUCIONAL = re.compile(
    r"\b(?:gracias por (?:comunicarte|contactarnos|escribirnos)|bienvenid[oa] a|"
    r"has contactado con|te damos la bienvenida)\b"
)
_BOT_ACUSE = re.compile(
    r"\b(?:hemos recibido tu (?:mensaje|consulta)|recibimos tu mensaje|"
    r"te responderemos|responderemos lo antes posible|un ejecutivo se comunicara|"
    r"uno de nuestros asesores|en breve te responderemos)\b"
)
_BOT_MENU = re.compile(r"\b(?:presiona|escribe|selecciona|elige|marca)\s*(?:la opcion\s*)?[1-9]\b")
_BOT_HORARIO = re.compile(r"\b(?:fuera de horario|nuestro horario|horario de atencion)\b")


def detectar_automatizacion(texto: str) -> tuple[str, tuple[str, ...]]:
    """Evidencia lingüística, nunca confirmación técnica del origen del mensaje."""
    t = normalizar(texto)
    if not t:
        return "SIN_EVIDENCIA_AUTOMATIZACION", ()
    indicadores = tuple(nombre for nombre, patron in (
        ("declaracion_automatica", _BOT_EXPLICITO),
        ("saludo_institucional", _BOT_INSTITUCIONAL),
        ("acuse_generico", _BOT_ACUSE),
        ("menu", _BOT_MENU),
        ("horario_empresarial", _BOT_HORARIO),
    ) if patron.search(t))
    if ("declaracion_automatica" in indicadores or
            {"saludo_institucional", "acuse_generico"}.issubset(indicadores) or
            "menu" in indicadores and len(indicadores) >= 2):
        return "AUTOMATICO_ALTA_CONFIANZA", indicadores
    if indicadores:
        return "AUTOMATICO_PROBABLE", indicadores
    return "SIN_EVIDENCIA_AUTOMATIZACION", ()


def normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", (texto or "").casefold())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", texto)).strip()[:2000]


@dataclass(frozen=True)
class Clasificacion:
    motivos: tuple[str, ...] = ()
    confirma: bool = False
    cancela: bool = False
    automatizacion: str = "SIN_EVIDENCIA_AUTOMATIZACION"
    indicadores: tuple[str, ...] = ()

    @property
    def derivar(self) -> bool:
        return bool(self.motivos) and self.automatizacion == "SIN_EVIDENCIA_AUTOMATIZACION"


def patrones_configurados(valor: str) -> dict[str, tuple[str, ...]]:
    """Acepta solo frases literales cortas; nunca regex inyectada desde UI."""
    try:
        raw = json.loads(valor or "{}")
    except (TypeError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    resultado = {}
    for motivo, frases in raw.items():
        if motivo not in MOTIVOS or not isinstance(frases, list):
            continue
        limpias = tuple(dict.fromkeys(normalizar(f) for f in frases
                                     if isinstance(f, str) and 4 <= len(f) <= 80
                                     and not re.search(r"\d|@", f)))[:30]
        resultado[motivo] = limpias
    return resultado


def clasificar(texto: str, catalogo: dict[str, tuple[str, ...]] | None = None) -> Clasificacion:
    t = normalizar(texto)
    automatizacion, indicadores = detectar_automatizacion(texto)
    if not t or _NO_DERIVAR.fullmatch(t):
        return Clasificacion(automatizacion=automatizacion, indicadores=indicadores)
    confirma = bool(_CONFIRMACION.search(t))
    cancela = bool(_CANCELACION.search(t))
    motivos = []
    for motivo in MOTIVOS:
        if any(re.search(p, t) for p in PATRONES[motivo]):
            motivos.append(motivo)
            continue
        if any(re.search(r"(?<!\w)" + re.escape(f) + r"(?!\w)", t)
               for f in (catalogo or {}).get(motivo, ())):
            motivos.append(motivo)
    # Tolerancia acotada a errores habituales, sin clasificar «Sí» o negaciones.
    palabras = t.split()
    if not motivos and any(get_close_matches(w, ("informacion", "consulta", "horas", "doctores", "precios"), n=1, cutoff=.82)
                            for w in palabras if len(w) >= 5):
        if any(w in palabras for w in ("quiero", "necesito", "tienen", "hay", "cuanto", "una")):
            motivos.append("CONSULTA_AMBIGUA")
    if not motivos and re.search(r"\b(?:pregunta|duda)\b", t) and not (confirma or cancela):
        motivos.append("CONSULTA_AMBIGUA")
    if (confirma or cancela) and motivos == ["CONSULTA_AMBIGUA"]:
        motivos.clear()
    return Clasificacion(tuple(motivos), confirma, cancela, automatizacion, indicadores)
