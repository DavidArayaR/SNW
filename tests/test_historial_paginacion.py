import datetime
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import main


class HistorialPaginacionTests(unittest.TestCase):
    def test_ruta_nueva_y_filtros_de_origen(self):
        self.assertIn("get", main.app.openapi()["paths"]["/api/notificaciones/historial/pagina"])
        sesion = {"rol": "administrador", "area_ids": []}
        with patch.object(main, "_es_privilegiado", return_value=True):
            sql_manual, _ = main._fuentes_historial_pagina(
                sesion, "todos", None, None, "manual", "", {"programado_id", "area_id"})
            sql_prog, _ = main._fuentes_historial_pagina(
                sesion, "todos", None, None, "programado", "", {"programado_id", "area_id"})
        self.assertIn("e.programado_id IS NULL", sql_manual)
        self.assertNotIn("FROM envios_programados p", sql_manual)
        self.assertIn("e.programado_id IS NOT NULL", sql_prog)
        self.assertIn("NOT EXISTS", sql_prog)

    def test_solo_consulta_detalles_de_la_pagina_y_no_repite_total(self):
        conexion = MagicMock()
        cur = conexion.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = {"total": 23}
        ahora = datetime.datetime(2026, 10, 16, 12)
        lote = {"id": 11, "fecha_hora": ahora, "programado_creado": None,
                "programado_id": None, "base_datos": "pacientes_prod"}
        cur.fetchall.side_effect = [
            [{"tipo": 0, "id": 11}], [lote.copy()],
            [{"tipo": 0, "id": 8}], [{**lote, "id": 8}],
        ]
        with patch.object(main, "conectar", return_value=conexion), \
                patch.object(main, "columnas_tabla", return_value={"programado_id", "area_id"}), \
                patch.object(main, "_es_privilegiado", return_value=True), \
                patch.object(main, "_presentar_historial", side_effect=lambda filas, *_args, **_kw: filas):
            primero = main.listar_historial_pagina(
                q="", ambiente="todos", area_id=None, tabla=None, origen="manual",
                pagina=1, limite=10, incluir_total=True, sesion={"rol": "administrador"})
            segundo = main.listar_historial_pagina(
                q="", ambiente="todos", area_id=None, tabla=None, origen="manual",
                pagina=2, limite=10, incluir_total=False, sesion={"rol": "administrador"})
        self.assertEqual(primero["total"], 23)
        self.assertEqual(primero["registros"][0]["id"], 11)
        self.assertIsNone(segundo["total"])
        self.assertEqual(segundo["registros"][0]["id"], 8)
        consultas = [c.args[0] for c in cur.execute.call_args_list]
        self.assertEqual(sum("COUNT(*)" in sql for sql in consultas), 1)
        self.assertEqual(sum("LIMIT %s OFFSET %s" in sql for sql in consultas), 2)
        self.assertEqual(cur.execute.call_args.args[1], (8,))

    def test_cuenta_normal_no_ve_bases_legacy_ni_areas_ajenas(self):
        sesion = {"rol": "usuario", "usuario": "operador", "area_ids": [7]}
        with patch.object(main, "_es_privilegiado", return_value=False):
            sql, args = main._fuentes_historial_pagina(
                sesion, "todos", None, None, "todos", "", {"programado_id", "area_id"})
            with self.assertRaises(main.HTTPException) as error:
                main._fuentes_historial_pagina(
                    sesion, "todos", None, "pacientes_dev", "todos", "",
                    {"programado_id", "area_id"})
        self.assertEqual(error.exception.status_code, 403)
        self.assertIn("e.area_id IN (%s)", sql)
        self.assertIn("p.area_id IN (%s)", sql)
        self.assertIn("NOT (p.ambiente = 'desarrollo'", sql)
        self.assertIn(7, args)

    def test_mezcla_envio_ejecutado_y_programacion_pendiente(self):
        conexion = MagicMock()
        cur = conexion.__enter__.return_value.cursor.return_value.__enter__.return_value
        fecha = datetime.datetime(2026, 10, 16, 12)
        cur.fetchall.side_effect = [
            [{"tipo": 1, "id": 9}, {"tipo": 0, "id": 11}],
            [{"id": 11, "fecha_hora": fecha, "programado_creado": fecha,
              "programado_id": 7}],
            [{"id": 9, "creado": fecha, "programado_para": fecha,
              "ambiente": "produccion", "area_id": 3}],
        ]
        with patch.object(main, "conectar", return_value=conexion), \
                patch.object(main, "columnas_tabla", return_value={"programado_id", "area_id"}), \
                patch.object(main, "_es_privilegiado", return_value=True), \
                patch.object(main, "_prog_visible", return_value=True), \
                patch.object(main, "_presentar_historial", side_effect=lambda filas, *_args, **_kw: filas):
            resultado = main.listar_historial_pagina(
                q="", ambiente="todos", area_id=None, tabla=None, origen="programado",
                pagina=1, limite=10, incluir_total=False, sesion={"rol": "administrador"})
        self.assertEqual(resultado["total"], None)
        self.assertEqual([r["id"] for r in resultado["registros"]], [9, 11])
        self.assertTrue(resultado["registros"][0]["_sin_lote"])
        self.assertEqual([r["origen"] for r in resultado["registros"]],
                         ["programado", "programado"])
        consultas = [c.args[0] for c in cur.execute.call_args_list]
        self.assertNotIn("COUNT(*)", " ".join(consultas))
        self.assertIn("NOT EXISTS", consultas[0])


if __name__ == "__main__":
    unittest.main()
