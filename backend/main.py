import asyncio
import csv as _csv
import hashlib
import math
import html as _html
import io as _io
import json
import random
import re
import secrets
import threading
import time
import unicodedata
import urllib.parse
import uuid
from pathlib import Path

import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from db import (
    conectar, entorno_valido, log_error, nombre_base, columnas_tabla, columna_existe,
    tabla_pacientes, asegurar_tabla_config, config_all, config_get, config_set,
    CONFIG_DEFAULTS,
    ROLES_USUARIO, PERMISOS_VALIDOS, PERMISOS_BASICOS, MAX_DESARROLLADORES,
    usuarios_listar, usuario_buscar, usuario_crear, usuario_actualizar, usuario_borrar,
    usuario_cambiar_clave, contar_desarrolladores,
    usuario_por_recuperacion, usuario_set_correo_recuperacion, correo_en_uso,
    reset_crear, reset_estado, reset_consumir,
    invite_crear, invite_estado, invite_consumir,
    auditoria_registrar, auditoria_listar,
)
from motor_envio import obtener_canal
import whatsapp_service
from wa_rate_limit import gobernador as wa_gobernador
from whatsapp_service import WhatsAppService, es_mensaje_interes
from whatsapp_webhook import router as whatsapp_router
from config_service import config_correo as _config_correo, enviar_correo as _enviar_correo, leer_config, url_base
from schemas import (
    ActivarCuentaIn, ClavePropiaIn, ConfigIn, ConfigTodoIn,
    CorreoRecuperacionIn, EnvioIn, InvitarIn, LoginIn, OlvideIn,
    PlantillaIn, PruebaWAIn, ResetIn, UsuarioUpdIn,
)
from telefono import normalizar_telefono
from routes import auth, configuracion, estadisticas as rutas_estadisticas
from routes import notificaciones, pacientes, plantillas, usuarios

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_FILE = BASE_DIR / "data" / "plantillas.json"
FRONTEND_DIR = BASE_DIR / "frontend"


app = FastAPI(title="SNW - API de Notificaciones WhatsApp")
app.include_router(whatsapp_router)


# Crea/siembra la tabla `configuracion` al arrancar
asegurar_tabla_config()


@app.middleware("http")
async def sin_cache(request, call_next):
    try:
        respuesta = await call_next(request)
    except Exception as e:
        # Cualquier error no controlado de una ruta queda en consola.
        log_error(f"{request.method} {request.url.path}", e)
        raise
    respuesta.headers["Cache-Control"] = "no-store"
    return respuesta


MAX_TEXTO_PLANTILLA = 1024  # máximo de caracteres del cuerpo del mensaje (límite de Meta)
CATEGORIAS_TEMPLATE = ("UTILITY", "MARKETING", "AUTHENTICATION")  # categorías válidas de Meta
# Estados de plantilla en los que SÍ se puede editar/guardar/eliminar:
# aprobada, o rechazada (para poder corregirla y volver a mandarla a revisión,
# o borrarla). Mientras esté realmente pendiente de revisión (recién creada,
# sin categoría todavía enviada, o en estado PENDING) queda de solo lectura,
# para no tocar algo que Meta está evaluando en ese momento. Solo APPROVED se
# puede usar para enviar mensajes (ver POST /api/notificaciones/enviar).
ESTADOS_TEMPLATE_EDITABLES = ("APPROVED", "REJECTED")


def _validar_categoria_template(valor: str | None) -> str:
    """La categoría del template es obligatoria para guardar una plantilla: sin
    ella no se puede registrar en Meta. Devuelve la categoría en mayúsculas."""
    cat = (valor or "").strip().upper()
    if cat not in CATEGORIAS_TEMPLATE:
        raise HTTPException(
            400,
            detail="Selecciona la categoría del template (Utility, Marketing o "
                   "Authentication) para poder guardar la plantilla.",
        )
    return cat

# Mensaje de call center: se manda automáticamente 1s después de detectar
# interés (ver CALL_CENTER_AUTO_SEGUNDOS más abajo). No es editable: es un
# texto fijo con un botón al call center. El número al que lleva el botón se
# pide en cada respuesta a `call_center_url` (con respaldo manual en
# `call_center_numeros`), ambas configurables en Configuración.
CALL_CENTER_CLAVE = "call_center"
CALL_CENTER_TEXTO_DEFAULT = (
    "¡Hola {nombre}! Gracias por tu interés. Nuestro equipo de atención te "
    "puede ayudar directamente por WhatsApp."
)
CALL_CENTER_PLANTILLA_FIJA = {
    "clave": CALL_CENTER_CLAVE,
    "texto": CALL_CENTER_TEXTO_DEFAULT,
    "cc_boton": True,
    "cc_boton_texto": "Ir al call center",
}


SESIONES_FILE = BASE_DIR / "data" / "sesiones.json"

# --- Roles y permisos --------------------------------------------------------
# Hay tres roles. `administrador` y `desarrollador` tienen TODOS los permisos de
# forma implícita; la lista `permisos` solo se consulta para el rol `usuario`.
#   - usuario:       solo ve/hace lo que tenga en `permisos`.
#   - administrador: acceso total; puede editar los permisos de las cuentas de
#                    rol `usuario` (nunca las de otro admin/dev ni las propias).
#   - desarrollador: acceso total; puede editar rol y permisos de cualquier
#                    cuenta excepto la suya. Máximo MAX_DESARROLLADORES cuentas.
# Las cuentas viven en la tabla `usuarios` (ver db.py).
ROLES = ROLES_USUARIO
ROLES_PRIVILEGIADOS = {"administrador", "desarrollador"}

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")


def validar_clave_segura(clave: str) -> str | None:
    """Devuelve un mensaje de error si la contraseña no es segura, o None si lo es."""
    if len(clave) < 8:
        return "La contraseña debe tener al menos 8 caracteres."
    if len(clave) > 128:
        return "La contraseña no puede superar los 128 caracteres."
    if not re.search(r"[a-z]", clave):
        return "La contraseña debe incluir al menos una letra minúscula."
    if not re.search(r"[A-Z]", clave):
        return "La contraseña debe incluir al menos una letra mayúscula."
    if not re.search(r"\d", clave):
        return "La contraseña debe incluir al menos un número."
    return None


def _cargar_sesiones() -> dict[str, dict]:
    try:
        datos = json.loads(SESIONES_FILE.read_text(encoding="utf-8"))
        return datos if isinstance(datos, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        log_error(f"_cargar_sesiones: {SESIONES_FILE.name} ilegible", e)
        return {}


def guardar_sesiones() -> None:
    SESIONES_FILE.write_text(
        json.dumps(SESIONES, ensure_ascii=False, indent=2), encoding="utf-8"
    )


SESIONES: dict[str, dict] = _cargar_sesiones()


def permisos_efectivos(usuario: dict) -> list[str]:
    """Permisos reales de una cuenta: todos si el rol es privilegiado."""
    if usuario.get("rol") in ROLES_PRIVILEGIADOS:
        return list(PERMISOS_VALIDOS)
    return [p for p in (usuario.get("permisos") or []) if p in PERMISOS_VALIDOS]


def _sesion_expira_segundos() -> float:
    try:
        horas = float(config_get("sesion_expira_horas", "5") or 0)
    except (TypeError, ValueError):
        horas = 5.0
    return max(0.0, horas) * 3600.0


def _purgar_sesiones_expiradas() -> None:
    """Al arrancar, descarta sesiones que ya llevan más de sesion_expira_horas
    sin actividad (p. ej. quedaron abiertas de una corrida anterior)."""
    expira_seg = _sesion_expira_segundos()
    if not expira_seg:
        return
    ahora = time.time()
    quitadas = False
    for tk, s in list(SESIONES.items()):
        ultima = s.get("actividad") or s.get("creada") or ahora
        if ahora - ultima > expira_seg:
            SESIONES.pop(tk, None)
            quitadas = True
    if quitadas:
        guardar_sesiones()


_purgar_sesiones_expiradas()


def sesion_actual(request: Request) -> dict:
    authz = request.headers.get("Authorization", "")
    token = authz[7:] if authz.startswith("Bearer ") else ""
    sesion = SESIONES.get(token)
    if not sesion:
        raise HTTPException(401, detail="Sesión no válida. Inicia sesión nuevamente.")

    # Expira sola tras N horas SIN actividad (0 = no expira). Cualquier
    # petición autenticada cuenta como actividad y renueva el plazo; las
    # sesiones cargadas de antes de esta funcionalidad (sin "actividad") no
    # se cierran de golpe: el plazo arranca a contar recién ahora.
    ahora = time.time()
    expira_seg = _sesion_expira_segundos()
    ultima_actividad = sesion.get("actividad") or sesion.get("creada") or ahora
    if expira_seg and (ahora - ultima_actividad) > expira_seg:
        SESIONES.pop(token, None)
        guardar_sesiones()
        raise HTTPException(401, detail="Tu sesión expiró por inactividad. Inicia sesión nuevamente.")
    sesion["actividad"] = ahora
    # El respaldo en disco se actualiza como mucho una vez por minuto por
    # sesión: así un reinicio del servidor no da por inactiva una sesión que
    # en realidad se estaba usando, sin escribir el archivo en cada petición.
    if ahora - (sesion.get("actividad_guardada") or 0) > 60:
        sesion["actividad_guardada"] = ahora
        guardar_sesiones()

    # El archivo de usuarios es la fuente de verdad: al validar cada petición se
    # refrescan rol y permisos, de modo que un cambio del administrador surte
    # efecto de inmediato y una cuenta eliminada queda sin sesión.
    correo = str(sesion.get("usuario", "")).strip().lower()
    if correo:
        u = usuario_buscar(correo)
        if u is None:
            SESIONES.pop(token, None)
            guardar_sesiones()
            raise HTTPException(401, detail="Tu cuenta ya no está disponible. Inicia sesión de nuevo.")
        sesion["rol"] = u.get("rol", "usuario")
        sesion["permisos"] = permisos_efectivos(u)
        sesion["correo_recuperacion"] = u.get("correo_recuperacion", "")
    else:
        sesion.setdefault("permisos", list(PERMISOS_VALIDOS)
                          if sesion.get("rol") in ROLES_PRIVILEGIADOS else [])
    return sesion


def tiene_permiso(sesion: dict, permiso: str) -> bool:
    if sesion.get("rol") in ROLES_PRIVILEGIADOS:
        return True
    return permiso in (sesion.get("permisos") or [])


def exigir(*permisos: str):
    """Dependencia FastAPI: exige al menos uno de los permisos indicados."""
    def dep(sesion: dict = Depends(sesion_actual)) -> dict:
        if not any(tiene_permiso(sesion, p) for p in permisos):
            raise HTTPException(403, detail="No tienes permiso para acceder a esta sección.")
        return sesion
    return dep


def solo_admin(sesion: dict = Depends(sesion_actual)) -> dict:
    """Zona privilegiada: administrador o desarrollador."""
    if sesion.get("rol") not in ROLES_PRIVILEGIADOS:
        raise HTTPException(403, detail="Esta sección requiere rol administrador.")
    return sesion


def solo_dev(sesion: dict = Depends(sesion_actual)) -> dict:
    """Zona exclusiva del rol desarrollador (p. ej. Configuración)."""
    if sesion.get("rol") != "desarrollador":
        raise HTTPException(403, detail="Esta sección es exclusiva del rol desarrollador.")
    return sesion


def login(body: LoginIn):
    clave_hash = hashlib.sha256(body.clave.encode("utf-8")).hexdigest()
    usuario = usuario_buscar(body.usuario)
    if usuario is None or usuario.get("clave_hash") != clave_hash:
        raise HTTPException(401, detail="Usuario o contraseña incorrectos")
    if not usuario.get("activo", True):
        raise HTTPException(403, detail="Tu cuenta está desactivada. Contacta a un administrador.")

    token = uuid.uuid4().hex
    ahora = time.time()
    SESIONES[token] = {
        "usuario": str(usuario.get("usuario", "")).strip().lower(),
        "rol": usuario.get("rol", "usuario"),
        "nombre": usuario.get("nombre", body.usuario),
        "permisos": permisos_efectivos(usuario),
        "creada": ahora,
        "actividad": ahora,
        "actividad_guardada": ahora,
    }
    guardar_sesiones()
    s = SESIONES[token]
    return {"token": token, "rol": s["rol"], "nombre": s["nombre"], "permisos": s["permisos"]}


def auth_me(sesion: dict = Depends(sesion_actual)):
    """Rol y permisos vigentes de la sesión actual (para refrescar el cliente)."""
    return {
        "usuario": sesion.get("usuario"),
        "nombre": sesion.get("nombre"),
        "rol": sesion.get("rol"),
        "permisos": sesion.get("permisos") or [],
        "correo_recuperacion": sesion.get("correo_recuperacion", ""),
    }


def cambiar_clave_propia(body: ClavePropiaIn, sesion: dict = Depends(sesion_actual)):
    """Cualquier cuenta puede cambiar su propia contraseña indicando la actual.
    Si la cuenta tiene un correo de recuperación guardado, se le avisa ahí."""
    correo = str(sesion.get("usuario", "")).strip().lower()
    u = usuario_buscar(correo)
    if u is None:
        raise HTTPException(401, detail="Tu cuenta ya no está disponible. Inicia sesión de nuevo.")
    actual_hash = hashlib.sha256(body.clave_actual.encode("utf-8")).hexdigest()
    if u.get("clave_hash") != actual_hash:
        raise HTTPException(403, detail="La contraseña actual no es correcta.")
    err = validar_clave_segura(body.clave_nueva)
    if err:
        raise HTTPException(422, detail=err)
    usuario_cambiar_clave(correo, hashlib.sha256(body.clave_nueva.encode("utf-8")).hexdigest())
    destino = (u.get("correo_recuperacion") or "").strip()
    if destino:
        _enviar_correo(
            destino, "[SNW] Tu contraseña cambió",
            _html_correo_clave_cambiada(u.get("nombre", "") or correo),
        )
    return {"ok": True}


def cambiar_correo_recuperacion(body: CorreoRecuperacionIn, sesion: dict = Depends(sesion_actual)):
    """La cuenta define a qué correo llegará el enlace de «Olvidé mi contraseña».
    Para las cuentas cuyo usuario ya es un correo suele ser el mismo; admin/dev
    (que entran con un nombre corto) lo necesitan para poder recuperar el acceso."""
    yo = str(sesion.get("usuario", "")).strip().lower()
    correo_rec = (body.correo or "").strip().lower()
    if not _EMAIL_RE.match(correo_rec):
        raise HTTPException(422, detail="Escribe un correo electrónico válido.")
    if correo_en_uso(correo_rec, excluir_usuario=yo):
        raise HTTPException(409, detail="Ese correo ya está en uso en otra cuenta.")
    usuario_set_correo_recuperacion(yo, correo_rec)
    return {"ok": True, "correo_recuperacion": correo_rec}


def _html_correo_clave_cambiada(nombre: str) -> str:
    n = _html.escape(nombre or "")
    return f"""
    <html><body style="font-family: Arial, sans-serif; color: #24303c;">
      <h2>Tu contraseña cambió</h2>
      <p>Hola {n}, te avisamos que la contraseña de tu cuenta de SNW se acaba de cambiar
      desde «Mi cuenta».</p>
      <p style="font-size:13px; color:#66757f;">Si fuiste tú, no necesitas hacer nada. Si no
      reconoces este cambio, contacta a un administrador de inmediato.</p>
    </body></html>
    """


def _html_correo_invitacion(enlace: str) -> str:
    return f"""
    <html><body style="font-family: Arial, sans-serif; color: #24303c;">
      <h2>Te invitaron a SNW</h2>
      <p>Un administrador te invitó a crear una cuenta en el Sistema de Notificaciones WhatsApp.</p>
      <p style="margin:24px 0;">
        <a href="{enlace}" style="display:inline-block; background:#128c7e; color:#fff; padding:12px 22px; border-radius:8px; text-decoration:none; font-weight:bold;">Crear mi cuenta</a>
      </p>
      <p style="font-size:13px; color:#66757f;">El enlace dura <strong>48 horas</strong>. Si no esperabas esta invitación, ignora este correo.</p>
      <p style="font-size:12px; color:#66757f;">Si el botón no funciona, copia y pega este enlace:<br>{enlace}</p>
    </body></html>
    """


def _html_correo_reset(nombre: str, enlace: str) -> str:
    n = _html.escape(nombre or "")
    return f"""
    <html><body style="font-family: Arial, sans-serif; color: #24303c;">
      <h2>Restablecer tu contraseña</h2>
      <p>Hola {n}, recibimos una solicitud para restablecer la contraseña de tu cuenta de SNW.</p>
      <p style="margin:24px 0;">
        <a href="{enlace}" style="display:inline-block; background:#128c7e; color:#fff; padding:12px 22px; border-radius:8px; text-decoration:none; font-weight:bold;">Crear una contraseña nueva</a>
      </p>
      <p style="font-size:13px; color:#66757f;">El enlace dura <strong>2 horas</strong>. Si no fuiste tú, ignora este correo: tu contraseña no cambia.</p>
      <p style="font-size:12px; color:#66757f;">Si el botón no funciona, copia y pega este enlace:<br>{enlace}</p>
    </body></html>
    """


def olvide_clave(body: OlvideIn):
    """Pide un enlace de restablecimiento. Responde siempre igual (no revela si
    la cuenta existe). El enlace llega al correo de recuperación de la cuenta."""
    correo = (body.correo or "").strip().lower()
    if _EMAIL_RE.match(correo):
        cuenta = usuario_por_recuperacion(correo)
        if cuenta:
            destino = cuenta.get("correo_recuperacion") or ""
            if not destino and _EMAIL_RE.match(str(cuenta.get("usuario", ""))):
                destino = cuenta["usuario"]
            if _EMAIL_RE.match(destino):
                token = secrets.token_hex(32)
                reset_crear(token, cuenta["usuario"], horas=2)
                enlace = f"{url_base()}/reset.html?token={token}"
                _enviar_correo(destino, "[SNW] Restablecer tu contraseña",
                               _html_correo_reset(cuenta.get("nombre") or cuenta["usuario"], enlace))
    return {"ok": True}


def reset_verificar(token: str):
    est = reset_estado(token)
    if est.get("estado") == "ok":
        return {"ok": True}
    detalle = {
        "usado": "Este enlace ya se usó. Solicita uno nuevo desde «Olvidé mi contraseña».",
        "expirado": "El enlace expiró (dura 2 horas). Solicita uno nuevo.",
    }.get(est.get("estado"), "El enlace no es válido.")
    raise HTTPException(400, detail=detalle)


def reset_aplicar(body: ResetIn):
    est = reset_estado(body.token)
    if est.get("estado") != "ok":
        raise HTTPException(400, detail="El enlace no es válido o expiró. Solicita uno nuevo.")
    err = validar_clave_segura(body.clave_nueva)
    if err:
        raise HTTPException(422, detail=err)
    correo = str(est["usuario"]).strip().lower()
    usuario_cambiar_clave(correo, hashlib.sha256(body.clave_nueva.encode("utf-8")).hexdigest())
    reset_consumir(body.token)
    for tk, s in list(SESIONES.items()):
        if str(s.get("usuario", "")).strip().lower() == correo:
            SESIONES.pop(tk, None)
    guardar_sesiones()
    return {"ok": True}


def logout(request: Request):
    authz = request.headers.get("Authorization", "")
    token = authz[7:] if authz.startswith("Bearer ") else ""
    SESIONES.pop(token, None)
    guardar_sesiones()
    return {"ok": True}


def invitar_usuario(body: InvitarIn, sesion: dict = Depends(solo_admin)):
    """Un admin/dev invita a crear una cuenta: se le manda un correo con un
    enlace (48 h) para que la persona elija su propia contraseña. La cuenta
    nace con rol `usuario` y los permisos básicos recién al activarse."""
    correo = (body.correo or "").strip().lower()
    if not _EMAIL_RE.match(correo):
        raise HTTPException(422, detail="Escribe un correo electrónico válido.")
    if usuario_buscar(correo) is not None:
        raise HTTPException(409, detail="Ya existe una cuenta con ese correo.")
    token = secrets.token_hex(32)
    invite_crear(token, correo, invitado_por=sesion.get("usuario", ""), horas=48)
    enlace = f"{url_base()}/registro.html?token={token}"
    enviado = _enviar_correo(correo, "[SNW] Crea tu cuenta",
                              _html_correo_invitacion(enlace))
    auditoria_registrar(sesion.get("usuario", ""), "invitar", correo,
                        "Invitación enviada" if enviado else "Invitación creada (no se pudo enviar el correo)")
    return {"ok": True, "correo_enviado": enviado}


def invitacion_verificar(token: str):
    est = invite_estado(token)
    if est.get("estado") == "ok":
        return {"ok": True, "correo": est["correo"]}
    detalle = {
        "usado": "Este enlace ya se usó.",
        "expirado": "El enlace expiró (dura 48 horas). Pide que te inviten de nuevo.",
    }.get(est.get("estado"), "El enlace no es válido.")
    raise HTTPException(400, detail=detalle)


def activar_cuenta(body: ActivarCuentaIn):
    """Último paso de la invitación: la persona invitada elige su contraseña,
    la cuenta se crea con rol `usuario` y permisos básicos, y queda con la
    sesión ya iniciada (mismo formato de respuesta que /api/auth/login)."""
    est = invite_estado(body.token)
    if est.get("estado") != "ok":
        raise HTTPException(400, detail="El enlace no es válido o expiró. Pide que te inviten de nuevo.")
    err = validar_clave_segura(body.clave)
    if err:
        raise HTTPException(422, detail=err)
    correo = str(est["correo"]).strip().lower()
    if usuario_buscar(correo) is not None:
        invite_consumir(body.token)
        raise HTTPException(409, detail="Ya existe una cuenta con ese correo.")
    usuario_crear(
        correo, correo.split("@")[0], "usuario", list(PERMISOS_BASICOS),
        hashlib.sha256(body.clave.encode("utf-8")).hexdigest(),
    )
    invite_consumir(body.token)
    invitador = est.get("invitado_por") or "invitación"
    auditoria_registrar(invitador, "cuenta_creada", correo,
                        f"Cuenta creada por invitación de {invitador}" if est.get("invitado_por") else "Cuenta creada por invitación")

    usuario = usuario_buscar(correo)
    token = uuid.uuid4().hex
    ahora = time.time()
    SESIONES[token] = {
        "usuario": correo,
        "rol": usuario.get("rol", "usuario"),
        "nombre": usuario.get("nombre", correo),
        "permisos": permisos_efectivos(usuario),
        "creada": ahora,
        "actividad": ahora,
        "actividad_guardada": ahora,
    }
    guardar_sesiones()
    s = SESIONES[token]
    return {"token": token, "rol": s["rol"], "nombre": s["nombre"], "permisos": s["permisos"]}


def _puede_gestionar(actor: dict, objetivo: dict) -> tuple[bool, str]:
    """Reglas de quién puede tocar la cuenta `objetivo`."""
    if str(objetivo.get("usuario", "")).strip().lower() == str(actor.get("usuario", "")).strip().lower():
        return False, "No puedes modificar tu propia cuenta."
    if actor.get("rol") == "administrador" and objetivo.get("rol") in ROLES_PRIVILEGIADOS:
        return False, "Un administrador solo puede gestionar cuentas de rol «usuario»."
    return True, ""


def listar_usuarios(sesion: dict = Depends(solo_admin)):
    yo = str(sesion.get("usuario", "")).strip().lower()
    filas = []
    for u in usuarios_listar():
        rol = u.get("rol", "usuario")
        es_actual = str(u.get("usuario", "")).strip().lower() == yo
        puede, motivo = _puede_gestionar(sesion, u)
        filas.append({
            "usuario": u.get("usuario"),
            "nombre": u.get("nombre") or "",
            "rol": rol,
            "permisos": permisos_efectivos(u),
            "rol_total": rol in ROLES_PRIVILEGIADOS,
            "es_actual": es_actual,
            "editable": puede,
            "motivo_bloqueo": motivo,
            "activo": u.get("activo", True),
            "correo_recuperacion": u.get("correo_recuperacion") or "",
        })
    return {
        "usuarios": filas,
        "permisos_validos": list(PERMISOS_VALIDOS),
        "roles": list(ROLES),
        "mi_rol": sesion.get("rol"),
        "puede_cambiar_rol": sesion.get("rol") in ROLES_PRIVILEGIADOS,
        "desarrolladores": contar_desarrolladores(),
        "max_desarrolladores": MAX_DESARROLLADORES,
    }


def actualizar_usuario(usuario: str, body: UsuarioUpdIn, sesion: dict = Depends(solo_admin)):
    obj = usuario_buscar(usuario)
    if obj is None:
        raise HTTPException(404, detail="Usuario no encontrado.")
    puede, motivo = _puede_gestionar(sesion, obj)
    if not puede:
        raise HTTPException(403, detail=motivo)

    nuevo_rol = None
    if body.rol is not None and body.rol != obj.get("rol"):
        if sesion.get("rol") == "desarrollador":
            if body.rol not in ROLES:
                raise HTTPException(422, detail="Rol no válido.")
        elif sesion.get("rol") == "administrador":
            # Un administrador solo puede ascender una cuenta de rol «usuario»
            # a «administrador» (nunca a «desarrollador»).
            if body.rol != "administrador":
                raise HTTPException(403, detail="Un administrador solo puede dar permisos de administrador.")
        else:
            raise HTTPException(403, detail="No puedes cambiar el rol de esta cuenta.")
        if body.rol == "desarrollador" and contar_desarrolladores(excluir=obj["usuario"]) >= MAX_DESARROLLADORES:
            raise HTTPException(409, detail=f"Solo puede haber {MAX_DESARROLLADORES} desarrolladores.")
        nuevo_rol = body.rol

    nuevo_nombre = None
    if body.nombre is not None:
        nuevo_nombre = body.nombre.strip()[:120] or obj.get("nombre") or obj["usuario"].split("@")[0]

    nuevos_permisos = None
    if body.permisos is not None:
        nuevos_permisos = [p for p in body.permisos if p in PERMISOS_VALIDOS]
        # "Administrar tarifas y costos" necesita "Estadísticas".
        if "tarifas_editar" in nuevos_permisos and "estadisticas" not in nuevos_permisos:
            nuevos_permisos.append("estadisticas")

    # Arma el detalle de auditoría ANTES de aplicar los cambios (compara contra
    # el estado que tenía la cuenta) para que quede trazable qué cambió y quién.
    cambios = []
    if nuevo_rol is not None:
        cambios.append(f"Rol: {obj.get('rol')} → {nuevo_rol}")
    if nuevos_permisos is not None:
        antes = set(obj.get("permisos") or [])
        despues = set(nuevos_permisos)
        agregados = sorted(despues - antes)
        quitados = sorted(antes - despues)
        if agregados or quitados:
            partes = []
            if agregados:
                partes.append("+" + ", +".join(agregados))
            if quitados:
                partes.append("-" + ", -".join(quitados))
            cambios.append("Permisos: " + "; ".join(partes))
    if body.activo is not None and bool(body.activo) != bool(obj.get("activo", True)):
        cambios.append("Cuenta reactivada" if body.activo else "Cuenta desactivada")

    usuario_actualizar(obj["usuario"], nombre=nuevo_nombre, rol=nuevo_rol, permisos=nuevos_permisos,
                        activo=body.activo)
    if cambios:
        auditoria_registrar(sesion.get("usuario", ""), "editar", obj["usuario"], "; ".join(cambios))
    if body.activo is False:
        # Corta el acceso al instante: cierra cualquier sesión abierta de esa cuenta.
        for tk, s in list(SESIONES.items()):
            if str(s.get("usuario", "")).strip().lower() == obj["usuario"]:
                SESIONES.pop(tk, None)
        guardar_sesiones()
    fresco = usuario_buscar(obj["usuario"]) or obj
    return {"ok": True, "usuario": fresco["usuario"], "rol": fresco["rol"],
            "permisos": permisos_efectivos(fresco), "activo": fresco.get("activo", True)}


def asignar_correo_recuperacion(usuario: str, body: CorreoRecuperacionIn, sesion: dict = Depends(solo_admin)):
    """Un admin/dev le asigna (o cambia) el correo de recuperación a una cuenta
    que gestiona, típicamente para poder mandarle luego un enlace de cambio de
    contraseña. Se verifica que ese correo no esté ya en uso en otra cuenta."""
    obj = usuario_buscar(usuario)
    if obj is None:
        raise HTTPException(404, detail="Usuario no encontrado.")
    puede, motivo = _puede_gestionar(sesion, obj)
    if not puede:
        raise HTTPException(403, detail=motivo)
    correo_rec = (body.correo or "").strip().lower()
    if not _EMAIL_RE.match(correo_rec):
        raise HTTPException(422, detail="Escribe un correo electrónico válido.")
    if correo_en_uso(correo_rec, excluir_usuario=obj["usuario"]):
        raise HTTPException(409, detail="Ese correo ya está en uso en otra cuenta.")
    usuario_set_correo_recuperacion(obj["usuario"], correo_rec)
    auditoria_registrar(sesion.get("usuario", ""), "correo_recuperacion", obj["usuario"],
                        f"Correo de recuperación asignado: {correo_rec}")
    return {"ok": True, "correo_recuperacion": correo_rec}


def enviar_cambio_clave(usuario: str, sesion: dict = Depends(solo_admin)):
    """Un admin/dev activa el cambio de contraseña de una cuenta que gestiona:
    le manda el mismo enlace de «Olvidé mi contraseña» al correo que la cuenta
    tiene registrado. Requiere que ya haya un correo (propio o de recuperación)."""
    obj = usuario_buscar(usuario)
    if obj is None:
        raise HTTPException(404, detail="Usuario no encontrado.")
    puede, motivo = _puede_gestionar(sesion, obj)
    if not puede:
        raise HTTPException(403, detail=motivo)
    destino = (obj.get("correo_recuperacion") or "").strip()
    if not destino and _EMAIL_RE.match(str(obj.get("usuario", ""))):
        destino = obj["usuario"]
    if not _EMAIL_RE.match(destino):
        raise HTTPException(400, detail="Esta cuenta no tiene un correo asignado. Asígnale uno primero.")
    token = secrets.token_hex(32)
    reset_crear(token, obj["usuario"], horas=2)
    enlace = f"{url_base()}/reset.html?token={token}"
    enviado = _enviar_correo(destino, "[SNW] Restablecer tu contraseña",
                              _html_correo_reset(obj.get("nombre") or obj["usuario"], enlace))
    auditoria_registrar(sesion.get("usuario", ""), "reset_clave", obj["usuario"],
                        f"Enlace de cambio de contraseña enviado a {destino}")
    return {"ok": True, "correo_enviado": enviado, "destino": destino}


def envios_de_usuario(usuario: str, sesion: dict = Depends(solo_admin)):
    """Envíos masivos que inició esta cuenta: fecha, plantilla, estado
    (completado/cancelado/rechazado), cantidad de pacientes y costo
    aproximado. De solo lectura: cualquier admin/dev puede consultar el
    historial de cualquier cuenta (no aplican las reglas de «quién gestiona a
    quién», que son solo para editar permisos/rol/acceso)."""
    obj = usuario_buscar(usuario)
    if obj is None:
        raise HTTPException(404, detail="Usuario no encontrado.")
    com_col = "comentario" if "comentario" in columnas_tabla("envios", "produccion") else "NULL AS comentario"
    sql = (f"SELECT id, base_datos, plantilla_clave, plantilla_nombre, total_pacientes,"
           f" enviados, fallidos, invalidos, estado, {com_col}, fecha_hora FROM envios"
           " WHERE usuario = %s ORDER BY id DESC LIMIT 200")
    with conectar("produccion") as conn, conn.cursor() as cur:
        cur.execute(sql, (obj["usuario"],))
        filas = cur.fetchall()
    for f in filas:
        f["fecha"] = f.pop("fecha_hora").strftime("%d-%m-%Y %H:%M")
        f["costo"] = _costo_estimado_por_clave(f.get("plantilla_clave") or "", f.get("total_pacientes") or 0)
    return filas


def auditoria_de_usuario(usuario: str, sesion: dict = Depends(solo_admin)):
    """Trazabilidad de la cuenta: qué hizo (a quién invitó, a quién le cambió
    permisos/rol/acceso, a quién le asignó un correo de recuperación o le
    activó el cambio de contraseña, qué plantilla creó, etc.). Se guarda en
    la cuenta que HIZO la acción, no en la que la recibió. De solo lectura,
    igual que «Envíos realizados»: no aplican las reglas de «quién gestiona a
    quién»."""
    obj = usuario_buscar(usuario)
    correo = (usuario or "").strip().lower()
    if obj is None and not auditoria_listar(correo, limite=1, campo="actor"):
        raise HTTPException(404, detail="Usuario no encontrado.")
    filas = auditoria_listar(correo, campo="actor")
    for f in filas:
        f["fecha"] = f.pop("fecha_hora").strftime("%d-%m-%Y %H:%M")
    return filas


def eliminar_usuario(usuario: str, sesion: dict = Depends(solo_admin)):
    obj = usuario_buscar(usuario)
    if obj is None:
        raise HTTPException(404, detail="Usuario no encontrado.")
    puede, motivo = _puede_gestionar(sesion, obj)
    if not puede:
        raise HTTPException(403, detail=motivo)
    correo = str(obj.get("usuario", "")).strip().lower()
    usuario_borrar(correo)
    auditoria_registrar(sesion.get("usuario", ""), "eliminar", correo, "Cuenta eliminada")
    for tk, s in list(SESIONES.items()):
        if str(s.get("usuario", "")).strip().lower() == correo:
            SESIONES.pop(tk, None)
    guardar_sesiones()
    return {"ok": True}


def _plantilla_valida(p) -> bool:
    """Descarta entradas rotas (ej. ediciones manuales de plantillas.json que
    dejan un objeto a medias): hace falta id, nombre y texto como mínimo."""
    return (
        isinstance(p, dict)
        and p.get("id") is not None
        and bool(str(p.get("nombre") or "").strip())
        and bool(str(p.get("texto") or "").strip())
    )


def leer_plantillas() -> list:
    try:
        datos = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        if not isinstance(datos, list):
            return []
        return [p for p in datos if _plantilla_valida(p)]
    except FileNotFoundError:
        return []
    except Exception as e:
        log_error(f"leer_plantillas: {DATA_FILE.name} ilegible", e)
        return []


def escribir_plantillas(datos: list) -> None:
    DATA_FILE.write_text(
        json.dumps(datos, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )




def slug(texto: str) -> str:
    t = unicodedata.normalize("NFD", texto.strip().lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = re.sub(r"[^a-z0-9]+", "_", t).strip("_")
    return t[:40] or "plantilla"


def from_pacientes(ambiente: str) -> str:
    """Cláusula FROM + LEFT JOIN sobre la tabla de pacientes del entorno."""
    t = tabla_pacientes(ambiente)
    return (
        f" FROM {t} p"
        " LEFT JOIN log_envios l ON l.id = ("
        "   SELECT l2.id FROM log_envios l2"
        "   WHERE l2.paciente_id = p.id ORDER BY l2.id DESC LIMIT 1"
        ")"
    )


def expr_respuesta_efectiva(alias: str = "p", tiene_opt_out: bool = True,
                            tiene_manual: bool = False) -> str:
    """Respuesta de un paciente para saber quién interactuó por WhatsApp.

    Prioridad:
      1. opt-out activo  -> 'baja'
      2. corrección manual (columna respuesta_manual), si existe
      3. señal automática 'pegajosa': la más fuerte que haya tenido alguna vez
         (baja > respondió), ignorando los ajustes manuales antiguos
      4. 'pendiente'
    Se usa igual en /api/pacientes y en /api/estadisticas."""
    baja_opt = f"WHEN {alias}.whatsapp_opt_out = 1 THEN 'baja' " if tiene_opt_out else ""
    manual = (
        f"WHEN {alias}.respuesta_manual IS NOT NULL AND {alias}.respuesta_manual <> '' "
        f"THEN {alias}.respuesta_manual "
        if tiene_manual else ""
    )
    ex = lambda r: (
        f"WHEN EXISTS(SELECT 1 FROM log_envios le WHERE le.paciente_id = {alias}.id"
        f" AND COALESCE(le.plantilla_clave, '') <> 'ajuste_manual'"
        f" AND le.respuesta = '{r}') THEN '{r}' "
    )
    return (
        "(CASE "
        + baja_opt
        + manual
        + ex("baja")
        + ex("respondio")
        + "ELSE 'pendiente' END)"
    )


def _pacientes_baja_bloqueada(cur, tabla: str, ambiente: str, ids: list[int]) -> set[int]:
    """IDs (entre los pedidos) que pidieron la baja con sus propias palabras
    por WhatsApp: no se les puede quitar esa respuesta a mano, solo si el
    propio paciente se retracta (vuelve a escribir mostrando interés)."""
    if not ids or not columna_existe(tabla, "opt_out_explicito", ambiente):
        return set()
    placeholders = ", ".join("%s" for _ in ids)
    cur.execute(
        f"SELECT id FROM {tabla} WHERE id IN ({placeholders})"
        " AND whatsapp_opt_out = 1 AND opt_out_explicito = 1",
        tuple(ids),
    )
    return {r["id"] for r in cur.fetchall()}


def expr_select_pacientes(ambiente: str) -> str:
    """Genera las expresiones SELECT de la tabla pacientes adaptándose a las
    columnas reales existentes (soporta bases con esquema mínimo)."""
    cols = columnas_tabla(tabla_pacientes(ambiente), ambiente)
    exprs = ["p.id", "p.nombre", "p.apellido", "p.telefono"]
    if "estado" in cols:
        exprs.append("COALESCE(NULLIF(p.estado, ''), 'pendiente') AS estado")
    else:
        exprs.append("'pendiente' AS estado")
    if "fecha_actualizacion" in cols:
        exprs.append("p.fecha_actualizacion")
    else:
        exprs.append("NULL AS fecha_actualizacion")
    exprs.append(
        expr_respuesta_efectiva("p", "whatsapp_opt_out" in cols, "respuesta_manual" in cols)
        + " AS respuesta"
    )
    exprs.append("COALESCE(l.respuesta, 'pendiente') AS respuesta_ultimo_envio")
    if "whatsapp_opt_out" in cols:
        exprs.append("p.whatsapp_opt_out")
    else:
        exprs.append("0 AS whatsapp_opt_out")
    if "opt_out_explicito" in cols:
        exprs.append("p.opt_out_explicito")
    else:
        exprs.append("0 AS opt_out_explicito")
    if "interesado" in cols:
        exprs.append("COALESCE(p.interesado, 0) AS interesado")
    else:
        exprs.append("0 AS interesado")
    if "no_interesado" in cols:
        exprs.append("COALESCE(p.no_interesado, 0) AS no_interesado")
    else:
        exprs.append("0 AS no_interesado")
    exprs.append("l.id AS ultimo_log_id")
    exprs.append("l.estado_envio AS ultimo_estado_envio")
    exprs.append("l.descripcion_error AS ultimo_error")
    # Fecha y texto del último mensaje que ESCRIBIÓ el paciente (filas de log
    # con plantilla_clave = 'respuesta', que inserta el webhook).
    exprs.append(
        "(SELECT le.fecha_hora FROM log_envios le WHERE le.paciente_id = p.id"
        "  AND le.plantilla_clave = 'respuesta' ORDER BY le.id DESC LIMIT 1) AS ultima_respuesta_fecha"
    )
    exprs.append(
        "(SELECT le.mensaje FROM log_envios le WHERE le.paciente_id = p.id"
        "  AND le.plantilla_clave = 'respuesta' ORDER BY le.id DESC LIMIT 1) AS ultimo_mensaje_recibido"
    )
    return ", ".join(exprs)


def listar_pacientes(q: str | None = Query(None), ambiente: str = Query("produccion"),
                     sesion: dict = Depends(exigir("pacientes"))):
    sql = "SELECT " + expr_select_pacientes(ambiente) + from_pacientes(ambiente)
    args: list = []
    if q and q.strip():
        like = f"%{q.strip()}%"
        sql += " WHERE p.nombre LIKE %s OR p.telefono LIKE %s"
        args = [like, like]
    sql += " ORDER BY p.id"

    with conectar(ambiente) as conn, conn.cursor() as cur:
        cur.execute(sql, tuple(args) or None)
        filas = cur.fetchall()

    for f in filas:
        fecha = f.pop("fecha_actualizacion", None)
        f["actualizado"] = fecha.strftime("%d-%m-%Y %H:%M") if fecha else "—"
        fr = f.get("ultima_respuesta_fecha")
        f["ultima_respuesta_fecha"] = fr.strftime("%d-%m-%Y %H:%M") if fr else None
    return filas


class EstadoPacienteIn(BaseModel):
    estado: str


class RespuestaIn(BaseModel):
    respuesta: str


class EstadoPacientesBulkIn(BaseModel):
    pacientes: list[int]
    estado: str


def actualizar_estado_pacientes(body: EstadoPacientesBulkIn, ambiente: str = Query("produccion"),
                                sesion: dict = Depends(exigir("pacientes"))):
    """Cambia el estado de varios pacientes de una sola vez (selección en
    Base de datos). Misma validación y permiso que el ajuste individual."""
    if not body.pacientes:
        raise HTTPException(400, detail="No se seleccionó ningún paciente")
    if body.estado not in ("pendiente", "enviado", "error"):
        raise HTTPException(400, detail="Estado inválido. Use: pendiente, enviado o error")
    t = tabla_pacientes(ambiente)
    placeholders = ", ".join("%s" for _ in body.pacientes)
    with conectar(ambiente) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT id FROM {t} WHERE id IN ({placeholders})", tuple(body.pacientes))
        encontrados = [r["id"] for r in cur.fetchall()]
        if not encontrados:
            raise HTTPException(404, detail="No se encontró ninguno de los pacientes seleccionados")
        if columna_existe(t, "estado", ambiente):
            placeholders_enc = ", ".join("%s" for _ in encontrados)
            cur.execute(
                f"UPDATE {t} SET estado = %s WHERE id IN ({placeholders_enc})",
                (body.estado, *encontrados),
            )
            conn.commit()
    return {"ok": True, "actualizados": len(encontrados)}


class RespuestaPacientesBulkIn(BaseModel):
    pacientes: list[int]
    respuesta: str


def actualizar_respuesta_pacientes(body: RespuestaPacientesBulkIn, ambiente: str = Query("produccion"),
                                   sesion: dict = Depends(exigir("pacientes"))):
    """Ajuste manual de la respuesta de varios pacientes a la vez (mismo efecto
    que el ajuste individual: 'baja' activa el opt-out y quita el interés)."""
    if not body.pacientes:
        raise HTTPException(400, detail="No se seleccionó ningún paciente")
    # "respondio" no es un ajuste manual válido: solo la pone el paciente al
    # contestar de verdad por WhatsApp (vía el webhook), nunca un admin a mano.
    if body.respuesta not in ("pendiente", "baja"):
        raise HTTPException(400, detail="Respuesta inválida. Use: pendiente, baja")
    t = tabla_pacientes(ambiente)
    tiene_manual = columna_existe(t, "respuesta_manual", ambiente)
    tiene_interesado = columna_existe(t, "interesado", ambiente)
    placeholders = ", ".join("%s" for _ in body.pacientes)
    with conectar(ambiente) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT id FROM {t} WHERE id IN ({placeholders})", tuple(body.pacientes))
        encontrados = [r["id"] for r in cur.fetchall()]
        if not encontrados:
            raise HTTPException(404, detail="No se encontró ninguno de los pacientes seleccionados")

        bloqueados: set[int] = set()
        if body.respuesta != "baja":
            bloqueados = _pacientes_baja_bloqueada(cur, t, ambiente, encontrados)
            encontrados = [pid for pid in encontrados if pid not in bloqueados]
        if not encontrados:
            raise HTTPException(
                409,
                detail="Los pacientes seleccionados pidieron la baja con sus propias palabras por "
                       "WhatsApp: no se les puede quitar esa respuesta a mano.",
            )
        placeholders_enc = ", ".join("%s" for _ in encontrados)

        cur.execute(
            f"UPDATE {t} SET whatsapp_opt_out = %s WHERE id IN ({placeholders_enc})",
            (1 if body.respuesta == "baja" else 0, *encontrados),
        )
        if body.respuesta == "baja" and tiene_interesado:
            cur.execute(f"UPDATE {t} SET interesado = 0 WHERE id IN ({placeholders_enc})", tuple(encontrados))
        if tiene_manual:
            cur.execute(
                f"UPDATE {t} SET respuesta_manual = %s WHERE id IN ({placeholders_enc})",
                (body.respuesta, *encontrados),
            )
        if body.respuesta == "pendiente":
            cur.execute(
                "UPDATE log_envios SET respuesta = 'pendiente'"
                f" WHERE paciente_id IN ({placeholders_enc}) AND respuesta = 'baja'",
                tuple(encontrados),
            )
        elif not tiene_manual:
            # Esquema antiguo sin columna: se conserva el mecanismo por log
            # (una fila por paciente, no se puede hacer en un solo UPDATE).
            for pid in encontrados:
                cur.execute(
                    "INSERT INTO log_envios (paciente_id, nombre_paciente, numero_telefono,"
                    " mensaje, plantilla_clave, estado_envio, respuesta)"
                    f" SELECT id, nombre, telefono, %s, 'ajuste_manual', 'enviado', %s"
                    f" FROM {t} WHERE id = %s",
                    (f"[Ajuste manual · {sesion.get('nombre', 'admin')}]", body.respuesta, pid),
                )
        conn.commit()
    return {"ok": True, "actualizados": len(encontrados), "bloqueados": len(bloqueados)}


def actualizar_paciente(paciente_id: int, body: EstadoPacienteIn,
                        ambiente: str = Query("produccion"),
                        sesion: dict = Depends(exigir("pacientes"))):
    if body.estado not in ("pendiente", "enviado", "error"):
        raise HTTPException(400, detail="Estado inválido. Use: pendiente, enviado o error")
    t = tabla_pacientes(ambiente)
    with conectar(ambiente) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT id FROM {t} WHERE id = %s", (paciente_id,))
        if not cur.fetchone():
            raise HTTPException(404, detail="Paciente no encontrado")
        if columna_existe(t, "estado", ambiente):
            cur.execute(f"UPDATE {t} SET estado = %s WHERE id = %s", (body.estado, paciente_id))
            conn.commit()
        cur.execute(
            "SELECT " + expr_select_pacientes(ambiente) + from_pacientes(ambiente) + " WHERE p.id = %s",
            (paciente_id,),
        )
        fila = cur.fetchone()
        fecha = fila.pop("fecha_actualizacion", None)
        fila["actualizado"] = fecha.strftime("%d-%m-%Y %H:%M") if fecha else "—"
        return fila


def actualizar_respuesta_paciente(paciente_id: int, body: RespuestaIn,
                                  ambiente: str = Query("produccion"),
                                  sesion: dict = Depends(exigir("pacientes"))):
    """Ajuste manual de la respuesta de un paciente (fallback si el webhook no
    llegó, o si el paciente avisó por otro canal). 'baja' activa el opt-out."""
    # "respondio" no es un ajuste manual válido: solo la pone el paciente al
    # contestar de verdad por WhatsApp (vía el webhook), nunca un admin a mano.
    if body.respuesta not in ("pendiente", "baja"):
        raise HTTPException(400, detail="Respuesta inválida. Use: pendiente, baja")
    t = tabla_pacientes(ambiente)
    tiene_manual = columna_existe(t, "respuesta_manual", ambiente)
    with conectar(ambiente) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT id FROM {t} WHERE id = %s", (paciente_id,))
        if not cur.fetchone():
            raise HTTPException(404, detail="Paciente no encontrado")

        if body.respuesta != "baja" and _pacientes_baja_bloqueada(cur, t, ambiente, [paciente_id]):
            raise HTTPException(
                409,
                detail="Este paciente pidió la baja con sus propias palabras por WhatsApp: no se "
                       "puede quitar esa respuesta a mano. Solo se revierte si el paciente vuelve "
                       "a escribir mostrando interés.",
            )

        cur.execute(
            f"UPDATE {t} SET whatsapp_opt_out = %s WHERE id = %s",
            (1 if body.respuesta == "baja" else 0, paciente_id),
        )
        # Al marcar 'baja' se quita la marca de interés.
        if body.respuesta == "baja" and columna_existe(t, "interesado", ambiente):
            cur.execute(f"UPDATE {t} SET interesado = 0 WHERE id = %s", (paciente_id,))
        # La corrección manual gana sobre la señal automática 'pegajosa'.
        if tiene_manual:
            cur.execute(
                f"UPDATE {t} SET respuesta_manual = %s WHERE id = %s",
                (body.respuesta, paciente_id),
            )
        # Deshace un 'baja' que el webhook hubiera marcado en el último envío.
        if body.respuesta == "pendiente":
            cur.execute(
                "UPDATE log_envios SET respuesta = 'pendiente'"
                " WHERE paciente_id = %s AND respuesta = 'baja'",
                (paciente_id,),
            )
        elif not tiene_manual:
            # Esquema antiguo sin columna: se conserva el mecanismo por log.
            cur.execute(
                "INSERT INTO log_envios (paciente_id, nombre_paciente, numero_telefono,"
                " mensaje, plantilla_clave, estado_envio, respuesta)"
                f" SELECT id, nombre, telefono, %s, 'ajuste_manual', 'enviado', %s"
                f" FROM {t} WHERE id = %s",
                (f"[Ajuste manual · {sesion.get('nombre', 'admin')}]", body.respuesta, paciente_id),
            )
        conn.commit()

        cur.execute(
            "SELECT " + expr_select_pacientes(ambiente) + from_pacientes(ambiente) + " WHERE p.id = %s",
            (paciente_id,),
        )
        fila = cur.fetchone()
    fecha = fila.pop("fecha_actualizacion", None)
    fila["actualizado"] = fecha.strftime("%d-%m-%Y %H:%M") if fecha else "—"
    fr = fila.get("ultima_respuesta_fecha")
    fila["ultima_respuesta_fecha"] = fr.strftime("%d-%m-%Y %H:%M") if fr else None
    return fila


def mensajes_paciente(paciente_id: int, ambiente: str = Query("produccion"),
                      sesion: dict = Depends(exigir("historial", "pacientes"))):
    """Todos los mensajes (entrantes y salientes) de un paciente, para revisar
    a mano si su interés es real. Los entrantes se guardan tal cual los escribió.
    Accesible a cualquier usuario (solo lectura)."""
    t = tabla_pacientes(ambiente)
    cols = columnas_tabla(t, ambiente)
    tiene_opt = "whatsapp_opt_out" in cols
    tiene_int = "interesado" in cols
    re_expr = expr_respuesta_efectiva("p", tiene_opt, "respuesta_manual" in cols)
    with conectar(ambiente) as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT p.id, p.nombre, p.apellido, p.telefono,"
            f" {'p.whatsapp_opt_out' if tiene_opt else '0'} AS whatsapp_opt_out,"
            f" {'COALESCE(p.interesado, 0)' if tiene_int else '0'} AS interesado,"
            f" {re_expr} AS respuesta"
            f" FROM {t} p WHERE p.id = %s",
            (paciente_id,),
        )
        pac = cur.fetchone()
        if not pac:
            raise HTTPException(404, detail="Paciente no encontrado")
        cur.execute(
            "SELECT id, fecha_hora, mensaje, plantilla_clave, estado_envio, descripcion_error"
            " FROM log_envios WHERE paciente_id = %s"
            "   AND ((mensaje IS NOT NULL AND mensaje <> '') OR plantilla_clave = 'respuesta')"
            "   AND COALESCE(plantilla_clave, '') <> 'ajuste_manual'"
            " ORDER BY id",
            (paciente_id,),
        )
        filas = cur.fetchall()
    pac["interesado"] = bool(pac.get("interesado"))
    pac["whatsapp_opt_out"] = bool(pac.get("whatsapp_opt_out"))
    if sesion.get("rol") not in ROLES_PRIVILEGIADOS:
        # El número de teléfono del paciente es solo para admin/dev.
        pac["telefono"] = None
    mensajes = []
    for f in filas:
        entrante = (f.get("plantilla_clave") or "") == "respuesta"
        txt = (f.get("mensaje") or "").strip()
        mensajes.append({
            "id": f["id"],
            "fecha": f["fecha_hora"].strftime("%d-%m-%Y %H:%M"),
            "direccion": "entrante" if entrante else "saliente",
            "texto": txt,
            "plantilla": None if entrante else (f.get("plantilla_clave") or None),
            "estado": f.get("estado_envio"),
            "error": f.get("descripcion_error"),
            "interes": entrante and es_mensaje_interes(txt),
        })
    return {"paciente": pac, "mensajes": mensajes}


def _call_center_numeros() -> list[str]:
    """Respaldo manual de números del call center (solo dígitos), en orden.
    Se usa solo si `call_center_url` no responde. Uno o varios, separados por
    coma, en la clave `call_center_numeros`."""
    crudo = config_get("call_center_numeros", "") or ""
    out = []
    for parte in crudo.split(","):
        n = re.sub(r"\D", "", parte)
        if n and n not in out:
            out.append(n)
    return out


def _numero_call_center_desde_url() -> str:
    """Pide un número al servicio configurado en `call_center_url`. Ese servicio
    devuelve un único número (solo dígitos, con código de país) y ya reparte la
    carga por su cuenta. Devuelve '' si no hay URL o la respuesta no sirve."""
    url = (config_get("call_center_url", "") or "").strip()
    if not url:
        return ""
    try:
        with httpx.Client(timeout=8, follow_redirects=True) as c:
            r = c.get(url)
        r.raise_for_status()
        n = re.sub(r"\D", "", r.text or "")
        # Un móvil chileno con código de país son 11 dígitos; damos margen.
        return n if 8 <= len(n) <= 15 else ""
    except Exception as e:
        log_error(f"_numero_call_center_desde_url({url})", e)
        return ""


def _contadores_call_center() -> dict:
    """{numero: nº de respuestas enviadas con ese número} según call_center_log
    (envíos con éxito). Como los números los entrega un servicio externo, se
    listan todos los que hayan aparecido en el registro."""
    usos = {}
    try:
        with conectar() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT numero_call_center AS n, COUNT(*) AS c FROM call_center_log"
                "  WHERE estado = 'enviado' GROUP BY numero_call_center ORDER BY c DESC, n"
            )
            for row in cur.fetchall():
                if row["n"]:
                    usos[row["n"]] = int(row["c"] or 0)
    except Exception as e:
        log_error("_contadores_call_center", e)
    return usos


def _elegir_numero_call_center() -> str:
    """Número del call center para la próxima respuesta. Primero se lo pide al
    servicio de `call_center_url` (que ya reparte la carga); si no responde, usa
    el respaldo manual `call_center_numeros` (el que menos se ha usado)."""
    n = _numero_call_center_desde_url()
    if n:
        return n
    nums = _call_center_numeros()
    if not nums:
        return ""
    if len(nums) == 1:
        return nums[0]
    usos = _contadores_call_center()
    minimo = min((usos.get(x, 0) for x in nums), default=0)
    return random.choice([x for x in nums if usos.get(x, 0) == minimo])


def _registrar_call_center_log(paciente_id, nombre, numero_paciente, numero_cc,
                               plantilla_clave, automatico, estado, error, base_datos) -> None:
    try:
        with conectar() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO call_center_log (paciente_id, nombre_paciente, numero_paciente,"
                " numero_call_center, plantilla_clave, automatico, estado, descripcion_error, base_datos)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (paciente_id, nombre, numero_paciente, numero_cc, plantilla_clave,
                 1 if automatico else 0, estado, (error or "")[:255] or None, base_datos),
            )
            conn.commit()
    except Exception as e:
        log_error("_registrar_call_center_log", e)


# Palabras que, si aparecen en la última plantilla enviada al paciente, hacen
# que el botón del call center autocomplete el mensaje de «oferta» en vez del
# genérico.
_CC_PALABRAS_OFERTA = re.compile(
    r"descuento|oferta|promoci[oó]n|precio\s+(?:especial|preferencial|rebajado)|"
    r"rebaja|liquidaci[oó]n|beca|arancel\s+especial|2\s*x\s*1|\d+\s*%|por\s+ciento|gratis|"
    r"sin\s+costo",
    re.IGNORECASE,
)


def _mensaje_boton_call_center(paciente_id, ambiente: str) -> str:
    """Texto que se autocompleta en el chat del call center cuando el paciente
    pulsa el botón. Depende de la última plantilla normal que se le envió: si
    mencionaba un descuento o un precio especial, usa el mensaje de «oferta»."""
    generico = (config_get("call_center_boton_mensaje", "") or "").strip()
    oferta = (config_get("call_center_boton_mensaje_oferta", "") or "").strip() or generico
    if not generico and not oferta:
        return ""
    if paciente_id is None:
        return generico
    try:
        with conectar(ambiente) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT mensaje FROM log_envios WHERE paciente_id = %s"
                "  AND estado_envio = 'enviado'"
                "  AND COALESCE(plantilla_clave, '') NOT IN (%s, 'respuesta', 'ajuste_manual')"
                " ORDER BY id DESC LIMIT 1",
                (paciente_id, CALL_CENTER_CLAVE),
            )
            fila = cur.fetchone()
    except Exception as e:
        log_error(f"_mensaje_boton_call_center({paciente_id}, {ambiente})", e)
        return generico
    texto_origen = (fila or {}).get("mensaje") or ""
    return oferta if _CC_PALABRAS_OFERTA.search(texto_origen) else generico


def _cc_datos_envio(pac: dict, ambiente: str) -> tuple[str, dict, str]:
    """Devuelve (texto renderizado, datos_plantilla, numero_asignado) para el
    mensaje fijo de call center. Añade el botón que abre el chat del call
    center si hay al menos un número configurado; el número se elige por
    menor uso. El botón lleva un mensaje autocompletado según lo que decía la
    última plantilla enviada."""
    texto = renderizar_mensaje(CALL_CENTER_PLANTILLA_FIJA["texto"], pac)
    datos = {"texto": texto}
    numero = _elegir_numero_call_center()
    if numero:
        url = f"https://wa.me/{numero}"
        prefijo = _mensaje_boton_call_center(pac.get("id"), ambiente)
        if prefijo:
            url += "?text=" + urllib.parse.quote(prefijo, safe="")
        datos["cta"] = {
            "texto": CALL_CENTER_PLANTILLA_FIJA["cc_boton_texto"][:20],
            "url": url,
        }
    return texto, datos, numero


def _despachar_call_center(pac: dict, ambiente: str) -> dict:
    """Envía el mensaje fijo de call center a un paciente y lo registra en el
    historial y en call_center_log. Devuelve {ok, error, telefono}. No lanza."""
    telefono = normalizar_telefono((pac.get("telefono") or "").strip())
    if telefono is None:
        return {"ok": False, "error": "El teléfono del paciente no es válido", "telefono": None}

    cfg = leer_config(ambiente)
    if entorno_valido(ambiente) == "desarrollo" and telefono not in set(cfg.get("numeros_autorizados", [])):
        return {"ok": False, "error": "El número no está autorizado en la base de desarrollo", "telefono": telefono}

    nombre = " ".join(x for x in [pac.get("nombre"), pac.get("apellido")] if x)
    texto, datos_plantilla, numero_cc = _cc_datos_envio(pac, ambiente)

    canal = obtener_canal(cfg)
    try:
        resultado = canal.enviar(telefono, texto, plantilla=datos_plantilla)
    except Exception as e:
        log_error(f"_despachar_call_center(pac={pac.get('id')}, {ambiente})", e)
        resultado = (False, None, f"Error inesperado: {e}")
    if len(resultado) == 3:
        ok, message_id, error = resultado
    else:
        ok, error = resultado
        message_id = None

    registrar_historial(pac.get("id"), nombre, telefono, CALL_CENTER_CLAVE, texto,
                        "enviado" if ok else "error", error, ambiente=ambiente,
                        whatsapp_message_id=message_id)
    if numero_cc:
        _registrar_call_center_log(
            pac.get("id"), nombre, telefono, numero_cc, CALL_CENTER_CLAVE,
            True, "enviado" if ok else "error", error, nombre_base(ambiente),
        )
    return {"ok": ok, "error": error, "telefono": telefono, "numero_call_center": numero_cc}


# Teléfonos con un envío automático de call center ya programado o en curso: si
# el paciente manda varios mensajes de interés seguidos, la respuesta sale UNA
# sola vez (no una por cada mensaje).
_cc_auto_lock = threading.Lock()
_cc_auto_pendientes: set[str] = set()


def _enviar_call_center_auto(tel: str) -> None:
    """Timer: envía el mensaje fijo de call center al paciente interesado cuyo
    número coincide, en la(s) base(s) donde esté marcado como interesado.

    Se manda UNA vez por cada plantilla enviada (una ronda): si el paciente
    responde interés a una plantilla nueva, se le manda el call center; si
    vuelve a escribir «me interesa» sin que haya una plantilla nueva de por
    medio, no se repite. Una baja y reintegración en cualquier momento lo
    vuelven elegible (si vuelve a estar `interesado`)."""
    try:
        match = "REPLACE(REPLACE(telefono, '+', ''), ' ', '') = REPLACE(REPLACE(%s, '+', ''), ' ', '')"
        for amb in ("produccion", "desarrollo"):
            try:
                t = tabla_pacientes(amb)
                if "interesado" not in columnas_tabla(t, amb):
                    continue
                with conectar(amb) as conn, conn.cursor() as cur:
                    cur.execute(f"SELECT * FROM {t} WHERE {match} AND interesado = 1 LIMIT 1", (tel,))
                    pac = cur.fetchone()
                    if not pac:
                        continue
                    # Un call center por cada plantilla REAL enviada (ronda).
                    # La exclusión es la lista canónica de plantillas "de
                    # sistema" (misma que usa _ultima_plantilla_ofertada): los
                    # marcadores que inserta el webhook (interes_boton,
                    # no_interes_boton, respuesta, bajas, avisos y ajustes)
                    # NO cuentan como plantilla, así que repetir interés en la
                    # misma ronda no re-dispara el envío.
                    cur.execute(
                        "SELECT"
                        "  (SELECT MAX(id) FROM log_envios WHERE paciente_id = %s"
                        "     AND estado_envio = 'enviado'"
                        "     AND COALESCE(plantilla_clave, '') NOT IN"
                        "      ('respuesta', 'ajuste_manual', 'call_center', 'interes_boton',"
                        "       'no_interes_boton', 'baja_aviso', 'retractacion_aviso')"
                        "  ) AS ult_plantilla,"
                        "  (SELECT MAX(id) FROM log_envios WHERE paciente_id = %s"
                        "     AND plantilla_clave = %s AND estado_envio = 'enviado') AS ult_cc",
                        (pac["id"], pac["id"], CALL_CENTER_CLAVE),
                    )
                    d = cur.fetchone() or {}
                    ult_plantilla, ult_cc = d.get("ult_plantilla"), d.get("ult_cc")
                    # Si ya se le mandó el call center después de la última
                    # plantilla, no hubo plantilla nueva en el medio: el interés
                    # repetido no se responde otra vez.
                    if ult_cc is not None and (ult_plantilla is None or ult_cc > ult_plantilla):
                        continue
                res = _despachar_call_center(pac, amb)
                if res.get("ok"):
                    print(f"[CALL-CENTER auto] enviado a {res['telefono']} ({amb})"
                          + (f" · call center {res['numero_call_center']}" if res.get("numero_call_center") else ""),
                          flush=True)
                else:
                    log_error(f"_enviar_call_center_auto({tel}, {amb}): {res.get('error')}")
            except Exception as e:
                log_error(f"_enviar_call_center_auto({tel}, {amb})", e)
    finally:
        with _cc_auto_lock:
            _cc_auto_pendientes.discard(tel)


CALL_CENTER_AUTO_SEGUNDOS = 1  # espera fija: siempre da tiempo a que quede feedback perceptible.


def _programar_call_center_auto(telefono: str) -> None:
    """Callback del webhook: programa el envío automático 1s después de
    detectar interés. Si ya hay uno programado para este número, no programa
    otro."""
    tel = normalizar_telefono(telefono) or telefono
    with _cc_auto_lock:
        if tel in _cc_auto_pendientes:
            return
        _cc_auto_pendientes.add(tel)
    timer = threading.Timer(CALL_CENTER_AUTO_SEGUNDOS, _enviar_call_center_auto, args=(tel,))
    timer.daemon = True
    timer.start()


# El webhook llama a este callback al detectar un mensaje de interés.
whatsapp_service.al_detectar_interes = _programar_call_center_auto


_MENSAJE_BAJA_DESPEDIDA = (
    "Lamentamos que te vayas. Si quieres, puedes reactivar las notificaciones"
    " en cualquier momento escribiendo cualquier mensaje."
)
_MENSAJE_BIENVENIDA_DEVUELTA = "¡Bienvenido/a de vuelta! Ya reactivamos tus notificaciones."


def _enviar_mensaje_directo(telefono_evento: str, texto: str, clave_log: str) -> None:
    """Manda un mensaje de texto libre (respuesta inmediata, dentro de la
    ventana de 24h) al paciente que coincide con este teléfono. Se usa para
    los avisos automáticos de baja y retractación: no dependen de un template
    aprobado por Meta porque son respuesta inmediata a un mensaje del propio
    paciente.

    El mensaje sale UNA sola vez: se busca en producción y, si el paciente
    existe ahí, es el único envío. Desarrollo solo se usa como respaldo
    cuando el paciente no está en producción."""
    tel = normalizar_telefono(telefono_evento) or telefono_evento
    match = "REPLACE(REPLACE(telefono, '+', ''), ' ', '') = REPLACE(REPLACE(%s, '+', ''), ' ', '')"
    for amb in ("produccion", "desarrollo"):
        try:
            t = tabla_pacientes(amb)
            with conectar(amb) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT * FROM {t} WHERE {match} LIMIT 1", (tel,))
                pac = cur.fetchone()
            if not pac:
                continue
            cfg = leer_config(amb)
            if entorno_valido(amb) == "desarrollo" and tel not in set(cfg.get("numeros_autorizados", [])):
                continue
            nombre = " ".join(x for x in [pac.get("nombre"), pac.get("apellido")] if x)
            canal = obtener_canal(cfg)
            try:
                resultado = canal.enviar(tel, texto)
            except Exception as e:
                log_error(f"_enviar_mensaje_directo(pac={pac.get('id')}, {amb})", e)
                resultado = (False, None, f"Error inesperado: {e}")
            if len(resultado) == 3:
                ok, message_id, error = resultado
            else:
                ok, error = resultado
                message_id = None
            registrar_historial(pac.get("id"), nombre, tel, clave_log, texto,
                                "enviado" if ok else "error", error, ambiente=amb,
                                whatsapp_message_id=message_id)
            break
        except Exception as e:
            log_error(f"_enviar_mensaje_directo({telefono_evento}, {amb})", e)


def _avisar_baja(telefono: str) -> None:
    _enviar_mensaje_directo(telefono, _MENSAJE_BAJA_DESPEDIDA, "baja_aviso")


def _avisar_retractacion(telefono: str) -> None:
    _enviar_mensaje_directo(telefono, _MENSAJE_BIENVENIDA_DEVUELTA, "retractacion_aviso")


def _programar_aviso(fn, telefono: str) -> None:
    """El webhook (endpoint async) llama a estos callbacks de forma síncrona,
    dentro del event loop en curso; MotorApiOficial.enviar usa asyncio.run()
    por dentro, que no se puede anidar en un loop ya corriendo. Igual que el
    auto-envío de call center (_programar_call_center_auto), se despacha en
    un hilo aparte para salir del event loop."""
    timer = threading.Timer(0, fn, args=(telefono,))
    timer.daemon = True
    timer.start()


# El webhook llama a estos callbacks al detectar una baja (por botón o texto
# libre) y al detectar que un paciente dado de baja volvió a escribir.
whatsapp_service.al_detectar_baja = lambda tel: _programar_aviso(_avisar_baja, tel)
whatsapp_service.al_detectar_retractacion = lambda tel: _programar_aviso(_avisar_retractacion, tel)


def _registrar_template_meta(p: dict, nombre_anterior: str | None = None,
                             lang_anterior: str | None = None,
                             template_id_anterior: str | None = None) -> dict:
    """Registra la plantilla en Meta: la CREA si es nueva, o la EDITA si ya
    existía con el mismo nombre+idioma. Devuelve la plantilla actualizada con
    los datos de Meta (template_id, status, error).

    'nombre_anterior'/'lang_anterior'/'template_id_anterior' deben ser los
    valores que tenía la plantilla ANTES de aplicar los cambios del usuario;
    así se detecta si el template de Meta sigue siendo el mismo (edición) o
    si cambió a otro nombre/idioma distinto (lo que requiere crear uno nuevo).

    No rompe el flujo si Meta falla: el error queda guardado en la plantilla."""
    nombre_template = (p.get("whatsapp_template") or "").strip() or slug(p.get("nombre", ""))
    lang = (p.get("whatsapp_template_lang") or "").strip() or "es"
    categoria = (p.get("whatsapp_template_categoria") or "").strip()

    p["whatsapp_template"] = nombre_template
    p["whatsapp_template_lang"] = lang
    p["whatsapp_template_categoria"] = categoria

    # Salvaguarda: los endpoints de crear/editar ya exigen categoría, pero si
    # llegara sin ella no se intenta registrar en Meta.
    if not categoria:
        p["whatsapp_template_id"] = None
        p["whatsapp_template_status"] = None
        p["whatsapp_template_error"] = None
        return p

    # Solo reutilizamos el template_id conocido si el nombre y el idioma no
    # cambiaron; si cambiaron, es un template distinto y hay que crearlo.
    id_conocido = (
        template_id_anterior
        if nombre_anterior == nombre_template and lang_anterior == lang
        else None
    )

    try:
        servicio = WhatsAppService()
        resultado = servicio.guardar_template_meta(
            nombre_template, p.get("texto", ""), lang, categoria,
            template_id_conocido=id_conocido,
        )
    except Exception as e:
        log_error(f"_registrar_template_meta({nombre_template!r})", e)
        resultado = {"ok": False, "template_id": id_conocido, "status": None, "error": str(e)}

    p["whatsapp_template_id"] = resultado.get("template_id") or id_conocido
    p["whatsapp_template_status"] = resultado.get("status")
    p["whatsapp_template_error"] = resultado.get("error")
    # Crear o reenviar a revisión nunca deja la plantilla en APPROVED de
    # entrada (Meta siempre la vuelve a revisar); se limpia el aviso de
    # «aprobada recientemente» de una aprobación previa si la hubo.
    if resultado.get("status") != "APPROVED":
        p["whatsapp_template_aprobada_en"] = None
    return p


def listar_plantillas(sesion: dict = Depends(sesion_actual)):
    return sorted(leer_plantillas(), key=lambda p: p.get("actualizada", 0), reverse=True)


def _actualizar_estado_meta(p: dict) -> dict:
    """Consulta el estado real en Meta de una plantilla y actualiza sus campos.

    Si la consulta falla (Meta caído, rate limit, credenciales...) NO se pisa
    el último estado conocido: solo se registra el error. Esto importa sobre
    todo para el barrido automático (`_revisar_plantillas_pendientes`), que
    corre solo cada tantos minutos: una falla transitoria no debe hacer
    "desaparecer" una aprobación ya detectada.

    También registra `whatsapp_template_aprobada_en` (epoch ms) la primera vez
    que el estado pasa a APPROVED, para mostrar el aviso de «aprobada
    recientemente» un rato después de detectarlo (se limpia si deja de estar
    aprobada)."""
    nombre_template = (p.get("whatsapp_template") or "").strip()
    if not nombre_template:
        p["whatsapp_template_status"] = None
        p["whatsapp_template_error"] = "Esta plantilla no tiene un template de Meta configurado"
        p["whatsapp_template_rejected_reason"] = None
        p["whatsapp_template_aprobada_en"] = None
        return p

    lang = (p.get("whatsapp_template_lang") or "").strip() or "es"
    servicio = WhatsAppService()
    resultado = servicio.estado_template_meta(nombre_template, lang)

    if not resultado.get("ok"):
        p["whatsapp_template_error"] = resultado.get("error")
        return p

    anterior = p.get("whatsapp_template_status")
    nuevo = resultado.get("status")
    p["whatsapp_template_status"] = nuevo
    p["whatsapp_template_error"] = None
    p["whatsapp_template_rejected_reason"] = resultado.get("rejected_reason")
    if nuevo == "APPROVED" and anterior != "APPROVED":
        p["whatsapp_template_aprobada_en"] = int(time.time() * 1000)
    elif nuevo != "APPROVED":
        p["whatsapp_template_aprobada_en"] = None
    return p


def estado_plantilla_meta(plantilla_id: int, sesion: dict = Depends(sesion_actual)):
    plantillas = leer_plantillas()
    p = next((x for x in plantillas if x["id"] == plantilla_id), None)
    if p is None:
        raise HTTPException(404, detail="Plantilla no encontrada")

    p = _actualizar_estado_meta(p)
    escribir_plantillas(plantillas)
    return p


def actualizar_todos_estados_meta(sesion: dict = Depends(sesion_actual)):
    plantillas = leer_plantillas()
    for p in plantillas:
        if (p.get("whatsapp_template") or "").strip():
            _actualizar_estado_meta(p)
    escribir_plantillas(plantillas)
    return sorted(plantillas, key=lambda p: p.get("actualizada", 0), reverse=True)


# --- Revisión automática del estado en Meta (cron) --------------------------
# Nadie tiene que apretar "Consultar estado": cada `plantillas_revision_minutos`
# este barrido consulta en Meta las plantillas que todavía no están APPROVED
# (incluye las PENDING y las ya REJECTED, por si se reenviaron a revisión o
# Meta revierte un rechazo) y actualiza su estado solo. Así se detecta la
# aprobación O el rechazo sin intervención manual; el aviso de «aprobada
# recientemente» sale de acá (ver _actualizar_estado_meta). El frontend hace
# su propio polling cada 30 s y avisa con un toast apenas ve el cambio.
def _revisar_plantillas_pendientes() -> None:
    try:
        plantillas = leer_plantillas()
        pendientes = [
            p for p in plantillas
            if not p.get("especial")
            and (p.get("whatsapp_template") or "").strip()
            and p.get("whatsapp_template_status") != "APPROVED"
        ]
        for p in pendientes:
            _actualizar_estado_meta(p)
        if pendientes:
            escribir_plantillas(plantillas)
    except Exception as e:
        log_error("_revisar_plantillas_pendientes", e)
    finally:
        _programar_revision_plantillas()


def _programar_revision_plantillas(demora_seg: float | None = None) -> None:
    """Programa el próximo barrido según `plantillas_revision_minutos`
    (0 = desactivado). `demora_seg` fuerza una espera puntual en vez del
    intervalo completo: se usa para el primer chequeo al arrancar el
    servidor, así una aprobación/rechazo que ya estaba esperando se detecta
    en segundos y no hay que esperar el intervalo entero la primera vez."""
    try:
        minutos = float(config_get("plantillas_revision_minutos", "2") or 0)
    except (TypeError, ValueError):
        minutos = 5.0
    if minutos <= 0:
        return
    espera = demora_seg if demora_seg is not None else minutos * 60
    timer = threading.Timer(espera, _revisar_plantillas_pendientes)
    timer.daemon = True
    timer.start()


_programar_revision_plantillas(demora_seg=20)


def _texto_desde_componentes(components: list) -> str:
    """Extrae el texto del componente BODY de un template de Meta y traduce
    sus placeholders {{1}}, {{2}}... a los comodines internos ({nombre},
    {apellido}...), para que una plantilla importada por «Sincronizar» se
    pueda usar igual que una creada acá.

    Si el template trae valores de ejemplo (`example.body_text`, los que esta
    app manda al crear un template — ver `WhatsAppService.COMODINES`) se usan
    para saber a qué comodín corresponde cada posición, sin importar el
    orden. Si no hay ejemplo (templates hechos a mano en Meta), se asume el
    orden habitual de esta app: {{1}} = nombre, {{2}} = apellido. Cualquier
    posición que no se pueda identificar queda como {variable}."""
    texto = ""
    ejemplos: list = []
    for c in components or []:
        if (c.get("type") or "").upper() == "BODY":
            texto = c.get("text", "") or ""
            cuerpo_ejemplo = (c.get("example") or {}).get("body_text") or []
            if cuerpo_ejemplo and isinstance(cuerpo_ejemplo[0], list):
                ejemplos = cuerpo_ejemplo[0]
            break
    if not texto or "{{" not in texto:
        return texto

    ejemplo_a_comodin = {
        str(valor).strip().lower(): clave for clave, valor in WhatsAppService.COMODINES.items()
    }
    orden_por_defecto = list(WhatsAppService.COMODINES.keys())  # ["nombre", "apellido"]

    def reemplazo(m: re.Match) -> str:
        n = int(m.group(1))
        valor_ejemplo = str(ejemplos[n - 1]).strip().lower() if n - 1 < len(ejemplos) else ""
        comodin = ejemplo_a_comodin.get(valor_ejemplo)
        if not comodin:
            comodin = orden_por_defecto[n - 1] if n <= len(orden_por_defecto) else "variable"
        return "{" + comodin + "}"

    return re.sub(r"\{\{(\d+)\}\}", reemplazo, texto)


def _nombre_libre(base: str, ocupados: set) -> str:
    """Genera un nombre no usado a partir de 'base', agregando ' (2)', ' (3)'..."""
    nombre = base
    i = 2
    while nombre.lower() in ocupados:
        nombre = f"{base} ({i})"
        i += 1
    ocupados.add(nombre.lower())
    return nombre


def _clave_libre(base: str, ocupadas: set) -> str:
    """Genera una clave no usada a partir de 'base', agregando '_2', '_3'..."""
    clave = base
    i = 2
    while clave in ocupadas:
        clave = f"{base}_{i}"
        i += 1
    ocupadas.add(clave)
    return clave


def sincronizar_plantillas_meta(sesion: dict = Depends(exigir("mensajeria"))):
    """Solo lee de Meta (no crea ni edita nada allá), así que no hace falta
    el permiso de edición de plantillas: alcanza con poder ver Mensajería.

    Meta es la fuente de verdad para los templates: esta cuenta no tiene
    permiso para crear/editar templates vía API, así que se gestionan a mano
    en Meta y aquí solo se leen (GET). Esta acción:
      - Actualiza el estado/id/categoría de las plantillas locales que ya
        tienen un 'whatsapp_template' configurado, contra lo que hay en Meta.
      - Importa como plantillas nuevas los templates que existen en Meta y
        todavía no tienen una plantilla local asociada.
    No crea ni edita nada en Meta, solo lee."""
    servicio = WhatsAppService()
    resultado = servicio.listar_templates_meta()
    if not resultado["ok"]:
        raise HTTPException(502, detail=f"No se pudo consultar los templates en Meta: {resultado['error']}")

    templates_meta = resultado["templates"]
    plantillas = leer_plantillas()

    usados_id = set()
    actualizadas = 0
    for p in plantillas:
        nombre_tpl = (p.get("whatsapp_template") or "").strip()
        if not nombre_tpl:
            continue
        lang = (p.get("whatsapp_template_lang") or "").strip() or "es"
        match = next((t for t in templates_meta
                      if t.get("name") == nombre_tpl and t.get("language") == lang), None)
        if not match:
            match = next((t for t in templates_meta if t.get("name") == nombre_tpl), None)

        if match:
            usados_id.add(match.get("id"))

            # Meta es la fuente de verdad: el texto que realmente se envía es
            # el que está aprobado allá, no esta copia local. Por eso el
            # texto siempre se trae de Meta al sincronizar (si sus
            # componentes traen algo utilizable) — antes esto solo pasaba
            # si el texto local todavía tenía placeholders crudos ({{1}}...),
            # así que una plantilla ya "limpia" quedaba congelada para
            # siempre y nunca reflejaba un cambio hecho en Meta o en otro
            # servidor.
            texto_actual = p.get("texto") or ""
            texto_nuevo = texto_actual
            texto_traducido = _texto_desde_componentes(match.get("components"))
            if texto_traducido:
                texto_nuevo = texto_traducido

            cambio = (
                p.get("whatsapp_template_id") != match.get("id")
                or p.get("whatsapp_template_status") != match.get("status")
                or p.get("whatsapp_template_categoria") != match.get("category")
                or texto_nuevo != texto_actual
            )
            actualizadas += 1 if cambio else 0
            p["whatsapp_template_id"] = match.get("id")
            p["whatsapp_template_status"] = match.get("status")
            p["whatsapp_template_categoria"] = match.get("category") or p.get("whatsapp_template_categoria")
            p["whatsapp_template_lang"] = match.get("language") or lang
            p["whatsapp_template_rejected_reason"] = match.get("rejected_reason") or match.get("reject_reason")
            p["whatsapp_template_error"] = None
            p["texto"] = texto_nuevo
        else:
            p["whatsapp_template_status"] = None
            p["whatsapp_template_error"] = (
                f"No se encontró el template '{nombre_tpl}' (idioma '{lang}') en Meta."
            )

    nombres_ocupados = {p["nombre"].lower() for p in plantillas}
    claves_ocupadas = {p["clave"] for p in plantillas}
    siguiente_id = max((p["id"] for p in plantillas), default=0) + 1
    creadas = 0

    for t in templates_meta:
        if t.get("id") in usados_id:
            continue
        nombre_base = (t.get("name") or "").replace("_", " ").strip().capitalize() or "Template de Meta"
        nombre = _nombre_libre(nombre_base, nombres_ocupados)
        clave = _clave_libre(slug(nombre), claves_ocupadas)

        plantillas.append({
            "id": siguiente_id,
            "clave": clave,
            "nombre": nombre,
            "texto": _texto_desde_componentes(t.get("components")),
            "whatsapp_template": t.get("name"),
            "whatsapp_template_lang": t.get("language"),
            "whatsapp_template_categoria": t.get("category"),
            "whatsapp_template_id": t.get("id"),
            "whatsapp_template_status": t.get("status"),
            "whatsapp_template_rejected_reason": t.get("rejected_reason") or t.get("reject_reason"),
            "whatsapp_template_error": None,
            "actualizada": int(time.time() * 1000),
        })
        siguiente_id += 1
        creadas += 1

    escribir_plantillas(plantillas)
    return {
        "creadas": creadas,
        "actualizadas": actualizadas,
        "total_meta": len(templates_meta),
        "plantillas": sorted(plantillas, key=lambda p: p.get("actualizada", 0), reverse=True),
    }


def crear_plantilla(body: PlantillaIn, sesion: dict = Depends(exigir("plantillas_editar"))):
    if not body.nombre.strip() or not body.texto.strip():
        raise HTTPException(400, detail="Nombre y mensaje son obligatorios")
    if len(body.texto) > MAX_TEXTO_PLANTILLA:
        raise HTTPException(400, detail=f"El mensaje supera el limite de {MAX_TEXTO_PLANTILLA} caracteres")
    categoria = _validar_categoria_template(body.whatsapp_template_categoria)

    plantillas = leer_plantillas()
    clave = slug(body.clave or body.nombre)

    if clave == CALL_CENTER_CLAVE or any(p["clave"] == clave for p in plantillas):
        raise HTTPException(409, detail="Ya existe una plantilla con esa clave")

    nueva = {
        "id": max((p["id"] for p in plantillas), default=0) + 1,
        "clave": clave,
        "nombre": body.nombre.strip(),
        "texto": body.texto,
        # El nombre del template de Meta se deriva SIEMPRE del nombre de la
        # plantilla (no se acepta uno arbitrario desde el cliente).
        "whatsapp_template": slug(body.nombre) or None,
        "whatsapp_template_lang": (body.whatsapp_template_lang or "").strip() or None,
        "whatsapp_template_categoria": categoria,
        "actualizada": int(time.time() * 1000),
        "creado_por": sesion.get("usuario", ""),
        "creada_en": int(time.time() * 1000),
    }
    nueva = _registrar_template_meta(nueva)
    plantillas.append(nueva)
    escribir_plantillas(plantillas)
    # Trazabilidad: queda en la Actividad de quien la creó (Usuarios ->
    # cuenta -> Actividad, filtrado por actor), igual que las acciones sobre
    # cuentas. Acá el "objetivo" es la plantilla, no otra cuenta.
    auditoria_registrar(sesion.get("usuario", ""), "plantilla_creada", nueva["clave"],
                        f"Creó la plantilla «{nueva['nombre']}»")
    return nueva


def actualizar_plantilla(plantilla_id: int, body: PlantillaIn, sesion: dict = Depends(exigir("plantillas_editar"))):
    if not body.nombre.strip() or not body.texto.strip():
        raise HTTPException(400, detail="Nombre y mensaje son obligatorios")
    if len(body.texto) > MAX_TEXTO_PLANTILLA:
        raise HTTPException(400, detail=f"El mensaje supera el limite de {MAX_TEXTO_PLANTILLA} caracteres")

    plantillas = leer_plantillas()
    for p in plantillas:
        if p["id"] == plantilla_id:
            # El mensaje de call center es fijo (CALL_CENTER_PLANTILLA_FIJA):
            # no es editable desde ningún lado. Esto solo protege registros
            # antiguos que pudieran quedar marcados como 'especial'.
            if p.get("especial"):
                raise HTTPException(
                    400,
                    detail="El mensaje de call center no es editable.",
                )
            # Mientras esté pendiente de revisión en Meta la plantilla queda de
            # solo lectura (ver también DELETE y el envío); aprobada o
            # rechazada sí se puede editar (una rechazada, para corregirla).
            if p.get("whatsapp_template_status") not in ESTADOS_TEMPLATE_EDITABLES:
                raise HTTPException(
                    400,
                    detail="Esta plantilla todavía está pendiente de revisión en Meta: no se puede "
                           "editar hasta que se apruebe o sea rechazada.",
                )
            # Meta solo permite editar un template una vez cada 24h. Se avisa
            # antes de intentarlo en vez de dejar que falle allá.
            ultima_edicion = p.get("ultima_edicion")
            if ultima_edicion:
                transcurrido_ms = int(time.time() * 1000) - ultima_edicion
                restante_ms = 24 * 3600 * 1000 - transcurrido_ms
                if restante_ms > 0:
                    horas_restantes = math.ceil(restante_ms / 3600000)
                    raise HTTPException(
                        400,
                        detail=f"Esta plantilla se editó hace menos de 24 horas (Meta solo permite "
                               f"editar un template una vez al día): se podrá editar de nuevo en "
                               f"{horas_restantes} hora{'s' if horas_restantes != 1 else ''}.",
                    )
            # La categoría del template es obligatoria para poder guardar.
            categoria = _validar_categoria_template(body.whatsapp_template_categoria)
            # El nombre es permanente: una vez creada la plantilla no se puede
            # cambiar (solo eliminándola). La clave interna se deriva del nombre
            # al crear y quedaría desincronizada si se editara.
            if body.nombre.strip() != p.get("nombre", ""):
                raise HTTPException(
                    400,
                    detail="El nombre de la plantilla no se puede cambiar. Elimínala y crea una nueva.",
                )

            nombre_template_anterior = p.get("whatsapp_template")
            lang_anterior = p.get("whatsapp_template_lang")
            template_id_anterior = p.get("whatsapp_template_id")

            p["texto"] = body.texto
            p["whatsapp_template"] = p.get("whatsapp_template") or (slug(p.get("nombre", "")) or None)
            p["whatsapp_template_lang"] = (body.whatsapp_template_lang or "").strip() or None
            p["whatsapp_template_categoria"] = categoria
            p["actualizada"] = int(time.time() * 1000)
            p["ultima_edicion"] = p["actualizada"]
            p = _registrar_template_meta(p, nombre_template_anterior, lang_anterior, template_id_anterior)
            escribir_plantillas(plantillas)
            return p

    raise HTTPException(404, detail="Plantilla no encontrada")


def eliminar_plantilla(plantilla_id: int, sesion: dict = Depends(exigir("plantillas_editar"))):
    plantillas = leer_plantillas()
    objetivo = next((p for p in plantillas if p["id"] == plantilla_id), None)
    if objetivo is None:
        raise HTTPException(404, detail="Plantilla no encontrada")
    if objetivo.get("especial"):
        raise HTTPException(
            400,
            detail="El mensaje de call center no se puede eliminar desde acá.",
        )
    if objetivo.get("whatsapp_template_status") not in ESTADOS_TEMPLATE_EDITABLES:
        raise HTTPException(
            400,
            detail="Esta plantilla todavía está pendiente de revisión en Meta: no se puede "
                   "eliminar hasta que se apruebe o sea rechazada.",
        )

    # Borra también el template en Meta. Si Meta falla, se avisa pero la
    # plantilla local se elimina igual (no dejamos algo a medias en el sistema).
    aviso_meta = None
    borrado_meta = False
    nombre_template = (objetivo.get("whatsapp_template") or "").strip()
    if nombre_template:
        try:
            res = WhatsAppService().eliminar_template_meta(
                nombre_template, objetivo.get("whatsapp_template_id"),
            )
        except Exception as e:
            log_error(f"eliminar_plantilla({plantilla_id}): fallo borrando en Meta", e)
            res = {"ok": False, "error": str(e)}
        if res.get("ok"):
            borrado_meta = True
        else:
            aviso_meta = res.get("error") or "No se pudo borrar el template en Meta."

    escribir_plantillas([p for p in plantillas if p["id"] != plantilla_id])
    return {"ok": True, "meta_borrado": borrado_meta, "meta_advertencia": aviso_meta}


# --- Registro de respuestas de call center (solo lectura) ----------------------
# El mensaje de call center es fijo (CALL_CENTER_PLANTILLA_FIJA) y no se edita
# desde la app; solo queda su historial de envíos acá.

def obtener_call_center_log(sesion: dict = Depends(exigir("call_center_registro"))):
    """Últimas respuestas enviadas a pacientes interesados, con el número de
    call center asignado a cada una, y el contador de usos por número.
    Permiso propio: `call_center_registro` (admin/dev lo tienen de forma implícita)."""
    entradas = []
    try:
        with conectar() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, nombre_paciente, numero_paciente, numero_call_center,"
                " plantilla_clave, automatico, estado, descripcion_error, base_datos, fecha_hora"
                " FROM call_center_log ORDER BY id DESC LIMIT 200"
            )
            privilegiado = sesion.get("rol") in ROLES_PRIVILEGIADOS
            for r in cur.fetchall():
                r["fecha"] = r.pop("fecha_hora").strftime("%d-%m-%Y %H:%M")
                r["automatico"] = bool(r["automatico"])
                if not privilegiado:
                    # El número de teléfono del paciente es solo para admin/dev.
                    r["numero_paciente"] = None
                entradas.append(r)
    except Exception as e:
        log_error("obtener_call_center_log", e)
    return {"entradas": entradas, "contadores": _contadores_call_center()}


def _leer_config_legacy(ambiente: str | None = None) -> dict:
    # Vista mínima de la config que necesitan las pantallas y el motor de envío.
    # Los secretos y el resto de claves se ven solo en /api/configuracion/todo.
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


def obtener_configuracion(ambiente: str | None = Query(None),
                          sesion: dict = Depends(sesion_actual)):
    try:
        return leer_config(ambiente)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))


def actualizar_configuracion(body: ConfigIn, sesion: dict = Depends(solo_dev)):
    cambios: dict[str, str] = {}

    if body.entorno is not None:
        if body.entorno not in ("desarrollo", "produccion"):
            raise HTTPException(400, detail="Entorno inválido")
        cambios["entorno"] = body.entorno

    if body.metodo_envio is not None:
        if body.metodo_envio not in ("simulado", "api_oficial"):
            raise HTTPException(400, detail="Método de envío inválido")
        cambios["metodo_envio"] = body.metodo_envio

    if body.numeros_prueba_dev is not None:
        cambios["numeros_prueba_dev"] = ",".join(n.strip() for n in body.numeros_prueba_dev if n.strip())

    if body.numeros_prueba_prod is not None:
        cambios["numeros_prueba_prod"] = ",".join(n.strip() for n in body.numeros_prueba_prod if n.strip())

    if body.intervalo_ms is not None:
        cambios["intervalo_ms"] = str(max(0, int(body.intervalo_ms)))

    if body.smtp_port is not None:
        cambios["smtp_port"] = str(max(1, int(body.smtp_port)))

    if body.smtp_tls is not None:
        cambios["smtp_tls"] = "true" if body.smtp_tls else "false"

    # Resto de claves de texto: se guardan tal cual (recortando espacios).
    _texto = {
        "url_base": body.url_base,
        "smtp_host": body.smtp_host,
        "smtp_user": body.smtp_user,
        "smtp_pass": body.smtp_pass,
        "correo_emisor": body.correo_emisor,
        "correo_destino": body.correo_destino,
        "wa_token": body.wa_token,
        "wa_phone_id": body.wa_phone_id,
        "wa_business_account_id": body.wa_business_account_id,
        "wa_verify_token": body.wa_verify_token,
        "wa_template_nombre": body.wa_template_nombre,
        "wa_template_lang": body.wa_template_lang,
        "wa_webhook_path": body.wa_webhook_path,
        "wa_graph_version": body.wa_graph_version,
    }
    for clave, valor in _texto.items():
        if valor is not None:
            cambios[clave] = valor.strip()

    if cambios:
        config_set(cambios)

    return leer_config()


# ---------------------------------------------------------------------------
#  Página de Configuración (solo admin): ver y editar TODAS las claves.
# ---------------------------------------------------------------------------
_CONFIG_SECCIONES = [
    {
        "id": "app", "titulo": "Aplicación", "icono": "fa-sliders",
        "campos": [
            {"clave": "entorno", "etiqueta": "Entorno activo", "tipo": "select",
             "opciones": ["desarrollo", "produccion"],
             "ayuda": "En «desarrollo» los envíos masivos se bloquean y solo salen a los números de prueba."},
            {"clave": "metodo_envio", "etiqueta": "Método de envío", "tipo": "select",
             "opciones": ["simulado", "api_oficial"],
             "ayuda": "«simulado» no manda nada real; «api_oficial» usa la WhatsApp Cloud API de Meta."},
            {"clave": "intervalo_ms", "etiqueta": "Intervalo entre mensajes (ms)", "tipo": "number",
             "ayuda": "Pausa entre cada mensaje de un envío masivo. No es para los rate limits de "
                      "Meta (de eso se encarga «Throughput»): sirve para espaciar el envío y cuidar "
                      "la calificación de calidad del número, y para tener margen de pausar/cancelar. "
                      "1000 (1 msg/s) está bien para tandas normales; bajarlo (100–250) si se necesita "
                      "más velocidad —el throughput lo limita igual— o subirlo para ir más suave. "
                      "0 = tan rápido como permita el throughput."},
            {"clave": "numeros_prueba_dev", "etiqueta": "Números de prueba · desarrollo", "tipo": "text",
             "ayuda": "Separados por coma. En entorno de desarrollo solo se envía a estos."},
            {"clave": "numeros_prueba_prod", "etiqueta": "Números de prueba · producción", "tipo": "text",
             "ayuda": "Separados por coma."},
            {"clave": "sesion_expira_horas", "etiqueta": "Sesión inactiva · horas para expirar", "tipo": "number",
             "ayuda": "Una sesión se cierra sola si pasa este tiempo sin que la cuenta haga ninguna "
                      "acción en la app; cada acción renueva el plazo. 0 = las sesiones no expiran."},
        ],
    },
    {
        "id": "url", "titulo": "URL pública", "icono": "fa-link",
        "campos": [
            {"clave": "url_base", "etiqueta": "URL base del servidor", "tipo": "text",
             "ayuda": "Dominio público (ngrok o servidor) que se usa en los links del correo y del webhook. Ej: https://mi-dominio.com"},
        ],
    },
    {
        "id": "correo", "titulo": "Correo / SMTP", "icono": "fa-envelope",
        "campos": [
            {"clave": "smtp_host", "etiqueta": "Servidor SMTP", "tipo": "text"},
            {"clave": "smtp_port", "etiqueta": "Puerto", "tipo": "number"},
            {"clave": "smtp_user", "etiqueta": "Usuario", "tipo": "text"},
            {"clave": "smtp_pass", "etiqueta": "Contraseña", "tipo": "password", "secreto": True},
            {"clave": "smtp_tls", "etiqueta": "Usar TLS / STARTTLS", "tipo": "bool"},
            {"clave": "correo_emisor", "etiqueta": "Correo emisor (From)", "tipo": "text"},
            {"clave": "correo_destino", "etiqueta": "Correo del supervisor (confirmaciones)", "tipo": "text"},
        ],
    },
    {
        "id": "whatsapp", "titulo": "WhatsApp · Meta Cloud API", "icono": "fa-whatsapp", "marca": True,
        "campos": [
            {"clave": "wa_token", "etiqueta": "Access token", "tipo": "password", "secreto": True},
            {"clave": "wa_phone_id", "etiqueta": "Phone number ID", "tipo": "text"},
            {"clave": "wa_business_account_id", "etiqueta": "WhatsApp Business Account ID (WABA)", "tipo": "text"},
            {"clave": "wa_verify_token", "etiqueta": "Verify token del webhook", "tipo": "password", "secreto": True},
            {"clave": "wa_webhook_path", "etiqueta": "Ruta del webhook", "tipo": "text",
             "ayuda": "Se concatena a la URL base. Debe empezar con «/». Ej: /api/whatsapp/webhook"},
            {"tipo": "derivado", "etiqueta": "URL del webhook (para pegar en Meta)",
             "formula": ["url_base", "wa_webhook_path"],
             "ayuda": "URL base del servidor + ruta del webhook. Cópiala y pégala en el panel de "
                      "Webhooks de Meta; si cambia (nueva URL de servidor), hay que actualizarla allí.",
             "enlace": {
                 "url": "https://developers.facebook.com/apps/1392977325249373/webhooks/?business_id=799315784581199&view=whatsapp_business_account",
                 "texto": "Abrir Webhooks en Meta",
             }},
            {"clave": "wa_template_nombre", "etiqueta": "Template por defecto", "tipo": "text"},
            {"clave": "wa_template_lang", "etiqueta": "Idioma del template", "tipo": "text"},
            {"clave": "wa_graph_version", "etiqueta": "Versión de Graph API", "tipo": "text",
             "ayuda": "Ej: v26.0"},
            {"clave": "wa_moneda", "etiqueta": "Moneda de facturación", "tipo": "text",
             "ayuda": "Se autodetecta al pulsar «Actualizar tarifas» en Estadísticas. Ej: CLP, USD."},
            {"clave": "plantillas_revision_minutos", "etiqueta": "Revisar plantillas pendientes cada (min)", "tipo": "number",
             "ayuda": "Cada cuántos minutos se consulta en Meta el estado de las plantillas que "
                      "todavía no están aprobadas, para detectar la aprobación o el rechazo solas, "
                      "sin tener que consultarlo a mano. 0 = desactivado."},
            {"clave": "plantillas_badge_aprobada_minutos", "etiqueta": "Aviso «Aprobada recientemente» (min)", "tipo": "number",
             "ayuda": "Cuánto tiempo se muestra el aviso «✨ Aprobada recientemente» en la lista y el "
                      "editor de plantillas después de detectar la aprobación. 0 = no mostrarlo nunca."},
            {"clave": "wa_rate_limit_activo", "etiqueta": "Frenar envíos al acercarse al límite de Meta", "tipo": "bool",
             "ayuda": "Lee el consumo de cuota que Meta informa en cada respuesta y espera antes de "
                      "seguir enviando. Un error 429 de Meta se respeta aunque esto esté apagado."},
            {"clave": "wa_rate_limit_umbral_pct", "etiqueta": "Umbral de cuota para empezar a esperar (%)", "tipo": "number",
             "ayuda": "Por debajo de este porcentaje de uso no se añade ninguna espera. Ej: 80."},
            {"clave": "wa_rate_limit_pausa_max_s", "etiqueta": "Espera máxima entre mensajes (s)", "tipo": "number",
             "ayuda": "Espera que se aplica cuando la cuota llega al 100 %. Entre el umbral y el 100 % "
                      "se interpola."},
            {"clave": "wa_rate_limit_espera_defecto_s", "etiqueta": "Espera tras un 429 sin dato (s)", "tipo": "number",
             "ayuda": "Cuando Meta responde «límite alcanzado» pero no dice cuánto falta para "
                      "recuperar el acceso."},
            {"clave": "wa_rate_limit_reintentos", "etiqueta": "Reintentos por llamada tras un 429", "tipo": "number",
             "ayuda": "Cuántas veces se reintenta una misma llamada (esperando lo que indique Meta) "
                      "antes de darla por fallida."},
            {"clave": "wa_throughput_mps", "etiqueta": "Throughput · mensajes por segundo", "tipo": "number",
             "ayuda": "Ritmo máximo de salida hacia Meta (que permite 80/s por número, contando "
                      "entrantes y salientes). Conviene dejar margen. 0 = sin límite de ritmo."},
            {"clave": "wa_messaging_limit_24h", "etiqueta": "Messaging limit · usuarios únicos / 24 h", "tipo": "number",
             "ayuda": "Límite de Meta: usuarios únicos a los que el negocio puede escribir en una "
                      "ventana móvil de 24 h (250, 1000, 2000, 10000, 100000). Al alcanzarlo se bloquean "
                      "los envíos masivos en producción. 0 = ilimitado (sin control)."},
        ],
    },
    {
        "id": "bajas", "titulo": "Bajas y reactivaciones", "icono": "fa-user-slash",
        "campos": [
            {"clave": "anti_flip_flop_dev", "etiqueta": "Aplicar el límite también en desarrollo", "tipo": "bool",
             "ayuda": "En producción, el paciente que se da de baja y se reintegra no puede volver a "
                      "darse de baja hasta que pasen 24 h (siempre activo). Con esto activado, la "
                      "restricción también aplica en la base de desarrollo; desactívalo para probar "
                      "el flujo de baja/reintegración sin límite en desarrollo."},
        ],
    },
    {
        "id": "call_center", "titulo": "Call center", "icono": "fa-headset",
        "campos": [
            {"clave": "call_center_url", "etiqueta": "URL de teléfonos del call center", "tipo": "text",
             "ayuda": "Servicio que devuelve un número de call center (solo dígitos, con "
                      "código de país). Se consulta en cada respuesta y ese servicio ya "
                      "reparte la carga entre los teléfonos. Es el número al que lleva el "
                      "botón de las plantillas de call center."},
            {"clave": "call_center_numeros", "etiqueta": "Números de respaldo (manual)", "tipo": "text",
             "ayuda": "Uno o varios, separados por coma, con código de país y sin «+». "
                      "Ej: 56912345678, 56987654321. Solo se usan si la URL de números no "
                      "responde; si hay varios se elige el menos usado."},
            {"clave": "call_center_boton_mensaje", "etiqueta": "Mensaje del botón (genérico)", "tipo": "text",
             "ayuda": "Texto que se autocompleta en el chat del call center cuando el paciente pulsa "
                      "el botón. Déjalo vacío para no autocompletar nada."},
            {"clave": "call_center_boton_mensaje_oferta", "etiqueta": "Mensaje del botón (oferta / precio especial)", "tipo": "text",
             "ayuda": "Se usa en lugar del genérico cuando la última plantilla enviada al paciente "
                      "mencionaba un descuento, una oferta o un precio especial."},
        ],
    },
]
_CONFIG_ENUM = {
    "entorno": ("desarrollo", "produccion"),
    "metodo_envio": ("simulado", "api_oficial"),
}


def _config_valores() -> dict:
    cfg = config_all()
    return {c: ("" if cfg.get(c) is None else str(cfg.get(c))) for c in CONFIG_DEFAULTS}


def obtener_configuracion_completa(sesion: dict = Depends(solo_dev)):
    """Todas las claves de configuración con su valor real (incluye secretos)."""
    return {"secciones": _CONFIG_SECCIONES, "valores": _config_valores()}


def actualizar_configuracion_completa(body: ConfigTodoIn, sesion: dict = Depends(solo_dev)):
    cambios: dict[str, str] = {}
    for clave, valor in (body.cambios or {}).items():
        if clave not in CONFIG_DEFAULTS:
            raise HTTPException(400, detail=f"Clave de configuración desconocida: «{clave}»")
        v = "" if valor is None else str(valor).strip()
        if clave in _CONFIG_ENUM and v not in _CONFIG_ENUM[clave]:
            raise HTTPException(400, detail=f"«{clave}»: usa uno de {', '.join(_CONFIG_ENUM[clave])}")
        if clave in ("intervalo_ms", "smtp_port",
                     "wa_rate_limit_umbral_pct", "wa_rate_limit_pausa_max_s",
                     "wa_rate_limit_espera_defecto_s", "wa_rate_limit_reintentos",
                     "wa_throughput_mps", "wa_messaging_limit_24h", "sesion_expira_horas",
                     "plantillas_revision_minutos", "plantillas_badge_aprobada_minutos"):
            try:
                n = int(v or "0")
            except ValueError:
                raise HTTPException(400, detail=f"«{clave}» debe ser un número entero")
            minimo = 1 if clave == "smtp_port" else 0
            if clave == "wa_rate_limit_umbral_pct":
                n = min(100, n)
            v = str(max(minimo, n))
        if clave == "call_center_numeros":
            partes = [re.sub(r"\D", "", p) for p in v.split(",")]
            v = ", ".join(dict.fromkeys(p for p in partes if p))
        if clave in ("smtp_tls", "wa_rate_limit_activo", "anti_flip_flop_dev"):
            v = "true" if v.lower() in ("true", "1", "on", "si", "sí") else "false"
        cambios[clave] = v

    if cambios:
        config_set(cambios)
    return {"valores": _config_valores()}


def probar_api_wa(body: PruebaWAIn, sesion: dict = Depends(solo_dev)):
    """Envía un mensaje real vía la API oficial para validar las credenciales de WhatsApp."""
    cfg = leer_config()
    if cfg["metodo_envio"] != "api_oficial":
        raise HTTPException(400, detail="El método de envío no está en 'api_oficial' (cámbialo en Configuración)")

    canal = obtener_canal(cfg)
    if canal is None or not canal.disponible():
        raise HTTPException(
            400,
            detail="Faltan el token o el phone ID de WhatsApp (configúralos en Configuración)",
        )

    telefono = normalizar_telefono(body.telefono)
    if telefono is None:
        raise HTTPException(400, detail=f"Formato de teléfono inválido: '{body.telefono}'")

    resultado = canal.enviar(telefono, body.mensaje)
    if len(resultado) == 3:
        ok, message_id, error = resultado
    else:
        ok, error = resultado
        message_id = None
    return {"ok": ok, "telefono": telefono, "error": error, "message_id": message_id}


def estado_rate_limit(sesion: dict = Depends(solo_dev)):
    """Consumo de cuota de la Graph API visto en la última respuesta de Meta y
    la espera que el sistema está aplicando (si la hay)."""
    return wa_gobernador.estado()


# Plantillas cuyos envíos NO cuentan para el messaging limit de Meta: respuestas
# dentro de la ventana de 24 h (no son "iniciados por el negocio").
_CLAVES_NO_CUENTAN_LIMITE = ("call_center", "respuesta", "ajuste_manual")


def _limite_mensajeria_tier() -> int:
    try:
        return max(0, int(config_get("wa_messaging_limit_24h", "0") or 0))
    except (TypeError, ValueError):
        return 0


def _uso_mensajeria_24h() -> int:
    """Usuarios ÚNICOS a los que el negocio escribió (mensajes iniciados por el
    negocio) en las últimas 24 h. Es lo que Meta cuenta para el messaging limit.
    `log_envios` es una tabla compartida, así que el conteo no depende del entorno."""
    marcadores = ", ".join(["%s"] * len(_CLAVES_NO_CUENTAN_LIMITE))
    try:
        with conectar() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(DISTINCT REPLACE(REPLACE(numero_telefono, '+', ''), ' ', '')) AS n"
                "  FROM log_envios"
                " WHERE estado_envio = 'enviado'"
                "   AND fecha_hora >= (NOW() - INTERVAL 24 HOUR)"
                "   AND COALESCE(plantilla_clave, '') NOT IN (" + marcadores + ")",
                _CLAVES_NO_CUENTAN_LIMITE,
            )
            fila = cur.fetchone() or {}
            return int(fila.get("n") or 0)
    except Exception as e:
        log_error("_uso_mensajeria_24h", e)
        return 0


def _estado_limite_mensajeria() -> dict:
    tier = _limite_mensajeria_tier()
    usados = _uso_mensajeria_24h()
    return {
        "tier": tier,
        "usados_24h": usados,
        "disponibles": (max(0, tier - usados) if tier else None),
        "ventana_horas": 24,
    }


def _limite_mensajeria_actual() -> dict | None:
    """Cupo de messaging limit de Meta (compartido por entorno). None si no hay
    límite configurado. `disponibles` = usuarios únicos que aún se pueden
    contactar dentro de la ventana de 24 h."""
    tier = _limite_mensajeria_tier()
    if not tier:
        return None
    usados = _uso_mensajeria_24h()
    return {"tier": tier, "usados_24h": usados, "disponibles": max(0, tier - usados)}


def _limite_mensajeria_info(amb: str) -> dict | None:
    """Info del cupo para APLICARLO (enforcement): solo producción con la API
    oficial. None en cualquier otro caso."""
    if amb != "produccion" or config_get("metodo_envio", "").strip() != "api_oficial":
        return None
    return _limite_mensajeria_actual()


def _limite_mensajeria_aviso(amb: str) -> dict | None:
    """Info del cupo para MOSTRARLO. En producción se muestra siempre que aplique
    (API oficial); en desarrollo solo si el límite diario ya se alcanzó, para
    poder indicarlo sin bloquear el envío."""
    info = _limite_mensajeria_actual()
    if not info:
        return None
    if amb == "desarrollo":
        return info if info["disponibles"] <= 0 else None
    if amb == "produccion" and config_get("metodo_envio", "").strip() == "api_oficial":
        return info
    return None


def estado_messaging_limit(sesion: dict = Depends(solo_admin)):
    """Cuántos usuarios únicos se contactaron en las últimas 24 h frente al
    límite de mensajería configurado (Meta: 250 / 1K / 10K / 100K / ilimitado)."""
    return _estado_limite_mensajeria()


class MessagingLimitIn(BaseModel):
    limite: int


def actualizar_messaging_limit(body: MessagingLimitIn, sesion: dict = Depends(solo_admin)):
    """El admin ajusta el límite diario de Meta (`wa_messaging_limit_24h`) según
    lo que indique el dashboard de WhatsApp Business. 0 = ilimitado."""
    valor = max(0, int(body.limite))
    config_set({"wa_messaging_limit_24h": str(valor)})
    return _estado_limite_mensajeria()


JOBS: dict = {}
PENDIENTES: dict = {}

# Un solo envío a la vez en toda la aplicación: mientras un job está en curso
# (en proceso o pausado), ningún otro usuario puede iniciar/confirmar otro.
# La consulta bajo el lock garantiza que dos peticiones simultáneas no pasen
# el chequeo las dos a la vez.
JOBS_LOCK = threading.Lock()
ESTADOS_ENVIO_EN_CURSO = ("en_proceso", "pausado")


def _envio_en_curso(ambiente: str) -> dict | None:
    """Primer envío activo (en proceso o pausado) sobre esa base de datos, o
    None si no hay ninguno. El bloqueo es POR BASE: un envío en producción no
    impide iniciar otro en desarrollo (y al revés). Se llama SIEMPRE bajo JOBS_LOCK."""
    for job in JOBS.values():
        if job.get("estado") in ESTADOS_ENVIO_EN_CURSO and job.get("ambiente") == ambiente:
            return job
    return None


def _error_envio_en_curso(job: dict | None) -> HTTPException:
    """Mensaje para el usuario que intenta iniciar/confirmar un envío mientras
    hay otro en curso. Indica quién lo inició y qué se está enviando."""
    if job is None:
        return HTTPException(409, detail="Ya hay un envío en curso. Espera a que termine antes de iniciar otro.")
    quien = (job.get("nombre_enviador") or "").strip() or "otro usuario"
    plantilla = (job.get("plantilla") or {}).get("nombre") or ""
    ambiente = "producción" if job.get("ambiente") == "produccion" else "desarrollo"
    partes = ["Ya hay un envío en curso"]
    if quien:
        partes.append(f"iniciado por {quien}")
    if plantilla:
        partes.append(f"con la plantilla «{plantilla}»")
    partes.append(f"en la base de {ambiente}.")
    partes.append("Espera a que termine antes de iniciar otro.")
    return HTTPException(409, detail=" ".join(partes))


def _url_base_legacy() -> str:
    base = (config_get("url_base") or "").strip().rstrip("/")
    return base or "http://localhost:8000"


def _config_correo_legacy() -> dict:
    """Parámetros de correo/SMTP desde la tabla `configuracion`."""
    emisor = (config_get("correo_emisor") or "").strip()
    try:
        port = int((config_get("smtp_port") or "587").strip() or 587)
    except ValueError:
        port = 587
    return {
        "emisor": emisor,
        "destino": (config_get("correo_destino") or "").strip(),
        "host": (config_get("smtp_host") or "").strip(),
        "port": port,
        "user": (config_get("smtp_user") or "").strip() or emisor,
        "pwd": (config_get("smtp_pass") or "").strip().replace(" ", ""),
        "tls": (config_get("smtp_tls", "true") or "true").lower() in ("1", "true", "yes", "si"),
    }


def _enviar_correo_legacy(destino: str, subject: str, html: str) -> bool:
    """Envía un correo HTML usando la configuración SMTP. Devuelve True si se
    entregó (o si no hay SMTP y solo se registró en consola)."""
    c = _config_correo()
    emisor = c["emisor"]
    if not emisor or not destino:
        print(f"[CORREO] Sin emisor o destino para '{subject}' -> {destino!r}")
        return False
    host, port, user, pwd, tls = c["host"], c["port"], c["user"], c["pwd"], c["tls"]
    if not host or not pwd:
        print(f"[CORREO SIMULADO] Para {destino} desde {emisor}: {subject}")
        return True
    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = emisor
        msg["To"] = destino
        msg["Subject"] = subject
        msg.attach(MIMEText(html, "html", "utf-8"))
        context = ssl.create_default_context()
        with smtplib.SMTP(host, port) as server:
            if tls:
                server.starttls(context=context)
            if user and pwd:
                server.login(user, pwd)
            server.sendmail(emisor, destino, msg.as_string())
        print(f"[CORREO] Enviado a {destino}: {subject}")
        return True
    except Exception as e:
        log_error(f"_enviar_correo a {destino}", e)
        return False


def _fmt_moneda(monto: float, moneda: str) -> str:
    entero = float(monto).is_integer()
    s = (f"{monto:,.0f}" if entero else f"{monto:,.2f}").replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{s} {moneda}"


def _costo_estimado_por_clave(clave: str, total: int) -> dict | None:
    """Costo aproximado de enviar `total` mensajes de esta plantilla, en la
    moneda de facturación de la cuenta. None si no hay tarifas descargadas o la
    plantilla no se factura (texto libre / sin template)."""
    cat = _categorias_por_clave().get(clave)
    if not cat:
        return None
    moneda = _moneda_cuenta()
    vig = _tarifa_vigente(_tarifas_guardadas(moneda) or _tarifas_guardadas("USD"))
    rate = (vig or {}).get(cat)
    if rate is None:
        return None
    return {
        "moneda": (vig or {}).get("moneda") or moneda,
        "categoria": cat,
        "rate": float(rate),
        "total": total,
        "costo": math.ceil(float(rate) * total),
    }


def _enviar_correo_confirmacion(token: str, total: int, plantilla_nombre: str, plantilla_texto: str,
                                ambiente: str, plantilla_clave: str = "", solicitante: str = "") -> bool:
    c = _config_correo()
    emisor, destino = c["emisor"], c["destino"]
    base = url_base()
    if not emisor or not destino:
        print(f"[CORREO] Emisor o destino no configurado. Token {token} -> {base}/api/notificaciones/confirmar/{token}")
        return False
    host, port, user, pwd, tls = c["host"], c["port"], c["user"], c["pwd"], c["tls"]

    costo = _costo_estimado_por_clave(plantilla_clave, total)
    costo_txt = _fmt_moneda(costo["costo"], costo["moneda"]) if costo else "no disponible"
    quien = solicitante.strip() or "usuario desconocido"
    quien_html = _html.escape(quien)
    plantilla_nombre_html = _html.escape(plantilla_nombre)
    plantilla_texto_html = _html.escape(plantilla_texto)

    if not host or not pwd:
        # Modo simulado: logear URL para pruebas sin SMTP real
        print(f"[CORREO SIMULADO] Para {destino} desde {emisor}: solicitado por {quien} - confirmar {base}/api/notificaciones/confirmar/{token} | rechazar {base}/api/notificaciones/rechazar/{token} - {total} personas, plantilla '{plantilla_nombre}', base {ambiente}, costo aprox. {costo_txt}")
        return True

    confirm_url = f"{base}/api/notificaciones/confirmar/{token}"
    reject_url = f"{base}/api/notificaciones/rechazar/{token}"
    subject = f"[SNW] {quien} pide confirmar un envío masivo - {total} destinatarios (~{costo_txt})"

    if costo:
        bloque_costo = f"""
      <div style="text-align:center; margin:26px 0;">
        <p style="margin:0 0 4px; font-size:13px; color:#66757f;">Costo aproximado de este envío</p>
        <p style="margin:0; font-size:40px; line-height:1.1; font-weight:bold; color:#d11a1a;">{costo_txt}</p>
        <p style="margin:8px 0 0; font-size:12px; color:#66757f;">{total} mensajes &times; {_fmt_moneda(costo['rate'], costo['moneda'])} c/u &middot; categoría {costo['categoria'].capitalize()}</p>
      </div>"""
    else:
        bloque_costo = """
      <p style="text-align:center; margin:24px 0; font-size:13px; color:#b23b37; font-weight:bold;">
        Costo aproximado no disponible (revisa las tarifas de Meta en Estadísticas).
      </p>"""

    html = f"""
    <html><body style="font-family: Arial, sans-serif; color: #24303c;">
      <h2>Solicitud de envío masivo</h2>
      <p><strong>{quien_html}</strong> solicitó enviar la plantilla <strong>{plantilla_nombre_html}</strong> a <strong>{total} personas</strong> desde la base de datos <strong>{ambiente}</strong>.</p>
      {bloque_costo}
      <div style="background:#f5f7f8; border-left:4px solid #128c7e; padding:14px 16px; margin:18px 0; border-radius:6px;">
        <p style="margin:0 0 6px; font-size:12px; color:#66757f; font-weight:bold;">Mensaje a enviar:</p>
        <p style="margin:0; white-space:pre-wrap; font-family:Consolas,monospace; font-size:13px; color:#24303c;">{plantilla_texto_html}</p>
      </div>
      <p style="margin:24px 0;">
        <a href="{confirm_url}" style="display:inline-block; background:#128c7e; color:#fff; padding:12px 22px; border-radius:8px; text-decoration:none; font-weight:bold;">Confirmar envío</a>
        &nbsp;&nbsp;
        <a href="{reject_url}" style="display:inline-block; background:#b23b37; color:#fff; padding:12px 22px; border-radius:8px; text-decoration:none; font-weight:bold;">Rechazar envío</a>
      </p>
      <p>Si no reconoces esta solicitud, ignora este correo.</p>
      <p style="font-size:12px; color:#66757f;">Token: {token}</p>
    </body></html>
    """
    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = emisor
        msg["To"] = destino
        msg["Subject"] = subject
        msg.attach(MIMEText(html, "html", "utf-8"))
        context = ssl.create_default_context()
        with smtplib.SMTP(host, port) as server:
            if tls:
                server.starttls(context=context)
            if user and pwd:
                server.login(user, pwd)
            server.sendmail(emisor, destino, msg.as_string())
        print(f"[CORREO] Confirmación enviada a {destino} token {token}")
        return True
    except Exception as e:
        log_error(f"_enviar_correo_confirmacion a {destino}", e)
        return False


def renderizar_mensaje(texto: str, paciente: dict) -> str:
    reemplazos = {
        "{nombre}": paciente.get("nombre") or "",
        "{apellido}": paciente.get("apellido") or "",
    }
    for clave, valor in reemplazos.items():
        texto = texto.replace(clave, valor)
    return texto


def actualizar_estado_paciente(paciente_id: int, estado: str, ambiente: str) -> None:
    t = tabla_pacientes(ambiente)
    if not columna_existe(t, "estado", ambiente):
        return
    with conectar(ambiente) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {t} SET estado = %s WHERE id = %s", (estado, paciente_id))
        conn.commit()


def limpiar_interes_paciente(paciente_id: int, ambiente: str) -> None:
    """Al mandarle una plantilla nueva a un paciente, su interés/desinterés
    por la oferta ANTERIOR ya no aplica: queda «sin respuesta» hasta que
    conteste esta nueva oferta."""
    t = tabla_pacientes(ambiente)
    cols = columnas_tabla(t, ambiente)
    campos = [c for c in ("interesado", "no_interesado", "interes_plantilla_clave", "interes_fecha") if c in cols]
    if not campos:
        return
    set_sql = ", ".join(f"{c} = NULL" if c in ("interes_plantilla_clave", "interes_fecha") else f"{c} = 0" for c in campos)
    with conectar(ambiente) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {t} SET {set_sql} WHERE id = %s", (paciente_id,))
        conn.commit()


def actualizar_telefono(paciente_id: int, telefono: str, ambiente: str) -> None:
    t = tabla_pacientes(ambiente)
    if not columna_existe(t, "telefono", ambiente):
        return
    with conectar(ambiente) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {t} SET telefono = %s WHERE id = %s", (telefono, paciente_id))
        conn.commit()


def registrar_historial(paciente_id, nombre, telefono, clave_plantilla, mensaje, estado, error=None,
                        ambiente="produccion", envio_id=None, whatsapp_message_id=None) -> None:
    try:
        with conectar(ambiente) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO log_envios (envio_id, paciente_id, nombre_paciente, numero_telefono, mensaje,"
                " plantilla_clave, estado_envio, descripcion_error, whatsapp_message_id, estado_whatsapp)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (envio_id, paciente_id, nombre, telefono, mensaje, clave_plantilla, estado, error,
                 whatsapp_message_id, "sent" if estado == "enviado" and whatsapp_message_id else None),
            )
            conn.commit()
    except Exception as e:
        print(f"[HISTORIAL] No se pudo registrar: {e}")


def crear_envio_batch(base_datos, plantilla_clave, plantilla_nombre, total, ambiente, usuario: str = "") -> int | None:
    try:
        with conectar(ambiente) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO envios (base_datos, plantilla_clave, plantilla_nombre, total_pacientes, estado, usuario)"
                " VALUES (%s, %s, %s, %s, 'completado', %s)",
                (base_datos, plantilla_clave, plantilla_nombre, total, (usuario or "").strip().lower() or None),
            )
            conn.commit()
            return cur.lastrowid
    except Exception as e:
        print(f"[ENVIO] No se pudo crear batch: {e}")
        return None


def actualizar_envio_batch(envio_id, ambiente, enviados=0, fallidos=0, invalidos=0, estado=None) -> None:
    if envio_id is None:
        return
    try:
        with conectar(ambiente) as conn, conn.cursor() as cur:
            if estado:
                cur.execute(
                    "UPDATE envios SET enviados = enviados + %s, fallidos = fallidos + %s,"
                    " invalidos = invalidos + %s, estado = %s WHERE id = %s",
                    (enviados, fallidos, invalidos, estado, envio_id),
                )
            else:
                cur.execute(
                    "UPDATE envios SET enviados = enviados + %s, fallidos = fallidos + %s,"
                    " invalidos = invalidos + %s WHERE id = %s",
                    (enviados, fallidos, invalidos, envio_id),
                )
            conn.commit()
    except Exception as e:
        print(f"[ENVIO] No se pudo actualizar batch: {e}")


def procesar_job(job_id: str) -> None:
    """Wrapper: garantiza que cualquier error del envío quede en consola y
    marque el job como fallido en vez de morir en silencio."""
    try:
        _procesar_job(job_id)
    except Exception as e:
        log_error(f"procesar_job({job_id})", e)
        job = JOBS.get(job_id)
        if job is not None:
            job["estado"] = "error"
            job["detalle"] = f"Error interno del envío: {e}"


def _procesar_job(job_id: str) -> None:
    job = JOBS[job_id]
    amb = job["ambiente"]
    cfg = leer_config()
    canal = obtener_canal(cfg)
    clave = job.get("plantilla", {}).get("clave")
    plantilla_datos = job.get("plantilla")
    envio_id = job.get("envio_id")

    if canal is None or not canal.disponible():
        for d in job["destinatarios"]:
            actualizar_estado_paciente(d["id"], "error", amb)
            job["fallidos"] += 1
            registrar_historial(d["id"], d["nombre"], d["telefono"], clave,
                                d["mensaje"], "error",
                                f"Canal no disponible: {canal.nombre if canal else 'desconocido'}",
                                ambiente=amb, envio_id=envio_id)
        actualizar_envio_batch(envio_id, amb, fallidos=len(job["destinatarios"]))
        job["estado"] = "error"
        job["detalle"] = f"Canal de envío no disponible ({cfg.get('metodo_envio')})"
        return

    intervalo = max(0, int(cfg.get("intervalo_ms", 1000))) / 1000
    total = len(job["destinatarios"])

    for i, d in enumerate(job["destinatarios"]):
        if job.get("cancelado"):
            job["actual"] = ""
            job["estado"] = "cancelado"
            job["detalle"] = "Cancelado por el usuario"
            actualizar_envio_batch(envio_id, amb, enviados=job["enviados"], fallidos=job["fallidos"], estado="cancelado")
            break
        # Si está pausado, esperar (sin enviar) hasta reanudar o cancelar
        while job.get("pausado") and not job.get("cancelado"):
            job["estado"] = "pausado"
            time.sleep(0.5)
        job["estado"] = "en_proceso"
        if job.get("cancelado"):
            job["actual"] = ""
            job["estado"] = "cancelado"
            job["detalle"] = "Cancelado por el usuario"
            actualizar_envio_batch(envio_id, amb, enviados=job["enviados"], fallidos=job["fallidos"], estado="cancelado")
            break
        job["actual"] = d["nombre"]

        # Rate limits de Meta: si la cuota de la Graph API está alta (o Meta ya
        # nos frenó), esperar aquí —en tramos, para poder cancelar— y dejar el
        # motivo visible en el progreso, en vez de disparar errores 429.
        if canal.nombre == "api_oficial":
            espera_rl = wa_gobernador.pausa_antes_de_enviar()
            if espera_rl >= 1:
                job["estado"] = "en_proceso"
                job["detalle"] = (f"Esperando por el límite de la API de Meta "
                                  f"(~{int(espera_rl)} s)")
                fin = time.time() + espera_rl
                while time.time() < fin and not job.get("cancelado"):
                    time.sleep(min(1.0, max(0.0, fin - time.time())))
                job["detalle"] = ""

        try:
            resultado = canal.enviar(d["telefono"], d["mensaje"], plantilla=plantilla_datos, variables=d.get("variables"))
            # Unificar: (ok, message_id, error) o (ok, error) según el motor
            if len(resultado) == 3:
                ok, message_id, error = resultado
            else:
                ok, error = resultado
                message_id = None
        except Exception as e:
            log_error(f"procesar_job {job_id}: fallo enviando a {d.get('telefono')}", e)
            ok, message_id, error = False, None, f"Error inesperado: {e}"

        actualizar_estado_paciente(d["id"], "enviado" if ok else "error", amb)
        if ok:
            limpiar_interes_paciente(d["id"], amb)

        if ok:
            job["enviados"] += 1
        else:
            job["fallidos"] += 1
            job["errores"].append({"id": d["id"], "telefono": d["telefono"], "detalle": error})
            message_id = None
            log_error(f"procesar_job {job_id}: {d.get('telefono')} -> {error}")

        registrar_historial(d["id"], d["nombre"], d["telefono"], clave,
                            d["mensaje"], "enviado" if ok else "error", error,
                            ambiente=amb, envio_id=envio_id,
                            whatsapp_message_id=message_id)

        if i < total - 1 and intervalo > 0:
            time.sleep(intervalo)

    job["actual"] = ""
    if job.get("estado") != "cancelado":
        job["estado"] = "completado"
        actualizar_envio_batch(envio_id, amb, enviados=job["enviados"], fallidos=job["fallidos"])


def iniciar_envio(body: EnvioIn, background_tasks: BackgroundTasks,
                  sesion: dict = Depends(exigir("mensajeria"))):
    try:
        amb = entorno_valido(body.ambiente)
    except ValueError:
        raise HTTPException(400, detail=f"Entorno inválido: '{body.ambiente}'")

    # En ambiente de desarrollo, quien no tenga permiso de envío en producción
    # solo puede enviar a la base de desarrollo (números autorizados).
    entorno_global = config_get("entorno", "desarrollo").strip().lower()
    if entorno_global == "desarrollo" and not tiene_permiso(sesion, "envio_produccion"):
        amb = "desarrollo"

    # Un solo envío a la vez POR BASE: si ya hay uno en curso en esa misma base
    # (iniciado por este u otro usuario), se rechaza de inmediato con el detalle
    # de quién y qué se envía. Una base con un envío activo no bloquea a la otra.
    with JOBS_LOCK:
        job_en_curso = _envio_en_curso(amb)
    if job_en_curso is not None:
        raise _error_envio_en_curso(job_en_curso)

    if not body.pacientes and body.pacientes is not None:
        raise HTTPException(400, detail="No se seleccionaron pacientes")

    cfg = leer_config(amb)
    lista_autorizados = cfg.get("numeros_autorizados", [])
    autorizados = set(lista_autorizados)

    plantilla = next((p for p in leer_plantillas() if p["id"] == body.plantilla_id), None)
    if plantilla is None:
        raise HTTPException(404, detail="Plantilla no encontrada")
    if plantilla.get("especial"):
        raise HTTPException(
            400,
            detail="El mensaje de call center se manda solo, automáticamente, a los pacientes interesados.",
        )
    if plantilla.get("whatsapp_template_status") != "APPROVED":
        raise HTTPException(
            400,
            detail="Esta plantilla todavía no fue aprobada por Meta: no se puede usar para enviar "
                   "hasta que se apruebe.",
        )

    with conectar(amb) as conn, conn.cursor() as cur:
        t = tabla_pacientes(amb)
        tiene_opt_out = columna_existe(t, "whatsapp_opt_out", amb)
        tiene_estado = columna_existe(t, "estado", amb)

        base_select = "SELECT " + expr_select_pacientes(amb) + from_pacientes(amb)

        if body.pacientes:
            # Se traen TODOS los seleccionados (incluidos los de baja) para poder
            # informar al usuario por qué se descartó cada uno.
            placeholders = ", ".join("%s" for _ in body.pacientes)
            cur.execute(base_select + f" WHERE p.id IN ({placeholders})", tuple(body.pacientes))
        else:
            if amb == "desarrollo":
                # En desarrollo se puede reenviar sin importar el estado del paciente;
                # la única restricción real sigue siendo el filtro de números autorizados,
                # que se aplica más abajo en este mismo endpoint.
                if tiene_opt_out:
                    cur.execute(base_select + " WHERE p.whatsapp_opt_out = 0 ORDER BY p.id")
                else:
                    cur.execute(base_select + " ORDER BY p.id")
            else:
                cond = []
                if tiene_estado:
                    partes_estado = ["p.estado = 'pendiente'"]
                    # Los que respondieron "me interesa" / "no me interesa" a una
                    # oferta vuelven a ser elegibles al cabo de una semana.
                    if columna_existe(t, "interes_fecha", amb):
                        cols_t = columnas_tabla(t, amb)
                        partes_interes = [c for c in ("interesado", "no_interesado") if c in cols_t]
                        if partes_interes:
                            partes_estado.append(
                                "((" + " OR ".join(f"p.{c} = 1" for c in partes_interes) + ")"
                                " AND p.interes_fecha IS NOT NULL"
                                " AND p.interes_fecha < DATE_SUB(NOW(), INTERVAL 7 DAY))"
                            )
                    cond.append("(" + " OR ".join(partes_estado) + ")")
                if tiene_opt_out:
                    cond.append("p.whatsapp_opt_out = 0")
                if cond:
                    cur.execute(base_select + " WHERE " + " AND ".join(cond) + " ORDER BY p.id")
                else:
                    cur.execute(base_select + " ORDER BY p.id")
        filas = cur.fetchall()

    rechazados: list[dict] = []
    destinatarios: list[dict] = []

    for p in filas:
        telefono_crudo = (p.get("telefono") or "").strip()
        nombre_completo = " ".join(x for x in [p.get("nombre"), p.get("apellido")] if x)

        # Pacientes dados de baja o con opt-out: nunca se les envían mensajes
        if p.get("whatsapp_opt_out") or (p.get("respuesta") or "pendiente") == "baja":
            rechazados.append({"id": p["id"], "nombre": nombre_completo, "telefono": telefono_crudo,
                               "motivo": "Paciente se dio de baja"})
            continue

        telefono = normalizar_telefono(telefono_crudo)
        if telefono is None:
            rechazados.append({"id": p["id"], "nombre": nombre_completo, "telefono": telefono_crudo,
                               "motivo": "Formato de teléfono inválido"})
            actualizar_estado_paciente(p["id"], "error", amb)
            registrar_historial(p["id"], nombre_completo, telefono_crudo, plantilla["clave"],
                                "", "numero_invalido", f"Formato de teléfono inválido: '{telefono_crudo}'",
                                ambiente=amb)
            continue

        if telefono != telefono_crudo:
            actualizar_telefono(p["id"], telefono, amb)

        if amb == "desarrollo" and telefono not in autorizados:
            rechazados.append({"id": p["id"], "nombre": nombre_completo, "telefono": telefono,
                               "motivo": f"Número no autorizado en base {amb}"})
            registrar_historial(p["id"], nombre_completo, telefono, plantilla["clave"],
                                "", "error", f"Número no autorizado en base {amb}",
                                ambiente=amb)
            continue

        destinatarios.append({
            "id": p["id"],
            "nombre": nombre_completo,
            "telefono": telefono,
            "mensaje": renderizar_mensaje(plantilla["texto"], p),
            "variables": {
                "nombre": p.get("nombre") or "",
                "apellido": p.get("apellido") or "",
            },
        })

    # En producción se puede limitar cuántos se envían de esta tanda; el resto
    # queda pendiente para un envío posterior. En desarrollo no aplica.
    if amb == "produccion" and body.limite is not None and destinatarios:
        n = max(1, min(int(body.limite), len(destinatarios)))
        destinatarios = destinatarios[:n]

    # Messaging limit de Meta: usuarios ÚNICOS contactados (mensajes iniciados
    # por el negocio) en una ventana móvil de 24 h. No se permite superar el
    # cupo diario: si el lote es mayor que los usuarios que quedan disponibles,
    # se recorta (el resto queda pendiente para más adelante).
    aviso_limite = None
    info_limite = _limite_mensajeria_info(amb)
    if info_limite:
        tier = info_limite["tier"]
        usados = info_limite["usados_24h"]
        disponibles = info_limite["disponibles"]
        if disponibles <= 0:
            raise HTTPException(429, detail=(
                f"Límite diario de WhatsApp alcanzado: en las últimas 24 h ya se contactó a "
                f"{usados} usuarios únicos (límite {tier}). Espera a que avance la ventana de "
                f"24 h o sube el límite en Configuración."))
        if len(destinatarios) > disponibles:
            recortados = len(destinatarios) - disponibles
            destinatarios = destinatarios[:disponibles]
            aviso_limite = (
                f"El límite diario de WhatsApp es {tier} usuarios únicos en 24 h y ya se "
                f"contactó a {usados}: el envío se recortó a {disponibles} mensaje(s). Los "
                f"{recortados} restantes quedan pendientes para más adelante.")

    if not destinatarios:
        # Aunque no salga ningún mensaje, si hubo rechazados se deja constancia
        # del intento en el historial (0 enviados, N inválidos).
        envio_id = None
        if rechazados:
            envio_id = crear_envio_batch(nombre_base(amb), plantilla["clave"],
                                         plantilla["nombre"], len(rechazados), amb,
                                         usuario=sesion.get("usuario", ""))
            if envio_id:
                actualizar_envio_batch(envio_id, amb, invalidos=len(rechazados))
                for r in rechazados:
                    registrar_historial(r.get("id"), r.get("nombre"), r.get("telefono"),
                                        plantilla["clave"], "", "numero_invalido",
                                        r.get("motivo"), ambiente=amb, envio_id=envio_id)
        return {"iniciado": False, "total": 0, "rechazados": rechazados,
                "requiere_confirmacion": False, "envio_id": envio_id}

    # En producción se requiere confirmación por correo del supervisor, salvo
    # que la cuenta tenga el permiso de envío directo en producción.
    if amb == "produccion" and not tiene_permiso(sesion, "envio_produccion"):
        token = uuid.uuid4().hex
        envio_id = crear_envio_batch(nombre_base(amb), plantilla["clave"], plantilla["nombre"],
                                     len(destinatarios) + len(rechazados), amb,
                                     usuario=sesion.get("usuario", ""))
        PENDIENTES[token] = {
            "ambiente": amb,
            "plantilla": {"id": plantilla["id"], "clave": plantilla["clave"],
                          "nombre": plantilla["nombre"], "texto": plantilla["texto"],
                          "whatsapp_template": plantilla.get("whatsapp_template"),
                          "whatsapp_template_lang": plantilla.get("whatsapp_template_lang")},
            "destinatarios": destinatarios,
            "rechazados": rechazados,
            "estado": "pendiente",
            "job_id": None,
            "envio_id": envio_id,
            "creado": time.time(),
            "usuario": sesion.get("usuario", ""),
            "nombre_enviador": sesion.get("nombre", "Usuario"),
        }
        if envio_id:
            actualizar_envio_batch(envio_id, amb, invalidos=len(rechazados))
            for r in rechazados:
                registrar_historial(r.get("id"), r.get("nombre"), r.get("telefono"),
                                    plantilla["clave"], "", "numero_invalido",
                                    r.get("motivo"), ambiente=amb, envio_id=envio_id)
        nombre_sol = (sesion.get("nombre") or "").strip()
        correo_sol = (sesion.get("usuario") or "").strip()
        if nombre_sol and correo_sol and nombre_sol.lower() != correo_sol.lower():
            solicitante = f"{nombre_sol} ({correo_sol})"
        else:
            solicitante = nombre_sol or correo_sol or "usuario desconocido"
        _enviar_correo_confirmacion(token, len(destinatarios), plantilla["nombre"], plantilla["texto"],
                                    amb, plantilla["clave"], solicitante)
        return {"requiere_confirmacion": True, "solicitud_id": token, "total": len(destinatarios),
                "ambiente": amb, "rechazados": rechazados, "aviso_limite_mensajeria": aviso_limite,
                "confirm_url": f"{url_base()}/api/notificaciones/confirmar/{token}"}

    envio_id = crear_envio_batch(nombre_base(amb), plantilla["clave"], plantilla["nombre"],
                                 len(destinatarios) + len(rechazados), amb,
                                 usuario=sesion.get("usuario", ""))
    if envio_id:
        actualizar_envio_batch(envio_id, amb, invalidos=len(rechazados))
        for r in rechazados:
            registrar_historial(r.get("id"), r.get("nombre"), r.get("telefono"),
                                plantilla["clave"], "", "numero_invalido",
                                r.get("motivo"), ambiente=amb, envio_id=envio_id)

    job_id = uuid.uuid4().hex[:8]
    # Chequeo atómico: entre el chequeo rápido de arriba y acá pudo arrancar
    # otro envío EN ESTA MISMA BASE; bajo el lock se vuelve a verificar y se
    # reserva el job.
    with JOBS_LOCK:
        job_en_curso = _envio_en_curso(amb)
        if job_en_curso is not None:
            raise _error_envio_en_curso(job_en_curso)
        JOBS[job_id] = {
            "estado": "en_proceso",
            "total": len(destinatarios),
            "enviados": 0,
            "fallidos": 0,
            "actual": "",
            "errores": [],
            "detalle": "",
            "ambiente": amb,
            "plantilla": plantilla,
            "destinatarios": destinatarios,
            "envio_id": envio_id,
            "pausado": False,
            "usuario": sesion.get("usuario", ""),
            "nombre_enviador": sesion.get("nombre", "Usuario"),
        }

    background_tasks.add_task(procesar_job, job_id)
    return {"requiere_confirmacion": False, "iniciado": True, "job_id": job_id, "total": len(destinatarios),
            "ambiente": amb, "rechazados": rechazados, "aviso_limite_mensajeria": aviso_limite}


def estado_solicitud(token: str, sesion: dict = Depends(sesion_actual)):
    pend = PENDIENTES.get(token)
    if not pend:
        raise HTTPException(404, detail="Solicitud no encontrada")
    return {"solicitud_id": token, "estado": pend["estado"], "total": len(pend["destinatarios"]),
            "job_id": pend.get("job_id"), "ambiente": pend["ambiente"],
            "comentario": pend.get("comentario", "")}


def formulario_rechazo(token: str):
    pend = PENDIENTES.get(token)
    if not pend:
        return HTMLResponse("<html><body style='font-family:Arial; text-align:center; padding:40px;'><h3>Solicitud no encontrada o expirada</h3></body></html>", status_code=404)
    if pend["estado"] == "rechazado":
        return HTMLResponse("<html><body style='font-family:Arial; text-align:center; padding:40px;'><h2>Este envío ya fue rechazado</h2></body></html>")
    if pend["estado"] == "confirmado":
        return HTMLResponse("<html><body style='font-family:Arial; text-align:center; padding:40px;'><h2>Este envío ya fue confirmado y está en proceso</h2></body></html>")
    total = len(pend["destinatarios"])
    plantilla = _html.escape(pend["plantilla"]["nombre"])
    return HTMLResponse(f"""
<html><head><meta charset='utf-8'><title>Rechazar envío</title></head>
<body style='font-family: Segoe UI, Arial; text-align:center; padding:40px; background:#f0f2f5;'>
<div style='background:#fff; max-width:520px; margin:40px auto; padding:32px; border-radius:14px; box-shadow:0 4px 20px rgba(0,0,0,0.1); text-align:left;'>
<h2 style='color:#b23b37; margin-top:0;'>Rechazar envío</h2>
<p>Vas a rechazar el envío de <strong>{total} mensajes</strong> de la plantilla <strong>{plantilla}</strong>.</p>
<form method="POST" action="/api/notificaciones/rechazar/{token}">
  <label style="display:block; font-size:14px; color:#66757f; margin:14px 0 6px;">Comentario para el enviador (opcional, máximo 255 caracteres):</label>
  <textarea name="comentario" rows="4" maxlength="255" style="width:100%; padding:10px; font:inherit; font-size:14px; border:1px solid #dde1e6; border-radius:8px;" placeholder="Ej: falta adjuntar el consentimiento firmado."></textarea>
  <div style="margin-top:20px; text-align:right;">
    <button type="button" onclick="window.location.href='{url_base()}/api/notificaciones/confirmar/{token}'" style="background:#fafbfc; color:#24303c; border:1px solid #dde1e6; padding:11px 20px; border-radius:10px; font-weight:600; cursor:pointer; font-family:inherit;">Volver</button>
    <button type="submit" style="background:#b23b37; color:#fff; border:none; padding:11px 22px; border-radius:10px; font-weight:700; cursor:pointer; font-family:inherit;">Rechazar envío</button>
  </div>
</form>
</div>
</body></html>
""")


def rechazar_envio(token: str, comentario: str = Form("")):
    pend = PENDIENTES.get(token)
    if not pend:
        return HTMLResponse("<html><body style='font-family:Arial; text-align:center; padding:40px;'><h3>Solicitud no encontrada o expirada</h3></body></html>", status_code=404)
    if pend["estado"] == "rechazado":
        return HTMLResponse("<html><body style='font-family:Arial; text-align:center; padding:40px;'><h2>Este envío ya fue rechazado</h2></body></html>")
    if pend["estado"] == "confirmado":
        return HTMLResponse("<html><body style='font-family:Arial; text-align:center; padding:40px;'><h2>Este envío ya fue confirmado y está en proceso</h2></body></html>")
    comentario = (comentario or "").strip()
    if len(comentario) > 255:
        return HTMLResponse(
            "<html><body style='font-family:Arial; text-align:center; padding:40px;'>"
            "<h3>El comentario no puede superar los 255 caracteres.</h3>"
            "<p style='color:#66757f;'>Vuelve atrás e intenta de nuevo con un comentario más corto.</p>"
            "</body></html>", status_code=422)
    pend["estado"] = "rechazado"
    pend["comentario"] = comentario
    envio_id = pend.get("envio_id")
    if envio_id:
        amb = pend["ambiente"]
        # El batch NO se borra: queda en el historial como «rechazado» con el
        # comentario del supervisor y sin ningún mensaje enviado.
        try:
            with conectar(amb) as conn, conn.cursor() as cur:
                cur.execute(
                    "UPDATE envios SET estado = 'rechazado', comentario = %s WHERE id = %s",
                    (comentario[:255] or None, envio_id),
                )
                conn.commit()
        except Exception as e:
            log_error(f"rechazar_envio: no se pudo marcar el batch {envio_id}", e)
    return HTMLResponse("""
<html><head><meta charset='utf-8'><title>Envío rechazado</title></head>
<body style='font-family: Segoe UI, Arial; text-align:center; padding:40px; background:#f0f2f5;'>
<div style='background:#fff; max-width:520px; margin:40px auto; padding:32px; border-radius:14px; box-shadow:0 4px 20px rgba(0,0,0,0.1);'>
<h2 style='color:#b23b37; margin-top:0;'>Envío rechazado</h2>
<p>El envío fue rechazado y se notificó al usuario que lo solicitó.</p>
<p style='color:#66757f; font-size:14px;'>Puedes cerrar esta ventana.</p>
</div>
</body></html>
""")


def confirmar_envio(token: str, background_tasks: BackgroundTasks):
    pend = PENDIENTES.get(token)
    if not pend:
        return HTMLResponse("<html><body style='font-family:Arial; text-align:center; padding:40px;'><h3>Solicitud no encontrada o expirada</h3></body></html>", status_code=404)
    if pend["estado"] == "confirmado":
        return HTMLResponse(f"<html><body style='font-family:Arial; text-align:center; padding:40px;'><h2>Este envío ya fue confirmado</h2><p>Job: {pend.get('job_id')}</p></body></html>")
    if pend["estado"] == "rechazado":
        return HTMLResponse("<html><body style='font-family:Arial; text-align:center; padding:40px;'><h2>Este envío ya fue rechazado</h2></body></html>")
    pend["estado"] = "confirmado"
    job_id = uuid.uuid4().hex[:8]
    # Un solo envío a la vez POR BASE: si mientras la solicitud esperaba
    # confirmación arrancó otro envío en esa misma base (de la misma u otra
    # solicitud), se rechaza la confirmación y se muestra al supervisor quién y
    # qué está en curso. Una base libre no bloquea a la otra.
    with JOBS_LOCK:
        job_en_curso = _envio_en_curso(pend["ambiente"])
        if job_en_curso is not None:
            exc = _error_envio_en_curso(job_en_curso)
            pend["estado"] = "pendiente"
            detalle = _html.escape(exc.detail)
            return HTMLResponse(f"""
<html><head><meta charset='utf-8'><title>Envío no confirmado</title></head>
<body style='font-family: Segoe UI, Arial; text-align:center; padding:40px; background:#f0f2f5;'>
<div style='background:#fff; max-width:520px; margin:40px auto; padding:32px; border-radius:14px; box-shadow:0 4px 20px rgba(0,0,0,0.1);'>
<h2 style='color:#b23b37; margin-top:0;'>Ya hay un envío en curso</h2>
<p>{detalle}</p>
<p style='color:#66757f; font-size:14px;'>Puedes cerrar esta ventana e intentar confirmarlo de nuevo cuando el envío termine.</p>
</div>
</body></html>
""", status_code=409)
        pend["job_id"] = job_id
        JOBS[job_id] = {
            "estado": "en_proceso",
            "total": len(pend["destinatarios"]),
            "enviados": 0,
            "fallidos": 0,
            "actual": "",
            "errores": [],
            "detalle": "",
            "ambiente": pend["ambiente"],
            "plantilla": pend["plantilla"],
            "destinatarios": pend["destinatarios"],
            "envio_id": pend.get("envio_id"),
            "pausado": False,
            "usuario": pend.get("usuario", ""),
            "nombre_enviador": pend.get("nombre_enviador", "Usuario"),
        }
    background_tasks.add_task(procesar_job, job_id)
    return HTMLResponse(f"""
<html><head><meta charset='utf-8'><title>Envío confirmado</title></head>
<body style='font-family: Segoe UI, Arial; text-align:center; padding:40px; background:#f0f2f5;'>
<div style='background:#fff; max-width:520px; margin:40px auto; padding:32px; border-radius:14px; box-shadow:0 4px 20px rgba(0,0,0,0.1);'>
<h2 style='color:#128c7e; margin-top:0;'>&#10003; Envío confirmado</h2>
<p>Se iniciará el envío de <strong>{len(pend['destinatarios'])} mensajes</strong><br>plantilla <strong>{pend['plantilla']['nombre']}</strong>.</p>
<p style='color:#66757f; font-size:14px;'>Puedes cerrar esta ventana. El sistema continuará automáticamente.</p>
</div>
</body></html>
""")


class DestinosIn(BaseModel):
    ambiente: str = "produccion"
    plantilla_id: int | None = None


def contar_destinatarios(body: DestinosIn, sesion: dict = Depends(exigir("mensajeria"))):
    try:
        amb = entorno_valido(body.ambiente)
    except ValueError:
        raise HTTPException(400, detail=f"Entorno inválido: '{body.ambiente}'")

    t = tabla_pacientes(amb)
    with conectar(amb) as conn, conn.cursor() as cur:
        if columna_existe(t, "estado", amb):
            cur.execute(
                f"SELECT COUNT(*) AS total,"
                f" SUM(estado = 'pendiente') AS pendientes"
                f" FROM {t}"
            )
        else:
            cur.execute(
                f"SELECT COUNT(*) AS total, COUNT(*) AS pendientes FROM {t}"
            )
        fila = cur.fetchone()

    total = int(fila["total"] or 0)
    # En desarrollo se envía sin importar el estado, así que "elegibles" = todos los pacientes.
    # En producción se respeta el filtro de solo pendientes.
    elegibles = total if amb == "desarrollo" else int(fila["pendientes"] or 0)

    costo = None
    if body.plantilla_id is not None and tiene_permiso(sesion, "tarifas_editar"):
        plantilla = next((p for p in leer_plantillas() if p["id"] == body.plantilla_id), None)
        if plantilla is not None:
            costo = _costo_estimado_por_clave(plantilla.get("clave", ""), elegibles)

    return {
        "total": total,
        "pendientes": elegibles,
        "base_datos": nombre_base(amb),
        "costo": costo,
        "limite_mensajeria": _limite_mensajeria_aviso(amb),
    }


def estado_job(job_id: str, sesion: dict = Depends(exigir("mensajeria"))):
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, detail="Envío no encontrado")

    return {k: v for k, v in job.items() if k != "destinatarios"}


def cancelar_job(job_id: str, sesion: dict = Depends(exigir("mensajeria"))):
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, detail="Envío no encontrado")
    if job.get("estado") in ("completado", "cancelado", "error"):
        return {"ok": False, "estado": job.get("estado"), "detail": "El envío ya finalizó"}
    job["cancelado"] = True
    return {"ok": True, "estado": "cancelado"}


def pausar_job(job_id: str, sesion: dict = Depends(exigir("mensajeria"))):
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, detail="Envío no encontrado")
    if job.get("estado") in ("completado", "cancelado", "error"):
        return {"ok": False, "estado": job.get("estado"), "detail": "El envío ya finalizó"}
    job["pausado"] = True
    return {"ok": True, "estado": "pausado"}


def reanudar_job(job_id: str, sesion: dict = Depends(exigir("mensajeria"))):
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, detail="Envío no encontrado")
    if job.get("estado") in ("completado", "cancelado", "error"):
        return {"ok": False, "estado": job.get("estado"), "detail": "El envío ya finalizó"}
    job["pausado"] = False
    return {"ok": True, "estado": "en_proceso"}


def listar_historial(q: str | None = Query(None), estado: str | None = Query(None),
                     ambiente: str = Query("produccion"), sesion: dict = Depends(exigir("historial"))):
    com_col = "comentario" if "comentario" in columnas_tabla("envios", "produccion") else "NULL AS comentario"
    sql = ("SELECT id, base_datos, plantilla_clave, plantilla_nombre, total_pacientes,"
           f" enviados, fallidos, invalidos, estado, {com_col}, fecha_hora FROM envios")
    condiciones: list[str] = []
    args: list = []

    # envios es una única tabla; 'base_datos' guarda 'pacientes_dev' o 'pacientes_prod'.
    if ambiente != "todos":
        condiciones.append("base_datos = %s")
        args.append(nombre_base(ambiente))

    if q and q.strip():
        like = f"%{q.strip()}%"
        condiciones.append(
            "(base_datos LIKE %s OR plantilla_clave LIKE %s OR plantilla_nombre LIKE %s)"
        )
        args += [like, like, like]

    if condiciones:
        sql += " WHERE " + " AND ".join(condiciones)
    sql += " ORDER BY id DESC LIMIT 300"

    with conectar(ambiente) as conn, conn.cursor() as cur:
        cur.execute(sql, tuple(args) or None)
        filas = cur.fetchall()

    for f in filas:
        f["fecha"] = f.pop("fecha_hora").strftime("%d-%m-%Y %H:%M")
    return filas


def detalle_historial(envio_id: int, ambiente: str = Query("produccion"),
                      sesion: dict = Depends(exigir("historial"))):
    # log_envios es única para todo el sistema; el detalle se busca por envio_id.
    # Para 'respuesta' se usa la señal EFECTIVA actual del paciente (respondió /
    # se dio de baja / sin respuesta), no el valor congelado en la fila de envío.
    t = tabla_pacientes(ambiente)
    tiene_opt = columna_existe(t, "whatsapp_opt_out", ambiente)
    tiene_int = columna_existe(t, "interesado", ambiente)
    re_expr = expr_respuesta_efectiva("pac", tiene_opt, columna_existe(t, "respuesta_manual", ambiente))
    int_expr = "COALESCE(pac.interesado, 0)" if tiene_int else "0"
    sql = (
        "SELECT le.id, le.paciente_id, le.nombre_paciente, le.numero_telefono, le.estado_envio,"
        " le.descripcion_error, le.fecha_hora,"
        "  (SELECT r.mensaje FROM log_envios r"
        "     WHERE r.paciente_id = le.paciente_id AND r.plantilla_clave = 'respuesta'"
        "       AND r.fecha_hora >= le.fecha_hora"
        "     ORDER BY r.id DESC LIMIT 1) AS mensaje_respuesta,"
        f" {int_expr} AS interesado,"
        f" (CASE WHEN pac.id IS NOT NULL THEN {re_expr}"
        "        ELSE COALESCE(le.respuesta, 'pendiente') END) AS respuesta"
        f" FROM log_envios le LEFT JOIN {t} pac ON pac.id = le.paciente_id"
        " WHERE le.envio_id = %s ORDER BY le.id"
    )
    with conectar(ambiente) as conn, conn.cursor() as cur:
        cur.execute(sql, (envio_id,))
        filas = cur.fetchall()
    privilegiado = sesion.get("rol") in ROLES_PRIVILEGIADOS
    for f in filas:
        f["fecha"] = f.pop("fecha_hora").strftime("%d-%m-%Y %H:%M")
        f["interesado"] = bool(f.get("interesado"))
        msg = (f.get("mensaje_respuesta") or "").strip()
        f["mensaje_respuesta"] = msg
        f["respuesta_interes"] = bool(msg) and es_mensaje_interes(msg)
        if not privilegiado:
            # El número de teléfono del paciente es solo para admin/dev.
            f["numero_telefono"] = None
    return filas


def actualizar_respuesta(registro_id: int, body: EstadoPacienteIn,
                         ambiente: str = Query("produccion"),
                         sesion: dict = Depends(exigir("historial"))):
    if body.estado not in ("pendiente", "respondio", "baja"):
        raise HTTPException(400, detail="Respuesta inválida. Use: pendiente, respondio, baja")
    with conectar(ambiente) as conn, conn.cursor() as cur:
        cur.execute("UPDATE log_envios SET respuesta = %s WHERE id = %s", (body.estado, registro_id))
        conn.commit()
    return {"ok": True}


def _pacientes_por_respuesta(ambiente: str) -> dict:
    """Cuántos pacientes hay en cada estado de respuesta de WhatsApp
    (pendiente / respondió / baja), usando la señal 'pegajosa'.

    Solo se cuentan los pacientes a los que YA se les envió un mensaje
    (estado = 'enviado'); los pendientes y los que fallaron quedan fuera."""
    t = tabla_pacientes(ambiente)
    tiene_opt = columna_existe(t, "whatsapp_opt_out", ambiente)
    re_expr = expr_respuesta_efectiva("p", tiene_opt, columna_existe(t, "respuesta_manual", ambiente))
    where = " WHERE p.estado = 'enviado'" if columna_existe(t, "estado", ambiente) else ""
    base = {"pendiente": 0, "respondio": 0, "baja": 0}
    try:
        with conectar(ambiente) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT {re_expr} AS r, COUNT(*) AS n FROM {t} p{where} GROUP BY r")
            for row in cur.fetchall():
                if row["r"] in base:
                    base[row["r"]] = int(row["n"] or 0)
    except Exception as e:
        log_error(f"_pacientes_por_respuesta({ambiente})", e)
    base["total"] = sum(base.values())
    return base


# En las estadísticas SOLO cuentan los envíos de producción, no los de
# desarrollo/pruebas. Cada fila de log_envios se ata a su lote (envios.base_datos).
_SOLO_PROD = "envio_id IN (SELECT id FROM envios WHERE base_datos = 'pacientes_prod')"


def estadisticas(sesion: dict = Depends(exigir("estadisticas"))):
    """Resumen de envíos para la página de Estadísticas (solo producción)."""
    with conectar() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT"
            "  SUM(estado_envio = 'enviado') AS enviados,"
            "  SUM(estado_envio = 'error') AS fallidos,"
            "  SUM(estado_envio = 'numero_invalido') AS invalidos,"
            "  SUM(respuesta = 'respondio') AS respondio,"
            "  SUM(respuesta = 'baja') AS baja"
            " FROM log_envios"
            " WHERE fecha_hora >= DATE_FORMAT(CURDATE(), '%Y-%m-01')"
            f"   AND {_SOLO_PROD}"
        )
        mes = cur.fetchone() or {}

        cur.execute(f"SELECT COUNT(*) AS n FROM log_envios WHERE estado_envio = 'enviado' AND {_SOLO_PROD}")
        total_enviados = int((cur.fetchone() or {}).get("n", 0))
        cur.execute("SELECT COUNT(*) AS n FROM envios WHERE base_datos = 'pacientes_prod'")
        total_batches = int((cur.fetchone() or {}).get("n", 0))

        # Salud del webhook: cuándo llegó el último evento de Meta.
        cur.execute("SELECT COUNT(*) AS n, MAX(recibido) AS ult FROM whatsapp_eventos")
        w = cur.fetchone() or {}
        ult = w.get("ult")
        webhook = {
            "total_eventos": int(w.get("n") or 0),
            "ultimo_evento": ult.strftime("%d-%m-%Y %H:%M") if ult else None,
            "hace_horas": round((time.time() - ult.timestamp()) / 3600, 1) if ult else None,
        }

    enviados_mes = int(mes.get("enviados") or 0)
    return {
        "mes": time.strftime("%Y-%m"),
        "enviados_mes": enviados_mes,
        "fallidos_mes": int(mes.get("fallidos") or 0),
        "invalidos_mes": int(mes.get("invalidos") or 0),
        "respondio_mes": int(mes.get("respondio") or 0),
        "baja_mes": int(mes.get("baja") or 0),
        "total_enviados_historico": total_enviados,
        "total_batches": total_batches,
        "pacientes_por_respuesta": _pacientes_por_respuesta("produccion"),
        "webhook": webhook,
    }


def estadisticas_envios(granularidad: str = Query("mes"), sesion: dict = Depends(exigir("estadisticas"))):
    """Mensajes enviados agrupados por periodo (para el gráfico de barras)."""
    if granularidad not in ("dia", "mes", "anio"):
        raise HTTPException(400, detail="granularidad debe ser dia, mes o anio")
    # (formato de DATE_FORMAT, ventana hacia atrás)
    # Sin parámetros en execute(): PyMySQL no pasa por mogrify, así que '%' va simple.
    cfg = {
        "dia": ("%Y-%m-%d", "CURDATE() - INTERVAL 29 DAY"),
        "mes": ("%Y-%m", "DATE_FORMAT(CURDATE() - INTERVAL 11 MONTH, '%Y-%m-01')"),
        "anio": ("%Y", "DATE_FORMAT(CURDATE() - INTERVAL 5 YEAR, '%Y-01-01')"),
    }[granularidad]
    fmt, desde = cfg
    with conectar() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT DATE_FORMAT(fecha_hora, '{fmt}') AS periodo, COUNT(*) AS enviados"
            " FROM log_envios"
            " WHERE estado_envio = 'enviado'"
            f"   AND fecha_hora >= {desde}"
            f"   AND {_SOLO_PROD}"
            " GROUP BY periodo ORDER BY periodo"
        )
        filas = [{"periodo": r["periodo"], "enviados": int(r["enviados"] or 0)}
                 for r in cur.fetchall()]
    return {
        "granularidad": granularidad,
        "filas": filas,
        "total": sum(f["enviados"] for f in filas),
    }


# ===========================================================================
#  Tarifas de WhatsApp (rate card de Meta) y costos de los envíos  — SOLO ADMIN
# ===========================================================================

PRICING_PAGE = "https://developers.facebook.com/docs/whatsapp/pricing/"
_MESES_EN = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], 1)}
_CATS = ("marketing", "utility", "authentication", "service")


def _parse_fecha_efectiva(txt: str) -> str | None:
    m = re.search(r"effective\s+([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})", txt or "", re.I)
    if not m:
        return None
    mes = _MESES_EN.get(m.group(1).lower())
    if not mes:
        return None
    try:
        return f"{int(m.group(3)):04d}-{mes:02d}-{int(m.group(2)):02d}"
    except ValueError:
        return None


def _extraer_chile_csv(texto: str) -> dict | None:
    """Del CSV de un rate card de Meta saca la fila de Chile (tarifas por
    categoría, en USD) y la fecha efectiva."""
    filas = list(_csv.reader(_io.StringIO(texto)))
    if not filas:
        return None
    efectiva = _parse_fecha_efectiva(filas[0][0] if filas[0] else "")

    idx_h = None
    for i, f in enumerate(filas):
        if f and f[0].strip().lower() in ("market", "country") and "marketing" in ",".join(f).lower():
            idx_h = i
            break
    if idx_h is None:
        return None
    header = [c.strip().lower() for c in filas[idx_h]]

    def col(nombre):
        return next((j for j, h in enumerate(header) if nombre in h), None)

    js = {c: col(c) for c in _CATS}
    for f in filas[idx_h + 1:]:
        if not f or f[0].strip().lower() != "chile":
            continue

        def val(j):
            if j is None or j >= len(f):
                return None
            v = (f[j] or "").strip().lower()
            if v in ("", "n/a", "na", "-", "free"):
                return 0.0 if v == "free" else None
            try:
                return float(v)
            except ValueError:
                return None

        rates = {c: val(js[c]) for c in _CATS}
        return {
            "moneda": (f[1].strip() if len(f) > 1 else "USD") or "USD",
            "efectiva_desde": efectiva,
            **rates,
        }
    return None


async def _bajar_csvs(urls: list[str]) -> list[str]:
    sem = asyncio.Semaphore(16)
    out: list[str] = []
    async with httpx.AsyncClient(timeout=15, follow_redirects=True,
                                 headers={"User-Agent": "Mozilla/5.0"}) as c:
        async def uno(u):
            async with sem:
                try:
                    r = await c.get(u)
                    if r.status_code == 200 and "chile" in r.text.lower():
                        out.append(r.text)
                except Exception:
                    pass
        await asyncio.gather(*[uno(u) for u in urls])
    return out


def _fetch_tarifas_meta() -> list[dict]:
    """Descarga la página de precios de Meta, baja sus CSV de rate card y
    devuelve las tarifas de Chile encontradas (una por rate card distinto)."""
    with httpx.Client(timeout=25, follow_redirects=True,
                      headers={"User-Agent": "Mozilla/5.0"}) as c:
        pg = c.get(PRICING_PAGE).text

    reales, vistos = [], set()
    for L in re.findall(r'href="(https://l\.facebook\.com/l\.php\?u=[^"]+\.csv[^"]*)"', pg):
        m = re.search(r'[?&]u=([^&]+)', _html.unescape(L))
        if not m:
            continue
        u = urllib.parse.unquote(m.group(1))
        if u.split("?")[0] in vistos:
            continue
        vistos.add(u.split("?")[0])
        reales.append(u)
    if not reales:
        return []

    textos = asyncio.run(_bajar_csvs(reales))
    resultados, hashes = [], set()
    for txt in textos:
        ch = _extraer_chile_csv(txt)
        if not ch or not any(ch.get(c) for c in ("marketing", "utility", "authentication")):
            continue
        h = hashlib.sha256(
            "|".join(f"{ch.get(c)}" for c in _CATS).encode("utf-8")
        ).hexdigest()
        if h in hashes:
            continue
        hashes.add(h)
        resultados.append({**ch, "hash": h, "csv_texto": txt})
    return resultados


def _moneda_cuenta() -> str:
    """Moneda de facturación de la cuenta de Meta (CLP, USD, ...)."""
    return (config_get("wa_moneda", "USD") or "USD").strip().upper() or "USD"


def _tarifas_guardadas(moneda: str | None = None) -> list[dict]:
    # Los '%' de DATE_FORMAT se escapan como '%%' porque PyMySQL siempre pasa
    # la consulta por mogrify cuando se le entregan parámetros.
    sql = (
        "SELECT id, pais, moneda, marketing, utility, authentication, service,"
        " DATE_FORMAT(efectiva_desde, '%%Y-%%m-%%d') AS efectiva_desde,"
        " DATE_FORMAT(descargada, '%%d-%%m-%%Y %%H:%%i') AS descargada,"
        " UNIX_TIMESTAMP(descargada) * 1000 AS descargada_ms"
        " FROM tarifas_whatsapp"
    )
    args: list = []
    if moneda:
        sql += " WHERE moneda = %s"
        args = [moneda]
    sql += " ORDER BY (efectiva_desde IS NULL), efectiva_desde DESC, id DESC"
    with conectar() as conn, conn.cursor() as cur:
        cur.execute(sql, args)
        filas = cur.fetchall()
    for f in filas:
        for c in _CATS:
            f[c] = float(f[c]) if f[c] is not None else None
    return filas


def _tarifa_para_fecha(tarifas: list[dict], fecha_iso: str | None) -> dict | None:
    """Tarifa vigente en una fecha 'YYYY-MM-...' (o la más reciente si no hay match)."""
    if not tarifas:
        return None
    conf = [t for t in tarifas if t.get("efectiva_desde")]
    if fecha_iso:
        aplicables = [t for t in conf if t["efectiva_desde"] <= fecha_iso]
        if aplicables:
            return max(aplicables, key=lambda t: t["efectiva_desde"])
    if conf:
        return min(conf, key=lambda t: t["efectiva_desde"])
    return tarifas[0]


def _tarifa_vigente(tarifas: list[dict]) -> dict | None:
    return _tarifa_para_fecha(tarifas, time.strftime("%Y-%m-%d"))


def _tarifa_proxima(tarifas: list[dict]) -> dict | None:
    hoy = time.strftime("%Y-%m-%d")
    futuras = [t for t in tarifas if t.get("efectiva_desde") and t["efectiva_desde"] > hoy]
    return min(futuras, key=lambda t: t["efectiva_desde"]) if futuras else None


def _obtener_moneda_meta() -> str | None:
    """Consulta a Meta la moneda de facturación de la WABA (ej. CLP, USD)."""
    from whatsapp_service import graph_url
    s = WhatsAppService()
    if not s.waba_id or not s.token:
        return None
    try:
        with httpx.Client(timeout=15) as c:
            r = c.get(f"{graph_url()}/{s.waba_id}",
                      params={"fields": "currency"},
                      headers={"Authorization": f"Bearer {s.token}"})
        if r.status_code == 200:
            return (r.json().get("currency") or "").strip().upper() or None
    except Exception as e:
        log_error("_obtener_moneda_meta", e)
    return None


def obtener_tarifas(sesion: dict = Depends(exigir("tarifas_editar"))):
    moneda = _moneda_cuenta()
    tarifas = _tarifas_guardadas(moneda) or _tarifas_guardadas("USD")
    todas = _tarifas_guardadas()
    return {
        "moneda": moneda,
        "vigente": _tarifa_vigente(tarifas),
        "proxima": _tarifa_proxima(tarifas),
        "usd_vigente": _tarifa_vigente(_tarifas_guardadas("USD")),
        "historial": tarifas,
        "ultima_descarga": todas[0]["descargada"] if todas else None,
        "ultima_descarga_ms": todas[0]["descargada_ms"] if todas else None,
        # Para el cooldown de 24h del botón: cuándo se INTENTÓ la descarga
        # por última vez (se haya guardado un dato nuevo o no) — a
        # diferencia de ultima_descarga_ms, que es de la última fila NUEVA.
        "ultimo_intento_ms": int(config_get("tarifas_ultimo_intento", "0") or 0) or None,
        "nunca_descargada": not todas,
    }


def actualizar_tarifas(sesion: dict = Depends(exigir("tarifas_editar"))):
    try:
        encontradas = _fetch_tarifas_meta()
    except Exception as e:
        log_error("actualizar_tarifas: no se pudo descargar de Meta", e)
        raise HTTPException(502, detail=f"No se pudo descargar la página de precios de Meta: {e}")
    if not encontradas:
        raise HTTPException(502, detail="No se encontró ningún rate card con la fila de Chile en la página de Meta.")

    moneda_meta = _obtener_moneda_meta()
    if moneda_meta:
        config_set({"wa_moneda": moneda_meta})

    # Se guarda el momento del INTENTO (se haya insertado una fila nueva o
    # no), no cuándo cambió el dato: la tarifa de Meta rara vez cambia, así
    # que con INSERT IGNORE casi siempre "nuevas" da 0 y la fila existente
    # no actualiza su propio "descargada" — si el cooldown de 24h del botón
    # se basara en eso, nunca se activaría aunque la descarga en sí (la
    # scrapeada a la página de Meta) se repita en cada clic.
    config_set({"tarifas_ultimo_intento": str(int(time.time() * 1000))})

    nuevas = 0
    with conectar() as conn, conn.cursor() as cur:
        for t in encontradas:
            cur.execute(
                "INSERT IGNORE INTO tarifas_whatsapp"
                " (pais, moneda, marketing, utility, authentication, service, efectiva_desde, hash, fuente, csv_texto)"
                " VALUES ('Chile', %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (t["moneda"], t.get("marketing"), t.get("utility"), t.get("authentication"),
                 t.get("service"), t.get("efectiva_desde"), t["hash"], PRICING_PAGE, t["csv_texto"]),
            )
            nuevas += cur.rowcount
        conn.commit()

    moneda = _moneda_cuenta()
    tarifas = _tarifas_guardadas(moneda) or _tarifas_guardadas("USD")
    return {
        "ok": True,
        "moneda": moneda,
        "encontradas": len(encontradas),
        "nuevas": nuevas,
        "cambio": nuevas > 0,
        "vigente": _tarifa_vigente(tarifas),
        "proxima": _tarifa_proxima(tarifas),
    }


def descargar_tarifa_csv(sesion: dict = Depends(exigir("tarifas_editar"))):
    moneda = _moneda_cuenta()
    with conectar() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT csv_texto FROM tarifas_whatsapp WHERE moneda IN (%s, 'USD')"
            " ORDER BY (moneda <> %s), (efectiva_desde IS NULL), efectiva_desde DESC, id DESC LIMIT 1",
            (moneda, moneda),
        )
        fila = cur.fetchone()
    if not fila or not fila.get("csv_texto"):
        raise HTTPException(404, detail="Todavía no se ha descargado ningún rate card.")
    return PlainTextResponse(
        fila["csv_texto"], media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="whatsapp_tarifas_chile.csv"'},
    )


_CATS_FACTURABLES = ("marketing", "utility", "authentication")


def _categorias_por_clave() -> dict:
    """clave de plantilla -> categoría facturable de Meta.

    SOLO incluye plantillas que se envían como *template* aprobado (tienen un
    `whatsapp_template` configurado) y con categoría facturable. Los mensajes de
    texto libre —las respuestas dentro de la ventana de 24 h de atención al
    cliente— son gratuitos desde nov-2024, así que no entran en el costo.
    """
    m = {}
    for p in leer_plantillas():
        cat = (p.get("whatsapp_template_categoria") or "").strip().lower()
        tiene_template = bool((p.get("whatsapp_template") or "").strip())
        if p.get("clave") and tiene_template and cat in _CATS_FACTURABLES:
            m[p["clave"]] = cat
    return m


def estadisticas_costos(granularidad: str = Query("mes"), sesion: dict = Depends(exigir("tarifas_editar"))):
    if granularidad not in ("dia", "mes", "anio"):
        raise HTTPException(400, detail="granularidad debe ser dia, mes o anio")
    fmt = {"dia": "%Y-%m-%d", "mes": "%Y-%m", "anio": "%Y"}[granularidad]

    moneda = _moneda_cuenta()
    tarifas = _tarifas_guardadas(moneda) or _tarifas_guardadas("USD")
    cats_clave = _categorias_por_clave()

    with conectar() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT DATE_FORMAT(fecha_hora, '{fmt}') AS periodo, plantilla_clave AS clave, COUNT(*) AS n"
            " FROM log_envios"
            " WHERE estado_envio = 'enviado'"
            "   AND plantilla_clave NOT IN ('respuesta', 'ajuste_manual')"
            f"   AND {_SOLO_PROD}"
            " GROUP BY periodo, plantilla_clave ORDER BY periodo"
        )
        crudo = cur.fetchall()

    periodos: dict[str, dict] = {}
    tot = {"mensajes": 0, "costo": 0.0, "por_categoria": {c: 0 for c in _CATS}}
    excluidos = 0  # mensajes de texto libre / ventana 24 h (no facturables)
    for r in crudo:
        per = r["periodo"]
        n = int(r["n"] or 0)
        cat = cats_clave.get(r["clave"])
        if cat is None:
            # Envío que no salió como template facturable: no se cobra.
            excluidos += n
            periodos.setdefault(per, {
                "periodo": per, "mensajes": 0, "costo": 0.0, "excluidos": 0,
                "por_categoria": {c: 0 for c in _CATS},
            })["excluidos"] += n
            continue
        tarifa = _tarifa_para_fecha(tarifas, per if len(per) >= 7 else per + "-12")
        rate = (tarifa.get(cat) if tarifa else None) or 0.0
        costo = n * rate

        p = periodos.setdefault(per, {
            "periodo": per, "mensajes": 0, "costo": 0.0, "excluidos": 0,
            "por_categoria": {c: 0 for c in _CATS},
        })
        p["mensajes"] += n
        p["costo"] = round(p["costo"] + costo, 4)
        p["por_categoria"][cat] += n
        tot["mensajes"] += n
        tot["costo"] = round(tot["costo"] + costo, 4)
        tot["por_categoria"][cat] += n

    vig = _tarifa_vigente(tarifas)
    return {
        "granularidad": granularidad,
        "moneda": (vig or {}).get("moneda") or moneda,
        "tarifa_vigente": vig,
        "sin_tarifas": not tarifas,
        "filas": [periodos[k] for k in sorted(periodos)],
        "total": {"mensajes": tot["mensajes"], "costo": round(tot["costo"], 4),
                  "excluidos": excluidos, "por_categoria": tot["por_categoria"]},
    }


# Cada módulo mantiene el contrato HTTP de su dominio. `main.py` conserva los
# manejadores mientras su lógica de negocio se migra paulatinamente a servicios.
for registrar in (
    auth.registrar, usuarios.registrar, pacientes.registrar, plantillas.registrar,
    configuracion.registrar, notificaciones.registrar, rutas_estadisticas.registrar,
):
    app.include_router(registrar(globals()))

app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="localhost", port=8000)
