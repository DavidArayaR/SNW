"""Envíos manuales con mensaje editable y destino CRM fijado por el historial."""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import main
import whatsapp_service


class CallCenterManualTests(unittest.TestCase):
    def ejecutar(self, ventana=True, permitido=True):
        conexion = MagicMock()
        cur = conexion.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect = [
            {"paciente_id": 2, "numero_telefono": "+56911111111", "wa_phone_id": "12345",
             "tabla": "pacientes_test"},
            {"id": 2, "telefono": "+56911111111", "whatsapp_opt_out": 0},
            {"ok": 1} if ventana else None]
        emisor = {"codigo": "VS", "phoneNumberId": "12345", "configured": True}
        with patch.object(main, "tiene_permiso", return_value=permitido), \
             patch.object(main, "detalle_historial", return_value=[{"id": 9}]), \
             patch.object(main, "conectar", return_value=conexion), \
             patch.object(main.servicio_areas, "tabla_valida", return_value=True), \
             patch.object(main.servicio_areas, "tabla_fisica_existe", return_value=True), \
             patch.object(main, "resolver_phone_id", return_value=emisor), \
             patch.object(main, "config_get", side_effect=lambda k: "api_oficial" if k == "metodo_envio" else "produccion"), \
             patch.object(main, "esta_suprimido", return_value=False), \
             patch.object(main.WhatsAppService, "_estaba_opt_out", return_value=False), \
             patch.object(main, "_despachar_call_center", return_value={"ok": True}) as enviar, \
             patch.object(main, "auditoria_registrar"):
            try:
                resultado = main.enviar_callcenter_manual(1, 9,
                    main.CallCenterManualIn(mensaje="Disculpa la confusión. Puedes continuar aquí."),
                    ambiente="produccion", sesion={"usuario": "operador"})
            except main.HTTPException as exc:
                return exc.status_code, enviar
        return resultado, enviar

    def test_conserva_texto_editado_y_emisor_del_registro(self):
        resultado, enviar = self.ejecutar()
        self.assertTrue(resultado["ok"])
        self.assertEqual(enviar.call_args.kwargs["texto_personalizado"],
                         "Disculpa la confusión. Puedes continuar aquí.")
        self.assertEqual(enviar.call_args.kwargs["emisor_forzado"]["codigo"], "VS")
        self.assertFalse(enviar.call_args.kwargs["automatico"])

    def test_no_envia_fuera_de_ventana(self):
        estado, enviar = self.ejecutar(ventana=False)
        self.assertEqual(estado, 409)
        enviar.assert_not_called()

    def test_requiere_permiso_de_callcenter(self):
        estado, enviar = self.ejecutar(permitido=False)
        self.assertEqual(estado, 403)
        enviar.assert_not_called()

    def test_el_texto_personalizado_conserva_el_boton_crm(self):
        canal = MagicMock()
        canal.enviar.return_value = (True, "wamid.test", None)
        emisor = {"codigo": "VS", "phoneNumberId": "12345", "nombre": "Vida y Salud"}
        with patch.object(main, "esta_suprimido", return_value=False), \
             patch.object(main, "leer_config", return_value={}), \
             patch.object(main, "config_get", side_effect=lambda k: "56922222222" if k == "VS_CRM_PHONE_NUMBER" else ""), \
             patch.object(main, "obtener_canal", return_value=canal), \
             patch.object(main, "registrar_historial"), \
             patch.object(main, "_registrar_call_center_log"):
            resultado = main._despachar_call_center(
                {"id": 2, "nombre": "Paciente", "telefono": "+56911111111"},
                "produccion", texto_personalizado="Disculpa la confusión.",
                emisor_forzado=emisor, automatico=False)
        self.assertTrue(resultado["ok"])
        self.assertEqual(canal.enviar.call_args.args[1], "Disculpa la confusión.")
        cta = canal.enviar.call_args.kwargs["plantilla"]["cta"]
        self.assertEqual(cta["texto"], "Ir a call center")
        self.assertTrue(cta["url"].startswith("https://wa.me/56922222222?text="))

    def test_ventana_usa_fecha_del_paciente_y_no_fecha_de_reintento(self):
        conexion = MagicMock()
        cur = conexion.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.rowcount = 1
        emisor = {"codigo": "VS", "phoneNumberId": "12345", "phoneNumber": "+56922222222"}
        token = whatsapp_service.EMISOR_WEBHOOK.set(emisor)
        try:
            with patch.object(whatsapp_service, "conectar", return_value=conexion):
                self.assertTrue(main.WhatsAppService._guardar_mensaje_entrante(
                    {"id": "wamid.test", "type": "text", "timestamp": "1792611000"},
                    "+56911111111", "Hola"))
            self.assertEqual(cur.execute.call_args.args[1][-1], 1792611000)
            self.assertIn("FROM_UNIXTIME", cur.execute.call_args.args[0])
        finally:
            whatsapp_service.EMISOR_WEBHOOK.reset(token)


if __name__ == "__main__":
    unittest.main()
