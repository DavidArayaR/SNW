"""El registro de errores se conserva en disco sin persistir secretos obvios."""

import contextlib
import io
import logging
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import db
import main
import whatsapp_webhook


class RegistroErroresTests(unittest.TestCase):
    def test_crea_carpeta_solo_con_errores_y_un_archivo_por_error(self):
        with tempfile.TemporaryDirectory() as temporal:
            carpeta = Path(temporal) / "logs"
            with patch.object(db, "LOGS_DIR", carpeta), contextlib.redirect_stderr(io.StringIO()):
                self.assertFalse(carpeta.exists())
                db.log_error("primer fallo")
                db.log_error("segundo fallo")
            archivos = list(carpeta.glob("errores-*.log"))
            self.assertEqual(len(archivos), 2)
            for archivo in archivos:
                self.assertRegex(archivo.name,
                                 r"^errores-\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}-\d{6}-\d+-\d+\.log$")
            contenidos = [archivo.read_text(encoding="utf-8").splitlines()[0]
                         for archivo in archivos]
            self.assertEqual(sum("primer fallo" in texto for texto in contenidos), 1)
            self.assertEqual(sum("segundo fallo" in texto for texto in contenidos), 1)

    def test_nombre_distingue_errores_del_mismo_instante(self):
        instante = datetime(2026, 10, 23, 11, 15, 54, 123456)
        primero = db._nombre_log_para(instante, 1)
        segundo = db._nombre_log_para(instante, 2)
        self.assertTrue(primero.startswith("errores-2026-10-23_11-15-54-123456-"))
        self.assertNotEqual(primero, segundo)

    def test_log_y_consola_muestran_accion_y_errores_de_cinco_minutos(self):
        with tempfile.TemporaryDirectory() as temporal:
            carpeta = Path(temporal) / "logs"
            carpeta.mkdir()
            ahora = datetime.now(db._ZONA_LOG)
            reciente = carpeta / db._nombre_log_para(ahora - timedelta(minutes=4), 90001)
            antiguo = carpeta / db._nombre_log_para(ahora - timedelta(minutes=6), 90002)
            reciente.write_text("[ERROR] fallo reciente\nAcción: HTTP POST /api/prueba\n",
                                encoding="utf-8")
            antiguo.write_text("[ERROR] fallo antiguo\nAcción: HTTP GET /api/vieja\n",
                               encoding="utf-8")
            consola = io.StringIO()
            with patch.object(db, "LOGS_DIR", carpeta), contextlib.redirect_stderr(consola):
                with db.accion_de_error("HTTP POST /api/actual"):
                    db.log_error("fallo actual")
            nuevos = [ruta for ruta in carpeta.glob("errores-*.log")
                      if ruta not in (reciente, antiguo)]
            self.assertEqual(len(nuevos), 1)
            contenido = nuevos[0].read_text(encoding="utf-8")
            self.assertIn("Acción: HTTP POST /api/actual", contenido)
            self.assertIn("fallo reciente", contenido)
            self.assertNotIn("fallo antiguo", contenido)
            self.assertIn("[ACCIÓN] HTTP POST /api/actual", consola.getvalue())
            self.assertIn("fallo reciente", consola.getvalue())
            self.assertNotIn("fallo antiguo", consola.getvalue())

    def test_contexto_de_accion_no_se_filtra_al_error_siguiente(self):
        with tempfile.TemporaryDirectory() as temporal:
            carpeta = Path(temporal) / "logs"
            with patch.object(db, "LOGS_DIR", carpeta), contextlib.redirect_stderr(io.StringIO()):
                with db.accion_de_error("acción temporal"):
                    db.log_error("primer error")
                db.log_error("segundo error")
            segundo = next(ruta for ruta in carpeta.glob("*.log")
                           if "segundo error" in ruta.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(segundo.read_text(encoding="utf-8").splitlines()[1],
                             "Acción: segundo error")

    def test_peticion_http_registra_la_ruta_que_provoco_el_error(self):
        with tempfile.TemporaryDirectory() as temporal:
            carpeta = Path(temporal) / "logs"
            with patch.object(db, "LOGS_DIR", carpeta), \
                    patch.object(whatsapp_webhook, "config_get", return_value=""), \
                    contextlib.redirect_stderr(io.StringIO()):
                respuesta = TestClient(main.app).post("/api/whatsapp/webhook", json={})
            self.assertEqual(respuesta.status_code, 503)
            contenido = next(carpeta.glob("*.log")).read_text(encoding="utf-8")
            self.assertIn("Acción: HTTP POST /api/whatsapp/webhook", contenido)

    def test_excepcion_conserva_tipo_y_traza_sin_mensaje_sensible(self):
        with tempfile.TemporaryDirectory() as temporal:
            carpeta = Path(temporal) / "logs"
            with patch.object(db, "LOGS_DIR", carpeta), contextlib.redirect_stderr(io.StringIO()):
                try:
                    raise ValueError("contraseña secreta del paciente")
                except ValueError as error:
                    db.log_error("contacto persona@example.com +56912345678", error,
                                 solo_tipo=True)
            texto = next(carpeta.glob("*.log")).read_text(encoding="utf-8")
            self.assertIn("ValueError", texto)
            self.assertIn("test_error_logging.py", texto)
            self.assertNotIn("contraseña secreta", texto)
            self.assertNotIn("persona@example.com", texto)
            self.assertNotIn("+56912345678", texto)

    def test_errores_de_logging_estandar_se_archivan_una_vez(self):
        with tempfile.TemporaryDirectory() as temporal:
            carpeta = Path(temporal) / "logs"
            raiz = logging.getLogger()
            uvicorn = logging.getLogger("uvicorn.error")
            with patch.object(db, "LOGS_DIR", carpeta):
                db.instalar_registro_errores()
                db.instalar_registro_errores()
                try:
                    logging.getLogger("uvicorn.error").error("fallo de arranque")
                finally:
                    raiz.removeHandler(db._ARCHIVO_ERRORES_HANDLER)
                    uvicorn.removeHandler(db._ARCHIVO_ERRORES_HANDLER)
            texto = next(carpeta.glob("*.log")).read_text(encoding="utf-8")
            self.assertEqual(texto.splitlines()[0].count("fallo de arranque"), 1)


if __name__ == "__main__":
    unittest.main()
