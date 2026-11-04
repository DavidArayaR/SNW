"""La respuesta a una prueba se asocia a su clínica y plantilla sin crear campaña."""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import main

TEL = '+56912345678'
EMISOR = {'codigo': 'SMPT', 'phoneNumberId': '123', 'phoneNumber': '+56911111111'}


class RespuestaInteresPruebaTest(unittest.TestCase):
    def tearDown(self):
        main._cc_auto_pendientes.clear()

    def test_prueba_contextual_envia_mensaje_de_su_plantilla(self):
        contexto = {'plantilla_id': 7, 'centro_codigo': 'SMPT', 'ambiente': 'desarrollo',
                    'nombre': 'Ana', 'apellido': 'Pérez'}
        plantilla = {'id': 7, 'centro_codigo': 'SMPT', 'botones': [
            {'accion': 'interes', 'crm_mensaje': 'Quiero la oferta A'}]}
        with patch.object(main, 'esta_suprimido', return_value=False), \
             patch.object(main, 'es_numero_prueba', return_value=True), \
             patch.object(main, 'emisor_webhook_actual', return_value=EMISOR), \
             patch.object(main, '_contexto_prueba_interes', return_value=contexto) as buscar, \
             patch.object(main, 'leer_plantillas', return_value=[plantilla]), \
             patch.object(main, '_despachar_call_center', return_value={'ok': True}) as enviar:
            main._enviar_call_center_auto(TEL, 'wamid.respuesta', 'wamid.prueba')
        buscar.assert_called_once_with(TEL, EMISOR, 'wamid.prueba')
        self.assertEqual(enviar.call_args.kwargs['mensaje_prueba'], 'Quiero la oferta A')
        self.assertEqual(enviar.call_args.kwargs['emisor_forzado'], EMISOR)
        self.assertEqual(enviar.call_args.args[0]['telefono'], TEL)

    def test_prueba_de_otra_clinica_no_se_atribuye(self):
        contexto = {'plantilla_id': 7, 'centro_codigo': 'SMPT', 'ambiente': 'desarrollo'}
        with patch.object(main, 'esta_suprimido', return_value=False), \
             patch.object(main, 'es_numero_prueba', return_value=True), \
             patch.object(main, 'emisor_webhook_actual', return_value=EMISOR), \
             patch.object(main, '_contexto_prueba_interes', return_value=contexto), \
             patch.object(main, 'leer_plantillas', return_value=[{'id': 7, 'centro_codigo': 'VS'}]), \
             patch.object(main, '_despachar_call_center') as enviar, \
             patch.object(main, 'log_error'):
            main._enviar_call_center_auto(TEL, 'wamid.respuesta', 'wamid.prueba')
        enviar.assert_not_called()

    def test_contexto_filtra_telefono_emisor_clinica_y_referencia(self):
        conn = MagicMock()
        conn.__enter__.return_value = conn
        cur = conn.cursor.return_value
        cur.__enter__.return_value = cur
        cur.fetchone.return_value = None
        with patch.object(main, 'conectar', return_value=conn):
            main._contexto_prueba_interes(TEL, EMISOR, 'wamid.prueba')
        sql, args = cur.execute.call_args.args
        self.assertIn('INTERVAL 24 HOUR', sql)
        self.assertEqual(args, (TEL, '123', 'SMPT', 'wamid.prueba'))


if __name__ == '__main__': unittest.main()
