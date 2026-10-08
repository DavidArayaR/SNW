import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import main


class PacientesPruebaTests(unittest.TestCase):
    def setUp(self):
        self.admin = {"rol": "administrador", "usuario": "admin@example.com"}
        self.usuario = {"rol": "usuario", "usuario": "user@example.com"}

    def test_base_desarrollo_restringida_a_privilegiados(self):
        with self.assertRaises(HTTPException) as error:
            main._resolver_tabla_pacientes(self.usuario, "desarrollo", None)
        self.assertEqual(error.exception.status_code, 403)
        self.assertEqual(main._resolver_tabla_pacientes(self.admin, "desarrollo", None)[0],
                         "pacientes_dev")
        with self.assertRaises(HTTPException) as error:
            main.contar_destinatarios(main.DestinosIn(ambiente="desarrollo"), sesion=self.usuario)
        self.assertEqual(error.exception.status_code, 403)
        with self.assertRaises(HTTPException) as error:
            main.iniciar_envio(main.EnvioIn(ambiente="desarrollo", plantilla_id=1),
                               main.BackgroundTasks(), sesion=self.usuario)
        self.assertEqual(error.exception.status_code, 403)

    def test_config_no_entrega_lista_dev_a_roles_no_privilegiados(self):
        with patch.object(main, "leer_config", side_effect=lambda _: {
                "entorno": "desarrollo", "numeros_autorizados": ["+56912345678"]}):
            self.assertNotIn("numeros_autorizados",
                             main.obtener_configuracion(ambiente="desarrollo", sesion=self.usuario))
            self.assertEqual(main.obtener_configuracion(ambiente="desarrollo", sesion=self.admin)
                             ["numeros_autorizados"], ["+56912345678"])

    def test_alta_rechaza_numero_suprimido(self):
        body = main.PacientePruebaIn(nombre="Ana", apellido="Test", telefono="+56912345678")
        with patch.object(main, "esta_suprimido", return_value=True), \
                patch.object(main, "conectar") as conectar:
            with self.assertRaises(HTTPException) as error:
                main.crear_paciente_prueba(body, sesion=self.admin)
        self.assertEqual(error.exception.status_code, 409)
        conectar.assert_not_called()

    def test_alta_solo_en_dev_y_ata_acceso_al_creador(self):
        db = MagicMock()
        cursor = db.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = []
        cursor.lastrowid = 27
        body = main.PacientePruebaIn(nombre=" Ana ", apellido=" Test ", telefono="912345678")
        with patch.object(main, "esta_suprimido", return_value=False), \
                patch.object(main.servicio_areas, "usuario_id_por_correo", return_value=5), \
                patch.object(main, "conectar", return_value=db) as conectar, \
                patch.object(main, "auditoria_registrar") as auditoria, \
                patch.object(main, "_numeros_prueba_editables", return_value=set()):
            resultado = main.crear_paciente_prueba(body, sesion=self.admin)
        conectar.assert_called_once_with("desarrollo")
        consultas = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertTrue(any("INSERT INTO pacientes_dev" in sql for sql in consultas))
        self.assertTrue(any("INSERT INTO paciente_csv_accesos" in sql for sql in consultas))
        self.assertFalse(any("pacientes_prod" in sql for sql in consultas))
        self.assertEqual(resultado["id"], 27)
        self.assertEqual(resultado["telefono"], "+56912345678")
        auditoria.assert_called_once()

    def test_autorizacion_separa_alta_de_lista_de_prueba(self):
        db = MagicMock()
        cursor = db.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = {"telefono": "+56912345678"}
        with patch.object(main, "conectar", return_value=db), \
                patch.object(main, "esta_suprimido", return_value=False), \
                patch.object(main, "agregar_numero_prueba_dev", return_value=True) as agregar, \
                patch.object(main, "auditoria_registrar") as auditoria:
            resultado = main.autorizar_numero_paciente_prueba(27, sesion=self.admin)
        agregar.assert_called_once_with("+56912345678")
        self.assertTrue(resultado["agregado"])
        auditoria.assert_called_once()

    def test_programados_dev_no_visibles_para_otros_roles(self):
        fila = {"ambiente": "desarrollo", "area_id": None, "creador": "user@example.com"}
        self.assertFalse(main._prog_visible(self.usuario, fila))
        self.assertFalse(main._prog_visible({"rol": "supervisor"}, fila))
        self.assertTrue(main._prog_visible(self.admin, fila))

    def test_numeros_de_prueba_aparecen_antes_de_paginar(self):
        db = MagicMock()
        cursor = db.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = [
            {"id": 1, "nombre": "A", "apellido": "", "telefono": "+56911111111",
             "fecha_actualizacion": None, "ultima_respuesta_fecha": None},
            {"id": 2, "nombre": "B", "apellido": "", "telefono": "+56922222222",
             "fecha_actualizacion": None, "ultima_respuesta_fecha": None},
            {"id": 3, "nombre": "C", "apellido": "", "telefono": "+56933333333",
             "fecha_actualizacion": None, "ultima_respuesta_fecha": None},
            {"id": 4, "nombre": "D", "apellido": "", "telefono": "+56944444444",
             "fecha_actualizacion": None, "ultima_respuesta_fecha": None},
        ]
        with patch.object(main, "conectar", return_value=db), \
                patch.object(main, "expr_select_pacientes", return_value="p.*"), \
                patch.object(main, "from_pacientes", return_value=" FROM pacientes_dev p"), \
                patch.object(main, "_ids_csv_propios", return_value={1, 2, 3, 4}), \
                patch.object(main, "_numeros_prueba_editables",
                             return_value={"+56922222222", "+56944444444"}):
            filas = main.listar_pacientes(q=None, ambiente="desarrollo", area_id=None,
                                          sesion=self.admin)
        self.assertEqual([fila["id"] for fila in filas], [2, 4, 1, 3])
        self.assertEqual([fila["editable"] for fila in filas], [True, True, False, False])


if __name__ == "__main__":
    unittest.main()
