"""Servicios de configuración y correo del sistema."""

import html as _html_mod
import re
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid

from db import config_all, config_get, entorno_valido, log_error, nombre_base


def leer_config(ambiente: str | None = None) -> dict:
    """Vista de configuración necesaria para las pantallas y el motor de envío."""
    cfg = config_all()
    ent = entorno_valido(ambiente)
    clave_numeros = "numeros_prueba_dev" if ent == "desarrollo" else "numeros_prueba_prod"

    def lista(valor: str) -> list:
        return [n.strip() for n in (valor or "").split(",") if n.strip()]

    try:
        intervalo = int(cfg.get("intervalo_ms") or 1000)
    except (TypeError, ValueError):
        intervalo = 1000
    try:
        badge_aprobada_min = int(cfg.get("plantillas_badge_aprobada_minutos") or 10)
    except (TypeError, ValueError):
        badge_aprobada_min = 10

    return {
        "entorno": ent,
        "base_datos": nombre_base(ent),
        "numeros_autorizados": lista(cfg.get(clave_numeros)),
        "metodo_envio": cfg.get("metodo_envio") or "simulado",
        "intervalo_ms": intervalo,
        "plantillas_badge_aprobada_minutos": badge_aprobada_min,
    }


def url_base() -> str:
    base = (config_get("url_base") or "").strip().rstrip("/")
    return base or "http://localhost:8000"


def config_correo() -> dict:
    """Parámetros SMTP desde la configuración persistida."""
    emisor = (config_get("correo_emisor") or "").strip()
    try:
        port = int((config_get("smtp_port") or "587").strip() or 587)
    except ValueError:
        port = 587
    return {
        "host": (config_get("smtp_host") or "").strip(),
        "port": port,
        "user": (config_get("smtp_user") or "").strip() or emisor,
        "destino": (config_get("correo_destino") or "").strip(),
        "pwd": (config_get("smtp_pass") or "").strip().replace(" ", ""),
        "tls": (config_get("smtp_tls", "true") or "true").lower() in ("1", "true", "yes", "si"),
        "emisor": emisor,
    }


def html_a_texto(html: str) -> str:
    """Versión texto plano de un HTML (para el multipart/alternative).
    Los filtros antispam penalizan los correos solo-HTML."""
    texto = re.sub(r"<br\s*/?>", "\n", html or "", flags=re.I)
    texto = re.sub(r"</(p|div|h\d|li|tr)\s*>", "\n\n", texto, flags=re.I)
    texto = re.sub(r"<[^>]+>", "", texto)
    texto = _html_mod.unescape(texto)
    lineas = [l.strip() for l in texto.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lineas)).strip()


def armar_mensaje(subject: str, emisor: str, destino: str, html: str) -> MIMEMultipart:
    """MIME multipart con parte de texto + parte HTML y cabeceras Date y
    Message-ID (su ausencia también suma puntos de spam)."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = emisor
    msg["To"] = destino
    msg["Date"] = formatdate(localtime=True)
    try:
        dominio = emisor.split("@")[-1] if "@" in (emisor or "") else None
        msg["Message-ID"] = make_msgid(domain=dominio)
    except Exception:
        msg["Message-ID"] = make_msgid()
    msg.attach(MIMEText(html_a_texto(html), "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    return msg


def enviar_correo(destino: str, subject: str, html: str) -> bool:
    """Entrega un correo HTML; registra el fallo sin exponer secretos."""
    c = config_correo()
    emisor = c["emisor"]
    if not emisor or not destino:
        return False
    if not c["host"] or not c["pwd"]:
        # Se conserva el modo de correo simulado usado durante el despliegue local.
        print(f"[CORREO SIMULADO] Para {destino} desde {emisor}: {subject}")
        return True
    try:
        mensaje = armar_mensaje(subject, emisor, destino, html)
        with smtplib.SMTP(c["host"], c["port"]) as servidor:
            if c["tls"]:
                servidor.starttls(context=ssl.create_default_context())
            if c["user"]:
                servidor.login(c["user"], c["pwd"])
            servidor.sendmail(emisor, destino, mensaje.as_string())
        return True
    except Exception as exc:
        log_error(f"enviar_correo a {destino}", exc)
        return False
