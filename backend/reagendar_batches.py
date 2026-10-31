"""Un envío de historial por carga CSV y clínica; mantiene los IDs históricos."""
from db import conectar


def resumen(cur, envio_id: int) -> dict:
    cur.execute(
        "SELECT COUNT(*) AS total_pacientes,"
        " COALESCE(SUM(estado='ENVIADO' AND COALESCE(entrega,'')<>'failed'),0) AS enviados,"
        " COALESCE(SUM(estado='ERROR' OR (estado='ENVIADO' AND entrega='failed')),0) AS fallidos,"
        " COALESCE(SUM(estado='OMITIDO'),0) AS invalidos,"
        " COALESCE(SUM(estado IN ('PENDIENTE','REINTENTO','PROCESANDO')),0) AS pendientes"
        " FROM (SELECT r.estado_envio AS estado, le.estado_whatsapp AS entrega"
        " FROM reagendar_batches b JOIN paciente_reagendar r"
        " ON r.lote_id=b.lote_id AND r.centro_codigo=b.centro_codigo"
        " LEFT JOIN log_envios le ON le.id=r.log_envio_id"
        " WHERE b.envio_id=%s AND r.modo_prueba=0"
        " AND (r.estado_envio<>'OMITIDO' OR r.envio_id=b.envio_id)"
        " AND r.paciente_telefono_normalizado IS NOT NULL"
        " UNION ALL SELECT CASE WHEN le.estado_envio='error' THEN 'ERROR' ELSE 'ENVIADO' END,"
        " le.estado_whatsapp FROM log_envios le JOIN reagendar_batches b ON b.envio_id=le.envio_id"
        " WHERE le.envio_id=%s AND NOT EXISTS (SELECT 1 FROM paciente_reagendar r"
        " WHERE r.lote_id=b.lote_id AND r.centro_codigo=b.centro_codigo"
        " AND BINARY r.paciente_telefono_normalizado=BINARY le.numero_telefono)"
        " AND NOT EXISTS (SELECT 1 FROM log_envios nuevo WHERE nuevo.envio_id=le.envio_id"
        " AND BINARY nuevo.numero_telefono=BINARY le.numero_telefono AND nuevo.id>le.id)"
        ") resultados", (envio_id, envio_id))
    datos = {k: int(v or 0) for k, v in cur.fetchone().items()}
    pendientes = datos.pop("pendientes")
    datos["estado"] = "en_proceso" if pendientes else "error" if datos["fallidos"] or datos["invalidos"] else "completado"
    return datos


def guardar_resumen(cur, envio_id: int) -> None:
    datos = resumen(cur, envio_id)
    cur.execute("UPDATE envios SET total_pacientes=%s, enviados=%s, fallidos=%s, invalidos=%s, estado=%s"
                " WHERE id=%s", (datos["total_pacientes"], datos["enviados"], datos["fallidos"],
                                  datos["invalidos"], datos["estado"], envio_id))


def asegurar_grupo(cur, lote_id: int, centro: str, plantilla: dict | None = None,
                   canal: dict | None = None) -> int:
    # El registro único serializa workers sin mantener bloqueos durante el POST.
    cur.execute("INSERT IGNORE INTO reagendar_batches (lote_id, centro_codigo) VALUES (%s,%s)",
                (lote_id, centro))
    cur.execute("SELECT envio_id FROM reagendar_batches WHERE lote_id=%s AND centro_codigo=%s FOR UPDATE",
                (lote_id, centro))
    envio_id = cur.fetchone()["envio_id"]
    if envio_id is not None:
        return int(envio_id)
    cur.execute("SELECT DISTINCT r.envio_id FROM paciente_reagendar r JOIN envios e ON e.id=r.envio_id"
                " WHERE r.lote_id=%s AND r.centro_codigo=%s AND e.base_datos='paciente_reagendar'"
                " ORDER BY r.envio_id", (lote_id, centro))
    anteriores = [fila["envio_id"] for fila in cur.fetchall()]
    if envio_id is None:
        if anteriores:
            envio_id = anteriores[0]
        else:
            if not plantilla or not canal:
                raise ValueError("Faltan datos para crear el lote de reagendamiento.")
            cur.execute("INSERT INTO envios (base_datos, plantilla_clave, plantilla_nombre, total_pacientes,"
                        " estado, usuario, centro_codigo, wa_phone_id, numero_emisor)"
                        " VALUES ('paciente_reagendar',%s,%s,0,'en_proceso','integracion_reagendar',%s,%s,%s)",
                        (plantilla["clave"], plantilla.get("nombre") or plantilla["clave"],
                         centro, canal["phoneNumberId"], canal["phoneNumber"]))
            envio_id = cur.lastrowid
        cur.execute("UPDATE reagendar_batches SET envio_id=%s WHERE lote_id=%s AND centro_codigo=%s",
                    (envio_id, lote_id, centro))
    for anterior in anteriores:
        if anterior == envio_id:
            continue
        cur.execute("INSERT INTO reagendar_envios_agrupados (envio_id,batch_envio_id) VALUES (%s,%s)"
                    " ON DUPLICATE KEY UPDATE batch_envio_id=VALUES(batch_envio_id)", (anterior, envio_id))
        cur.execute("UPDATE log_envios SET envio_id=%s WHERE envio_id=%s", (envio_id, anterior))
        cur.execute("UPDATE paciente_reagendar SET envio_id=%s WHERE envio_id=%s"
                    " AND lote_id=%s AND centro_codigo=%s", (envio_id, anterior, lote_id, centro))
    guardar_resumen(cur, envio_id)
    return envio_id


def agrupar_existentes() -> int:
    """Agrupa los envíos vinculables a una carga, sin borrar mensajes ni envíos antiguos."""
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT r.lote_id, r.centro_codigo FROM paciente_reagendar r"
                    " JOIN envios e ON e.id=r.envio_id"
                    " LEFT JOIN reagendar_batches b ON b.lote_id=r.lote_id AND b.centro_codigo=r.centro_codigo"
                    " WHERE r.lote_id IS NOT NULL AND r.centro_codigo IS NOT NULL"
                    " AND e.base_datos='paciente_reagendar' AND b.envio_id IS NULL"
                    " ORDER BY r.lote_id, r.centro_codigo")
        grupos = cur.fetchall()
    for grupo in grupos:
        with conectar() as conn, conn.cursor() as cur:
            envio_id = asegurar_grupo(cur, grupo["lote_id"], grupo["centro_codigo"])
            guardar_resumen(cur, envio_id)
            conn.commit()
    return len(grupos)


def resolver_envio(envio_id: int) -> int:
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("SELECT batch_envio_id FROM reagendar_envios_agrupados WHERE envio_id=%s", (envio_id,))
        fila = cur.fetchone()
    return int(fila["batch_envio_id"]) if fila else envio_id


def resumenes(ids: list[int]) -> dict[int, dict]:
    if not ids:
        return {}
    marcadores = ",".join(["%s"] * len(ids))
    with conectar() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT envio_id FROM reagendar_batches WHERE envio_id IN ({marcadores})", tuple(ids))
        presentes = [fila["envio_id"] for fila in cur.fetchall()]
        return {envio_id: resumen(cur, envio_id) for envio_id in presentes}
