"""Señales de lenguaje ofensivo independientes de la intención del paciente.

La lista original se conserva intacta. Una coincidencia léxica no es, por sí
sola, una agresión: hay términos clínicos, citas y usos ambiguos en la fuente.
"""

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


RUTA_BLACKLIST = Path(__file__).resolve().parents[1] / "blacklist_es.txt"


def _normalizar(texto: str) -> str:
    base = unicodedata.normalize("NFKD", (texto or "").casefold())
    base = "".join(c for c in base if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", base)).strip()


# Solo estos adjetivos/epítetos españoles admiten flexión productiva conocida.
# El resto del catálogo se reconoce exactamente; nunca se agrega «s» a todos.
_ADJETIVOS = frozenset({
    "asqueroso", "baboso", "boludo", "castroso", "culiado", "estupido",
    "jodido", "maldito", "malnacido", "malparido", "manco", "payaso",
    "pelotudo", "pendejo", "puto", "ridiculo", "tarado",
})
_ON = frozenset({"cabron", "cagon", "guevon", "huevon", "mamon", "puton", "sangron"})
_INVARIABLE = {"idiota": "idiotas", "imbecil": "imbeciles"}
_IRREGULARES = {"maricon": ("maricona", "maricones", "mariconas"),
                "weon": ("weona", "weones", "weonas"),
                "culiao": ("culiaos", "culias")}
_FRASES_ANTERIORES = frozenset({
    "hijos de puta", "hijo de puta", "hija de puta", "vayanse a la mierda",
    "andate a la mierda", "son una mierda", "servicio de mierda",
    "conchetumadre", "conchesumadre",
})

# Voces presentes en la fuente que también pueden designar condiciones de
# salud, anatomía, lugares, identidad o expresiones no dirigidas a una persona.
# Se conservan como hallazgos, pero no disparan respuesta automática solas.
_AMBIGUOS = frozenset({
    "anal", "cancer", "sida", "sidoso", "pene", "tetas", "leche", "pedo",
    "puta", "puto", "putos", "putas", "mierda", "asco", "basura", "caca",
    "marica", "marico", "maricon", "maricona", "queer", "nazi", "hitler",
    "fascista", "auschwitz", "ausschwitz", "malvinas", "falkland",
    "falklands", "chile", "chilote", "paragua", "gonorrea", "pederasta",
    "pedofilo", "pedofila", "porno", "prostituta", "prostituto", "job",
    "asqueroso", "asquerosa", "coger", "chucha", "pucha", "culo", "concha",
    "pito", "polla", "garcha", "pinga", "verga", "wey", "guey", "weon",
    "weona", "huevon", "huevona", "wn", "ctm", "csm", "ql", "ass",
})
_NEUTROS = frozenset({
    "anal", "cancer", "sida", "pene", "tetas", "leche", "pedo", "queer",
    "auschwitz", "ausschwitz", "malvinas", "falkland", "falklands",
    "chile", "chilote", "paragua", "gonorrea", "pederasta", "pedofilo",
    "pedofila", "porno", "prostituta", "prostituto", "job", "coger",
})
_DIRIGIDOS_AMBIGUOS = frozenset({
    "puta", "puto", "putos", "putas", "mierda", "basura", "asco",
    "marica", "marico", "maricon", "maricona", "weon", "weona",
    "huevon", "huevona", "wn", "ctm", "csm", "ql",
})

_REFERENCIA = re.compile(
    r"\b(?:dijo|dijeron|me dijeron|escribio|escribieron|me llamo|"
    r"palabra|termino|insulto|no digas|no use|no usar|eviten|prohibido|"
    r"no toleramos|no eres(?: un[ao]?)?|no son|me siento|fui|soy|"
    r"se dice|significa)\b"
)
_DIRECCION = re.compile(
    r"\b(?:eres|son|sois|ustedes|tu|tus|su servicio|su atencion|"
    r"este centro|esta clinica|vayanse|andate|tu servicio|"
    r"sus ejecutiv[oa]s?|la ejecutiva|el ejecutivo)\b"
)
_FUERTES = frozenset({
    "idiota", "idiotas", "imbecil", "imbeciles", "estupido", "estupida",
    "estupidos", "estupidas", "tarado", "tarada", "tarados", "taradas",
    "pelotudo", "pelotuda", "pelotudos", "pelotudas", "pendejo",
    "pendeja", "pendejos", "pendejas", "culiao", "culiaos", "culiada",
    "culiadas", "conchetumadre", "conchatumadre", "hijoputa", "hijodeputa",
    "conchesumadre",
    "hijos de puta", "weones", "huevones", "cabron", "cabrona", "cabrones",
    "cabronas", "inutil", "inutiles", "hijo de puta", "hija de puta",
    "vayanse a la mierda", "andate a la mierda", "son una mierda",
    "servicio de mierda",
})


@dataclass(frozen=True)
class DeteccionOfensa:
    categoria: str = "SIN_INSULTO"
    confianza: float = 0.0
    terminos: tuple[str, ...] = ()
    accion_recomendada: str = "ninguna"


@lru_cache(maxsize=1)
def cargar_lexico() -> tuple[frozenset[str], frozenset[str]]:
    """Devuelve términos originales normalizados y variantes únicas válidas."""
    lineas = RUTA_BLACKLIST.read_text(encoding="utf-8-sig").splitlines()
    entradas = ",".join(l for l in lineas if l.strip() and not l.lstrip().startswith(("//", "#")))
    originales = frozenset(filter(None, (_normalizar(x) for x in entradas.split(","))))
    variantes = set()
    for masculino in _ADJETIVOS:
        if masculino not in originales:
            continue
        raiz = masculino[:-1]
        variantes.update(raiz + final for final in ("a", "os", "as"))
    for masculino in _ON:
        if masculino in originales:
            raiz = masculino[:-2]
            variantes.update((raiz + "ona", raiz + "ones", raiz + "onas"))
    for base, plural in _INVARIABLE.items():
        if base in originales:
            variantes.add(plural)
    for base, formas in _IRREGULARES.items():
        if base in originales:
            variantes.update(formas)
    # «inútil» era parte del detector anterior; conserva su flexión irregular.
    variantes.update(("inutil", "inutiles"))
    variantes.update(_FRASES_ANTERIORES)
    variantes.difference_update(originales)
    return originales, frozenset(variantes)


@lru_cache(maxsize=1)
def _patron_lexico() -> re.Pattern:
    originales, variantes = cargar_lexico()
    terminos = sorted(originales | variantes, key=len, reverse=True)
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(re.escape(t) for t in terminos) + r")(?![a-z0-9])")


def detectar_ofensa(texto: str, *, automatica: bool = False) -> DeteccionOfensa:
    """No modifica estado, no envía mensajes y nunca expone el texto en logs."""
    normal = _normalizar((texto or "")[:4096])
    if not normal:
        return DeteccionOfensa()
    coincidencias = tuple(_patron_lexico().finditer(normal))
    hallazgos = tuple(dict.fromkeys(m.group() for m in coincidencias))
    if not hallazgos:
        return DeteccionOfensa()
    es_referencia = all(_REFERENCIA.search(normal[max(0, m.start() - 70):m.start()])
                       for m in coincidencias)
    if automatica or es_referencia:
        return DeteccionOfensa("MENCION_DE_INSULTO", 0.85, hallazgos, "ninguna")
    dirigido = bool(_DIRECCION.search(normal))
    if all(t in _NEUTROS for t in hallazgos):
        return DeteccionOfensa("SIN_INSULTO", 0.85, hallazgos, "ninguna")
    fuerte = any(t in _FUERTES for t in hallazgos)
    no_ambiguo = any(t not in _AMBIGUOS for t in hallazgos)
    dirigido_ambiguo = any(t in _DIRIGIDOS_AMBIGUOS for t in hallazgos)
    if fuerte or (dirigido and (no_ambiguo or dirigido_ambiguo)):
        return DeteccionOfensa("INSULTO_DIRECTO", 0.9 if dirigido else 0.75,
                                hallazgos, "ofrecer_opciones")
    return DeteccionOfensa("LENGUAJE_OFENSIVO", 0.55 if no_ambiguo else 0.35,
                            hallazgos, "revision")
