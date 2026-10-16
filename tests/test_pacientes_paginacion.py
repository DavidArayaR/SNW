import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import main


class PacientesPaginacionTests(unittest.TestCase):
    def test_ruta_paginada_esta_registrada(self):
        self.assertIn("get", main.app.openapi()["paths"]["/api/pacientes/pagina"])

    def test_lee_solo_la_pagina_y_no_repite_el_conteo_al_navegar(self):
        conexion = MagicMock()
        cur = conexion.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = {"total": 3}
        cur.fetchall.side_effect = [
            [{"id": 1}, {"id": 2}], [{"id": 2}, {"id": 1}],
            [{"id": 3}], [{"id": 3}],
        ]

        with patch.object(main, "_tabla_lectura_pacientes", return_value=("pacientes_test", None)), \
                patch.object(main, "conectar", return_value=conexion), \
                patch.object(main, "expr_select_pacientes", return_value="p.id"), \
                patch.object(main, "from_pacientes", return_value=" FROM pacientes_test p"), \
                patch.object(main, "_filtro_pagina_pacientes", return_value=("", ())), \
                patch.object(main, "_numeros_prueba_editables", return_value=set()), \
                patch.object(main, "_presentar_filas_pacientes", side_effect=lambda filas, *_: filas):
            primera = main.listar_pacientes_pagina(
                ambiente="produccion", area_id=None, pagina=1, limite=2,
                q="", estado="todos", respuesta="todas", interes="todos",
                incluir_total=True, incluir_resumen=False, sesion={"rol": "administrador"})
            segunda = main.listar_pacientes_pagina(
                ambiente="produccion", area_id=None, pagina=2, limite=2,
                q="", estado="todos", respuesta="todas", interes="todos",
                incluir_total=False, incluir_resumen=False, sesion={"rol": "administrador"})

        self.assertEqual(primera, {"pacientes": [{"id": 1}, {"id": 2}],
                                   "total": 3, "resumen": None})
        self.assertEqual(segunda["pacientes"], [{"id": 3}])
        self.assertIsNone(segunda["total"])
        consultas = [c.args[0] for c in cur.execute.call_args_list]
        self.assertEqual(sum("COUNT(*)" in sql for sql in consultas), 1)
        self.assertEqual(sum("LIMIT %s OFFSET %s" in sql for sql in consultas), 2)
        self.assertEqual(cur.execute.call_args_list[-2].args[1], (2, 2))
        self.assertEqual(cur.execute.call_args.args[1], (3,))

    def test_pagina_exige_el_mismo_permiso_que_la_lista(self):
        with patch.object(main, "tiene_permiso", return_value=False), \
                patch.object(main, "conectar") as conectar:
            with self.assertRaises(main.HTTPException) as error:
                main.listar_pacientes_pagina(
                    ambiente="produccion", area_id=None, pagina=1, limite=10,
                    q="", estado="todos", respuesta="todas", interes="todos",
                    incluir_total=True, incluir_resumen=False, sesion={"rol": "usuario"})
        self.assertEqual(error.exception.status_code, 403)
        conectar.assert_not_called()

    def test_resumen_reutiliza_total_y_numeros_prueba_van_primero(self):
        conexion = MagicMock()
        cur = conexion.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = {"total": 40, "pendiente": 20}
        cur.fetchall.side_effect = [[{"id": 7}], [{"id": 7}]]
        with patch.object(main, "_tabla_lectura_pacientes", return_value=("pacientes_test", None)), \
                patch.object(main, "conectar", return_value=conexion), \
                patch.object(main, "_filtro_pagina_pacientes", return_value=("", ())), \
                patch.object(main, "_expresiones_filtro_pacientes",
                             return_value=("p.estado", "p.respuesta_manual", "p.interesado", "p.no_interesado")), \
                patch.object(main, "_numeros_prueba_editables", return_value={"+56993921740"}), \
                patch.object(main, "expr_select_pacientes", return_value="p.id"), \
                patch.object(main, "from_pacientes", return_value=" FROM pacientes_test p"), \
                patch.object(main, "_presentar_filas_pacientes", side_effect=lambda filas, *_: filas):
            datos = main.listar_pacientes_pagina(
                ambiente="produccion", area_id=None, pagina=1, limite=10,
                q="", estado="todos", respuesta="todas", interes="todos",
                incluir_total=True, incluir_resumen=True, sesion={"rol": "administrador"})
        self.assertEqual(datos["total"], 40)
        self.assertEqual(datos["resumen"]["pendiente"], 20)
        self.assertEqual(cur.execute.call_count, 3)
        self.assertIn("CASE WHEN p.telefono IN (%s) THEN 0 ELSE 1 END",
                      cur.execute.call_args_list[-2].args[0])

    def test_busqueda_no_usa_datos_completos_de_pacientes_ajenos(self):
        with patch.object(main, "_expresiones_filtro_pacientes",
                          return_value=("p.estado", "p.respuesta_manual", "p.interesado", "p.no_interesado")), \
                patch.object(main.servicio_areas, "usuario_id_por_correo", return_value=4):
            sql, args = main._filtro_pagina_pacientes(
                "pacientes_test", "produccion", {"usuario": "operador@test", "rol": "usuario"},
                "93921740", "todos", "todas", "todos")
        self.assertIn("EXISTS (SELECT 1 FROM paciente_csv_accesos", sql)
        self.assertIn("NOT (EXISTS", sql)
        self.assertIn("LOWER(CONCAT(LEFT(p.nombre", sql)
        self.assertEqual(args, ("pacientes_test", 4, "93921740", "93921740",
                                "pacientes_test", 4, "93921740", "93921740"))

    def test_pagina_conserva_mascara_de_datos_ajenos(self):
        fila = {"id": 9, "nombre": "Ana", "apellido": "Sol",
                "telefono": "+56993921740", "fecha_actualizacion": None,
                "ultima_respuesta_fecha": None, "ultimo_mensaje_recibido": "Privado"}
        with patch.object(main, "_ids_csv_propios", return_value=set()), \
                patch.object(main, "_numeros_prueba_editables", return_value=set()):
            main._presentar_filas_pacientes([fila], "pacientes_test", "produccion", None,
                                           {"rol": "usuario"})
        self.assertFalse(fila["datos_completos"])
        self.assertEqual(fila["nombre"], "A. S.")
        self.assertEqual(fila["telefono"], "+569 **** *740")
        self.assertIsNone(fila["ultimo_mensaje_recibido"])


if __name__ == "__main__":
    unittest.main()
