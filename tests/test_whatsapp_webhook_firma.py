import hashlib
import hmac
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from main import app
from whatsapp_webhook import firma_meta_valida


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


if __name__ == "__main__":
    unittest.main()
