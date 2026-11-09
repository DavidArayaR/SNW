"""Solicitudes persistentes y análisis de última atención efectiva en Reservo."""

import asyncio
import hashlib
from calendar import monthrange
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
                               fecha_atencion_utc, normalizar_cliente_uuid)


RUTA = "/api/v1/recuperacion-pacientes"
ZONA = ZoneInfo("America/Santiago")
CLINICAS = {("VS" if codigo == "MVYS" else codigo): codigo for codigo in RESERVO_CLINICAS}
_CAMPOS = (
    "id, solicitud_id, clinica_codigo, anio, mes, meses_inactividad, "
    "fecha_referencia, estado, version, creado_en"
)


class SolicitudRecuperacionIn(BaseModel):
    clinica_codigo: str
    anio: int = Field(ge=1000, le=9999)
    mes: int = Field(ge=1, le=12)


def configurar_tablas(cur):
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


def _publica(fila):
    publica = {k: v for k, v in fila.items() if k != "id"}
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
    parametros = (codigo, body.anio, body.mes)
    cfg = config_all()
    with conectar() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {_CAMPOS} FROM recuperacion_solicitudes"
                    " WHERE usuario=%s AND clave_idempotencia=%s", (usuario, clave))
        fila = cur.fetchone()
        nueva = False
        if fila is None:
            ahora = datetime.now(ZoneInfo("America/Santiago"))
            if (body.anio, body.mes) >= (ahora.year, ahora.month):
                raise HTTPException(422, detail="Selecciona un mes histórico completo.")
            valor = cfg.get(clave_umbral(CLINICAS[codigo]), "")
            if not umbral_valido(valor):
                raise HTTPException(503, detail="Configura el umbral de recuperación de esta clínica.")
            solicitud_id = uuid4().hex
            cur.execute(
                "INSERT INTO recuperacion_solicitudes"
                " (solicitud_id,usuario,clave_idempotencia,clinica_codigo,anio,mes,"
                " meses_inactividad,fecha_referencia,creado_en)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                " ON DUPLICATE KEY UPDATE id=id",
                (solicitud_id, usuario, clave, *parametros, int(valor), ahora.date(),
                 ahora.astimezone(timezone.utc).replace(tzinfo=None)),
            )
            fila = _buscar_clave(cur, usuario, clave)
            nueva = fila["solicitud_id"] == solicitud_id
        if (fila["clinica_codigo"], fila["anio"], fila["mes"]) != parametros:
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
            " a.pacientes_analizados,a.pacientes_inactivos,a.citas_sin_uuid"
            " FROM recuperacion_solicitudes s LEFT JOIN recuperacion_analisis a ON a.solicitud=s.id"
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
        "lista_generada_id": None,
        "error": {"codigo": "ANALISIS_RESERVO", "detalle": fila["error"]} if fila["error"] else None,
    }


def fecha_umbral(referencia: date, meses: int) -> date:
    anio, indice_mes = divmod(referencia.year * 12 + referencia.month - 1 - meses, 12)
    mes = indice_mes + 1
    return date(anio, mes, min(referencia.day, monthrange(anio, mes)[1]))


def _leer_tramo(cliente, desde, hasta, atendido, instante):
    """Consolida un tramo completo antes de confirmar su checkpoint."""
    ultimas = {}
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
            ultimas[uuid] = max(dia, ultimas.get(uuid, dia))
    if not paginas:
        raise ReservoError("No se recibió ninguna página del tramo.")
    return ultimas, paginas, sin_uuid


def _procesar(conn, cur, job):
    identificador = job["id"]
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
                filas, paginas, sin_uuid = _leer_tramo(cliente, a, b, atendido, job["creado_en"])
                if b > fin_cohorte and sin_uuid:
                    raise ReservoError("El historial posterior contiene atenciones sin identidad verificable.")
                if filas and b <= fin_cohorte:
                    cur.executemany(
                        "INSERT INTO recuperacion_cohorte (solicitud,cliente_uuid,ultima_atencion)"
                        " VALUES (%s,%s,%s) ON DUPLICATE KEY UPDATE"
                        " ultima_atencion=GREATEST(ultima_atencion,VALUES(ultima_atencion))",
                        [(identificador, uuid, dia) for uuid, dia in filas.items()],
                    )
                elif filas:
                    cur.executemany(
                        "UPDATE recuperacion_cohorte SET ultima_atencion=GREATEST(ultima_atencion,%s)"
                        " WHERE solicitud=%s AND cliente_uuid=%s",
                        [(dia, identificador, uuid) for uuid, dia in filas.items()],
                    )
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
    cur.execute("SELECT COUNT(*) AS total, COALESCE(SUM(ultima_atencion<=%s),0) AS inactivos"
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
