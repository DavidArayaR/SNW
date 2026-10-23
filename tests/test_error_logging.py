"""El registro de errores se conserva en disco sin persistir secretos obvios."""

import contextlib
import io
import logging
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import db


class RegistroErroresTests(unittest.TestCase):
    def test_crea_carpeta_al_primer_error_y_anexa_entradas(self):
        with tempfile.TemporaryDirectory() as temporal:
            carpeta = Path(temporal) / "logs"
            with patch.object(db, "LOGS_DIR", carpeta), contextlib.redirect_stderr(io.StringIO()):
                self.assertFalse(carpeta.exists())
                db.log_error("primer fallo")
                db.log_error("segundo fallo")
            archivos = list(carpeta.glob("errores-*.log"))
            self.assertEqual(len(archivos), 1)
            texto = archivos[0].read_text(encoding="utf-8")
            self.assertIn("primer fallo", texto)
            self.assertIn("segundo fallo", texto)
            self.assertLess(texto.index("primer fallo"), texto.index("segundo fallo"))

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
            self.assertEqual(texto.count("fallo de arranque"), 1)


if __name__ == "__main__":
    unittest.main()
