"""Flujo de dos mensajes para consultas posteriores a campañas normales."""

import datetime
import sys
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import main
import whatsapp_service
from whatsapp_service import ACCIONES_BOTON_TEMPLATE, WhatsAppService, es_solo_saludo_o_estado


TELEFONO = "+56912345678"
EMISOR = {"codigo": "VS", "nombre": "VIDA Y SALUD",
          "phoneNumberId": "1234500005", "phoneNumber": "+56900000005"}
CAMPANIA = {"id": 77, "base_datos": "pacientes_vsy"}


class EnrutamientoWebhookTests(unittest.TestCase):
    def setUp(self):
        self.servicio = WhatsAppService()
        for objetivo, retorno in (
            ("whatsapp_service.esta_suprimido", False),
            ("whatsapp_service.es_exento", False),
            ("whatsapp_service.es_numero_prueba", False),
            ("whatsapp_service.config_get", "{}"),
        ):
            parche = patch(objetivo, return_value=retorno)
            parche.start()
            self.addCleanup(parche.stop)
        for nombre, retorno in (
            ("_guardar_mensaje_entrante", True),
            ("_estaba_opt_out", False),
            ("_registrar_respuesta", "registrada"),
        ):
            parche = patch.object(self.servicio, nombre, return_value=retorno)
            parche.start()
            self.addCleanup(parche.stop)
        parche = patch.object(self.servicio, "_guardar_corpus_respuesta")
        parche.start()
        self.addCleanup(parche.stop)

    def procesar(self, texto, *, tipo="text", payload=""):
        mensaje = {"id": "wamid.respuesta", "type": tipo, "timestamp": "1792611000"}
        if tipo == "text":
            mensaje["text"] = {"body": texto}
        else:
            mensaje["button"] = {"text": texto, "payload": payload}
        return self.servicio._procesar_mensajes(
            {"contacts": [{"wa_id": TELEFONO}], "messages": [mensaje]})

    def test_saludos_y_estado_simple_no_piden_consulta(self):
        for texto in ("Hola", "Buenos días", "Estoy bien, gracias", "Me siento mal", "Ok"):
            self.assertTrue(es_solo_saludo_o_estado(texto), texto)
        self.assertFalse(es_solo_saludo_o_estado("Hola, ¿cuánto cuesta?"))
        with patch("whatsapp_service.al_consulta_campania") as flujo, \
                patch("whatsapp_service.al_detectar_consulta") as derivar:
            acciones = self.procesar("Hola")
        self.assertIn("respuesta_registrada", acciones)
        flujo.assert_not_called()
        derivar.assert_not_called()

    def test_pregunta_libre_pide_detalle_y_no_deriva_inmediatamente(self):
        with patch("whatsapp_service.al_consulta_campania",
                   return_value="detalle_solicitado") as flujo, \
                patch("whatsapp_service.al_detectar_consulta") as derivar:
            acciones = self.procesar("¿Cuánto cuesta?")
        self.assertIn("consulta_campania_detalle_solicitado", acciones)
        flujo.assert_called_once_with(TELEFONO, "¿Cuánto cuesta?", "wamid.respuesta", None)
        derivar.assert_not_called()

    def test_tengo_una_consulta_pide_un_solo_mensaje_antes_de_derivar(self):
        with patch("whatsapp_service.al_consulta_campania",
                   return_value="detalle_solicitado") as flujo, \
                patch("whatsapp_service.al_detectar_consulta") as derivar:
            acciones = self.procesar("Hola, tengo una consulta sobre mi atención")
        self.assertIn("consulta_campania_detalle_solicitado", acciones)
        flujo.assert_called_once_with(
            TELEFONO, "Hola, tengo una consulta sobre mi atención", "wamid.respuesta", None)
        derivar.assert_not_called()
        self.assertIn("en un solo mensaje", main._AVISO_CONSULTA_CAMPANIA)
        self.assertIn("registrar tu consulta", main._AVISO_CONSULTA_CAMPANIA)

    def test_intencion_de_agendar_en_texto_tambien_pide_detalle(self):
        with patch("whatsapp_service.al_consulta_campania",
                   return_value="detalle_solicitado") as flujo, \
                patch("whatsapp_service.al_detectar_consulta") as derivar, \
                patch.object(self.servicio, "_registrar_interes") as interes:
            self.procesar("Quiero agendar una hora")
        flujo.assert_called_once()
        derivar.assert_not_called()
        interes.assert_not_called()

    def test_boton_de_consulta_pide_detalle_antes_de_derivar(self):
        with patch("whatsapp_service.al_consulta_campania",
                   return_value="detalle_solicitado") as flujo, \
                patch("whatsapp_service.al_detectar_consulta") as derivar:
            acciones = self.procesar(
                "Consultar", tipo="button", payload=ACCIONES_BOTON_TEMPLATE["consulta"])
        self.assertIn("consulta_campania_detalle_solicitado", acciones)
        flujo.assert_called_once()
        derivar.assert_not_called()

    def test_botones_interes_desinteres_y_baja_no_entran_al_flujo(self):
        for texto, payload in (("Me interesa", "snw_tpl_interes"),
                               ("No me interesa", "snw_tpl_desinteres"),
                               ("Dar de baja", "snw_tpl_baja")):
            with self.subTest(texto=texto), \
                    patch("whatsapp_service.al_consulta_campania") as flujo, \
                    patch.object(self.servicio, "_registrar_interes"), \
                    patch.object(self.servicio, "_registrar_no_interes"), \
                    patch.object(self.servicio, "_registrar_baja"), \
                    patch("whatsapp_service.al_detectar_interes"), \
                    patch("whatsapp_service.al_detectar_baja"), \
                    patch("whatsapp_service.oferta_eliminacion_enviada", return_value=True):
                self.procesar(texto, tipo="button", payload=payload)
                flujo.assert_not_called()


class EstadoConsultaTests(unittest.TestCase):
    def conexion(self, fila_estado):
        conn = MagicMock()
        conn.__enter__.return_value = conn
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = fila_estado
        return conn, cur

    def test_primera_respuesta_reserva_pregunta_por_campania(self):
        conn, cur = self.conexion(None)
        with patch.object(main, "conectar", return_value=conn), \
                patch.object(main, "emisor_webhook_actual", return_value=EMISOR), \
                patch.object(main, "_ultima_campania_del_emisor", return_value=CAMPANIA), \
                patch.object(main.threading, "Timer") as timer:
            resultado = main._gestionar_consulta_campania(
                TELEFONO, "Necesito información", "wamid.primero", None)
        self.assertEqual(resultado, "detalle_solicitado")
        self.assertTrue(any("INSERT INTO campania_consultas" in c.args[0]
                            for c in cur.execute.call_args_list))
        timer.assert_called_once()
        timer.return_value.start.assert_called_once()

    def test_segunda_respuesta_usa_su_texto_y_su_base(self):
        conn, _ = self.conexion({
            "campania_log_id": 77, "primer_mensaje_id": "wamid.primero",
            "estado": "esperando", "actualizado": datetime.datetime.now()})
        with patch.object(main, "conectar", return_value=conn), \
                patch.object(main, "emisor_webhook_actual", return_value=EMISOR), \
                patch.object(main, "_ultima_campania_del_emisor", return_value=CAMPANIA), \
                patch.object(main, "_registrar_derivacion_consulta", return_value="nueva") as derivar:
            resultado = main._gestionar_consulta_campania(
                TELEFONO, "¿Puedo cambiar mi hora?", "wamid.segundo", None)
        self.assertEqual(resultado, "nueva")
        derivar.assert_called_once_with(
            TELEFONO, "¿Puedo cambiar mi hora?", "wamid.segundo",
            ("CONSULTA_CAMPANIA",), True, tabla_preferida="pacientes_vsy")

    def test_consulta_larga_no_se_recorta_ni_deriva(self):
        conn, _ = self.conexion({
            "campania_log_id": 77, "primer_mensaje_id": "wamid.primero",
            "ultimo_mensaje_id": None, "estado": "esperando",
            "actualizado": datetime.datetime.now()})
        with patch.object(main, "conectar", return_value=conn), \
                patch.object(main, "emisor_webhook_actual", return_value=EMISOR), \
                patch.object(main, "_ultima_campania_del_emisor", return_value=CAMPANIA), \
                patch.object(main, "_registrar_derivacion_consulta") as derivar, \
                patch.object(main.threading, "Timer") as timer:
            resultado = main._gestionar_consulta_campania(
                TELEFONO, "x" * 501, "wamid.largo", None)
        self.assertEqual(resultado, "texto_largo")
        derivar.assert_not_called()
        timer.assert_called_once()

    def test_sin_campania_o_ya_completada_no_reenvia(self):
        conn, _ = self.conexion(None)
        with patch.object(main, "conectar", return_value=conn), \
                patch.object(main, "emisor_webhook_actual", return_value=EMISOR), \
                patch.object(main, "_ultima_campania_del_emisor", return_value=None):
            self.assertEqual(main._gestionar_consulta_campania(
                TELEFONO, "Consulta", "wamid.x", None), "sin_campania")
        conn, _ = self.conexion({"campania_log_id": 77, "estado": "completada"})
        with patch.object(main, "conectar", return_value=conn), \
                patch.object(main, "emisor_webhook_actual", return_value=EMISOR), \
                patch.object(main, "_ultima_campania_del_emisor", return_value=CAMPANIA), \
                patch.object(main.threading, "Timer") as timer:
            self.assertEqual(main._gestionar_consulta_campania(
                TELEFONO, "Otra", "wamid.y", None), "ya_gestionada")
        timer.assert_not_called()

    def test_referencia_a_reagendamiento_no_se_atribuye_a_campania(self):
        cur = MagicMock()
        cur.fetchone.return_value = {"id": 99, "base_datos": "paciente_reagendar"}
        self.assertIsNone(main._ultima_campania_del_emisor(
            cur, TELEFONO, EMISOR["phoneNumberId"], "wamid.reagendar"))
        self.assertEqual(cur.execute.call_count, 1)

    def test_boton_crm_contiene_consulta_y_aviso_de_enviar_el_preparado(self):
        configuracion = {
            "call_center_aviso_consulta": "🤖 Mensaje automático: recibimos tu consulta.",
            "VS_CRM_PHONE_NUMBER": "+56999999999",
        }
        with patch.object(main, "config_get", side_effect=lambda clave: configuracion.get(clave, "")):
            texto, datos, numero = main._cc_datos_envio(
                {"nombre": "Prueba"}, "produccion", "¿Puedo cambiar mi hora?", EMISOR)
        self.assertEqual(numero, "56999999999")
        self.assertIn("Tu consulta quedó registrada", texto)
        self.assertIn("tal como está", texto)
        self.assertEqual(urllib.parse.parse_qs(
            urllib.parse.urlparse(datos["cta"]["url"]).query)["text"][0],
            "Hola, esta es mi consulta: ¿Puedo cambiar mi hora?")


if __name__ == "__main__":
    unittest.main()
