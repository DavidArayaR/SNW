"""Lectura temporal de credenciales antiguas para migrarlas a `configuracion` en claro.

No cifra valores nuevos ni crea archivos de clave.
"""

import base64
import ctypes
import sys
from ctypes import wintypes
from pathlib import Path


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _leer_dpapi(valor: str) -> str:
    if sys.platform != "win32":
        raise OSError("DPAPI solo se puede migrar desde la cuenta original de Windows.")
    data = base64.b64decode(valor[len("dpapi:"):], validate=True)
    entrada = ctypes.create_string_buffer(data)
    blob_entrada = _Blob(len(data), ctypes.cast(entrada, ctypes.POINTER(ctypes.c_ubyte)))
    blob_salida = _Blob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    funcion = crypt32.CryptUnprotectData
    funcion.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p,
                        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    funcion.restype = wintypes.BOOL
    if not funcion(ctypes.byref(blob_entrada), None, None, None, None, 0, ctypes.byref(blob_salida)):
        raise OSError("La cuenta de Windows no puede descifrar esta credencial antigua.")
    try:
        return ctypes.string_at(blob_salida.pbData, blob_salida.cbData).decode("utf-8")
    finally:
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        kernel32.LocalFree(ctypes.cast(blob_salida.pbData, ctypes.c_void_p))


def leer_credencial_antigua(valor: str) -> str:
    if valor.startswith("dpapi:"):
        return _leer_dpapi(valor)
    if valor.startswith("fernet-v1:"):
        from cryptography.fernet import Fernet

        ruta = Path("/var/lib/snw/config.key")
        if not ruta.is_file() or ruta.is_symlink():
            raise OSError("Falta la clave local antigua para migrar esta credencial.")
        return Fernet(ruta.read_bytes().strip()).decrypt(valor[len("fernet-v1:"):].encode("ascii")).decode("utf-8")
    raise ValueError("Formato antiguo desconocido.")
