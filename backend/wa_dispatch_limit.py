"""Presupuesto de despacho compartido por emisor y pareja emisor-destinatario.

La reserva usa dos filas bloqueadas en MySQL y no mantiene una transacción
abierta durante la llamada HTTP. Si la base falla, el envío real falla cerrado.
"""

import hashlib
import math
import re
import threading
import time
from datetime import datetime

from db import CONFIG_DEFAULTS, conectar
from whatsapp_channels import CENTROS, resolver_phone_id


def _entero(valor, defecto: int, minimo: int, maximo: int) -> int:
    try:
        return min(maximo, max(minimo, int(valor)))
    except (TypeError, ValueError):
        return defecto


_config_lock = threading.Lock()
_config_cache: tuple[float, dict] = (0, {})


def _config_actual() -> dict:
    """Refresca políticas entre instancias en <=5 s; falla cerrado si cae BD."""
    global _config_cache
    with _config_lock:
        if time.monotonic() < _config_cache[0]:
            return dict(_config_cache[1])
        with conectar() as conn, conn.cursor() as cur:
            cur.execute("SELECT clave, valor FROM configuracion")
            valores = {**{clave: CONFIG_DEFAULTS[clave] for clave in _CLAVES},
                       **{fila["clave"]: fila["valor"] for fila in cur.fetchall()
                          if fila["clave"] in _CLAVES or fila["clave"].endswith((
                              "_WSP_PHONE_NUMBER", "_WSP_PHONE_NUMBER_ID",
                              "_WSP_WABA_ID", "_WSP_ACCESS_TOKEN",
                              "_WSP_RATE_LIMIT_MPS", "_WSP_RATE_LIMIT_MODE",
                              "_WSP_RATE_LIMIT_VERIFIED"))}}
        _config_cache = (time.monotonic() + 5, valores)
        return dict(valores)


_CLAVES = tuple(
    ["wa_clinicas_adicionales", "wa_dispatch_pair_seconds", "wa_dispatch_reserved_percent",
     "wa_dispatch_max_retries", "wa_dispatch_backoff_base_seconds",
     "wa_dispatch_backoff_max_seconds"] +
    [f"{centro}_WSP_{campo}" for centro in CENTROS
     for campo in ("PHONE_NUMBER", "PHONE_NUMBER_ID", "WABA_ID", "ACCESS_TOKEN", "RATE_LIMIT_MPS", "RATE_LIMIT_MODE",
                   "RATE_LIMIT_VERIFIED")]
)


def politica(phone_id: str, valores: dict | None = None) -> dict:
    """El modo coexistencia tiene techo 20/s; otros, 80/s sin upgrade probado."""
    cfg = valores if valores is not None else _config_actual()
    canal = resolver_phone_id(phone_id, cfg)
    if not canal or not canal["configured"]:
        raise ValueError("El número emisor no está configurado o ya no está disponible")
    codigo = canal["codigo"]
    modo = cfg.get(f"{codigo}_WSP_RATE_LIMIT_MODE", "estandar")
    if modo not in ("estandar", "coexistencia"):
        raise ValueError("Modo de throughput no reconocido")
    capacidad = 20 if modo == "coexistencia" else 80
    reservado = _entero(cfg.get("wa_dispatch_reserved_percent"), 25, 0, 75)
    mps_solicitados = _entero(cfg.get(f"{codigo}_WSP_RATE_LIMIT_MPS"), 10, 1, 1000)
    # El margen incluye respuestas entrantes; no asumir upgrade a 1000/s.
    mps = min(mps_solicitados, max(1, math.floor(capacidad * (100 - reservado) / 100)))
    verificado = str(cfg.get(f"{codigo}_WSP_RATE_LIMIT_VERIFIED", "false")).lower() == "true"
    if not verificado:
        mps = min(mps, 3 if modo == "coexistencia" else 10)
    return {
        "centro": codigo, "modo": modo, "mps": mps, "verificado": verificado,
        "mps_inicial": min(mps, 3 if modo == "coexistencia" else 10),
        "pair_seconds": _entero(cfg.get("wa_dispatch_pair_seconds"), 6, 1, 60),
        "max_retries": _entero(cfg.get("wa_dispatch_max_retries"), 3, 0, 6),
        "backoff_base": _entero(cfg.get("wa_dispatch_backoff_base_seconds"), 2, 1, 60),
        "backoff_max": _entero(cfg.get("wa_dispatch_backoff_max_seconds"), 60, 2, 900),
    }


def _segundos(hasta: datetime | None, ahora: datetime) -> float:
    return max(0.0, (hasta - ahora).total_seconds()) if hasta else 0.0


def huella_pareja(phone_id: str, destinatario: str) -> str:
    digitos = re.sub(r"\D", "", destinatario or "")
    if not digitos:
        raise ValueError("Falta destinatario para limitar el envío")
    # Reutiliza la huella HMAC de supresión: un SHA simple de teléfonos
    # permitiría probar por fuerza bruta el pequeño espacio de números.
    from supresion import huella_telefono
    huella_paciente = huella_telefono("+" + digitos)
    return hashlib.sha256(f"{phone_id}:{huella_paciente}".encode()).hexdigest()


def reservar(phone_id: str, destinatario: str, *, cfg: dict | None = None) -> float:
    """Devuelve 0 si se obtuvo el turno, o segundos hasta reintentar.

    Se inserta y bloquea primero la fila del emisor y luego la de la pareja,
    siempre en ese orden. Varios workers y campañas comparten el presupuesto.
    """
    if not destinatario:
        raise ValueError("Falta destinatario para limitar el envío")
    reglas = politica(phone_id, cfg)
    huella = huella_pareja(phone_id, destinatario)
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("INSERT IGNORE INTO wa_despacho_canales (phone_id) VALUES (%s)", (phone_id,))
        cur.execute("SELECT UTC_TIMESTAMP(6) AS ahora")
        ahora = cur.fetchone()["ahora"]
        cur.execute("SELECT proximo_turno, bloqueado_hasta, mps_actual FROM wa_despacho_canales"
                    " WHERE phone_id = %s FOR UPDATE", (phone_id,))
        canal = cur.fetchone()
        cur.execute("INSERT IGNORE INTO wa_despacho_pares"
                    " (phone_id, destinatario_hash, proximo_turno) VALUES (%s, %s, %s)",
                    (phone_id, huella, ahora))
        cur.execute("SELECT proximo_turno FROM wa_despacho_pares"
                    " WHERE phone_id = %s AND destinatario_hash = %s FOR UPDATE",
                    (phone_id, huella))
        pareja = cur.fetchone()
        espera = max(_segundos(canal["proximo_turno"], ahora),
                     _segundos(canal["bloqueado_hasta"], ahora),
                     _segundos(pareja["proximo_turno"], ahora))
        if espera <= 0:
            mps_actual = min(reglas["mps"], canal["mps_actual"] or reglas["mps_inicial"])
            micros = math.ceil(1_000_000 / mps_actual)
            cur.execute("UPDATE wa_despacho_canales"
                        " SET proximo_turno = DATE_ADD(%s, INTERVAL %s MICROSECOND),"
                        " mps_actual = %s, ajustado_en = COALESCE(ajustado_en, %s)"
                        " WHERE phone_id = %s", (ahora, micros, mps_actual, ahora, phone_id))
            cur.execute("UPDATE wa_despacho_pares"
                        " SET proximo_turno = DATE_ADD(%s, INTERVAL %s SECOND)"
                        " WHERE phone_id = %s AND destinatario_hash = %s",
                        (ahora, reglas["pair_seconds"], phone_id, huella))
        conn.commit()
    return espera


def bloquear(phone_id: str, codigo: int, segundos: float) -> None:
    """Enfría solo el emisor saturado; otros canales continúan."""
    espera = max(1, min(900, math.ceil(segundos)))
    contador = "errores_131057" if codigo == 131057 else "errores_130429"
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("INSERT IGNORE INTO wa_despacho_canales (phone_id) VALUES (%s)", (phone_id,))
        cur.execute(f"UPDATE wa_despacho_canales SET {contador} = {contador} + 1,"
                    " bloqueado_hasta = GREATEST(COALESCE(bloqueado_hasta, UTC_TIMESTAMP(6)),"
                    " DATE_ADD(UTC_TIMESTAMP(6), INTERVAL %s SECOND)),"
                    " mps_actual = CASE WHEN %s = 130429 THEN GREATEST(1, FLOOR(COALESCE(mps_actual, 10) / 2))"
                    " ELSE mps_actual END, aceptados_desde_ajuste = 0,"
                    " ajustado_en = UTC_TIMESTAMP(6) WHERE phone_id = %s",
                    (espera, codigo, phone_id))
        conn.commit()


def aplazar_pareja(phone_id: str, destinatario: str, segundos: float) -> None:
    huella = huella_pareja(phone_id, destinatario)
    espera = max(1, min(900, math.ceil(segundos)))
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO wa_despacho_pares"
                    " (phone_id, destinatario_hash, proximo_turno)"
                    " VALUES (%s, %s, DATE_ADD(UTC_TIMESTAMP(6), INTERVAL %s SECOND))"
                    " ON DUPLICATE KEY UPDATE proximo_turno = GREATEST(proximo_turno,"
                    " VALUES(proximo_turno))", (phone_id, huella, espera))
        conn.commit()


def registrar_aceptado(phone_id: str) -> None:
    reglas = politica(phone_id)
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("SELECT mps_actual, aceptados_desde_ajuste, ajustado_en,"
                    " UTC_TIMESTAMP(6) AS ahora FROM wa_despacho_canales"
                    " WHERE phone_id = %s FOR UPDATE", (phone_id,))
        fila = cur.fetchone()
        if fila:
            actual = min(reglas["mps"], fila["mps_actual"] or reglas["mps_inicial"])
            aceptados = fila["aceptados_desde_ajuste"] + 1
            estable = bool(fila["ajustado_en"] and
                           (fila["ahora"] - fila["ajustado_en"]).total_seconds() >= 300 and
                           aceptados >= actual * 60)
            nuevo = min(reglas["mps"], actual + (1 if reglas["modo"] == "coexistencia" else 5)) if estable else actual
            cur.execute("UPDATE wa_despacho_canales SET enviados = enviados + 1,"
                        " mps_actual = %s, aceptados_desde_ajuste = %s,"
                        " ajustado_en = CASE WHEN %s THEN %s ELSE ajustado_en END"
                        " WHERE phone_id = %s",
                        (nuevo, 0 if estable else aceptados, estable, fila["ahora"], phone_id))
        conn.commit()


def estados() -> list[dict]:
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("SELECT phone_id, bloqueado_hasta, mps_actual, errores_130429,"
                    " errores_131057, enviados, actualizado"
                    " FROM wa_despacho_canales")
        return cur.fetchall()


def limpiar_pares_vencidos() -> int:
    """Retira huellas efímeras ya inservibles sin recorrer toda la tabla."""
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM wa_despacho_pares"
                    " WHERE proximo_turno < DATE_SUB(UTC_TIMESTAMP(6), INTERVAL 1 DAY)"
                    " LIMIT 5000")
        borrados = cur.rowcount
        conn.commit()
    return borrados
