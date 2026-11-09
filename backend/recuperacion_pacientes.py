"""Registro de solicitudes de recuperación. El análisis pertenece a la fase B."""

from datetime import datetime, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from pydantic import BaseModel, Field

from db import config_all, conectar
from reservo_catalogo import RESERVO_CLINICAS, clave_umbral_recuperacion as clave_umbral


RUTA = "/api/v1/recuperacion-pacientes"
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
    if not fila:
        raise HTTPException(404, detail="Solicitud no disponible.")
    return _publica(fila)
