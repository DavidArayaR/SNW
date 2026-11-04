"""Prueba individual de reagendamiento, sin enviar a WhatsApp real."""
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import main


class PruebaReagendarTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cita = {'id': 8, 'fecha': '2026-11-04', 'ventana': 'AM',
                     'clinica_nombre': 'Clínica', 'centro_codigo': 'SMPT',
                     'paciente_nombre': 'Paciente Prueba', 'paciente_telefono_normalizado': '+56912345678',
                     'doctor_nombre': 'Dra. Soto', 'inicio_cita': '2026-11-04 09:00:00'}
        self.conn = MagicMock()
        self.conn.__enter__.return_value = self.conn
        self.cur = self.conn.cursor.return_value
        self.cur.__enter__.return_value = self.cur
        self.cur.fetchone.return_value = self.cita
        self.cur.lastrowid = 42
        self.cur.rowcount = 1
        main._pruebas_plantilla_en_curso.clear()
        main._pruebas_plantilla_ultima.clear()

    async def test_rechaza_numero_no_autorizado_antes_de_contactar_meta(self):
        with patch.object(main, 'conectar', return_value=self.conn), \
             patch.object(main, 'es_numero_prueba', return_value=False), \
             patch.object(main, 'WhatsAppService') as servicio:
            with self.assertRaises(HTTPException) as error:
                await main.enviar_prueba_reagendamiento(8, {'usuario': 'admin'})
        self.assertEqual(error.exception.status_code, 403)
        servicio.assert_not_called()
        self.assertFalse(any('INSERT' in llamada.args[0] for llamada in self.cur.execute.call_args_list))

    async def test_usa_clinica_y_datos_reales_y_registro_separado(self):
        plantilla = {'id': 11, 'clave': 'reagendar', 'centro_codigo': 'SMPT',
                     'texto': 'Hola {nombre_paciente} con {nombre_doc}',
                     'whatsapp_template_status': 'APPROVED', 'aprobacion_estado': 'aprobada'}
        emisor = {'codigo': 'SMPT', 'phoneNumberId': '123', 'phoneNumber': '+56911111111'}
        servicio = MagicMock()
        servicio.configurada.return_value = True
        servicio.enviar = AsyncMock(return_value=(True, 'wamid.test', None, 'sent'))
        with patch.object(main, 'conectar', return_value=self.conn), \
             patch.object(main, 'es_numero_prueba', return_value=True), \
             patch.object(main, 'esta_suprimido', return_value=False), \
             patch.object(main, 'esta_de_baja', return_value=False), \
             patch.object(main, 'config_all', return_value={'metodo_envio': 'api_oficial',
                'reagendar_plantillas_json': '{"SMPT":"reagendar"}'}), \
             patch.object(main, 'leer_plantillas', return_value=[plantilla]), \
             patch.object(main, '_exigir_centro_envio', return_value=emisor), \
             patch.object(main, 'WhatsAppService', return_value=servicio), \
             patch.object(main, 'auditoria_registrar'):
            resultado = await main.enviar_prueba_reagendamiento(8, {'usuario': 'admin'})
        self.assertTrue(resultado['ok'])
        argumentos = servicio.enviar.await_args
        self.assertEqual(argumentos.args[0], '+56912345678')
        self.assertEqual(argumentos.kwargs['variables']['nombre_doc'], 'Dra. Soto')
        self.assertTrue(argumentos.kwargs['es_prueba'])
        insercion = next(x for x in self.cur.execute.call_args_list if 'INSERT INTO paciente_reagendar' in x.args[0])
        self.assertIn("1,'PROCESANDO'", insercion.args[0])
        self.assertFalse(any('UPDATE paciente_reagendar' in x.args[0] and x.args[1][-1] == 8
                             for x in self.cur.execute.call_args_list if 'UPDATE paciente_reagendar' in x.args[0]))


class RutaPruebaReagendarTest(unittest.TestCase):
    def test_peticion_http_rechaza_numero_no_autorizado(self):
        from fastapi.testclient import TestClient
        conn = MagicMock()
        conn.__enter__.return_value = conn
        cur = conn.cursor.return_value
        cur.__enter__.return_value = cur
        cur.fetchone.return_value = {'paciente_telefono_normalizado': '+56912345678'}
        main.app.dependency_overrides[main.solo_admin] = lambda: {'usuario': 'admin'}
        try:
            with patch.object(main, 'conectar', return_value=conn), \
                 patch.object(main, 'es_numero_prueba', return_value=False):
                respuesta = TestClient(main.app).post('/api/v1/paciente-reagendar/8/enviar-prueba')
            self.assertEqual(respuesta.status_code, 403)
            self.assertIn('números de prueba', respuesta.json()['detail'])
        finally:
            main.app.dependency_overrides.pop(main.solo_admin, None)


if __name__ == '__main__':
    unittest.main()
