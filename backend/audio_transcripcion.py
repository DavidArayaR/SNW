"""Transcripción manual de audios autorizados; archivos solo en memoria."""
import hashlib
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException

from db import conectar
from snw.mensajeria.groq_service import leer_clave, consumir_cuota
from whatsapp_channels import credenciales_centro
from whatsapp_service import graph_url

MAX_AUDIO = 25 * 1024 * 1024
FORMATOS = {"audio/ogg": "ogg", "audio/opus": "ogg", "audio/mpeg": "mp3",
            "audio/mp4": "m4a",
            "audio/wav": "wav", "audio/x-wav": "wav", "audio/webm": "webm"}


def descargar_audio(fila, cliente):
    token = credenciales_centro(fila["wa_phone_id"])["token"]
    if not token:
        raise HTTPException(409, detail="Configura el token de WhatsApp de esta clínica.")
    media = str(fila.get("audio_media_id") or "")
    if not media.isdigit():
        raise HTTPException(410, detail="Este audio no tiene una referencia disponible. Pide que lo reenvíen.")
    headers = {"Authorization": f"Bearer {token}"}
    respuesta = cliente.get(f"{graph_url()}/{media}", headers=headers)
    if respuesta.status_code != 200:
        raise HTTPException(410, detail="Meta ya no permite descargar este audio. Pide que lo reenvíen.")
    datos = respuesta.json()
    url = str(datos.get("url") or "")
    partes = urlsplit(url)
    host = partes.hostname or ""
    if (partes.scheme != "https" or partes.username or partes.password
            or partes.port not in (None, 443)
            or not (host == "lookaside.fbsbx.com" or host.endswith(".fbcdn.net"))):
        raise HTTPException(502, detail="Meta devolvió una dirección de audio no válida.")
    mime = str(datos.get("mime_type") or "").split(";")[0].lower()
    if mime not in FORMATOS:
        raise HTTPException(422, detail="El formato de este audio no es compatible.")
    if int(datos.get("file_size") or 0) > MAX_AUDIO:
        raise HTTPException(413, detail="El audio supera el máximo de 25 MB.")
    contenido = bytearray()
    with cliente.stream("GET", url, headers=headers) as descarga:
        if descarga.status_code != 200:
            raise HTTPException(410, detail="No se pudo descargar el audio. Pide que lo reenvíen.")
        for parte in descarga.iter_bytes():
            contenido.extend(parte)
            if len(contenido) > MAX_AUDIO:
                raise HTTPException(413, detail="El audio supera el máximo de 25 MB.")
    if not contenido:
        raise HTTPException(422, detail="El audio está vacío.")
    return bytes(contenido), mime, FORMATOS[mime]


def transcribir(mensaje_id: str, usuario: str) -> dict:
    """El controlador debe comprobar acceso a la conversación antes de llamar."""
    llave = "audio:" + hashlib.sha256(mensaje_id.encode()).hexdigest()[:50]
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("SELECT GET_LOCK(%s, 0) AS adquirido", (llave,))
        if not (cur.fetchone() or {}).get("adquirido"):
            raise HTTPException(409, detail="Este audio ya se está transcribiendo. Actualiza en unos segundos.")
        try:
            cur.execute("SELECT * FROM whatsapp_conversaciones_mensajes WHERE mensaje_id=%s AND tipo='audio'",
                        (mensaje_id,))
            fila = cur.fetchone()
            conn.commit()  # No mantener una instantánea antigua durante las llamadas externas.
            if not fila:
                raise HTTPException(404, detail="Audio no encontrado.")
            if fila.get("audio_transcripcion"):
                return {"texto": fila["audio_transcripcion"]}
            clave = leer_clave()
            try:
                consumir_cuota(usuario)
            except HTTPException as exc:
                if exc.status_code == 429:
                    raise HTTPException(429, detail="Llegaste al límite de solicitudes de IA de esta hora.") from None
                raise
            with httpx.Client(timeout=60, follow_redirects=False) as cliente:
                audio, mime, extension = descargar_audio(fila, cliente)
                respuesta = cliente.post("https://api.groq.com/openai/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {clave}"},
                    data={"model": "whisper-large-v3-turbo", "response_format": "json"},
                    files={"file": (f"audio.{extension}", audio, mime)})
            if respuesta.status_code == 429:
                raise HTTPException(429, detail="Groq alcanzó su límite. Inténtalo más tarde.")
            if respuesta.status_code != 200:
                raise HTTPException(502, detail="Groq no pudo transcribir este audio. Inténtalo más tarde.")
            texto = respuesta.json().get("text")
            if not isinstance(texto, str) or not texto.strip() or len(texto) > 16000:
                raise HTTPException(422, detail="No se pudo reconocer texto en este audio.")
            cur.execute("UPDATE whatsapp_conversaciones_mensajes SET audio_transcripcion=%s"
                        " WHERE mensaje_id=%s AND telefono_paciente=%s AND audio_media_id=%s",
                        (texto.strip(), mensaje_id, fila["telefono_paciente"], fila["audio_media_id"]))
            if not cur.rowcount:
                raise HTTPException(404, detail="El audio ya no está disponible.")
            conn.commit()
            return {"texto": texto.strip()}
        except (httpx.HTTPError, ValueError, TypeError):
            # Nunca registrar URL firmada, credenciales, audio ni texto del paciente.
            raise HTTPException(502, detail="No se pudo completar la transcripción. Inténtalo más tarde.") from None
        finally:
            cur.execute("SELECT RELEASE_LOCK(%s)", (llave,))
