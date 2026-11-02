"""Lectura segura de los registros de errores desde Configuración."""

import sys
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import main


class ConfigLogsTests(unittest.TestCase):
    def test_borrado_por_lotes_primero_mas_antiguos_y_minimo_24_horas(self):
        with tempfile.TemporaryDirectory() as temporal:
            carpeta = Path(temporal)
            ahora = 2000000000
            nombres = ["errores-2026-10-20.log", "csv-remoto-2026-10-21.log",
                       "errores-2026-10-22.log", "errores-2026-10-23.log"]
            edades = [200000, 100000, 86400, 86399]
            for nombre, edad in zip(nombres, edades):
                ruta = carpeta / nombre
                ruta.write_text("registro", encoding="utf-8")
                os.utime(ruta, (ahora - edad, ahora - edad))
            ajeno = carpeta / "otro.log"
            ajeno.write_text("conservar", encoding="utf-8")
            os.utime(ajeno, (0, 0))
            with patch.object(main, "LOGS_DIR", carpeta), \
                    patch.object(main.time, "time", return_value=ahora), \
                    patch.object(main, "_LOGS_LOTE_BORRADO", 2):
                self.assertEqual(main.borrar_logs_antiguos(sesion={}),
                                 {"eliminados": 2, "pendientes": 1, "errores": 0})
                self.assertFalse((carpeta / nombres[0]).exists())
                self.assertFalse((carpeta / nombres[1]).exists())
                self.assertTrue((carpeta / nombres[2]).exists())
                self.assertEqual(main.borrar_logs_antiguos(sesion={})["eliminados"], 1)
                self.assertEqual(main.borrar_logs_antiguos(sesion={})["eliminados"], 0)
                self.assertTrue((carpeta / nombres[3]).exists())
                self.assertTrue(ajeno.exists())

    def test_borrado_no_crea_carpeta_y_no_borra_directorios(self):
        with tempfile.TemporaryDirectory() as temporal:
            carpeta = Path(temporal) / "logs"
            with patch.object(main, "LOGS_DIR", carpeta):
                self.assertEqual(main.borrar_logs_antiguos(sesion={})["eliminados"], 0)
                self.assertFalse(carpeta.exists())
                carpeta.mkdir()
                (carpeta / "errores-2026-10-20.log").mkdir()
                self.assertEqual(main.borrar_logs_antiguos(sesion={})["eliminados"], 0)

    def test_lista_y_lectura_de_archivos_diarios(self):
        with tempfile.TemporaryDirectory() as temporal:
            carpeta = Path(temporal) / "logs"
            with patch.object(main, "LOGS_DIR", carpeta):
                self.assertEqual(main.listar_logs_consola(sesion={}), {"archivos": []})
                carpeta.mkdir()
                (carpeta / "errores-2026-10-23_11-15-54-123456-42.log").write_text(
                    "error reciente\n", encoding="utf-8")
                (carpeta / "errores-2026-10-23_11-15-54-123456-42-1.log").write_text(
                    "error nuevo\n", encoding="utf-8")
                (carpeta / "errores-2026-10-22.log").write_text("error anterior\n", encoding="utf-8")
                (carpeta / "otro.txt").write_text("no mostrar", encoding="utf-8")
                lista = main.listar_logs_consola(sesion={})["archivos"]
                self.assertEqual([item["nombre"] for item in lista], [
                    "errores-2026-10-23_11-15-54-123456-42-1.log",
                    "errores-2026-10-23_11-15-54-123456-42.log",
                    "errores-2026-10-22.log"])
                resultado = main.leer_log_consola(
                    "errores-2026-10-23_11-15-54-123456-42.log", sesion={})
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
        self.assertIn(cliente.delete("/api/configuracion/logs").status_code, (401, 403))
        self.assertIn(cliente.get("/api/configuracion/logs/errores-2026-10-23.log").status_code,
                      (401, 403))

    def test_api_exige_rol_desarrollador(self):
        cliente = TestClient(main.app)
        main.app.dependency_overrides[main.sesion_actual] = lambda: {"rol": "administrador"}
        try:
            self.assertEqual(cliente.get("/api/configuracion/logs").status_code, 403)
            self.assertEqual(cliente.delete("/api/configuracion/logs").status_code, 403)
            self.assertEqual(cliente.get(
                "/api/configuracion/logs/errores-2026-10-23.log").status_code, 403)
        finally:
            main.app.dependency_overrides.pop(main.sesion_actual, None)


if __name__ == "__main__":
    unittest.main()
