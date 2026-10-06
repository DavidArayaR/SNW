import datetime
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from service_pricing import clasificar_entregas_servicio, validar_periodos


class ServicioGratisTests(unittest.TestCase):
    def test_calendario_acepta_dias_meses_y_anios_multiples(self):
        self.assertEqual(validar_periodos("2026-10-03,2026-10-05", "dia"),
                         {"2026-10-03", "2026-10-05"})
        self.assertEqual(validar_periodos("2026-10,2027-01", "mes"),
                         {"2026-10", "2027-01"})
        self.assertEqual(validar_periodos("2025,2026", "anio"), {"2025", "2026"})
        with self.assertRaises(ValueError):
            validar_periodos("2026-02-30", "dia")
        with self.assertRaises(ValueError):
            validar_periodos("2026-13", "mes")

    def test_cupo_por_numero_y_mes_solo_desde_octubre(self):
        septiembre = {"id": 1, "fecha_entrega": datetime.datetime(2026, 9, 30, 23, 59),
                      "wa_phone_id": "a"}
        octubre = [
            {"id": i + 2, "fecha_entrega": datetime.datetime(2026, 10, 1, 9, 0),
             "wa_phone_id": "a"}
            for i in range(1002)
        ]
        otro_numero = {"id": 2000, "fecha_entrega": datetime.datetime(2026, 10, 2, 9, 0),
                       "wa_phone_id": "b"}
        noviembre = {"id": 2001, "fecha_entrega": datetime.datetime(2026, 11, 1, 9, 0),
                     "wa_phone_id": "a"}
        resultado = list(clasificar_entregas_servicio(
            [noviembre, otro_numero, *reversed(octubre), septiembre]))
        por_id = {fila["id"]: gratis for fila, gratis in resultado}
        self.assertTrue(por_id[1])
        self.assertTrue(por_id[1001])
        self.assertFalse(por_id[1002])
        self.assertFalse(por_id[1003])
        self.assertTrue(por_id[2000])
        self.assertTrue(por_id[2001])

    def test_areas_comparten_el_mismo_cupo(self):
        filas = [
            {"id": i, "fecha_entrega": datetime.datetime(2026, 10, 3, 10, 0),
             "wa_phone_id": "a", "area_id": 1 if i <= 1000 else 2}
            for i in range(1, 1002)
        ]
        resultado = list(clasificar_entregas_servicio(filas))
        self.assertFalse(next(gratis for fila, gratis in resultado if fila["area_id"] == 2))

    def test_registros_antiguos_comparten_cupo_con_unico_emisor(self):
        filas = [
            {"id": i, "fecha_entrega": datetime.datetime(2026, 10, 4, 10, 0),
             "wa_phone_id": None if i <= 500 else "empresa"}
            for i in range(1, 1002)
        ]
        resultado = list(clasificar_entregas_servicio(filas))
        self.assertTrue(resultado[999][1])
        self.assertFalse(resultado[1000][1])

    def test_filtro_de_dia_no_reinicia_cupo_mensual(self):
        filas = [
            {"id": i, "fecha_entrega": datetime.datetime(2026, 10, 3, 10, 0),
             "wa_phone_id": "empresa"}
            for i in range(1, 1001)
        ]
        filas.append({"id": 1001, "fecha_entrega": datetime.datetime(2026, 10, 4, 9, 0),
                      "wa_phone_id": "empresa"})
        solo_cuatro = [(fila, gratis) for fila, gratis in clasificar_entregas_servicio(filas)
                       if fila["fecha_entrega"].date() == datetime.date(2026, 10, 4)]
        self.assertEqual(len(solo_cuatro), 1)
        self.assertFalse(solo_cuatro[0][1])


if __name__ == "__main__":
    unittest.main()
