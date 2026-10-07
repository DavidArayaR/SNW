import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import main
import whatsapp_service


class CursorFalso:
    def __init__(self, filas=None):
        self.consultas = []
        self.respuestas = iter(({}, {"n": 0}, {"n": 0}, {"n": 0}, {"n": 0, "ult": None}))
        self.filas = iter(filas or ())

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

    def fetchall(self):
        return next(self.filas)


class EstadisticasAreaTests(unittest.TestCase):
    def test_resumen_con_area_no_interpreta_formato_fecha_como_parametro(self):
        cursor = CursorFalso()
        area = {"id": 7, "nombre_visible": "Área 7", "nombre_tabla_base": "pacientes_7"}
        with patch.object(main, "conectar", return_value=cursor), \
             patch.object(main, "_exigir_area", return_value=area), \
             patch.object(main, "columnas_tabla", return_value={"interesado", "interes_fecha"}), \
             patch.object(main, "_pacientes_por_respuesta", return_value={"total": 0}), \
             patch.object(main, "_pacientes_por_interes", return_value={"total": 0}):
            resultado = main.estadisticas(area_id=7, sesion={})

        self.assertEqual(cursor.consultas[0][1], ("%Y-%m-01", 7, 7))
        self.assertIn("area_id = %s OR (area_id IS NULL", cursor.consultas[0][0])
        self.assertIn("plantilla_clave = 'respuesta'", cursor.consultas[2][0])
        self.assertIn("COLLATE utf8mb4_unicode_ci FROM pacientes_7", cursor.consultas[2][0])
        self.assertEqual(cursor.consultas[2][1], ("%Y-%m-01", 7, 7, "%Y-%m-01"))
        self.assertEqual(resultado["area"]["id"], 7)

    def test_calendario_solo_ofrece_dias_con_envios_del_area(self):
        cursor = CursorFalso(filas=(
            [{"dia": "2026-10-03"}, {"dia": "2026-10-05"}, {"dia": "2027-01-01"}],
            [{"periodo": "2026-10", "enviados": 2}],
        ))
        area = {"id": 7, "nombre_visible": "Área 7"}
        with patch.object(main, "conectar", return_value=cursor), \
             patch.object(main, "_exigir_area", return_value=area):
            resultado = main.estadisticas_envios(granularidad="mes", area_id=7,
                                                  periodos=None, sesion={})

        self.assertEqual(resultado["dias_disponibles"],
                         ["2026-10-03", "2026-10-05", "2027-01-01"])
        self.assertEqual(resultado["meses_disponibles"], ["2026-10", "2027-01"])
        self.assertEqual(resultado["anios_disponibles"], [2026, 2027])
        self.assertEqual(cursor.consultas[0][1], ("%Y-%m-%d", 7, 7))

    def test_respuesta_de_interes_antigua_cuenta_como_respondida(self):
        expresion = main.expr_respuesta_efectiva(
            "p", tabla_log="pacientes_test", tiene_interes=True)
        self.assertIn("le.tabla_pacientes = 'pacientes_test'", expresion)
        self.assertIn("p.interesado, 0) = 1", expresion)
        self.assertIn("p.no_interesado, 0) = 1", expresion)
        self.assertLess(expresion.index("le.respuesta = 'baja'"),
                        expresion.index("p.interesado, 0) = 1"))

    def test_webhook_registra_respuesta_en_cada_base(self):
        class ConexionFalsa:
            def __init__(self):
                self.consultas = []
                self.commits = 0

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def cursor(self):
                return self

            def execute(self, sql, parametros=None):
                self.consultas.append((sql, parametros))

            def commit(self):
                self.commits += 1

        conexion = ConexionFalsa()
        pacientes = [
            {"id": 1, "nombre": "A", "telefono": "test", "tabla": "pacientes_prod"},
            {"id": 2, "nombre": "B", "telefono": "test", "tabla": "pacientes_test"},
        ]
        servicio = whatsapp_service.WhatsAppService()
        with patch.object(whatsapp_service, "conectar", return_value=conexion), \
             patch.object(servicio, "_pacientes_con_tel", return_value=pacientes), \
             patch.object(whatsapp_service.servicio_areas, "area_por_tabla",
                          side_effect=lambda tabla: {"id": 9} if tabla == "pacientes_test" else None):
            resultado = servicio._registrar_respuesta("test", "hola", "2026-10-07")

        registros = [params for sql, params in conexion.consultas if sql.startswith("INSERT INTO log_envios")]
        self.assertEqual(resultado, "registrada")
        self.assertEqual(conexion.commits, 1)
        self.assertEqual([(r[0], r[-2], r[-1]) for r in registros],
                         [(1, None, "pacientes_prod"), (2, 9, "pacientes_test")])


if __name__ == "__main__":
    unittest.main()
