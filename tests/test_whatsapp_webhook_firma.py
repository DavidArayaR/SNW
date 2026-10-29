import hashlib
import hmac
import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from starlette.requests import ClientDisconnect

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from main import app
from whatsapp_webhook import _recibir_evento, firma_meta_valida


class FirmaWebhookTests(unittest.TestCase):
    def setUp(self):
        self.cuerpo = json.dumps({"entry": [{"changes": []}]}).encode("utf-8")
        self.secreto = "secreto-de-prueba"
        self.firma = "sha256=" + hmac.new(
            self.secreto.encode("utf-8"), self.cuerpo, hashlib.sha256
        ).hexdigest()

    def test_firma_valida_depende_del_cuerpo_original(self):
        self.assertTrue(firma_meta_valida(self.cuerpo, self.firma, self.secreto))
        self.assertFalse(firma_meta_valida(self.cuerpo + b" ", self.firma, self.secreto))
        self.assertFalse(firma_meta_valida(self.cuerpo, "sha256=incorrecta", self.secreto))

    def test_cliente_desconectado_no_procesa_evento_incompleto(self):
        request = AsyncMock()
        request.body.side_effect = ClientDisconnect()
        with patch("whatsapp_webhook.handler.procesar") as procesar:
            respuesta = asyncio.run(_recibir_evento(request, secreto=self.secreto))
        self.assertEqual(respuesta.status_code, 204)
        procesar.assert_not_called()

    def test_sin_secreto_no_se_procesa(self):
        with patch.dict("os.environ", {"WA_APP_SECRET": self.secreto}), \
                patch("whatsapp_webhook.config_get", return_value="") as config_get, \
                patch("whatsapp_webhook.handler.procesar") as procesar:
            respuesta = TestClient(app).post(
                "/api/whatsapp/webhook", content=self.cuerpo,
                headers={"X-Hub-Signature-256": self.firma},
            )
        self.assertEqual(respuesta.status_code, 503)
        config_get.assert_called_once_with("wa_app_secret")
        procesar.assert_not_called()

    def test_firma_ausente_o_falsa_no_se_procesa(self):
        with patch("whatsapp_webhook.config_get", return_value=self.secreto), \
                patch("whatsapp_webhook.handler.procesar") as procesar:
            cliente = TestClient(app)
            for firma in ("", "sha256=" + "0" * 64, "sha256=mal"):
                respuesta = cliente.post(
                    "/api/whatsapp/webhook", content=self.cuerpo,
                    headers={"X-Hub-Signature-256": firma},
                )
                self.assertEqual(respuesta.status_code, 403)
        procesar.assert_not_called()

    def test_firma_valida_se_procesa_una_vez(self):
        with patch("whatsapp_webhook.config_get", return_value=self.secreto) as config_get, \
                patch("whatsapp_webhook.handler.procesar", return_value=["ok"]) as procesar:
            respuesta = TestClient(app).post(
                "/api/whatsapp/webhook", content=self.cuerpo,
                headers={"X-Hub-Signature-256": self.firma},
            )
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json()["acciones"], ["ok"])
        config_get.assert_called_once_with("wa_app_secret")
        procesar.assert_called_once_with(json.loads(self.cuerpo))


class WebhookPorClinicaTests(unittest.TestCase):
    def setUp(self):
        self.cliente = TestClient(app)
        self.config = {
            "wa_token": "token-de-prueba", "wa_verify_token": "token-global",
            "wa_app_secret": "secreto-global",
            "SMPT_WSP_PHONE_NUMBER": "+56911111111",
            "SMPT_WSP_PHONE_NUMBER_ID": "1111100001",
            "SMPT_WSP_WABA_ID": "2222200001",
            "SMPT_WSP_ACCESS_TOKEN": "token-smpt-acceso",
            "SMPT_WSP_VERIFY_TOKEN": "token-smpt",
            "SMPT_WSP_APP_SECRET": "secreto-smpt",
        }
        self.payload = {"entry": [{"id": "2222200001", "changes": [{
            "field": "messages", "value": {
                "metadata": {"phone_number_id": "1111100001"}, "messages": []},
        }]}]}

    def enviar(self, payload=None, *, secreto="secreto-smpt"):
        cuerpo = json.dumps(payload if payload is not None else self.payload).encode("utf-8")
        firma = "sha256=" + hmac.new(secreto.encode(), cuerpo, hashlib.sha256).hexdigest()
        return self.cliente.post("/api/whatsapp/webhook/SMPT", content=cuerpo,
                                 headers={"X-Hub-Signature-256": firma})

    def test_verificacion_usa_token_propio_y_rechaza_otros_centros(self):
        with patch("whatsapp_webhook.config_all", return_value=self.config):
            parametros = {"hub.mode": "subscribe", "hub.challenge": "reto"}
            correcta = self.cliente.get("/api/whatsapp/webhook/SMPT",
                                        params={**parametros, "hub.verify_token": "token-smpt"})
            global_incorrecta = self.cliente.get("/api/whatsapp/webhook/SMPT",
                                                 params={**parametros, "hub.verify_token": "token-global"})
            desconocido = self.cliente.get("/api/whatsapp/webhook/OTRO",
                                           params={**parametros, "hub.verify_token": "token-smpt"})
        self.assertEqual(correcta.status_code, 200)
        self.assertEqual(correcta.text, "reto")
        self.assertEqual(global_incorrecta.status_code, 403)
        self.assertEqual(desconocido.status_code, 404)

    def test_verificacion_no_hereda_token_global_si_no_hay_propio(self):
        config = {k: v for k, v in self.config.items() if k != "SMPT_WSP_VERIFY_TOKEN"}
        with patch("whatsapp_webhook.config_all", return_value=config):
            respuesta = self.cliente.get("/api/whatsapp/webhook/SMPT", params={
                "hub.mode": "subscribe", "hub.verify_token": "token-global", "hub.challenge": "reto"})
        self.assertEqual(respuesta.status_code, 403)

    def test_firma_propia_y_emisor_correcto_se_procesan(self):
        with patch("whatsapp_webhook.config_all", return_value=self.config), \
             patch("whatsapp_webhook.handler.procesar", return_value=["ok"]) as procesar:
            respuesta = self.enviar()
            firma_global = self.enviar(secreto="secreto-global")
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json()["acciones"], ["ok"])
        self.assertEqual(firma_global.status_code, 403)
        procesar.assert_called_once_with(self.payload)

    def test_waba_y_phone_id_ajenos_no_se_procesan(self):
        otra_waba = {"entry": [{**self.payload["entry"][0], "id": "9999900001"}]}
        otro_phone = {"entry": [{"id": "2222200001", "changes": [{
            "field": "messages", "value": {
                "metadata": {"phone_number_id": "9999900001"}, "messages": []},
        }]}]}
        with patch("whatsapp_webhook.config_all", return_value=self.config), \
             patch("whatsapp_webhook.handler.procesar") as procesar:
            respuestas = [self.enviar(otra_waba), self.enviar(otro_phone)]
        self.assertEqual([r.status_code for r in respuestas], [403, 403])
        procesar.assert_not_called()

    def test_secreto_global_es_respaldo_y_centro_desconocido_no_procesa(self):
        config = {k: v for k, v in self.config.items() if k != "SMPT_WSP_APP_SECRET"}
        with patch("whatsapp_webhook.config_all", return_value=config), \
             patch("whatsapp_webhook.handler.procesar", return_value=[]) as procesar:
            correcta = self.enviar(secreto="secreto-global")
            desconocido = self.cliente.post("/api/whatsapp/webhook/OTRO", json=self.payload)
        self.assertEqual(correcta.status_code, 200)
        self.assertEqual(desconocido.status_code, 404)
        procesar.assert_called_once()

    def test_clinica_pausada_recibe_webhook_y_eliminada_no(self):
        parametros = {"hub.mode": "subscribe", "hub.verify_token": "token-smpt",
                      "hub.challenge": "reto"}
        estado = {"codigo": "SMPT", "nombre": "Salud Mental Para Todos",
                  "activo": False, "eliminada": False}
        pausada = {**self.config, "wa_clinicas_adicionales": json.dumps([estado])}
        eliminada = {**self.config, "wa_clinicas_adicionales": json.dumps([
            {**estado, "eliminada": True}])}
        with patch("whatsapp_webhook.config_all", return_value=pausada):
            respuesta_pausada = self.cliente.get("/api/whatsapp/webhook/SMPT", params=parametros)
        with patch("whatsapp_webhook.config_all", return_value=eliminada):
            respuesta_eliminada = self.cliente.get("/api/whatsapp/webhook/SMPT", params=parametros)
        self.assertEqual(respuesta_pausada.status_code, 200)
        self.assertEqual(respuesta_eliminada.status_code, 404)


if __name__ == "__main__":
    unittest.main()
