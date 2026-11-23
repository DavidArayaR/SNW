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


def existe_reserva_sql(telefono: str, programado_actual: str = "0", *, solo_por_enviar: bool = False) -> str:
    # Acepta también las representaciones históricas del número. Mantiene
    # pd.telefono sin funciones para poder usar su índice. Si ya había
    # reservas superpuestas, tiene prioridad la primera que se creó.
    telefono = f"(CONVERT(({telefono}) USING utf8mb4) COLLATE utf8mb4_unicode_ci)"
    pendiente = (
        " AND (ep.estado<>'enviando' OR NOT EXISTS (SELECT 1 FROM envios e"
        " JOIN log_envios le ON le.envio_id=e.id WHERE e.programado_id=ep.id"
        f" AND le.numero_telefono={telefono} AND le.estado_envio='enviado'))"
        if solo_por_enviar else ""
    )
    return (
        "EXISTS (SELECT 1 FROM programado_destinatarios pd"
        " JOIN envios_programados ep ON ep.id=pd.prog_id"
        f" WHERE pd.telefono IN ({telefono}, SUBSTRING({telefono},2),"
        f" SUBSTRING({telefono},4), CONCAT('56',SUBSTRING({telefono},5)), SUBSTRING({telefono},5))"
        " AND ep.estado IN ('pendiente','aprobado','enviando')"
        f" AND ({programado_actual}=0 OR ep.id<{programado_actual}){pendiente})"
    )


def estado_sql(estado: str, columna_telefono: str = "p.telefono") -> str:
    reserva = existe_reserva_sql(telefono_sql(columna_telefono), solo_por_enviar=True)
    return f"CASE WHEN {reserva} THEN 'programado' ELSE {estado} END"


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
