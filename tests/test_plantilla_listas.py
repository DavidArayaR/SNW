import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import main
from plantilla_revision import presentar_plantilla, version_opciones
from whatsapp_service import WhatsAppApiClient, WhatsAppService


BOTONES = [
    {"tipo": "respuesta", "texto": "Ver opciones", "accion": "opciones",
     "opciones": [{"texto": "Horarios", "mensaje": "Atendemos de lunes a viernes."},
                  {"texto": "Servicios", "mensaje": "Conoce nuestras atenciones."}]},
    {"tipo": "respuesta", "texto": "Dar de baja", "accion": "baja"},
]
PLANTILLA = {"id": 7, "nombre": "Ejemplo", "texto": "Hola", "whatsapp_template": "ejemplo",
            "whatsapp_template_status": "APPROVED", "botones": BOTONES}
TEL = "+56912345678"


class ValidacionListaTests(unittest.TestCase):
    def test_lista_valida_con_baja(self):
        vista = presentar_plantilla(PLANTILLA)
        self.assertFalse(vista["errores"])
        self.assertEqual(len(vista["botones"][0]["opciones"]), 2)

    def test_lista_exige_opciones_completas_y_con_limites(self):
        casos = ([], [{"texto": "Horarios", "mensaje": ""}],
                 [{"texto": "ELIMINAR", "mensaje": "No"}],
                 [{"texto": str(i), "mensaje": "Información"} for i in range(11)])
        for opciones in casos:
            with self.subTest(opciones=opciones):
                plantilla = {**PLANTILLA, "botones": [{**BOTONES[0], "opciones": opciones}, BOTONES[1]]}
                self.assertTrue(presentar_plantilla(plantilla)["errores"])

    def test_solo_una_lista_por_plantilla(self):
        plantilla = {**PLANTILLA, "botones": [BOTONES[0],
            {**BOTONES[0], "texto": "Más opciones"}, BOTONES[1]]}
        self.assertTrue(any("Solo se permite un botón" in error
                            for error in presentar_plantilla(plantilla)["errores"]))

    def test_version_cambia_si_cambia_el_contenido(self):
        self.assertNotEqual(version_opciones(BOTONES[0]["opciones"]),
                            version_opciones([{"texto": "Horarios", "mensaje": "Otro horario"}]))

    def test_menu_de_meta_lleva_ids_de_version(self):
        servicio = WhatsAppService()
        payload, tipo = servicio.construir_payload_lista(TEL, 7, BOTONES[0])
        self.assertEqual(tipo, "lista_opciones")
        self.assertEqual(payload["interactive"]["type"], "list")
        filas = payload["interactive"]["action"]["sections"][0]["rows"]
        self.assertEqual([f["title"] for f in filas], ["Horarios", "Servicios"])
        self.assertEqual(filas[1]["id"],
                         f"snw_lista_7_{version_opciones(BOTONES[0]['opciones'])}_1")


class EnvioListaTests(unittest.IsolatedAsyncioTestCase):
    async def test_template_envia_payload_de_ver_opciones(self):
        servicio = WhatsAppService()
        with patch("whatsapp_service.esta_suprimido", return_value=False), \
             patch.object(servicio, "_validar_canal_antes_envio", new_callable=AsyncMock,
                          return_value=(True, None)), \
             patch.object(servicio, "_plantilla_aprobada_en_waba", new_callable=AsyncMock,
                          return_value=True), \
             patch.object(WhatsAppApiClient, "enviar", new_callable=AsyncMock,
                          return_value={"messages": [{"id": "wamid.template"}]}) as enviar:
            ok, _, _, _ = await servicio.enviar(TEL, "Hola", PLANTILLA)
        self.assertTrue(ok)
        componentes = enviar.call_args.args[0]["template"]["components"]
        self.assertEqual(componentes[0]["parameters"][0]["payload"], "snw_tpl_opciones_7")


class WebhookListaTests(unittest.TestCase):
    def setUp(self):
        self.servicio = WhatsAppService()

    def _procesar(self, mensaje):
        valor = {"contacts": [{"wa_id": TEL.lstrip("+")}], "messages": [mensaje]}
        with patch("whatsapp_service.esta_suprimido", return_value=False), \
             patch("whatsapp_service.es_exento", return_value=False), \
             patch("whatsapp_service.es_numero_prueba", return_value=False), \
             patch.object(self.servicio, "_estaba_opt_out", return_value=False), \
             patch.object(self.servicio, "_guardar_corpus_respuesta"), \
             patch.object(self.servicio, "_registrar_respuesta", return_value="registrada"):
            return self.servicio._procesar_mensajes(valor)

    def test_pulsacion_del_boton_solicita_lista(self):
        mensaje = {"id": "wamid.boton", "type": "button",
                   "button": {"text": "Ver opciones", "payload": "snw_tpl_opciones_7"}}
        with patch("whatsapp_service.al_mostrar_opciones") as mostrar:
            acciones = self._procesar(mensaje)
        self.assertIn("lista_solicitada", acciones)
        mostrar.assert_called_once_with(TEL, 7, "wamid.boton")

    def test_seleccion_de_lista_solicita_respuesta(self):
        version = version_opciones(BOTONES[0]["opciones"])
        mensaje = {"id": "wamid.fila", "type": "interactive", "interactive": {
            "type": "list_reply", "list_reply": {
                "id": f"snw_lista_7_{version}_1", "title": "Servicios"}}}
        with patch("whatsapp_service.al_responder_opcion") as responder:
            acciones = self._procesar(mensaje)
        self.assertIn("opcion_lista_elegida", acciones)
        responder.assert_called_once_with(TEL, 7, version, 1, "wamid.fila")


class CallbackListaTests(unittest.TestCase):
    def test_reserva_identificador_sin_guardar_el_numero(self):
        conexion = MagicMock()
        cursor = conexion.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.rowcount = 1
        with patch.object(main, "conectar", return_value=conexion):
            self.assertTrue(main._reservar_interaccion_lista("wamid.prueba"))
        self.assertEqual(cursor.execute.call_args.args[0].split(" VALUES")[0],
                         "INSERT IGNORE INTO whatsapp_interacciones_lista (mensaje_hash)")
        self.assertEqual(len(cursor.execute.call_args.args[1][0]), 64)
        self.assertNotIn("wamid.prueba", cursor.execute.call_args.args[1][0])
        self.assertFalse(main._reservar_interaccion_lista(None))

    def test_muestra_lista_de_plantilla_vigente(self):
        with patch.object(main, "leer_plantillas", return_value=[PLANTILLA]), \
             patch.object(main, "config_get", return_value="api_oficial"), \
             patch.object(main, "esta_suprimido", return_value=False), \
             patch.object(main, "_reservar_interaccion_lista", return_value=True), \
             patch.object(main, "_enviar_mensaje_directo") as enviar:
            main._enviar_lista_opciones(TEL, 7, "wamid.boton")
        self.assertEqual(enviar.call_args.args[2], "lista_opciones")
        self.assertEqual(enviar.call_args.kwargs["plantilla"]["lista_opciones"]["boton"]["opciones"],
                         BOTONES[0]["opciones"])

    def test_respuesta_usa_mensaje_configurado_y_rechaza_lista_antigua(self):
        with patch.object(main, "leer_plantillas", return_value=[PLANTILLA]), \
             patch.object(main, "config_get", return_value="api_oficial"), \
             patch.object(main, "esta_suprimido", return_value=False), \
             patch.object(main, "_reservar_interaccion_lista", return_value=True), \
             patch.object(main, "_enviar_mensaje_directo") as enviar:
            main._responder_opcion_lista(TEL, 7, "000000000000", 1, "wamid.viejo")
            enviar.assert_not_called()
            main._responder_opcion_lista(TEL, 7, version_opciones(BOTONES[0]["opciones"]),
                                        1, "wamid.nuevo")
        enviar.assert_called_once_with(TEL, "Conoce nuestras atenciones.", "lista_respuesta")


if __name__ == "__main__":
    unittest.main()
