"""Precio persistido de cada reagendamiento, sin llamadas a Meta."""
import datetime as dt
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import main
import paciente_reagendar as reagendar


class PrecioReagendarTests(unittest.TestCase):
    def test_captura_tarifa_vigente_de_la_categoria_real(self):
        tarifas = [{"efectiva_desde": "2026-10-01", "moneda": "CLP", "utility": 12.5},
                   {"efectiva_desde": "2026-12-01", "moneda": "CLP", "utility": 99}]
        with patch.object(main, "_moneda_cuenta", return_value="CLP"), \
             patch.object(main, "_tarifas_guardadas", return_value=tarifas):
            precio = main._precio_reagendamiento({"whatsapp_template_categoria": "UTILITY"}, dt.date(2026, 10, 31))
        self.assertEqual(precio, {"categoria": "utility", "moneda": "CLP", "unitario": 12.5,
                                  "tarifa_fecha": "2026-10-01"})

    def test_sin_tarifas_o_solo_futuras_no_inventa_costo_cero(self):
        for tarifas in ([], [{"efectiva_desde": "2027-01-01", "moneda": "USD", "utility": 1}]):
            with patch.object(main, "_moneda_cuenta", return_value="USD"), \
                 patch.object(main, "_tarifas_guardadas", return_value=tarifas):
                precio = main._precio_reagendamiento({"whatsapp_template_categoria": "UTILITY"}, dt.date(2026, 10, 31))
            self.assertIsNone(precio["unitario"])
            self.assertEqual(precio["categoria"], "utility")

    def test_precio_cero_real_se_conserva_y_moneda_usd_es_respaldo(self):
        tarifa = {"efectiva_desde": "2026-10-01", "moneda": "USD", "utility": 0}
        with patch.object(main, "_moneda_cuenta", return_value="CLP"), \
             patch.object(main, "_tarifas_guardadas", side_effect=[[], [tarifa]]):
            precio = main._precio_reagendamiento({"whatsapp_template_categoria": "UTILITY"}, dt.date(2026, 10, 31))
        self.assertEqual(precio["unitario"], 0)
        self.assertEqual(precio["moneda"], "USD")

    def test_historial_y_precio_se_guardan_en_la_misma_transaccion(self):
        for precio in (None, {"unitario": 12.5, "moneda": "CLP", "categoria": "utility", "tarifa_fecha": "2026-10-01"}):
            conn = MagicMock()
            cur = conn.__enter__.return_value.cursor.return_value.__enter__.return_value
            cur.fetchone.return_value = {"estado_envio": "PROCESANDO", "reserva_token": "token"}
            cur.lastrowid = 8
            with patch.object(reagendar, "conectar", return_value=conn):
                reagendar.marcar_enviado({"id": 1, "reserva_token": "token", "envio_id": 3,
                    "paciente_nombre": "Prueba", "paciente_telefono_normalizado": "+56900000000"},
                    {"phoneNumberId": "prueba", "codigo": "VS", "phoneNumber": "+56900000001"},
                    {"clave": "prueba"}, "Prueba", "wamid.prueba", precio=precio)
            sql, args = next(c.args for c in cur.execute.call_args_list if c.args[0].startswith("INSERT INTO log_envios"))
            self.assertIn("precio_unitario", sql)
            self.assertEqual(args[-4:], tuple((precio or {}).get(c) for c in
                ("unitario", "moneda", "categoria", "tarifa_fecha")))
            conn.__enter__.return_value.commit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
