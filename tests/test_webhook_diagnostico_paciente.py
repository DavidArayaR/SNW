"""Diagnóstico de respuestas sin paciente asociado."""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from whatsapp_channels import EMISOR_WEBHOOK
from whatsapp_service import WhatsAppService


class DiagnosticoRespuestaTest(unittest.TestCase):
    def setUp(self):
        self.servicio = WhatsAppService()
        self.conn = MagicMock()
        self.conn.__enter__.return_value = self.conn
        self.cur = self.conn.cursor.return_value
        self.cur.__enter__.return_value = self.cur
        self.token = EMISOR_WEBHOOK.set({'codigo': 'SMPT', 'phoneNumberId': '123'})
        self.addCleanup(EMISOR_WEBHOOK.reset, self.token)

    def test_reagendamiento_gestionado_no_reporta_falso_error(self):
        with patch('whatsapp_service.conectar', return_value=self.conn), \
             patch.object(self.servicio, '_pacientes_con_tel', return_value=[]), \
             patch('whatsapp_service.log_error') as log:
            resultado = self.servicio._registrar_respuesta('+56912345678', 'Sí', '1',
                        referencia_meta_id='wamid.test', gestionada_reagendar=True)
        self.assertEqual(resultado, 'sin_cliente')
        log.assert_not_called()

    def test_contexto_sin_paciente_en_campania_se_registra(self):
        with patch('whatsapp_service.conectar', return_value=self.conn), \
             patch.object(self.servicio, '_pacientes_con_tel', return_value=[]), \
             patch('whatsapp_service.log_error') as log:
            self.servicio._registrar_respuesta('+56912345678', 'Sí', '1', referencia_meta_id='wamid.test')
        self.assertIn('sin paciente en bases', log.call_args.args[0])

    def test_error_de_consulta_no_se_confunde_con_paciente_ausente(self):
        self.cur.execute.side_effect = RuntimeError('error de prueba')
        with patch.object(self.servicio, '_tablas_pac', return_value=('pacientes_prod',)), \
             patch('whatsapp_service.log_error') as log:
            with self.assertRaises(RuntimeError):
                self.servicio._pacientes_con_tel(self.cur, '+56912345678')
        self.assertIn('error al consultar base de pacientes', log.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
