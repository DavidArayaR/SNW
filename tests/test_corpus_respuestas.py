import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from corpus_respuestas import desidentificar_mensaje
from corpus_respuestas import guardar_mensaje_completo
from whatsapp_service import WhatsAppService


class CorpusRespuestasTests(unittest.TestCase):
    def test_conserva_intencion_sin_nombre_telefono_ni_clinica(self):
        texto = "Hola, soy Camila Rojas. Me interesa consultar por depresión; llámenme al +56 9 8765 4321."
        resultado = desidentificar_mensaje(texto)
        self.assertIn("me interesa consultar", resultado)
        self.assertIn("[dato]", resultado)
        for dato in ("camila", "rojas", "depresion", "8765", "4321"):
            self.assertNotIn(dato, resultado)

    def test_inserta_texto_completo_con_vinculo_separado(self):
        conexion = MagicMock()
        cur = conexion.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.lastrowid = 18
        cur.fetchone.return_value = None
        with patch("whatsapp_service.conectar", return_value=conexion), \
             patch("supresion.huella_telefono", return_value="h" * 64):
            WhatsAppService._guardar_corpus_respuesta("Me interesa, soy Camila", "+56912345678")
        self.assertEqual(cur.execute.call_args_list[1].args,
                         ("INSERT INTO corpus_respuestas_completas (mensaje) VALUES (%s)",
                          ("Me interesa, soy Camila",)))
        self.assertEqual(cur.execute.call_args_list[2].args[1], (18, "h" * 64))
        conexion.__enter__.return_value.commit.assert_called_once()

    def test_fallo_del_vinculo_no_confirma_texto_huerfano(self):
        conexion = MagicMock()
        cur = conexion.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = None
        cur.execute.side_effect = [None, None, RuntimeError("fallo en vínculo")]
        with patch("whatsapp_service.conectar", return_value=conexion), \
             patch("supresion.huella_telefono", return_value="h" * 64), \
             patch("whatsapp_service.log_error"):
            WhatsAppService._guardar_corpus_respuesta("texto", "+56912345678")
        conexion.__enter__.return_value.commit.assert_not_called()

    def test_numero_suprimido_no_se_reincorpora_al_corpus(self):
        cur = MagicMock()
        cur.fetchone.return_value = {"estado": "pendiente"}
        with patch("supresion.huella_telefono", return_value="h" * 64):
            guardar_mensaje_completo(cur, "texto privado", "+56912345678")
        self.assertEqual(cur.execute.call_count, 1)

    def test_solicitud_eliminar_no_entra_al_corpus(self):
        servicio = WhatsAppService()
        valor = {"contacts": [{"wa_id": "56912345678"}],
                 "messages": [{"type": "text", "text": {"body": "ELIMINAR"}}]}
        with patch("whatsapp_service.esta_suprimido", return_value=False), \
             patch("whatsapp_service.es_exento", return_value=True), \
             patch("whatsapp_service.es_numero_prueba", return_value=False), \
             patch.object(servicio, "_guardar_corpus_respuesta") as guardar:
            servicio._procesar_mensajes(valor)
        guardar.assert_not_called()

    def test_multimedia_sin_texto_solo_deja_marcador(self):
        servicio = WhatsAppService()
        valor = {"contacts": [{"wa_id": "56912345678"}],
                 "messages": [{"type": "image", "image": {"id": "media_privada"}}]}
        with patch("whatsapp_service.esta_suprimido", return_value=False), \
             patch("whatsapp_service.es_exento", return_value=False), \
             patch("whatsapp_service.es_numero_prueba", return_value=False), \
             patch.object(servicio, "_guardar_corpus_respuesta") as guardar:
            servicio._procesar_mensajes(valor)
        guardar.assert_called_once_with("", "+56912345678")
        self.assertEqual(desidentificar_mensaje(""), "[sin_texto]")


if __name__ == "__main__":
    unittest.main()
