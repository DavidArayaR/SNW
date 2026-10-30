"""Regresiones de asociación de respuestas a campañas de Centro Mentalidad Saludable."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from whatsapp_channels import EMISOR_WEBHOOK
from whatsapp_service import WhatsAppService


class RespuestaCentroMSTests(unittest.TestCase):
    def setUp(self):
        self.servicio = WhatsAppService()
        self.emisor = {"codigo": "MS", "phoneNumberId": "1234500002",
                       "phoneNumber": "+56911111111"}
        self.pacientes = [
            {"id": 12, "nombre": "Paciente A", "telefono": "56912345678",
             "tabla": "pacientes_prod"},
            {"id": 34, "nombre": "Paciente B", "telefono": "+56912345678",
             "tabla": "pacientes_ms"},
        ]

    def _registrar(self, origenes, referencia=None):
        conexion = MagicMock()
        conexion.__enter__.return_value = conexion
        cursor = conexion.cursor.return_value
        cursor.__enter__.return_value = cursor
        cursor.fetchone.side_effect = origenes
        cursor.lastrowid = 91
        token = EMISOR_WEBHOOK.set(self.emisor)
        try:
            with patch("whatsapp_service.conectar", return_value=conexion), \
                 patch.object(self.servicio, "_pacientes_con_tel", return_value=self.pacientes), \
                 patch("whatsapp_service.servicio_areas.area_por_tabla", return_value=None), \
                 patch("whatsapp_service.log_error"):
                resultado = self.servicio._registrar_respuesta(
                    "+56912345678", "Me interesa", "2026-10-30",
                    mensaje_id="wamid.respuesta", referencia_meta_id=referencia)
        finally:
            EMISOR_WEBHOOK.reset(token)
        return resultado, cursor

    def test_clic_ms_usa_contexto_y_asocia_solo_su_paciente(self):
        resultado, cursor = self._registrar(
            [{"tabla_pacientes": "pacientes_ms", "paciente_id": 34}],
            referencia="wamid.campania.ms")
        self.assertEqual(resultado, "registrada")
        consultas = cursor.execute.call_args_list
        sql_origen, args_origen = consultas[0].args
        self.assertIn("BINARY le.whatsapp_message_id = BINARY %s", sql_origen)
        self.assertIn("REPLACE", sql_origen)
        self.assertIn("e.tabla_pacientes", sql_origen)
        self.assertEqual(args_origen[-1], "wamid.campania.ms")
        registros = [llamada.args[1] for llamada in consultas
                     if llamada.args[0].startswith("INSERT INTO log_envios")]
        self.assertEqual(len(registros), 1)
        self.assertEqual(registros[0][0], 34)
        self.assertEqual(registros[0][7], "pacientes_ms")

    def test_sin_contexto_busca_ultima_campania_del_emisor(self):
        resultado, cursor = self._registrar(
            [{"tabla_pacientes": "pacientes_ms", "paciente_id": 34}])
        self.assertEqual(resultado, "registrada")
        self.assertIn("le.envio_id IS NOT NULL", cursor.execute.call_args_list[0].args[0])

    def test_no_atribuye_clic_a_otra_base_si_falta_origen(self):
        resultado, cursor = self._registrar([{}, {}], referencia="wamid.desconocido")
        self.assertEqual(resultado, "sin_base_inequivoca")
        self.assertFalse(any(llamada.args[0].startswith("INSERT INTO log_envios")
                             for llamada in cursor.execute.call_args_list))

    def test_webhook_entrega_referencia_del_boton_al_registro(self):
        mensaje = {"id": "wamid.respuesta", "from": "56912345678", "type": "button",
                   "timestamp": "1793300000", "context": {"id": "wamid.campania.ms"},
                   "button": {"text": "Me interesa", "payload": "snw_tpl_interes"}}
        with patch("whatsapp_service.esta_suprimido", return_value=False), \
             patch("whatsapp_service.es_exento", return_value=False), \
             patch("whatsapp_service.es_numero_prueba", return_value=False), \
             patch.object(self.servicio, "_guardar_mensaje_entrante", return_value=True), \
             patch.object(self.servicio, "_guardar_corpus_respuesta"), \
             patch.object(self.servicio, "_estaba_opt_out", return_value=False), \
             patch.object(self.servicio, "_registrar_respuesta", return_value="registrada") as guardar, \
             patch.object(self.servicio, "_registrar_interes"), \
             patch("whatsapp_service.al_detectar_interes", return_value=None):
            acciones = self.servicio._procesar_mensajes({"messages": [mensaje]})
        self.assertIn("interes_boton", acciones)
        self.assertEqual(guardar.call_args.kwargs["referencia_meta_id"], "wamid.campania.ms")


if __name__ == "__main__":
    unittest.main()
