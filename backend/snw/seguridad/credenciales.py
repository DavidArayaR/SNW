"""Derivación y verificación de contraseñas de usuarios de SNW."""

import hashlib
import re
import secrets
from threading import BoundedSemaphore


_PREFIJO = "scrypt-v1"
_CUPO = BoundedSemaphore(2)
# Identifica verificadores de instalaciones antiguas para revocar su acceso.
HASHES_PREDETERMINADOS = (
    "240be518fabd2724ddb6f04eeb1da5967448d7e831c08c8fa822809f74c720a9",
    "dfa7a2273567dcd1efffb9a46308e91c20fa13c44c3441bc69cd6a7869b3f7fd",
    "87274af01876341455b32d805946f272871bb42effa6604dccf28bb027afa82b",
)


def credencial_deshabilitada(guardado: str) -> bool:
    return guardado == "deshabilitada" or guardado in HASHES_PREDETERMINADOS


def _derivar(clave: str, sal: bytes) -> bytes:
    with _CUPO:
        return hashlib.scrypt(clave.encode("utf-8"), salt=sal, n=2**15, r=8, p=3,
                              maxmem=64 * 1024 * 1024, dklen=32)


def hash_clave(clave: str) -> str:
    sal = secrets.token_bytes(16)
    return f"{_PREFIJO}${sal.hex()}${_derivar(clave, sal).hex()}"


def es_hash_legacy(valor: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{64}", valor))


def verificar_clave(clave: str, guardado: str) -> bool:
    if len(clave) > 128 or credencial_deshabilitada(guardado):
        return False
    if es_hash_legacy(guardado):
        return secrets.compare_digest(hashlib.sha256(clave.encode("utf-8")).hexdigest(), guardado)
    partes = guardado.split("$")
    if len(partes) != 3 or partes[0] != _PREFIJO:
        return False
    if not re.fullmatch(r"[0-9a-f]{32}", partes[1]) or not es_hash_legacy(partes[2]):
        return False
    return secrets.compare_digest(_derivar(clave, bytes.fromhex(partes[1])), bytes.fromhex(partes[2]))
