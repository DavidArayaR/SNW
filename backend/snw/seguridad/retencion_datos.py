"""Retención automática y minimización de datos personales en SNW.

Plazos iniciales aprobados para SNW:
* 365 días para datos operativos identificables y contenido de conversaciones.
* 90 días para archivos de diagnóstico en logs/.
* 730 días para auditoría de cuentas.
* 730 días para el detalle de destinatarios de campañas; los totales del lote
  viven en `envios` y no se borran con ese detalle.

Los informes de campañas se conservan sin identificadores del paciente. Las
huellas HMAC necesarias para hacer cumplir una eliminación permanente y para
evitar duplicar un reagendamiento ya enviado se mantienen sin teléfono crudo.
"""

from __future__ import annotations

import re
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

from db import LOGS_DIR, conectar, config_get, log_error

_BATCH = 500
_MAX_FILAS_POR_EJECUCION = 5000
_NOMBRE_LOG = re.compile(
    r"(?:errores|csv-remoto)-\d{4}-\d{2}-\d{2}(?:_\d{2}-\d{2}-\d{2}-\d{6}-\d+-\d+)?\.log\Z"
)
_ZONA_CHILE = ZoneInfo("America/Santiago")


def segundos_hasta_limpieza_diaria() -> float:
    ahora = dt.datetime.now(_ZONA_CHILE)
    proxima = ahora.replace(hour=3, minute=30, second=0, microsecond=0)
    if proxima <= ahora:
        proxima += dt.timedelta(days=1)
    return max(1.0, (proxima - ahora).total_seconds())


def _dias(clave: str, defecto: int, minimo: int, maximo: int) -> int:
    try:
        valor = int(config_get(clave, str(defecto)))
    except (TypeError, ValueError):
        return defecto
    return min(maximo, max(minimo, valor))


def _existe_tabla(cur, tabla: str) -> bool:
    cur.execute(
        "SELECT 1 FROM information_schema.TABLES"
        " WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s LIMIT 1", (tabla,)
    )
    return cur.fetchone() is not None


def _columnas(cur, tabla: str) -> set[str]:
    cur.execute(
        "SELECT COLUMN_NAME FROM information_schema.COLUMNS"
        " WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s", (tabla,)
    )
    return {fila["COLUMN_NAME"] for fila in cur.fetchall()}


def asegurar_esquema() -> None:
    """Instala las columnas/índices nuevos sin alterar el contenido existente."""
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("SELECT GET_LOCK('snw_retencion_esquema_v1', 10) AS tomado")
        if not (cur.fetchone() or {}).get("tomado"):
            return
        try:
            for tabla_corpus in ("corpus_respuestas_completas", "corpus_respuestas"):
                if not _existe_tabla(cur, tabla_corpus):
                    continue
                cols = _columnas(cur, tabla_corpus)
                if "recibido_en" not in cols:
                    # Las filas antiguas no tenían fecha. Se les concede el
                    # plazo completo desde esta migración, sin borrado inicial.
                    cur.execute(
                        f"ALTER TABLE `{tabla_corpus}` ADD COLUMN recibido_en DATETIME NOT NULL"
                        " DEFAULT CURRENT_TIMESTAMP"
                    )
                cur.execute(
                    "SELECT 1 FROM information_schema.STATISTICS"
                    " WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s"
                    " AND INDEX_NAME='idx_corpus_recibido' LIMIT 1", (tabla_corpus,)
                )
                if not cur.fetchone():
                    cur.execute(f"ALTER TABLE `{tabla_corpus}`"
                                " ADD INDEX idx_corpus_recibido (recibido_en)")

            cur.execute(
                "CREATE TABLE IF NOT EXISTS reagendar_destinatarios_huellas ("
                " huella CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY,"
                " creado DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
                ") ENGINE=InnoDB"
            )

            if _existe_tabla(cur, "call_center_derivaciones"):
                cols = _columnas(cur, "call_center_derivaciones")
                if "telefono" in cols:
                    cur.execute(
                        "ALTER TABLE call_center_derivaciones"
                        " MODIFY telefono VARCHAR(20) NULL"
                    )
                if "mensaje_original" in cols:
                    cur.execute(
                        "ALTER TABLE call_center_derivaciones"
                        " MODIFY mensaje_original TEXT NULL"
                    )

            # Los filtros diarios consultan estas columnas. Los índices se
            # agregan una sola vez y bajo un lock compartido entre instancias.
            for tabla in ("pacientes_dev", "pacientes_prod"):
                if _existe_tabla(cur, tabla) and "fecha_actualizacion" in _columnas(cur, tabla):
                    _asegurar_indice(cur, tabla, "idx_retencion_fecha", "fecha_actualizacion")

            from snw.pacientes.servicio_areas import tablas_de_pacientes, tabla_valida
            for tabla in tablas_de_pacientes():
                if (tabla_valida(tabla) and _existe_tabla(cur, tabla)
                        and "fecha_actualizacion" in _columnas(cur, tabla)):
                    _asegurar_indice(cur, tabla, "idx_retencion_fecha", "fecha_actualizacion")

            if _existe_tabla(cur, "paciente_reagendar"):
                _asegurar_indice(cur, "paciente_reagendar", "idx_retencion_reagendar",
                                 "actualizado_en")
            if _existe_tabla(cur, "log_envios") and "fecha_hora" in _columnas(cur, "log_envios"):
                _asegurar_indice(cur, "log_envios", "idx_retencion_fecha", "fecha_hora")
            if (_existe_tabla(cur, "reservo_lista_pacientes")
                    and "fecha_actualizacion" in _columnas(cur, "reservo_lista_pacientes")):
                _asegurar_indice(cur, "reservo_lista_pacientes", "idx_retencion_fecha",
                                 "fecha_actualizacion")
            if (_existe_tabla(cur, "reservo_pacientes")
                    and "ultima_sincronizacion_en" in _columnas(cur, "reservo_pacientes")):
                _asegurar_indice(cur, "reservo_pacientes", "idx_retencion_sync",
                                 "ultima_sincronizacion_en")
            conn.commit()
        finally:
            cur.execute("SELECT RELEASE_LOCK('snw_retencion_esquema_v1')")


def _asegurar_indice(cur, tabla: str, indice: str, columna: str) -> None:
    cur.execute(
        "SELECT 1 FROM information_schema.STATISTICS"
        " WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND INDEX_NAME=%s LIMIT 1",
        (tabla, indice),
    )
    if not cur.fetchone():
        cur.execute(f"ALTER TABLE `{tabla}` ADD INDEX `{indice}` (`{columna}`)")


def _ejecutar_paso(conn, nombre: str, funcion, resultado: dict) -> None:
    try:
        with conn.cursor() as cur:
            resultado[nombre] = int(funcion(cur) or 0)
        conn.commit()
    except Exception as exc:
        conn.rollback()
        resultado["errores"] += 1
        log_error(f"retención de datos: {nombre}", exc)


def _borrar_vencidos(cur, tabla: str, columna: str, dias: int,
                     condicion_extra: str = "") -> int:
    if not _existe_tabla(cur, tabla) or columna not in _columnas(cur, tabla):
        return 0
    borrados = 0
    while borrados < _MAX_FILAS_POR_EJECUCION:
        limite = min(_BATCH, _MAX_FILAS_POR_EJECUCION - borrados)
        cur.execute(
            f"DELETE FROM `{tabla}` WHERE `{columna}` < "
            f"DATE_SUB(NOW(), INTERVAL {int(dias)} DAY){condicion_extra} LIMIT {limite}"
        )
        n = max(0, int(cur.rowcount))
        borrados += n
        if n < limite:
            break
    return borrados


def _borrar_condicion(cur, tabla: str, condicion: str, maximo: int = _MAX_FILAS_POR_EJECUCION) -> int:
    if not _existe_tabla(cur, tabla):
        return 0
    borrados = 0
    while borrados < maximo:
        limite = min(_BATCH, maximo - borrados)
        cur.execute(f"DELETE FROM `{tabla}` WHERE {condicion} LIMIT {limite}")
        n = max(0, int(cur.rowcount))
        borrados += n
        if n < limite:
            break
    return borrados


def _anonimizar_historial(cur, dias: int) -> int:
    if not _existe_tabla(cur, "log_envios"):
        return 0
    columnas = _columnas(cur, "log_envios")
    pii = [c for c in ("paciente_id", "nombre_paciente", "numero_telefono", "mensaje",
                       "whatsapp_message_id", "descripcion_error") if c in columnas]
    if not pii or "fecha_hora" not in columnas:
        return 0
    hay_pii = " OR ".join(f"`{c}` IS NOT NULL" for c in pii)
    ids_procesados = 0
    while ids_procesados < _MAX_FILAS_POR_EJECUCION:
        limite = min(_BATCH, _MAX_FILAS_POR_EJECUCION - ids_procesados)
        cur.execute(
            "SELECT id FROM log_envios WHERE fecha_hora < "
            f"DATE_SUB(NOW(), INTERVAL {int(dias)} DAY) AND ({hay_pii})"
            f" ORDER BY id LIMIT {limite}"
        )
        ids = [int(f["id"]) for f in cur.fetchall()]
        if not ids:
            break
        marcadores = ",".join(["%s"] * len(ids))
        if _existe_tabla(cur, "respuesta_etiquetas"):
            cur.execute(
                "UPDATE respuesta_etiquetas e JOIN log_envios l ON l.id=e.log_envio_id"
                f" SET e.ofensa_detalle=NULL WHERE l.id IN ({marcadores})",
                tuple(ids),
            )
        asignaciones = ", ".join(f"`{c}`=NULL" for c in pii)
        cur.execute(f"UPDATE log_envios SET {asignaciones} WHERE id IN ({marcadores})",
                    tuple(ids))
        ids_procesados += len(ids)
        if len(ids) < limite:
            break
    return ids_procesados


def _limpiar_notas_operativas(cur, dias: int) -> int:
    """Retira texto libre antiguo que podría incluir datos de pacientes."""
    total = 0
    if _existe_tabla(cur, "envios"):
        cols = _columnas(cur, "envios")
        for col in ("comentario",):
            if col in cols:
                cur.execute(
                    f"UPDATE envios SET `{col}`=NULL WHERE fecha_hora < "
                    f"DATE_SUB(NOW(), INTERVAL {int(dias)} DAY) AND `{col}` IS NOT NULL"
                )
                total += max(0, int(cur.rowcount))
    if _existe_tabla(cur, "reagendar_lotes"):
        cols = _columnas(cur, "reagendar_lotes")
        cambios = []
        condiciones = []
        if "motivo_aplazamiento" in cols:
            cambios.append("motivo_aplazamiento=NULL")
            condiciones.append("motivo_aplazamiento IS NOT NULL")
        if "aplazado_por" in cols:
            cambios.append("aplazado_por=NULL")
            condiciones.append("aplazado_por IS NOT NULL")
        if cambios and condiciones and "recibido_en" in cols:
            cur.execute(
                "UPDATE reagendar_lotes SET " + ",".join(cambios)
                + " WHERE recibido_en < DATE_SUB(NOW(), INTERVAL %s DAY)"
                + " AND (" + " OR ".join(condiciones) + ")",
                (dias,),
            )
            total += max(0, int(cur.rowcount))
    return total


def _anonimizar_agregados_antiguos(cur, dias: int) -> int:
    """Quita datos de cuentas/usuarios sin tocar los contadores agregados."""
    total = 0
    if _existe_tabla(cur, "envios"):
        cols = _columnas(cur, "envios")
        sets = [f"`{col}`=NULL" for col in ("usuario",) if col in cols]
        if sets and "fecha_hora" in cols:
            cur.execute(
                "UPDATE envios SET " + ",".join(sets)
                + " WHERE fecha_hora < DATE_SUB(NOW(), INTERVAL %s DAY)"
                + " AND (" + " OR ".join(f"`{col}` IS NOT NULL" for col in ("usuario",)
                                             if col in cols) + ")", (dias,)
            )
            total += max(0, int(cur.rowcount))
    if _existe_tabla(cur, "envios_programados"):
        cols = _columnas(cur, "envios_programados")
        campos_usuario = ("creador", "creador_nombre", "decidido_por", "motivo")
        asignaciones = [f"`{col}`=''" for col in campos_usuario if col in cols]
        if "token" in cols:
            asignaciones.append("token=NULL")
        if "job_id" in cols:
            asignaciones.append("job_id=NULL")
        if asignaciones and "creado" in cols:
            condiciones = [f"`{col}`<>''" for col in campos_usuario if col in cols]
            condiciones.extend(f"`{col}` IS NOT NULL" for col in ("token", "job_id") if col in cols)
            cur.execute(
                "UPDATE envios_programados SET " + ",".join(asignaciones)
                + " WHERE creado < DATE_SUB(NOW(), INTERVAL %s DAY)"
                " AND estado IN ('completado','cancelado','rechazado','error')"
                " AND (" + " OR ".join(condiciones) + ")", (dias,)
            )
            total += max(0, int(cur.rowcount))
    if _existe_tabla(cur, "reservo_sincronizaciones"):
        cols = _columnas(cur, "reservo_sincronizaciones")
        asignaciones = []
        condiciones = []
        if "creado_por" in cols:
            asignaciones.append("creado_por=''")
            condiciones.append("creado_por<>''")
        if "ultimo_error" in cols:
            asignaciones.append("ultimo_error=NULL")
            condiciones.append("ultimo_error IS NOT NULL")
        if asignaciones and condiciones:
            cur.execute(
                "UPDATE reservo_sincronizaciones SET " + ",".join(asignaciones)
                + " WHERE creado_en < DATE_SUB(NOW(), INTERVAL %s DAY)"
                + " AND (" + " OR ".join(condiciones) + ")", (dias,)
            )
            total += max(0, int(cur.rowcount))
    if _existe_tabla(cur, "reservo_bases_contacto"):
        cols = _columnas(cur, "reservo_bases_contacto")
        if {"creado_por", "creado_en"}.issubset(cols):
            cur.execute(
                "UPDATE reservo_bases_contacto SET creado_por=''"
                " WHERE creado_en < DATE_SUB(NOW(), INTERVAL %s DAY)"
                " AND creado_por<>''", (dias,)
            )
            total += max(0, int(cur.rowcount))
    return total


def _limpiar_cuotas_antiguas(cur, dias: int) -> int:
    total = 0
    ventanas = (
        ("gemini_cuotas", "ventana_hora", 3600),
        ("groq_cuotas", "ventana_hora", 3600),
        ("reagendar_import_quota", "ventana", 900),
    )
    for tabla, columna, segundos in ventanas:
        if not _existe_tabla(cur, tabla) or columna not in _columnas(cur, tabla):
            continue
        umbral = f"FLOOR((UNIX_TIMESTAMP(NOW())-{int(dias) * 86400})/{segundos})"
        total += _borrar_condicion(cur, tabla, f"`{columna}` < {umbral}")
    return total


def _limpiar_recuperaciones_e_invitaciones(cur) -> int:
    total = 0
    for tabla in ("password_resets", "account_invites"):
        if not _existe_tabla(cur, tabla):
            continue
        if not {"usado", "expira"}.issubset(_columnas(cur, tabla)):
            continue
        total += _borrar_condicion(
            cur, tabla,
            "usado=1 OR expira < DATE_SUB(NOW(), INTERVAL 7 DAY)",
        )
    return total


def _anonimizar_destinatarios_programados(cur, dias: int) -> int:
    if not all(_existe_tabla(cur, t) for t in ("programado_destinatarios", "envios_programados")):
        return 0
    total = 0
    while total < _MAX_FILAS_POR_EJECUCION:
        limite = min(_BATCH, _MAX_FILAS_POR_EJECUCION - total)
        cur.execute(
            "SELECT pd.prog_id,pd.paciente_id FROM programado_destinatarios pd"
            " JOIN envios_programados ep ON ep.id=pd.prog_id"
            " WHERE ep.creado < DATE_SUB(NOW(), INTERVAL %s DAY)"
            " AND ep.estado IN ('completado','cancelado','rechazado','error')"
            " AND (pd.nombre<>'' OR pd.telefono<>'')"
            " ORDER BY pd.prog_id,pd.paciente_id LIMIT %s", (dias, limite)
        )
        filas = cur.fetchall()
        if not filas:
            break
        pares = [(int(f["prog_id"]), int(f["paciente_id"])) for f in filas]
        for prog_id, paciente_id in pares:
            cur.execute(
                "UPDATE programado_destinatarios SET nombre='',telefono=''"
                " WHERE prog_id=%s AND paciente_id=%s", (prog_id, paciente_id)
            )
        total += len(pares)
        if len(pares) < limite:
            break
    return total


def _cancelar_envios_programados_vencidos(cur, dias: int) -> int:
    if not _existe_tabla(cur, "envios_programados"):
        return 0
    total = 0
    while total < _MAX_FILAS_POR_EJECUCION:
        limite = min(_BATCH, _MAX_FILAS_POR_EJECUCION - total)
        cur.execute(
            "SELECT id FROM envios_programados WHERE creado < "
            "DATE_SUB(NOW(), INTERVAL %s DAY)"
            " AND estado NOT IN ('completado','cancelado','rechazado','error')"
            " ORDER BY id LIMIT %s", (dias, limite)
        )
        ids = [int(f["id"]) for f in cur.fetchall()]
        if not ids:
            break
        marcadores = ",".join(["%s"] * len(ids))
        cur.execute(
            "UPDATE envios_programados SET estado='cancelado',token=NULL,job_id=NULL"
            f" WHERE id IN ({marcadores})", tuple(ids)
        )
        total += max(0, int(cur.rowcount))
        if len(ids) < limite:
            break
    return total


def _borrar_detalles_historial(cur, dias: int) -> int:
    if not _existe_tabla(cur, "log_envios"):
        return 0
    total = 0
    while total < _MAX_FILAS_POR_EJECUCION:
        limite = min(_BATCH, _MAX_FILAS_POR_EJECUCION - total)
        cur.execute(
            "SELECT id FROM log_envios WHERE fecha_hora < "
            f"DATE_SUB(NOW(), INTERVAL {int(dias)} DAY) ORDER BY id LIMIT {limite}"
        )
        ids = [int(f["id"]) for f in cur.fetchall()]
        if not ids:
            break
        marcadores = ",".join(["%s"] * len(ids))
        if _existe_tabla(cur, "respuesta_etiquetas"):
            cur.execute(f"DELETE FROM respuesta_etiquetas WHERE log_envio_id IN ({marcadores})",
                        tuple(ids))
        cur.execute(f"DELETE FROM log_envios WHERE id IN ({marcadores})", tuple(ids))
        total += max(0, int(cur.rowcount))
        if len(ids) < limite:
            break
    return total


def _borrar_destinatarios_programados_vencidos(cur, dias: int) -> int:
    if not all(_existe_tabla(cur, t) for t in ("programado_destinatarios", "envios_programados")):
        return 0
    total = 0
    while total < _MAX_FILAS_POR_EJECUCION:
        limite = min(_BATCH, _MAX_FILAS_POR_EJECUCION - total)
        cur.execute(
            "SELECT pd.prog_id,pd.paciente_id FROM programado_destinatarios pd"
            " JOIN envios_programados ep ON ep.id=pd.prog_id"
            " WHERE ep.creado < DATE_SUB(NOW(), INTERVAL %s DAY)"
            " AND ep.estado IN ('completado','cancelado','rechazado','error')"
            " ORDER BY pd.prog_id,pd.paciente_id LIMIT %s", (dias, limite)
        )
        pares = [(int(f["prog_id"]), int(f["paciente_id"])) for f in cur.fetchall()]
        if not pares:
            break
        for prog_id, paciente_id in pares:
            cur.execute("DELETE FROM programado_destinatarios"
                        " WHERE prog_id=%s AND paciente_id=%s", (prog_id, paciente_id))
            total += max(0, int(cur.rowcount))
        if len(pares) < limite:
            break
    return total


def _purga_pacientes_dinamicos(cur, dias: int) -> int:
    from snw.pacientes.servicio_areas import tablas_de_pacientes, tabla_valida

    total = 0
    tiene_logs = _existe_tabla(cur, "log_envios")
    cols_log = _columnas(cur, "log_envios") if tiene_logs else set()
    puede_proteger_log = {"paciente_id", "fecha_hora"}.issubset(cols_log)
    tiene_programados = all(_existe_tabla(cur, t)
                            for t in ("programado_destinatarios", "envios_programados"))
    tiene_mensajes = _existe_tabla(cur, "whatsapp_conversaciones_mensajes")

    for tabla in tablas_de_pacientes():
        if not tabla_valida(tabla) or not _existe_tabla(cur, tabla):
            continue
        if "fecha_actualizacion" not in _columnas(cur, tabla):
            continue
        condiciones = [
            "COALESCE(p.fecha_actualizacion,'1970-01-01')"
            f" < DATE_SUB(NOW(), INTERVAL {int(dias)} DAY)"
        ]
        if puede_proteger_log:
            alcance = "l.tabla_pacientes=%s"
            if tabla in ("pacientes_dev", "pacientes_prod"):
                alcance = "(l.tabla_pacientes=%s OR l.tabla_pacientes IS NULL)"
            condiciones.append(
                "NOT EXISTS (SELECT 1 FROM log_envios l WHERE l.paciente_id=p.id"
                " AND l.fecha_hora >= DATE_SUB(NOW(), INTERVAL "
                f"{int(dias)} DAY) AND {alcance})"
            )
        if tiene_programados:
            condiciones.append(
                "NOT EXISTS (SELECT 1 FROM programado_destinatarios pd"
                " JOIN envios_programados ep ON ep.id=pd.prog_id"
                " WHERE pd.paciente_id=p.id AND ep.tabla=%s"
                " AND ep.estado NOT IN ('completado','cancelado','rechazado','error'))"
            )
        if tiene_mensajes and "telefono" in _columnas(cur, tabla):
            condiciones.append(
                "NOT EXISTS (SELECT 1 FROM whatsapp_conversaciones_mensajes cm"
                " WHERE BINARY cm.telefono_paciente=BINARY p.telefono"
                f" AND cm.recibido >= DATE_SUB(NOW(), INTERVAL {int(dias)} DAY))"
            )
        total_tabla = 0
        while total_tabla < _MAX_FILAS_POR_EJECUCION:
            limite = min(_BATCH, _MAX_FILAS_POR_EJECUCION - total_tabla)
            args: list = []
            if puede_proteger_log:
                args.append(tabla)
            if tiene_programados:
                args.append(tabla)
            cur.execute(
                f"SELECT p.id FROM `{tabla}` p WHERE "
                + " AND ".join(condiciones)
                + f" ORDER BY p.id LIMIT {limite}", tuple(args)
            )
            ids = [int(f["id"]) for f in cur.fetchall()]
            if not ids:
                break
            marcadores = ",".join(["%s"] * len(ids))
            if _existe_tabla(cur, "paciente_csv_accesos"):
                cur.execute(
                    "DELETE FROM paciente_csv_accesos WHERE tabla_pacientes=%s"
                    f" AND paciente_id IN ({marcadores})", (tabla, *ids)
                )
            if tiene_programados:
                cur.execute(
                    "UPDATE programado_destinatarios pd"
                    " JOIN envios_programados ep ON ep.id=pd.prog_id"
                    " SET pd.nombre='',pd.telefono=''"
                    " WHERE ep.tabla=%s AND pd.paciente_id IN (" + marcadores + ")"
                    " AND ep.estado IN ('completado','cancelado','rechazado','error')",
                    (tabla, *ids),
                )
            cur.execute(f"DELETE FROM `{tabla}` WHERE id IN ({marcadores})", tuple(ids))
            total_tabla += len(ids)
            if len(ids) < limite:
                break
        total += total_tabla
    return total


def _purga_reservo(cur, dias: int) -> int:
    total = _borrar_vencidos(cur, "reservo_lista_pacientes", "fecha_actualizacion", dias)
    if all(_existe_tabla(cur, t) for t in ("reservo_pacientes", "reservo_lista_pacientes")):
        borrados = 0
        while borrados < _MAX_FILAS_POR_EJECUCION:
            limite = min(_BATCH, _MAX_FILAS_POR_EJECUCION - borrados)
            cur.execute(
                "SELECT p.id FROM reservo_pacientes p"
                " WHERE p.ultima_sincronizacion_en < DATE_SUB(NOW(), INTERVAL %s DAY)"
                " AND NOT EXISTS (SELECT 1 FROM reservo_lista_pacientes lp"
                " WHERE lp.paciente_id=p.id) ORDER BY p.id LIMIT %s", (dias, limite)
            )
            ids = [int(f["id"]) for f in cur.fetchall()]
            if not ids:
                break
            marcadores = ",".join(["%s"] * len(ids))
            cur.execute(f"DELETE FROM reservo_pacientes WHERE id IN ({marcadores})", tuple(ids))
            n = max(0, int(cur.rowcount))
            borrados += n
            if len(ids) < limite:
                break
        total += borrados
    return total


def _purga_reagendamientos(cur, dias: int) -> int:
    if not _existe_tabla(cur, "paciente_reagendar"):
        return 0
    from snw.seguridad.supresion import huella_telefono

    total = 0
    while total < _MAX_FILAS_POR_EJECUCION:
        limite = min(_BATCH, _MAX_FILAS_POR_EJECUCION - total)
        cur.execute(
            "SELECT id,paciente_telefono_normalizado,estado_envio,modo_prueba"
            " FROM paciente_reagendar WHERE actualizado_en <"
            " DATE_SUB(NOW(), INTERVAL %s DAY) ORDER BY id LIMIT %s",
            (dias, limite),
        )
        filas = cur.fetchall()
        if not filas:
            break
        ids = []
        for fila in filas:
            telefono = fila.get("paciente_telefono_normalizado")
            if (fila.get("estado_envio") == "ENVIADO" and not fila.get("modo_prueba")
                    and telefono):
                # El bloqueo histórico contra duplicados sobrevive como HMAC;
                # nunca conserva el teléfono en claro después de vencer la cita.
                # Se usa el mismo cerrojo por huella que usan importaciones y
                # bajas para que una carga concurrente no pueda saltarse esto.
                try:
                    huella = huella_telefono(telefono)
                except (ValueError, RuntimeError):
                    # Si falta la clave HMAC, no se elimina la fila que aún
                    # contiene el teléfono: se podrá reintentar tras restaurarla.
                    log_error("retención de datos: no se pudo crear huella de reagendamiento")
                    continue
                if _existe_tabla(cur, "pacientes_baja_huellas"):
                    cur.execute(
                        "INSERT IGNORE INTO pacientes_baja_huellas (huella,activo)"
                        " VALUES (%s,0)", (huella,)
                    )
                    cur.execute(
                        "SELECT activo FROM pacientes_baja_huellas"
                        " WHERE huella=%s FOR UPDATE", (huella,)
                    )
                    marcador = cur.fetchone() or {}
                cur.execute(
                    "INSERT IGNORE INTO reagendar_destinatarios_huellas (huella) VALUES (%s)",
                    (huella,),
                )
                if _existe_tabla(cur, "pacientes_baja_huellas") and not marcador.get("activo"):
                    cur.execute(
                        "DELETE FROM pacientes_baja_huellas WHERE huella=%s AND activo=0",
                        (huella,),
                    )
            ids.append(int(fila["id"]))
        if not ids:
            break
        marcadores = ",".join(["%s"] * len(ids))
        if _existe_tabla(cur, "reagendar_destinatarios"):
            cur.execute(f"DELETE FROM reagendar_destinatarios WHERE cita_id IN ({marcadores})",
                        tuple(ids))
        if _existe_tabla(cur, "reagendar_flujo"):
            cur.execute(f"DELETE FROM reagendar_flujo WHERE cita_id IN ({marcadores})", tuple(ids))
        cur.execute(f"DELETE FROM paciente_reagendar WHERE id IN ({marcadores})", tuple(ids))
        total += len(ids)
        if len(filas) < limite:
            break
    return total


def _anonimizar_call_center(cur, dias: int) -> int:
    if not _existe_tabla(cur, "call_center_log"):
        return 0
    columnas = _columnas(cur, "call_center_log")
    pii = [c for c in ("paciente_id", "nombre_paciente", "numero_paciente",
                       "vista_mensaje", "descripcion_error") if c in columnas]
    if not pii:
        return 0
    asignaciones = ",".join(f"`{c}`=NULL" for c in pii)
    total = 0
    while total < _MAX_FILAS_POR_EJECUCION:
        limite = min(_BATCH, _MAX_FILAS_POR_EJECUCION - total)
        cur.execute(
            f"UPDATE call_center_log SET {asignaciones} WHERE fecha_hora < "
            f"DATE_SUB(NOW(), INTERVAL {int(dias)} DAY)"
            " AND (" + " OR ".join(f"`{c}` IS NOT NULL" for c in pii) + f") LIMIT {limite}"
        )
        n = max(0, int(cur.rowcount))
        total += n
        if n < limite:
            break
    return total


def _anonimizar_derivaciones(cur, dias: int) -> int:
    if not _existe_tabla(cur, "call_center_derivaciones"):
        return 0
    total = 0
    while total < _MAX_FILAS_POR_EJECUCION:
        limite = min(_BATCH, _MAX_FILAS_POR_EJECUCION - total)
        cur.execute(
            "SELECT id FROM call_center_derivaciones WHERE creado < "
            f"DATE_SUB(NOW(), INTERVAL {int(dias)} DAY)"
            " AND (telefono IS NOT NULL OR mensaje_original IS NOT NULL OR aviso_id IS NOT NULL)"
            f" ORDER BY id LIMIT {limite}"
        )
        ids = [int(f["id"]) for f in cur.fetchall()]
        if not ids:
            break
        marcadores = ",".join(["%s"] * len(ids))
        if _existe_tabla(cur, "call_center_derivacion_mensajes"):
            cur.execute(f"DELETE FROM call_center_derivacion_mensajes"
                        f" WHERE derivacion_id IN ({marcadores})", tuple(ids))
        cur.execute(
            "UPDATE call_center_derivaciones SET telefono=NULL,mensaje_original=NULL,"
            "paciente_id=NULL,aviso_id=NULL,estado='finalizado'"
            f" WHERE id IN ({marcadores})", tuple(ids)
        )
        total += len(ids)
        if len(ids) < limite:
            break
    return total


def _limpiar_archivos(dias: int) -> int:
    if not LOGS_DIR.is_dir():
        return 0
    import time
    corte = time.time() - dias * 24 * 60 * 60
    raiz = LOGS_DIR.resolve()
    eliminados = 0
    candidatos = sorted(LOGS_DIR.iterdir(), key=lambda p: p.name)
    for ruta in candidatos:
        if not _NOMBRE_LOG.fullmatch(ruta.name) or ruta.is_symlink():
            continue
        try:
            if ruta.resolve().parent != raiz or not ruta.is_file() or ruta.stat().st_mtime > corte:
                continue
            ruta.unlink()
            eliminados += 1
        except FileNotFoundError:
            continue
        except OSError as exc:
            log_error("retención de logs: no se pudo eliminar un archivo antiguo", exc,
                      solo_tipo=True)
    return eliminados


def ejecutar_limpieza() -> dict[str, int]:
    """Aplica los vencimientos por lotes y deja intactas las métricas agregadas."""
    dias_datos = _dias("retencion_datos_dias", 365, 30, 3650)
    dias_logs = _dias("retencion_logs_dias", 90, 30, 365)
    dias_auditoria = _dias("retencion_auditoria_dias", 730, 90, 3650)
    dias_historial = _dias("retencion_historial_dias", 730, 365, 3650)
    resultado: dict[str, int] = {"errores": 0}
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("SELECT GET_LOCK('snw_retencion_datos_v1', 0) AS tomado")
        if not (cur.fetchone() or {}).get("tomado"):
            return resultado
        pasos = (
            ("mensajes_conversacion", lambda c: _borrar_vencidos(
                c, "whatsapp_conversaciones_mensajes", "recibido", dias_datos)),
            ("corpus_completo", lambda c: _borrar_vencidos(
                c, "corpus_respuestas_completas", "recibido_en", dias_datos)),
            ("corpus_desidentificado", lambda c: _borrar_vencidos(
                c, "corpus_respuestas", "recibido_en", dias_historial)),
            ("historial_anonimizado", lambda c: _anonimizar_historial(c, dias_datos)),
            ("notas_operativas", lambda c: _limpiar_notas_operativas(c, dias_datos)),
            ("programados_vencidos", lambda c: _cancelar_envios_programados_vencidos(c, dias_datos)),
            ("destinatarios_programados", lambda c: _anonimizar_destinatarios_programados(c, dias_datos)),
            ("historial_vencido", lambda c: _borrar_detalles_historial(c, dias_historial)),
            ("destinatarios_programados_vencidos", lambda c: _borrar_destinatarios_programados_vencidos(c, dias_historial)),
            ("agregados_usuarios", lambda c: _anonimizar_agregados_antiguos(c, dias_auditoria)),
            ("datos_reagendamiento", lambda c: _purga_reagendamientos(c, dias_datos)),
            ("derivaciones_call_center", lambda c: _anonimizar_derivaciones(c, dias_datos)),
            ("historial_call_center", lambda c: _anonimizar_call_center(c, dias_datos)),
            ("historial_call_center_vencido", lambda c: _borrar_vencidos(c, "call_center_log", "fecha_hora", dias_historial)),
            ("respuestas_manuales_vencidas", lambda c: _borrar_vencidos(
                c, "respuestas_manuales_envios", "fecha_hora", dias_historial)),
            ("derivaciones_vencidas", lambda c: _borrar_vencidos(c, "call_center_derivaciones", "creado", dias_historial)),
            ("pacientes", lambda c: _purga_pacientes_dinamicos(c, dias_datos)),
            ("listas_reservo", lambda c: _purga_reservo(c, dias_datos)),
            ("cola_crm", lambda c: _borrar_vencidos(c, "crm_derivaciones_pendientes", "creado", dias_datos)),
            ("consultas_campana", lambda c: _borrar_vencidos(c, "campania_consultas", "actualizado", dias_datos)),
            ("avisos_unicos", lambda c: _borrar_vencidos(c, "whatsapp_avisos_unicos", "enviado", dias_datos)),
            ("pruebas_call_center", lambda c: _borrar_vencidos(c, "call_center_prueba_envios", "creado", 30)),
            ("intereses_prueba", lambda c: _borrar_vencidos(c, "call_center_prueba_interes", "creado", 30)),
            ("interacciones", lambda c: _borrar_vencidos(c, "whatsapp_interacciones_lista", "creado", 30)),
            ("cache_reservo", lambda c: _borrar_vencidos(c, "reservo_citas_cache", "expira_en", 0)),
            ("recuperaciones_invitaciones", _limpiar_recuperaciones_e_invitaciones),
            ("cuotas_antiguas", lambda c: _limpiar_cuotas_antiguas(c, 90)),
            ("pares_despacho", lambda c: _borrar_vencidos(c, "wa_despacho_pares", "proximo_turno", 1)),
            ("auditoria_usuarios", lambda c: _borrar_vencidos(c, "usuarios_auditoria", "fecha_hora", dias_auditoria)),
            ("simulaciones", lambda c: _borrar_vencidos(c, "envios_simulaciones", "creado", dias_datos)),
        )
        try:
            for nombre, funcion in pasos:
                _ejecutar_paso(conn, nombre, funcion, resultado)
        finally:
            try:
                cur.execute("SELECT RELEASE_LOCK('snw_retencion_datos_v1')")
            except Exception:
                pass
    try:
        resultado["archivos_logs"] = _limpiar_archivos(dias_logs)
    except Exception as exc:
        resultado["errores"] += 1
        log_error("retención de logs: error al limpiar archivos", exc)
    if any(v for k, v in resultado.items() if k not in ("errores",)):
        resumen = ", ".join(f"{k}={v}" for k, v in resultado.items() if v)
        print(f"[RETENCIÓN DE DATOS] Limpieza diaria terminada: {resumen}", flush=True)
    resultado["requiere_continuar"] = int(
        any(v >= _MAX_FILAS_POR_EJECUCION for k, v in resultado.items()
            if k not in ("errores", "requiere_continuar"))
    )
    return resultado

