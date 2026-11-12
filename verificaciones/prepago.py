"""Pruebas de Extras sin pagos ni correo real.

Ejecutar con python verificaciones/prepago.py.
Con --puerto se añade integración contra MariaDB local de pruebas.
"""

import argparse
import datetime as dt
import smtplib
import sys
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

import pymysql
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from snw.extras import prepago
from snw.infraestructura import config_service
from routes.prepago import registrar

PUERTO = None
SMTP = {"host": "smtp.example.test", "port": 587, "emisor": "snw@example.test",
        "destino": "admin@example.test", "tls": True, "user": "", "pwd": ""}


class PrepagoUnitario(unittest.TestCase):
    def test_calendario_bisiesto_y_cambio_de_anio(self):
        ciclo = dt.date(2023, 10, 2)
        vence = dt.date(2024, 2, 29)
        antes = prepago.calendario(ciclo, vence - dt.timedelta(days=1))
        self.assertEqual(antes["proximo_recordatorio"], vence)
        self.assertEqual(antes["dias_restantes"], 1)
        self.assertEqual(antes["estado"], "Próximo")
        self.assertEqual(prepago.calendario(ciclo, vence)["estado"], "Pendiente")
        inicial = prepago.calendario(None, vence)
        self.assertEqual(inicial["estado"], "Pendiente")
        self.assertIsNone(inicial["proximo_recordatorio"])
        self.assertIsNone(inicial["dias_restantes"])
        self.assertEqual(prepago.ZONA.key, "America/Santiago")

    def test_urls_de_operador_y_dominio(self):
        for _, _, dominio, url in prepago.OPERADORES:
            self.assertEqual(prepago.url_oficial(url, dominio), (dominio, url))
        for url in ("http://wom.cl", "https://wom.cl.ejemplo.org", "https://wom.cl@evil.org",
                    "https://wom.cl/?redirect=evil", "https://wom.cl\\evil.org"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                prepago.url_oficial(url, "wom.cl")

    def test_plazo_configurable(self):
        self.assertEqual(prepago.plazo_recordatorio({}), 150)
        for invalido in ("", "no", "0", "3651"):
            self.assertEqual(prepago.plazo_recordatorio({prepago.CLAVE_PLAZO: invalido}), 150)
        dias = prepago.plazo_recordatorio({prepago.CLAVE_PLAZO: "90"})
        fecha = dt.date(2026, 1, 1)
        self.assertEqual(prepago.calendario(fecha, fecha, dias)["dias_restantes"], 90)
        with patch("snw.extras.prepago.config_set") as guardar:
            prepago.guardar_plazo(dias)
            guardar.assert_called_once_with({prepago.CLAVE_PLAZO: 90})

    def test_smtp_error_confirmado_y_entrega_incierta(self):
        casos = [(None, "enviado"),
                 (smtplib.SMTPDataError(451, b"rechazado"), "error"),
                 (smtplib.SMTPServerDisconnected(), "incierto")]
        for fallo, esperado in casos:
            with self.subTest(esperado=esperado), patch("snw.infraestructura.config_service.smtplib.SMTP") as smtp:
                conexion = smtp.return_value.__enter__.return_value
                conexion.sendmail.side_effect = fallo
                estado, _ = config_service.entregar_correo_real(SMTP["destino"], "Aviso", "<p>Hola</p>", SMTP)
                self.assertEqual(estado, esperado)
                smtp.assert_called_once_with(SMTP["host"], 587, timeout=15)
                conexion.starttls.assert_called_once()

    def test_permisos_http(self):
        def admin(rol: str = Header("")):
            if rol not in {"admin", "dev"}:
                raise HTTPException(403)
            return {"usuario": "pruebas"}

        def dev(rol: str = Header("")):
            if rol != "dev":
                raise HTTPException(403)
            return {"usuario": "pruebas"}

        app = FastAPI()
        app.include_router(registrar(admin, dev))
        with TestClient(app) as cliente, patch("snw.extras.prepago.estado", return_value={"lineas": []}):
            ruta = "/api/extras/prepago"
            self.assertEqual(cliente.get(ruta).status_code, 403)
            self.assertEqual(cliente.get(ruta, headers={"rol": "admin"}).json(), {"lineas": []})
            self.assertEqual(cliente.put(ruta + "/operadores", headers={"rol": "admin"},
                                         json={"codigo": "nuevo", "nombre": "Nuevo"}).status_code, 403)
            self.assertEqual(cliente.post(ruta + "/lineas", headers={"rol": "dev"},
                                          json={"nombre": "Ejemplo"}).status_code, 422)
            with patch("snw.extras.prepago.guardar_plazo") as guardar, patch("routes.prepago.auditoria_registrar"):
                self.assertEqual(cliente.put(ruta + "/plazo", json={"dias": 90}).status_code, 403)
                for invalido in (0, 3651, 1.5, True):
                    self.assertEqual(cliente.put(ruta + "/plazo", headers={"rol": "admin"},
                                                 json={"dias": invalido}).status_code, 422)
                self.assertEqual(cliente.put(ruta + "/plazo", headers={"rol": "admin"},
                                             json={"dias": 90}).json(), {"recordatorio_dias": 90})
                guardar.assert_called_once_with(90)


class PrepagoMariaDB(unittest.TestCase):
    def setUp(self):
        if PUERTO is None:
            self.skipTest("Ejecuta con --puerto para probar MariaDB.")
        esquema = "snw_test_prepago_" + uuid.uuid4().hex

        def conectar(database=None):
            return pymysql.connect(host="127.0.0.1", port=PUERTO, user="root",
                                   database=database, cursorclass=pymysql.cursors.DictCursor)

        def borrar():
            with conectar() as conn, conn.cursor() as cur:
                cur.execute(f"DROP DATABASE `{esquema}`")

        with conectar() as conn, conn.cursor() as cur:
            cur.execute(f"CREATE DATABASE `{esquema}` CHARACTER SET utf8mb4")
        self.addCleanup(borrar)
        self.db = lambda: conectar(esquema)
        for sustitucion in (
            patch.object(prepago, "conectar", self.db),
            patch.object(prepago, "config_all", return_value={"prepago_correo_destino": "extras@example.test"}),
            patch.object(prepago, "config_correo", return_value=SMTP),
            patch.object(prepago, "log_error"),
        ):
            sustitucion.start()
            self.addCleanup(sustitucion.stop)
        self.hoy = prepago.hoy()
        self.config = {"VS_WSP_PHONE_NUMBER": "+56900000001", "MS_CRM_PHONE_NUMBER": "+56900000002"}
        prepago.asegurar_esquema(self.config)
        with self.db() as conn, conn.cursor() as cur:
            cur.execute("UPDATE prepago_lineas SET avisar=0 WHERE semilla IS NOT NULL")
            conn.commit()

    def crear(self, numero, fecha=None):
        return prepago.guardar_linea(dict(nombre="Línea ficticia", telefono=numero, operador="entel",
                                          sistema="Otro", clinica="MVYS", ultima_recarga=fecha,
                                          activo=True, avisar=True), "pruebas")

    def test_semillas_lineas_recarga_y_reinicio(self):
        prepago.asegurar_esquema({"VS_WSP_PHONE_NUMBER": "+56900000003"})
        lineas = prepago.estado()["lineas"]
        self.assertEqual([l["nombre"] for l in lineas], ["SNW MVYS", "SNW MS", "CRM MS", "CRM MVYS"])
        self.assertEqual([l["telefono"] for l in lineas], ["+56900000001", None, "+56900000002", None])
        self.assertTrue(all(l["operador"] == "wom" and not l["ultima_recarga"] for l in lineas))
        linea_id = self.crear("9 0000 0004", self.hoy - dt.timedelta(days=50))
        self.crear("900000005")
        with self.assertRaisesRegex(ValueError, "ya está registrado"):
            self.crear("+56900000004")
        with self.assertRaisesRegex(ValueError, "válido"):
            self.crear("inválido")
        self.assertEqual(len(prepago.estado()["lineas"]), 6)
        prepago.registrar_recarga(linea_id, self.hoy, "Anticipada", "pruebas", 1)
        with self.assertRaisesRegex(RuntimeError, "La línea cambió"):
            prepago.registrar_recarga(linea_id, self.hoy, "", "pruebas", 1)
        l = next(l for l in prepago.estado()["lineas"] if l["id"] == linea_id)
        self.assertEqual(l["clinica"], "VS")
        self.assertEqual(l["dias_restantes"], 150)
        self.assertIn("entel.cl", l["url_recarga"])
        l.update(operador="claro", activo=False)
        prepago.guardar_linea(l, "pruebas", linea_id)
        l = next(l for l in prepago.estado()["lineas"] if l["id"] == linea_id)
        self.assertFalse(l["activo"])
        self.assertIn("clarochile.cl", l["url_recarga"])
        self.assertEqual(len(prepago.historial(linea_id, 1)["recargas"]), 2)

    def test_concurrencia_ciclo_y_reintento(self):
        fecha = self.hoy - dt.timedelta(days=150)
        linea = self.crear("900000004", fecha)
        otra = self.crear("900000007", fecha)
        futura = self.crear("900000008", self.hoy)
        entro = threading.Event()
        liberar = threading.Event()

        def enviar(*args):
            entro.set()
            if not liberar.wait(5):
                raise RuntimeError("La segunda instancia no terminó.")
            return "enviado", None

        smtp = Mock(side_effect=enviar)
        prepago.revisar_recordatorios(self.hoy - dt.timedelta(days=1), smtp)
        smtp.assert_not_called()
        with ThreadPoolExecutor(max_workers=2) as pool:
            primera = pool.submit(prepago.revisar_recordatorios, self.hoy, smtp)
            try:
                self.assertTrue(entro.wait(5))
                pool.submit(prepago.revisar_recordatorios, self.hoy, smtp).result(5)
            finally:
                liberar.set()
            primera.result(5)
        prepago.asegurar_esquema(self.config)
        prepago.revisar_recordatorios(self.hoy, smtp)
        smtp.assert_called_once()
        self.assertEqual(smtp.call_args.args[0], "extras@example.test")
        _, asunto, cuerpo, _ = smtp.call_args.args
        self.assertEqual(asunto, "SNW | Recordatorio de recarga prepago")
        vencidas, restantes = cuerpo.split("<h2>Otras líneas activas</h2>")
        self.assertIn("+56900000004", vencidas)
        self.assertIn("+56900000007", vencidas)
        self.assertIn("+56900000008", restantes)
        self.assertEqual(prepago.historial(linea, 1)["avisos"][0]["estado"], "enviado")
        self.assertEqual(prepago.historial(otra, 1)["avisos"][0]["estado"], "enviado")
        self.assertFalse(prepago.historial(futura, 1)["avisos"])
        corregida = next(l for l in prepago.estado()["lineas"] if l["id"] == linea)
        corregida["ultima_recarga"] -= dt.timedelta(days=1)
        prepago.guardar_linea(corregida, "pruebas", linea)
        prepago.revisar_recordatorios(self.hoy, smtp)
        smtp.assert_called_once()
        prepago.registrar_recarga(linea, self.hoy, "", "pruebas", 2)

        fallida = self.crear("900000005", fecha)
        otra_fallida = self.crear("900000010", fecha)
        smtp = Mock(side_effect=[("error", "SMTPDataError"), ("enviado", None)])
        prepago.revisar_recordatorios(self.hoy, smtp)
        prepago.revisar_recordatorios(self.hoy, smtp)
        self.assertEqual(smtp.call_count, 1)
        prepago.revisar_recordatorios(self.hoy + dt.timedelta(days=1), smtp)
        self.assertEqual([a["estado"] for a in prepago.historial(fallida, 1)["avisos"]],
                         ["enviado", "error"])
        self.assertEqual([a["estado"] for a in prepago.historial(otra_fallida, 1)["avisos"]],
                         ["enviado", "error"])
        incierta = self.crear("900000006", fecha)
        smtp = Mock(return_value=("incierto", "SMTPServerDisconnected"))
        prepago.revisar_recordatorios(self.hoy, smtp)
        prepago.revisar_recordatorios(self.hoy + dt.timedelta(days=1), smtp)
        smtp.assert_called_once()
        self.assertEqual(prepago.historial(incierta, 1)["avisos"][0]["estado"], "incierto")

    def test_plazo_recalcula_ciclos_sin_reenviar(self):
        linea_id = self.crear("900000004", self.hoy - dt.timedelta(days=90))
        smtp = Mock(return_value=("enviado", None))
        prepago.revisar_recordatorios(self.hoy, smtp)
        smtp.assert_not_called()
        with patch.object(prepago, "config_all", return_value={prepago.CLAVE_PLAZO: "90"}):
            linea = next(l for l in prepago.estado()["lineas"] if l["id"] == linea_id)
            self.assertEqual(linea["proximo_recordatorio"], self.hoy)
            prepago.revisar_recordatorios(self.hoy, smtp)
        with patch.object(prepago, "config_all", return_value={prepago.CLAVE_PLAZO: "60"}):
            prepago.revisar_recordatorios(self.hoy, smtp)
        smtp.assert_called_once()
        self.assertEqual(prepago.historial(linea_id, 1)["avisos"][0]["programado"], self.hoy)

    def test_migracion_y_lineas_sin_fecha(self):
        # Reproduce un esquema instalado antes de incorporar las etapas.
        with self.db() as conn, conn.cursor() as cur:
            cur.execute("ALTER TABLE prepago_recordatorios MODIFY fecha_ultima_recarga DATE NOT NULL")
            cur.execute("ALTER TABLE prepago_recordatorios DROP COLUMN etapa")
        prepago.asegurar_esquema(self.config)
        prepago.asegurar_esquema(self.config)
        iniciales = [self.crear("900000004"), self.crear("900000005")]
        lista = {linea["id"]: linea for linea in prepago.estado()["lineas"]}
        self.assertTrue(all(lista[id_]["estado"] == "Pendiente" for id_ in iniciales))
        sin_numero = next(linea for linea in lista.values() if linea["nombre"] == "SNW MS")
        self.assertEqual(sin_numero["estado"], "Sin configurar")
        smtp = Mock(return_value=("enviado", None))
        prepago.revisar_recordatorios(self.hoy, smtp)
        smtp.assert_not_called()
        for linea_id in iniciales:
            self.assertFalse(prepago.historial(linea_id, 1)["avisos"])
            prepago.registrar_recarga(linea_id, self.hoy, "", "pruebas", 1)
        lista = {linea["id"]: linea for linea in prepago.estado()["lineas"]}
        self.assertTrue(all(lista[id_]["dias_restantes"] == 150 for id_ in iniciales))
        self.assertTrue(all(lista[id_]["estado"] == "Al día" for id_ in iniciales))

    def test_aviso_urgente_cuatro_dias_despues_solo_para_ciclo_sin_recarga(self):
        fecha = self.hoy - dt.timedelta(days=150)
        pendiente = self.crear("900000004", fecha)
        recargada = self.crear("900000005", fecha)
        nuevo_vencimiento = self.crear("900000006", fecha + dt.timedelta(days=4))
        smtp = Mock(return_value=("enviado", None))
        prepago.revisar_recordatorios(self.hoy - dt.timedelta(days=1), smtp)
        smtp.assert_not_called()
        prepago.revisar_recordatorios(self.hoy, smtp)
        smtp.assert_called_once()
        self.assertNotIn("URGENTE", smtp.call_args.args[1])
        prepago.registrar_recarga(recargada, self.hoy, "", "pruebas", 1)
        prepago.revisar_recordatorios(self.hoy + dt.timedelta(days=3), smtp)
        smtp.assert_called_once()
        fecha_urgente = self.hoy + dt.timedelta(days=4)
        prepago.revisar_recordatorios(fecha_urgente, smtp)
        prepago.revisar_recordatorios(fecha_urgente, smtp)
        self.assertEqual(smtp.call_count, 2)
        self.assertIn("URGENTE", smtp.call_args.args[1])
        cuerpo = smtp.call_args.args[2].split("<h2>Otras líneas activas</h2>")[0]
        self.assertIn("+56900000004", cuerpo)
        self.assertIn("+56900000006", cuerpo)
        self.assertNotIn("+56900000005", cuerpo)
        self.assertIn("Recarga urgente", cuerpo)
        avisos = prepago.historial(pendiente, 1)["avisos"]
        self.assertEqual([a["etapa"] for a in avisos], ["urgente", "inicial"])
        self.assertEqual(avisos[0]["programado"], fecha_urgente)
        for linea_id in (recargada, nuevo_vencimiento):
            self.assertEqual([a["etapa"] for a in prepago.historial(linea_id, 1)["avisos"]], ["inicial"])


if __name__ == "__main__":
    argumentos = argparse.ArgumentParser()
    argumentos.add_argument("--puerto", type=int, help="Puerto de MariaDB local de pruebas con root sin clave.")
    PUERTO = argumentos.parse_args().puerto
    unittest.main(argv=[sys.argv[0]], verbosity=2)
