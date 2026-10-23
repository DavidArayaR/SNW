"""Lectura segura de los registros de errores desde Configuración."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import main


class ConfigLogsTests(unittest.TestCase):
    def test_lista_y_lectura_de_archivos_diarios(self):
        with tempfile.TemporaryDirectory() as temporal:
            carpeta = Path(temporal) / "logs"
            with patch.object(main, "LOGS_DIR", carpeta):
                self.assertEqual(main.listar_logs_consola(sesion={}), {"archivos": []})
                carpeta.mkdir()
                (carpeta / "errores-2026-10-23.log").write_text("error reciente\n", encoding="utf-8")
                (carpeta / "errores-2026-10-22.log").write_text("error anterior\n", encoding="utf-8")
                (carpeta / "otro.txt").write_text("no mostrar", encoding="utf-8")
                lista = main.listar_logs_consola(sesion={})["archivos"]
                self.assertEqual([item["nombre"] for item in lista], [
                    "errores-2026-10-23.log", "errores-2026-10-22.log"])
                resultado = main.leer_log_consola("errores-2026-10-23.log", sesion={})
                self.assertEqual(resultado["contenido"].replace("\r\n", "\n"),
                                 "error reciente\n")
                self.assertFalse(resultado["truncado"])

    def test_rechaza_rutas_arbitrarias(self):
        with tempfile.TemporaryDirectory() as temporal:
            with patch.object(main, "LOGS_DIR", Path(temporal)):
                for nombre in ("../.env", "otro.txt", "errores-2026-10-23.log/../.env"):
                    with self.subTest(nombre=nombre), self.assertRaises(HTTPException) as error:
                        main.leer_log_consola(nombre, sesion={})
                    self.assertEqual(error.exception.status_code, 404)

    def test_archivo_grande_informa_recorte(self):
        with tempfile.TemporaryDirectory() as temporal:
            carpeta = Path(temporal)
            (carpeta / "errores-2026-10-23.log").write_text(
                "antiguo\nreciente\n", encoding="utf-8")
            with patch.object(main, "LOGS_DIR", carpeta), \
                    patch.object(main, "_MAX_LOG_VISTA_BYTES", 12):
                resultado = main.leer_log_consola("errores-2026-10-23.log", sesion={})
            self.assertTrue(resultado["truncado"])
            self.assertEqual(resultado["contenido"].replace("\r\n", "\n"), "reciente\n")

    def test_api_no_expone_logs_sin_sesion(self):
        cliente = TestClient(main.app)
        self.assertIn(cliente.get("/api/configuracion/logs").status_code, (401, 403))
        self.assertIn(cliente.get("/api/configuracion/logs/errores-2026-10-23.log").status_code,
                      (401, 403))

    def test_api_exige_rol_desarrollador(self):
        cliente = TestClient(main.app)
        main.app.dependency_overrides[main.sesion_actual] = lambda: {"rol": "administrador"}
        try:
            self.assertEqual(cliente.get("/api/configuracion/logs").status_code, 403)
            self.assertEqual(cliente.get(
                "/api/configuracion/logs/errores-2026-10-23.log").status_code, 403)
        finally:
            main.app.dependency_overrides.pop(main.sesion_actual, None)


if __name__ == "__main__":
    unittest.main()
