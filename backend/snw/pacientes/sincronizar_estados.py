"""Propaga estados de envío entre las bases elegidas."""

import asyncio
import datetime as dt
import json
import re
from contextlib import contextmanager
from zoneinfo import ZoneInfo

from db import accion_de_error, conectar, log_error
from snw.mensajeria.reservas_programadas import telefono_sql
from snw.pacientes.servicio_areas import tabla_valida

_CHILE = ZoneInfo("America/Santiago")
_HORA_DIARIA = dt.time(23, 59)
_CLINICAS = {"SMPT": "SMPT", "MS": "MS", "MVYS": "MVYS", "VYS": "MVYS", "VS": "MVYS"}


@contextmanager
def bloquear(cur):
    """Evita ejecutar sincronizaciones manuales y automáticas superpuestas."""
    clave = "snw-sincronizar-estados-pacientes"
    cur.execute("SELECT GET_LOCK(%s, 0) AS tomado", (clave,))
    tomado = bool(cur.fetchone()["tomado"])
    try:
        yield tomado
    finally:
        if tomado:
            cur.execute("SELECT RELEASE_LOCK(%s)", (clave,))


def propagar_enviados(cur, bases: list[tuple[str, int | None]]) -> dict[tuple[str, int | None], dict]:
    """Toma los enviados de todas las bases antes de modificar cualquiera."""
    cur.execute(
        "CREATE TEMPORARY TABLE tmp_sincronizar_enviados ("
        " telefono CHAR(12) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci PRIMARY KEY"
        ") ENGINE=InnoDB"
    )
    telefono = telefono_sql("p.telefono")
    try:
        for tabla, lista_id in bases:
            if not tabla_valida(tabla) and (tabla != "reservo_lista_pacientes" or lista_id is None):
                raise ValueError("Tabla de pacientes inválida.")
            filtro = " AND p.lista_id=%s" if lista_id is not None else ""
            args = (lista_id,) if lista_id is not None else ()
            cur.execute(
                "INSERT IGNORE INTO tmp_sincronizar_enviados (telefono)"
                f" SELECT {telefono} FROM `{tabla}` p"
                f" WHERE p.estado='enviado'{filtro} AND ({telefono}) REGEXP '^[+]569[0-9]{{8}}$'",
                args,
            )

        actualizados = {}
        for tabla, lista_id in bases:
            filtro = " AND p.lista_id=%s" if lista_id is not None else ""
            args = (lista_id,) if lista_id is not None else ()
            cur.execute(
                f"UPDATE `{tabla}` p JOIN tmp_sincronizar_enviados e"
                f" ON e.telefono=CONVERT(({telefono}) USING utf8mb4) COLLATE utf8mb4_unicode_ci"
                f" SET p.estado='enviado' WHERE COALESCE(NULLIF(p.estado,''),'pendiente')='pendiente'{filtro}",
                args,
            )
            cantidad = cur.rowcount
            cur.execute(
                "SELECT COALESCE(SUM(COALESCE(NULLIF(p.estado,''),'pendiente')='pendiente'),0) AS pendientes,"
                " COALESCE(SUM(p.estado='enviado'),0) AS enviados"
                f" FROM `{tabla}` p WHERE 1=1{filtro}",
                args,
            )
            despues = {estado: int(total) for estado, total in cur.fetchone().items()}
            # Solo se promueven pendientes a enviados. Reconstruir el antes
            # con las filas modificadas evita una segunda lectura de la base.
            actualizados[(tabla, lista_id)] = {
                "actualizados": cantidad,
                "antes": {"pendientes": despues["pendientes"] + cantidad,
                          "enviados": despues["enviados"] - cantidad},
                "despues": despues,
            }
        return actualizados
    finally:
        cur.execute("DROP TEMPORARY TABLE tmp_sincronizar_enviados")


def registrar_historial(cur, bases: list[dict], fecha_automatica: dt.date | None = None) -> None:
    cur.execute(
        "INSERT INTO pacientes_sincronizaciones (creado, bases) VALUES (UTC_TIMESTAMP(), %s)",
        (json.dumps(bases, ensure_ascii=False),),
    )
    if fecha_automatica is not None:
        cur.execute(
            "INSERT INTO pacientes_sincronizaciones_diarias (fecha, sincronizacion_id) VALUES (%s, %s)",
            (fecha_automatica, cur.lastrowid),
        )


def listar_historial(cur, pagina: int, limite: int) -> dict:
    cur.execute(
        "SELECT h.id, h.creado, h.bases, d.fecha AS fecha_automatica FROM pacientes_sincronizaciones h"
        " LEFT JOIN pacientes_sincronizaciones_diarias d ON d.sincronizacion_id=h.id"
        " ORDER BY h.id DESC LIMIT %s OFFSET %s",
        (limite + 1, (pagina - 1) * limite),
    )
    filas = cur.fetchall()
    items = []
    for fila in filas[:limite]:
        bases = json.loads(fila["bases"])
        items.append({"id": fila["id"], "fecha": fila["creado"].isoformat() + "Z",
                      "fecha_automatica": fila["fecha_automatica"],
                      "bases": bases, "actualizados": sum(base["actualizados"] for base in bases)})
    return {"items": items, "hay_siguiente": len(filas) > limite}


def _clinica_base(codigo: str | None, nombre: str, tabla: str) -> str | None:
    if codigo and codigo.strip():
        return _CLINICAS.get(codigo.strip().upper())
    # Los nombres y tablas físicas pueden tener el prefijo pacientes_.
    codigos = set()
    for valor in (nombre, tabla):
        prefijo = re.match(r"^(?:pacientes[ _-]+)?(SMPT|MVYS|VYS|VS|MS)(?=$|[ _-])",
                           valor.strip(), re.IGNORECASE)
        if prefijo:
            codigos.add(_CLINICAS[prefijo[1].upper()])
    return codigos.pop() if len(codigos) == 1 else None


def _bases_por_clinica(cur) -> dict:
    cur.execute(
        "SELECT TABLE_NAME AS tabla FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE()"
        " AND COLUMN_NAME IN ('telefono', 'estado') GROUP BY TABLE_NAME HAVING COUNT(*)=2"
    )
    disponibles = {fila["tabla"] for fila in cur.fetchall()}
    cur.execute("SELECT id, nombre_visible, nombre_tabla_base FROM areas ORDER BY id")
    areas = cur.fetchall()
    cur.execute(
        "SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE()"
        " AND TABLE_NAME='reservo_bases_contacto' AND COLUMN_NAME='area_id'"
    )
    listas = ()
    if cur.fetchone():
        cur.execute("SELECT id, area_id, nombre, clinica_codigo FROM reservo_bases_contacto ORDER BY id")
        listas = cur.fetchall()
    por_area = {fila["area_id"]: fila["clinica_codigo"] for fila in listas if fila["area_id"] is not None}
    grupos = {}
    for area in areas:
        tabla = area["nombre_tabla_base"]
        if tabla not in disponibles or not tabla_valida(tabla) or tabla == "pacientes_dev":
            continue
        nombre = area["nombre_visible"] or tabla
        clinica = _clinica_base(por_area.get(area["id"]), nombre, tabla)
        if clinica:
            grupos.setdefault(clinica, {})[(tabla, None)] = nombre
    if "reservo_lista_pacientes" in disponibles:
        for lista in listas:
            clinica = _clinica_base(lista["clinica_codigo"], lista["nombre"], "")
            if lista["area_id"] is None and clinica:
                grupos.setdefault(clinica, {})[("reservo_lista_pacientes", lista["id"])] = lista["nombre"]
    return grupos


def _ejecutar_diaria(fecha: dt.date, permitir_primera: bool) -> bool:
    """Completa la última fecha vencida una sola vez, incluso entre procesos."""
    with conectar() as conn, conn.cursor() as cur, bloquear(cur) as tomado:
        if not tomado:
            return False
        cur.execute("SELECT MAX(fecha) AS ultima FROM pacientes_sincronizaciones_diarias")
        ultima = cur.fetchone()["ultima"]
        if (ultima and ultima >= fecha) or (ultima is None and not permitir_primera):
            return True
        detalle = []
        for clinica, bases in sorted(_bases_por_clinica(cur).items()):
            if len(bases) < 2:
                continue
            cambios = propagar_enviados(cur, sorted(bases))
            detalle.extend({"nombre": bases[base], "clinica": clinica, **resultado}
                           for base, resultado in cambios.items())
        registrar_historial(cur, detalle, fecha)
        conn.commit()
    return True


def _fecha_diaria_vencida(ahora: dt.datetime) -> dt.date:
    local = ahora.astimezone(_CHILE)
    return local.date() if local.time() >= _HORA_DIARIA else local.date() - dt.timedelta(days=1)


async def loop_diario() -> None:
    revisada = None
    permitir_primera = False
    while True:
        ahora = dt.datetime.now(_CHILE)
        fecha = _fecha_diaria_vencida(ahora)
        permitir_primera = permitir_primera or fecha == ahora.date()
        if fecha != revisada:
            try:
                with accion_de_error("sincronización diaria de pacientes"):
                    if await asyncio.to_thread(_ejecutar_diaria, fecha, permitir_primera):
                        revisada = fecha
            except Exception as exc:
                log_error("sincronización diaria de pacientes", exc, solo_tipo=True)
                await asyncio.sleep(60)
                continue
        await asyncio.sleep(15)
