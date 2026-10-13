"""Migración de credenciales antiguas a valores de `configuracion` legibles."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import db


class ConfigLegacyTests(unittest.TestCase):
    def _connection(self, rows):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        cursor = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        cursor.fetchall.return_value = rows
        return connection, cursor

    def test_migra_credenciales_recuperables_sin_registrarlas(self):
        connection, cursor = self._connection([
            {"clave": "wa_token", "valor": "dpapi:cifrado"},
            {"clave": "gemini_api_key", "valor": "dpapi:cifrado"},
        ])
        with patch.object(db, "conectar", return_value=connection), \
             patch.object(db, "leer_credencial_antigua", return_value="token-recuperado"):
            values, ok = db._cargar_config()
        self.assertTrue(ok)
        self.assertEqual(values["wa_token"], "token-recuperado")
        self.assertEqual(values["gemini_api_key"], "token-recuperado")
        updates = [call for call in cursor.execute.call_args_list if "UPDATE configuracion" in call.args[0]]
        self.assertEqual(len(updates), 2)
        self.assertTrue(all(call.args[1][0] == "token-recuperado" for call in updates))
        connection.commit.assert_called_once()

    def test_no_usa_credencial_cifrada_si_no_puede_migrarla(self):
        connection, cursor = self._connection([{"clave": "wa_token", "valor": "dpapi:cifrado"}])
        with patch.object(db, "conectar", return_value=connection), \
             patch.object(db, "leer_credencial_antigua", side_effect=OSError("sin clave")), \
             patch.object(db, "log_error") as log:
            values, ok = db._cargar_config()
        self.assertTrue(ok)
        self.assertEqual(values["wa_token"], "")
        self.assertNotIn("token-recuperado", str(log.call_args))
        self.assertFalse(any("UPDATE configuracion" in call.args[0] for call in cursor.execute.call_args_list))
        connection.commit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
