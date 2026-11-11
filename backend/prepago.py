"""Líneas prepago, historial y despacho de recordatorios."""

import asyncio
import datetime as dt
import re
from collections import Counter
from contextlib import contextmanager
from html import escape
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from pymysql.err import IntegrityError

from config_service import config_correo, entregar_correo_real
from db import conectar, config_all, config_set, log_error
from telefono import normalizar_telefono
from whatsapp_channels import catalogo_centros, codigo_centro_canonico


DIAS = 150
MAX_DIAS = 3650
CLAVE_PLAZO = "prepago_recordatorio_dias"
ZONA = ZoneInfo("America/Santiago")
OPERADORES = (
    ("wom", "WOM", "wom.cl", "https://www.wom.cl/recargas/"),
    ("entel", "Entel", "entel.cl", "https://miportal.entel.cl/marketing/lugares-recargas"),
    ("movistar", "Movistar", "movistar.cl", "https://recargaweb.movistar.cl/"),
    ("claro", "Claro", "clarochile.cl", "https://recargas.clarochile.cl/"),
)
LINEAS = (
    ("SNW MVYS", "SNW", "VS"), ("SNW MS", "SNW", "MS"),
    ("CRM MS", "CRM", "MS"), ("CRM MVYS", "CRM", "VS"),
)

ESQUEMA = (
    """CREATE TABLE IF NOT EXISTS prepago_operadores (
        codigo VARCHAR(32) PRIMARY KEY, nombre VARCHAR(80) NOT NULL,
        dominio VARCHAR(253) NULL, url_recarga VARCHAR(500) NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS prepago_lineas (
        id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, semilla VARCHAR(24) NULL UNIQUE,
        nombre VARCHAR(80) NOT NULL, telefono VARCHAR(16) NULL UNIQUE,
        operador VARCHAR(32) NOT NULL, sistema VARCHAR(12) NOT NULL,
        clinica VARCHAR(8) NULL, ultima_recarga DATE NULL,
        ciclo INT UNSIGNED NOT NULL DEFAULT 1,
        activo BOOLEAN NOT NULL DEFAULT TRUE, avisar BOOLEAN NOT NULL DEFAULT TRUE,
        version INT UNSIGNED NOT NULL DEFAULT 1,
        creado_en TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        actualizado_en TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        INDEX prepago_vencimientos (activo, avisar, ultima_recarga),
        FOREIGN KEY (operador) REFERENCES prepago_operadores(codigo)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS prepago_recargas (
        id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, linea_id BIGINT UNSIGNED NOT NULL,
        fecha DATE NOT NULL, fecha_anterior DATE NULL, tipo VARCHAR(16) NOT NULL,
        registrado_por VARCHAR(100) NOT NULL, registrado_en TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        observacion VARCHAR(500) NOT NULL DEFAULT '',
        INDEX prepago_recargas_linea (linea_id, id),
        FOREIGN KEY (linea_id) REFERENCES prepago_lineas(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS prepago_recordatorios (
        id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, linea_id BIGINT UNSIGNED NOT NULL,
        ciclo INT UNSIGNED NOT NULL, fecha_ultima_recarga DATE NOT NULL, programado DATE NOT NULL,
        intento_en DATETIME NOT NULL, enviado_en DATETIME NULL,
        destinatario VARCHAR(254) NOT NULL, estado VARCHAR(20) NOT NULL,
        error VARCHAR(100) NULL,
        INDEX prepago_avisos_ciclo (linea_id, ciclo, id),
        FOREIGN KEY (linea_id) REFERENCES prepago_lineas(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
)


def asegurar_esquema(config=None):
    cfg = config if config is not None else config_all(refresh=True)
    telefonos = {}
    for nombre, sistema, clinica in LINEAS:
        sufijo = "WSP_PHONE_NUMBER" if sistema == "SNW" else "CRM_PHONE_NUMBER"
        telefonos[nombre] = telefono_valido(cfg.get(f"{clinica}_{sufijo}", ""))
    coincidencias = Counter(telefonos.values())
    with conectar() as conn, conn.cursor() as cur:
        for sentencia in ESQUEMA:
            cur.execute(sentencia)
        cur.executemany(
            "INSERT INTO prepago_operadores (codigo,nombre,dominio,url_recarga) VALUES (%s,%s,%s,%s)"
            " ON DUPLICATE KEY UPDATE codigo=VALUES(codigo)", OPERADORES,
        )
        for nombre, sistema, clinica in LINEAS:
            cur.execute(
                "INSERT INTO prepago_lineas (semilla,nombre,operador,sistema,clinica) "
                "VALUES (%s,%s,'wom',%s,%s) ON DUPLICATE KEY UPDATE semilla=VALUES(semilla)",
                (nombre, nombre, sistema, clinica),
            )
            telefono = telefonos[nombre]
            if cur.rowcount == 1 and telefono and coincidencias[telefono] == 1:
                try:
                    cur.execute("UPDATE prepago_lineas SET telefono=%s WHERE semilla=%s AND telefono IS NULL",
                                (telefono, nombre))
                except IntegrityError as exc:
                    if exc.args[0] != 1062:
                        raise
        conn.commit()


def hoy():
    return dt.datetime.now(ZONA).date()


def fecha_valida(fecha):
    if fecha and not dt.date(1900, 1, 1) <= fecha <= hoy():
        raise ValueError("Indica una fecha de recarga válida, hasta hoy.")
    return fecha


def telefono_valido(crudo):
    crudo = (crudo or "").strip()
    return normalizar_telefono(crudo) if re.fullmatch(r"\+?[0-9 ()-]{8,24}", crudo) else None


def plazo_recordatorio(config):
    try:
        dias = int(config.get(CLAVE_PLAZO, DIAS))
    except (TypeError, ValueError):
        return DIAS
    return dias if 1 <= dias <= MAX_DIAS else DIAS


def guardar_plazo(dias):
    config_set({CLAVE_PLAZO: dias})


def calendario(fecha, hoy_local, dias=DIAS):
    vence = fecha + dt.timedelta(days=dias) if fecha else None
    restantes = (vence - hoy_local).days if vence else None
    return {
        "proximo_recordatorio": vence,
        "dias_transcurridos": (hoy_local - fecha).days if fecha else None,
        "dias_restantes": restantes,
        "estado": ("Sin configurar" if restantes is None else "Pendiente" if restantes <= 0
                   else "Próximo" if restantes <= 30 else "Al día"),
    }


def url_oficial(url, dominio):
    dominio = dominio.strip().lower()
    patron = r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}"
    if len(dominio) > 253 or not re.fullmatch(patron, dominio) or dominio.endswith((".localhost", ".local", ".internal", ".test", ".example", ".invalid", ".onion")):
        raise ValueError("Indica el dominio público oficial de la compañía.")
    url = url.strip()
    if not url:
        return dominio, None
    if len(url) > 500 or not re.fullmatch(r"https://[a-zA-Z0-9.-]+(?:/[a-zA-Z0-9._/-]*)?", url):
        raise ValueError("Usa una URL HTTPS oficial, sin credenciales ni parámetros.")
    partes = urlsplit(url)
    if partes.hostname != dominio and not partes.hostname.endswith("." + dominio):
        raise ValueError("La URL debe pertenecer al dominio oficial de la compañía.")
    return dominio, url


def guardar_operador(datos):
    codigo = datos["codigo"].strip().lower()
    nombre = datos["nombre"].strip()
    if not nombre.isprintable() or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,31}", codigo):
        raise ValueError("Indica un nombre y un código de operador válido.")
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("SELECT dominio FROM prepago_operadores WHERE codigo=%s FOR UPDATE", (codigo,))
        existente = cur.fetchone()
        dominio = (existente and existente["dominio"]) or datos["dominio"]
        url = datos["url_recarga"]
        if dominio or url:
            dominio, url = url_oficial(url, dominio)
        if url and not datos["verificado"]:
            raise ValueError("Confirma que verificaste el enlace en el sitio oficial del operador.")
        cur.execute(
            "INSERT INTO prepago_operadores (codigo,nombre,dominio,url_recarga) VALUES (%s,%s,%s,%s)"
            " ON DUPLICATE KEY UPDATE nombre=VALUES(nombre), dominio=VALUES(dominio), url_recarga=VALUES(url_recarga)",
            (codigo, nombre, dominio or None, url or None),
        )
        conn.commit()
    return codigo


def correo_valido(correo):
    return len(correo) <= 254 and re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", correo)


def guardar_destino(correo):
    correo = correo.strip()
    if correo and not correo_valido(correo):
        raise ValueError("Ingresa un correo electrónico válido.")
    config_set({"prepago_correo_destino": correo})


def estado():
    cfg = config_all(refresh=True)
    dias = plazo_recordatorio(cfg)
    destino = str(cfg.get("prepago_correo_destino") or "").strip()
    destino_general = str(cfg.get("correo_destino") or "").strip()
    fecha = hoy()
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("SELECT l.*,o.nombre AS compania,o.url_recarga FROM prepago_lineas l "
                    "JOIN prepago_operadores o ON o.codigo=l.operador ORDER BY l.activo DESC,l.id")
        lineas = cur.fetchall()
        cur.execute("SELECT * FROM prepago_operadores ORDER BY nombre")
        operadores = cur.fetchall()
    for linea in lineas:
        linea.update(calendario(linea["ultima_recarga"], fecha, dias))
        if not linea["telefono"]:
            linea["estado"] = "Sin configurar"
    return {"lineas": lineas, "operadores": operadores, "hoy": fecha,
            "recordatorio_dias": dias, "recordatorio_dias_max": MAX_DIAS,
            "clinicas": catalogo_centros(cfg, incluir_inactivas=True),
            "destino": destino, "destino_general": destino_general,
            "correo_listo": bool(correo_valido(destino or destino_general)
                                and str(cfg.get("smtp_host") or "").strip()
                                and str(cfg.get("correo_emisor") or "").strip())}


@contextmanager
def bloquear_linea(cur, linea_id, espera=0):
    # El mismo cerrojo serializa cambios de ficha y despacho entre procesos.
    cur.execute("SELECT GET_LOCK(SHA2(CONCAT(DATABASE(),':prepago:',%s),256),%s) AS tomado",
                (linea_id, espera))
    tomado = cur.fetchone()["tomado"] == 1
    try:
        yield tomado
    finally:
        if tomado:
            cur.execute("SELECT RELEASE_LOCK(SHA2(CONCAT(DATABASE(),':prepago:',%s),256))",
                        (linea_id,))


def _linea_actual(cur, linea_id, version=None):
    cur.execute("SELECT * FROM prepago_lineas WHERE id=%s FOR UPDATE", (linea_id,))
    linea = cur.fetchone()
    if not linea:
        raise LookupError("La línea no existe.")
    if version is not None and linea["version"] != version:
        raise RuntimeError("La línea cambió. Actualiza la lista antes de guardar.")
    return linea


def _registrar_fecha(cur, linea_id, fecha, anterior, tipo, actor, observacion):
    cur.execute(
        "INSERT INTO prepago_recargas (linea_id,fecha,fecha_anterior,tipo,registrado_por,observacion) "
        "VALUES (%s,%s,%s,%s,%s,%s)", (linea_id, fecha, anterior, tipo, actor, observacion),
    )
    cur.execute(
        "UPDATE prepago_recordatorios SET estado='cancelado' "
        "WHERE linea_id=%s AND fecha_ultima_recarga<>%s AND estado='error'", (linea_id, fecha),
    )


def guardar_linea(datos, actor, linea_id=None):
    nombre = datos["nombre"].strip()
    crudo = datos["telefono"].strip()
    telefono = telefono_valido(crudo)
    clinica = codigo_centro_canonico(datos["clinica"])
    if clinica and clinica not in catalogo_centros(config_all(), incluir_inactivas=True):
        raise ValueError("Selecciona una clínica válida.")
    if not nombre.isprintable() or (not telefono and (crudo or linea_id is None)):
        raise ValueError("Indica un nombre y un número móvil chileno válido.")
    fecha = fecha_valida(datos["ultima_recarga"])
    valores = (nombre, telefono, datos["operador"], datos["sistema"], clinica or None,
               datos["activo"], datos["avisar"], fecha)
    try:
        with conectar() as conn, conn.cursor() as cur:
            cur.execute("SELECT codigo FROM prepago_operadores WHERE codigo=%s", (datos["operador"],))
            if not cur.fetchone():
                raise ValueError("Selecciona una compañía válida.")
            if linea_id is None:
                cur.execute(
                    "INSERT INTO prepago_lineas (nombre,telefono,operador,sistema,clinica,activo,avisar,ultima_recarga) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", valores,
                )
                linea_id = cur.lastrowid
                if fecha:
                    _registrar_fecha(cur, linea_id, fecha, None, "inicial", actor, "")
                conn.commit()
            else:
                with bloquear_linea(cur, linea_id, 2) as tomado:
                    if not tomado:
                        raise RuntimeError("La línea está ocupada. Inténtalo nuevamente.")
                    anterior = _linea_actual(cur, linea_id, datos["version"])
                    if not telefono and not anterior["semilla"]:
                        raise ValueError("Indica un número móvil chileno válido.")
                    if not fecha and anterior["ultima_recarga"]:
                        raise ValueError("Para corregir la fecha, indica la fecha real de recarga.")
                    cur.execute(
                        "UPDATE prepago_lineas SET nombre=%s,telefono=%s,operador=%s,sistema=%s,"
                        "clinica=%s,activo=%s,avisar=%s,ultima_recarga=%s,version=version+1 WHERE id=%s",
                        (*valores, linea_id),
                    )
                    if fecha != anterior["ultima_recarga"]:
                        _registrar_fecha(cur, linea_id, fecha, anterior["ultima_recarga"], "correccion", actor, "")
                    conn.commit()
    except IntegrityError as exc:
        if exc.args[0] == 1062:
            raise ValueError("Ese teléfono ya está registrado, incluso si la línea fue desactivada.") from exc
        raise
    return linea_id


def registrar_recarga(linea_id, fecha, observacion, actor, version):
    fecha_valida(fecha)
    with conectar() as conn, conn.cursor() as cur, bloquear_linea(cur, linea_id, 2) as tomado:
        if not tomado:
            raise RuntimeError("La línea está ocupada. Inténtalo nuevamente.")
        linea = _linea_actual(cur, linea_id, version)
        anterior = linea["ultima_recarga"]
        if anterior and fecha <= anterior:
            raise ValueError("La fecha debe ser posterior a la última recarga. Para corregirla, usa Editar.")
        _registrar_fecha(cur, linea_id, fecha, anterior, "recarga", actor, observacion.strip())
        cur.execute("UPDATE prepago_lineas SET ultima_recarga=%s,ciclo=ciclo+1,version=version+1 WHERE id=%s",
                    (fecha, linea_id))
        conn.commit()


def historial(linea_id, pagina):
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM prepago_lineas WHERE id=%s", (linea_id,))
        if not cur.fetchone():
            raise LookupError("La línea no existe.")
        offset = (pagina - 1) * 30
        cur.execute("SELECT * FROM prepago_recargas WHERE linea_id=%s ORDER BY id DESC LIMIT 31 OFFSET %s",
                    (linea_id, offset))
        recargas = cur.fetchall()
        cur.execute("SELECT * FROM prepago_recordatorios WHERE linea_id=%s ORDER BY id DESC LIMIT 31 OFFSET %s",
                    (linea_id, offset))
        avisos = cur.fetchall()
        return {"recargas": recargas[:30], "avisos": avisos[:30],
                "mas": len(recargas) > 30 or len(avisos) > 30}


def _procesar_linea(linea_id, hoy_local, destino, correo, enviar, dias):
    with conectar() as conn, conn.cursor() as cur, bloquear_linea(cur, linea_id) as tomado:
        if not tomado:
            return
        cur.execute("SELECT l.*,o.nombre AS compania FROM prepago_lineas l "
                    "JOIN prepago_operadores o ON o.codigo=l.operador WHERE l.id=%s", (linea_id,))
        linea = cur.fetchone()
        ultima_recarga = linea["ultima_recarga"]
        if not (linea["activo"] and linea["avisar"] and linea["telefono"] and ultima_recarga):
            return
        vence = ultima_recarga + dt.timedelta(days=dias)
        if vence > hoy_local:
            return
        cur.execute(
            "SELECT estado,intento_en FROM prepago_recordatorios WHERE linea_id=%s AND ciclo=%s "
            "AND estado IN ('enviado','incierto','error') ORDER BY id DESC LIMIT 1", (linea_id, linea["ciclo"]),
        )
        previo = cur.fetchone()
        if previo and (previo["estado"] != "error" or
                       previo["intento_en"].replace(tzinfo=dt.timezone.utc).astimezone(ZONA).date() >= hoy_local):
            return
        cur.execute(
            "INSERT INTO prepago_recordatorios (linea_id,ciclo,fecha_ultima_recarga,programado,intento_en,destinatario,estado) "
            "VALUES (%s,%s,%s,%s,UTC_TIMESTAMP(),%s,'incierto')",
            (linea_id, linea["ciclo"], ultima_recarga, vence, destino),
        )
        intento = cur.lastrowid
        conn.commit()
        asunto = f"SNW | Recordatorio de recarga prepago — {linea['nombre']} ({linea['compania']})"
        cuerpo = (
            f"<p>Recarga pendiente para {escape(linea['nombre'])} ({escape(linea['compania'])}).</p>"
            f"<p>Número: {escape(linea['telefono'])}<br>Última recarga: {ultima_recarga:%d-%m-%Y}<br>"
            f"Días transcurridos: {(hoy_local - ultima_recarga).days}</p>"
            "<p>Entra en SNW → Extras → Recordatorio prepago para abrir el portal del operador. "
            "Cuando completes la recarga, registra la fecha en SNW.</p>"
        )
        estado_envio, error = enviar(destino, asunto, cuerpo, correo)
        cur.execute("UPDATE prepago_recordatorios SET estado=%s,error=%s,"
                    "enviado_en=IF(%s='enviado',UTC_TIMESTAMP(),NULL) WHERE id=%s",
                    (estado_envio, error, estado_envio, intento))
        conn.commit()
        if error:
            log_error(f"recordatorio prepago, línea {linea_id}: {estado_envio} ({error})")


def revisar_recordatorios(hoy_local=None, enviar=entregar_correo_real):
    hoy_local = hoy_local or hoy()
    cfg = config_all(refresh=True)
    dias = plazo_recordatorio(cfg)
    correo = config_correo()
    destino = (cfg.get("prepago_correo_destino") or correo["destino"]).strip()
    if not correo_valido(destino) or not correo["host"] or not correo["emisor"]:
        return
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM prepago_lineas WHERE activo=1 AND avisar=1 "
                    "AND telefono IS NOT NULL AND ultima_recarga<=%s ORDER BY id",
                    (hoy_local - dt.timedelta(days=dias),))
        candidatos = cur.fetchall()
    for fila in candidatos:
        try:
            _procesar_linea(fila["id"], hoy_local, destino, correo, enviar, dias)
        except Exception as exc:
            log_error(f"recordatorio prepago, línea {fila['id']}", exc, solo_tipo=True)


async def loop_recordatorios():
    preparado = False
    while True:
        try:
            if not preparado:
                await asyncio.to_thread(asegurar_esquema)
                preparado = True
            await asyncio.to_thread(revisar_recordatorios)
        except Exception as exc:
            log_error("scheduler de recordatorios prepago", exc, solo_tipo=True)
            await asyncio.sleep(60)
            continue
        # Ejecuta al arrancar y luego una vez por día calendario en Chile.
        ahora = dt.datetime.now(ZONA)
        manana = dt.datetime.combine(ahora.date() + dt.timedelta(days=1), dt.time(8), ZONA)
        await asyncio.sleep(manana.timestamp() - ahora.timestamp())
