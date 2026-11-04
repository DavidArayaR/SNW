"""Mensajes preparados por plantilla en la pestaña Respuestas."""
import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import main


class MensajePlantillaTest(unittest.TestCase):
    def setUp(self):
        self.p = {'id': 7, 'clave': 'oferta', 'nombre': 'Oferta', 'texto': 'Hola',
                  'centro_codigo': 'SMPT', 'area_id': None, 'whatsapp_template_status': 'PENDING',
                  'ultima_edicion': 123, 'botones': [
                      {'tipo': 'respuesta', 'texto': 'Me interesa', 'accion': 'interes', 'crm_mensaje': 'Anterior'},
                      {'tipo': 'respuesta', 'texto': 'Dar de baja', 'accion': 'baja'}]}
        self.sesion = {'rol': 'administrador', 'usuario': 'admin'}

    def guardar(self, tipo='interes', mensaje='Nuevo', anterior='Anterior'):
        with patch.object(main, 'leer_plantillas', return_value=[self.p]), \
             patch.object(main, 'escribir_plantillas') as guardar, \
             patch.object(main, '_registrar_template_meta') as meta, \
             patch.object(main, 'auditoria_registrar'):
            resultado = main.guardar_mensaje_preparado_plantilla(7,
                main.MensajePreparadoPlantillaIn(tipo=tipo, mensaje=mensaje, anterior=anterior), self.sesion)
        meta.assert_not_called()
        guardar.assert_called_once()
        return resultado

    def test_interes_guarda_sin_revisar_meta(self):
        r = self.guardar()
        self.assertEqual(r['plantilla']['botones'][0]['crm_mensaje'], 'Nuevo')
        self.assertEqual(r['plantilla']['whatsapp_template_status'], 'PENDING')
        self.assertEqual(r['plantilla']['ultima_edicion'], 123)

    def test_conflicto_no_sobrescribe(self):
        with self.assertRaises(HTTPException) as e:
            self.guardar(anterior='desactualizado')
        self.assertEqual(e.exception.status_code, 409)

    def test_reagendar_exige_variables_y_usa_plantilla_y_clinica(self):
        self.p['botones'][0] = {'tipo': 'respuesta', 'texto': 'Sí, quiero reagendar', 'accion': 'reagendar'}
        mensaje = 'Soy {paciente_nombre}; médico {doctor_nombre}; clínica {clinica_nombre}'
        self.guardar(tipo='reagendar', mensaje=mensaje, anterior='')
        cita = {'plantilla_clave': 'oferta', 'centro_codigo': 'SMPT', 'paciente_nombre': 'Ana',
                'doctor_nombre': 'Soto', 'clinica_nombre': 'Clínica'}
        with patch.object(main, 'leer_plantillas', return_value=[self.p]):
            self.assertEqual(main.paciente_reagendar.mensaje_preparado_reagendar(cita),
                             'Soy Ana; médico Soto; clínica Clínica')
            self.assertEqual(main._mensaje_reagendar_de_plantilla({**cita, 'centro_codigo': 'VS'}), '')
        with self.assertRaises(HTTPException):
            self.guardar(tipo='reagendar', mensaje='Hola {paciente_nombre}', anterior=mensaje)

    def test_sin_permiso_de_autor_no_se_guarda(self):
        self.p['creado_por'] = 'otra@ejemplo.cl'
        with patch.object(main, 'leer_plantillas', return_value=[self.p]), \
             patch.object(main, 'escribir_plantillas') as escribir:
            with self.assertRaises(HTTPException) as e:
                main.guardar_mensaje_preparado_plantilla(7,
                    main.MensajePreparadoPlantillaIn(tipo='interes', mensaje='X', anterior='Anterior'),
                    {'rol': 'usuario', 'usuario': 'yo@ejemplo.cl'})
        self.assertEqual(e.exception.status_code, 403)
        escribir.assert_not_called()


if __name__ == '__main__': unittest.main()
