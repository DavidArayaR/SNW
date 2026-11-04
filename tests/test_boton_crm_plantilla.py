"""Botón de plantilla que abre el CRM de su propia clínica."""
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse, unquote
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import main
from plantilla_revision import presentar_plantilla, botones_para_meta


class BotonCrmPlantillaTest(unittest.TestCase):
    def resolver(self, boton, numero='+56912345678'):
        with patch.object(main, 'catalogo_centros', return_value={'SMPT': 'Clínica'}), \
             patch.object(main, 'config_get', return_value=numero):
            return main._resolver_botones_crm([boton], 'SMPT')[0]

    def test_mensaje_codificado_y_numero_de_clinica(self):
        boton = self.resolver({'tipo': 'crm', 'texto': 'Abrir chat',
                               'crm_mensaje': 'Hola, me interesa la promoción 💚'})
        url = urlparse(boton['url'])
        self.assertEqual(url.netloc, 'wa.me')
        self.assertEqual(url.path, '/56912345678')
        self.assertEqual(parse_qs(url.query)['text'], ['Hola, me interesa la promoción 💚'])
        vista = presentar_plantilla({'texto': 'Hola', 'botones': [boton,
              {'tipo': 'respuesta', 'texto': 'Dar de baja', 'accion': 'baja'}]})
        self.assertFalse(vista['errores'])
        self.assertEqual(vista['botones'][0]['crm_mensaje'], 'Hola, me interesa la promoción 💚')
        self.assertEqual(botones_para_meta(vista['botones'])[0]['url'], boton['url'])

    def test_orden_obligatorio(self):
        crm = self.resolver({'tipo': 'crm', 'texto': 'Me interesa', 'crm_mensaje': 'Hola'})
        no = {'tipo': 'respuesta', 'texto': 'No me interesa', 'accion': 'desinteres'}
        baja = {'tipo': 'respuesta', 'texto': 'Dar de baja', 'accion': 'baja'}
        main._validar_botones_obligatorios([crm, no, baja])
        for botones in ([no, crm, baja], [crm, baja], [crm, baja, no]):
            with self.assertRaises(HTTPException):
                main._validar_botones_obligatorios(botones)

    def test_botones_reagendamiento_conservan_mensaje_preparado(self):
        from paciente_reagendar import es_aceptacion_reagendar, mensaje_preparado_reagendar
        botones = [
            self.resolver({'tipo': 'reagendar_url', 'texto': 'Sí, quiero reagendar'}),
            {'tipo': 'respuesta', 'texto': 'No me interesa', 'accion': 'desinteres'},
            {'tipo': 'respuesta', 'texto': 'Dar de baja', 'accion': 'baja'},
        ]
        vista = presentar_plantilla({'texto': 'Hola {nombre_paciente} con {nombre_doc}', 'botones': botones})
        self.assertFalse(vista['errores'])
        main._validar_botones_obligatorios(vista['botones'], 'Hola {nombre_paciente}')
        self.assertTrue(es_aceptacion_reagendar(botones[0]['texto']))
        meta = botones_para_meta(vista['botones'])[0]
        self.assertEqual(meta['type'], 'URL')
        self.assertTrue(meta['url'].endswith('?text={{1}}'))
        self.assertTrue(meta['example'][0].startswith('https://wa.me/56912345678?text='))
        self.assertEqual(mensaje_preparado_reagendar({'paciente_nombre': 'Ana Pérez',
            'doctor_nombre': 'Dra. Soto', 'clinica_nombre': 'Clínica'}),
            'Sí me gustaría agendar, mi nombre es Ana Pérez, con el médico: Dra. Soto, clínica: Clínica')

    def test_reagendamiento_con_datos_no_acepta_boton_crm_fijo(self):
        crm = self.resolver({'tipo': 'crm', 'texto': 'Me interesa', 'crm_mensaje': 'Hola'})
        no = {'tipo': 'respuesta', 'texto': 'No me interesa', 'accion': 'desinteres'}
        baja = {'tipo': 'respuesta', 'texto': 'Dar de baja', 'accion': 'baja'}
        with self.assertRaises(HTTPException):
            main._validar_botones_obligatorios([crm, no, baja], 'Hola {nombre_paciente}')

    def test_rechaza_numero_invalido_y_variables(self):
        boton = {'tipo': 'crm', 'texto': 'Abrir', 'crm_mensaje': 'Hola'}
        with self.assertRaises(HTTPException):
            self.resolver(boton, '')
        with self.assertRaises(HTTPException):
            self.resolver({**boton, 'crm_mensaje': 'Hola {nombre}'})


class ReagendamientoUrlTest(unittest.IsolatedAsyncioTestCase):
    async def test_envio_incluye_mensaje_codificado_en_boton_url(self):
        from whatsapp_service import WhatsAppService, WhatsAppApiClient
        boton = {'tipo': 'url', 'texto': 'Sí, quiero reagendar',
                 'url': 'https://wa.me/56912345678?text={{1}}', 'crm_reagendar': True}
        plantilla = {'id': 7, 'texto': 'Hola {nombre_paciente} con {nombre_doc}',
                     'whatsapp_template': 'reagendar', 'whatsapp_template_lang': 'es',
                     'botones': [boton, {'tipo': 'respuesta', 'texto': 'No me interesa', 'accion': 'desinteres'},
                                 {'tipo': 'respuesta', 'texto': 'Dar de baja', 'accion': 'baja'}]}
        mensaje = 'Sí me gustaría agendar, mi nombre es Ana Pérez, con el médico: Dra. Soto, clínica: Clínica'
        variables = {'nombre_paciente': 'Ana Pérez', 'nombre_doc': 'Dra. Soto', 'crm_reagendar': mensaje}
        servicio = WhatsAppService()
        with patch('whatsapp_service.esta_suprimido', return_value=False), \
             patch.object(servicio, '_validar_canal_antes_envio', new_callable=AsyncMock, return_value=(True, None)), \
             patch.object(servicio, '_plantilla_aprobada_en_waba', new_callable=AsyncMock, return_value=True), \
             patch.object(WhatsAppApiClient, 'enviar', new_callable=AsyncMock,
                          return_value={'messages': [{'id': 'wamid.test'}]}) as enviar:
            ok, _, _, _ = await servicio.enviar('+56912345678', 'Hola', plantilla, variables=variables)
        self.assertTrue(ok)
        componentes = enviar.call_args.args[0]['template']['components']
        url_button = next(c for c in componentes if c.get('sub_type') == 'url')
        self.assertEqual(url_button['index'], '0')
        self.assertEqual(unquote(url_button['parameters'][0]['text']), mensaje)


if __name__ == '__main__':
    unittest.main()
