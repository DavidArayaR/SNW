import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from deteccion_ofensas import cargar_lexico, detectar_ofensa
from intenciones_call_center import clasificar, contiene_insulto
from whatsapp_service import (BOTON_ID_BAJA_INSULTO, BOTON_ID_EJECUTIVA,
                              WhatsAppService, etiquetas_automaticas)


TEL = "+56912345678"


class AvisoInsultoTests(unittest.TestCase):
    def setUp(self):
        p = patch.object(WhatsAppService, "_guardar_corpus_respuesta")
        p.start()
        self.addCleanup(p.stop)

    def test_insulto_explicito_sin_confundir_critica_o_palabra_parcial(self):
        for texto in ("Son unos idiotas", "Qué imbécil", "son unos weones",
                      "hijos de puta", "su servicio de mierda"):
            self.assertTrue(contiene_insulto(texto), texto)
        for texto in ("Tuve una mala experiencia", "No me interesa", "inutilización"):
            self.assertFalse(contiene_insulto(texto), texto)

    def test_insulto_se_etiqueta_desinteres_sin_convertirlo_en_baja(self):
        self.assertEqual(etiquetas_automaticas("Son unos idiotas"),
                         ("lenguaje_ofensivo", "desinteres"))
        self.assertEqual(etiquetas_automaticas("Idiotas, no me escriban más"),
                         ("baja_solicitada", "lenguaje_ofensivo"))

    def test_lexico_real_y_variantes_sin_duplicados(self):
        originales, variantes = cargar_lexico()
        self.assertGreater(len(originales), 500)
        self.assertFalse(originales & variantes)
        self.assertTrue(all(t in detectar_ofensa(t).terminos for t in originales))
        for termino in ("idiota", "imbecil", "estupido", "pendeja", "pelotudo",
                        "conchetumare", "chinga tu madre"):
            self.assertIn(termino, originales)
        for termino in ("estupidos", "estupidas", "babosos", "babosas",
                        "cabrones", "cabronas", "imbeciles", "idiotas"):
            self.assertIn(termino, originales | variantes)
            self.assertEqual(detectar_ofensa("Ustedes son " + termino).categoria,
                             "INSULTO_DIRECTO")

    def test_tildes_mayusculas_puntuacion_y_limites_de_palabra(self):
        for texto in ("¡Son ESTÚPIDAS!", "¿Eres un IMBÉCIL?", "Son unas pendejas."):
            self.assertEqual(detectar_ofensa(texto).categoria, "INSULTO_DIRECTO")
        self.assertEqual(detectar_ofensa("Ustedes son una basura").categoria,
                         "INSULTO_DIRECTO")
        for texto in ("inutilización", "estupidamente", "Mi análisis fue normal"):
            self.assertEqual(detectar_ofensa(texto).categoria, "SIN_INSULTO")

    def test_mencion_y_vocabulario_clinico_no_son_insultos_directos(self):
        self.assertEqual(detectar_ofensa('Me dijeron "idiota"').categoria,
                         "MENCION_DE_INSULTO")
        self.assertEqual(detectar_ofensa("No eres un idiota").categoria,
                         "MENCION_DE_INSULTO")
        self.assertEqual(detectar_ofensa("No toleramos insultos como idiota",
                                         automatica=True).categoria,
                         "MENCION_DE_INSULTO")
        self.assertEqual(detectar_ofensa("Tengo cáncer").categoria,
                         "SIN_INSULTO")
        self.assertFalse(contiene_insulto("Tengo cáncer"))

    def test_ofensa_no_borra_consulta_ni_interes(self):
        texto = "¿Tienen horas mañana, idiotas? Me interesa"
        c = clasificar(texto)
        self.assertEqual(c.ofensa.categoria, "INSULTO_DIRECTO")
        self.assertTrue(c.derivar)
        etiquetas = etiquetas_automaticas(texto, c)
        self.assertIn("lenguaje_ofensivo", etiquetas)
        self.assertIn("consulta", etiquetas)
        self.assertIn("interes", etiquetas)
        self.assertNotIn("desinteres", etiquetas)

    def test_respuesta_guarda_intencion_y_ofensa_en_metadatos(self):
        servicio = WhatsAppService()
        conn = MagicMock()
        cur = conn.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.lastrowid = 25
        texto = "¿Tienen horas mañana, idiotas?"
        with patch("whatsapp_service.conectar", return_value=conn), \
             patch.object(servicio, "_pacientes_con_tel", return_value=[{
                 "id": 7, "tabla": "pacientes_prod", "nombre": "Prueba",
                 "telefono": TEL}]), \
             patch("whatsapp_service.servicio_areas.area_por_tabla", return_value=None):
            self.assertEqual(servicio._registrar_respuesta(TEL, texto, None), "registrada")
        inserciones = [c for c in cur.execute.call_args_list
                       if "INSERT IGNORE INTO respuesta_etiquetas" in c.args[0]]
        self.assertEqual(len(inserciones), 1)
        etiquetas = json.loads(inserciones[0].args[1][1])
        detalle = json.loads(inserciones[0].args[1][2])
        self.assertIn("consulta", etiquetas)
        self.assertIn("lenguaje_ofensivo", etiquetas)
        self.assertEqual(detalle["categoria"], "INSULTO_DIRECTO")
        self.assertIn("idiotas", detalle["terminos"])
        self.assertIn("CONSULTA_HORAS", detalle["motivos_consulta"])

    def test_payload_ofrece_dos_botones_de_respuesta(self):
        payload, tipo = WhatsAppService().construir_payload_opciones_insulto(
            TEL, "Lamentamos que hayas tenido una mala experiencia.")
        self.assertEqual(tipo, "botones_servicio")
        self.assertEqual(payload["interactive"]["type"], "button")
        botones = payload["interactive"]["action"]["buttons"]
        self.assertEqual([b["reply"]["id"] for b in botones],
                         [BOTON_ID_EJECUTIVA, BOTON_ID_BAJA_INSULTO])
        self.assertTrue(all(len(b["reply"]["title"]) <= 20 for b in botones))

    def test_envio_oficial_usa_payload_interactivo(self):
        servicio = WhatsAppService()
        cliente = MagicMock()
        cliente.enviar = AsyncMock(return_value={"messages": [{"id": "wamid.aviso"}]})
        with patch("whatsapp_service.esta_suprimido", return_value=False), \
             patch.object(WhatsAppService, "cliente", new=property(lambda _self: cliente)):
            ok, mensaje_id, _, _ = asyncio.run(servicio.enviar(
                TEL, "Lamentamos que hayas tenido una mala experiencia.",
                plantilla={"opciones_insulto": True}))
        self.assertTrue(ok)
        self.assertEqual(mensaje_id, "wamid.aviso")
        self.assertEqual(cliente.enviar.call_args.args[0]["interactive"]["type"], "button")

    def test_insulto_ofrece_opciones_sin_dar_de_baja_ni_derivar(self):
        servicio = WhatsAppService()
        valor = {"contacts": [{"wa_id": TEL.lstrip("+")}], "messages": [{
            "id": "wamid.insulto", "type": "text", "text": {"body": "Son unos idiotas"}}]}
        with patch("whatsapp_service.esta_suprimido", return_value=False), \
             patch("whatsapp_service.es_exento", return_value=False), \
             patch("whatsapp_service.es_numero_prueba", return_value=False), \
             patch.object(servicio, "_estaba_opt_out", return_value=False), \
             patch.object(servicio, "_registrar_respuesta", return_value="registrada"), \
             patch.object(servicio, "_registrar_baja") as baja, \
             patch("whatsapp_service.al_detectar_consulta") as consulta, \
             patch("whatsapp_service.al_detectar_insulto") as aviso:
            acciones = servicio._procesar_mensajes(valor)
        self.assertIn("opciones_baja_ofrecidas", acciones)
        aviso.assert_called_once_with(TEL, "wamid.insulto")
        baja.assert_not_called()
        consulta.assert_not_called()

    def test_consulta_con_insulto_se_deriva_y_ofrece_opciones(self):
        servicio = WhatsAppService()
        valor = {"contacts": [{"wa_id": TEL.lstrip("+")}], "messages": [{
            "id": "wamid.consulta-insulto", "type": "text",
            "text": {"body": "¿Tienen horas mañana, idiotas?"}}]}
        with patch("whatsapp_service.esta_suprimido", return_value=False), \
             patch("whatsapp_service.es_exento", return_value=False), \
             patch("whatsapp_service.es_numero_prueba", return_value=False), \
             patch.object(servicio, "_estaba_opt_out", return_value=False), \
             patch.object(servicio, "_registrar_respuesta", return_value="registrada"), \
             patch.object(servicio, "_registrar_baja") as baja, \
             patch("whatsapp_service.al_detectar_consulta", return_value="nueva") as consulta, \
             patch("whatsapp_service.al_detectar_insulto") as aviso:
            acciones = servicio._procesar_mensajes(valor)
        self.assertIn("derivacion_nueva", acciones)
        self.assertIn("opciones_baja_ofrecidas", acciones)
        consulta.assert_called_once()
        aviso.assert_called_once()
        baja.assert_not_called()

    def test_baja_explicita_tiene_prioridad_sobre_insulto(self):
        servicio = WhatsAppService()
        valor = {"contacts": [{"wa_id": TEL.lstrip("+")}], "messages": [{
            "id": "wamid.baja", "type": "text",
            "text": {"body": "Idiotas, no me escriban más"}}]}
        with patch("whatsapp_service.esta_suprimido", return_value=False), \
             patch("whatsapp_service.es_exento", return_value=False), \
             patch("whatsapp_service.es_numero_prueba", return_value=False), \
             patch.object(servicio, "_estaba_opt_out", side_effect=[False, True]), \
             patch.object(servicio, "_registrar_respuesta", return_value="registrada"), \
             patch.object(servicio, "_registrar_baja") as baja, \
             patch("whatsapp_service.al_detectar_insulto") as aviso, \
             patch("whatsapp_service.oferta_eliminacion_enviada", return_value=False), \
             patch("whatsapp_service.al_detectar_baja"):
            servicio._procesar_mensajes(valor)
        baja.assert_called_once()
        aviso.assert_not_called()

    def test_boton_ejecutiva_deriva_una_consulta_nueva(self):
        servicio = WhatsAppService()
        valor = {"contacts": [{"wa_id": TEL.lstrip("+")}], "messages": [{
            "id": "wamid.ejecutiva", "type": "interactive",
            "interactive": {"button_reply": {"id": BOTON_ID_EJECUTIVA,
                                             "title": "Hablar con ejecutiva"}}}]}
        with patch("whatsapp_service.esta_suprimido", return_value=False), \
             patch("whatsapp_service.es_exento", return_value=False), \
             patch("whatsapp_service.es_numero_prueba", return_value=False), \
             patch.object(servicio, "_estaba_opt_out", return_value=False), \
             patch.object(servicio, "_registrar_respuesta", return_value="registrada"), \
             patch("whatsapp_service.al_detectar_consulta", return_value="nueva") as consulta:
            acciones = servicio._procesar_mensajes(valor)
        self.assertIn("derivacion_nueva", acciones)
        self.assertEqual(consulta.call_args.args[-1], True)

    def test_boton_baja_insulto_conserva_la_baja_explicita(self):
        servicio = WhatsAppService()
        valor = {"contacts": [{"wa_id": TEL.lstrip("+")}], "messages": [{
            "id": "wamid.boton-baja", "type": "interactive",
            "interactive": {"button_reply": {"id": BOTON_ID_BAJA_INSULTO,
                                             "title": "Dar de baja"}}}]}
        with patch("whatsapp_service.esta_suprimido", return_value=False), \
             patch("whatsapp_service.es_exento", return_value=False), \
             patch("whatsapp_service.es_numero_prueba", return_value=False), \
             patch.object(servicio, "_estaba_opt_out", side_effect=[False, True]), \
             patch.object(servicio, "_registrar_respuesta", return_value="registrada"), \
             patch.object(servicio, "_registrar_baja") as baja, \
             patch("whatsapp_service.oferta_eliminacion_enviada", return_value=False), \
             patch("whatsapp_service.al_detectar_baja"):
            acciones = servicio._procesar_mensajes(valor)
        self.assertIn("baja_boton", acciones)
        baja.assert_called_once_with(TEL, aviso=False)

    def test_aviso_es_inmediato_y_respeta_cooldown(self):
        import main
        with patch.object(main, "esta_suprimido", return_value=False), \
             patch.object(main, "eliminacion_simulada_activa", return_value=False), \
             patch.object(main.WhatsAppService, "_estaba_opt_out", return_value=False), \
             patch.object(main, "_reservar_aviso_insulto", return_value=True), \
             patch.object(main, "_enviar_mensaje_directo", return_value=True) as enviar:
            main._avisar_insulto(TEL)
        self.assertEqual(enviar.call_args.kwargs["plantilla"], {"opciones_insulto": True})
        self.assertIn("hablar con una ejecutiva", enviar.call_args.args[1])
        conn = MagicMock()
        cur = conn.__enter__.return_value.cursor.return_value.__enter__.return_value
        def ejecutar(sql, *_):
            cur.rowcount = 0 if sql.startswith("INSERT") else 1
        cur.execute.side_effect = ejecutar
        with patch.object(main, "conectar", return_value=conn):
            self.assertTrue(main._reservar_aviso_insulto(TEL))
        self.assertTrue(any("INTERVAL 24 HOUR" in c.args[0]
                            for c in cur.execute.call_args_list))


if __name__ == "__main__":
    unittest.main()
