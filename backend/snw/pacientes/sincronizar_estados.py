"""Propaga estados de envío entre las bases elegidas."""

import json

from snw.mensajeria.reservas_programadas import telefono_sql
from snw.pacientes.servicio_areas import tabla_valida


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


def registrar_historial(cur, bases: list[dict]) -> None:
    cur.execute(
        "INSERT INTO pacientes_sincronizaciones (creado, bases) VALUES (UTC_TIMESTAMP(), %s)",
        (json.dumps(bases, ensure_ascii=False),),
    )


def listar_historial(cur, pagina: int, limite: int) -> dict:
    cur.execute(
        "SELECT id, creado, bases FROM pacientes_sincronizaciones ORDER BY id DESC LIMIT %s OFFSET %s",
        (limite + 1, (pagina - 1) * limite),
    )
    filas = cur.fetchall()
    items = []
    for fila in filas[:limite]:
        bases = json.loads(fila["bases"])
        items.append({"id": fila["id"], "fecha": fila["creado"].isoformat() + "Z",
                      "bases": bases, "actualizados": sum(base["actualizados"] for base in bases)})
    return {"items": items, "hay_siguiente": len(filas) > limite}
