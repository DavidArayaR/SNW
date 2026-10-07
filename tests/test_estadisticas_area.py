import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import main


class CursorFalso:
    def __init__(self):
        self.consultas = []
        self.respuestas = iter(({}, {"n": 0}, {"n": 0}, {"n": 0, "ult": None}))

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def cursor(self):
        return self

    def execute(self, sql, parametros=None):
        # PyMySQL interpola con % cuando recibe parámetros: esto reproduce
        # el fallo que causaba DATE_FORMAT(..., '%Y-%m-01') al elegir área.
        if parametros:
            sql % tuple(repr(valor) for valor in parametros)
        self.consultas.append((sql, parametros))

    def fetchone(self):
        return next(self.respuestas)


class EstadisticasAreaTests(unittest.TestCase):
    def test_resumen_con_area_no_interpreta_formato_fecha_como_parametro(self):
        cursor = CursorFalso()
        area = {"id": 7, "nombre_visible": "Área 7", "nombre_tabla_base": "pacientes_7"}
        with patch.object(main, "conectar", return_value=cursor), \
             patch.object(main, "_filtro_esp_estadisticas", return_value=(
                 " AND envio_id IN (SELECT id FROM envios WHERE area_id = %s)", (7,), area)), \
             patch.object(main, "_pacientes_por_respuesta", return_value={"total": 0}), \
             patch.object(main, "_pacientes_por_interes", return_value={"total": 0}):
            resultado = main.estadisticas(area_id=7, sesion={})

        self.assertEqual(cursor.consultas[0][1], ("%Y-%m-01", 7))
        self.assertEqual(resultado["area"]["id"], 7)


if __name__ == "__main__":
    unittest.main()
