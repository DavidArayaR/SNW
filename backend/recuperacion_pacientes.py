"""Solicitudes persistentes y análisis de última atención efectiva en Reservo."""

import asyncio
import hashlib
import json
from calendar import monthrange
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, Field

from db import config_all, conectar, log_error
from reservo_catalogo import (RESERVO_CLINICAS, clave_umbral_recuperacion as clave_umbral,
                             clave_cobertura_recuperacion as clave_cobertura,
                             clave_token, clave_estado_atendido, clave_estado_verificado)
from reservo_pacientes import (ClienteReservo, ReservoError, codigo_estado, dividir_periodo,
                               fecha_atencion_utc, generar_nombre_base, normalizar_cliente_uuid,
                               cliente_valido, _guardar_paciente_cursor, tratamientos_cita, validar_uuid)
from snw.seguridad.supresion import (bloquear_huella_baja, limpiar_cerrojos_baja_inactivos,
                       telefonos_bloqueados_para_envio)
from snw.pacientes.telefono import normalizar_telefono


RUTA = "/api/v1/recuperacion-pacientes"
ZONA = ZoneInfo("America/Santiago")
CLINICAS = {("VS" if codigo == "MVYS" else codigo): codigo for codigo in RESERVO_CLINICAS}
_CAMPOS = (
    "id, solicitud_id, clinica_codigo, anio, mes, meses_inactividad, "
    "fecha_referencia, estado, version, creado_en, tratamientos_json"
)


class SolicitudRecuperacionIn(BaseModel):
    clinica_codigo: str
    anio: int = Field(ge=1000, le=9999)
    mes: int = Field(ge=1, le=12)
    meses_inactividad: int | None = Field(default=None, ge=1, le=120, strict=True)
    tratamientos: list[str] = Field(default_factory=list, max_length=50)


class ConsultaTratamientosIn(BaseModel):
    clinica_codigo: str
    anio: int = Field(ge=1000, le=9999)
    mes: int = Field(ge=1, le=12)


def configurar_tablas(cur):
    cur.execute("SHOW COLUMNS FROM reservo_bases_contacto LIKE 'sync_job_id'")
    if cur.fetchone()["Null"] == "NO":
        cur.execute("ALTER TABLE reservo_bases_contacto MODIFY sync_job_id BIGINT UNSIGNED NULL")
    cur.execute(
        "CREATE TABLE IF NOT EXISTS recuperacion_solicitudes ("
        " id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,"
        " solicitud_id CHAR(32) NOT NULL UNIQUE,"
        " usuario VARCHAR(150) NOT NULL,"
        " clave_idempotencia VARBINARY(128) NOT NULL,"
        " clinica_codigo VARCHAR(8) NOT NULL,"
        " anio SMALLINT UNSIGNED NOT NULL, mes TINYINT UNSIGNED NOT NULL,"
        " meses_inactividad TINYINT UNSIGNED NOT NULL, fecha_referencia DATE NOT NULL,"
        " estado VARCHAR(20) NOT NULL DEFAULT 'PENDIENTE', version INT NOT NULL DEFAULT 1,"
        " creado_en DATETIME NOT NULL,"
        " UNIQUE KEY uq_rec_usuario_clave (usuario, clave_idempotencia),"
        " KEY idx_rec_usuario_id (usuario, id),"
        " KEY idx_rec_estado_id (estado, id)"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
    )
    cur.execute("SHOW COLUMNS FROM recuperacion_solicitudes LIKE 'tratamientos_json'")
    if not cur.fetchone():
        cur.execute("ALTER TABLE recuperacion_solicitudes ADD tratamientos_json TEXT NULL")
    cur.execute(
        "CREATE TABLE IF NOT EXISTS recuperacion_publicaciones ("
        " solicitud BIGINT UNSIGNED PRIMARY KEY, base_id INT NULL UNIQUE,"
        " contactos INT UNSIGNED NOT NULL,"
        " FOREIGN KEY (solicitud) REFERENCES recuperacion_solicitudes(id) ON DELETE CASCADE,"
        " FOREIGN KEY (base_id) REFERENCES reservo_bases_contacto(id) ON DELETE SET NULL"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
    )
    cur.execute(
        "CREATE TABLE IF NOT EXISTS recuperacion_analisis ("
        " solicitud BIGINT UNSIGNED PRIMARY KEY, siguiente_fecha DATE NOT NULL,"
        " huella_fuente CHAR(64) NOT NULL, tramos INT UNSIGNED NOT NULL DEFAULT 0,"
        " paginas INT UNSIGNED NOT NULL DEFAULT 0, citas_sin_uuid INT UNSIGNED NOT NULL DEFAULT 0,"
        " pacientes_analizados INT UNSIGNED NULL, pacientes_inactivos INT UNSIGNED NULL,"
        " intentos INT UNSIGNED NOT NULL DEFAULT 0, reintentar_en DATETIME NULL,"
        " error VARCHAR(255) NULL, finalizado_en DATETIME NULL,"
        " FOREIGN KEY (solicitud) REFERENCES recuperacion_solicitudes(id) ON DELETE CASCADE"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
    )
    cur.execute(
        "CREATE TABLE IF NOT EXISTS recuperacion_cohorte ("
        " solicitud BIGINT UNSIGNED NOT NULL, cliente_uuid VARBINARY(320) NOT NULL,"
        " ultima_atencion DATE NOT NULL, PRIMARY KEY (solicitud,cliente_uuid),"
        " FOREIGN KEY (solicitud) REFERENCES recuperacion_solicitudes(id) ON DELETE CASCADE"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
    )
    cur.execute("SHOW COLUMNS FROM recuperacion_cohorte LIKE 'ultima_atencion_utc'")
    if not cur.fetchone():
        cur.execute("ALTER TABLE recuperacion_cohorte ADD ultima_atencion_utc DATETIME(6) NULL,"
                    " ADD coincide_tratamiento TINYINT(1) NOT NULL DEFAULT 1")
    cur.execute("SHOW COLUMNS FROM recuperacion_cohorte LIKE 'tratamiento_ultima_atencion'")
    if not cur.fetchone():
        cur.execute("ALTER TABLE recuperacion_cohorte ADD tratamiento_ultima_atencion TEXT NULL")
    cur.execute(
        "CREATE TABLE IF NOT EXISTS recuperacion_consultas_tratamientos ("
        " id CHAR(32) PRIMARY KEY, usuario VARCHAR(150) NOT NULL, clinica_codigo VARCHAR(8) NOT NULL,"
        " desde DATE NOT NULL, hasta DATE NOT NULL, creado_en DATETIME NOT NULL,"
        " estado VARCHAR(16) NOT NULL DEFAULT 'PENDIENTE', paginas INT UNSIGNED NOT NULL DEFAULT 0,"
        " resultado LONGTEXT NULL, error VARCHAR(255) NULL, finalizado_en DATETIME NULL,"
        " KEY idx_rec_consultas_estado (estado,creado_en), KEY idx_rec_consultas_fin (finalizado_en)"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
    )


def umbral_valido(valor):
    return valor.isascii() and valor.isdecimal() and len(valor) <= 3 and 1 <= int(valor) <= 120


def _clinica(codigo):
    codigo = codigo.strip().upper()
    codigo = "VS" if codigo == "MVYS" else codigo
    if codigo not in CLINICAS:
        raise HTTPException(422, detail="Clínica de Reservo inválida.")
    return codigo


def listar_clinicas():
    cfg = config_all()
    items = []
    for canonico, origen in CLINICAS.items():
        valor = cfg.get(clave_umbral(origen), "")
        items.append({"clinica_codigo": canonico, "nombre": RESERVO_CLINICAS[origen],
                      "meses_inactividad": int(valor) if umbral_valido(valor) else None})
    return {"items": items}


def registrar_consulta_tratamientos(usuario, body):
    codigo = _clinica(body.clinica_codigo)
    desde = date(body.anio, body.mes, 1)
    if desde >= datetime.now(ZONA).date().replace(day=1):
        raise HTTPException(422, detail="Selecciona un mes histórico completo.")
    hasta = desde.replace(day=monthrange(body.anio, body.mes)[1])
    with conectar() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM recuperacion_consultas_tratamientos WHERE usuario=%s AND clinica_codigo=%s"
            " AND desde=%s AND (estado IN ('PENDIENTE','PROCESANDO')"
            " OR (estado='COMPLETADA' AND finalizado_en>=UTC_TIMESTAMP()-INTERVAL 30 MINUTE))"
            " ORDER BY creado_en DESC LIMIT 1", (usuario, codigo, desde),
        )
        anterior = cur.fetchone()
        if anterior:
            return anterior
        cur.execute("DELETE FROM recuperacion_consultas_tratamientos"
                    " WHERE finalizado_en < UTC_TIMESTAMP()-INTERVAL 1 DAY")
        identificador = uuid4().hex
        cur.execute("INSERT INTO recuperacion_consultas_tratamientos (id,usuario,clinica_codigo,desde,hasta,creado_en)"
                    " VALUES (%s,%s,%s,%s,%s,UTC_TIMESTAMP())",
                    (identificador, usuario, codigo, desde, hasta))
        conn.commit()
    return {"id": identificador}


def obtener_consulta_tratamientos(usuario, identificador):
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("SELECT estado,paginas,resultado,error FROM recuperacion_consultas_tratamientos"
                    " WHERE id=%s AND usuario=%s", (identificador, usuario))
        fila = cur.fetchone()
    if not fila:
        raise HTTPException(404, detail="Consulta de tratamientos no disponible.")
    fila["items"] = json.loads(fila.pop("resultado") or "[]")
    return fila


def procesar_consulta_tratamientos():
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("SELECT GET_LOCK('snw-recuperacion-tratamientos',0) AS adquirido")
        if not cur.fetchone()["adquirido"]:
            return False
        cur.execute("SELECT id,clinica_codigo,desde,hasta FROM recuperacion_consultas_tratamientos"
                    " WHERE estado IN ('PENDIENTE','PROCESANDO') ORDER BY creado_en,id LIMIT 1")
        consulta = cur.fetchone()
        if not consulta:
            conn.commit()
            return False
        identificador = consulta["id"]
        codigo = consulta["clinica_codigo"]
        cur.execute("UPDATE recuperacion_consultas_tratamientos SET estado='PROCESANDO',paginas=0 WHERE id=%s",
                    (identificador,))
        conn.commit()

        def registrar_pagina(*_):
            cur.execute("UPDATE recuperacion_consultas_tratamientos SET paginas=paginas+1 WHERE id=%s",
                        (identificador,))
            conn.commit()

        cliente = None
        items, error = [], None
        try:
            token = config_all(refresh=True).get(clave_token(CLINICAS[codigo]), "").strip()
            if not token:
                raise ReservoError("Configura el token de Reservo de esta clínica.")
            cliente = ClienteReservo(token)
            items = cliente.listar_tratamientos(
                consulta["desde"], consulta["hasta"], al_recibir_pagina=registrar_pagina,
            )
        except ReservoError as exc:
            error = exc.categoria[:255]
            log_error(f"consulta de tratamientos de recuperación ({codigo}, {identificador}): {error}")
        except Exception as exc:
            error = "No se pudieron consultar los tratamientos de Reservo."
            log_error(f"consulta de tratamientos de recuperación ({codigo}, {identificador})", exc, solo_tipo=True)
        finally:
            if cliente:
                cliente.close()
        cur.execute(
            "UPDATE recuperacion_consultas_tratamientos SET estado=%s,resultado=%s,error=%s,"
            " finalizado_en=UTC_TIMESTAMP() WHERE id=%s",
            ("ERROR" if error else "COMPLETADA", json.dumps(items), error, identificador),
        )
        conn.commit()
        return True


async def loop_consultas_tratamientos():
    while True:
        try:
            if not await asyncio.to_thread(procesar_consulta_tratamientos):
                await asyncio.sleep(5)
        except Exception as exc:
            log_error("worker de tratamientos de recuperación", exc, solo_tipo=True)
            await asyncio.sleep(10)


def _publica(fila):
    publica = {k: v for k, v in fila.items() if k != "id"}
    publica["tratamientos"] = json.loads(publica.pop("tratamientos_json", None) or "[]")
    publica["creado_en"] = fila["creado_en"].replace(tzinfo=timezone.utc)
    return publica


def _buscar_clave(cur, usuario, clave):
    cur.execute(f"SELECT {_CAMPOS} FROM recuperacion_solicitudes"
                " WHERE usuario=%s AND clave_idempotencia=%s FOR UPDATE", (usuario, clave))
    return cur.fetchone()


def registrar(usuario, clave, body):
    if not clave or len(clave) > 128 or any(not 33 <= ord(c) <= 126 for c in clave):
        raise HTTPException(422, detail="La clave de idempotencia no es válida.")
    clave = clave.encode("ascii")
    codigo = _clinica(body.clinica_codigo)
    try:
        tratamientos = sorted({validar_uuid(uuid).replace("-", "") for uuid in body.tratamientos})
    except ValueError as exc:
        raise HTTPException(422, detail="Selecciona tratamientos válidos de Reservo.") from exc
    parametros = (codigo, body.anio, body.mes)
    with conectar() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {_CAMPOS} FROM recuperacion_solicitudes"
                    " WHERE usuario=%s AND clave_idempotencia=%s", (usuario, clave))
        fila = cur.fetchone()
        nueva = False
        if fila is None:
            ahora = datetime.now(ZoneInfo("America/Santiago"))
            if (body.anio, body.mes) >= (ahora.year, ahora.month):
                raise HTTPException(422, detail="Selecciona un mes histórico completo.")
            meses = body.meses_inactividad
            if meses is None:
                valor = config_all().get(clave_umbral(CLINICAS[codigo]), "")
                if not umbral_valido(valor):
                    raise HTTPException(503, detail="Indica los meses de inactividad.")
                meses = int(valor)
            solicitud_id = uuid4().hex
            cur.execute(
                "INSERT INTO recuperacion_solicitudes"
                " (solicitud_id,usuario,clave_idempotencia,clinica_codigo,anio,mes,"
                " meses_inactividad,fecha_referencia,creado_en,tratamientos_json)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                " ON DUPLICATE KEY UPDATE id=id",
                (solicitud_id, usuario, clave, *parametros, meses, ahora.date(),
                 ahora.astimezone(timezone.utc).replace(tzinfo=None), json.dumps(tratamientos)),
            )
            fila = _buscar_clave(cur, usuario, clave)
            nueva = fila["solicitud_id"] == solicitud_id
        if ((fila["clinica_codigo"], fila["anio"], fila["mes"]) != parametros
                or json.loads(fila["tratamientos_json"] or "[]") != tratamientos
                or (body.meses_inactividad is not None
                    and fila["meses_inactividad"] != body.meses_inactividad)):
            raise HTTPException(409, detail="La clave ya se usó con otros parámetros.")
        conn.commit()
        return _publica(fila), nueva


def listar(usuario, clinica, cursor, limite):
    condiciones = ["usuario=%s"]
    args = [usuario]
    if clinica:
        condiciones.append("clinica_codigo=%s")
        args.append(_clinica(clinica))
    if cursor is not None:
        condiciones.append("id<%s")
        args.append(cursor)
    with conectar() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {_CAMPOS} FROM recuperacion_solicitudes WHERE "
                    + " AND ".join(condiciones) + " ORDER BY id DESC LIMIT %s",
                    (*args, limite + 1))
        filas = cur.fetchall()
    pagina = filas[:limite]
    return {"items": [_publica(f) for f in pagina],
            "siguiente_cursor": str(pagina[-1]["id"]) if len(filas) > limite else None}


def obtener(usuario, solicitud_id):
    with conectar() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {_CAMPOS} FROM recuperacion_solicitudes"
                    " WHERE usuario=%s AND solicitud_id=%s", (usuario, solicitud_id))
        fila = cur.fetchone()
        if fila:
            cur.execute("SELECT siguiente_fecha,tramos,paginas FROM recuperacion_analisis"
                        " WHERE solicitud=%s", (fila["id"],))
            fila["progreso"] = cur.fetchone()
    if not fila:
        raise HTTPException(404, detail="Solicitud no disponible.")
    return _publica(fila)


def obtener_resultado(usuario, solicitud_id):
    with conectar() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT s.solicitud_id,s.estado,s.version,a.finalizado_en,a.error,"
            " a.pacientes_analizados,a.pacientes_inactivos,a.citas_sin_uuid,p.base_id,p.contactos"
            " FROM recuperacion_solicitudes s LEFT JOIN recuperacion_analisis a ON a.solicitud=s.id"
            " LEFT JOIN recuperacion_publicaciones p ON p.solicitud=s.id"
            " WHERE s.usuario=%s AND s.solicitud_id=%s", (usuario, solicitud_id),
        )
        fila = cur.fetchone()
    if not fila:
        raise HTTPException(404, detail="Solicitud no disponible.")
    if fila["estado"] in ("PENDIENTE", "PROCESANDO"):
        raise HTTPException(409, detail="La solicitud todavía no tiene un resultado.")
    return {
        "solicitud_id": solicitud_id, "estado": fila["estado"], "version": fila["version"],
        "finalizado_en": fila["finalizado_en"].replace(tzinfo=timezone.utc)
        if fila["finalizado_en"] else None,
        "conteos": {k: fila[k] for k in ("pacientes_analizados", "pacientes_inactivos", "citas_sin_uuid")}
        if fila["pacientes_analizados"] is not None else None,
        "lista_generada_id": fila["base_id"],
        "contactos_publicados": fila["contactos"],
        "error": {"codigo": "ANALISIS_RESERVO", "detalle": fila["error"]} if fila["error"] else None,
    }


def fecha_umbral(referencia: date, meses: int) -> date:
    anio, indice_mes = divmod(referencia.year * 12 + referencia.month - 1 - meses, 12)
    mes = indice_mes + 1
    return date(anio, mes, min(referencia.day, monthrange(anio, mes)[1]))


def _solicitud_contactos(cur, usuario, solicitud_id, *, bloquear=False):
    cur.execute("SELECT id,solicitud_id,usuario,estado,clinica_codigo,anio,mes,fecha_referencia,"
                " meses_inactividad,creado_en,tratamientos_json"
                " FROM recuperacion_solicitudes WHERE usuario=%s AND solicitud_id=%s"
                + (" FOR UPDATE" if bloquear else ""), (usuario, solicitud_id))
    solicitud = cur.fetchone()
    if not solicitud:
        raise HTTPException(404, detail="Solicitud no disponible.")
    return solicitud


def _contactos(cur, solicitud, *, bloquear=False):
    if solicitud["estado"] != "ANALIZADA":
        raise HTTPException(409, detail="La revisión requiere un análisis terminado.")
    origen = CLINICAS[solicitud["clinica_codigo"]]
    limite = fecha_umbral(solicitud["fecha_referencia"], solicitud["meses_inactividad"])
    filtra_tratamientos = bool(json.loads(solicitud["tratamientos_json"] or "[]"))
    cur.execute(
        "SELECT p.id AS ficha_id,p.paciente_telefono,p.paciente_nombre,p.paciente_apellido,"
        " p.cliente_uuid,p.fecha_ultima_atencion AS ultima_local,c.ultima_atencion,"
        " c.ultima_atencion_utc,c.tratamiento_ultima_atencion,"
        " c.ultima_atencion<=%s AND c.coincide_tratamiento=1 AS inactivo"
        " FROM recuperacion_cohorte c"
        " LEFT JOIN reservo_pacientes p ON p.clinica_codigo=%s"
        " AND BINARY p.cliente_uuid=c.cliente_uuid WHERE c.solicitud=%s"
        + (" FOR UPDATE" if bloquear else ""), (limite, origen, solicitud["id"]),
    )
    inactivos = sin_ficha = invalidos = activos_recientes = 0
    frecuencias = Counter()
    candidatos = {}
    for fila in cur.fetchall():
        tel = normalizar_telefono(fila["paciente_telefono"] or "")
        if tel:
            frecuencias[tel] += 1
        if not fila["inactivo"]:
            continue
        inactivos += 1
        if fila["ficha_id"] is None:
            sin_ficha += 1
        elif fila["ultima_local"] and fila["ultima_local"].replace(tzinfo=timezone.utc).astimezone(ZONA).date() > limite:
            activos_recientes += 1
        elif (filtra_tratamientos and fila["ultima_local"] and fila["ultima_atencion_utc"]
              and fila["ultima_local"] > fila["ultima_atencion_utc"]):
            activos_recientes += 1
        elif tel:
            candidatos.setdefault(tel, []).append(fila)
        else:
            invalidos += 1
    compartidos = sum(len(filas) for tel, filas in candidatos.items() if frecuencias[tel] > 1)
    unicos = {tel: filas[0] for tel, filas in candidatos.items() if frecuencias[tel] == 1}
    return unicos, {"pacientes_inactivos": inactivos, "sin_ficha_local": sin_ficha,
                    "telefonos_invalidos": invalidos, "telefonos_compartidos": compartidos,
                    "atenciones_recientes": activos_recientes}


def revisar_contactos(usuario, solicitud_id):
    with conectar() as conn, conn.cursor() as cur:
        solicitud = _solicitud_contactos(cur, usuario, solicitud_id)
        unicos, conteos = _contactos(cur, solicitud)
    if conteos["sin_ficha_local"]:
        _completar_fichas(solicitud)
        with conectar() as conn, conn.cursor() as cur:
            solicitud = _solicitud_contactos(cur, usuario, solicitud_id)
            unicos, conteos = _contactos(cur, solicitud)
    bloqueados = telefonos_bloqueados_para_envio(set(unicos)) if unicos else set()
    disponibles = len(unicos.keys() - bloqueados)
    nombre = generar_nombre_base(CLINICAS[solicitud["clinica_codigo"]], "pacientes_inactivos",
                                 fecha_periodo=date(solicitud["anio"], solicitud["mes"], 1))
    return {
        "solicitud_id": solicitud_id, "nombre_propuesto": nombre["nombre"],
        "fuente_contactos": "fichas_reservo_sincronizadas",
        "conteos": {**conteos, "bloqueados": len(bloqueados), "contactos_sin_bloqueo": disponibles},
        "publicacion_disponible": bool(disponibles),
    }


def _completar_fichas(solicitud):
    """Recupera contactos de análisis anteriores a la sincronización automática."""
    origen = CLINICAS[solicitud["clinica_codigo"]]
    cfg = config_all(refresh=True)
    token = cfg.get(clave_token(origen), "").strip()
    atendido = cfg.get(clave_estado_atendido(origen), "").strip()
    if not token or not atendido or cfg.get(clave_estado_verificado(origen)) != "true":
        raise HTTPException(503, detail="Revisa la configuración de Reservo de esta clínica.")
    inicio = date(solicitud["anio"], solicitud["mes"], 1)
    fin = inicio.replace(day=monthrange(inicio.year, inicio.month)[1])
    cliente = ClienteReservo(token)
    try:
        for desde, hasta in dividir_periodo(inicio, fin, dias_por_tramo=7):
            _, _, _, contactos = _leer_tramo(cliente, desde, hasta, atendido, solicitud["creado_en"], set())
            if not contactos:
                continue
            with conectar() as conn, conn.cursor() as cur:
                actual = _solicitud_contactos(cur, solicitud["usuario"], solicitud["solicitud_id"])
                if actual["estado"] != "ANALIZADA":
                    raise HTTPException(409, detail="La solicitud ya no está disponible para revisión.")
                marcas = ",".join(["%s"] * len(contactos))
                cur.execute("SELECT c.cliente_uuid FROM recuperacion_cohorte c"
                            " LEFT JOIN reservo_pacientes p ON p.clinica_codigo=%s"
                            " AND BINARY p.cliente_uuid=c.cliente_uuid"
                            f" WHERE c.solicitud=%s AND p.id IS NULL AND c.cliente_uuid IN ({marcas})",
                            (origen, solicitud["id"], *contactos))
                faltantes = {fila["cliente_uuid"] for fila in cur.fetchall()}
                cerrojos = []
                for uuid in sorted(faltantes):
                    paciente, fecha = contactos[uuid]
                    _guardar_paciente_cursor(cur, origen, paciente, [], fecha, cerrojos)
                limpiar_cerrojos_baja_inactivos(cur, cerrojos)
                conn.commit()
    finally:
        cliente.close()


def publicar_base(usuario, solicitud_id):
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("SELECT GET_LOCK('snw-reservo-listas', 5) AS adquirido")
        if not cur.fetchone()["adquirido"]:
            raise HTTPException(409, detail="Hay otra base guardándose. Intenta nuevamente.")
        try:
            solicitud = _solicitud_contactos(cur, usuario, solicitud_id, bloquear=True)
            cur.execute("SELECT base_id,contactos FROM recuperacion_publicaciones WHERE solicitud=%s",
                        (solicitud["id"],))
            existente = cur.fetchone()
            if existente:
                if existente["base_id"] is None:
                    raise HTTPException(409, detail="La base de esta solicitud fue eliminada.")
                return {"lista_generada_id": existente["base_id"], "contactos": existente["contactos"]}, False
            candidatos, _ = _contactos(cur, solicitud, bloquear=True)
            bloqueados = telefonos_bloqueados_para_envio(set(candidatos)) if candidatos else set()
            filas, cerrojos = [], []
            for tel in sorted(candidatos.keys() - bloqueados):
                huella, baja = bloquear_huella_baja(cur, tel)
                if baja:
                    continue
                cerrojos.append(huella)
                cur.execute("SELECT estado FROM supresion_telefonos WHERE huella=%s FOR UPDATE", (huella,))
                supresion = cur.fetchone()
                if supresion and supresion["estado"] != "exento":
                    continue
                cur.execute("SELECT 1 FROM whatsapp_avisos_unicos WHERE telefono=%s"
                            " AND tipo='eliminacion_simulada' FOR UPDATE", (tel,))
                if cur.fetchone():
                    continue
                fila = candidatos[tel]
                filas.append((fila["ficha_id"], fila["cliente_uuid"], fila["paciente_nombre"],
                              fila["paciente_apellido"], tel, fila["ultima_atencion"],
                              fila["tratamiento_ultima_atencion"]))
            if not filas:
                raise HTTPException(409, detail="La solicitud no tiene contactos disponibles para publicar.")
            desde = date(solicitud["anio"], solicitud["mes"], 1)
            hasta = desde.replace(day=monthrange(desde.year, desde.month)[1])
            origen = CLINICAS[solicitud["clinica_codigo"]]
            datos = generar_nombre_base(origen, "pacientes_inactivos", fecha_periodo=desde)
            nombre = datos["nombre"]
            sufijo = 1
            while True:
                cur.execute("SELECT id FROM reservo_bases_contacto WHERE nombre=%s", (nombre,))
                if not cur.fetchone():
                    break
                sufijo += 1
                nombre = f"{datos['nombre']}_{sufijo}"
            cur.execute(
                "INSERT INTO reservo_bases_contacto (nombre_personalizado,nombre,clinica_codigo,"
                " origen,sync_job_id,tratamientos_json,fecha_desde,fecha_hasta,mes,anio,creado_por)"
                " VALUES (%s,%s,%s,'RECUPERACION',NULL,'[]',%s,%s,%s,%s,%s)",
                (datos["nombre_personalizado"], nombre, origen, desde, hasta,
                 datos["mes"], solicitud["anio"], usuario),
            )
            base_id = cur.lastrowid
            for inicio in range(0, len(filas), 500):
                cur.executemany(
                    "INSERT INTO reservo_lista_pacientes (lista_id,paciente_id,cliente_uuid,"
                    " nombre,apellido,telefono,fecha_ultima_atencion,tratamiento_ultima_atencion,"
                    " telefono_valido,consentimiento_verificado)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,1,1)",
                    [(base_id, *fila) for fila in filas[inicio:inicio + 500]],
                )
            cur.execute("INSERT INTO recuperacion_publicaciones (solicitud,base_id,contactos)"
                        " VALUES (%s,%s,%s)", (solicitud["id"], base_id, len(filas)))
            cur.execute("UPDATE recuperacion_solicitudes SET estado='COMPLETADA',version=version+1 WHERE id=%s",
                        (solicitud["id"],))
            limpiar_cerrojos_baja_inactivos(cur, cerrojos)
            conn.commit()
            return {"lista_generada_id": base_id, "contactos": len(filas)}, True
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.execute("SELECT RELEASE_LOCK('snw-reservo-listas')")


def _leer_tramo(cliente, desde, hasta, atendido, instante, seleccionados):
    """Consolida un tramo completo antes de confirmar su checkpoint."""
    ultimas = {}
    contactos = {}
    sin_uuid = paginas = 0
    for _, _, _, citas in cliente.iterar_citas(desde, hasta, exigir_cobertura=True):
        paginas += 1
        for cita in citas:
            if not isinstance(cita, dict) or not codigo_estado(cita):
                raise ReservoError("Reservo devolvió una cita sin estado verificable.")
            if codigo_estado(cita) != atendido:
                continue
            inicio = fecha_atencion_utc(cita)
            if inicio is None:
                raise ReservoError("Reservo devolvió una atención sin fecha verificable.")
            dia = inicio.replace(tzinfo=timezone.utc).astimezone(ZONA).date()
            if not desde <= dia <= hasta:
                raise ReservoError("Reservo devolvió una atención fuera del tramo solicitado.")
            if inicio > instante:
                continue
            paciente = cita.get("cliente")
            uuid = paciente.get("uuid") if isinstance(paciente, dict) else None
            if not isinstance(uuid, str) or not uuid.strip() or len(uuid) > 80:
                sin_uuid += 1
                continue
            uuid = normalizar_cliente_uuid(uuid).encode("utf-8")
            tratamientos = tratamientos_cita(cita)
            nombres = {tratamiento["nombre"] for tratamiento in tratamientos}
            coincide = not seleccionados or any(
                tratamiento["uuid"].replace("-", "") in seleccionados
                for tratamiento in tratamientos
            )
            anterior = ultimas.get(uuid)
            if anterior is None or inicio > anterior[1]:
                ultimas[uuid] = (dia, inicio, coincide, nombres)
            elif inicio == anterior[1]:
                ultimas[uuid] = (dia, inicio, coincide or anterior[2], nombres | anterior[3])
            valido = cliente_valido(cita)
            if valido and (uuid not in contactos or inicio > contactos[uuid][1]):
                contactos[uuid] = (valido, inicio)
    if not paginas:
        raise ReservoError("No se recibió ninguna página del tramo.")
    return {
        uuid: (dia, inicio, coincide, ", ".join(sorted(nombres)) or None)
        for uuid, (dia, inicio, coincide, nombres) in ultimas.items()
    }, paginas, sin_uuid, contactos


def _procesar(conn, cur, job):
    identificador = job["id"]
    seleccionados = set(json.loads(job["tratamientos_json"] or "[]"))
    origen = CLINICAS[job["clinica_codigo"]]
    cfg = config_all(refresh=True)
    token = cfg.get(clave_token(origen), "").strip()
    atendido = cfg.get(clave_estado_atendido(origen), "").strip()
    if (not token or not atendido or cfg.get(clave_estado_verificado(origen)) != "true"
            or cfg.get(clave_cobertura(origen)) != "true"):
        raise ReservoError("Confirma el estado Atendido y la cobertura histórica de esta clínica en Configuración.")
    huella = hashlib.sha256(f"{token}\0{atendido}".encode()).hexdigest()
    inicio = date(job["anio"], job["mes"], 1)
    fin_cohorte = inicio.replace(day=monthrange(inicio.year, inicio.month)[1])
    cur.execute("SELECT siguiente_fecha,huella_fuente FROM recuperacion_analisis WHERE solicitud=%s",
                (identificador,))
    avance = cur.fetchone()
    if avance and avance["huella_fuente"] != huella:
        raise ReservoError("La fuente de Reservo cambió durante el análisis. Crea una solicitud nueva.")
    desde = avance["siguiente_fecha"] if avance else inicio
    if avance is None:
        cur.execute("INSERT INTO recuperacion_analisis (solicitud,siguiente_fecha,huella_fuente)"
                    " VALUES (%s,%s,%s)", (identificador, inicio, huella))
    conn.commit()
    cliente = ClienteReservo(token)
    try:
        for ultimo in (fin_cohorte, job["fecha_referencia"]):
            if desde > ultimo:
                continue
            for a, b in dividir_periodo(desde, ultimo, dias_por_tramo=7):
                filas, paginas, sin_uuid, contactos = _leer_tramo(
                    cliente, a, b, atendido, job["creado_en"], seleccionados,
                )
                if b > fin_cohorte and sin_uuid:
                    raise ReservoError("El historial posterior contiene atenciones sin identidad verificable.")
                if filas and b <= fin_cohorte:
                    cur.executemany(
                        "INSERT INTO recuperacion_cohorte (solicitud,cliente_uuid,ultima_atencion,"
                        " ultima_atencion_utc,coincide_tratamiento,tratamiento_ultima_atencion)"
                        " VALUES (%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE"
                        " ultima_atencion=VALUES(ultima_atencion),"
                        " ultima_atencion_utc=VALUES(ultima_atencion_utc),"
                        " coincide_tratamiento=VALUES(coincide_tratamiento),"
                        " tratamiento_ultima_atencion=VALUES(tratamiento_ultima_atencion)",
                        [(identificador, uuid, *ultima) for uuid, ultima in filas.items()],
                    )
                elif filas:
                    cur.executemany(
                        "UPDATE recuperacion_cohorte SET ultima_atencion=%s, ultima_atencion_utc=%s,"
                        " coincide_tratamiento=%s, tratamiento_ultima_atencion=%s"
                        " WHERE solicitud=%s AND cliente_uuid=%s",
                        [(*ultima, identificador, uuid) for uuid, ultima in filas.items()],
                    )
                if contactos:
                    cur.execute("SELECT cliente_uuid FROM recuperacion_cohorte WHERE solicitud=%s",
                                (identificador,))
                    incluidos = {fila["cliente_uuid"] for fila in cur.fetchall()}
                    cerrojos = []
                    for uuid in sorted(contactos.keys() & incluidos):
                        paciente, fecha = contactos[uuid]
                        cur.execute("SELECT fecha_ultima_atencion FROM reservo_pacientes"
                                    " WHERE clinica_codigo=%s AND cliente_uuid=%s",
                                    (origen, uuid.decode("utf-8")))
                        previa = cur.fetchone()
                        if previa and previa["fecha_ultima_atencion"] and previa["fecha_ultima_atencion"] > fecha:
                            continue
                        _guardar_paciente_cursor(cur, origen, paciente, [], fecha, cerrojos)
                    limpiar_cerrojos_baja_inactivos(cur, cerrojos)
                desde = b + timedelta(days=1)
                cur.execute(
                    "UPDATE recuperacion_analisis SET siguiente_fecha=%s,tramos=tramos+1,"
                    " paginas=paginas+%s,citas_sin_uuid=citas_sin_uuid+%s,"
                    " intentos=0,reintentar_en=NULL WHERE solicitud=%s",
                    (desde, paginas, sin_uuid, identificador),
                )
                conn.commit()
    finally:
        cliente.close()
    limite = fecha_umbral(job["fecha_referencia"], job["meses_inactividad"])
    cur.execute("SELECT COUNT(*) AS total,"
                " COALESCE(SUM(ultima_atencion<=%s AND coincide_tratamiento=1),0) AS inactivos"
                " FROM recuperacion_cohorte WHERE solicitud=%s", (limite, identificador))
    conteos = cur.fetchone()
    cur.execute("UPDATE recuperacion_analisis SET pacientes_analizados=%s,pacientes_inactivos=%s,"
                " finalizado_en=UTC_TIMESTAMP(),error=NULL WHERE solicitud=%s",
                (conteos["total"], conteos["inactivos"], identificador))
    cur.execute("UPDATE recuperacion_solicitudes SET estado='ANALIZADA',version=version+1 WHERE id=%s",
                (identificador,))
    conn.commit()


def procesar_siguiente():
    """Un solo análisis a la vez. Si cae el proceso, MySQL libera el cerrojo."""
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("SELECT GET_LOCK('snw-recuperacion-worker',0) AS adquirido")
        if not cur.fetchone()["adquirido"]:
            return False
        try:
            cur.execute(
                "SELECT s.* FROM recuperacion_solicitudes s LEFT JOIN recuperacion_analisis a ON a.solicitud=s.id"
                " WHERE s.estado IN ('PENDIENTE','PROCESANDO')"
                " AND (a.reintentar_en IS NULL OR a.reintentar_en<=UTC_TIMESTAMP()) ORDER BY s.id LIMIT 1"
            )
            job = cur.fetchone()
            if not job:
                return False
            cur.execute("UPDATE recuperacion_solicitudes SET estado='PROCESANDO',version=version+1 WHERE id=%s",
                        (job["id"],))
            conn.commit()
            try:
                _procesar(conn, cur, job)
            except ReservoError as exc:
                conn.rollback()
                cur.execute("SELECT intentos FROM recuperacion_analisis WHERE solicitud=%s", (job["id"],))
                avance = cur.fetchone()
                status = exc.status_code
                transitorio = (status == 429 or (status is not None and status >= 500)
                               or isinstance(exc.__cause__, httpx.RequestError))
                if avance and avance["intentos"] < 2 and transitorio:
                    cur.execute("UPDATE recuperacion_analisis SET intentos=intentos+1,"
                                " reintentar_en=UTC_TIMESTAMP()+INTERVAL 1 MINUTE WHERE solicitud=%s",
                                (job["id"],))
                    estado = "PENDIENTE"
                else:
                    cur.execute(
                        "INSERT INTO recuperacion_analisis (solicitud,siguiente_fecha,huella_fuente,error,finalizado_en)"
                        " VALUES (%s,%s,'',%s,UTC_TIMESTAMP()) ON DUPLICATE KEY UPDATE"
                        " error=VALUES(error),finalizado_en=VALUES(finalizado_en)",
                        (job["id"], job["fecha_referencia"], exc.categoria[:255]),
                    )
                    cur.execute("DELETE FROM recuperacion_cohorte WHERE solicitud=%s", (job["id"],))
                    estado = "ERROR"
                cur.execute("UPDATE recuperacion_solicitudes SET estado=%s,version=version+1 WHERE id=%s",
                            (estado, job["id"]))
                conn.commit()
            return True
        finally:
            cur.execute("SELECT RELEASE_LOCK('snw-recuperacion-worker')")


async def loop_trabajos():
    while True:
        try:
            if not await asyncio.to_thread(procesar_siguiente):
                await asyncio.sleep(5)
        except Exception as exc:
            log_error("worker de recuperación de pacientes", exc, solo_tipo=True)
            await asyncio.sleep(10)
