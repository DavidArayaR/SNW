"""Identificadores del módulo de consulta directa a Reservo."""

RESERVO_CLINICAS = {
    "SMPT": "Salud Mental Para Todos",
    "MS": "Centro Mentalidad Saludable",
    # Mantener el código independiente. No se interpreta como VS.
    "MVYS": "Más Vida y Salud",
}


def clave_token(codigo: str) -> str:
    return f"{codigo}_RESERVO_API_TOKEN"


def clave_tratamiento(codigo: str) -> str:
    return f"{codigo}_RESERVO_TRATAMIENTO_UUID"


def clave_estado_atendido(codigo: str) -> str:
    return f"{codigo}_RESERVO_ATENDIDO_STATE_CODE"


def clave_estado_verificado(codigo: str) -> str:
    return f"{codigo}_RESERVO_ATENDIDO_VERIFICADO"


CLAVES_TOKEN = {clave_token(codigo) for codigo in RESERVO_CLINICAS}
