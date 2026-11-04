"""Configuración y selección de respuestas automáticas por clínica."""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import main


class RespuestasSistemaTests(unittest.TestCase):
    def test_patrones_guardan_tildes_y_se_comparan_sin_perder_tolerancia(self):
        from intenciones_call_center import clasificar, patrones_configurados

        clave = "call_center_patrones_consulta"
        catalogo = {"CONSULTA_HORAS": ["¿Hay atención el próximo miércoles?", "Hora para mañana"]}
        with patch.object(main, "catalogo_centros", return_value={}), \
             patch.object(main, "config_set") as guardar, \
             patch.object(main, "_config_valores", return_value={}), \
             patch.object(main, "listar_canales", return_value=[]), \
             patch.object(main, "config_get", return_value=""):
            main.actualizar_configuracion_completa(
                main.ConfigTodoIn(cambios={clave: json.dumps(catalogo, ensure_ascii=False)}),
                sesion={"usuario": "dev"})
        guardado = guardar.call_args.args[0][clave]
        self.assertEqual(json.loads(guardado), catalogo)
        self.assertIn("mañana", guardado)
        self.assertIn("miércoles", guardado)
        patrones = patrones_configurados(guardado)
        self.assertIn("hay atencion el proximo miercoles", patrones["CONSULTA_HORAS"])
        self.assertIn("CONSULTA_HORAS", clasificar("hay atencion el proximo miercoles", patrones).motivos)

    def test_cada_clinica_usa_su_texto_y_conserva_el_predeterminado(self):
        cfg = {"respuesta_sistema_SMPT_reagendar_derivacion": "Respuesta de SMPT"}
        self.assertEqual(main._respuesta_sistema("reagendar_derivacion", "SMPT", cfg),
                         "Respuesta de SMPT")
        self.assertIn("reagendamiento",
                      main._respuesta_sistema("reagendar_derivacion", "VS", cfg))

    def test_la_configuracion_muestra_todas_las_respuestas_por_clinica(self):
        seccion = main._seccion_respuestas_centro("SMPT", "Salud Mental Para Todos")
        claves = {campo["clave"] for campo in seccion["campos"]}
        self.assertEqual(len(claves), len(main._RESPUESTAS_SISTEMA))
        self.assertIn("respuesta_sistema_SMPT_baja", claves)
        self.assertIn("respuesta_sistema_SMPT_reagendar_derivacion", claves)

    def test_mensajes_preparados_visibles_por_clinica(self):
        claves = {c['clave'] for c in main._seccion_respuestas_centro('SMPT', 'Clínica')['campos']}
        self.assertTrue({'respuesta_sistema_SMPT_preparado_interes',
                         'respuesta_sistema_SMPT_preparado_consulta',
                         'respuesta_sistema_SMPT_preparado_reagendar'} <= claves)

    def test_reagendar_y_consulta_utilizan_datos_y_clinica_correctos(self):
        cfg = {'respuesta_sistema_SMPT_preparado_reagendar':
               'Soy {paciente_nombre}; médico {doctor_nombre}; centro {clinica_nombre}.',
               'respuesta_sistema_SMPT_preparado_consulta': 'Consulta: {consulta}'}
        with patch.object(main.paciente_reagendar, 'config_get', side_effect=lambda clave: cfg.get(clave, '')):
            mensaje = main.paciente_reagendar.mensaje_preparado_reagendar({
                'centro_codigo': 'SMPT', 'paciente_nombre': 'Ana', 'doctor_nombre': 'Soto',
                'clinica_nombre': 'Clínica'})
            consulta = main.paciente_reagendar.mensaje_preparado_consulta('¿Hay horas?', 'SMPT')
        self.assertEqual(mensaje, 'Soy Ana; médico Soto; centro Clínica.')
        self.assertEqual(consulta, 'Consulta: ¿Hay horas?')

    def test_validacion_no_permite_perder_datos_de_la_cita(self):
        clave = 'respuesta_sistema_SMPT_preparado_reagendar'
        with patch.object(main, 'catalogo_centros', return_value={'SMPT': 'Clínica'}), \
             patch.object(main, 'config_set') as guardar:
            with self.assertRaises(main.HTTPException):
                main.actualizar_configuracion_completa(
                    main.ConfigTodoIn(cambios={clave: 'Hola {paciente_nombre}'}), sesion={'usuario': 'dev'})
        guardar.assert_not_called()

    def test_mensaje_interes_de_plantilla_tiene_prioridad_sobre_clinica(self):
        from urllib.parse import urlparse, parse_qs
        cfg = {'SMPT_CRM_PHONE_NUMBER': '56912345678',
               'respuesta_sistema_SMPT_preparado_interes': 'Mensaje de clínica'}
        with patch.object(main, 'config_get', side_effect=lambda clave, *args: cfg.get(clave, '')), \
             patch.object(main, '_mensaje_crm_ultima_campania', return_value='Mensaje de plantilla'):
            _, datos, _ = main._cc_datos_envio({'telefono': '+56912345678'}, 'produccion',
                                               emisor={'codigo': 'SMPT', 'phoneNumberId': '123'})
        self.assertEqual(parse_qs(urlparse(datos['cta']['url']).query)['text'], ['Mensaje de plantilla'])

    def test_mensaje_interes_sin_personalizacion_usa_clinica(self):
        from urllib.parse import urlparse, parse_qs
        cfg = {'SMPT_CRM_PHONE_NUMBER': '56912345678',
               'respuesta_sistema_SMPT_preparado_interes': 'Texto propio de esta clínica'}
        with patch.object(main, 'config_get', side_effect=lambda clave, *args: cfg.get(clave, '')), \
             patch.object(main, '_mensaje_crm_ultima_campania', return_value=None):
            _, datos, _ = main._cc_datos_envio({'telefono': '+56912345678'}, 'produccion',
                                               emisor={'codigo': 'SMPT', 'phoneNumberId': '123'})
        self.assertEqual(parse_qs(urlparse(datos['cta']['url']).query)['text'],
                         ['Texto propio de esta clínica'])

    def test_no_permite_quitar_la_explicacion_de_eliminar(self):
        clave = "respuesta_sistema_SMPT_baja"
        with patch.object(main, "catalogo_centros", return_value={"SMPT": "Clínica"}), \
             patch.object(main, "config_set") as guardar:
            with self.assertRaises(main.HTTPException) as error:
                main.actualizar_configuracion_completa(
                    main.ConfigTodoIn(cambios={clave: "Ya no recibirás notificaciones."}),
                    sesion={"usuario": "dev"})
        self.assertEqual(error.exception.status_code, 422)
        guardar.assert_not_called()

    def test_guarda_solo_la_respuesta_de_la_clinica_elegida(self):
        clave = "respuesta_sistema_VS_reagendar_derivacion"
        with patch.object(main, "catalogo_centros", return_value={"SMPT": "Uno", "VS": "Dos"}), \
             patch.object(main, "config_set") as guardar, \
             patch.object(main, "_config_valores", return_value={clave: "Texto de Vida y Salud"}), \
             patch.object(main, "listar_canales", return_value=[]), \
             patch.object(main, "config_get", return_value=""):
            respuesta = main.actualizar_configuracion_completa(
                main.ConfigTodoIn(cambios={clave: "Texto de Vida y Salud"}),
                sesion={"usuario": "dev"})
        guardar.assert_called_once_with({clave: "Texto de Vida y Salud"})
        self.assertEqual(respuesta["valores"][clave], "Texto de Vida y Salud")

    def test_derivacion_de_consulta_usa_el_aviso_de_la_clinica(self):
        cfg = {"respuesta_sistema_VS_consulta_callcenter": "Aviso propio de Vida y Salud.",
               "VS_CRM_PHONE_NUMBER": "56922222222"}
        with patch.object(main, "config_get", side_effect=lambda clave: cfg.get(clave, "")):
            texto, _datos, numero = main._cc_datos_envio(
                {"nombre": "Paciente"}, "produccion", "¿Hay horas?", {"codigo": "VS"})
        self.assertEqual(texto, "Aviso propio de Vida y Salud.")
        self.assertEqual(numero, "56922222222")

    def test_reagendamiento_usa_su_respuesta_configurada(self):
        cita = {"paciente_nombre": "Paciente", "doctor_nombre": "Doctora",
                "clinica_nombre": "VIDA Y SALUD", "paciente_telefono_normalizado": "+56911111111",
                "whatsapp_phone_number_id": "12345", "centro_codigo": "VS"}
        canal = {"codigo": "VS", "phoneNumberId": "12345", "configured": True}
        servicio = MagicMock()
        servicio.enviar = AsyncMock(return_value=(True, "wamid.out", None, "sent"))
        cfg = {"respuesta_sistema_VS_reagendar_derivacion": "Continuemos tu reagendamiento."}
        with patch.object(main, "esta_suprimido", return_value=False), \
             patch.object(main, "config_all", return_value={
                 "metodo_envio": "api_oficial", "entorno": "produccion",
                 "VS_CRM_PHONE_NUMBER": "56922222222"}), \
             patch.object(main, "config_get", side_effect=lambda clave: cfg.get(clave, "")), \
             patch.object(main, "resolver_phone_id", return_value=canal), \
             patch.object(main, "WhatsAppService", return_value=servicio), \
             patch.object(main, "registrar_historial"), \
             patch.object(main.paciente_reagendar, "marcar_respuesta_reagendar"):
            main._enviar_respuesta_reagendar({
                "accion": "REAGENDAR", "cita": cita, "texto": "Reagendar", "mensaje_id": "wamid.in"})
        self.assertEqual(servicio.enviar.await_args.args[1], "Continuemos tu reagendamiento.")


if __name__ == "__main__":
    unittest.main()
