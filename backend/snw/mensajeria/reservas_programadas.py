"""Reservas de destinatarios de envíos programados activos."""

from contextlib import contextmanager


def telefono_sql(columna: str) -> str:
    telefono = f"REPLACE(REPLACE(REPLACE(REPLACE(REPLACE({columna}, ' ', ''), '-', ''), '(', ''), ')', ''), '+', '')"
    return (
        f"CASE WHEN LENGTH({telefono})=11 AND LEFT({telefono},3)='569' THEN CONCAT('+',{telefono}) "
        f"WHEN LENGTH({telefono})=10 AND LEFT({telefono},2)='56' THEN CONCAT('+569',SUBSTRING({telefono},3)) "
        f"WHEN LENGTH({telefono})=9 AND LEFT({telefono},1)='9' THEN CONCAT('+56',{telefono}) "
        f"WHEN LENGTH({telefono})=8 AND LEFT({telefono},1)='9' THEN CONCAT('+569',{telefono}) ELSE NULL END"
    )


def existe_reserva_sql(telefono: str, programado_actual: str = "0") -> str:
    # Acepta también las representaciones históricas del número. Mantiene
    # pd.telefono sin funciones para poder usar su índice. Si ya había
    # reservas superpuestas, tiene prioridad la primera que se creó.
    telefono = f"(CONVERT(({telefono}) USING utf8mb4) COLLATE utf8mb4_unicode_ci)"
    return (
        "EXISTS (SELECT 1 FROM programado_destinatarios pd"
        " JOIN envios_programados ep ON ep.id=pd.prog_id"
        f" WHERE pd.telefono IN ({telefono}, SUBSTRING({telefono},2),"
        f" SUBSTRING({telefono},4), CONCAT('56',SUBSTRING({telefono},5)), SUBSTRING({telefono},5))"
        " AND ep.estado IN ('pendiente','aprobado','enviando')"
        f" AND ({programado_actual}=0 OR ep.id<{programado_actual}))"
    )


def unir_reservas_para_lectura_sql() -> str:
    """Agrupa la cohorte activa una vez, sin releerla por paciente o contador."""
    telefono_reserva = f"CONVERT(({telefono_sql('pd.telefono')}) USING utf8mb4) COLLATE utf8mb4_unicode_ci"
    telefono_paciente = f"CONVERT(({telefono_sql('p.telefono')}) USING utf8mb4) COLLATE utf8mb4_unicode_ci"
    return (
        f" LEFT JOIN (SELECT DISTINCT {telefono_reserva} AS telefono_reservado"
        " FROM envios_programados ep JOIN programado_destinatarios pd ON pd.prog_id=ep.id"
        " WHERE ep.estado IN ('pendiente','aprobado','enviando') AND pd.telefono<>''"
        " AND (ep.estado<>'enviando' OR NOT EXISTS (SELECT 1 FROM envios e"
        " JOIN log_envios le ON le.envio_id=e.id"
        f" WHERE e.programado_id=ep.id AND le.numero_telefono={telefono_reserva}"
        " AND le.estado_envio='enviado'))"
        f") reserva_paciente ON reserva_paciente.telefono_reservado={telefono_paciente}"
    )


def estado_sql(estado: str) -> str:
    return f"CASE WHEN reserva_paciente.telefono_reservado IS NOT NULL THEN 'programado' ELSE {estado} END"


def telefono_reservado(cur, telefono: str) -> bool:
    sql = existe_reserva_sql("%(telefono)s")
    cur.execute(f"SELECT {sql} AS reservado", {"telefono": telefono})
    return bool(cur.fetchone()["reservado"])


@contextmanager
def bloqueo_creacion(cur):
    # Las reservas compiten por teléfono, incluso entre tablas distintas.
    clave = "snw-programar-destinatarios"
    cur.execute("SELECT GET_LOCK(%s, 5) AS tomado", (clave,))
    tomado = bool((cur.fetchone() or {}).get("tomado"))
    try:
        yield tomado
    finally:
        if tomado:
            cur.execute("SELECT RELEASE_LOCK(%s)", (clave,))
