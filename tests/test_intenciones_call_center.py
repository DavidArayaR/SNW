import sys
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import MagicMock, patch
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from intenciones_call_center import clasificar, patrones_configurados


class IntencionesCallCenterTests(unittest.TestCase):
    def test_consultas_principales(self):
        casos = {
            "Quiero saber más": "SOLICITA_INFORMACION",
            "¿Tienen horas para mañana?": "CONSULTA_HORAS",
            "¿Quedan cupos?": "CONSULTA_HORAS",
            "¿Hay especialistas?": "CONSULTA_MEDICO",
            "¿Está atendiendo el doctor?": "CONSULTA_MEDICO",
            "¿Cuánto cuesta?": "CONSULTA_PRECIOS",
            "¿Qué especialidades tienen?": "CONSULTA_PRECIOS",
            "¿Me pueden llamar?": "SOLICITA_CONTACTO",
            "Quiero hablar con una persona": "SOLICITA_CONTACTO",
            "Una consulta": "CONSULTA_AMBIGUA",
            "Necesito ayuda": "CONSULTA_AMBIGUA",
            "holi": "CONSULTA_AMBIGUA",
            "Quiero informaicon": "CONSULTA_AMBIGUA",
            "Tengo una pregunta distinta": "CONSULTA_AMBIGUA",
        }
        for texto, motivo in casos.items():
            with self.subTest(texto=texto):
                self.assertIn(motivo, clasificar(texto).motivos)

    def test_prioridades_y_multiples_intenciones(self):
        self.assertFalse(clasificar("Sí").derivar)
        self.assertFalse(clasificar("Confirmo").derivar)
        self.assertFalse(clasificar("No me interesa").derivar)
        r = clasificar("Confirmo mi hora, pero quiero saber si hay otro doctor")
        self.assertTrue(r.confirma)
        self.assertIn("CONSULTA_MEDICO", r.motivos)
        r = clasificar("No puedo asistir, ¿tienen otra hora?")
        self.assertTrue(r.cancela)
        self.assertIn("CONSULTA_HORAS", r.motivos)

    def test_catalogo_validado(self):
        catalogo = patrones_configurados('{"CONSULTA_HORAS":["Hora para el sábado"],"MALO":["x"]}')
        self.assertEqual(catalogo, {"CONSULTA_HORAS": ("hora para el sabado",)})
        self.assertIn("CONSULTA_HORAS", clasificar("¿Hora para el sábado?", catalogo).motivos)
        self.assertEqual(patrones_configurados("{mal"), {})

    def test_webhook_deriva_sin_disparar_otra_respuesta_generica(self):
        from whatsapp_service import WhatsAppService
        servicio = WhatsAppService()
        payload = {"contacts": [{"wa_id": "56912345678"}], "messages": [{
            "id": "wamid.123", "type": "text",
            "text": {"body": "¿Tienen horas para mañana?"}}]}
        with patch("whatsapp_service.esta_suprimido", return_value=False), \
             patch("whatsapp_service.es_exento", return_value=False), \
             patch("whatsapp_service.es_numero_prueba", return_value=False), \
             patch("whatsapp_service.config_get", return_value="{}"), \
             patch.object(servicio, "_estaba_opt_out", return_value=False), \
             patch.object(servicio, "_registrar_respuesta", return_value="registrada"), \
             patch("whatsapp_service.al_detectar_consulta", return_value="nueva") as consulta, \
             patch("whatsapp_service.al_detectar_interes") as interes:
            acciones = servicio._procesar_mensajes(payload)
        self.assertIn("derivacion_nueva", acciones)
        self.assertEqual(consulta.call_args.args[2], "wamid.123")
        interes.assert_not_called()

    def test_cancelar_cita_no_da_de_baja_al_paciente(self):
        from whatsapp_service import WhatsAppService
        servicio = WhatsAppService()
        payload = {"contacts": [{"wa_id": "56912345678"}], "messages": [{
            "id": "wamid.cita", "type": "text",
            "text": {"body": "Quiero cancelar mi hora, ¿tienen otra?"}}]}
        with patch("whatsapp_service.esta_suprimido", return_value=False), \
             patch("whatsapp_service.es_exento", return_value=False), \
             patch("whatsapp_service.es_numero_prueba", return_value=False), \
             patch("whatsapp_service.config_get", return_value="{}"), \
             patch.object(servicio, "_estaba_opt_out", return_value=False), \
             patch.object(servicio, "_registrar_respuesta", return_value="registrada"), \
             patch.object(servicio, "_registrar_baja") as baja, \
             patch("whatsapp_service.al_detectar_consulta", return_value="nueva"):
            acciones = servicio._procesar_mensajes(payload)
        self.assertIn("derivacion_nueva", acciones)
        baja.assert_not_called()

    def test_derivacion_unica_por_mensaje(self):
        import main
        conn = MagicMock()
        cur = MagicMock()
        conn.__enter__.return_value = conn
        conn.cursor.return_value.__enter__.return_value = cur
        cur.lastrowid = 7
        with patch.object(main, "conectar", return_value=conn), \
             patch.object(main, "esta_suprimido", return_value=False), \
             patch.object(main.WhatsAppService, "_pacientes_con_tel", return_value=[
                 {"id": 2, "tabla": "pacientes_prod"}]), \
             patch.object(main.threading, "Timer") as timer:
            cur.fetchone.side_effect = [None, None]
            self.assertEqual(main._registrar_derivacion_consulta(
                "+56912345678", "¿Tienen horas?", "wamid.123", ("CONSULTA_HORAS",)), "nueva")
            timer.assert_called_once()
            timer.return_value.start.assert_called_once()
            cur.fetchone.side_effect = [{"derivacion_id": 7}]
            self.assertEqual(main._registrar_derivacion_consulta(
                "+56912345678", "¿Tienen horas?", "wamid.123", ("CONSULTA_HORAS",)), "duplicada")
            timer.assert_called_once()

    def test_consulta_nueva_reutiliza_derivacion_abierta_sin_otro_aviso(self):
        import main
        conn = MagicMock()
        cur = conn.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect = [None, {"id": 7, "estado": "notificado"}]
        with patch.object(main, "conectar", return_value=conn), \
             patch.object(main, "esta_suprimido", return_value=False), \
             patch.object(main.threading, "Timer") as timer:
            estado = main._registrar_derivacion_consulta(
                "+56912345678", "¿Cuánto cuesta?", "wamid.124", ("CONSULTA_PRECIOS",))
        self.assertEqual(estado, "reutilizada")
        timer.assert_not_called()

    def test_aviso_identifica_automatizacion_y_boton_conserva_consulta(self):
        import main
        with patch.object(main, "config_get", return_value=(
                "🤖 Mensaje automático: Pulsa Ir al call center y envía tu consulta.")), \
             patch.object(main, "_elegir_numero_call_center", return_value="56999999999"):
            texto, datos, numero = main._cc_datos_envio(
                {"id": 1, "nombre": "Paciente"}, "produccion", "¿Tienen horas mañana?")
        self.assertIn("Mensaje automático", texto)
        self.assertEqual(numero, "56999999999")
        enlace = datos["cta"]["url"]
        self.assertEqual(urllib.parse.unquote(enlace.split("text=", 1)[1]),
                         "Hola, esta es mi consulta: ¿Tienen horas mañana?")

    def test_reintento_restringido_a_propietario_del_csv(self):
        import main
        conn = MagicMock()
        cur = conn.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = {"paciente_id": 2, "base_datos": "pacientes_prod", "estado": "error"}
        with patch.object(main, "conectar", return_value=conn), \
             patch.object(main, "_ids_csv_propios", return_value=set()), \
             patch.object(main.threading, "Timer") as timer:
            with self.assertRaises(HTTPException) as error:
                main.reintentar_derivacion_call_center(7, {"usuario": "otro"})
        self.assertEqual(error.exception.status_code, 403)
        timer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
