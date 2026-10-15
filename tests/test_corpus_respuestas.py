import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from corpus_respuestas import desidentificar_mensaje
from migrar_corpus_respuestas import migrar
from whatsapp_service import WhatsAppService


class CorpusRespuestasTests(unittest.TestCase):
    def test_conserva_intencion_sin_nombre_telefono_ni_clinica(self):
        texto = "Hola, soy Camila Rojas. Me interesa consultar por depresión; llámenme al +56 9 8765 4321."
        resultado = desidentificar_mensaje(texto)
        self.assertIn("me interesa consultar", resultado)
        self.assertIn("[dato]", resultado)
        for dato in ("camila", "rojas", "depresion", "8765", "4321"):
            self.assertNotIn(dato, resultado)

    def test_inserta_solo_texto_transformado(self):
        conexion = MagicMock()
        cur = conexion.__enter__.return_value.cursor.return_value.__enter__.return_value
        with patch("whatsapp_service.conectar", return_value=conexion):
            WhatsAppService._guardar_corpus_respuesta("Me interesa, soy Camila")
        consulta, parametros = cur.execute.call_args.args
        self.assertEqual(consulta,
                         "INSERT INTO corpus_respuestas (texto_desidentificado) VALUES (%s)")
        self.assertEqual(len(parametros), 1)
        self.assertNotIn("camila", parametros[0])
        conexion.__enter__.return_value.commit.assert_called_once()

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
        guardar.assert_called_once_with("")
        self.assertEqual(desidentificar_mensaje(""), "[sin_texto]")

    def test_migracion_historica_es_unica_y_excluye_eliminar(self):
        conexion = MagicMock()
        cur = conexion.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect = [None, {"valor": "5"}]
        cur.fetchall.side_effect = [
            [{"id": 1, "mensaje": "Hola Camila"}, {"id": 3, "mensaje": "ELIMINAR"}],
            [],
        ]
        with patch("migrar_corpus_respuestas.conectar", return_value=conexion):
            total = migrar()
        self.assertEqual(total, 1)
        corpus = [c for c in cur.execute.call_args_list
                  if c.args[0].startswith("INSERT INTO corpus_respuestas")]
        self.assertEqual(len(corpus), 1)
        self.assertNotIn("camila", corpus[0].args[1][0])
        conexion.__enter__.return_value.commit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
