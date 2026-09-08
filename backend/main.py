import asyncio
import csv as _csv
import hashlib
import math
import html as _html
import io as _io
import json
import re
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
)
from motor_envio import obtener_canal
from whatsapp_service import WhatsAppService
from whatsapp_webhook import router as whatsapp_router

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_FILE = BASE_DIR / "data" / "plantillas.json"
FRONTEND_DIR = BASE_DIR / "frontend"


def normalizar_telefono(crudo: str) -> str | None:
    """Corrige variantes comunes de números móviles chilenos al formato +569XXXXXXXX."""
    limpio = re.sub(r"[^\d+]", "", crudo.strip())
    digitos = re.sub(r"\D", "", limpio)

    if limpio.startswith("+"):
        if len(digitos) == 11 and digitos.startswith("569"):
            return "+" + digitos
        if len(digitos) == 10 and digitos.startswith("56"):
            return "+569" + digitos[2:]
        if len(digitos) == 9 and digitos.startswith("9"):
            return "+56" + digitos
        return None

    if len(digitos) == 11 and digitos.startswith("569"):
        return "+" + digitos
    if len(digitos) == 10 and digitos.startswith("56"):
        return "+569" + digitos[2:]
    if len(digitos) == 9 and digitos.startswith("9"):
        return "+56" + digitos
    if len(digitos) == 8 and digitos.startswith("9"):
        return "+569" + digitos
    return None

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


class PlantillaIn(BaseModel):
    nombre: str
    texto: str
    clave: str | None = None
    whatsapp_template: str | None = None
    whatsapp_template_lang: str | None = None
    whatsapp_template_categoria: str | None = None


class EnvioIn(BaseModel):
    pacientes: list[int] | None = None
    plantilla_id: int
    ambiente: str | None = None
    limite: int | None = None  # solo producción: cuántos enviar de los pendientes


class ConfigIn(BaseModel):
    entorno: str | None = None
    metodo_envio: str | None = None
    numeros_prueba_dev: list[str] | None = None
    numeros_prueba_prod: list[str] | None = None
    intervalo_ms: int | None = None
    url_base: str | None = None
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_user: str | None = None
    smtp_pass: str | None = None
    smtp_tls: bool | None = None
    correo_emisor: str | None = None
    correo_destino: str | None = None
    wa_token: str | None = None
    wa_phone_id: str | None = None
    wa_business_account_id: str | None = None
    wa_verify_token: str | None = None
    wa_template_nombre: str | None = None
    wa_template_lang: str | None = None
    wa_webhook_path: str | None = None
    wa_graph_version: str | None = None


class LoginIn(BaseModel):
    usuario: str
    clave: str


USUARIOS_FILE = BASE_DIR / "data" / "usuarios.json"
SESIONES_FILE = BASE_DIR / "data" / "sesiones.json"


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


def cargar_usuarios() -> list:
    try:
        datos = json.loads(USUARIOS_FILE.read_text(encoding="utf-8"))
        return datos if isinstance(datos, list) else []
    except FileNotFoundError:
        return []
    except Exception as e:
        log_error(f"cargar_usuarios: {USUARIOS_FILE.name} ilegible", e)
        return []


def sesion_actual(request: Request) -> dict:
    authz = request.headers.get("Authorization", "")
    token = authz[7:] if authz.startswith("Bearer ") else ""
    sesion = SESIONES.get(token)
    if not sesion:
        raise HTTPException(401, detail="Sesión no válida. Inicia sesión nuevamente.")
    return sesion


def solo_admin(sesion: dict = Depends(sesion_actual)) -> dict:
    if sesion.get("rol") != "administrador":
        raise HTTPException(403, detail="Esta sección requiere rol administrador")
    return sesion


@app.post("/api/auth/login")
def login(body: LoginIn):
    usuarios = cargar_usuarios()
    clave_hash = hashlib.sha256(body.clave.encode("utf-8")).hexdigest()
    usuario = next(
        (
            u
            for u in usuarios
            if str(u.get("usuario", "")).lower() == body.usuario.strip().lower()
            and u.get("clave_hash") == clave_hash
        ),
        None,
    )
    if usuario is None:
        raise HTTPException(401, detail="Usuario o contraseña incorrectos")

    token = uuid.uuid4().hex
    SESIONES[token] = {
        "rol": usuario.get("rol", "usuario"),
        "nombre": usuario.get("nombre", body.usuario),
    }
    guardar_sesiones()
    return {"token": token, "rol": SESIONES[token]["rol"], "nombre": SESIONES[token]["nombre"]}


@app.post("/api/auth/logout")
def logout(request: Request):
    authz = request.headers.get("Authorization", "")
    token = authz[7:] if authz.startswith("Bearer ") else ""
    SESIONES.pop(token, None)
    guardar_sesiones()
    return {"ok": True}


def leer_plantillas() -> list:
    try:
        datos = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        return datos if isinstance(datos, list) else []
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


def expr_select_pacientes(ambiente: str) -> str:
    """Genera las expresiones SELECT de la tabla pacientes adaptándose a las
    columnas reales existentes (soporta bases con esquema mínimo)."""
    cols = columnas_tabla(tabla_pacientes(ambiente), ambiente)
    exprs = ["p.id", "p.nombre", "p.apellido", "p.telefono"]
    if "estado" in cols:
        exprs.append("COALESCE(NULLIF(p.estado, ''), 'pendiente') AS estado")
    else:
        exprs.append("'pendiente' AS estado")
    if "info_extra" in cols:
        exprs.append("COALESCE(p.info_extra, '') AS info_extra")
    else:
        exprs.append("'' AS info_extra")
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


@app.get("/api/pacientes")
def listar_pacientes(q: str | None = Query(None), ambiente: str = Query("produccion"),
                     sesion: dict = Depends(solo_admin)):
    sql = "SELECT " + expr_select_pacientes(ambiente) + from_pacientes(ambiente)
    args: list = []
    if q and q.strip():
        like = f"%{q.strip()}%"
        if columna_existe(tabla_pacientes(ambiente), "info_extra", ambiente):
            sql += " WHERE p.nombre LIKE %s OR p.telefono LIKE %s OR p.info_extra LIKE %s"
            args = [like, like, like]
        else:
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


@app.put("/api/pacientes/{paciente_id}")
def actualizar_paciente(paciente_id: int, body: EstadoPacienteIn,
                        ambiente: str = Query("produccion"),
                        sesion: dict = Depends(solo_admin)):
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


@app.put("/api/pacientes/{paciente_id}/respuesta")
def actualizar_respuesta_paciente(paciente_id: int, body: RespuestaIn,
                                  ambiente: str = Query("produccion"),
                                  sesion: dict = Depends(solo_admin)):
    """Ajuste manual de la respuesta de un paciente (fallback si el webhook no
    llegó, o si el paciente avisó por otro canal). 'baja' activa el opt-out."""
    if body.respuesta not in ("pendiente", "respondio", "baja"):
        raise HTTPException(400, detail="Respuesta inválida. Use: pendiente, respondio, baja")
    t = tabla_pacientes(ambiente)
    tiene_manual = columna_existe(t, "respuesta_manual", ambiente)
    with conectar(ambiente) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT id FROM {t} WHERE id = %s", (paciente_id,))
        if not cur.fetchone():
            raise HTTPException(404, detail="Paciente no encontrado")

        cur.execute(
            f"UPDATE {t} SET whatsapp_opt_out = %s WHERE id = %s",
            (1 if body.respuesta == "baja" else 0, paciente_id),
        )
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
    categoria = (p.get("whatsapp_template_categoria") or "").strip() or "UTILITY"

    p["whatsapp_template"] = nombre_template
    p["whatsapp_template_lang"] = lang
    p["whatsapp_template_categoria"] = categoria

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
    return p


@app.get("/api/plantillas")
def listar_plantillas(sesion: dict = Depends(sesion_actual)):
    return sorted(leer_plantillas(), key=lambda p: p.get("actualizada", 0), reverse=True)


def _actualizar_estado_meta(p: dict) -> dict:
    """Consulta el estado real en Meta de una plantilla y actualiza sus campos."""
    nombre_template = (p.get("whatsapp_template") or "").strip()
    if not nombre_template:
        p["whatsapp_template_status"] = None
        p["whatsapp_template_error"] = "Esta plantilla no tiene un template de Meta configurado"
        p["whatsapp_template_rejected_reason"] = None
        return p

    lang = (p.get("whatsapp_template_lang") or "").strip() or "es"
    servicio = WhatsAppService()
    resultado = servicio.estado_template_meta(nombre_template, lang)

    p["whatsapp_template_status"] = resultado.get("status")
    p["whatsapp_template_error"] = resultado.get("error")
    p["whatsapp_template_rejected_reason"] = resultado.get("rejected_reason")
    return p


@app.get("/api/plantillas/{plantilla_id}/estado-meta")
def estado_plantilla_meta(plantilla_id: int, sesion: dict = Depends(sesion_actual)):
    plantillas = leer_plantillas()
    p = next((x for x in plantillas if x["id"] == plantilla_id), None)
    if p is None:
        raise HTTPException(404, detail="Plantilla no encontrada")

    p = _actualizar_estado_meta(p)
    escribir_plantillas(plantillas)
    return p


@app.post("/api/plantillas/estado-meta/actualizar")
def actualizar_todos_estados_meta(sesion: dict = Depends(sesion_actual)):
    plantillas = leer_plantillas()
    for p in plantillas:
        if (p.get("whatsapp_template") or "").strip():
            _actualizar_estado_meta(p)
    escribir_plantillas(plantillas)
    return sorted(plantillas, key=lambda p: p.get("actualizada", 0), reverse=True)


def _texto_desde_componentes(components: list) -> str:
    """Extrae el texto del componente BODY de un template de Meta (los
    placeholders {{1}}, {{2}}... quedan tal cual, no sabemos a qué comodín
    del sistema corresponden)."""
    for c in components or []:
        if (c.get("type") or "").upper() == "BODY":
            return c.get("text", "") or ""
    return ""


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


@app.post("/api/plantillas/sincronizar-meta")
def sincronizar_plantillas_meta(sesion: dict = Depends(sesion_actual)):
    """Meta es la fuente de verdad para los templates: esta cuenta no tiene
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
            cambio = (
                p.get("whatsapp_template_id") != match.get("id")
                or p.get("whatsapp_template_status") != match.get("status")
                or p.get("whatsapp_template_categoria") != match.get("category")
            )
            actualizadas += 1 if cambio else 0
            p["whatsapp_template_id"] = match.get("id")
            p["whatsapp_template_status"] = match.get("status")
            p["whatsapp_template_categoria"] = match.get("category") or p.get("whatsapp_template_categoria")
            p["whatsapp_template_lang"] = match.get("language") or lang
            p["whatsapp_template_rejected_reason"] = match.get("rejected_reason") or match.get("reject_reason")
            p["whatsapp_template_error"] = None
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


@app.post("/api/plantillas", status_code=201)
def crear_plantilla(body: PlantillaIn, sesion: dict = Depends(sesion_actual)):
    if not body.nombre.strip() or not body.texto.strip():
        raise HTTPException(400, detail="Nombre y mensaje son obligatorios")
    if len(body.texto) > MAX_TEXTO_PLANTILLA:
        raise HTTPException(400, detail=f"El mensaje supera el limite de {MAX_TEXTO_PLANTILLA} caracteres")

    plantillas = leer_plantillas()
    clave = slug(body.clave or body.nombre)

    if any(p["clave"] == clave for p in plantillas):
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
        "whatsapp_template_categoria": (body.whatsapp_template_categoria or "").strip() or None,
        "actualizada": int(time.time() * 1000),
    }
    nueva = _registrar_template_meta(nueva)
    plantillas.append(nueva)
    escribir_plantillas(plantillas)
    return nueva


@app.put("/api/plantillas/{plantilla_id}")
def actualizar_plantilla(plantilla_id: int, body: PlantillaIn, sesion: dict = Depends(sesion_actual)):
    if not body.nombre.strip() or not body.texto.strip():
        raise HTTPException(400, detail="Nombre y mensaje son obligatorios")
    if len(body.texto) > MAX_TEXTO_PLANTILLA:
        raise HTTPException(400, detail=f"El mensaje supera el limite de {MAX_TEXTO_PLANTILLA} caracteres")

    plantillas = leer_plantillas()
    for p in plantillas:
        if p["id"] == plantilla_id:
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
            # El nombre del template de Meta va ligado al nombre de la plantilla
            # (que es inmutable), así que nunca cambia al editar.
            p["whatsapp_template"] = p.get("whatsapp_template") or (slug(p.get("nombre", "")) or None)
            p["whatsapp_template_lang"] = (body.whatsapp_template_lang or "").strip() or None
            p["whatsapp_template_categoria"] = (body.whatsapp_template_categoria or "").strip() or None
            p["actualizada"] = int(time.time() * 1000)
            p = _registrar_template_meta(p, nombre_template_anterior, lang_anterior, template_id_anterior)
            escribir_plantillas(plantillas)
            return p

    raise HTTPException(404, detail="Plantilla no encontrada")


@app.delete("/api/plantillas/{plantilla_id}")
def eliminar_plantilla(plantilla_id: int, sesion: dict = Depends(sesion_actual)):
    plantillas = leer_plantillas()
    objetivo = next((p for p in plantillas if p["id"] == plantilla_id), None)
    if objetivo is None:
        raise HTTPException(404, detail="Plantilla no encontrada")

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


def leer_config(ambiente: str | None = None) -> dict:
    # Toda la config vive en la tabla `configuracion`. Este endpoint devuelve
    # una vista segura (sin volcar secretos como el token de Meta o la clave SMTP).
    cfg = config_all()
    ent = entorno_valido(ambiente)
    clave_numeros = "numeros_prueba_dev" if ent == "desarrollo" else "numeros_prueba_prod"

    def lista(valor: str) -> list:
        return [n.strip() for n in (valor or "").split(",") if n.strip()]

    try:
        intervalo = int(cfg.get("intervalo_ms") or 1000)
    except (TypeError, ValueError):
        intervalo = 1000

    return {
        "entorno": ent,
        "base_datos": nombre_base(ent),
        "numeros_autorizados": lista(cfg.get(clave_numeros)),
        "metodo_envio": cfg.get("metodo_envio") or "simulado",
        "url_base": (cfg.get("url_base") or "").strip(),
        "correo_configurado": bool((cfg.get("smtp_host") or "").strip() and (cfg.get("smtp_pass") or "").strip()),
        "wa_api": {
            "configurada": bool(
                (cfg.get("wa_token") or "").strip()
                and (cfg.get("wa_phone_id") or "").strip()
            ),
            "waba_id": (cfg.get("wa_business_account_id") or "").strip(),
            "template_lang": (cfg.get("wa_template_lang") or "es").strip(),
            "version_graph": (cfg.get("wa_graph_version") or "v26.0").strip(),
        },
        "intervalo_ms": intervalo,
    }


@app.get("/api/configuracion")
def obtener_configuracion(ambiente: str | None = Query(None),
                          sesion: dict = Depends(sesion_actual)):
    try:
        return leer_config(ambiente)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))


@app.put("/api/configuracion")
def actualizar_configuracion(body: ConfigIn, sesion: dict = Depends(solo_admin)):
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
            {"clave": "intervalo_ms", "etiqueta": "Intervalo entre mensajes (ms)", "tipo": "number"},
            {"clave": "numeros_prueba_dev", "etiqueta": "Números de prueba · desarrollo", "tipo": "text",
             "ayuda": "Separados por coma. En entorno de desarrollo solo se envía a estos."},
            {"clave": "numeros_prueba_prod", "etiqueta": "Números de prueba · producción", "tipo": "text",
             "ayuda": "Separados por coma."},
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


@app.get("/api/configuracion/todo")
def obtener_configuracion_completa(sesion: dict = Depends(solo_admin)):
    """Todas las claves de configuración con su valor real (incluye secretos)."""
    return {"secciones": _CONFIG_SECCIONES, "valores": _config_valores()}


class ConfigTodoIn(BaseModel):
    cambios: dict[str, str]


@app.put("/api/configuracion/todo")
def actualizar_configuracion_completa(body: ConfigTodoIn, sesion: dict = Depends(solo_admin)):
    cambios: dict[str, str] = {}
    for clave, valor in (body.cambios or {}).items():
        if clave not in CONFIG_DEFAULTS:
            raise HTTPException(400, detail=f"Clave de configuración desconocida: «{clave}»")
        v = "" if valor is None else str(valor).strip()
        if clave in _CONFIG_ENUM and v not in _CONFIG_ENUM[clave]:
            raise HTTPException(400, detail=f"«{clave}»: usa uno de {', '.join(_CONFIG_ENUM[clave])}")
        if clave in ("intervalo_ms", "smtp_port"):
            try:
                n = int(v or "0")
            except ValueError:
                raise HTTPException(400, detail=f"«{clave}» debe ser un número entero")
            v = str(max(1 if clave == "smtp_port" else 0, n))
        if clave == "smtp_tls":
            v = "true" if v.lower() in ("true", "1", "on", "si", "sí") else "false"
        cambios[clave] = v

    if cambios:
        config_set(cambios)
    return {"valores": _config_valores()}


class PruebaWAIn(BaseModel):
    telefono: str
    mensaje: str = "Mensaje de prueba del sistema SNW"


@app.post("/api/notificaciones/prueba-wa")
def probar_api_wa(body: PruebaWAIn, sesion: dict = Depends(solo_admin)):
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


JOBS: dict = {}
PENDIENTES: dict = {}


def url_base() -> str:
    base = (config_get("url_base") or "").strip().rstrip("/")
    return base or "http://localhost:8000"


def _config_correo() -> dict:
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
                                ambiente: str, plantilla_clave: str = "") -> bool:
    c = _config_correo()
    emisor, destino = c["emisor"], c["destino"]
    base = url_base()
    if not emisor or not destino:
        print(f"[CORREO] Emisor o destino no configurado. Token {token} -> {base}/api/notificaciones/confirmar/{token}")
        return False
    host, port, user, pwd, tls = c["host"], c["port"], c["user"], c["pwd"], c["tls"]

    costo = _costo_estimado_por_clave(plantilla_clave, total)
    costo_txt = _fmt_moneda(costo["costo"], costo["moneda"]) if costo else "no disponible"

    if not host or not pwd:
        # Modo simulado: logear URL para pruebas sin SMTP real
        print(f"[CORREO SIMULADO] Para {destino} desde {emisor}: confirmar {base}/api/notificaciones/confirmar/{token} | rechazar {base}/api/notificaciones/rechazar/{token} - {total} personas, plantilla '{plantilla_nombre}', base {ambiente}, costo aprox. {costo_txt}")
        return True

    confirm_url = f"{base}/api/notificaciones/confirmar/{token}"
    reject_url = f"{base}/api/notificaciones/rechazar/{token}"
    subject = f"[SNW] Confirmar envío masivo - {total} destinatarios (~{costo_txt})"

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
      <p>Se ha solicitado enviar la plantilla <strong>{plantilla_nombre}</strong> a <strong>{total} personas</strong> desde la base de datos <strong>{ambiente}</strong>.</p>
      {bloque_costo}
      <div style="background:#f5f7f8; border-left:4px solid #128c7e; padding:14px 16px; margin:18px 0; border-radius:6px;">
        <p style="margin:0 0 6px; font-size:12px; color:#66757f; font-weight:bold;">Mensaje a enviar:</p>
        <p style="margin:0; white-space:pre-wrap; font-family:Consolas,monospace; font-size:13px; color:#24303c;">{plantilla_texto}</p>
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


def _enviar_correo_rechazo(nombre_enviador: str, plantilla_nombre: str, total: int, comentario: str) -> bool:
    c = _config_correo()
    emisor, destino = c["emisor"], c["destino"]
    if not emisor or not destino:
        print(f"[CORREO] Emisor o destino no configurado. Rechazo -> {destino}")
        return False
    host, port, user, pwd, tls = c["host"], c["port"], c["user"], c["pwd"], c["tls"]

    if not host or not pwd:
        print(f"[CORREO SIMULADO] Rechazo de envío de '{nombre_enviador}' plantilla '{plantilla_nombre}' ({total} dest.): {comentario}")
        return True

    subject = f"[SNW] Envío rechazado por supervisor - {plantilla_nombre}"
    html = f"""
    <html><body style="font-family: Arial, sans-serif; color: #24303c;">
      <h2 style="color:#b23b37;">Envío rechazado</h2>
      <p>El envío de la plantilla <strong>{plantilla_nombre}</strong> a <strong>{total} personas</strong>, solicitado por <strong>{nombre_enviador}</strong>, fue <strong>rechazado</strong> por el supervisor.</p>
      <div style="background:#fdf0f0; border-left:4px solid #b23b37; padding:14px 16px; margin:18px 0; border-radius:6px;">
        <p style="margin:0 0 6px; font-size:12px; color:#66757f; font-weight:bold;">Comentario del supervisor:</p>
        <p style="margin:0; white-space:pre-wrap; font-size:14px; color:#24303c;">{comentario or "(sin comentario)"}</p>
      </div>
      <p>Revisa el comentario y vuelve a intentarlo si corresponde.</p>
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
        print(f"[CORREO] Rechazo enviado a {destino}")
        return True
    except Exception as e:
        log_error(f"_enviar_correo_rechazo a {destino}", e)
        return False


def renderizar_mensaje(texto: str, paciente: dict) -> str:
    reemplazos = {
        "{nombre}": paciente.get("nombre") or "",
        "{apellido}": paciente.get("apellido") or "",
        "{info_extra}": paciente.get("info_extra") or "",
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


def crear_envio_batch(base_datos, plantilla_clave, plantilla_nombre, total, ambiente) -> int | None:
    try:
        with conectar(ambiente) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO envios (base_datos, plantilla_clave, plantilla_nombre, total_pacientes, estado)"
                " VALUES (%s, %s, %s, %s, 'completado')",
                (base_datos, plantilla_clave, plantilla_nombre, total),
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


@app.post("/api/notificaciones/enviar", status_code=202)
def iniciar_envio(body: EnvioIn, background_tasks: BackgroundTasks,
                  sesion: dict = Depends(sesion_actual)):
    try:
        amb = entorno_valido(body.ambiente)
    except ValueError:
        raise HTTPException(400, detail=f"Entorno inválido: '{body.ambiente}'")

    # En ambiente de desarrollo, el usuario normal solo puede enviar a la base
    # de desarrollo (números autorizados); nunca a producción.
    entorno_global = config_get("entorno", "desarrollo").strip().lower()
    if entorno_global == "desarrollo" and sesion.get("rol") != "administrador":
        amb = "desarrollo"

    if not body.pacientes and body.pacientes is not None:
        raise HTTPException(400, detail="No se seleccionaron pacientes")

    cfg = leer_config(amb)
    lista_autorizados = cfg.get("numeros_autorizados", [])
    autorizados = set(lista_autorizados)

    plantilla = next((p for p in leer_plantillas() if p["id"] == body.plantilla_id), None)
    if plantilla is None:
        raise HTTPException(404, detail="Plantilla no encontrada")

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
                    cond.append("p.estado = 'pendiente'")
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
                "info_extra": p.get("info_extra") or "",
            },
        })

    # En producción se puede limitar cuántos se envían de esta tanda; el resto
    # queda pendiente para un envío posterior. En desarrollo no aplica.
    if amb == "produccion" and body.limite is not None and destinatarios:
        n = max(1, min(int(body.limite), len(destinatarios)))
        destinatarios = destinatarios[:n]

    if not destinatarios:
        # Aunque no salga ningún mensaje, si hubo rechazados se deja constancia
        # del intento en el historial (0 enviados, N inválidos).
        envio_id = None
        if rechazados:
            envio_id = crear_envio_batch(nombre_base(amb), plantilla["clave"],
                                         plantilla["nombre"], len(rechazados), amb)
            if envio_id:
                actualizar_envio_batch(envio_id, amb, invalidos=len(rechazados))
                for r in rechazados:
                    registrar_historial(r.get("id"), r.get("nombre"), r.get("telefono"),
                                        plantilla["clave"], "", "numero_invalido",
                                        r.get("motivo"), ambiente=amb, envio_id=envio_id)
        return {"iniciado": False, "total": 0, "rechazados": rechazados,
                "requiere_confirmacion": False, "envio_id": envio_id}

    # En producción se requiere confirmación por correo del supervisor,
    # salvo que el usuario sea administrador (envía directo sin correo).
    if amb == "produccion" and sesion.get("rol") != "administrador":
        token = uuid.uuid4().hex
        envio_id = crear_envio_batch(nombre_base(amb), plantilla["clave"], plantilla["nombre"],
                                     len(destinatarios) + len(rechazados), amb)
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
            "nombre_enviador": sesion.get("nombre", "Usuario"),
        }
        if envio_id:
            actualizar_envio_batch(envio_id, amb, invalidos=len(rechazados))
            for r in rechazados:
                registrar_historial(r.get("id"), r.get("nombre"), r.get("telefono"),
                                    plantilla["clave"], "", "numero_invalido",
                                    r.get("motivo"), ambiente=amb, envio_id=envio_id)
        _enviar_correo_confirmacion(token, len(destinatarios), plantilla["nombre"], plantilla["texto"],
                                    amb, plantilla["clave"])
        return {"requiere_confirmacion": True, "solicitud_id": token, "total": len(destinatarios),
                "ambiente": amb, "rechazados": rechazados,
                "confirm_url": f"{url_base()}/api/notificaciones/confirmar/{token}"}

    envio_id = crear_envio_batch(nombre_base(amb), plantilla["clave"], plantilla["nombre"],
                                 len(destinatarios) + len(rechazados), amb)
    if envio_id:
        actualizar_envio_batch(envio_id, amb, invalidos=len(rechazados))
        for r in rechazados:
            registrar_historial(r.get("id"), r.get("nombre"), r.get("telefono"),
                                plantilla["clave"], "", "numero_invalido",
                                r.get("motivo"), ambiente=amb, envio_id=envio_id)

    job_id = uuid.uuid4().hex[:8]
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
    }

    background_tasks.add_task(procesar_job, job_id)
    return {"requiere_confirmacion": False, "iniciado": True, "job_id": job_id, "total": len(destinatarios),
            "ambiente": amb, "rechazados": rechazados}


@app.get("/api/notificaciones/solicitud/{token}")
def estado_solicitud(token: str, sesion: dict = Depends(sesion_actual)):
    pend = PENDIENTES.get(token)
    if not pend:
        raise HTTPException(404, detail="Solicitud no encontrada")
    return {"solicitud_id": token, "estado": pend["estado"], "total": len(pend["destinatarios"]),
            "job_id": pend.get("job_id"), "ambiente": pend["ambiente"],
            "comentario": pend.get("comentario", "")}


@app.get("/api/notificaciones/rechazar/{token}")
def formulario_rechazo(token: str):
    pend = PENDIENTES.get(token)
    if not pend:
        return HTMLResponse("<html><body style='font-family:Arial; text-align:center; padding:40px;'><h3>Solicitud no encontrada o expirada</h3></body></html>", status_code=404)
    if pend["estado"] == "rechazado":
        return HTMLResponse("<html><body style='font-family:Arial; text-align:center; padding:40px;'><h2>Este envío ya fue rechazado</h2></body></html>")
    if pend["estado"] == "confirmado":
        return HTMLResponse("<html><body style='font-family:Arial; text-align:center; padding:40px;'><h2>Este envío ya fue confirmado y está en proceso</h2></body></html>")
    total = len(pend["destinatarios"])
    plantilla = pend["plantilla"]["nombre"]
    return HTMLResponse(f"""
<html><head><meta charset='utf-8'><title>Rechazar envío</title></head>
<body style='font-family: Segoe UI, Arial; text-align:center; padding:40px; background:#f0f2f5;'>
<div style='background:#fff; max-width:520px; margin:40px auto; padding:32px; border-radius:14px; box-shadow:0 4px 20px rgba(0,0,0,0.1); text-align:left;'>
<h2 style='color:#b23b37; margin-top:0;'>Rechazar envío</h2>
<p>Vas a rechazar el envío de <strong>{total} mensajes</strong> de la plantilla <strong>{plantilla}</strong>.</p>
<form method="POST" action="/api/notificaciones/rechazar/{token}">
  <label style="display:block; font-size:14px; color:#66757f; margin:14px 0 6px;">Comentario para el enviador (opcional):</label>
  <textarea name="comentario" rows="4" style="width:100%; padding:10px; font:inherit; font-size:14px; border:1px solid #dde1e6; border-radius:8px;" placeholder="Ej: falta adjuntar el consentimiento firmado."></textarea>
  <div style="margin-top:20px; text-align:right;">
    <button type="button" onclick="window.location.href='{url_base()}/api/notificaciones/confirmar/{token}'" style="background:#fafbfc; color:#24303c; border:1px solid #dde1e6; padding:11px 20px; border-radius:10px; font-weight:600; cursor:pointer; font-family:inherit;">Volver</button>
    <button type="submit" style="background:#b23b37; color:#fff; border:none; padding:11px 22px; border-radius:10px; font-weight:700; cursor:pointer; font-family:inherit;">Rechazar envío</button>
  </div>
</form>
</div>
</body></html>
""")


@app.post("/api/notificaciones/rechazar/{token}")
def rechazar_envio(token: str, comentario: str = Form("")):
    pend = PENDIENTES.get(token)
    if not pend:
        return HTMLResponse("<html><body style='font-family:Arial; text-align:center; padding:40px;'><h3>Solicitud no encontrada o expirada</h3></body></html>", status_code=404)
    if pend["estado"] == "rechazado":
        return HTMLResponse("<html><body style='font-family:Arial; text-align:center; padding:40px;'><h2>Este envío ya fue rechazado</h2></body></html>")
    if pend["estado"] == "confirmado":
        return HTMLResponse("<html><body style='font-family:Arial; text-align:center; padding:40px;'><h2>Este envío ya fue confirmado y está en proceso</h2></body></html>")
    pend["estado"] = "rechazado"
    comentario = (comentario or "").strip()
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


@app.get("/api/notificaciones/confirmar/{token}")
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


@app.post("/api/notificaciones/destinatarios")
def contar_destinatarios(body: DestinosIn, sesion: dict = Depends(sesion_actual)):
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

    return {
        "total": total,
        "pendientes": elegibles,
        "base_datos": nombre_base(amb),
    }


@app.get("/api/notificaciones/jobs/{job_id}")
def estado_job(job_id: str, sesion: dict = Depends(sesion_actual)):
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, detail="Envío no encontrado")

    return {k: v for k, v in job.items() if k != "destinatarios"}


@app.post("/api/notificaciones/jobs/{job_id}/cancelar")
def cancelar_job(job_id: str, sesion: dict = Depends(sesion_actual)):
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, detail="Envío no encontrado")
    if job.get("estado") in ("completado", "cancelado", "error"):
        return {"ok": False, "estado": job.get("estado"), "detail": "El envío ya finalizó"}
    job["cancelado"] = True
    return {"ok": True, "estado": "cancelado"}


@app.post("/api/notificaciones/jobs/{job_id}/pausa")
def pausar_job(job_id: str, sesion: dict = Depends(sesion_actual)):
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, detail="Envío no encontrado")
    if job.get("estado") in ("completado", "cancelado", "error"):
        return {"ok": False, "estado": job.get("estado"), "detail": "El envío ya finalizó"}
    job["pausado"] = True
    return {"ok": True, "estado": "pausado"}


@app.post("/api/notificaciones/jobs/{job_id}/reanudar")
def reanudar_job(job_id: str, sesion: dict = Depends(sesion_actual)):
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, detail="Envío no encontrado")
    if job.get("estado") in ("completado", "cancelado", "error"):
        return {"ok": False, "estado": job.get("estado"), "detail": "El envío ya finalizó"}
    job["pausado"] = False
    return {"ok": True, "estado": "en_proceso"}


@app.get("/api/notificaciones/historial")
def listar_historial(q: str | None = Query(None), estado: str | None = Query(None),
                     ambiente: str = Query("produccion"), sesion: dict = Depends(sesion_actual)):
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


@app.get("/api/notificaciones/historial/{envio_id}/detalle")
def detalle_historial(envio_id: int, ambiente: str = Query("produccion"),
                      sesion: dict = Depends(sesion_actual)):
    # log_envios es única para todo el sistema; el detalle se busca por envio_id.
    # Para 'respuesta' se usa la señal EFECTIVA actual del paciente (respondió /
    # se dio de baja / sin respuesta), no el valor congelado en la fila de envío.
    t = tabla_pacientes(ambiente)
    tiene_opt = columna_existe(t, "whatsapp_opt_out", ambiente)
    re_expr = expr_respuesta_efectiva("pac", tiene_opt, columna_existe(t, "respuesta_manual", ambiente))
    sql = (
        "SELECT le.id, le.nombre_paciente, le.numero_telefono, le.estado_envio,"
        " le.descripcion_error, le.fecha_hora,"
        f" (CASE WHEN pac.id IS NOT NULL THEN {re_expr}"
        "        ELSE COALESCE(le.respuesta, 'pendiente') END) AS respuesta"
        f" FROM log_envios le LEFT JOIN {t} pac ON pac.id = le.paciente_id"
        " WHERE le.envio_id = %s ORDER BY le.id"
    )
    with conectar(ambiente) as conn, conn.cursor() as cur:
        cur.execute(sql, (envio_id,))
        filas = cur.fetchall()
    for f in filas:
        f["fecha"] = f.pop("fecha_hora").strftime("%d-%m-%Y %H:%M")
    return filas


@app.put("/api/notificaciones/historial/{registro_id}/respuesta")
def actualizar_respuesta(registro_id: int, body: EstadoPacienteIn,
                         ambiente: str = Query("produccion"),
                         sesion: dict = Depends(sesion_actual)):
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


@app.get("/api/estadisticas")
def estadisticas(sesion: dict = Depends(sesion_actual)):
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


@app.get("/api/estadisticas/envios")
def estadisticas_envios(granularidad: str = Query("mes"), sesion: dict = Depends(sesion_actual)):
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
        " DATE_FORMAT(descargada, '%%d-%%m-%%Y %%H:%%i') AS descargada"
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


@app.get("/api/tarifas")
def obtener_tarifas(sesion: dict = Depends(solo_admin)):
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
        "nunca_descargada": not todas,
    }


@app.post("/api/tarifas/actualizar")
def actualizar_tarifas(sesion: dict = Depends(solo_admin)):
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


@app.get("/api/tarifas/chile.csv")
def descargar_tarifa_csv(sesion: dict = Depends(solo_admin)):
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


@app.get("/api/estadisticas/costos")
def estadisticas_costos(granularidad: str = Query("mes"), sesion: dict = Depends(solo_admin)):
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


app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="localhost", port=8000)