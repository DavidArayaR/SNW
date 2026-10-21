"""Cobertura de la cola CRM local; no se realizan llamadas externas."""

import datetime
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import main
import whatsapp_service
from whatsapp_service import WhatsAppService
from whatsapp_channels import EMISOR_WEBHOOK


EMISOR = {"codigo": "VS", "nombre": "VIDA Y SALUD",
          "phoneNumberId": "1234500005", "phoneNumber": "+56900000005"}
TELEFONO = "+56912345678"


class WebhookCrmTests(unittest.TestCase):
    def setUp(self):
        self.servicio = WhatsAppService()
        self.parches = [
            patch("whatsapp_service.esta_suprimido", return_value=False),
            patch("whatsapp_service.es_exento", return_value=False),
            patch("whatsapp_service.es_numero_prueba", return_value=False),
            patch("whatsapp_service.config_get", return_value="{}"),
            patch.object(self.servicio, "_guardar_mensaje_entrante", return_value=True),
            patch.object(self.servicio, "_guardar_corpus_respuesta"),
            patch.object(self.servicio, "_estaba_opt_out", return_value=False),
            patch.object(self.servicio, "_registrar_respuesta", return_value="registrada"),
            patch.object(self.servicio, "_registrar_interes"),
            patch.object(self.servicio, "_registrar_no_interes"),
            patch.object(self.servicio, "_registrar_baja"),
            patch("whatsapp_service.al_detectar_consulta", return_value="nueva"),
            patch("whatsapp_service.al_detectar_interes"),
            patch("whatsapp_service.al_detectar_baja"),
            patch("whatsapp_service.al_derivar_crm"),
        ]
        for parche in self.parches:
            valor = parche.start()
            self.addCleanup(parche.stop)
            if parche.attribute == "al_derivar_crm":
                self.cola = valor

    def procesar(self, texto, mensaje_id="wamid.nuevo", *, tipo="text", contexto=None):
        mensaje = {"id": mensaje_id, "type": tipo, "timestamp": "1792611000"}
        if tipo == "text":
            mensaje["text"] = {"body": texto}
        elif tipo == "image":
            mensaje["image"] = {"caption": texto}
        if contexto:
            mensaje["context"] = {"id": contexto}
        return self.servicio._procesar_mensajes(
            {"contacts": [{"wa_id": TELEFONO}], "messages": [mensaje]})

    def test_todas_las_respuestas_humanas_entran_una_vez_a_cola(self):
        casos = [
            ("Me interesa", "INTERES"),
            ("¿Cuánto cuesta?", "CONSULTA_PRECIOS"),
            ("Quiero agendar una hora", "INTERES"),
            ("Tengo un reclamo", "GENERAL"),
            ("No gracias", "DESINTERES"),
            ("No me escriban más", "BAJA"),
            ("Hola, necesito consultar algo.", "CONSULTA_AMBIGUA"),
        ]
        for indice, (texto, motivo) in enumerate(casos):
            with self.subTest(texto=texto):
                self.cola.reset_mock()
                self.procesar(texto, f"wamid.{indice}", contexto="wamid.campania")
                self.cola.assert_called_once()
                self.assertIn(motivo, self.cola.call_args.args[3])
                self.assertEqual(self.cola.call_args.kwargs["referencia_meta_id"],
                                 "wamid.campania")
                if motivo == "BAJA":
                    self.assertEqual(self.cola.call_args.args[1], "Solicitud de baja")

    def test_adjunto_sin_texto_se_encola_sin_archivo(self):
        self.procesar("", tipo="image")
        self.cola.assert_called_once()
        self.assertEqual(self.cola.call_args.kwargs["tipo"], "image")
        self.assertEqual(self.cola.call_args.args[1], "")

    def test_automatizacion_y_eliminar_no_se_encolan(self):
        self.procesar("Gracias por comunicarte con Centro Ejemplo. Hemos recibido tu mensaje y pronto responderemos.")
        self.cola.assert_not_called()
        self.procesar("ELIMINAR", "wamid.eliminar")
        self.cola.assert_not_called()


class ContextoCrmTests(unittest.TestCase):
    def test_contexto_usa_solo_paciente_y_emisor_y_campania_explicita(self):
        cur = MagicMock()
        instante = datetime.datetime(2026, 10, 21, 12, 30)
        cur.fetchall.side_effect = [
            [{"envio_id": 8, "plantilla_clave": "promocion", "mensaje": "Oferta",
              "fecha_hora": instante, "plantilla_nombre": "Campaña", "base_datos": "pacientes_prod"}],
            [{"mensaje_id": "wamid.nuevo", "tipo": "text", "texto": "Consulta",
              "recibido": instante}],
            [{"whatsapp_message_id": "wamid.campania", "mensaje": "Oferta",
              "plantilla_clave": "promocion", "fecha_hora": instante}],
        ]
        contexto = main._contexto_derivacion_crm(
            cur, EMISOR, TELEFONO, "wamid.nuevo", "text", "1792611000", "wamid.campania")
        self.assertEqual(contexto["campania"]["envio_id"], 8)
        self.assertEqual(len(contexto["historial"]), 2)
        for llamada in cur.execute.call_args_list:
            self.assertIn(EMISOR["phoneNumberId"], llamada.args[1])
            self.assertIn(TELEFONO, llamada.args[1])
        self.assertEqual(contexto["clinica"]["codigo"], "VS")

    def test_sin_referencia_no_inventa_campania_y_limita_historial(self):
        cur = MagicMock()
        entradas = [{"mensaje_id": f"in.{i}", "tipo": "text", "texto": "Hola",
                     "recibido": datetime.datetime(2026, 10, 21, 10, i)} for i in range(20)]
        salidas = [{"whatsapp_message_id": f"out.{i}", "mensaje": "Aviso",
                    "plantilla_clave": "promocion",
                    "fecha_hora": datetime.datetime(2026, 10, 21, 9, i)} for i in range(20)]
        cur.fetchall.side_effect = [entradas, salidas]
        contexto = main._contexto_derivacion_crm(
            cur, EMISOR, TELEFONO, "in.19", "text", None, None)
        self.assertIsNone(contexto["campania"])
        self.assertEqual(len(contexto["historial"]), 20)
        self.assertEqual(cur.execute.call_count, 2)

    def test_cola_persistente_guarda_contexto_sin_envio_externo(self):
        conexion = MagicMock()
        cur = conexion.__enter__.return_value.cursor.return_value.__enter__.return_value
        contexto = {"mensaje_actual": {"id": "wamid.nuevo", "texto": ""}, "historial": []}
        token = EMISOR_WEBHOOK.set(EMISOR)
        try:
            with patch.object(main, "conectar", return_value=conexion), \
                 patch.object(main, "_contexto_derivacion_crm", return_value=contexto):
                main._encolar_derivacion_crm(TELEFONO, "Hola", "wamid.nuevo", ("GENERAL",))
        finally:
            EMISOR_WEBHOOK.reset(token)
        sql, parametros = cur.execute.call_args.args
        self.assertIn("INSERT IGNORE INTO crm_derivaciones_pendientes", sql)
        self.assertEqual(json.loads(parametros[-1])["mensaje_actual"]["texto"], "Hola")
        conexion.__enter__.return_value.commit.assert_called_once()

    def test_baja_omite_historial_y_campania_de_la_cola(self):
        conexion = MagicMock()
        cur = conexion.__enter__.return_value.cursor.return_value.__enter__.return_value
        contexto = {"mensaje_actual": {"id": "wamid.baja", "texto": ""},
                    "historial": [{"texto": "dato previo"}], "campania": {"mensaje_original": "Oferta"}}
        token = EMISOR_WEBHOOK.set(EMISOR)
        try:
            with patch.object(main, "conectar", return_value=conexion), \
                 patch.object(main, "_contexto_derivacion_crm", return_value=contexto):
                main._encolar_derivacion_crm(TELEFONO, "Solicitud de baja", "wamid.baja", ("BAJA",))
        finally:
            EMISOR_WEBHOOK.reset(token)
        guardado = json.loads(cur.execute.call_args.args[1][-1])
        self.assertEqual(guardado["historial"], [])
        self.assertIsNone(guardado["campania"])
        self.assertEqual(guardado["mensaje_actual"]["texto"], "Solicitud de baja")


if __name__ == "__main__":
    unittest.main()
