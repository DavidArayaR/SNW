import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import servicio_areas


class CursorFalso:
    def __init__(self, filas_insertadas):
        self.filas_insertadas = filas_insertadas
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, _params):
        if sql.startswith("INSERT IGNORE"):
            self.rowcount = self.filas_insertadas

    def fetchone(self):
        return {"id": 7, "nombre": "Área de prueba"}


class ConexionFalsa:
    def __init__(self, filas_insertadas):
        self.cur = CursorFalso(filas_insertadas)
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self.cur

    def commit(self):
        self.commits += 1


class AsignacionRolAreaTests(unittest.TestCase):
    def test_informa_si_se_creo_la_asignacion(self):
        for filas_insertadas, esperado in [(1, True), (0, False)]:
            with self.subTest(filas_insertadas=filas_insertadas):
                conexion = ConexionFalsa(filas_insertadas)
                with patch.object(servicio_areas, "conectar", return_value=conexion):
                    resultado = servicio_areas.asignar_rol_area(8, 42)
                self.assertEqual(resultado["asignado"], esperado)
                self.assertEqual(resultado["area_id"], 8)
                self.assertEqual(conexion.commits, 1)


if __name__ == "__main__":
    unittest.main()
