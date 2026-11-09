"""Convierte las cohortes Reservo al modelo común de bases de pacientes.

Reservo conserva los metadatos de procedencia; los pacientes operativos viven
en el mismo esquema, registro de áreas y rutas que cualquier otra base.
"""
from db import conectar
import servicio_areas


def integrar_bases(base_id: int | None = None):
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("SELECT GET_LOCK('snw-reservo-listas', 30) AS adquirido")
        if not (cur.fetchone() or {}).get('adquirido'):
            raise RuntimeError('Otra operación está modificando las bases Reservo.')
        try:
            cur.execute("SHOW COLUMNS FROM reservo_bases_contacto LIKE 'area_id'")
            if not cur.fetchone():
                cur.execute('ALTER TABLE reservo_bases_contacto ADD COLUMN area_id INT NULL')
            cur.execute("SELECT id,nombre,area_id FROM reservo_bases_contacto"
                        " WHERE area_id IS NULL" + (" AND id=%s" if base_id is not None else "")
                        + " ORDER BY id", (base_id,) if base_id is not None else ())
            bases = list(cur.fetchall())
            for base in bases:
                if base.get('area_id'):
                    continue
                lista_id = int(base['id'])
                tabla = f'pacientes_reservo_{lista_id}'
                # DDL antes de la transacción: un fallo posterior permite
                # reintentar, sin publicar una base parcialmente migrada.
                esquema = servicio_areas._ESQUEMA_PACIENTES.format(tabla=tabla)
                esquema = esquema.replace('VARCHAR(150)', 'VARCHAR(200)')
                cur.execute(esquema.replace('  INDEX idx_telefono',
                            '  reservo_cliente_uuid VARCHAR(80) NULL,'
                            '  reservo_paciente_id BIGINT UNSIGNED NULL,  INDEX idx_telefono'))
                conn.begin()
                try:
                    cur.execute('SELECT id FROM areas WHERE nombre_tabla_base=%s', (tabla,))
                    area = cur.fetchone()
                    if area:
                        raise RuntimeError(f'La tabla {tabla} ya pertenece a otra base; no se modificó.')
                    else:
                        cur.execute('INSERT INTO areas (nombre_visible,nombre_tabla_base) VALUES (%s,%s)',
                                    (base['nombre'], tabla))
                        area_id = cur.lastrowid
                        cur.execute('INSERT INTO roles_area (area_id,nombre,descripcion) VALUES (%s,%s,%s)',
                                    (area_id, base['nombre'], 'Base de pacientes importados desde Reservo'))
                    cur.execute(f'SELECT COUNT(*) AS total FROM {tabla}')
                    if int((cur.fetchone() or {}).get('total') or 0):
                        raise RuntimeError(f'La tabla {tabla} ya contiene datos; no se modificó.')
                    columnas = ('id,nombre,apellido,telefono,estado,whatsapp_opt_out,opt_out_explicito,'
                                'respuesta_manual,interesado,no_interesado,interes_plantilla_clave,'
                                'interes_fecha,tratamiento_ultima_atencion,fecha_ultima_atencion,fecha_actualizacion')
                    # IDs originales preservados: las etiquetas y destinatarios
                    # congelados siguen apuntando al mismo paciente de la lista.
                    cur.execute(f'INSERT INTO {tabla} ({columnas},reservo_cliente_uuid,reservo_paciente_id)'
                                f' SELECT {columnas},cliente_uuid,paciente_id'
                                ' FROM reservo_lista_pacientes WHERE lista_id=%s', (lista_id,))
                    cur.execute('UPDATE log_envios l JOIN reservo_lista_pacientes p ON p.id=l.paciente_id'
                                ' LEFT JOIN envios e ON e.id=l.envio_id'
                                ' SET l.tabla_pacientes=%s,l.area_id=%s'
                                " WHERE p.lista_id=%s AND (l.tabla_pacientes='reservo_lista_pacientes'"
                                ' OR e.reservo_lista_id=%s)',
                                (tabla, area_id, lista_id, lista_id))
                    cur.execute('UPDATE call_center_derivaciones d JOIN reservo_lista_pacientes p'
                                ' ON p.id=d.paciente_id SET d.base_datos=%s'
                                " WHERE p.lista_id=%s AND d.base_datos='reservo_lista_pacientes'",
                                (tabla, lista_id))
                    cur.execute('UPDATE call_center_log c JOIN reservo_lista_pacientes p'
                                ' ON p.id=c.paciente_id SET c.base_datos=%s'
                                " WHERE p.lista_id=%s AND c.base_datos='reservo_lista_pacientes'",
                                (tabla, lista_id))
                    cur.execute('UPDATE paciente_csv_accesos a JOIN reservo_lista_pacientes p'
                                ' ON p.id=a.paciente_id SET a.tabla_pacientes=%s'
                                " WHERE p.lista_id=%s AND a.tabla_pacientes='reservo_lista_pacientes'",
                                (tabla, lista_id))
                    cur.execute('UPDATE envios SET tabla_pacientes=%s,base_datos=%s,area_id=%s,'
                                'reservo_lista_id=NULL WHERE reservo_lista_id=%s',
                                (tabla, tabla, area_id, lista_id))
                    cur.execute('UPDATE envios_programados SET tabla=%s,area_id=%s,reservo_lista_id=NULL'
                                ' WHERE reservo_lista_id=%s', (tabla, area_id, lista_id))
                    cur.execute('UPDATE reservo_bases_contacto SET area_id=%s WHERE id=%s',
                                (area_id, lista_id))
                    # La cohorte original ya fue transferida: evita estados
                    # paralelos y que el webhook encuentre dos copias operativas.
                    cur.execute('DELETE FROM reservo_lista_pacientes WHERE lista_id=%s', (lista_id,))
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
        finally:
            cur.execute("SELECT RELEASE_LOCK('snw-reservo-listas')")


def datos_por_area():
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE()"
                    " AND TABLE_NAME='reservo_bases_contacto' AND COLUMN_NAME='area_id'")
        if not cur.fetchone():
            return {}
        cur.execute('SELECT b.id,b.area_id,b.clinica_codigo,b.nombre,e.nombre_tabla_base'
                    ' FROM reservo_bases_contacto b JOIN areas e ON e.id=b.area_id')
        return {int(f['area_id']): f for f in cur.fetchall()}


def presentar_areas(areas):
    datos = datos_por_area()
    for area in areas:
        base = datos.get(int(area['id']))
        if base:
            area.update(origen_reservo=True, clinica_codigo=base['clinica_codigo'])
    return areas
