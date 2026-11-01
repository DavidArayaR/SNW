"""Demostración persistida sin mensajes, pacientes, secretos ni contacto externo."""
import datetime as dt
import inspect
import json
import sys
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import main
import simulacion_envio as simulacion
from db import conectar


class DatosSimulacionTests(unittest.TestCase):
    def test_350_destinatarios_y_porcentajes_sin_errores(self):
        datos = simulacion.datos_ejemplo(7, dt.datetime(2026, 10, 31, 12))
        resumen = datos["resumen"]
        self.assertEqual(resumen["validos"] + resumen["errores"] + resumen["invalidos"], 350)
        self.assertEqual(sum(s["cantidad"] for s in datos["segmentos"]), 300)
        self.assertEqual(sum(s["costo"] for s in datos["segmentos"]), 16200)
        self.assertEqual(sum(s["cantidad"] for s in datos["segmentos"] if s["visto_sin_respuesta"]), 80)
        for clave, cantidad in (("interes", 90), ("desinteres", 45), ("baja", 15)):
            self.assertEqual(sum(s["cantidad"] for s in datos["segmentos"] if s["resultado"] == clave), cantidad)
            self.assertEqual(datos["interacciones"][clave]["porcentaje"], cantidad / 300 * 100)
        self.assertGreater(sum(b["clics"] for b in datos["botones"]), 150)
        self.assertTrue(datos["simulacion"])
        self.assertNotIn("telefono_paciente", json.dumps(datos))

    def test_endpoint_solo_admin_y_desarrolladores(self):
        self.assertIs(inspect.signature(main.crear_envio_simulado).parameters["sesion"].default.dependency,
                      main.solo_admin)


class SimulacionMySQLTests(unittest.TestCase):
    def test_idempotencia_y_aislamiento_sin_log_envios(self):
        try:
            conn = conectar()
        except Exception:
            self.skipTest("MySQL no disponible")
        solicitud = str(uuid.uuid4())
        envio_id = None
        try:
            datos = simulacion.crear(solicitud, "test_simulacion")
            envio_id = datos["envio"]["id"]
            self.assertEqual(simulacion.crear(solicitud, "test_simulacion")["envio"]["id"], envio_id)
            self.assertEqual(simulacion.obtener(envio_id), datos)
            with conn.cursor() as cur:
                cur.execute("SELECT base_datos,total_pacientes,enviados,fallidos,invalidos FROM envios WHERE id=%s", (envio_id,))
                registro = cur.fetchone()
                self.assertEqual(registro["base_datos"], "simulacion")
                self.assertEqual((registro["total_pacientes"], registro["enviados"], registro["fallidos"], registro["invalidos"]), (350, 300, 40, 10))
                cur.execute("SELECT COUNT(*) AS n FROM log_envios WHERE envio_id=%s", (envio_id,))
                self.assertEqual(cur.fetchone()["n"], 0)
                # El alcance de producción real no incluye esta base aislada.
                cur.execute("SELECT COUNT(*) AS n FROM envios WHERE id=%s AND " +
                            "base_datos<>'pacientes_dev' AND (base_datos='pacientes_prod' OR area_id IS NOT NULL)", (envio_id,))
                self.assertEqual(cur.fetchone()["n"], 0)
        finally:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM envios_simulaciones WHERE solicitud_id=%s AND usuario='test_simulacion'", (solicitud,))
                if envio_id:
                    cur.execute("DELETE FROM envios WHERE id=%s AND base_datos='simulacion' AND usuario='test_simulacion'", (envio_id,))
            conn.commit()
            conn.close()


if __name__ == "__main__":
    unittest.main()
