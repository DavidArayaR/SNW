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

import httpx

from db import BASE_DIR, conectar, config_get, log_error
from telefono import normalizar_telefono

RUTA_CLAVE = BASE_DIR / ".env.supresion"


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
            ") CHARACTER SET utf8mb4"
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
        conn.commit()


@lru_cache(maxsize=1)
def _clave_hmac() -> bytes:
    try:
        clave = RUTA_CLAVE.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        # Nunca generar silenciosamente otra clave si ya existen huellas.
        with conectar() as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM supresion_telefonos")
            if (cur.fetchone() or {}).get("n"):
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
    with conectar() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO supresion_telefonos (huella, telefono_pendiente, wa_phone_id)"
            " VALUES (%s, %s, %s)"
            " ON DUPLICATE KEY UPDATE"
            " telefono_pendiente = IF(estado IN ('completo','exento'), NULL, VALUES(telefono_pendiente)),"
            " wa_phone_id = IF(estado IN ('completo','exento'), wa_phone_id, VALUES(wa_phone_id))",
            (huella, tel, (config_get("wa_phone_id") or "").strip()),
        )
        cur.execute("SELECT estado FROM supresion_telefonos WHERE huella = %s", (huella,))
        exento = (cur.fetchone() or {}).get("estado") == "exento"
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
    token = (config_get("wa_token") or "").strip()
    version = (config_get("wa_graph_version") or "v26.0").strip()
    if not token or not phone_id:
        log_error("supresión: faltan credenciales de Meta; se mantiene pendiente")
        return False
    try:
        url = f"https://graph.facebook.com/{version}/{phone_id}/block_users"
        headers = {"Authorization": f"Bearer {token}"}
        with httpx.Client(timeout=20) as cliente:
            respuesta = cliente.post(
                url, headers=headers,
                json={"messaging_product": "whatsapp",
                      "block_users": [{"user": telefono.lstrip("+")}]},
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
        if not telefono or not _bloquear_en_meta(telefono, fila.get("wa_phone_id") or ""):
            return False
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
        # Si Meta o la limpieza fallan, conservar el teléfono solo para retry.
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
