"""
Gobernador de los límites de envío de WhatsApp / Meta.

Cubre las tres capas de límites que Meta aplica:

1. **Rate limits de la Graph API** — Meta informa el consumo de cuota en las
   cabeceras de CADA respuesta (`X-App-Usage`, `X-Business-Use-Case-Usage`, con
   `estimated_time_to_regain_access` en minutos). Se leen y, según el % de uso,
   se espera antes de la siguiente llamada.
   https://developers.facebook.com/docs/graph-api/overview/rate-limiting/

2. **Throughput de la Cloud API** — por número, por defecto 80 mensajes/segundo
   (entrantes + salientes, todos los tipos). Al superarlo Meta devuelve el
   código 130429. Se limita el ritmo de salida a `wa_throughput_mps` msg/s
   espaciando las llamadas a `/messages`.
   https://developers.facebook.com/documentation/business-messaging/whatsapp/throughput

3. **Messaging limits** — nº de usuarios ÚNICOS a los que el negocio puede
   escribir (mensajes iniciados por el negocio) en una ventana móvil de 24 h:
   250 / 1K / 10K / 100K / ilimitado. Ese control vive en `main.py` (necesita
   consultar `log_envios`); aquí solo se reconoce el error que devuelve Meta
   cuando se topa (131056 / 131049, por destinatario).
   https://developers.facebook.com/documentation/business-messaging/whatsapp/messaging-limits

Es un singleton en memoria del proceso (``gobernador``); no persiste nada.
"""

import json
import threading
import time

from db import config_get, log_error

# --- Errores que frenan TODOS los envíos (rate limit de la app / del número) ---
CODIGOS_THROTTLE_GLOBAL = {
    4, 17, 32, 613, 80007, 80008,   # rate limits de la Graph API (app / usuario / BUC)
    130429,                         # throughput de la Cloud API superado (> N msg/s)
    131048,                         # spam rate limit: el número quedó restringido
    131057,                         # número en mantenimiento / upgrade de throughput
    368,                            # bloqueo temporal por incumplir políticas
}
SUBCODIGO_THROTTLE = 2446079

# --- Errores POR DESTINATARIO: no frenan el resto del envío ni sirve reintentar -
CODIGOS_LIMITE_DESTINATARIO = {
    131056,   # (negocio, usuario) pair rate limit: demasiados mensajes a ese usuario
    131049,   # frecuencia por usuario ("healthy ecosystem engagement")
    130497,   # límite de mensajes de marketing para ese usuario
}

# Espera mínima por defecto (segundos) para ciertos códigos globales cuando Meta
# no indica un tiempo de recuperación en las cabeceras.
_ESPERA_POR_CODIGO = {
    130429: 2,      # throughput: se recupera en cuanto baja el ritmo
    131057: 60,     # upgrade de throughput: "hasta 1 minuto"
    131048: 900,    # spam: problema de calidad, cooldown largo
    368: 900,
}

# Compat.: algunas partes del código antiguo importaban este nombre.
CODIGOS_THROTTLE = CODIGOS_THROTTLE_GLOBAL

# Nunca se espera más que esto de una sola vez, pase lo que pase (15 min).
TOPE_ESPERA_S = 900.0


def _num(clave: str, defecto: float) -> float:
    try:
        return float(config_get(clave, str(defecto)) or defecto)
    except (TypeError, ValueError):
        return defecto


def _activo() -> bool:
    return (config_get("wa_rate_limit_activo", "true") or "true").strip().lower() not in (
        "false", "0", "no", "off",
    )


def reintentos_throttle() -> int:
    try:
        return max(0, int(_num("wa_rate_limit_reintentos", 3)))
    except (TypeError, ValueError):
        return 3


def _entero(v):
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def clasificar_error(status_code: int, codigo, subcodigo) -> str | None:
    """'global' -> frenar todos los envíos; 'destinatario' -> fallar solo ese
    mensaje; None -> no es un límite."""
    codigo = _entero(codigo)
    subcodigo = _entero(subcodigo)
    if codigo in CODIGOS_LIMITE_DESTINATARIO:
        return "destinatario"
    if (status_code == 429 or codigo in CODIGOS_THROTTLE_GLOBAL
            or subcodigo == SUBCODIGO_THROTTLE):
        return "global"
    return None


def es_error_throttle(status_code: int, codigo, subcodigo) -> bool:
    """True si es un límite que obliga a frenar todos los envíos."""
    return clasificar_error(status_code, codigo, subcodigo) == "global"


class _Gobernador:
    """Estado compartido de consumo de cuota y ritmo de envío. Thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.RLock()   # reentrante: estado() llama a pausa_antes_de_enviar()
        self._uso_pct = 0.0          # 0-100: porcentaje de cuota más alto reciente
        self._bloqueado_hasta = 0.0  # epoch; 0 = sin bloqueo duro
        self._ultimo_visto = 0.0     # epoch de la última cabecera leída
        self._ultimo_motivo = ""
        self._proximo_turno = 0.0    # epoch del próximo hueco de envío (throughput)

    # ------------------------------------------------------------------ escritura
    def registrar_respuesta(self, headers) -> None:
        """Lee las cabeceras de uso de una respuesta de Meta (cualquier código)."""
        try:
            pct = _max_pct(headers)
            regain = _espera_regain(headers)
        except Exception as e:  # una cabecera rara nunca debe romper un envío
            log_error("wa_rate_limit.registrar_respuesta", e)
            return
        with self._lock:
            self._ultimo_visto = time.time()
            # El valor nuevo manda si sube; si baja, se suaviza para no oscilar.
            self._uso_pct = pct if pct >= self._uso_pct else (self._uso_pct * 0.6 + pct * 0.4)
            if regain > 0:
                self._bloqueado_hasta = max(self._bloqueado_hasta, time.time() + min(regain, TOPE_ESPERA_S))
                self._ultimo_motivo = "cabecera estimated_time_to_regain_access"

    def registrar_error(self, codigo, subcodigo, headers=None, retry_after=None) -> float:
        """Meta respondió un error de throttle GLOBAL. Fija el bloqueo y devuelve
        los segundos que hay que esperar antes de reintentar."""
        segundos = _espera_regain(headers) if headers is not None else 0.0
        if retry_after:
            try:
                segundos = max(segundos, float(retry_after))
            except (TypeError, ValueError):
                pass
        segundos = max(segundos, _ESPERA_POR_CODIGO.get(_entero(codigo), 0))
        if segundos <= 0:
            segundos = _num("wa_rate_limit_espera_defecto_s", 60)
        segundos = min(segundos, TOPE_ESPERA_S)
        with self._lock:
            self._uso_pct = 100.0
            self._bloqueado_hasta = max(self._bloqueado_hasta, time.time() + segundos)
            self._ultimo_motivo = f"error de Meta (código {codigo}, subcódigo {subcodigo})"
        return segundos

    # ------------------------------------------------------------------- consulta
    def pausa_antes_de_enviar(self) -> float:
        """Segundos que conviene dormir ANTES de la próxima llamada a Meta
        (por consumo de cuota o por un bloqueo activo)."""
        with self._lock:
            ahora = time.time()
            if self._bloqueado_hasta > ahora:
                return self._bloqueado_hasta - ahora
            pct = self._uso_pct
        if not _activo():
            return 0.0
        umbral = _num("wa_rate_limit_umbral_pct", 80)
        if pct < umbral:
            return 0.0
        # De umbral% .. 100% se interpola linealmente 0 .. pausa_max.
        pausa_max = _num("wa_rate_limit_pausa_max_s", 30)
        frac = min(1.0, (pct - umbral) / max(1.0, 100.0 - umbral))
        return round(pausa_max * frac, 2)

    def reservar_turno_throughput(self) -> float:
        """Reserva el próximo hueco de envío a `/messages` y devuelve cuántos
        segundos hay que dormir hasta que llegue, para no superar
        `wa_throughput_mps` mensajes por segundo. Cada llamada reserva un hueco
        distinto, así que varios hilos enviando a la vez se turnan sin pisarse."""
        mps = _num("wa_throughput_mps", 10)
        if mps <= 0:
            return 0.0
        intervalo = 1.0 / mps
        with self._lock:
            ahora = time.time()
            turno = max(ahora, self._proximo_turno)
            self._proximo_turno = turno + intervalo
        return max(0.0, turno - ahora)

    def esperar_si_es_necesario(self) -> float:
        """Duerme lo que haga falta (en tramos de 1 s) y devuelve el total dormido."""
        total = 0.0
        while True:
            s = self.pausa_antes_de_enviar()
            if s <= 0:
                return total
            paso = min(s, 1.0)
            time.sleep(paso)
            total += paso

    def estado(self) -> dict:
        with self._lock:
            ahora = time.time()
            return {
                "activo": _activo(),
                "uso_pct": round(self._uso_pct, 1),
                "bloqueado": self._bloqueado_hasta > ahora,
                "bloqueado_segundos": max(0, round(self._bloqueado_hasta - ahora)),
                "pausa_sugerida_s": self.pausa_antes_de_enviar(),
                "throughput_mps": _num("wa_throughput_mps", 10),
                "ultimo_motivo": self._ultimo_motivo,
                "cabecera_hace_s": (round(ahora - self._ultimo_visto)
                                    if self._ultimo_visto else None),
            }


# ---------------------------------------------------------------- parseo cabeceras
def _a_dict(crudo) -> dict:
    if not crudo:
        return {}
    try:
        return json.loads(crudo)
    except (ValueError, TypeError):
        return {}


def _pcts_de(entrada: dict):
    for k in ("call_count", "total_cputime", "total_time"):
        v = entrada.get(k)
        if isinstance(v, (int, float)):
            yield float(v)


def _max_pct(headers) -> float:
    """Mayor porcentaje de uso entre X-App-Usage y X-Business-Use-Case-Usage."""
    pct = 0.0
    app = _a_dict(headers.get("x-app-usage") or headers.get("X-App-Usage"))
    for v in _pcts_de(app):
        pct = max(pct, v)
    buc = _a_dict(headers.get("x-business-use-case-usage") or headers.get("X-Business-Use-Case-Usage"))
    for entradas in buc.values():
        for e in (entradas or []):
            if isinstance(e, dict):
                for v in _pcts_de(e):
                    pct = max(pct, v)
    return min(pct, 100.0)


def _espera_regain(headers) -> float:
    """`estimated_time_to_regain_access` (viene en MINUTOS) -> segundos."""
    if headers is None:
        return 0.0
    buc = _a_dict(headers.get("x-business-use-case-usage") or headers.get("X-Business-Use-Case-Usage"))
    maximo = 0.0
    for entradas in buc.values():
        for e in (entradas or []):
            if isinstance(e, dict):
                v = e.get("estimated_time_to_regain_access") or 0
                try:
                    maximo = max(maximo, float(v) * 60.0)
                except (TypeError, ValueError):
                    pass
    return maximo


gobernador = _Gobernador()
