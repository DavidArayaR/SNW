import datetime
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import main


class HistorialOrigenTests(unittest.TestCase):
    def _consultar(self, origen, lotes, programados=()):
        conexion = MagicMock()
        cur = conexion.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.fetchall.side_effect = [lotes] + ([list(programados)] if origen != "manual" else [])
        with patch.object(main, "conectar", return_value=conexion), \
                patch.object(main, "columnas_tabla", return_value={"programado_id", "area_id", "tabla_pacientes"}), \
                patch.object(main, "_es_privilegiado", return_value=True), \
                patch.object(main, "_prog_visible", return_value=True), \
                patch.object(main, "_prog_a_respuesta", return_value={"puede_cancelar": False}):
            resultado = main.listar_historial(
                q=None, estado=None, ambiente="todos", area_id=None, tabla=None,
                origen=origen, sesion={"rol": "administrador"})
        return resultado, [c.args[0] for c in cur.execute.call_args_list]

    def test_manuales_no_incluye_programados(self):
        fecha = datetime.datetime(2026, 10, 8, 10)
        filas, consultas = self._consultar("manual", [{"id": 1, "fecha_hora": fecha,
            "programado_id": None, "programado_creado": None, "base_datos": "pacientes_prod"}])
        self.assertEqual([f["origen"] for f in filas], ["manual"])
        self.assertIn("programado_id IS NULL", consultas[0])
        self.assertEqual(len(consultas), 1)

    def test_programados_ejecutados_y_pendientes_sin_duplicados(self):
        fecha = datetime.datetime(2026, 10, 8, 10)
        creado = datetime.datetime(2026, 10, 7, 9)
        lote = {"id": 2, "fecha_hora": fecha, "programado_id": 5,
                "programado_creado": creado, "base_datos": "pacientes_prod"}
        pendiente = {"id": 6, "creado": fecha, "programado_para": fecha,
                     "tabla": "pacientes_prod", "ambiente": "produccion",
                     "estado": "pendiente", "area_id": None}
        filas, consultas = self._consultar("programado", [lote], [pendiente])
        self.assertEqual([f["id"] for f in filas], ["prog-6", 2])
        self.assertEqual([f["origen"] for f in filas], ["programado", "programado"])
        self.assertEqual(filas[1]["fecha"], "07-10-2026 09:00")
        self.assertIn("programado_id IS NOT NULL", consultas[0])
        self.assertIn("NOT EXISTS", consultas[1])

    def test_valor_desconocido_rechazado(self):
        with self.assertRaises(HTTPException) as error:
            main.listar_historial(origen="otro", sesion={})
        self.assertEqual(error.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
