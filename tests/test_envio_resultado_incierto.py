"""Un resultado incierto o una caída del limitador detienen el lote completo."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import main
from motor_envio import FalloInfraestructuraDespacho, ResultadoEnvioIncierto


class DetencionSeguraTests(unittest.TestCase):
    def _verificar_detencion(self, error):
        canal = MagicMock()
        canal.nombre = "api_oficial"
        canal.disponible.return_value = True
        canal.enviar.side_effect = error
        trabajo = {
            "ambiente": "desarrollo", "tabla_area": None, "area_id": None,
            "emisor": {"codigo": "SMPT", "phoneNumberId": "id-ficticio",
                       "phoneNumber": "+56900000001"},
            "plantilla": {"clave": "prueba"}, "envio_id": 123,
            "destinatarios": [
                {"id": 1, "nombre": "Uno", "telefono": "56900000001", "mensaje": "prueba"},
                {"id": 2, "nombre": "Dos", "telefono": "56900000002", "mensaje": "prueba"},
            ],
            "enviados": 0, "fallidos": 0, "errores": [], "estado": "en_proceso",
            "actual": "", "detalle": "", "pausado": False,
        }
        with patch.dict(main.JOBS, {"test_incierto": trabajo}), \
             patch.object(main, "leer_config", return_value={}), \
             patch.object(main, "_emisor_vigente", return_value=True), \
             patch.object(main, "obtener_canal", return_value=canal), \
             patch.object(main, "_restriccion_envio_desarrollo", return_value=None), \
             patch.object(main, "esta_suprimido", return_value=False), \
             patch.object(main, "_paciente_habilitado_antes_de_enviar", return_value=True), \
             patch.object(main, "actualizar_envio_batch") as batch, \
             patch.object(main, "actualizar_estado_paciente") as paciente, \
             patch.object(main, "registrar_historial") as historial, \
             patch.object(main, "log_error"), \
             patch.object(main.wa_gobernador, "pausa_antes_de_enviar", return_value=0):
            main._procesar_job("test_incierto")
        self.assertEqual(canal.enviar.call_count, 1)
        self.assertEqual(trabajo["estado"], "error")
        self.assertEqual(trabajo["enviados"], 0)
        self.assertEqual(trabajo["fallidos"], 0)
        paciente.assert_not_called()
        historial.assert_not_called()
        batch.assert_called_once_with(123, "desarrollo", estado="error")

    def test_no_avanza_tras_resultado_incierto(self):
        self._verificar_detencion(ResultadoEnvioIncierto("sin respuesta"))

    def test_no_avanza_si_limitador_no_puede_reservar(self):
        self._verificar_detencion(FalloInfraestructuraDespacho("base caída"))
