"""El mensaje preparado es local y no reinicia la revisión de Meta."""
import copy
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import main


class MensajePreparadoLocalTest(unittest.TestCase):
    def setUp(self):
        self.botones = [
            {'tipo': 'respuesta', 'texto': 'Me interesa', 'accion': 'interes', 'crm_mensaje': 'Anterior'},
            {'tipo': 'respuesta', 'texto': 'No me interesa', 'accion': 'desinteres'},
            {'tipo': 'respuesta', 'texto': 'Dar de baja', 'accion': 'baja'},
        ]
        self.plantilla = {'id': 7, 'clave': 'oferta', 'nombre': 'Oferta', 'texto': 'Hola',
                          'centro_codigo': 'SMPT', 'area_id': None, 'botones': self.botones,
                          'whatsapp_template_lang': 'es', 'whatsapp_template_categoria': 'MARKETING',
                          'whatsapp_template_status': 'APPROVED', 'aprobacion_estado': 'aprobada',
                          'ultima_edicion': int(time.time() * 1000)}
        self.body = {'nombre': 'Oferta', 'texto': 'Hola', 'centro': 'SMPT', 'area_id': None,
                     'whatsapp_template_lang': 'es', 'whatsapp_template_categoria': 'MARKETING',
                     'botones': [{'tipo': 'crm', 'texto': 'Me interesa', 'crm_mensaje': 'Nuevo texto'},
                                 *copy.deepcopy(self.botones[1:])]}

    def actualizar(self):
        with patch.object(main, 'leer_plantillas', return_value=[self.plantilla]), \
             patch.object(main, 'escribir_plantillas') as guardar, \
             patch.object(main, '_registrar_template_meta') as meta:
            resultado = main.actualizar_plantilla(7, main.PlantillaIn(**self.body),
                                                  sesion={'rol': 'administrador', 'usuario': 'admin'})
            return resultado, guardar, meta

    def test_guarda_sin_meta_durante_cooldown(self):
        ultima = self.plantilla['ultima_edicion']
        resultado, guardar, meta = self.actualizar()
        self.assertEqual(resultado['botones'][0]['crm_mensaje'], 'Nuevo texto')
        self.assertEqual(resultado['ultima_edicion'], ultima)
        self.assertEqual(resultado['whatsapp_template_status'], 'APPROVED')
        guardar.assert_called_once()
        meta.assert_not_called()

    def test_guarda_si_meta_aun_revisa(self):
        self.plantilla['whatsapp_template_status'] = 'PENDING'
        resultado, _, meta = self.actualizar()
        self.assertEqual(resultado['whatsapp_template_status'], 'PENDING')
        meta.assert_not_called()

    def test_con_cambio_de_texto_no_elude_restricciones(self):
        self.body['texto'] = 'Contenido distinto'
        with self.assertRaises(HTTPException):
            self.actualizar()

    def test_no_admite_variables_en_mensaje_local(self):
        self.body['botones'][0]['crm_mensaje'] = 'Hola {nombre}'
        with self.assertRaises(HTTPException):
            self.actualizar()


if __name__ == '__main__':
    unittest.main()
