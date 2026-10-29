"""Un fallo de entrega de Meta afecta solo al último intento del paciente."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import whatsapp_service


class EntregaEstadoPacienteTests(unittest.TestCase):
    def _ejecutar(self, *, estado_meta="failed", ultimo=8, estado_paciente="enviado",
                  opt_out=0):
        cur = MagicMock()
        cur.fetchall.return_value = [{
            "id": 8, "paciente_id": 10, "numero_telefono": "+56912345678",
            "tabla_pacientes": "pacientes_prod", "tabla": "pacientes_prod",
            "estado_whatsapp": estado_meta,
        }]
        cur.fetchone.side_effect = [
            {"ultimo": ultimo},
            {"telefono": "+56912345678", "estado": estado_paciente,
             "whatsapp_opt_out": opt_out},
        ]
        with patch.object(whatsapp_service.servicio_areas, "tabla_valida", return_value=True), \
                patch.object(whatsapp_service.servicio_areas, "tabla_fisica_existe", return_value=True):
            whatsapp_service.WhatsAppService._actualizar_paciente_por_entrega(
                cur, "wamid-prueba", estado_meta, "", ())
        return cur

    def test_fallo_del_ultimo_mensaje_marca_error(self):
        cur = self._ejecutar()
        self.assertTrue(any(c.args[0] == "UPDATE pacientes_prod SET estado = %s WHERE id = %s"
                            and c.args[1] == ("error", 10) for c in cur.execute.call_args_list))

    def test_entrega_posterior_corrige_error(self):
        cur = self._ejecutar(estado_meta="delivered", estado_paciente="error")
        self.assertTrue(any(c.args[1] == ("enviado", 10)
                            for c in cur.execute.call_args_list if c.args[0].startswith("UPDATE pacientes_prod")))

    def test_no_sobrescribe_envio_mas_reciente(self):
        cur = self._ejecutar(ultimo=9)
        self.assertFalse(any(c.args[0].startswith("UPDATE pacientes_prod")
                             for c in cur.execute.call_args_list))

    def test_no_reactiva_baja(self):
        cur = self._ejecutar(opt_out=1)
        self.assertFalse(any(c.args[0].startswith("UPDATE pacientes_prod")
                             for c in cur.execute.call_args_list))

    def test_no_revierte_cambio_manual_a_pendiente(self):
        cur = self._ejecutar(estado_paciente="pendiente")
        self.assertFalse(any(c.args[0].startswith("UPDATE pacientes_prod")
                             for c in cur.execute.call_args_list))

    def test_webhook_fallido_actualiza_log_y_solicita_estado_paciente(self):
        conn = MagicMock()
        conn.__enter__.return_value = conn
        cur = conn.cursor.return_value
        cur.__enter__.return_value = cur
        servicio = whatsapp_service.WhatsAppService()
        with patch.object(whatsapp_service, "conectar", return_value=conn), \
                patch.object(whatsapp_service, "EMISOR_WEBHOOK") as emisor, \
                patch.object(servicio, "_actualizar_paciente_por_entrega") as paciente:
            emisor.get.return_value = None
            servicio._actualizar_estado("wamid-prueba", "failed", "131042")
        self.assertTrue(any("estado_envio = 'error'" in c.args[0]
                            and "estado_whatsapp = 'failed'" in c.args[0]
                            for c in cur.execute.call_args_list))
        self.assertEqual(paciente.call_count, 2)  # ramas históricas de desarrollo y producción

    def test_entrega_posterior_restaura_el_log(self):
        conn = MagicMock()
        conn.__enter__.return_value = conn
        cur = conn.cursor.return_value
        cur.__enter__.return_value = cur
        servicio = whatsapp_service.WhatsAppService()
        with patch.object(whatsapp_service, "conectar", return_value=conn), \
                patch.object(whatsapp_service, "EMISOR_WEBHOOK") as emisor, \
                patch.object(servicio, "_actualizar_paciente_por_entrega"):
            emisor.get.return_value = None
            servicio._actualizar_estado("wamid-prueba", "delivered")
        self.assertTrue(any("estado_envio = 'enviado', descripcion_error = NULL" in c.args[0]
                            for c in cur.execute.call_args_list))


if __name__ == "__main__":
    unittest.main()
