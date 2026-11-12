"""Comprueba con Meta que el token de la clínica puede usar su WABA y número."""

import hashlib
import re
import threading
import time

import httpx


_cache: dict[tuple[str, str, str, str, bool], tuple[float, bool, str]] = {}
_lock = threading.Lock()
_TTL_SEGUNDOS = 60


def verificar_canal_meta(canal: dict, config: dict, *, mostrar_numeros: bool = False) -> tuple[bool, str]:
    """Falla cerrado. El detalle de números solo se solicita en la vista de desarrollador."""
    phone_id = str(canal.get("phoneNumberId") or "").strip()
    waba_id = str(canal.get("wabaId") or "").strip()
    numero = re.sub(r"\D", "", str(canal.get("phoneNumber") or ""))
    codigo = str(canal.get("codigo") or "").strip()
    token = str(config.get(f"{codigo}_WSP_ACCESS_TOKEN") or "").strip()
    version = str(config.get("wa_graph_version") or "v26.0").strip()
    if not (phone_id and waba_id and numero and token):
        return False, "Falta número emisor, Phone Number ID, WABA ID o access token de la clínica."
    if not re.fullmatch(r"v\d+\.\d+", version):
        return False, "La versión de Graph API configurada no es válida."
    clave = (hashlib.sha256(token.encode()).hexdigest(), waba_id, phone_id, numero,
             mostrar_numeros)
    with _lock:
        anterior = _cache.get(clave)
        if anterior and anterior[0] > time.monotonic():
            return anterior[1], anterior[2]

    def guardar(ok: bool, mensaje: str) -> tuple[bool, str]:
        with _lock:
            _cache[clave] = (time.monotonic() + (_TTL_SEGUNDOS if ok else 15), ok, mensaje)
        return ok, mensaje

    url = f"https://graph.facebook.com/{version}/{waba_id}/phone_numbers"
    cursor = None
    try:
        with httpx.Client(timeout=10) as cliente:
            for _ in range(20):
                parametros = {"fields": "id,display_phone_number", "limit": 100}
                if cursor:
                    parametros["after"] = cursor
                respuesta = cliente.get(url, params=parametros,
                                        headers={"Authorization": f"Bearer {token}"})
                if respuesta.status_code != 200:
                    return guardar(False, "Meta no autorizó consultar los números de este WABA con el access token de la clínica.")
                datos = respuesta.json()
                for item in datos.get("data") or []:
                    if str(item.get("id") or "") != phone_id:
                        continue
                    numero_meta = re.sub(r"\D", "", str(item.get("display_phone_number") or ""))
                    if numero_meta != numero:
                        if not numero_meta:
                            return guardar(False, "Meta no informó el número visible para este Phone Number ID. Verifícalo en Meta antes de enviar.")
                        if mostrar_numeros:
                            return guardar(
                                False,
                                "El Phone Number ID pertenece al WABA, pero los números no "
                                f"coinciden. Meta: +{numero_meta}; "
                                f"SNW: +{numero}. Corrige «Número visible» en "
                                "Configuración → Clínicas para que sea exactamente el de Meta.",
                            )
                        sufijo_meta = numero_meta[-4:] if numero_meta else "no disponible"
                        sufijo_local = numero[-4:]
                        return guardar(
                            False,
                            "El Phone Number ID sí pertenece al WABA, pero Meta lo asocia al "
                            f"número terminado en {sufijo_meta} y en SNW figura el terminado "
                            f"en {sufijo_local}. Aunque coincidan los últimos dígitos, "
                            "el número completo es distinto. Un desarrollador puede ver "
                            "ambos números en Configuración → Clínicas → Verificar en Meta.",
                        )
                    return guardar(True, "Número y WABA verificados en Meta.")
                cursor = ((datos.get("paging") or {}).get("cursors") or {}).get("after")
                if not cursor or not (datos.get("paging") or {}).get("next"):
                    break
    except (httpx.HTTPError, ValueError, TypeError):
        return guardar(False, "No se pudo verificar el canal en Meta. Revisa la conexión y el acceso del token de la clínica.")
    return guardar(False, "El Phone Number ID no pertenece al WABA ID configurado para esta clínica.")
