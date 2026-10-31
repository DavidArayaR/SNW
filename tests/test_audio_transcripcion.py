"""Audios: autorización, descarga acotada, caché y eliminación concurrente."""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import audio_transcripcion as audio
import main

FILA = {"mensaje_id": "wamid.audio", "wa_phone_id": "123456",
        "telefono_paciente": "+56911111111", "audio_media_id": "987654"}


class AudioTests(unittest.TestCase):
    def test_webhook_conserva_referencia_audio_sin_descargarlo(self):
        conn, cur = self.conexion()
        token = main.whatsapp_service.EMISOR_WEBHOOK.set({
            "codigo": "VS", "phoneNumberId": "123456", "phoneNumber": "+56922222222"})
        try:
            with patch.object(main.whatsapp_service, "conectar", return_value=conn):
                self.assertTrue(main.WhatsAppService._guardar_mensaje_entrante(
                    {"id": "wamid.audio", "type": "audio", "timestamp": "1792611000",
                     "audio": {"id": "987654", "mime_type": "audio/ogg"}},
                    FILA["telefono_paciente"], ""))
            self.assertEqual(cur.execute.call_args.args[1][-1], "987654")
        finally:
            main.whatsapp_service.EMISOR_WEBHOOK.reset(token)

    def conexion(self, fila=None):
        conn = MagicMock()
        cur = conn.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect = [{"adquirido": 1}, fila or FILA]
        cur.rowcount = 1
        return conn, cur

    def test_audio_ajeno_no_se_envia_a_groq(self):
        with patch.object(main.audio_transcripcion, "transcribir") as transcribir, \
             self.assertRaises(main.HTTPException) as error:
            main._transcribir_audio_visible({"mensajes": []},
                main.TranscribirAudioIn(mensaje_id="ajeno"), {"usuario": "dev"})
        self.assertEqual(error.exception.status_code, 404)
        transcribir.assert_not_called()

    def test_controladores_reutilizan_permisos_de_conversacion(self):
        with patch.object(main, "mensajes_paciente", side_effect=main.HTTPException(403)), \
             patch.object(main.audio_transcripcion, "transcribir") as transcribir, \
             self.assertRaises(main.HTTPException) as error:
            main.transcribir_audio_paciente(1, main.TranscribirAudioIn(mensaje_id="wamid.audio"),
                ambiente="produccion", area_id=None, sesion={"usuario": "sinpermiso"})
        self.assertEqual(error.exception.status_code, 403)
        transcribir.assert_not_called()

    def test_reutiliza_transcripcion_sin_descargar_ni_consumir_cuota(self):
        conn, cur = self.conexion({**FILA, "audio_transcripcion": "Texto guardado"})
        with patch.object(audio, "conectar", return_value=conn), \
             patch.object(audio, "descargar_audio") as descargar, \
             patch.object(audio, "consumir_cuota") as cuota:
            self.assertEqual(audio.transcribir("wamid.audio", "dev"), {"texto": "Texto guardado"})
        descargar.assert_not_called()
        cuota.assert_not_called()
        self.assertIn("RELEASE_LOCK", cur.execute.call_args.args[0])

    def test_descarga_rechaza_url_externa_y_archivos_grandes(self):
        for cambio, codigo in (({"url": "https://ejemplo.com/audio"}, 502),
                               ({"file_size": audio.MAX_AUDIO + 1}, 413)):
            cliente = MagicMock()
            cliente.get.return_value.status_code = 200
            cliente.get.return_value.json.return_value = {
                "url": "https://lookaside.fbsbx.com/audio", "mime_type": "audio/ogg", **cambio}
            with patch.object(audio, "credenciales_centro", return_value={"token": "secreto"}), \
                 self.assertRaises(main.HTTPException) as error:
                audio.descargar_audio(FILA, cliente)
            self.assertEqual(error.exception.status_code, codigo)
            cliente.stream.assert_not_called()

    def test_limite_real_de_descarga_aunque_metadata_mienta(self):
        cliente = MagicMock()
        cliente.get.return_value.status_code = 200
        cliente.get.return_value.json.return_value = {
            "url": "https://lookaside.fbsbx.com/audio", "mime_type": "audio/ogg"}
        descarga = cliente.stream.return_value.__enter__.return_value
        descarga.status_code = 200
        descarga.iter_bytes.return_value = [b"123", b"456"]
        with patch.object(audio, "MAX_AUDIO", 5), \
             patch.object(audio, "credenciales_centro", return_value={"token": "secreto"}), \
             self.assertRaises(main.HTTPException) as error:
            audio.descargar_audio(FILA, cliente)
        self.assertEqual(error.exception.status_code, 413)

    def test_transcribe_y_no_resucita_registro_eliminado(self):
        for existe in (True, False):
            conn, cur = self.conexion()
            cur.rowcount = int(existe)
            cliente = MagicMock()
            cliente.post.return_value.status_code = 200
            cliente.post.return_value.json.return_value = {"text": "Consulta del paciente"}
            with patch.object(audio, "conectar", return_value=conn), \
                 patch.object(audio, "leer_clave", return_value="clave"), \
                 patch.object(audio, "consumir_cuota"), \
                 patch.object(audio, "descargar_audio", return_value=(b"ogg", "audio/ogg", "ogg")), \
                 patch.object(audio.httpx, "Client") as client:
                client.return_value.__enter__.return_value = cliente
                if existe:
                    self.assertEqual(audio.transcribir("wamid.audio", "dev")["texto"], "Consulta del paciente")
                else:
                    with self.assertRaises(main.HTTPException) as error:
                        audio.transcribir("wamid.audio", "dev")
                    self.assertEqual(error.exception.status_code, 404)
            sql = [c.args[0] for c in cur.execute.call_args_list]
            self.assertFalse(any("INSERT" in s for s in sql))
            self.assertIn("RELEASE_LOCK", sql[-1])

    def test_doble_click_no_inicia_otra_transcripcion(self):
        conn, cur = self.conexion()
        cur.fetchone.side_effect = [{"adquirido": 0}]
        with patch.object(audio, "conectar", return_value=conn), \
             patch.object(audio, "leer_clave") as clave, \
             self.assertRaises(main.HTTPException) as error:
            audio.transcribir("wamid.audio", "dev")
        self.assertEqual(error.exception.status_code, 409)
        clave.assert_not_called()


if __name__ == "__main__":
    unittest.main()
