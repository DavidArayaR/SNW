"""Archivos locales para encabezados multimedia de plantillas de WhatsApp."""

import io
import json
import re
import shutil
import subprocess
import uuid
import warnings
from pathlib import Path

from fastapi import HTTPException
from PIL import Image


MEDIA_DIR = Path(__file__).resolve().parent.parent / "data" / "plantillas_media"
MAX_MEDIA_BYTES = 3_500_000
_ID_VALIDO = re.compile(r"[0-9a-f]{32}\Z")


def _comprobar_formato(datos: bytes) -> tuple[str, str, str]:
    if datos.startswith(b"\xff\xd8\xff"):
        return "imagen", "image/jpeg", ".jpg"
    if datos.startswith(b"\x89PNG\r\n\x1a\n"):
        return "imagen", "image/png", ".png"
    if datos.startswith((b"GIF87a", b"GIF89a")):
        return "gif", "image/gif", ".gif"
    raise HTTPException(415, detail="El encabezado debe ser JPG, PNG o GIF.")


def _ejecutable_ffmpeg() -> str | None:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError):
        return shutil.which("ffmpeg")


def guardar_media(datos: bytes, creador: str) -> dict:
    if not datos or len(datos) > MAX_MEDIA_BYTES:
        raise HTTPException(413, detail="La imagen o GIF debe pesar como máximo 3,5 MB.")
    tipo, mime_original, extension = _comprobar_formato(datos)
    # Decodificar la imagen impide almacenar un archivo truncado o un payload
    # arbitrario con una firma falsa; Meta validará además el ejemplo subido.
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(datos)) as imagen:
                imagen.verify()
    except (OSError, ValueError, Image.DecompressionBombWarning,
            Image.DecompressionBombError) as exc:
        raise HTTPException(415, detail="La imagen o GIF no es válido.") from exc

    ffmpeg = _ejecutable_ffmpeg() if tipo == "gif" else None
    if tipo == "gif" and not ffmpeg:
        raise HTTPException(503, detail="Para usar GIF instala FFmpeg o el paquete imageio-ffmpeg en el servidor.")

    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    identificador = uuid.uuid4().hex
    original = MEDIA_DIR / f"{identificador}{extension}"
    convertido = MEDIA_DIR / f"{identificador}.mp4"
    try:
        original.write_bytes(datos)
        if tipo == "gif":
            proceso = subprocess.run(
                [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(original),
                 "-vf", "fps=15,scale=trunc(iw/2)*2:trunc(ih/2)*2",
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
                 "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", str(convertido)],
                capture_output=True, timeout=90, check=False,
            )
            if proceso.returncode or not convertido.is_file():
                raise HTTPException(415, detail="No se pudo convertir el GIF a video para Meta.")
            if convertido.stat().st_size > MAX_MEDIA_BYTES:
                raise HTTPException(413, detail="El GIF convertido supera 3,5 MB. Usa uno más corto o pequeño.")
        metadata = {
            "id": identificador, "tipo": tipo, "creador": creador.lower(),
            "vista": original.name, "envio": convertido.name if tipo == "gif" else original.name,
            "mime_vista": mime_original,
            "mime_envio": "video/mp4" if tipo == "gif" else mime_original,
            "formato_meta": "VIDEO" if tipo == "gif" else "IMAGE",
        }
        (MEDIA_DIR / f"{identificador}.json").write_text(
            json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
        return metadata
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise HTTPException(503, detail="No se pudo preparar el encabezado multimedia.") from exc
    finally:
        if not (MEDIA_DIR / f"{identificador}.json").exists():
            original.unlink(missing_ok=True)
            convertido.unlink(missing_ok=True)


def obtener_media(identificador: str | None) -> dict | None:
    if not identificador:
        return None
    if not _ID_VALIDO.fullmatch(identificador):
        raise HTTPException(400, detail="Identificador de encabezado inválido.")
    try:
        metadata = json.loads((MEDIA_DIR / f"{identificador}.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(404, detail="No se encontró el encabezado multimedia.") from exc
    if metadata.get("id") != identificador:
        raise HTTPException(404, detail="No se encontró el encabezado multimedia.")
    for campo in ("vista", "envio"):
        archivo = metadata.get(campo) or ""
        if not archivo.startswith(identificador + ".") or not (MEDIA_DIR / archivo).is_file():
            raise HTTPException(404, detail="Falta el archivo del encabezado multimedia.")
    return metadata


def ruta_media(metadata: dict, vista: bool = False) -> Path:
    return MEDIA_DIR / metadata["vista" if vista else "envio"]
