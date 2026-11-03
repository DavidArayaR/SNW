import datetime as dt
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import reservo_pacientes as rp


class TestNombreBaseReservo(unittest.TestCase):
    def test_genera_nombre_con_clinica_nombre_mes_y_ano_chilenos(self):
        ahora = dt.datetime(2026, 11, 3, 12, 0, tzinfo=ZoneInfo("America/Santiago"))
        resultado = rp.generar_nombre_base("SMPT", "  Pacientes Tratamiento  ", ahora)
        self.assertEqual(resultado["nombre"], "SMPT_pacientes_tratamiento_noviembre_2026")
        self.assertEqual(resultado["nombre_personalizado"], "pacientes_tratamiento")

    def test_quita_prefijo_clinica_y_sufijo_mes_ano_duplicados(self):
        ahora = dt.datetime(2026, 11, 3, 12, 0, tzinfo=ZoneInfo("America/Santiago"))
        resultado = rp.generar_nombre_base(
            "MS", "MS_pacientes_psiquiatria_noviembre_2026", ahora)
        self.assertEqual(resultado["nombre"], "MS_pacientes_psiquiatria_noviembre_2026")

    def test_normaliza_tildes_y_separadores(self):
        ahora = dt.datetime(2026, 3, 1, tzinfo=ZoneInfo("America/Santiago"))
        resultado = rp.generar_nombre_base("MVYS", "Control Salud Mental", ahora)
        self.assertEqual(resultado["nombre"], "MVYS_control_salud_mental_marzo_2026")

    def test_limita_nombre_personalizado_a_longitud_de_columna(self):
        ahora = dt.datetime(2026, 11, 3, tzinfo=ZoneInfo("America/Santiago"))
        resultado = rp.generar_nombre_base("SMPT", "paciente " * 30, ahora)
        self.assertLessEqual(len(resultado["nombre_personalizado"]), 100)
        self.assertLessEqual(len(resultado["nombre"]), 150)

    def test_rechaza_clinica_o_nombre_vacio(self):
        with self.assertRaises(ValueError):
            rp.generar_nombre_base("NO_EXISTE", "pacientes")
        with self.assertRaises(ValueError):
            rp.generar_nombre_base("SMPT", "   ")


class TestListaPacientesReservo(unittest.TestCase):
    def test_base_guarda_solo_telefonos_chilenos_validos_y_normalizados(self):
        pacientes = [
            {"id": 1, "cliente_uuid": "uuid-1", "paciente_nombre": "Ana",
             "paciente_telefono": "+56912345678"},
            {"id": 2, "cliente_uuid": "uuid-2", "paciente_nombre": "Luis",
             "paciente_telefono": "912345679"},
            {"id": 3, "cliente_uuid": "uuid-3", "paciente_nombre": "Sin teléfono",
             "paciente_telefono": ""},
            {"id": 4, "cliente_uuid": "uuid-4", "paciente_nombre": "Teléfono inválido",
             "paciente_telefono": "+34912345678"},
        ]
        valores = rp._valores_pacientes_lista(7, pacientes)
        self.assertEqual([valor[1] for valor in valores], [1, 2])
        self.assertEqual([valor[5] for valor in valores], ["+56912345678", "+56912345679"])
        self.assertTrue(all(valor[6] == 1 for valor in valores))

    def test_lista_pacientes_pagina_busca_y_devuelve_resumen(self):
        cursor = MagicMock()
        cursor.fetchone.side_effect = [
            {"id": 8, "nombre": "SMPT_pacientes_noviembre_2026"},
            {"total": 1},
            {"total": 12, "telefonos_invalidos": 2, "enviados": 4,
             "errores": 1, "bajas": 1, "interesados": 3, "no_interesados": 1},
        ]
        cursor.fetchall.return_value = [{
            "id": 21, "cliente_uuid": "uuid-21", "nombre": "Ana", "apellido": "Pérez",
            "telefono": "+56912345678", "telefono_valido": 1, "estado": "pendiente",
            "interesado": 0, "no_interesado": 0, "respuesta_manual": None,
        }]
        cursor_ctx = MagicMock()
        cursor_ctx.__enter__.return_value = cursor
        cursor_ctx.__exit__.return_value = False
        conexion_ctx = MagicMock()
        conexion_ctx.__enter__.return_value = conexion_ctx
        conexion_ctx.cursor.return_value = cursor_ctx
        conexion_ctx.__exit__.return_value = False
        with patch.object(rp, "_db", return_value=conexion_ctx):
            resultado = rp.listar_pacientes_base_contacto(8, 2, 25, "  Ana  ")
        self.assertEqual(resultado["total"], 1)
        self.assertEqual(resultado["pagina"], 2)
        self.assertEqual(resultado["resumen"]["enviados"], 4)
        self.assertEqual(resultado["items"][0]["respuesta"], "pendiente")
        self.assertIn("cliente_uuid", resultado["items"][0])
        self.assertEqual(cursor.execute.call_args_list[1].args[1], [8, "%ana%"])
        self.assertEqual(cursor.execute.call_args_list[3].args[1], (8, "%ana%", 25, 25))

    def test_lista_reservo_incluye_autorizacion_heredada_y_migracion(self):
        cursor = MagicMock()
        rp.configurar_tablas(cursor)
        sqls = [llamada.args[0] for llamada in cursor.execute.call_args_list]
        sql_schema = "\n".join(sqls)
        self.assertIn("consentimiento_verificado TINYINT(1) NOT NULL DEFAULT 1", sql_schema)
        self.assertIn("UPDATE reservo_lista_pacientes SET consentimiento_verificado=1", sql_schema)


if __name__ == "__main__":
    unittest.main()
