"""Distribución del cupo gratuito mensual de mensajes de servicio de Meta."""

from collections import defaultdict
import datetime
import re


INICIO_COBRO_SERVICIO = datetime.date(2026, 10, 1)
CUPO_GRATUITO_SERVICIO = 1000
MAX_PERIODOS_FILTRO = 120


def validar_periodos(raw: str, granularidad: str) -> set[str]:
    """Valida la selección múltiple del calendario sin aceptar SQL arbitrario."""
    valores = raw.split(",")
    if not 1 <= len(valores) <= MAX_PERIODOS_FILTRO:
        raise ValueError("Selecciona entre 1 y 120 períodos")
    formato = {"dia": r"\d{4}-\d{2}-\d{2}",
               "mes": r"\d{4}-\d{2}",
               "anio": r"\d{4}"}[granularidad]
    for valor in valores:
        if not re.fullmatch(formato, valor):
            raise ValueError("Período con formato inválido")
        try:
            if granularidad == "dia":
                datetime.date.fromisoformat(valor)
            elif granularidad == "mes":
                datetime.date.fromisoformat(valor + "-01")
            elif int(valor) < 1:
                raise ValueError("Año inválido")
        except ValueError as exc:
            raise ValueError("Período inexistente") from exc
    return set(valores)


def clasificar_entregas_servicio(filas):
    """Devuelve (fila, gratis) en orden de entrega, por número emisor y mes.

    Si solo se conoce un número empresarial, los registros antiguos sin ID
    consumen su mismo cupo. Con varios emisores conocidos no se inventa a cuál
    pertenecían: comparten un grupo desconocido para mantener la estimación.
    """
    emisores = {f["wa_phone_id"] for f in filas if f.get("wa_phone_id")}
    emisor_antiguo = next(iter(emisores)) if len(emisores) == 1 else "__desconocido__"
    usados = defaultdict(int)
    for fila in sorted(filas, key=lambda f: (f["fecha_entrega"], f["id"])):
        fecha = fila["fecha_entrega"]
        if fecha.date() < INICIO_COBRO_SERVICIO:
            yield fila, True
            continue
        grupo = ((fila.get("wa_phone_id") or emisor_antiguo), fecha.strftime("%Y-%m"))
        usados[grupo] += 1
        yield fila, usados[grupo] <= CUPO_GRATUITO_SERVICIO
