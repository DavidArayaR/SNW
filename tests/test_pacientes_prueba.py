import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import main
import db


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
                patch.object(main, "agregar_numero_prueba_ambos",
                             return_value={"desarrollo": True, "produccion": True}) as agregar, \
                patch.object(main, "auditoria_registrar") as auditoria:
            resultado = main.autorizar_numero_paciente_prueba(27, sesion=self.admin)
        agregar.assert_called_once_with("+56912345678")
        self.assertTrue(resultado["agregado"])
        self.assertEqual(resultado["agregado_en"], {"desarrollo": True, "produccion": True})
        auditoria.assert_called_once()

    def test_autorizacion_completa_produccion_si_ya_estaba_en_desarrollo(self):
        conexion = MagicMock()
        cursor = conexion.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchone.side_effect = [
            {"valor": "+56912345678"}, {"valor": "+56999998888"},
        ]
        with patch.object(db, "conectar", return_value=conexion):
            agregados = db.agregar_numero_prueba_ambos("+56912345678")
        self.assertEqual(agregados, {"desarrollo": False, "produccion": True})
        actualizaciones = [call.args for call in cursor.execute.call_args_list
                          if call.args[0].startswith("UPDATE configuracion SET valor")]
        self.assertEqual(len(actualizaciones), 1)
        self.assertEqual(actualizaciones[0][1][1], "numeros_prueba_prod")
        self.assertIn("+56912345678", actualizaciones[0][1][0])
        conexion.__enter__.return_value.commit.assert_called_once()

    def test_autorizacion_nueva_actualiza_ambas_listas_en_una_transaccion(self):
        conexion = MagicMock()
        cursor = conexion.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchone.side_effect = [{"valor": ""}, {"valor": ""}]
        with patch.object(db, "conectar", return_value=conexion):
            agregados = db.agregar_numero_prueba_ambos("912345678")
        self.assertEqual(agregados, {"desarrollo": True, "produccion": True})
        actualizaciones = [call.args[1] for call in cursor.execute.call_args_list
                          if call.args[0].startswith("UPDATE configuracion SET valor")]
        self.assertEqual(actualizaciones, [
            ("+56912345678", "numeros_prueba_dev"),
            ("+56912345678", "numeros_prueba_prod"),
        ])
        conexion.__enter__.return_value.commit.assert_called_once()

    def test_migracion_inicial_copia_solo_numeros_de_dev_que_faltan(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = {"valor": "0"}
        cursor.fetchall.return_value = [
            {"clave": "numeros_prueba_dev", "valor": "912345678, +56999998888"},
            {"clave": "numeros_prueba_prod", "valor": "+56999998888"},
        ]
        db._migrar_numeros_prueba_a_produccion(cursor)
        actualizaciones = [call.args for call in cursor.execute.call_args_list
                          if call.args[0].startswith("UPDATE configuracion")]
        self.assertEqual(actualizaciones[0][1], (
            "+56999998888,+56912345678",))
        self.assertIn("numeros_prueba_prod_sync_v1", actualizaciones[1][1])

    def test_migracion_no_repone_numeros_retirados_despues(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = {"valor": "1"}
        db._migrar_numeros_prueba_a_produccion(cursor)
        cursor.fetchall.assert_not_called()
        self.assertFalse(any(call.args[0].startswith("UPDATE configuracion")
                             for call in cursor.execute.call_args_list))

    def test_programados_dev_no_visibles_para_otros_roles(self):
        fila = {"ambiente": "desarrollo", "area_id": None, "creador": "user@example.com"}
        self.assertFalse(main._prog_visible(self.usuario, fila))
        self.assertFalse(main._prog_visible({"rol": "supervisor"}, fila))
        self.assertTrue(main._prog_visible(self.admin, fila))

    def test_numeros_de_prueba_aparecen_antes_de_paginar_con_fetchall_tupla(self):
        db = MagicMock()
        cursor = db.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = (
            {"id": 1, "nombre": "A", "apellido": "", "telefono": "+56911111111",
             "fecha_actualizacion": None, "ultima_respuesta_fecha": None},
            {"id": 2, "nombre": "B", "apellido": "", "telefono": "+56922222222",
             "fecha_actualizacion": None, "ultima_respuesta_fecha": None},
            {"id": 3, "nombre": "C", "apellido": "", "telefono": "+56933333333",
             "fecha_actualizacion": None, "ultima_respuesta_fecha": None},
            {"id": 4, "nombre": "D", "apellido": "", "telefono": "+56944444444",
             "fecha_actualizacion": None, "ultima_respuesta_fecha": None},
        )
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
