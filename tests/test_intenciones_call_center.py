import sys
import unittest
import urllib.parse
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from intenciones_call_center import clasificar, detectar_automatizacion, patrones_configurados


class IntencionesCallCenterTests(unittest.TestCase):
    def setUp(self):
        from whatsapp_service import WhatsAppService
        p = patch.object(WhatsAppService, "_guardar_corpus_respuesta")
        p.start()
        self.addCleanup(p.stop)

    def test_etiquetas_automaticas_respetan_prioridades(self):
        from whatsapp_service import etiquetas_automaticas
        casos = {
            "Me interesa, ¿cuánto cuesta?": ("consulta", "interes"),
            "No gracias": ("desinteres",),
            "No me escriban más": ("baja_solicitada",),
            "Confirmo mi hora": ("confirmacion",),
            "No podré asistir": ("cancelacion",),
            "Hola": ("requiere_revision",),
            "Gracias por comunicarte con Centro Ejemplo. Hemos recibido tu mensaje y pronto responderemos.":
                ("respuesta_automatica",),
        }
        for texto, etiquetas in casos.items():
            with self.subTest(texto=texto):
                self.assertEqual(etiquetas_automaticas(texto), etiquetas)

    def test_automatizacion_exige_evidencia_y_no_deriva(self):
        casos = (
            "Hola, gracias por comunicarte con Centro Ejemplo. Hemos recibido tu mensaje y pronto responderemos.",
            "Soy un asistente virtual. Para agendar escribe 1. Para consultar escribe 2.",
            "Gracias por escribirnos. Nuestro horario de atención es de 9 a 18. Para continuar escribe 1.",
        )
        for texto in casos:
            with self.subTest(texto=texto):
                c = clasificar(texto)
                self.assertEqual(c.automatizacion, "AUTOMATICO_ALTA_CONFIANZA")
                self.assertFalse(c.derivar)
        humano = clasificar("Hola 😊 quería saber si tienen hora con un doctor mañana después de las cinco 🙏")
        self.assertEqual(humano.automatizacion, "SIN_EVIDENCIA_AUTOMATIZACION")
        self.assertTrue(humano.derivar)
        self.assertEqual(detectar_automatizacion("")[0], "SIN_EVIDENCIA_AUTOMATIZACION")

    def test_rechazo_no_es_baja_y_si_aislado_no_es_interes(self):
        from whatsapp_service import WhatsAppService, es_mensaje_interes, es_mensaje_no_interes
        for texto in ("No gracias", "Por ahora no", "Ya me atendí", "Ya tengo hora", "No quiero agendar"):
            with self.subTest(texto=texto):
                self.assertTrue(es_mensaje_no_interes(texto))
                self.assertFalse(WhatsAppService._es_baja(texto))
        self.assertFalse(es_mensaje_interes("Sí"))
        self.assertFalse(WhatsAppService._es_baja("No"))
        self.assertTrue(WhatsAppService._es_baja("No quiero recibir publicidad"))

    def test_webhook_bot_no_deriva_ni_reincorpora(self):
        from whatsapp_service import WhatsAppService
        servicio = WhatsAppService()
        payload = {"contacts": [{"wa_id": "56912345678"}], "messages": [{
            "id": "wamid.bot", "type": "text", "text": {"body":
                "Gracias por comunicarte con Centro Ejemplo. Hemos recibido tu mensaje y pronto responderemos."}}]}
        with patch("whatsapp_service.esta_suprimido", return_value=False), \
             patch("whatsapp_service.es_exento", return_value=False), \
             patch("whatsapp_service.es_numero_prueba", return_value=False), \
             patch("whatsapp_service.config_get", return_value="{}"), \
             patch.object(servicio, "_estaba_opt_out", return_value=True), \
             patch.object(servicio, "_registrar_respuesta", return_value="registrada") as registro, \
             patch.object(servicio, "_registrar_retractacion") as retractar, \
             patch("whatsapp_service.al_detectar_consulta") as consulta, \
             patch("whatsapp_service.al_detectar_interes") as interes:
            acciones = servicio._procesar_mensajes(payload)
        self.assertIn("respuesta_automatica_registrada", acciones)
        self.assertTrue(registro.call_args.kwargs["automatica"])
        retractar.assert_not_called()
        consulta.assert_not_called()
        interes.assert_not_called()

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
             patch.object(main, "es_numero_prueba", return_value=False), \
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
             patch.object(main, "es_numero_prueba", return_value=False), \
             patch.object(main.threading, "Timer") as timer:
            estado = main._registrar_derivacion_consulta(
                "+56912345678", "¿Cuánto cuesta?", "wamid.124", ("CONSULTA_PRECIOS",))
        self.assertEqual(estado, "reutilizada")
        timer.assert_not_called()

    def test_consultas_de_prueba_nuevas_generan_avisos_separados(self):
        import main
        conn = MagicMock()
        cur = conn.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect = [None, None]
        cur.lastrowid = 7
        with patch.object(main, "conectar", return_value=conn), \
             patch.object(main, "esta_suprimido", return_value=False), \
             patch.object(main, "es_numero_prueba", return_value=True), \
             patch.object(main.WhatsAppService, "_pacientes_con_tel", return_value=[
                 {"id": 2, "tabla": "pacientes_dev"}]), \
             patch.object(main.threading, "Timer") as timer:
            self.assertEqual(main._registrar_derivacion_consulta(
                "+56912345678", "¿Tienen horas?", "wamid.1", ("CONSULTA_HORAS",)), "nueva")
            self.assertEqual(main._registrar_derivacion_consulta(
                "+56912345678", "¿Cuánto cuesta?", "wamid.2", ("CONSULTA_PRECIOS",)), "nueva")
        self.assertEqual(timer.call_count, 2)
        self.assertFalse(any("INTERVAL 30 MINUTE" in c.args[0]
                             for c in cur.execute.call_args_list))

    def test_intereses_de_prueba_se_reservan_por_id_no_por_telefono(self):
        import main
        conn = MagicMock()
        cur = conn.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.rowcount = 1
        try:
            with patch.object(main, "conectar", return_value=conn), \
                 patch.object(main, "es_numero_prueba", return_value=True), \
                 patch.object(main.threading, "Timer") as timer:
                main._programar_call_center_auto("+56912345678", "wamid.1")
                main._programar_call_center_auto("+56912345678", "wamid.2")
                cur.rowcount = 0
                main._programar_call_center_auto("+56912345678", "wamid.1")
            self.assertEqual(timer.call_count, 2)
            self.assertEqual(timer.call_args_list[0].kwargs["args"],
                             (main._enviar_call_center_auto, "+56912345678", "wamid.1"))
        finally:
            main._cc_auto_pendientes.clear()

    def test_webhook_interes_de_prueba_entrega_id_del_mensaje(self):
        from whatsapp_service import WhatsAppService
        servicio = WhatsAppService()
        payload = {"contacts": [{"wa_id": "56912345678"}], "messages": [{
            "id": "wamid.interes-2", "type": "text", "text": {"body": "Me interesa"}}]}
        with patch("whatsapp_service.esta_suprimido", return_value=False), \
             patch("whatsapp_service.es_exento", return_value=False), \
             patch("whatsapp_service.es_numero_prueba", return_value=True), \
             patch.object(servicio, "_estaba_opt_out", return_value=False), \
             patch.object(servicio, "_registrar_respuesta", return_value="registrada"), \
             patch.object(servicio, "_registrar_interes"), \
             patch("whatsapp_service.al_detectar_interes") as interes:
            servicio._procesar_mensajes(payload)
        interes.assert_called_once_with("+56912345678", "wamid.interes-2")

    def test_interes_de_prueba_ignora_la_ronda_anterior_y_envia_una_vez(self):
        import main
        conn = MagicMock()
        cur = conn.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect = [
            {"id": 2, "nombre": "Prueba", "telefono": "+56912345678", "interesado": 1},
            {"ult_plantilla": 10, "ult_cc": 20},
        ]
        try:
            with patch.object(main, "esta_suprimido", return_value=False), \
                 patch.object(main, "es_numero_prueba", return_value=True), \
                 patch.object(main, "conectar", return_value=conn), \
                 patch.object(main, "columnas_tabla", return_value={"interesado"}), \
                 patch.object(main.servicio_areas, "listar_tablas_areas", return_value=[]), \
                 patch.object(main, "_despachar_call_center", return_value={"ok": True}) as despachar:
                main._enviar_call_center_auto("+56912345678", "wamid.nuevo")
            despachar.assert_called_once()
            self.assertFalse(any("INTERVAL 24 HOUR" in c.args[0]
                                 for c in cur.execute.call_args_list))
        finally:
            main._cc_auto_pendientes.clear()

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

    def test_vista_de_aviso_se_entrega_solo_con_permiso_de_datos(self):
        import main
        vista = '{"texto":"Aviso privado","boton_texto":"Ir al call center","mensaje_preparado":"Consulta privada"}'

        def consultar(permisos):
            conn = MagicMock()
            cur = conn.__enter__.return_value.cursor.return_value.__enter__.return_value
            cur.fetchall.side_effect = [[{
                "id": 7, "paciente_id": 2, "nombre_paciente": "Paciente Apellido",
                "numero_paciente": "+56912345678", "numero_call_center": "56999999999",
                "plantilla_clave": "call_center", "automatico": 1, "estado": "enviado",
                "descripcion_error": None, "base_datos": "pacientes_prod",
                "vista_mensaje": vista, "fecha_hora": datetime(2026, 10, 16, 12, 0),
            }], [], []]
            with patch.object(main, "conectar", return_value=conn), \
                 patch.object(main, "_es_privilegiado", return_value=True):
                return main.obtener_call_center_log({"rol": "usuario", "usuario": "operador",
                    "permisos": permisos})["entradas"][0]

        self.assertEqual(consultar(["pacientes_ver_datos"])["vista_mensaje"]["texto"], "Aviso privado")
        self.assertIsNone(consultar([])["vista_mensaje"])

    def test_despacho_guarda_la_vista_del_boton_realmente_preparado(self):
        import main
        canal = MagicMock()
        canal.enviar.return_value = (True, "wamid.aviso", None)
        datos = {"texto": "Mensaje automático", "cta": {
            "texto": "Ir al call center",
            "url": "https://wa.me/56999999999?text=Hola%2C%20esta%20es%20mi%20consulta",
        }}
        with patch.object(main, "esta_suprimido", return_value=False), \
             patch.object(main, "leer_config", return_value={}), \
             patch.object(main, "obtener_canal", return_value=canal), \
             patch.object(main, "_cc_datos_envio", return_value=("Mensaje automático", datos, "56999999999")), \
             patch.object(main, "registrar_historial"), \
             patch.object(main, "_registrar_call_center_log") as registrar:
            resultado = main._despachar_call_center(
                {"id": 2, "nombre": "Paciente", "telefono": "+56912345678"}, "produccion")
        self.assertTrue(resultado["ok"])
        self.assertEqual(registrar.call_args.args[-1], {
            "texto": "Mensaje automático", "boton_texto": "Ir al call center",
            "mensaje_preparado": "Hola, esta es mi consulta",
        })

    def test_reintento_requiere_permiso_de_datos(self):
        import main
        conn = MagicMock()
        cur = conn.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = {"paciente_id": 2, "base_datos": "pacientes_prod", "estado": "error"}
        with patch.object(main, "conectar", return_value=conn), \
             patch.object(main.threading, "Timer") as timer:
            with self.assertRaises(HTTPException) as error:
                main.reintentar_derivacion_call_center(7, {"rol": "usuario", "usuario": "otro",
                    "permisos": ["call_center_registro"]})
        self.assertEqual(error.exception.status_code, 403)
        timer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
