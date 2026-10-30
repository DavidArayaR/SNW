"""Detalle y conversación de reagendamientos sin paciente_id de bases normales."""
import datetime as dt
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import main
import paciente_reagendar as reagendar

DEV = {"rol": "desarrollador", "usuario": "dev"}
FECHA = dt.datetime(2026, 10, 30, 14, 0)


class HistorialReagendarTests(unittest.TestCase):
    def conexion(self):
        conn = MagicMock()
        cur = conn.__enter__.return_value.cursor.return_value.__enter__.return_value
        return conn, cur

    def fila(self):
        return {"id": 9, "nombre_paciente": "Persona Prueba", "numero_telefono": "+56911111111",
                "fecha_hora": FECHA, "estado_envio": "enviado", "estado_whatsapp": "read",
                "mensaje_enviado": "Plantilla de reagendamiento", "descripcion_error": None,
                "mensaje_respuesta": "Sí quiero reagendar", "ultima_accion": "REAGENDAR",
                "whatsapp_opt_out": 0}

    def test_detalle_muestra_respuesta_independiente_de_entrega_leida(self):
        conn, cur = self.conexion()
        cur.fetchall.return_value = [self.fila()]
        with patch.object(main, "conectar", return_value=conn):
            fila = main._detalle_historial_reagendar(7, DEV)[0]
        self.assertIsNone(fila["paciente_id"])
        self.assertTrue(fila["reagendamiento"])
        self.assertEqual(fila["estado_whatsapp"], "read")
        self.assertEqual(fila["respuesta"], "respondio")
        self.assertTrue(fila["interesado"])
        self.assertTrue(fila["datos_completos"])

    def test_ruta_de_detalle_elige_tabla_especial(self):
        conn, cur = self.conexion()
        cur.fetchone.return_value = {"base_datos": "paciente_reagendar", "area_id": None}
        with patch.object(main, "conectar", return_value=conn), \
             patch.object(main, "columnas_tabla", return_value={"base_datos", "area_id"}), \
             patch.object(main, "_detalle_historial_reagendar", return_value=[{"id": 9}]) as detalle:
            self.assertEqual(main.detalle_historial("7", ambiente="produccion", sesion=DEV), [{"id": 9}])
        detalle.assert_called_once_with(7, DEV)

    def test_desinteres_no_se_confunde_con_interes(self):
        conn, cur = self.conexion()
        cur.fetchall.return_value = [{**self.fila(), "mensaje_respuesta": "No me interesa", "ultima_accion": "DESINTERES"}]
        with patch.object(main, "conectar", return_value=conn):
            fila = main._detalle_historial_reagendar(7, DEV)[0]
        self.assertEqual(fila["respuesta"], "no_interesado")
        self.assertFalse(fila["interesado"])

    def test_conversacion_recupera_entrantes_sin_ficha_de_paciente(self):
        conn, cur = self.conexion()
        cur.fetchone.return_value = {"id": 9, "wa_phone_id": "12345", "numero_telefono": "+56911111111",
                                    "nombre_paciente": "Persona Prueba", "fecha_hora": FECHA}
        cur.fetchall.side_effect = [
            [{"mensaje_id": "wamid.in", "texto": "Sí quiero reagendar", "tipo": "text",
              "recibido": FECHA + dt.timedelta(minutes=1)}],
            [{"id": 9, "whatsapp_message_id": "wamid.out", "mensaje": "Plantilla",
              "plantilla_clave": "reagendar_vs", "estado_envio": "enviado", "fecha_hora": FECHA}]]
        with patch.object(main, "conectar", return_value=conn):
            datos = main.mensajes_historial_reagendar(7, 9, sesion=DEV)
        self.assertEqual([m["direccion"] for m in datos["mensajes"]], ["saliente", "entrante"])
        self.assertEqual(datos["paciente"]["respuesta"], "respondio")
        self.assertIsNone(datos["paciente"]["id"])
        self.assertEqual(cur.execute.call_args_list[1].args[1], ("12345", "+56911111111", FECHA))
        self.assertFalse(datos["mensajes"][1]["etiquetas_editables"])

    def test_no_expone_conversacion_de_otro_envio_o_sin_permiso(self):
        conn, cur = self.conexion()
        cur.fetchone.return_value = None
        with patch.object(main, "conectar", return_value=conn), self.assertRaises(main.HTTPException) as error:
            main.mensajes_historial_reagendar(8, 9, sesion=DEV)
        self.assertEqual(error.exception.status_code, 404)
        with patch.object(main, "conectar") as conexion, self.assertRaises(main.HTTPException) as error:
            main.mensajes_historial_reagendar(7, 9, sesion={"rol": "usuario", "permisos": ["historial", "pacientes_ver_datos"]})
        self.assertEqual(error.exception.status_code, 403)
        conexion.assert_not_called()

    def test_respuesta_recibida_actualiza_el_registro_original(self):
        conn, cur = self.conexion()
        cur.rowcount = 1
        cur.fetchone.side_effect = [
            {"id": 12, "log_envio_id": 9, "paciente_nombre": "Persona Prueba",
             "doctor_nombre": "Doctora", "clinica_nombre": "Clínica"}, None, None]
        with patch.object(reagendar, "conectar", return_value=conn):
            evento = reagendar.registrar_respuesta_reagendar(
                "+56911111111", "12345", "wamid.in", "wamid.out", "Sí quiero reagendar")
        self.assertEqual(evento["accion"], "REAGENDAR")
        actualizacion = next(c for c in cur.execute.call_args_list
                             if c.args[0].startswith("UPDATE log_envios SET respuesta"))
        self.assertEqual(actualizacion.args[1], ("respondio", 9, "12345", "+56911111111"))


if __name__ == "__main__":
    unittest.main()
