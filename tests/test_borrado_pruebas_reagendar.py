"""Borrado manual y acotado de citas de números de prueba."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import main
import paciente_reagendar as reagendar


class BorradoPruebasTests(unittest.TestCase):
    def test_identifica_solo_numeros_de_prueba_configurados(self):
        cfg = {"numeros_prueba_dev": "+56 9 1111 1111, +56 9 1111 1111",
               "numeros_prueba_prod": "+56 9 2222 2222"}
        with patch.object(reagendar, "config_all", return_value=cfg):
            numeros = reagendar._telefonos_prueba_configurados()
        self.assertEqual(len(numeros), 2)
        self.assertTrue(all(numero.startswith("+569") for numero in numeros))

    def test_selecciona_numeros_de_prueba_sin_restriccion_de_estado_o_tiempo(self):
        condicion, args = reagendar._condicion_borrado_pruebas(("+56911111111",))
        self.assertEqual(args, ("+56911111111",))
        self.assertEqual(condicion, "r.paciente_telefono_normalizado IN (%s)")
        self.assertEqual(reagendar._condicion_borrado_pruebas(()), ("1=0", ()))

    def test_borra_solo_ids_revalidados_y_conserva_historial(self):
        conexion = MagicMock()
        cur = conexion.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value = [{"id": 3, "lote_id": 7}, {"id": 5, "lote_id": 7}]
        with patch.object(reagendar, "_telefonos_prueba_configurados",
                          return_value=("+56911111111",)), \
             patch.object(reagendar, "conectar", return_value=conexion):
            resultado = reagendar.eliminar_pruebas()
        self.assertEqual(resultado, {"pacientes_eliminados": 2})
        sentencias = [llamada.args[0] for llamada in cur.execute.call_args_list]
        self.assertIn("FOR UPDATE", sentencias[0])
        self.assertIn("DELETE FROM reagendar_destinatarios", sentencias[1])
        self.assertIn("DELETE FROM paciente_reagendar", sentencias[2])
        self.assertFalse(any("DELETE FROM log_envios" in sql for sql in sentencias))
        conexion.__enter__.return_value.commit.assert_called_once()

    def test_ruta_no_borra_sin_solicitud_explicita(self):
        with patch.object(reagendar, "eliminar_pruebas",
                          return_value={"pacientes_eliminados": 1}) as borrar, \
             patch.object(main, "auditoria_registrar") as auditar:
            resultado = main.eliminar_pruebas_reagendar(sesion={"usuario": "dev"})
        self.assertEqual(resultado["pacientes_eliminados"], 1)
        borrar.assert_called_once()
        auditar.assert_called_once()


if __name__ == "__main__":
    unittest.main()
