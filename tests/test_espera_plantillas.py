"""Regresión de la espera por clínica, usando una tabla temporal aislada."""
import datetime
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import main


class EsperaPlantillasTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.conn = main.conectar()
            cls.cur = cls.conn.cursor()
            cls.cur.execute("CREATE TEMPORARY TABLE log_envios (numero_telefono VARCHAR(20), envio_id INT, "
                            "estado_envio VARCHAR(20), whatsapp_message_id VARCHAR(40), "
                            "estado_whatsapp VARCHAR(20), centro_codigo VARCHAR(20), "
                            "wa_phone_id VARCHAR(30), fecha_hora DATETIME)")
        except Exception as exc:
            raise unittest.SkipTest(f"No hay base local para la prueba aislada: {type(exc).__name__}")

    @classmethod
    def tearDownClass(cls):
        cls.cur.close()
        cls.conn.close()

    def setUp(self):
        self.cur.execute("DELETE FROM log_envios")
        self.ahora = datetime.datetime(2026, 11, 4, 12)
        self.cur.execute("INSERT INTO log_envios VALUES (%s,1,'enviado','mensaje','read','SMPT','123',%s)",
                         ('+56912345678', self.ahora - datetime.timedelta(hours=23)))

    def disponible(self, centro='SMPT', phone='123', fecha=None):
        self.cur.execute("SELECT p.telefono FROM (SELECT '912345678' AS telefono) p WHERE "
                         + main._condicion_espera_plantilla(),
                         main._parametros_espera({'codigo': centro, 'phoneNumberId': phone}, fecha or self.ahora))
        return bool(self.cur.fetchone())

    def test_espera_por_clinica_y_fecha_programada(self):
        self.assertFalse(self.disponible())
        self.assertTrue(self.disponible('OTRA', '456'))
        self.assertTrue(self.disponible(fecha=self.ahora + datetime.timedelta(hours=1)))
        self.assertFalse(self.disponible(fecha=self.ahora + datetime.timedelta(minutes=59)))

    def test_fallo_y_mensaje_de_servicio_no_cuentan(self):
        self.cur.execute("UPDATE log_envios SET estado_whatsapp='failed'")
        self.assertTrue(self.disponible())
        self.cur.execute("UPDATE log_envios SET estado_whatsapp='read', envio_id=NULL")
        self.assertTrue(self.disponible())


if __name__ == '__main__':
    unittest.main()
