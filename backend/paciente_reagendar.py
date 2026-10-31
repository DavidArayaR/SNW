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
from zoneinfo import ZoneInfo

import pymysql
import reagendar_batches

from db import conectar, config_all
from telefono import normalizar_telefono
from whatsapp_channels import catalogo_centros

CSV_MAX_BYTES = 5 * 1024 * 1024
CSV_MAX_FILAS = 10000
CSV_CARGA_MAX_BYTES = 50 * 1024 * 1024
CSV_CARGA_MAX_FILAS = 100000
REQUERIDAS = {"fecha", "ventana", "clinica_nombre", "reservo_cita_uuid",
             "paciente_nombre", "inicio_cita"}
# Nombres del sistema de origen para clínicas registradas con otro nombre en SNW.
ALIAS_CLINICAS_ORIGEN = {
    "mas vida y salud": "VS",
    "mentalidad saludable": "MS",
}
ZONA_CHILE = ZoneInfo("America/Santiago")
UTC = dt.timezone.utc
RETRASO_CARGA = dt.timedelta(minutes=3)


def programacion_carga(fecha: dt.date, ventana: str,
                       recibido: dt.datetime) -> dt.datetime | None:
    """Devuelve la hora de despacho UTC, solo para la carga habitual válida."""
    if recibido.tzinfo is None:
        raise ValueError("recibido debe tener zona horaria")
    local = recibido.astimezone(ZONA_CHILE)
    if local.date() != fecha:
        return None
    horarios = {"08-14": ((14, 0), (14, 30)), "14-20": ((20, 0), (20, 30))}
    horario = horarios.get(ventana)
    if not horario:
        return None
    desde = dt.datetime.combine(fecha, dt.time(*horario[0]), ZONA_CHILE)
    salida = dt.datetime.combine(fecha, dt.time(*horario[1]), ZONA_CHILE)
    return salida.astimezone(UTC).replace(tzinfo=None) if desde <= local < salida else None


def programacion_tras_carga(programado: dt.datetime | None,
                           carga_finalizada_utc: dt.datetime) -> dt.datetime | None:
    """Respeta la salida habitual y deja tres minutos completos para revisar."""
    if programado is None:
        return None
    if carga_finalizada_utc.tzinfo is not None:
        carga_finalizada_utc = carga_finalizada_utc.astimezone(UTC).replace(tzinfo=None)
    return max(programado, carga_finalizada_utc + RETRASO_CARGA)


def fecha_local_a_utc(valor: str) -> dt.datetime:
    """Interpreta fecha/hora de Chile; rechaza horas inexistentes o ambiguas."""
    local_sin_zona = dt.datetime.strptime(valor, "%Y-%m-%d %H:%M")
    primera = local_sin_zona.replace(tzinfo=ZONA_CHILE, fold=0)
    segunda = local_sin_zona.replace(tzinfo=ZONA_CHILE, fold=1)
    if primera.utcoffset() != segunda.utcoffset():
        raise ValueError("Esa hora es ambigua o no existe por el cambio de horario de Chile.")
    vuelta = primera.astimezone(UTC).astimezone(ZONA_CHILE).replace(tzinfo=None)
    if vuelta != local_sin_zona:
        raise ValueError("Esa hora no existe por el cambio de horario de Chile.")
    return primera.astimezone(UTC)


class CsvInvalido(ValueError):
    pass


class ModoImportacionIncompatible(ValueError):
    """Un UUID existente no puede convertirse entre prueba y operación real."""


def _texto(valor, limite: int) -> str:
    texto = str(valor or "").strip()
    if len(texto) > limite or "\x00" in texto:
        raise ValueError("longitud_invalida")
    return texto


def _normalizar_nombre(valor: str) -> str:
    texto = unicodedata.normalize("NFKD", valor.casefold())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", texto).split())


def es_aceptacion_reagendar(texto: str) -> bool:
    """Aceptaciones breves de una cita, no coincidencias dentro de consultas largas."""
    normalizado = _normalizar_nombre(texto or "")
    return normalizado in {
        "reagendar", "quiero reagendar", "si quiero reagendar",
        "me gustaria reagendar", "si me gustaria reagendar",
        "deseo reagendar", "si deseo reagendar", "acepto reagendar",
        "quiero agendar", "si quiero agendar", "me gustaria agendar",
        "me interesa reagendar", "claro quiero reagendar",
        "si me gustaria agendar", "si quiero", "si por favor", "claro que si",
    }


def mensaje_preparado_reagendar(cita: dict) -> str:
    if not cita.get("doctor_nombre"):
        raise ValueError("Falta el médico para preparar el reagendamiento")
    return ("Sí me gustaría agendar, mi nombre es "
            f"{cita['paciente_nombre']}, con el médico: "
            f"{cita['doctor_nombre']}, clínica: {cita['clinica_nombre']}")


def variables_plantilla_reagendar(cita: dict) -> dict[str, str]:
    """Datos reales del CSV para la plantilla de esta cita."""
    nombre_completo = str(cita.get("paciente_nombre") or "").strip()
    nombre, _, apellido = nombre_completo.partition(" ")
    return {"nombre": nombre, "apellido": apellido,
            "nombre_paciente": nombre_completo,
            "nombre_doc": str(cita.get("doctor_nombre") or "").strip()}


def resolver_clinica(nombre: str, clinica_id: int | None, cfg: dict) -> str | None:
    catalogo = catalogo_centros(cfg)
    nombre_normalizado = _normalizar_nombre(nombre)
    coincidencias = [codigo for codigo, etiqueta in catalogo.items()
                     if _normalizar_nombre(etiqueta) == nombre_normalizado]
    por_nombre = coincidencias[0] if len(coincidencias) == 1 else None
    if not por_nombre and not coincidencias:
        alias = ALIAS_CLINICAS_ORIGEN.get(nombre_normalizado)
        por_nombre = alias if alias in catalogo else None
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
        if "autorizado_contacto" in columnas:
            raise CsvInvalido("El CSV usa una columna de autorización obsoleta. "
                              "El sistema de origen debe filtrar los pacientes autorizados antes de enviarlo.")
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
                centro = resolver_clinica(clinica, clinica_id, cfg)
                problema = ("CLINICA_DESCONOCIDA" if not centro else
                            "TELEFONO_INVALIDO" if not normalizado else
                            "DOCTOR_FALTANTE" if not doctor else None)
                registros.append({"fecha": fecha, "ventana": ventana,
                                  "_row": numero,
                                  "clinica_id": clinica_id, "clinica_nombre": clinica,
                                  "reservo_cita_uuid": uuid, "paciente_nombre": nombre,
                                  "paciente_telefono": telefono,
                                  "paciente_telefono_normalizado": normalizado,
                                  "doctor_nombre": doctor, "inicio_cita": inicio,
                                  "centro_codigo": centro,
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


def dividir_csv(datos: bytes) -> list[tuple[bytes, int]]:
    """Divide un CSV UTF-8 en partes de hasta 10.000 filas y 5 MB.

    Conserva el encabezado en cada parte y respeta campos entrecomillados con
    saltos de línea. El segundo valor es la primera fila lógica del original.
    """
    if len(datos) > CSV_CARGA_MAX_BYTES:
        raise CsvInvalido("El archivo supera los 50 MB.")
    try:
        lector = csv.reader(io.StringIO(datos.decode("utf-8-sig"), newline=""), strict=True)
        encabezado = next(lector, None)
        if not encabezado:
            raise CsvInvalido("El archivo CSV está vacío.")
        def escribir(fila: list[str]) -> bytes:
            salida = io.StringIO(newline="")
            csv.writer(salida, lineterminator="\n").writerow(fila)
            return salida.getvalue().encode("utf-8")

        cabecera = escribir(encabezado)
        if len(cabecera) >= CSV_MAX_BYTES:
            raise CsvInvalido("El encabezado CSV supera los 5 MB.")
        partes = []
        bloque = bytearray(cabecera)
        filas_bloque = filas_total = 0
        fila_inicial = 2
        for fila in lector:
            if not fila:  # DictReader también ignora líneas vacías.
                continue
            filas_total += 1
            if filas_total > CSV_CARGA_MAX_FILAS:
                raise CsvInvalido("El CSV supera el máximo de 100.000 filas por carga.")
            codificada = escribir(fila)
            if len(cabecera) + len(codificada) > CSV_MAX_BYTES:
                raise CsvInvalido("Una fila CSV supera los 5 MB.")
            if filas_bloque and (filas_bloque >= CSV_MAX_FILAS
                                or len(bloque) + len(codificada) > CSV_MAX_BYTES):
                partes.append((bytes(bloque), fila_inicial))
                bloque = bytearray(cabecera)
                filas_bloque = 0
                fila_inicial = filas_total + 1
            bloque.extend(codificada)
            filas_bloque += 1
        if not filas_total:
            raise CsvInvalido("El CSV no contiene registros.")
        partes.append((bytes(bloque), fila_inicial))
        return partes
    except UnicodeDecodeError as exc:
        raise CsvInvalido("El CSV debe estar codificado en UTF-8.") from exc
    except csv.Error as exc:
        raise CsvInvalido("El CSV está mal formado.") from exc


def importar_por_partes(datos: bytes, cfg: dict, *, modo_prueba: bool = False) -> dict:
    """Valida el archivo completo y luego importa bloques idempotentes."""
    partes = dividir_csv(datos)
    fechas_ventanas = set()
    total_filas = 0
    for bloque, _ in partes:
        registros, _, recibidas = analizar_csv(bloque, cfg)
        total_filas += recibidas
        fechas_ventanas.update((r["fecha"], r["ventana"]) for r in registros)
    if modo_prueba and total_filas != 1:
        raise CsvInvalido("El modo de prueba requiere un CSV de una sola cita.")
    if len(fechas_ventanas) > 1:
        raise CsvInvalido("Cada archivo debe corresponder a una sola fecha y ventana de Reservo.")

    recibido = dt.datetime.now(UTC)
    resumen = {clave: 0 for clave in ("received", "inserted", "recovered", "eligible", "alreadyExists",
                                     "alreadySent", "rejected")}
    resumen["omittedReasons"] = {}
    ids_lote, estados, horarios, errores, resultados = [], [], [], [], []
    ids_elegibles = []
    for indice, (bloque, fila_inicial) in enumerate(partes, 1):
        # Los lotes parciales quedan en revisión hasta terminar todo el archivo.
        resultado = importar(bloque, cfg, recibido=recibido, modo_prueba=modo_prueba,
                             programar_automaticamente=False)
        parcial = resultado["summary"]
        for clave in ("received", "inserted", "recovered", "eligible", "alreadyExists",
                      "alreadySent", "rejected"):
            resumen[clave] += parcial.get(clave, 0)
        for motivo, cantidad in (parcial.get("omittedReasons") or {}).items():
            resumen["omittedReasons"][motivo] = resumen["omittedReasons"].get(motivo, 0) + cantidad
        if parcial.get("batchId") is not None:
            ids_lote.append(parcial["batchId"])
            if parcial.get("eligible") and not modo_prueba:
                ids_elegibles.append(parcial["batchId"])
        if parcial.get("batchStatus"):
            estados.append(parcial["batchStatus"])
        if parcial.get("scheduledAtUtc"):
            horarios.append(parcial["scheduledAtUtc"])
        errores.extend({**error, "row": error["row"] + fila_inicial - 2}
                       for error in resultado.get("errors", []) if len(errores) < 50)
        resultados.append({"part": indice, "firstRow": fila_inicial, "summary": parcial})
    if ids_elegibles and fechas_ventanas:
        fecha, ventana = next(iter(fechas_ventanas))
        horario_base = programacion_carga(fecha, ventana, recibido)
        horario_final = programacion_tras_carga(horario_base, dt.datetime.now(UTC))
        if horario_final is not None:
            activar_lotes_importados(ids_elegibles, horario_final)
            horario_texto = horario_final.isoformat() + "Z"
            for resultado in resultados:
                parcial = resultado["summary"]
                if parcial.get("batchId") in ids_elegibles:
                    parcial["batchStatus"] = "PROGRAMADO"
                    parcial["scheduledAtUtc"] = horario_texto
            estados = [r["summary"]["batchStatus"] for r in resultados]
            horarios = [horario_texto]
    resumen.update({"parts": len(partes), "batchIds": ids_lote,
                    "batchId": ids_lote[0] if len(ids_lote) == 1 else None,
                    "batchStatus": (estados[0] if estados and len(set(estados)) == 1
                                    else "VARIOS" if estados else None),
                    "scheduledAtUtc": horarios[0] if horarios and len(set(horarios)) == 1 else None})
    return {"success": True, "testMode": modo_prueba, "summary": resumen,
            "parts": resultados, "errors": errores[:50],
            "message": "Sin registros nuevos pendientes de envío" if not resumen["eligible"]
            and not modo_prueba else None}


def activar_lotes_importados(ids_lote: list[int], programado: dt.datetime) -> None:
    """Activa juntos los lotes de una carga completa, tras la ventana de revisión."""
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("UPDATE reagendar_lotes SET estado='PROGRAMADO', programado_para=%s"
                    " WHERE id IN (" + ",".join(["%s"] * len(ids_lote)) + ")"
                    " AND estado='REVISION'", (programado, *ids_lote))
        conn.commit()


_COLUMNAS = ("fecha", "ventana", "clinica_id", "clinica_nombre", "reservo_cita_uuid",
             "paciente_nombre", "paciente_telefono", "paciente_telefono_normalizado",
             "doctor_nombre", "inicio_cita", "centro_codigo",
             "ultimo_error_codigo", "modo_prueba")


def importar(datos: bytes, cfg: dict, *, recibido: dt.datetime | None = None,
            modo_prueba: bool = False, programar_automaticamente: bool = True) -> dict:
    registros, errores, recibidas = analizar_csv(datos, cfg)
    if modo_prueba and recibidas != 1:
        raise CsvInvalido("El modo de prueba requiere un CSV de una sola cita.")
    pares = {(r["fecha"], r["ventana"]) for r in registros}
    if not pares:
        return {"success": True, "testMode": modo_prueba, "summary": {"received": recibidas, "inserted": 0,
                "alreadyExists": 0, "rejected": len(errores), "batchId": None,
                "batchStatus": None, "scheduledAtUtc": None}, "errors": errores[:50],
                "message": "Sin registros nuevos pendientes de envío"}
    if len(pares) != 1:
        raise CsvInvalido("Cada archivo debe corresponder a una sola fecha y ventana de Reservo.")
    recibido = recibido or dt.datetime.now(UTC)
    if recibido.tzinfo is None:
        raise ValueError("La recepción debe tener zona horaria")
    fecha, ventana = next(iter(pares))
    programado = None if modo_prueba else programacion_carga(fecha, ventana, recibido)
    insertados = recuperados = duplicados = ya_enviados = elegibles = 0
    motivos_omitidos: dict[str, int] = {}
    from supresion import huella_telefono
    with conectar() as conn, conn.cursor() as cur:
        lote_id = None
        if not modo_prueba:
            cur.execute(
                "INSERT INTO reagendar_lotes (fecha, ventana, recibido_en, estado)"
                " VALUES (%s, %s, %s, 'REVISION')",
                (fecha, ventana, recibido.astimezone(UTC).replace(tzinfo=None)))
            lote_id = cur.lastrowid
        for registro in registros:
            registro["modo_prueba"] = int(modo_prueba)
            telefono = registro["paciente_telefono_normalizado"]
            cur.execute("SELECT id, lote_id, fecha, ventana, clinica_nombre, modo_prueba,"
                        " estado_envio, ultimo_error_codigo,"
                        " paciente_telefono_normalizado, whatsapp_opt_out, intentos_envio,"
                        " reserva_token, ultimo_intento_en, envio_id, log_envio_id,"
                        " whatsapp_message_id FROM paciente_reagendar"
                        " WHERE reservo_cita_uuid=%s FOR UPDATE", (registro["reservo_cita_uuid"],))
            existente = cur.fetchone()
            if existente:
                if bool(existente["modo_prueba"]) != modo_prueba:
                    raise ModoImportacionIncompatible(
                        "El identificador de cita ya pertenece a otro modo de importación.")
                # Solo una nueva importación explícita puede recuperar una cita
                # omitida por el nombre de la clínica. Nunca reactiva envíos
                # intentados, bajas ni un teléfono reservado por otra cita.
                if (not modo_prueba and telefono and not registro["ultimo_error_codigo"]
                        and existente["fecha"] == registro["fecha"]
                        and existente["ventana"] == registro["ventana"]
                        and _normalizar_nombre(existente["clinica_nombre"])
                        == _normalizar_nombre(registro["clinica_nombre"])
                        and existente["estado_envio"] == "OMITIDO"
                        and existente["ultimo_error_codigo"] == "CLINICA_DESCONOCIDA"
                        and existente["paciente_telefono_normalizado"] == telefono
                        and not any(existente[clave] for clave in (
                            "whatsapp_opt_out", "intentos_envio", "reserva_token",
                            "ultimo_intento_en", "envio_id", "log_envio_id",
                            "whatsapp_message_id"))):
                    cur.execute("SELECT 1 FROM supresion_telefonos"
                                " WHERE huella=%s AND estado <> 'exento' FOR UPDATE",
                                (huella_telefono(telefono),))
                    suprimido = cur.fetchone()
                    cur.execute("SELECT 1 FROM paciente_reagendar"
                                " WHERE paciente_telefono_normalizado=%s"
                                " AND whatsapp_opt_out=1 LIMIT 1", (telefono,))
                    baja = cur.fetchone()
                    cur.execute("SELECT cita_id, estado FROM reagendar_destinatarios"
                                " WHERE telefono=%s AND modo_prueba=0 FOR UPDATE", (telefono,))
                    destino = cur.fetchone()
                    if (not suprimido and not baja and destino
                            and destino["cita_id"] == existente["id"]
                            and destino["estado"] == "PENDIENTE"):
                        cur.execute("UPDATE paciente_reagendar SET lote_id=%s,"
                                    " centro_codigo=%s, estado_envio='PENDIENTE',"
                                    " ultimo_error_codigo=NULL WHERE id=%s"
                                    " AND estado_envio='OMITIDO'"
                                    " AND ultimo_error_codigo='CLINICA_DESCONOCIDA'"
                                    " AND intentos_envio=0 AND whatsapp_opt_out=0",
                                    (lote_id, registro["centro_codigo"], existente["id"]))
                        if cur.rowcount == 1:
                            if existente["lote_id"] is not None:
                                cur.execute("UPDATE reagendar_lotes"
                                            " SET creados=GREATEST(0, creados-1) WHERE id=%s",
                                            (existente["lote_id"],))
                            recuperados += 1
                            elegibles += 1
                            continue
                duplicados += 1
                ya_enviados += int(existente["estado_envio"] == "ENVIADO")
                continue
            if telefono:
                cur.execute("SELECT 1 FROM supresion_telefonos"
                            " WHERE huella=%s AND estado <> 'exento' FOR UPDATE",
                            (huella_telefono(telefono),))
                if cur.fetchone():
                    errores.append({"row": registro["_row"], "code": "paciente_eliminado"})
                    continue
                cur.execute("SELECT 1 FROM paciente_reagendar"
                            " WHERE paciente_telefono_normalizado=%s AND whatsapp_opt_out=1"
                            " LIMIT 1", (telefono,))
                if cur.fetchone():
                    registro["ultimo_error_codigo"] = "PACIENTE_DE_BAJA"
                cur.execute("SELECT d.estado, r.estado_envio AS envio_estado"
                            " FROM reagendar_destinatarios d"
                            " LEFT JOIN paciente_reagendar r ON r.id=d.cita_id"
                            " WHERE d.telefono=%s AND d.modo_prueba=%s FOR UPDATE",
                            (telefono, int(modo_prueba)))
                destinatario = cur.fetchone()
                if destinatario:
                    duplicados += 1
                    enviado = (destinatario["estado"] == "ENVIADO"
                               or destinatario["envio_estado"] == "ENVIADO")
                    ya_enviados += int(enviado)
                    if enviado and destinatario["estado"] != "ENVIADO":
                        cur.execute("UPDATE reagendar_destinatarios SET estado='ENVIADO'"
                                    " WHERE telefono=%s AND modo_prueba=%s",
                                    (telefono, int(modo_prueba)))
                    continue
                # Se reserva antes de insertar la cita: la clave única serializa
                # importaciones concurrentes del mismo teléfono en MySQL.
                cur.execute("INSERT IGNORE INTO reagendar_destinatarios"
                            " (telefono, modo_prueba, cita_id) VALUES (%s, %s, 0)",
                            (telefono, int(modo_prueba)))
                if not cur.rowcount:
                    duplicados += 1
                    continue
            try:
                cur.execute(
                    "INSERT INTO paciente_reagendar (lote_id," + ",".join(_COLUMNAS) + ")"
                    " VALUES (" + ",".join(["%s"] * (len(_COLUMNAS) + 1)) + ")",
                    (lote_id, *(registro[col] for col in _COLUMNAS)),
                )
                cita_id = cur.lastrowid
                if telefono:
                    cur.execute("UPDATE reagendar_destinatarios SET cita_id=%s"
                                " WHERE telefono=%s AND modo_prueba=%s",
                                (cita_id, telefono, int(modo_prueba)))
                if registro["ultimo_error_codigo"] or modo_prueba:
                    cur.execute("UPDATE paciente_reagendar SET estado_envio='OMITIDO'"
                                " WHERE id=%s", (cita_id,))
                    if registro["ultimo_error_codigo"]:
                        codigo = registro["ultimo_error_codigo"]
                        motivos_omitidos[codigo] = motivos_omitidos.get(codigo, 0) + 1
                else:
                    elegibles += 1
                insertados += 1
            except pymysql.err.IntegrityError as exc:
                if exc.args[0] != 1062:
                    raise
                if telefono:
                    cur.execute("DELETE FROM reagendar_destinatarios"
                                " WHERE telefono=%s AND modo_prueba=%s AND cita_id=0",
                                (telefono, int(modo_prueba)))
                cur.execute("SELECT modo_prueba FROM paciente_reagendar"
                            " WHERE reservo_cita_uuid=%s", (registro["reservo_cita_uuid"],))
                existente = cur.fetchone()
                if existente is None or bool(existente["modo_prueba"]) != modo_prueba:
                    raise ModoImportacionIncompatible(
                        "El identificador de cita ya pertenece a otro modo de importación.") from exc
                # Conserva íntegros estado, intentos, emisor e ID de Meta.
                duplicados += 1
        if modo_prueba:
            estado = None
        else:
            # En archivos divididos solo se activa al completar todas las partes.
            if programar_automaticamente:
                programado = programacion_tras_carga(programado, dt.datetime.now(UTC))
            vigente = elegibles > 0 and programado is not None and programar_automaticamente
            estado = "PROGRAMADO" if vigente else "REVISION" if insertados + recuperados else "COMPLETADO"
            cur.execute(
                "UPDATE reagendar_lotes SET estado=%s, programado_para=%s,"
                " creados=%s, rechazados=%s, repetidos=%s, recibidos=%s WHERE id=%s",
                (estado, programado if vigente else None, insertados + recuperados, len(errores),
                 duplicados, recibidas, lote_id))
        conn.commit()
    return {"success": True, "testMode": modo_prueba,
            "summary": {"received": recibidas, "inserted": insertados, "recovered": recuperados,
            "eligible": elegibles, "alreadyExists": duplicados,
            "alreadySent": ya_enviados, "rejected": len(errores),
            "omittedReasons": motivos_omitidos,
            "batchId": lote_id, "batchStatus": estado,
            "scheduledAtUtc": programado.isoformat() + "Z" if lote_id and estado == "PROGRAMADO" else None},
            "errors": errores[:50],
            "message": ("Sin registros nuevos pendientes de envío" if not elegibles and not modo_prueba
                        else None)}


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
            "SELECT r.* FROM paciente_reagendar r"
            " JOIN reagendar_lotes l ON l.id=r.lote_id"
            " WHERE r.estado_envio IN ('PENDIENTE','REINTENTO')"
            " AND r.whatsapp_opt_out=0 AND r.modo_prueba=0"
            " AND r.ultimo_error_codigo IS NULL"
            " AND r.paciente_telefono_normalizado IS NOT NULL"
            f" AND r.centro_codigo IN ({marcadores})"
            " AND l.estado IN ('PROGRAMADO','APLAZADO','EN_CURSO')"
            " AND l.programado_para <= UTC_TIMESTAMP()"
            " AND (r.proximo_reintento_en IS NULL OR r.proximo_reintento_en <= UTC_TIMESTAMP())"
            " ORDER BY r.id LIMIT 1 FOR UPDATE", tuple(centros))
        fila = cur.fetchone()
        if not fila:
            return None
        telefono = fila["paciente_telefono_normalizado"]
        cur.execute("INSERT IGNORE INTO reagendar_destinatarios"
                    " (telefono, modo_prueba, cita_id) VALUES (%s, 0, %s)",
                    (telefono, fila["id"]))
        cur.execute("SELECT cita_id, estado FROM reagendar_destinatarios"
                    " WHERE telefono=%s AND modo_prueba=0 FOR UPDATE", (telefono,))
        destinatario = cur.fetchone()
        if (destinatario["cita_id"] != fila["id"]
                or destinatario["estado"] in ("ENVIADO", "ERROR")):
            cur.execute("UPDATE paciente_reagendar SET estado_envio='OMITIDO',"
                        " ultimo_error_codigo='PACIENTE_DUPLICADO' WHERE id=%s",
                        (fila["id"],))
            conn.commit()
            return None
        if destinatario["estado"] == "PROCESANDO":
            return None
        token = secrets.token_hex(16)
        cur.execute("UPDATE reagendar_destinatarios SET estado='PROCESANDO'"
                    " WHERE telefono=%s AND modo_prueba=0", (telefono,))
        cur.execute(
            "UPDATE paciente_reagendar SET estado_envio='PROCESANDO', reserva_token=%s,"
            " reservado_en=UTC_TIMESTAMP(), ultimo_intento_en=UTC_TIMESTAMP(),"
            " intentos_envio=intentos_envio+1 WHERE id=%s",
            (token, fila["id"]))
        cur.execute("UPDATE reagendar_lotes SET estado='EN_CURSO'"
                    " WHERE id=%s AND estado IN ('PROGRAMADO','APLAZADO')", (fila["lote_id"],))
        conn.commit()
    fila["reserva_token"] = token
    fila["intentos_envio"] += 1
    return fila


def motivo_no_reintentar(cita: dict) -> str | None:
    """Solo habilita fallos definitivos, nunca entregas o POST de resultado incierto."""
    if cita.get("modo_prueba") or cita.get("whatsapp_opt_out"):
        return "Registro de prueba de importación o paciente dado de baja."
    if not cita.get("paciente_telefono_normalizado") or not cita.get("centro_codigo"):
        return "Falta el teléfono o la clínica del paciente."
    if cita.get("estado_envio") not in ("ERROR", "ENVIADO"):
        return "Este registro no tiene un error pendiente de reintento."
    if cita.get("ultimo_error_codigo") in ("RESULTADO_INCIERTO", "META_TEMPORAL"):
        return "El último intento tiene un resultado incierto; revisa su entrega en Meta antes de reenviar."
    if cita.get("estado_whatsapp") in ("delivered", "read"):
        return "WhatsApp ya confirmó la recepción del mensaje."
    if cita.get("whatsapp_message_id"):
        if cita.get("estado_whatsapp") != "failed":
            return "Meta aceptó el mensaje, pero no confirmó un fallo de entrega."
    elif cita.get("ultimo_error_codigo") not in (
            "META_RECHAZO", "ENVIO_DESACTIVADO", "CANAL_NO_DISPONIBLE", "PLANTILLA_INVALIDA"):
        return "El resultado es incierto o requiere corregir los datos antes de reenviar."
    return None


def preparar_reintentos_manuales(ids: list[int], validar_cita) -> dict:
    """Rehabilita citas concretas en su cola persistente; no llama a Meta."""
    marcadores = ",".join(["%s"] * len(ids))
    from supresion import huella_telefono
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("SELECT r.*, le.estado_whatsapp FROM paciente_reagendar r"
                    " LEFT JOIN log_envios le ON le.id=r.log_envio_id"
                    f" WHERE r.id IN ({marcadores}) ORDER BY r.id FOR UPDATE", tuple(ids))
        citas = cur.fetchall()
        if len(citas) != len(ids):
            raise ValueError("Hay registros que ya no están disponibles. Actualiza la lista.")
        for cita in citas:
            motivo = motivo_no_reintentar(cita)
            if motivo:
                raise ValueError(f"Registro #{cita['id']}: {motivo}")
            validar_cita(cita)
            telefono = cita["paciente_telefono_normalizado"]
            cur.execute("SELECT 1 FROM supresion_telefonos WHERE huella=%s"
                        " AND estado <> 'exento' LIMIT 1", (huella_telefono(telefono),))
            if cur.fetchone():
                raise ValueError("La selección incluye un paciente eliminado.")
            cur.execute("SELECT 1 FROM paciente_reagendar WHERE paciente_telefono_normalizado=%s"
                        " AND whatsapp_opt_out=1 LIMIT 1", (telefono,))
            if cur.fetchone():
                raise ValueError("La selección incluye un paciente dado de baja.")
            cur.execute("SELECT 1 FROM log_envios le JOIN envios e ON e.id=le.envio_id"
                        " WHERE le.numero_telefono=%s AND e.base_datos='paciente_reagendar'"
                        " AND le.estado_whatsapp IN ('delivered','read') LIMIT 1", (telefono,))
            if cur.fetchone():
                raise ValueError("Un paciente seleccionado ya recibió un reagendamiento.")
            cur.execute("SELECT cita_id, estado FROM reagendar_destinatarios"
                        " WHERE telefono=%s AND modo_prueba=0 FOR UPDATE", (telefono,))
            destino = cur.fetchone()
            if not destino or destino["cita_id"] != cita["id"] or destino["estado"] == "PROCESANDO":
                raise ValueError("Un destinatario está ocupado por otro envío. Actualiza la lista.")
            cur.execute("SELECT id, programado_para FROM reagendar_lotes WHERE id=%s FOR UPDATE",
                        (cita["lote_id"],))
            lote = cur.fetchone()
            if not lote:
                raise ValueError("No se encontró la carga de este reagendamiento.")
            if lote.get("programado_para") and lote["programado_para"] > dt.datetime.now(UTC).replace(tzinfo=None):
                raise ValueError("La carga está aplazada para una fecha futura; espera esa fecha o modifica su programación.")
        for cita in citas:
            cur.execute("UPDATE paciente_reagendar SET estado_envio='PENDIENTE',"
                        " ultimo_error_codigo=NULL, ultimo_error_detalle=NULL,"
                        " reserva_token=NULL, reservado_en=NULL, proximo_reintento_en=NULL"
                        " WHERE id=%s", (cita["id"],))
            cur.execute("UPDATE reagendar_destinatarios SET estado='PENDIENTE'"
                        " WHERE telefono=%s AND modo_prueba=0 AND cita_id=%s",
                        (cita["paciente_telefono_normalizado"], cita["id"]))
            cur.execute("UPDATE reagendar_lotes SET estado='PROGRAMADO',"
                        " programado_para=COALESCE(programado_para, UTC_TIMESTAMP()) WHERE id=%s",
                        (cita["lote_id"],))
        conn.commit()
    return {"encolados": len(citas)}


def aplazar_lote(lote_id: int, nueva_fecha_utc: dt.datetime,
                 usuario: str, motivo: str = "") -> dict:
    """No cambia citas ya reclamadas; serializa el aplazamiento con el claim."""
    if nueva_fecha_utc.tzinfo is None or nueva_fecha_utc <= dt.datetime.now(UTC):
        raise ValueError("La nueva fecha debe estar en el futuro.")
    nueva = nueva_fecha_utc.astimezone(UTC).replace(tzinfo=None)
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, estado FROM reagendar_lotes WHERE id=%s FOR UPDATE", (lote_id,))
        lote = cur.fetchone()
        if not lote:
            raise LookupError("Carga no encontrada.")
        if lote["estado"] not in ("PROGRAMADO", "APLAZADO", "REVISION"):
            raise ValueError("Esta carga ya comenzó a despacharse; no puede aplazarse completa.")
        cur.execute("UPDATE reagendar_lotes SET estado='APLAZADO', programado_para=%s,"
                    " aplazado_en=UTC_TIMESTAMP(), aplazado_por=%s, motivo_aplazamiento=%s"
                    " WHERE id=%s", (nueva, usuario[:100], motivo[:255] or None, lote_id))
        conn.commit()
    return {"id": lote_id, "estado": "APLAZADO", "programado_para": nueva.isoformat() + "Z"}


def eliminar_carga_sin_envios(lote_id: int) -> dict:
    """Retira una carga remota antes de cualquier intento de envío.

    Bloquea sus citas antes de revisar el estado para serializarse con el
    despachador. Elimina también las reservas de teléfonos que creó la carga;
    una importación futura podrá volver a evaluarlos.
    """
    with conectar() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, estado_envio, intentos_envio, whatsapp_opt_out, envio_id, log_envio_id,"
            " whatsapp_message_id FROM paciente_reagendar"
            " WHERE lote_id=%s FOR UPDATE", (lote_id,))
        citas = cur.fetchall()
        cur.execute("SELECT id, estado FROM reagendar_lotes WHERE id=%s FOR UPDATE", (lote_id,))
        lote = cur.fetchone()
        if not lote:
            raise LookupError("Carga no encontrada.")
        if lote["estado"] == "EN_CURSO" or any(
                c["estado_envio"] in ("PROCESANDO", "ENVIADO")
                or c["intentos_envio"] or c["whatsapp_opt_out"]
                or c["envio_id"] or c["log_envio_id"]
                or c["whatsapp_message_id"] for c in citas):
            raise ValueError("Esta carga tiene envíos, intentos o bajas que deben conservarse.")
        cur.execute(
            "SELECT COUNT(*) AS n FROM reagendar_destinatarios d"
            " JOIN paciente_reagendar r ON r.id=d.cita_id"
            " WHERE r.lote_id=%s AND d.estado IN ('PROCESANDO','ENVIADO')", (lote_id,))
        if (cur.fetchone() or {}).get("n"):
            raise ValueError("Esta carga tiene destinatarios en envío o ya enviados.")
        cur.execute(
            "DELETE d FROM reagendar_destinatarios d"
            " JOIN paciente_reagendar r ON r.id=d.cita_id WHERE r.lote_id=%s", (lote_id,))
        cur.execute("DELETE FROM paciente_reagendar WHERE lote_id=%s", (lote_id,))
        eliminados = cur.rowcount
        cur.execute("DELETE FROM reagendar_lotes WHERE id=%s", (lote_id,))
        conn.commit()
    return {"id": lote_id, "pacientes_eliminados": eliminados}


_BORRABLE_REAGENDAR = (
    "r.estado_envio NOT IN ('PROCESANDO','ENVIADO') AND r.intentos_envio=0"
    " AND r.whatsapp_opt_out=0 AND r.reserva_token IS NULL"
    " AND r.ultimo_intento_en IS NULL AND r.envio_id IS NULL"
    " AND r.log_envio_id IS NULL AND r.whatsapp_message_id IS NULL"
    " AND (d.estado IS NULL OR d.estado NOT IN ('PROCESANDO','ENVIADO'))"
)
_ORIGEN_BORRADO_REAGENDAR = (
    " paciente_reagendar r LEFT JOIN reagendar_destinatarios d ON d.cita_id=r.id"
)


def resumen_borrado_pacientes() -> dict:
    """Cuenta los registros eliminables sin mostrar datos de pacientes."""
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM paciente_reagendar")
        total = int(cur.fetchone()["n"])
        cur.execute("SELECT COUNT(*) AS n FROM" + _ORIGEN_BORRADO_REAGENDAR
                    + " WHERE " + _BORRABLE_REAGENDAR)
        eliminables = int(cur.fetchone()["n"])
    return {"eliminables": eliminables, "conservados": total - eliminables}


def eliminar_todos_sin_envio() -> dict:
    """Retira en una transacción todos los pacientes aún no contactados."""
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("SELECT r.id, r.lote_id FROM" + _ORIGEN_BORRADO_REAGENDAR
                    + " WHERE " + _BORRABLE_REAGENDAR + " FOR UPDATE")
        filas = cur.fetchall()
        ids = [fila["id"] for fila in filas]
        por_lote: dict[int, int] = {}
        for fila in filas:
            if fila["lote_id"] is not None:
                por_lote[fila["lote_id"]] = por_lote.get(fila["lote_id"], 0) + 1
        for inicio in range(0, len(ids), 1000):
            bloque = ids[inicio:inicio + 1000]
            marcadores = ",".join(["%s"] * len(bloque))
            cur.execute("DELETE FROM reagendar_destinatarios WHERE cita_id IN ("
                        + marcadores + ")", tuple(bloque))
            cur.execute("DELETE FROM paciente_reagendar WHERE id IN ("
                        + marcadores + ")", tuple(bloque))
        for lote_id, cantidad in por_lote.items():
            cur.execute("UPDATE reagendar_lotes SET creados=GREATEST(0, creados-%s)"
                        " WHERE id=%s", (cantidad, lote_id))
            cur.execute(
                "SELECT COUNT(*) AS n FROM paciente_reagendar"
                " WHERE lote_id=%s AND estado_envio IN ('PENDIENTE','REINTENTO','PROCESANDO')"
                " AND ultimo_error_codigo IS NULL AND paciente_telefono_normalizado IS NOT NULL"
                " AND centro_codigo IS NOT NULL", (lote_id,))
            if cur.fetchone()["n"] == 0:
                cur.execute("UPDATE reagendar_lotes SET estado='COMPLETADO',"
                            " programado_para=NULL WHERE id=%s", (lote_id,))
        conn.commit()
    return {"pacientes_eliminados": len(ids)}


def _telefonos_prueba_configurados() -> tuple[str, ...]:
    cfg = config_all(refresh=True)
    numeros = {normalizar_telefono(valor.strip())
               for clave in ("numeros_prueba_dev", "numeros_prueba_prod")
               for valor in (cfg.get(clave) or "").split(",") if valor.strip()}
    return tuple(sorted(numero for numero in numeros if numero))


def _condicion_borrado_pruebas(numeros: tuple[str, ...]) -> tuple[str, tuple]:
    if not numeros:
        return "1=0", ()
    marcadores = ",".join(["%s"] * len(numeros))
    condicion = f"r.paciente_telefono_normalizado IN ({marcadores})"
    return condicion, numeros


def resumen_borrado_pruebas() -> dict:
    """Cuenta citas de números de prueba, independientemente de su estado."""
    condicion, args = _condicion_borrado_pruebas(_telefonos_prueba_configurados())
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM paciente_reagendar r WHERE " + condicion, args)
        return {"eliminables": int(cur.fetchone()["n"])}


def eliminar_pruebas() -> dict:
    """Borrado manual de citas de prueba en cualquier estado; conserva el historial."""
    condicion, args = _condicion_borrado_pruebas(_telefonos_prueba_configurados())
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("SELECT r.id, r.lote_id FROM paciente_reagendar r WHERE " + condicion
                    + " ORDER BY r.id FOR UPDATE", args)
        filas = cur.fetchall()
        ids = [fila["id"] for fila in filas]
        for inicio in range(0, len(ids), 1000):
            bloque = ids[inicio:inicio + 1000]
            marcadores = ",".join(["%s"] * len(bloque))
            cur.execute("DELETE FROM reagendar_destinatarios WHERE cita_id IN ("
                        + marcadores + ")", tuple(bloque))
            cur.execute("DELETE FROM paciente_reagendar WHERE id IN ("
                        + marcadores + ")", tuple(bloque))
        for lote_id in {fila["lote_id"] for fila in filas if fila["lote_id"] is not None}:
            cur.execute(
                "UPDATE reagendar_lotes l SET estado='COMPLETADO', programado_para=NULL"
                " WHERE l.id=%s AND NOT EXISTS (SELECT 1 FROM paciente_reagendar r"
                " WHERE r.lote_id=l.id AND r.estado_envio IN ('PENDIENTE','REINTENTO','PROCESANDO')"
                " AND r.ultimo_error_codigo IS NULL)", (lote_id,))
        conn.commit()
    return {"pacientes_eliminados": len(ids)}


def eliminar_paciente_sin_envio(cita_id: int) -> dict:
    """Elimina una cita antes de reclamarla; libera su número para futuras cargas."""
    with conectar() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, lote_id, estado_envio, intentos_envio, whatsapp_opt_out,"
            " reserva_token, ultimo_intento_en, envio_id, log_envio_id,"
            " whatsapp_message_id FROM paciente_reagendar"
            " WHERE id=%s FOR UPDATE", (cita_id,))
        cita = cur.fetchone()
        if not cita:
            raise LookupError("Paciente de reagendamiento no encontrado.")
        if (cita["estado_envio"] in ("PROCESANDO", "ENVIADO")
                or cita["intentos_envio"] or cita["whatsapp_opt_out"]
                or cita["reserva_token"] or cita["ultimo_intento_en"]
                or cita["envio_id"] or cita["log_envio_id"]
                or cita["whatsapp_message_id"]):
            raise ValueError("Este paciente ya tiene envíos, intentos o una baja que deben conservarse.")
        lote_id = cita["lote_id"]
        if lote_id is not None:
            cur.execute("SELECT id FROM reagendar_lotes WHERE id=%s FOR UPDATE", (lote_id,))
            if not cur.fetchone():
                raise ValueError("No se encontró la carga del paciente.")
        cur.execute("SELECT estado FROM reagendar_destinatarios WHERE cita_id=%s FOR UPDATE",
                    (cita_id,))
        destinatario = cur.fetchone()
        if destinatario and destinatario["estado"] in ("PROCESANDO", "ENVIADO"):
            raise ValueError("El mensaje de este paciente ya está en proceso o fue enviado.")
        cur.execute("DELETE FROM reagendar_destinatarios WHERE cita_id=%s", (cita_id,))
        cur.execute("DELETE FROM paciente_reagendar WHERE id=%s", (cita_id,))
        if lote_id is not None:
            cur.execute("UPDATE reagendar_lotes SET creados=GREATEST(0, creados-1) WHERE id=%s",
                        (lote_id,))
            cur.execute(
                "SELECT COUNT(*) AS n FROM paciente_reagendar"
                " WHERE lote_id=%s AND estado_envio IN ('PENDIENTE','REINTENTO','PROCESANDO')"
                " AND ultimo_error_codigo IS NULL AND paciente_telefono_normalizado IS NOT NULL"
                " AND centro_codigo IS NOT NULL", (lote_id,))
            if cur.fetchone()["n"] == 0:
                cur.execute("UPDATE reagendar_lotes SET estado='COMPLETADO',"
                            " programado_para=NULL WHERE id=%s", (lote_id,))
        conn.commit()
    return {"id": cita_id, "lote_id": lote_id, "eliminado": True}


def completar_lote_si_corresponde(lote_id: int) -> None:
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("SELECT estado FROM reagendar_lotes WHERE id=%s FOR UPDATE", (lote_id,))
        lote = cur.fetchone() or {}
        if lote.get("estado") != "EN_CURSO":
            return
        cur.execute("SELECT COUNT(*) AS n FROM paciente_reagendar"
                    " WHERE lote_id=%s AND estado_envio IN ('PENDIENTE','REINTENTO','PROCESANDO')"
                    " AND ultimo_error_codigo IS NULL"
                    " AND paciente_telefono_normalizado IS NOT NULL"
                    " AND centro_codigo IS NOT NULL", (lote_id,))
        if cur.fetchone()["n"] == 0:
            cur.execute("UPDATE reagendar_lotes SET estado='COMPLETADO' WHERE id=%s", (lote_id,))
            cur.execute("SELECT envio_id FROM reagendar_batches WHERE lote_id=%s AND envio_id IS NOT NULL",
                        (lote_id,))
            for batch in cur.fetchall():
                reagendar_batches.guardar_resumen(cur, batch["envio_id"])
        conn.commit()


def registrar_respuesta_reagendar(telefono: str, phone_id: str, mensaje_id: str,
                                  referencia_id: str | None, texto: str,
                                  *, declina: str | None = None) -> dict | None:
    """Asocia una respuesta al envío exacto (o a una consulta en curso).

    No consume respuestas genéricas sin contexto: una conversación de otra
    campaña no puede heredar los datos de una cita por compartir teléfono.
    """
    if not mensaje_id or not phone_id or not telefono:
        return None
    with conectar() as conn, conn.cursor() as cur:
        cita = None
        if referencia_id:
            cur.execute(
                "SELECT * FROM paciente_reagendar WHERE whatsapp_message_id=%s"
                " AND paciente_telefono_normalizado=%s AND whatsapp_phone_number_id=%s"
                " AND estado_envio='ENVIADO' ORDER BY id DESC LIMIT 1",
                (referencia_id, telefono, phone_id))
            cita = cur.fetchone()
            if not cita:
                cur.execute(
                    "SELECT r.* FROM reagendar_interacciones i"
                    " JOIN paciente_reagendar r ON r.id=i.cita_id"
                    " WHERE i.respuesta_id=%s AND r.paciente_telefono_normalizado=%s"
                    " AND r.whatsapp_phone_number_id=%s AND r.estado_envio='ENVIADO'"
                    " ORDER BY r.id DESC LIMIT 1", (referencia_id, telefono, phone_id))
                cita = cur.fetchone()
            if not cita:
                return None
        if not cita:
            # Un texto nuevo sin contexto solo se atribuye al reagendamiento
            # si fue el último mensaje saliente de este número y clínica.
            if not referencia_id and es_aceptacion_reagendar(texto):
                cur.execute(
                    "SELECT r.* FROM log_envios le"
                    " LEFT JOIN paciente_reagendar r ON r.log_envio_id=le.id"
                    " WHERE le.numero_telefono=%s AND le.wa_phone_id=%s"
                    " AND le.estado_envio='enviado'"
                    " AND le.envio_id IS NOT NULL"
                    " AND le.plantilla_clave NOT IN ('respuesta','ajuste_manual')"
                    " AND le.fecha_hora >= DATE_SUB(NOW(), INTERVAL 7 DAY)"
                    " ORDER BY le.id DESC LIMIT 1", (telefono, phone_id))
                ultima = cur.fetchone()
                cita = ultima if ultima and ultima.get("id") is not None else None
        if not cita and not es_aceptacion_reagendar(texto):
            cur.execute(
                "SELECT r.* FROM reagendar_flujo f JOIN paciente_reagendar r ON r.id=f.cita_id"
                " WHERE r.paciente_telefono_normalizado=%s AND r.whatsapp_phone_number_id=%s"
                " AND f.estado='ESPERANDO_CONSULTA'"
                " AND f.actualizado_en >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL 24 HOUR)"
                " ORDER BY f.actualizado_en DESC LIMIT 1",
                (telefono, phone_id))
            cita = cur.fetchone()
        if not cita:
            if declina:
                return None
            cur.execute(
                "SELECT * FROM paciente_reagendar WHERE paciente_telefono_normalizado=%s"
                " AND whatsapp_phone_number_id=%s AND whatsapp_opt_out=1"
                " ORDER BY id DESC LIMIT 1", (telefono, phone_id))
            cita = cur.fetchone()
            if cita:
                cur.execute("UPDATE paciente_reagendar SET whatsapp_opt_out=0"
                            " WHERE paciente_telefono_normalizado=%s", (telefono,))
                conn.commit()
                return {"accion": "REACTIVADO"}
        if not cita:
            return None
        cur.execute("SELECT 1 FROM reagendar_interacciones WHERE mensaje_id=%s", (mensaje_id[:255],))
        if cur.fetchone():
            return {"accion": "DUPLICADO"}
        cur.execute("SELECT estado FROM reagendar_flujo WHERE cita_id=%s FOR UPDATE", (cita["id"],))
        flujo = cur.fetchone() or {}
        if declina:
            accion, estado = declina, "FINALIZADO"
        elif flujo.get("estado") in ("CONTACTO_OFRECIDO", "FINALIZADO"):
            return None
        elif es_aceptacion_reagendar(texto):
            accion, estado = "REAGENDAR", "CONTACTO_OFRECIDO"
        elif flujo.get("estado") == "ESPERANDO_CONSULTA":
            accion, estado = "CONSULTA", "CONTACTO_OFRECIDO"
        elif ("?" in texto or "¿" in texto) and len(texto.strip()) >= 15:
            accion, estado = "CONSULTA", "CONTACTO_OFRECIDO"
        else:
            accion, estado = "SOLICITAR_CONSULTA", "ESPERANDO_CONSULTA"
        preparado = mensaje_preparado_reagendar(cita) if accion == "REAGENDAR" else None
        cur.execute(
            "INSERT IGNORE INTO reagendar_interacciones"
            " (mensaje_id, cita_id, accion, texto, mensaje_preparado)"
            " VALUES (%s, %s, %s, %s, %s)",
            (mensaje_id[:255], cita["id"], accion, texto[:2000], preparado))
        if cur.rowcount != 1:
            return {"accion": "DUPLICADO"}
        cur.execute(
            "INSERT INTO reagendar_flujo (cita_id, estado, actualizado_en, ultimo_mensaje_id)"
            " VALUES (%s, %s, UTC_TIMESTAMP(), %s)"
            " ON DUPLICATE KEY UPDATE estado=VALUES(estado),"
            " actualizado_en=VALUES(actualizado_en), ultimo_mensaje_id=VALUES(ultimo_mensaje_id)",
            (cita["id"], estado, mensaje_id[:255]))
        if accion == "BAJA":
            cur.execute("UPDATE paciente_reagendar SET whatsapp_opt_out=1"
                        " WHERE paciente_telefono_normalizado=%s", (telefono,))
        if cita.get("log_envio_id"):
            cur.execute("UPDATE log_envios SET respuesta=%s WHERE id=%s"
                        " AND wa_phone_id=%s AND numero_telefono=%s",
                        ("baja" if accion == "BAJA" else "respondio",
                         cita["log_envio_id"], phone_id, telefono))
        conn.commit()
    return {"accion": accion, "cita": cita, "texto": texto, "mensaje_id": mensaje_id,
            "mensaje_preparado": preparado}


def marcar_opt_out_reagendar(telefono: str, valor: bool) -> None:
    """Replica la preferencia global del webhook en citas importadas."""
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("UPDATE paciente_reagendar SET whatsapp_opt_out=%s"
                    " WHERE paciente_telefono_normalizado=%s", (int(valor), telefono))
        conn.commit()


def marcar_respuesta_reagendar(mensaje_id: str, respuesta_id: str | None,
                                estado: str) -> None:
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("UPDATE reagendar_interacciones SET respuesta_id=%s, respuesta_estado=%s"
                    " WHERE mensaje_id=%s", (respuesta_id, estado, mensaje_id))
        if estado == "ERROR":
            cur.execute("UPDATE reagendar_flujo f JOIN reagendar_interacciones i"
                        " ON i.cita_id=f.cita_id SET f.estado='ERROR_RESPUESTA'"
                        " WHERE i.mensaje_id=%s AND f.ultimo_mensaje_id=%s",
                        (mensaje_id, mensaje_id))
        conn.commit()


def registrar_lote(cita: dict, plantilla: dict, canal: dict) -> int:
    """Comparte el contenedor del historial entre pacientes de la carga y clínica."""
    with conectar() as conn, conn.cursor() as cur:
        lote_id = reagendar_batches.asegurar_grupo(
            cur, cita["lote_id"], canal["codigo"], plantilla, canal)
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
        if cur.rowcount and cita.get("paciente_telefono_normalizado"):
            cur.execute("UPDATE reagendar_destinatarios SET estado=%s"
                        " WHERE telefono=%s AND modo_prueba=0 AND cita_id=%s"
                        " AND estado='PROCESANDO'",
                        (estado, cita["paciente_telefono_normalizado"], cita["id"]))
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
        cur.execute("UPDATE reagendar_destinatarios SET estado='ENVIADO'"
                    " WHERE telefono=%s AND modo_prueba=0 AND cita_id=%s",
                    (cita["paciente_telefono_normalizado"], cita["id"]))
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
        cur.execute("UPDATE reagendar_destinatarios d"
                    " JOIN paciente_reagendar r ON r.id=d.cita_id"
                    " SET d.estado='ERROR' WHERE d.estado='PROCESANDO'"
                    " AND r.estado_envio='ERROR'"
                    " AND r.ultimo_error_codigo='RESULTADO_INCIERTO'")
        conn.commit()
    return cantidad
