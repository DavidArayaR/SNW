"""Propaga estados de envío entre las bases elegidas."""

from snw.mensajeria.reservas_programadas import telefono_sql
from snw.pacientes.servicio_areas import tabla_valida


def propagar_enviados(cur, bases: list[tuple[str, int | None]]) -> int:
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

        actualizados = 0
        for tabla, lista_id in bases:
            filtro = " AND p.lista_id=%s" if lista_id is not None else ""
            args = (lista_id,) if lista_id is not None else ()
            cur.execute(
                f"UPDATE `{tabla}` p JOIN tmp_sincronizar_enviados e"
                f" ON e.telefono=CONVERT(({telefono}) USING utf8mb4) COLLATE utf8mb4_unicode_ci"
                f" SET p.estado='enviado' WHERE COALESCE(NULLIF(p.estado,''),'pendiente')='pendiente'{filtro}",
                args,
            )
            actualizados += cur.rowcount
        return actualizados
    finally:
        cur.execute("DROP TEMPORARY TABLE tmp_sincronizar_enviados")
