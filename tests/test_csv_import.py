import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import servicio_areas


class CsvImportTests(unittest.TestCase):
    def test_importa_csv_con_separadores_finales(self):
        conexion = MagicMock()
        cur = conexion.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value = []
        cur.fetchone.return_value = None
        cur.lastrowid = 7
        csv = (
            b"nombre,apellido,telefono;;;;;,respuesta;;;;;\n"
            b"David, Araya, 93921740;;;;;,Respondio;;;;;\n"
            b";;;;;\n"
            b";;;;;\n"
        )

        with patch("servicio_areas.obtener_area",
                   return_value={"nombre_tabla_base": "pacientes_test"}), \
                patch("servicio_areas.tabla_valida", return_value=True), \
                patch("servicio_areas.conectar", return_value=conexion), \
                patch("servicio_areas.indice_pacientes_por_telefono", return_value={}), \
                patch("supresion.huella_telefono", return_value="huella-test"), \
                patch("servicio_areas.aplicar_en_tabla") as aplicar, \
                patch("servicio_areas.propagar_a_todas_las_bases",
                      return_value={"bases": [], "omitidos": 0}):
            informe = servicio_areas.importar_pacientes_csv(1, csv, 2)

        self.assertEqual(informe["procesados"], 1)
        self.assertEqual(informe["insertados"], 1)
        self.assertEqual(informe["rechazados"], 0)
        insercion = next(c for c in cur.execute.call_args_list
                         if c.args[0].startswith("INSERT INTO pacientes_test"))
        self.assertEqual(insercion.args[1], ("David", "Araya", "+56993921740"))
        self.assertEqual(aplicar.call_args.args[3:5], ("respondio", None))


if __name__ == "__main__":
    unittest.main()
