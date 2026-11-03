"""Supresión por solicitud ELIMINAR, con huella local y bloqueo en Meta.

La huella es seudónima: la clave HMAC vive fuera de la base en el archivo local
ignorado `.env.supresion`. Conservar ese archivo es imprescindible para impedir
reimportaciones después de restaurar la base.
"""

import hashlib
import hmac
import os
import re
import secrets
import threading
from functools import lru_cache

import pymysql

import httpx

from db import BASE_DIR, conectar, config_get, log_error
from telefono import normalizar_telefono

RUTA_CLAVE = BASE_DIR / ".env.supresion"
TABLA_HUELLAS_BAJA = "pacientes_baja_huellas"
MIGRACION_HUELLAS_BAJA = "opt_out_hmac_v1"


def asegurar_tabla_supresion() -> None:
    with conectar() as conn, conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS supresion_telefonos ("
            " huella CHAR(64) PRIMARY KEY,"
            " estado ENUM('pendiente','procesando','completo','exento') NOT NULL DEFAULT 'pendiente',"
            " telefono_pendiente VARCHAR(20) NULL,"
            " wa_phone_id VARCHAR(64) NULL,"
            " bases_afectadas INT NOT NULL DEFAULT 0,"
            " pacientes_eliminados INT NOT NULL DEFAULT 0,"
            " creado DATETIME DEFAULT CURRENT_TIMESTAMP,"
            " completado DATETIME NULL"
            ") ENGINE=InnoDB CHARACTER SET utf8mb4"
        )
        cur.execute(
            "SELECT COLUMN_TYPE FROM information_schema.COLUMNS"
            " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'supresion_telefonos'"
            " AND COLUMN_NAME = 'estado'"
        )
        tipo = (cur.fetchone() or {}).get("COLUMN_TYPE") or ""
        if "procesando" not in tipo or "exento" not in tipo:
            cur.execute(
                "ALTER TABLE supresion_telefonos MODIFY COLUMN estado"
                " ENUM('pendiente','procesando','completo','exento') NOT NULL DEFAULT 'pendiente'"
            )
        cur.execute(
            "SELECT ENGINE FROM information_schema.TABLES"
            " WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='supresion_telefonos'"
        )
        motor = (cur.fetchone() or {}).get("ENGINE")
        if motor and motor.lower() != "innodb":
            cur.execute("ALTER TABLE supresion_telefonos ENGINE=InnoDB")
        cur.execute(
            "CREATE TABLE IF NOT EXISTS pacientes_baja_huellas ("
            " huella CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY,"
            " activo TINYINT(1) NOT NULL DEFAULT 0,"
            " origen ENUM('paciente','manual','migracion') NOT NULL DEFAULT 'paciente',"
            " creado DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,"
            " actualizado DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
            " ON UPDATE CURRENT_TIMESTAMP"
            ") ENGINE=InnoDB"
        )
        cur.execute(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS"
            " WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='pacientes_baja_huellas'"
            " AND COLUMN_NAME='origen'"
        )
        if not cur.fetchone():
            cur.execute(
                "ALTER TABLE pacientes_baja_huellas ADD COLUMN origen"
                " ENUM('paciente','manual','migracion') NOT NULL DEFAULT 'paciente'"
                " AFTER activo"
            )
        cur.execute(
            "CREATE TABLE IF NOT EXISTS snw_migraciones_datos ("
            " codigo VARCHAR(80) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY,"
            " completada TINYINT(1) NOT NULL DEFAULT 0,"
            " actualizado DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
            " ON UPDATE CURRENT_TIMESTAMP"
            ") ENGINE=InnoDB"
        )
        cur.execute(
            "INSERT IGNORE INTO snw_migraciones_datos (codigo, completada)"
            " VALUES (%s, 0)", (MIGRACION_HUELLAS_BAJA,)
        )
        conn.commit()


@lru_cache(maxsize=1)
def _clave_hmac() -> bytes:
    try:
        clave = RUTA_CLAVE.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        # Nunca generar silenciosamente otra clave si ya existen huellas.
        with conectar() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT TABLE_NAME FROM information_schema.TABLES"
                " WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME IN"
                " ('supresion_telefonos','pacientes_baja_huellas')"
            )
            tablas = {r["TABLE_NAME"] for r in cur.fetchall()}
            existentes = 0
            for tabla in tablas:
                if tabla in ("supresion_telefonos", "pacientes_baja_huellas"):
                    cur.execute(f"SELECT COUNT(*) AS n FROM {tabla}")
                    existentes += int((cur.fetchone() or {}).get("n") or 0)
            if existentes:
                raise RuntimeError("Falta .env.supresion: restaurar la clave original")
        nueva = secrets.token_hex(32)
        try:
            fd = os.open(RUTA_CLAVE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            clave = RUTA_CLAVE.read_text(encoding="ascii").strip()
        else:
            with os.fdopen(fd, "w", encoding="ascii") as archivo:
                archivo.write(nueva + "\n")
            clave = nueva
    if len(clave) != 64 or any(c not in "0123456789abcdef" for c in clave):
        raise RuntimeError("La clave de .env.supresion no es válida")
    return bytes.fromhex(clave)


def huella_telefono(telefono: str) -> str:
    tel = normalizar_telefono(telefono)
    if not tel:
        raise ValueError("Teléfono inválido para supresión")
    return hmac.new(_clave_hmac(), tel.encode("ascii"), hashlib.sha256).hexdigest()


def bloquear_huella_baja(cur, telefono: str) -> tuple[str, bool]:
    """Bloquea en InnoDB el ciclo de baja/importación para un teléfono.

    Se inserta una fila inactiva como cerrojo temporal. El llamador debe
    conservar la transacción hasta terminar y borrar las filas inactivas antes
    del commit. Las filas activas son exclusivamente huellas de bajas vigentes.
    """
    huella = huella_telefono(telefono)
    cur.execute(
        "INSERT IGNORE INTO pacientes_baja_huellas (huella, activo) VALUES (%s, 0)",
        (huella,),
    )
    cur.execute("SELECT activo FROM pacientes_baja_huellas WHERE huella=%s FOR UPDATE",
                (huella,))
    fila = cur.fetchone()
    # Un cursor de prueba puede no modelar el SELECT posterior al INSERT;
    # MySQL real siempre devuelve la fila que acaba de reservarse.
    return huella, bool((fila or {}).get("activo"))


def activar_huella_baja(cur, huella: str, *, origen: str = "paciente") -> None:
    if origen not in ("paciente", "manual", "migracion"):
        origen = "paciente"
    cur.execute(
        "INSERT INTO pacientes_baja_huellas (huella,activo,origen) VALUES (%s,1,%s)"
        " ON DUPLICATE KEY UPDATE origen=CASE"
        " WHEN VALUES(origen)='paciente' THEN 'paciente'"
        " WHEN activo=0 THEN VALUES(origen) ELSE origen END,activo=1",
        (huella, origen),
    )


def retirar_huella_baja(cur, huella: str) -> None:
    """Retira solo la huella de baja temporal; no modifica la tabla de supresión."""
    cur.execute("DELETE FROM pacientes_baja_huellas WHERE huella=%s", (huella,))


def limpiar_cerrojos_baja_inactivos(cur, huellas) -> None:
    unicas = list(dict.fromkeys(huellas or ()))
    for inicio in range(0, len(unicas), 500):
        lote = unicas[inicio:inicio + 500]
        if not lote:
            continue
        marcadores = ",".join("%s" for _ in lote)
        cur.execute(
            "DELETE FROM pacientes_baja_huellas WHERE activo=0"
            f" AND huella IN ({marcadores})", tuple(lote),
        )


def huella_baja_activa_cur(cur, telefono: str, *, bloquear: bool = False) -> bool:
    if bloquear:
        huella, activa = bloquear_huella_baja(cur, telefono)
        if not activa:
            limpiar_cerrojos_baja_inactivos(cur, (huella,))
        return activa
    cur.execute("SELECT 1 FROM pacientes_baja_huellas WHERE huella=%s AND activo=1",
                (huella_telefono(telefono),))
    return cur.fetchone() is not None


def _migracion_huellas_completa_cur(cur) -> bool:
    try:
        cur.execute("SELECT completada FROM snw_migraciones_datos WHERE codigo=%s",
                    (MIGRACION_HUELLAS_BAJA,))
    except pymysql.err.OperationalError as exc:
        if exc.args and exc.args[0] == 1146:
            return False
        raise
    fila = cur.fetchone()
    return bool(fila and fila.get("completada"))


def _baja_legacy_activa_cur(cur, telefono: str) -> bool:
    """Respaldo durante el backfill: consulta opt-outs antiguos aún no migrados."""
    import servicio_areas

    expresion = ("REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(telefono, '+', ''), ' ', ''),"
                 " '-', ''), '(', ''), ')', ''), '.', '')")
    for tabla in servicio_areas.tablas_de_pacientes():
        if not servicio_areas.tabla_valida(tabla):
            continue
        try:
            cur.execute(f"SELECT 1 FROM {tabla} WHERE whatsapp_opt_out=1 AND "
                        f"CAST({expresion} AS CHAR CHARACTER SET utf8mb4) COLLATE utf8mb4_bin="
                        "CAST(%s AS CHAR CHARACTER SET utf8mb4) COLLATE utf8mb4_bin LIMIT 1",
                        (normalizar_telefono(telefono),))
        except pymysql.err.OperationalError as exc:
            if exc.args and exc.args[0] in (1054, 1146):
                continue
            raise
        if cur.fetchone():
            return True
    try:
        cur.execute("SELECT 1 FROM paciente_reagendar"
                    " WHERE whatsapp_opt_out=1 AND"
                    " CAST(paciente_telefono_normalizado AS CHAR CHARACTER SET utf8mb4)"
                    " COLLATE utf8mb4_bin=CAST(%s AS CHAR CHARACTER SET utf8mb4)"
                    " COLLATE utf8mb4_bin LIMIT 1",
                    (normalizar_telefono(telefono),))
    except pymysql.err.OperationalError as exc:
        if not exc.args or exc.args[0] != 1146:
            raise
    else:
        if cur.fetchone():
            return True
    return False


def esta_de_baja(telefono: str) -> bool:
    """Consulta la huella HMAC de una baja vigente, aunque ya no haya ficha."""
    if not normalizar_telefono(telefono):
        return False
    try:
        with conectar() as conn, conn.cursor() as cur:
            if huella_baja_activa_cur(cur, telefono):
                return True
            return (not _migracion_huellas_completa_cur(cur)
                    and _baja_legacy_activa_cur(cur, telefono))
    except Exception as exc:
        # Si no se puede validar la lista de bajas, no se autoriza un envío.
        log_error("consultar huella de baja", exc, solo_tipo=True)
        return True


def esta_bloqueado_para_envio(telefono: str, *, permitir_aviso_baja: bool = False) -> bool:
    if not normalizar_telefono(telefono):
        return False
    try:
        huella = huella_telefono(telefono)
        with conectar() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM supresion_telefonos"
                        " WHERE huella=%s AND estado <> 'exento'", (huella,))
            if cur.fetchone():
                return True
            if not permitir_aviso_baja:
                cur.execute("SELECT 1 FROM pacientes_baja_huellas"
                            " WHERE huella=%s AND activo=1", (huella,))
                if cur.fetchone():
                    return True
            cur.execute("SELECT 1 FROM whatsapp_avisos_unicos"
                        " WHERE telefono=%s AND tipo='eliminacion_simulada' LIMIT 1",
                        (normalizar_telefono(telefono),))
            if cur.fetchone():
                return True
            return (not permitir_aviso_baja
                    and not _migracion_huellas_completa_cur(cur)
                    and _baja_legacy_activa_cur(cur, telefono))
    except Exception as exc:
        log_error("validar preferencia antes del envío", exc, solo_tipo=True)
        return True


def migrar_huellas_baja_existentes() -> int:
    """Migra una sola vez las bajas presentes en las tablas antes de esta versión.

    El marcador InnoDB serializa instancias concurrentes. Cada fila fuente se
    vuelve a leer con bloqueo después de reservar su HMAC, evitando restaurar
    una baja que se haya retractado durante la migración.
    """
    asegurar_tabla_supresion()
    import servicio_areas

    migradas = 0
    temporales: set[str] = set()
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("SELECT completada FROM snw_migraciones_datos"
                    " WHERE codigo=%s FOR UPDATE", (MIGRACION_HUELLAS_BAJA,))
        estado = cur.fetchone()
        if estado and estado.get("completada"):
            conn.commit()
            return 0

        tablas = [t for t in servicio_areas.tablas_de_pacientes()
                  if servicio_areas.tabla_valida(t)]
        fuentes = [(tabla, "telefono") for tabla in tablas]
        fuentes.append(("paciente_reagendar", "paciente_telefono_normalizado"))

        with conectar() as fuente_conn:
            for tabla, columna_telefono in fuentes:
                try:
                    with fuente_conn.cursor(pymysql.cursors.SSDictCursor) as fuente:
                        fuente.execute(
                            f"SELECT id, {columna_telefono} AS telefono FROM {tabla}"
                            " WHERE whatsapp_opt_out=1 AND "
                            f"{columna_telefono} IS NOT NULL"
                        )
                        while True:
                            lote = fuente.fetchmany(500)
                            if not lote:
                                break
                            for fila in lote:
                                telefono = normalizar_telefono(fila.get("telefono") or "")
                                if not telefono:
                                    continue
                                huella, activa = bloquear_huella_baja(cur, telefono)
                                if not activa:
                                    temporales.add(huella)
                                # Bloqueo actual de la fuente; una retractación
                                # que ganó antes de este cerrojo ya dejó el valor en 0.
                                cur.execute(
                                    f"SELECT whatsapp_opt_out FROM {tabla} WHERE id=%s FOR UPDATE",
                                    (fila["id"],),
                                )
                                actual = cur.fetchone()
                                if not actual or not actual.get("whatsapp_opt_out"):
                                    continue
                                cur.execute(
                                    "SELECT estado FROM supresion_telefonos"
                                    " WHERE huella=%s FOR UPDATE", (huella,),
                                )
                                permanente = cur.fetchone()
                                if permanente and permanente.get("estado") != "exento":
                                    continue
                                activar_huella_baja(cur, huella, origen="migracion")
                                temporales.discard(huella)
                                migradas += int(not activa)
                except pymysql.err.OperationalError as exc:
                    # Una instalación antigua puede no tener aún alguna tabla
                    # o columna opcional; otros errores sí cancelan el backfill.
                    if exc.args and exc.args[0] in (1054, 1146):
                        continue
                    raise
        limpiar_cerrojos_baja_inactivos(cur, temporales)
        cur.execute("UPDATE snw_migraciones_datos SET completada=1 WHERE codigo=%s",
                    (MIGRACION_HUELLAS_BAJA,))
        conn.commit()
    return migradas


def esta_suprimido(telefono: str) -> bool:
    if not normalizar_telefono(telefono):
        return False
    huella = huella_telefono(telefono)
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM supresion_telefonos"
                    " WHERE huella = %s AND estado <> 'exento'", (huella,))
        return cur.fetchone() is not None


def es_exento(telefono: str) -> bool:
    """Excepción de prueba explícita: conserva la huella, no el número crudo."""
    if not normalizar_telefono(telefono):
        return False
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM supresion_telefonos"
                    " WHERE huella = %s AND estado = 'exento'",
                    (huella_telefono(telefono),))
        return cur.fetchone() is not None


def es_numero_prueba(telefono: str) -> bool:
    """Un teléfono autorizado para pruebas en desarrollo o producción."""
    tel = normalizar_telefono(telefono)
    if not tel:
        return False
    for clave in ("numeros_prueba_dev", "numeros_prueba_prod"):
        if any(normalizar_telefono(valor) == tel
               for valor in config_get(clave, "").split(",") if valor.strip()):
            return True
    return False


def eliminacion_simulada_activa(telefono: str) -> bool:
    """Marcador reversible de prueba: no es una supresión ni bloquea en Meta."""
    tel = normalizar_telefono(telefono)
    if not tel:
        return False
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM whatsapp_avisos_unicos"
                    " WHERE telefono = %s AND tipo = 'eliminacion_simulada'", (tel,))
        return cur.fetchone() is not None


def solicitar_eliminacion(telefono: str) -> str:
    """Bloquea localmente antes de cualquier llamada externa; no envía respuesta."""
    tel = normalizar_telefono(telefono)
    if not tel:
        raise ValueError("Teléfono inválido para eliminación")
    huella = huella_telefono(tel)
    from whatsapp_channels import emisor_webhook_actual
    phone_id_origen = (emisor_webhook_actual() or {}).get("phoneNumberId")
    with conectar() as conn, conn.cursor() as cur:
        huella_bloqueo, _ = bloquear_huella_baja(cur, tel)
        cur.execute(
            "INSERT INTO supresion_telefonos (huella, telefono_pendiente, wa_phone_id)"
            " VALUES (%s, %s, %s)"
            " ON DUPLICATE KEY UPDATE"
            " telefono_pendiente = IF(estado IN ('completo','exento'), NULL, VALUES(telefono_pendiente)),"
            " wa_phone_id = IF(estado IN ('completo','exento'), wa_phone_id, VALUES(wa_phone_id))",
            (huella, tel, phone_id_origen),
        )
        cur.execute("SELECT estado FROM supresion_telefonos WHERE huella = %s", (huella,))
        exento = (cur.fetchone() or {}).get("estado") == "exento"
        # Las eliminaciones permanentes conservan su huella propia en
        # supresion_telefonos; no dejan una baja temporal duplicada.
        retirar_huella_baja(cur, huella_bloqueo)
        conn.commit()
    if exento:
        return huella
    hilo = threading.Thread(target=_procesar_sin_excepcion, args=(huella,), daemon=True)
    hilo.start()
    return huella


def _procesar_sin_excepcion(huella: str) -> None:
    try:
        procesar_pendiente(huella)
    except Exception as exc:
        log_error("supresión: error al procesar una eliminación", exc)


def oferta_eliminacion_enviada(telefono: str) -> bool:
    tel = normalizar_telefono(telefono)
    if not tel:
        return False
    with conectar() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM whatsapp_avisos_unicos"
            " WHERE telefono = %s AND tipo = 'eliminacion_ofrecida'",
            (tel,),
        )
        return cur.fetchone() is not None


def _bloquear_en_meta(telefono: str, phone_id: str) -> bool:
    from whatsapp_channels import credenciales_centro
    try:
        token = credenciales_centro(phone_id)["token"]
        version = (config_get("wa_graph_version") or "v26.0").strip()
        if not token or not phone_id:
            log_error("supresión: faltan credenciales de Meta; se completa la eliminación local")
            return False
        url = f"https://graph.facebook.com/{version}/{phone_id}/block_users"
        headers = {"Authorization": f"Bearer {token}"}
        with httpx.Client(timeout=20) as cliente:
            respuesta = cliente.post(
                url, headers=headers,
                json={"messaging_product": "whatsapp",
                      "block_users": [{"user": telefono}]},
            )
            if respuesta.status_code == 200:
                data = respuesta.json()
                bloqueos = data.get("block_users") or {}
                if not bloqueos.get("errors") and not data.get("error"):
                    if any(normalizar_telefono(x.get("wa_id") or x.get("input") or "") == telefono
                           for x in bloqueos.get("added_users") or []):
                        return True
            # Un bloqueo ya aplicado puede no reaparecer en added_users tras
            # un reintento (p. ej. fallo local después de la respuesta de Meta).
            cursor = None
            for _ in range(64):
                params = {"limit": 1000}
                if cursor:
                    params["after"] = cursor
                listado = cliente.get(url, headers=headers, params=params)
                if listado.status_code != 200:
                    break
                datos = listado.json()
                if any(normalizar_telefono(x.get("wa_id") or "") == telefono
                       for x in datos.get("data") or []):
                    return True
                siguiente = ((datos.get("paging") or {}).get("cursors") or {}).get("after")
                if not siguiente or siguiente == cursor:
                    break
                cursor = siguiente
        log_error(f"supresión: Meta no confirmó el bloqueo ({respuesta.status_code})")
        return False
    except Exception as exc:
        log_error("supresión: no se pudo confirmar el bloqueo en Meta", exc)
        return False


def _dos_letras(texto: str) -> str:
    return (texto or "").strip()[:2]


def _nombre_reducido(nombre: str, apellido: str = "") -> str:
    partes = (nombre or "").strip().split()
    primero = _dos_letras(partes[0]) if partes else ""
    segundo = _dos_letras(apellido or (partes[-1] if len(partes) > 1 else ""))
    return " ".join(x for x in (primero, segundo) if x)


def _limpiar_datos(cur, telefono: str) -> tuple[int, int]:
    """Limpia datos del número en una sola transacción; conserva métricas de envíos."""
    import servicio_areas

    afectados = 0
    tablas_afectadas = 0
    ids_por_tabla = {}
    nombre_por_id = {}
    areas_por_tabla = {r["nombre_tabla_base"]: r["id"]
                       for r in servicio_areas.listar_tablas_areas()}
    areas_pequenas = set()
    for tabla in servicio_areas.tablas_de_pacientes():
        if not servicio_areas.tabla_valida(tabla):
            continue
        cur.execute(f"SELECT id, nombre, apellido, telefono FROM {tabla}")
        todas = cur.fetchall()
        if tabla in areas_por_tabla and len(todas) < 10:
            areas_pequenas.add(areas_por_tabla[tabla])
        filas = [r for r in todas
                 if normalizar_telefono(r.get("telefono") or "") == telefono]
        if not filas:
            continue
        ids = [r["id"] for r in filas]
        ids_por_tabla[tabla] = ids
        for r in filas:
            nombre_por_id[(tabla, r["id"])] = _nombre_reducido(
                r.get("nombre") or "", r.get("apellido") or "")
        ph = ", ".join(["%s"] * len(ids))
        cur.execute(
            f"DELETE FROM paciente_csv_accesos WHERE tabla_pacientes = %s AND paciente_id IN ({ph})",
            (tabla, *ids),
        )
        cur.execute(f"DELETE FROM {tabla} WHERE id IN ({ph})", tuple(ids))
        afectados += len(ids)
        tablas_afectadas += 1

    # Las listas de Reservo guardan una ficha operativa por lista. Se incluyen
    # sus IDs para seudonimizar el historial antes de borrar la ficha canónica;
    # la FK elimina todas las copias de contacto al borrar el origen.
    try:
        cur.execute("SELECT lp.id,lp.nombre,lp.apellido,lp.telefono"
                    " FROM reservo_lista_pacientes lp"
                    " WHERE lp.telefono=%s", (telefono,))
        filas_reservo = cur.fetchall()
        if filas_reservo:
            tabla_reservo = "reservo_lista_pacientes"
            ids_reservo = [fila["id"] for fila in filas_reservo]
            ids_por_tabla[tabla_reservo] = ids_reservo
            for fila in filas_reservo:
                nombre_por_id[(tabla_reservo, fila["id"])] = _nombre_reducido(
                    fila.get("nombre") or "", fila.get("apellido") or "")
        cur.execute("SELECT id,paciente_telefono FROM reservo_pacientes")
        ids_origen = [fila["id"] for fila in cur.fetchall()
                      if normalizar_telefono(fila.get("paciente_telefono") or "") == telefono]
        if ids_origen:
            ph_origen = ",".join(["%s"] * len(ids_origen))
            cur.execute(f"DELETE FROM reservo_pacientes WHERE id IN ({ph_origen})",
                        tuple(ids_origen))
            afectados += len(ids_origen)
            tablas_afectadas += 1
    except pymysql.err.OperationalError as exc:
        # Durante una actualización antigua puede que aún no exista el esquema.
        if not exc.args or exc.args[0] not in (1054, 1146):
            raise

    # Las cohortes congeladas, logs de call center y avisos también guardan PII.
    cur.execute(
        "SELECT pd.prog_id, pd.paciente_id, pd.telefono, ep.tabla"
        " FROM programado_destinatarios pd"
        " LEFT JOIN envios_programados ep ON ep.id = pd.prog_id"
    )
    for row in cur.fetchall():
        numero_guardado = normalizar_telefono(row.get("telefono") or "")
        coincide = numero_guardado == telefono or (
            numero_guardado is None
            and row.get("paciente_id") in ids_por_tabla.get(row.get("tabla") or "", ())
        )
        if coincide:
            cur.execute("DELETE FROM programado_destinatarios WHERE prog_id = %s AND paciente_id = %s",
                        (row["prog_id"], row["paciente_id"]))
    cur.execute("SELECT id, paciente_id, base_datos, numero_paciente FROM call_center_log")
    for row in cur.fetchall():
        por_numero = normalizar_telefono(row.get("numero_paciente") or "") == telefono
        por_id = row.get("paciente_id") in ids_por_tabla.get(row.get("base_datos") or "", ())
        if por_numero or por_id:
            cur.execute("DELETE FROM call_center_log WHERE id = %s", (row["id"],))
    # La derivación conserva el texto original; ELIMINAR debe retirarlo también.
    cur.execute("SELECT id, telefono FROM call_center_derivaciones")
    for row in cur.fetchall():
        if normalizar_telefono(row.get("telefono") or "") == telefono:
            cur.execute("DELETE FROM call_center_derivaciones WHERE id = %s", (row["id"],))
    cur.execute("DELETE FROM crm_derivaciones_pendientes WHERE telefono = %s", (telefono,))
    cur.execute("DELETE FROM campania_consultas WHERE telefono = %s", (telefono,))
    # La nueva cola de citas también contiene PII del paciente. Borrar toda
    # copia pendiente o enviada; el historial se reduce más abajo.
    cur.execute("DELETE FROM paciente_reagendar"
                " WHERE paciente_telefono_normalizado = %s", (telefono,))
    cur.execute("DELETE FROM reagendar_destinatarios WHERE telefono = %s", (telefono,))
    cur.execute("DELETE FROM whatsapp_conversaciones_mensajes WHERE telefono_paciente = %s", (telefono,))
    cur.execute("DELETE FROM call_center_prueba_interes WHERE telefono = %s", (telefono,))
    # La cuota por pareja guarda solo la huella, pero sigue derivándose del
    # teléfono: también debe desaparecer al tramitar ELIMINAR.
    from wa_dispatch_limit import huella_pareja
    cur.execute("SELECT phone_id FROM wa_despacho_canales")
    for canal in cur.fetchall():
        phone_id = canal.get("phone_id")
        if not phone_id:
            continue
        pareja_hash = huella_pareja(phone_id, telefono)
        cur.execute("DELETE FROM wa_despacho_pares"
                    " WHERE phone_id = %s AND destinatario_hash = %s", (phone_id, pareja_hash))
    cur.execute("SELECT telefono, tipo FROM whatsapp_avisos_unicos")
    for row in cur.fetchall():
        if normalizar_telefono(row.get("telefono") or "") == telefono:
            cur.execute("DELETE FROM whatsapp_avisos_unicos WHERE telefono = %s AND tipo = %s",
                        (row["telefono"], row["tipo"]))

    cur.execute("SELECT id, paciente_id, area_id, tabla_pacientes,"
                " nombre_paciente, numero_telefono FROM log_envios")
    for row in cur.fetchall():
        tabla = row.get("tabla_pacientes") or ""
        numero_guardado = normalizar_telefono(row.get("numero_telefono") or "")
        coincide = numero_guardado == telefono
        if not coincide and numero_guardado is None and tabla in ids_por_tabla:
            coincide = row.get("paciente_id") in ids_por_tabla[tabla]
        if not coincide and numero_guardado is None and not tabla:
            coincide = any(row.get("paciente_id") in ids_por_tabla.get(t, ())
                           for t in ("pacientes_dev", "pacientes_prod"))
        if not coincide:
            continue
        # Las etiquetas de revisión también revelan información sobre la
        # respuesta; no deben sobrevivir a ELIMINAR junto al historial reducido.
        cur.execute("DELETE FROM respuesta_etiquetas WHERE log_envio_id = %s", (row["id"],))
        area_pequena = (row.get("area_id") in areas_pequenas
                        or (tabla in areas_por_tabla and areas_por_tabla[tabla] in areas_pequenas))
        nombre = None if area_pequena else (
            nombre_por_id.get((tabla, row.get("paciente_id")))
            or _nombre_reducido(row.get("nombre_paciente") or ""))
        numero = None if area_pequena else "****" + telefono[-4:]
        cur.execute(
            "UPDATE log_envios SET paciente_id = NULL, nombre_paciente = %s,"
            " numero_telefono = %s, mensaje = NULL, descripcion_error = NULL,"
            " whatsapp_message_id = NULL WHERE id = %s",
            (nombre, numero, row["id"]),
        )

    # Los eventos antiguos guardaron el payload original. Se vacían los que
    # contienen este número; los nuevos solo almacenan el hash del evento.
    digitos = telefono.lstrip("+")
    cur.execute("SELECT id, payload FROM whatsapp_eventos WHERE payload IS NOT NULL")
    for row in cur.fetchall():
        if digitos in re.sub(r"\D", "", row.get("payload") or ""):
            cur.execute("UPDATE whatsapp_eventos SET payload = '[suprimido]' WHERE id = %s",
                        (row["id"],))
    # Al final de la transacción: incluye mensajes sin paciente y cualquier
    # respuesta que haya entrado mientras se recorrían las demás tablas.
    cur.execute(
        "DELETE c FROM corpus_respuestas_completas c"
        " INNER JOIN corpus_respuestas_origen o ON o.corpus_id = c.id"
        " WHERE o.huella = %s", (huella_telefono(telefono),),
    )
    return tablas_afectadas, afectados


def procesar_pendiente(huella: str) -> bool:
    with conectar() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE supresion_telefonos SET estado = 'procesando', completado = NOW()"
            " WHERE huella = %s AND (estado = 'pendiente' OR"
            " (estado = 'procesando' AND completado < DATE_SUB(NOW(), INTERVAL 2 MINUTE)))",
            (huella,),
        )
        reclamado = cur.rowcount == 1
        conn.commit()
        if not reclamado:
            return False
        cur.execute("SELECT telefono_pendiente, wa_phone_id"
                    " FROM supresion_telefonos WHERE huella = %s", (huella,))
        fila = cur.fetchone()
    try:
        telefono = (fila or {}).get("telefono_pendiente")
        if not telefono:
            return False
        # Meta solo permite bloquear desde el número al que el paciente escribió.
        # El veto local por huella ya cubre a todas las clínicas de SNW.
        phone_id_origen = (fila or {}).get("wa_phone_id")
        if phone_id_origen:
            try:
                _bloquear_en_meta(telefono, phone_id_origen)
            except Exception as exc:
                # Nunca hacer depender el derecho a eliminación de Meta.
                log_error("supresión: fallo al intentar el bloqueo en Meta", exc)
        else:
            log_error("supresión: falta el emisor de origen para bloquear en Meta; "
                      "se completa la eliminación local")
        # Se conserva la huella y una constancia sin PII; se elimina el número crudo.
        with conectar() as conn, conn.cursor() as cur:
            bases, pacientes = _limpiar_datos(cur, telefono)
            cur.execute(
                "UPDATE supresion_telefonos SET estado = 'completo',"
                " telefono_pendiente = NULL, bases_afectadas = %s,"
                " pacientes_eliminados = %s, completado = NOW() WHERE huella = %s",
                (bases, pacientes, huella),
            )
            conn.commit()
        return True
    finally:
        # Solo un fallo de la limpieza local deja el teléfono pendiente para retry.
        with conectar() as conn, conn.cursor() as cur:
            cur.execute("UPDATE supresion_telefonos SET estado = 'pendiente',"
                        " completado = NULL WHERE huella = %s AND estado = 'procesando'",
                        (huella,))
            conn.commit()


def reintentar_pendientes() -> None:
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("SELECT huella FROM supresion_telefonos"
                    " WHERE estado = 'pendiente' OR"
                    " (estado = 'procesando' AND completado < DATE_SUB(NOW(), INTERVAL 2 MINUTE))"
                    " ORDER BY creado LIMIT 10")
        pendientes = [r["huella"] for r in cur.fetchall()]
    for huella in pendientes:
        try:
            procesar_pendiente(huella)
        except Exception as exc:
            log_error("supresión: error al reintentar una eliminación", exc)


def resumen_supresion() -> dict:
    """Conteos operativos sin huellas ni teléfonos, para desarrolladores."""
    resultado = {"pendientes": 0, "procesando": 0, "completas": 0, "exentos": 0}
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("SELECT estado, COUNT(*) AS n FROM supresion_telefonos GROUP BY estado")
        for row in cur.fetchall():
            clave = {"completo": "completas", "exento": "exentos"}.get(
                row["estado"], row["estado"])
            if clave in resultado:
                resultado[clave] = int(row["n"])
    return resultado
