import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import plantilla_media
import main
from whatsapp_service import WhatsAppApiClient, WhatsAppService


class PlantillaMediaTests(unittest.TestCase):
    def setUp(self):
        self.temporal = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporal.cleanup)
        self.parche_dir = patch.object(plantilla_media, "MEDIA_DIR", Path(self.temporal.name))
        self.parche_dir.start()
        self.addCleanup(self.parche_dir.stop)

    def test_valida_tamano_y_firma(self):
        with self.assertRaises(HTTPException) as error:
            plantilla_media.guardar_media(b"x" * (plantilla_media.MAX_MEDIA_BYTES + 1), "david")
        self.assertEqual(error.exception.status_code, 413)
        with self.assertRaises(HTTPException) as error:
            plantilla_media.guardar_media(b"archivo falso", "david")
        self.assertEqual(error.exception.status_code, 415)

    def test_guarda_imagen_valida_y_exige_id_seguro(self):
        buffer = io.BytesIO()
        Image.new("RGB", (4, 4), "green").save(buffer, format="PNG")
        media = plantilla_media.guardar_media(buffer.getvalue(), "David")
        self.assertEqual(media["formato_meta"], "IMAGE")
        self.assertEqual(plantilla_media.obtener_media(media["id"])["creador"], "david")
        self.assertTrue(plantilla_media.ruta_media(media).is_file())
        with self.assertRaises(HTTPException):
            plantilla_media.obtener_media("../archivo")

    def test_descarga_encabezado_devuelve_archivo(self):
        buffer = io.BytesIO()
        Image.new("RGB", (4, 4), "green").save(buffer, format="PNG")
        media = plantilla_media.guardar_media(buffer.getvalue(), "David")
        with patch.object(main, "leer_plantillas", return_value=[]):
            respuesta = main.ver_encabezado_plantilla(
                media["id"], sesion={"usuario": "david", "rol": "administrador"})
        self.assertEqual(Path(respuesta.path), plantilla_media.ruta_media(media, vista=True))
        self.assertEqual(respuesta.media_type, "image/png")
        self.assertEqual(respuesta.headers["x-content-type-options"], "nosniff")

    def test_gif_se_convierte_a_video_para_meta(self):
        buffer = io.BytesIO()
        Image.new("RGB", (4, 4), "green").save(buffer, format="GIF")

        def simular_ffmpeg(argumentos, **_):
            Path(argumentos[-1]).write_bytes(b"video")
            return type("Proceso", (), {"returncode": 0})()

        with patch.object(plantilla_media, "_ejecutable_ffmpeg", return_value="ffmpeg"), \
             patch.object(plantilla_media.subprocess, "run", side_effect=simular_ffmpeg):
            media = plantilla_media.guardar_media(buffer.getvalue(), "David")
        self.assertEqual(media["formato_meta"], "VIDEO")
        self.assertEqual(media["mime_envio"], "video/mp4")
        self.assertTrue(plantilla_media.ruta_media(media).is_file())

    def test_template_meta_incluye_header_antes_del_body(self):
        servicio = WhatsAppService()
        with patch.object(WhatsAppService, "waba_id", "waba"), \
             patch.object(WhatsAppService, "token", "token"), \
             patch.object(servicio, "_componente_encabezado_meta", return_value={
                 "type": "HEADER", "format": "IMAGE", "example": {"header_handle": ["handle"]}}), \
             patch.object(WhatsAppApiClient, "crear_template", new_callable=AsyncMock,
                          return_value={"id": "tpl", "status": "PENDING"}) as crear:
            resultado = servicio.crear_template_meta("ejemplo", "Hola", media_id="id")
        self.assertTrue(resultado["ok"])
        componentes = crear.call_args.args[1]["components"]
        self.assertEqual([c["type"] for c in componentes[:2]], ["HEADER", "BODY"])

    def test_no_guarda_encabezado_sin_app_id(self):
        with patch.object(main, "config_get", return_value=""), \
             patch.object(main, "obtener_media") as obtener:
            with self.assertRaises(HTTPException) as error:
                main._validar_media_plantilla("id", {"usuario": "David", "rol": "administrador"})
        self.assertEqual(error.exception.status_code, 400)
        self.assertIn("App ID", error.exception.detail)
        obtener.assert_not_called()

    def test_error_de_meta_no_marca_encabezado_como_registrado(self):
        plantilla = {"nombre": "Default", "texto": "Hola", "whatsapp_template": "default",
                    "whatsapp_template_lang": "es", "whatsapp_template_categoria": "MARKETING",
                    "encabezado_media_id": "archivo"}
        with patch.object(main.WhatsAppService, "guardar_template_meta", return_value={
                "ok": False, "template_id": "123", "status": None, "error": "Sin permiso"}):
            resultado = main._registrar_template_meta(plantilla)
        self.assertFalse(resultado["encabezado_meta_registrado"])
        self.assertIsNone(resultado["whatsapp_template_status"])


class EnvioConMediaTests(unittest.IsolatedAsyncioTestCase):
    async def test_envio_adjunta_video_y_parametros_de_cuerpo(self):
        servicio = WhatsAppService()
        with patch("whatsapp_service.esta_suprimido", return_value=False), \
             patch.object(WhatsAppService, "_id_media_envio", new_callable=AsyncMock,
                          return_value=("media-meta", "video")), \
             patch.object(WhatsAppApiClient, "enviar", new_callable=AsyncMock,
                          return_value={"messages": [{"id": "wamid"}]}) as enviar, \
             patch("whatsapp_service.presentar_plantilla", return_value={"errores": []}), \
             patch("whatsapp_service.config_get", return_value="es"):
            resultado = await servicio.enviar(
                "+56912345678", "Hola David", {
                    "whatsapp_template": "hola", "texto": "Hola {nombre}",
                    "encabezado_media_id": "archivo", "encabezado_meta_registrado": True},
                {"nombre": "David"})
        self.assertTrue(resultado[0])
        componentes = enviar.call_args.args[0]["template"]["components"]
        self.assertEqual(componentes[0]["parameters"][0]["video"]["id"], "media-meta")
        self.assertEqual(componentes[1]["parameters"][0]["text"], "David")

    async def test_envio_real_no_usa_variable_vacia(self):
        servicio = WhatsAppService()
        with patch("whatsapp_service.esta_suprimido", return_value=False), \
             patch.object(WhatsAppApiClient, "enviar", new_callable=AsyncMock) as enviar:
            resultado = await servicio.enviar(
                "+56912345678", "Hola", {"whatsapp_template": "hola",
                "texto": "Hola {nombre}"}, {"nombre": ""})
        self.assertFalse(resultado[0])
        self.assertIn("Falta un ejemplo", resultado[2])
        enviar.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
