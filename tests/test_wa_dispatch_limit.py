"""Reservas distribuidas del despacho; nunca llama a Meta ni usa pacientes reales."""

import sys
import threading
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from db import conectar
from wa_dispatch_limit import bloquear, politica, registrar_aceptado, reservar, huella_pareja


def configuracion():
    cfg = {"wa_dispatch_reserved_percent": "25", "wa_dispatch_pair_seconds": "6",
           "wa_token": "global"}
    for codigo in ("SMPT", "MS", "PS", "CP", "VS"):
        cfg[f"{codigo}_WSP_PHONE_NUMBER"] = "+5691111111" + str(len(cfg))
        cfg[f"{codigo}_WSP_PHONE_NUMBER_ID"] = str(1234500000 + len(cfg))
        cfg[f"{codigo}_WSP_WABA_ID"] = str(2234500000 + len(cfg))
        cfg[f"{codigo}_WSP_RATE_LIMIT_MPS"] = "10"
        cfg[f"{codigo}_WSP_RATE_LIMIT_MODE"] = "estandar"
        cfg[f"{codigo}_WSP_RATE_LIMIT_VERIFIED"] = "true"
    return cfg


class PoliticaDespachoTests(unittest.TestCase):
    def test_cada_centro_tiene_su_propia_capacidad(self):
        cfg = configuracion()
        cfg["SMPT_WSP_RATE_LIMIT_MPS"] = "50"
        cfg["MS_WSP_RATE_LIMIT_MPS"] = "3"
        self.assertEqual(politica(cfg["SMPT_WSP_PHONE_NUMBER_ID"], cfg)["mps"], 50)
        self.assertEqual(politica(cfg["MS_WSP_PHONE_NUMBER_ID"], cfg)["mps"], 3)

    def test_coexistencia_y_upgrade_no_verificado_tienen_techo(self):
        cfg = configuracion()
        cfg["SMPT_WSP_RATE_LIMIT_MODE"] = "coexistencia"
        cfg["SMPT_WSP_RATE_LIMIT_MPS"] = "1000"
        self.assertEqual(politica(cfg["SMPT_WSP_PHONE_NUMBER_ID"], cfg)["mps"], 15)
        cfg["SMPT_WSP_RATE_LIMIT_MODE"] = "estandar"
        self.assertEqual(politica(cfg["SMPT_WSP_PHONE_NUMBER_ID"], cfg)["mps"], 60)

    def test_numero_no_configurado_falla_cerrado(self):
        with self.assertRaises(ValueError):
            politica("desconocido", configuracion())

    def test_sin_verificar_no_se_eleva_la_velocidad(self):
        cfg = configuracion()
        cfg["SMPT_WSP_RATE_LIMIT_MPS"] = "60"
        cfg["SMPT_WSP_RATE_LIMIT_VERIFIED"] = "false"
        self.assertEqual(politica(cfg["SMPT_WSP_PHONE_NUMBER_ID"], cfg)["mps"], 10)

    def test_huella_de_pareja_usa_el_mismo_formato_con_o_sin_mas(self):
        self.assertEqual(huella_pareja("123", "+56900000001"),
                         huella_pareja("123", "56900000001"))


class ReservaMySQLTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            with conectar() as conn, conn.cursor() as cur:
                cur.execute("CREATE TABLE IF NOT EXISTS wa_despacho_canales ("
                            "phone_id VARCHAR(64) PRIMARY KEY, proximo_turno DATETIME(6) NULL,"
                            "bloqueado_hasta DATETIME(6) NULL, mps_actual INT NULL,"
                            "aceptados_desde_ajuste INT NOT NULL DEFAULT 0, ajustado_en DATETIME(6) NULL,"
                            "errores_130429 BIGINT NOT NULL DEFAULT 0,"
                            "errores_131057 BIGINT NOT NULL DEFAULT 0, enviados BIGINT NOT NULL DEFAULT 0,"
                            "actualizado DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)"
                            " ON UPDATE CURRENT_TIMESTAMP(6)) CHARACTER SET utf8mb4")
                cur.execute("CREATE TABLE IF NOT EXISTS wa_despacho_pares ("
                            "phone_id VARCHAR(64) NOT NULL, destinatario_hash CHAR(64) NOT NULL,"
                            "proximo_turno DATETIME(6) NOT NULL,"
                            "PRIMARY KEY (phone_id, destinatario_hash),"
                            "INDEX idx_pares_expira (proximo_turno)) CHARACTER SET utf8mb4")
                cur.execute("SELECT COLUMN_NAME FROM information_schema.COLUMNS"
                            " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'wa_despacho_canales'")
                columnas = {r["COLUMN_NAME"] for r in cur.fetchall()}
                for nombre, tipo in (("mps_actual", "INT NULL"),
                                     ("aceptados_desde_ajuste", "INT NOT NULL DEFAULT 0"),
                                     ("ajustado_en", "DATETIME(6) NULL")):
                    if nombre not in columnas:
                        cur.execute(f"ALTER TABLE wa_despacho_canales ADD COLUMN {nombre} {tipo}")
                conn.commit()
        except Exception as exc:
            raise unittest.SkipTest(f"MySQL no disponible para prueba de concurrencia: {type(exc).__name__}")

    def setUp(self):
        self.ids = [f"test-{uuid.uuid4().hex}" for _ in range(2)]
        self.reglas = {"mps": 10, "mps_inicial": 10, "modo": "estandar", "pair_seconds": 6}
        self.parche = patch("wa_dispatch_limit.politica", return_value=self.reglas)
        self.parche.start()

    def tearDown(self):
        self.parche.stop()
        with conectar() as conn, conn.cursor() as cur:
            for phone_id in self.ids:
                cur.execute("DELETE FROM wa_despacho_pares WHERE phone_id = %s", (phone_id,))
                cur.execute("DELETE FROM wa_despacho_canales WHERE phone_id = %s", (phone_id,))
            conn.commit()

    def test_dos_workers_mismo_emisor_comparten_turno_y_otro_centro_no(self):
        barrera = threading.Barrier(2)
        resultados = []
        def trabajador(destinatario):
            barrera.wait()
            resultados.append(reservar(self.ids[0], destinatario))
        hilos = [threading.Thread(target=trabajador, args=(f"5690000000{i}",)) for i in (1, 2)]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join(timeout=5)
        self.assertEqual(len(resultados), 2)
        self.assertEqual(sum(espera == 0 for espera in resultados), 1)
        self.assertGreater(max(resultados), 0)
        self.assertEqual(reservar(self.ids[1], "56900000001"), 0)

    def test_pareja_se_aplaza_sin_bloquear_otro_emisor(self):
        phone_id = self.ids[0]
        self.assertEqual(reservar(phone_id, "56900000001"), 0)
        self.assertGreater(reservar(phone_id, "56900000001"), 5)
        bloquear(phone_id, 130429, 4)
        self.assertGreater(reservar(phone_id, "56900000002"), 3)
        self.assertEqual(reservar(self.ids[1], "56900000002"), 0)

    def test_130429_reduce_velocidad_solo_del_emisor_afectado(self):
        self.assertEqual(reservar(self.ids[0], "56900000001"), 0)
        registrar_aceptado(self.ids[0])
        bloquear(self.ids[0], 130429, 2)
        with conectar() as conn, conn.cursor() as cur:
            cur.execute("SELECT mps_actual, enviados, errores_130429"
                        " FROM wa_despacho_canales WHERE phone_id = %s", (self.ids[0],))
            fila = cur.fetchone()
        self.assertEqual(fila["mps_actual"], 5)
        self.assertEqual(fila["enviados"], 1)
        self.assertEqual(fila["errores_130429"], 1)
        self.assertEqual(reservar(self.ids[1], "56900000001"), 0)

    def test_recupera_velocidad_solo_tras_volumen_y_estabilidad(self):
        self.reglas["mps"] = 20
        self.assertEqual(reservar(self.ids[0], "56900000001"), 0)
        with conectar() as conn, conn.cursor() as cur:
            cur.execute("UPDATE wa_despacho_canales SET mps_actual = 10,"
                        " ajustado_en = DATE_SUB(UTC_TIMESTAMP(6), INTERVAL 6 MINUTE),"
                        " aceptados_desde_ajuste = 600 WHERE phone_id = %s", (self.ids[0],))
            conn.commit()
        registrar_aceptado(self.ids[0])
        with conectar() as conn, conn.cursor() as cur:
            cur.execute("SELECT mps_actual, aceptados_desde_ajuste"
                        " FROM wa_despacho_canales WHERE phone_id = %s", (self.ids[0],))
            fila = cur.fetchone()
        self.assertEqual(fila["mps_actual"], 15)
        self.assertEqual(fila["aceptados_desde_ajuste"], 0)
