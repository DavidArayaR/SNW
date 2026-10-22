"""Recepción idempotente de citas atendidas; no almacena RUT ni el CSV original."""

import csv
import datetime as dt
import hashlib
import io
import json
import re
import secrets
import time
import unicodedata

import pymysql

from db import conectar
from telefono import normalizar_telefono
from whatsapp_channels import catalogo_centros

CSV_MAX_BYTES = 5 * 1024 * 1024
CSV_MAX_FILAS = 10000
REQUERIDAS = {"fecha", "ventana", "clinica_nombre", "reservo_cita_uuid",
             "paciente_nombre", "inicio_cita"}


class CsvInvalido(ValueError):
    pass


def _texto(valor, limite: int) -> str:
    texto = str(valor or "").strip()
    if len(texto) > limite or "\x00" in texto:
        raise ValueError("longitud_invalida")
    return texto


def _normalizar_nombre(valor: str) -> str:
    texto = unicodedata.normalize("NFKD", valor.casefold())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", texto).split())


def resolver_clinica(nombre: str, clinica_id: int | None, cfg: dict) -> str | None:
    catalogo = catalogo_centros(cfg)
    coincidencias = [codigo for codigo, etiqueta in catalogo.items()
                     if _normalizar_nombre(etiqueta) == _normalizar_nombre(nombre)]
    por_nombre = coincidencias[0] if len(coincidencias) == 1 else None
    try:
        mapa = json.loads(cfg.get("reagendar_clinicas_ids") or "{}")
        por_id = mapa.get(str(clinica_id)) if clinica_id is not None and isinstance(mapa, dict) else None
    except (ValueError, TypeError):
        por_id = None
    # Nunca escoger silenciosamente una clínica si ID y nombre discrepan.
    if por_id and por_id in catalogo and (not por_nombre or por_nombre == por_id):
        return por_id
    return por_nombre if not por_id else None


def analizar_csv(datos: bytes, cfg: dict) -> tuple[list[dict], list[dict], int]:
    if not datos:
        raise CsvInvalido("El archivo CSV está vacío.")
    if len(datos) > CSV_MAX_BYTES:
        raise CsvInvalido("El archivo supera los 5 MB.")
    try:
        texto = datos.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CsvInvalido("El CSV debe estar codificado en UTF-8.") from exc
    try:
        lector = csv.DictReader(io.StringIO(texto, newline=""), strict=True)
        encabezados = lector.fieldnames or []
        columnas = {c.strip().lower(): c for c in encabezados if c and c.strip()}
        if len(columnas) != len(encabezados) or not REQUERIDAS.issubset(columnas):
            raise CsvInvalido("El CSV tiene encabezados duplicados o le faltan columnas obligatorias.")
        registros, errores = [], []
        recibidas = 0
        for numero, fila in enumerate(lector, 2):
            recibidas += 1
            if recibidas > CSV_MAX_FILAS:
                raise CsvInvalido("El CSV supera el máximo de 10.000 filas.")
            if None in fila:
                errores.append({"row": numero, "code": "columnas_extra"})
                continue
            try:
                get = lambda clave: fila.get(columnas[clave]) if clave in columnas else None
                fecha = dt.date.fromisoformat(_texto(get("fecha"), 10))
                ventana = _texto(get("ventana"), 5)
                if ventana not in ("08-14", "14-20"):
                    raise ValueError("ventana_invalida")
                inicio = dt.datetime.strptime(_texto(get("inicio_cita"), 19), "%Y-%m-%d %H:%M:%S")
                uuid = _texto(get("reservo_cita_uuid"), 80)
                nombre = _texto(get("paciente_nombre"), 200)
                clinica = _texto(get("clinica_nombre"), 100)
                if not uuid or not nombre or not clinica or any(c in uuid for c in "\r\n\t"):
                    raise ValueError("campo_obligatorio")
                id_crudo = _texto(get("clinica_id"), 11)
                clinica_id = int(id_crudo) if id_crudo else None
                telefono = _texto(get("paciente_telefono"), 40) or None
                normalizado = normalizar_telefono(telefono) if telefono else None
                doctor = _texto(get("doctor_nombre"), 200) or None
                permiso = _texto(get("autorizado_contacto"), 5).casefold()
                autorizado = permiso in ("si", "sí", "true", "1")
                centro = resolver_clinica(clinica, clinica_id, cfg)
                problema = ("CLINICA_DESCONOCIDA" if not centro else
                            "TELEFONO_INVALIDO" if not normalizado else
                            "SIN_AUTORIZACION" if not autorizado else None)
                registros.append({"fecha": fecha, "ventana": ventana,
                                  "_row": numero,
                                  "clinica_id": clinica_id, "clinica_nombre": clinica,
                                  "reservo_cita_uuid": uuid, "paciente_nombre": nombre,
                                  "paciente_telefono": telefono,
                                  "paciente_telefono_normalizado": normalizado,
                                  "doctor_nombre": doctor, "inicio_cita": inicio,
                                  "centro_codigo": centro, "autorizado_contacto": autorizado,
                                  "ultimo_error_codigo": problema})
            except (ValueError, TypeError) as exc:
                codigo = str(exc) if str(exc) in (
                    "ventana_invalida", "campo_obligatorio",
                    "longitud_invalida") else "dato_invalido"
                errores.append({"row": numero, "code": codigo})
        if not recibidas:
            raise CsvInvalido("El CSV no contiene registros.")
        return registros, errores, recibidas
    except csv.Error as exc:
        raise CsvInvalido("El CSV está mal formado.") from exc


_COLUMNAS = ("fecha", "ventana", "clinica_id", "clinica_nombre", "reservo_cita_uuid",
             "paciente_nombre", "paciente_telefono", "paciente_telefono_normalizado",
             "doctor_nombre", "inicio_cita", "centro_codigo", "autorizado_contacto",
             "ultimo_error_codigo")


def importar(datos: bytes, cfg: dict) -> dict:
    registros, errores, recibidas = analizar_csv(datos, cfg)
    insertados = duplicados = 0
    from supresion import huella_telefono
    with conectar() as conn, conn.cursor() as cur:
        for registro in registros:
            telefono = registro["paciente_telefono_normalizado"]
            if telefono:
                cur.execute("SELECT 1 FROM supresion_telefonos"
                            " WHERE huella=%s AND estado <> 'exento' FOR UPDATE",
                            (huella_telefono(telefono),))
                if cur.fetchone():
                    errores.append({"row": registro["_row"], "code": "paciente_eliminado"})
                    continue
            try:
                cur.execute(
                    "INSERT INTO paciente_reagendar (" + ",".join(_COLUMNAS) + ")"
                    " VALUES (" + ",".join(["%s"] * len(_COLUMNAS)) + ")",
                    tuple(registro[col] for col in _COLUMNAS),
                )
                insertados += 1
            except pymysql.err.IntegrityError as exc:
                if exc.args[0] != 1062:
                    raise
                # Conserva íntegros estado, intentos, emisor e ID de Meta.
                duplicados += 1
        conn.commit()
    return {"success": True, "summary": {"received": recibidas, "inserted": insertados,
            "alreadyExists": duplicados, "rejected": len(errores)},
            "errors": errores[:50]}


def consumir_cuota(actor: str, maximo: int, ventana_s: int = 900) -> bool:
    """Cuota compartida en MySQL; no guarda IP, Bearer ni otros datos crudos."""
    actual = int(time.time() // ventana_s)
    huella = hashlib.sha256(actor.encode("utf-8")).hexdigest()
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO reagendar_import_quota (actor_hash, ventana, intentos)"
                    " VALUES (%s, %s, 1) ON DUPLICATE KEY UPDATE intentos = intentos + 1",
                    (huella, actual))
        cur.execute("SELECT intentos FROM reagendar_import_quota"
                    " WHERE actor_hash = %s AND ventana = %s", (huella, actual))
        permitido = int(cur.fetchone()["intentos"]) <= maximo
        conn.commit()
    return permitido


def reclamar_pendiente(centros: list[str]) -> dict | None:
    """Reserva atómica compartida entre workers; sin HTTP dentro de la transacción."""
    if not centros:
        return None
    marcadores = ",".join(["%s"] * len(centros))
    with conectar() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM paciente_reagendar WHERE estado_envio IN ('PENDIENTE','REINTENTO')"
            " AND autorizado_contacto = 1 AND paciente_telefono_normalizado IS NOT NULL"
            f" AND centro_codigo IN ({marcadores})"
            " AND (proximo_reintento_en IS NULL OR proximo_reintento_en <= UTC_TIMESTAMP())"
            " ORDER BY id LIMIT 1 FOR UPDATE", tuple(centros))
        fila = cur.fetchone()
        if not fila:
            return None
        token = secrets.token_hex(16)
        cur.execute(
            "UPDATE paciente_reagendar SET estado_envio='PROCESANDO', reserva_token=%s,"
            " reservado_en=UTC_TIMESTAMP(), ultimo_intento_en=UTC_TIMESTAMP(),"
            " intentos_envio=intentos_envio+1 WHERE id=%s",
            (token, fila["id"]))
        conn.commit()
    fila["reserva_token"] = token
    fila["intentos_envio"] += 1
    return fila


def registrar_lote(cita: dict, plantilla: dict, canal: dict) -> int:
    """Crea el contenedor del historial antes de tocar Meta."""
    with conectar() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO envios (base_datos, plantilla_clave, plantilla_nombre,"
            " total_pacientes, estado, usuario, centro_codigo, wa_phone_id, numero_emisor)"
            " VALUES ('paciente_reagendar', %s, %s, 1, 'completado',"
            " 'integracion_reagendar', %s, %s, %s)",
            (plantilla["clave"], plantilla.get("nombre") or plantilla["clave"],
             canal["codigo"], canal["phoneNumberId"], canal["phoneNumber"]))
        lote_id = cur.lastrowid
        cur.execute(
            "UPDATE paciente_reagendar SET envio_id=%s, plantilla_clave=%s"
            " WHERE id=%s AND reserva_token=%s AND estado_envio='PROCESANDO'",
            (lote_id, plantilla["clave"], cita["id"], cita["reserva_token"]))
        if cur.rowcount != 1:
            raise RuntimeError("Reserva de cita perdida")
        conn.commit()
    return lote_id


def marcar_fallo(cita: dict, codigo: str, *, reintentar: bool = False) -> None:
    """Los resultados inciertos nunca se reintentan automáticamente."""
    estado = "REINTENTO" if reintentar and cita["intentos_envio"] < 3 else "ERROR"
    demora = min(3600, 60 * 2 ** max(0, cita["intentos_envio"] - 1))
    with conectar() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE paciente_reagendar SET estado_envio=%s, reserva_token=NULL,"
            " reservado_en=NULL, ultimo_error_codigo=%s,"
            " proximo_reintento_en=CASE WHEN %s='REINTENTO'"
            " THEN DATE_ADD(UTC_TIMESTAMP(), INTERVAL %s SECOND) ELSE NULL END"
            " WHERE id=%s AND reserva_token=%s AND estado_envio='PROCESANDO'",
            (estado, codigo, estado, demora, cita["id"], cita["reserva_token"]))
        if cita.get("envio_id") and cur.rowcount:
            cur.execute("UPDATE envios SET fallidos=fallidos+1 WHERE id=%s", (cita["envio_id"],))
        conn.commit()


def marcar_enviado(cita: dict, canal: dict, plantilla: dict,
                   texto: str, message_id: str) -> None:
    """Resultado y entrada de historial juntos, o ninguno (sin reenvío)."""
    with conectar() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT estado_envio, reserva_token FROM paciente_reagendar"
            " WHERE id=%s FOR UPDATE", (cita["id"],))
        actual = cur.fetchone() or {}
        if actual.get("reserva_token") != cita["reserva_token"] or actual.get("estado_envio") != "PROCESANDO":
            raise RuntimeError("La reserva de cita cambió durante el envío")
        cur.execute(
            "INSERT INTO log_envios (envio_id, paciente_id, nombre_paciente,"
            " numero_telefono, mensaje, plantilla_clave, estado_envio,"
            " whatsapp_message_id, estado_whatsapp, wa_phone_id,"
            " centro_codigo, numero_emisor, tabla_pacientes)"
            " VALUES (%s, NULL, %s, %s, %s, %s, 'enviado', %s, 'sent', %s, %s, %s, NULL)",
            (cita["envio_id"], cita["paciente_nombre"],
             cita["paciente_telefono_normalizado"], texto, plantilla["clave"],
             message_id, canal["phoneNumberId"], canal["codigo"], canal["phoneNumber"]))
        log_id = cur.lastrowid
        cur.execute(
            "UPDATE paciente_reagendar SET estado_envio='ENVIADO',"
            " reserva_token=NULL, reservado_en=NULL, enviado_en=UTC_TIMESTAMP(),"
            " whatsapp_message_id=%s, whatsapp_phone_number_id=%s,"
            " whatsapp_numero_emisor=%s, log_envio_id=%s,"
            " ultimo_error_codigo=NULL, ultimo_error_detalle=NULL"
            " WHERE id=%s AND estado_envio='PROCESANDO'",
            (message_id, canal["phoneNumberId"], canal["phoneNumber"],
             log_id, cita["id"]))
        cur.execute("UPDATE envios SET enviados=enviados+1 WHERE id=%s", (cita["envio_id"],))
        conn.commit()


def recuperar_abandonados() -> int:
    """No repetir un POST incierto tras reinicio: queda para conciliación."""
    with conectar() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE paciente_reagendar SET estado_envio='ERROR',"
            " ultimo_error_codigo='RESULTADO_INCIERTO',"
            " ultimo_error_detalle='Revisar en Meta antes de volver a enviar',"
            " reservado_en=NULL, reserva_token=NULL"
            " WHERE estado_envio='PROCESANDO'"
            " AND reservado_en < DATE_SUB(UTC_TIMESTAMP(), INTERVAL 1 HOUR)")
        cantidad = cur.rowcount
        conn.commit()
    return cantidad
