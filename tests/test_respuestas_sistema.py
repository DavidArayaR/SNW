"""Configuración y selección de respuestas automáticas por clínica."""

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import main


class RespuestasSistemaTests(unittest.TestCase):
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
             patch.object(main.paciente_reagendar, "marcar_respuesta_reagendar"):
            main._enviar_respuesta_reagendar({
                "accion": "REAGENDAR", "cita": cita, "texto": "Reagendar", "mensaje_id": "wamid.in"})
        self.assertEqual(servicio.enviar.await_args.args[1], "Continuemos tu reagendamiento.")


if __name__ == "__main__":
    unittest.main()
