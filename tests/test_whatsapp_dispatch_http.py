"""Reintentos del cliente HTTP con Meta completamente simulado."""

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from whatsapp_service import WhatsAppApiClient, ResultadoEnvioIncierto, WhatsAppService
from motor_envio import MotorApiOficial, FalloInfraestructuraDespacho


class Respuesta:
    def __init__(self, status, datos, headers=None):
        self.status_code = status
        self._datos = datos
        self.headers = headers or {}
        self.text = "respuesta simulada"

    def json(self):
        return self._datos


class ClienteSimulado:
    def __init__(self, respuestas):
        self.respuestas = iter(respuestas)
        self.llamadas = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def request(self, *_args, **_kwargs):
        self.llamadas += 1
        siguiente = next(self.respuestas)
        if isinstance(siguiente, Exception):
            raise siguiente
        return siguiente


class DespachoHttpTests(unittest.TestCase):
    def _ejecutar(self, respuestas):
        falso = ClienteSimulado(respuestas)
        reglas = {"max_retries": 2, "backoff_base": 2, "backoff_max": 60,
                  "pair_seconds": 6}
        with patch("whatsapp_service.httpx.AsyncClient", return_value=falso), \
             patch("whatsapp_service.politica_despacho", return_value=reglas), \
             patch("whatsapp_service.reservar_despacho", return_value=0), \
             patch("whatsapp_service.bloquear_despacho") as bloquear, \
             patch("whatsapp_service.aplazar_pareja") as pareja, \
             patch("whatsapp_service.registrar_aceptado") as aceptado, \
             patch("whatsapp_service.gobernador.pausa_antes_de_enviar", return_value=0), \
             patch("whatsapp_service.gobernador.registrar_respuesta"), \
             patch("whatsapp_service.asyncio.sleep", new_callable=AsyncMock):
            cliente = WhatsAppApiClient("token-ficticio", "1234500001")
            try:
                resultado = asyncio.run(cliente.enviar({"to": "56900000001"}))
            except Exception as error:
                resultado = error
        return resultado, falso.llamadas, bloquear, pareja, aceptado

    def test_130429_enfria_solo_el_emisor_y_reintenta(self):
        resultado, llamadas, bloquear, pareja, aceptado = self._ejecutar([
            Respuesta(400, {"error": {"code": 130429}}),
            Respuesta(200, {"messages": [{"id": "simulado"}]}),
        ])
        self.assertEqual(llamadas, 2)
        self.assertEqual(resultado["messages"][0]["id"], "simulado")
        self.assertEqual(bloquear.call_args.args[:2], ("1234500001", 130429))
        pareja.assert_not_called()
        aceptado.assert_called_once_with("1234500001")

    def test_131056_aplaza_solo_la_pareja(self):
        resultado, llamadas, bloquear, pareja, aceptado = self._ejecutar([
            Respuesta(400, {"error": {"code": 131056}}),
            Respuesta(200, {"messages": [{"id": "simulado"}]}),
        ])
        self.assertEqual(llamadas, 2)
        self.assertIsInstance(resultado, dict)
        pareja.assert_called_once()
        self.assertEqual(pareja.call_args.args[:2], ("1234500001", "56900000001"))
        bloquear.assert_not_called()
        aceptado.assert_called_once()

    def test_timeout_no_se_reenvia_automaticamente(self):
        resultado, llamadas, bloquear, pareja, aceptado = self._ejecutar([
            httpx.ReadTimeout("resultado desconocido"),
        ])
        self.assertIsInstance(resultado, httpx.ReadTimeout)
        self.assertEqual(llamadas, 1)
        bloquear.assert_not_called()
        pareja.assert_not_called()
        aceptado.assert_not_called()

    def test_motor_detiene_resultado_incierto_sin_reintentar(self):
        motor = MotorApiOficial.__new__(MotorApiOficial)
        motor.servicio = MagicMock()
        motor.servicio.enviar = AsyncMock(side_effect=httpx.ReadTimeout("respuesta desconocida"))
        with patch("motor_envio.esta_suprimido", return_value=False), \
             patch("motor_envio.log_error"):
            with self.assertRaises(ResultadoEnvioIncierto):
                motor.enviar("56900000001", "mensaje")
        self.assertEqual(motor.servicio.enviar.await_count, 1)

    def test_motor_detiene_lote_si_falla_el_limitador(self):
        motor = MotorApiOficial.__new__(MotorApiOficial)
        motor.servicio = MagicMock()
        motor.servicio.enviar = AsyncMock(side_effect=RuntimeError("base no disponible"))
        with patch("motor_envio.esta_suprimido", return_value=False), \
             patch("motor_envio.log_error"):
            with self.assertRaises(FalloInfraestructuraDespacho):
                motor.enviar("56900000001", "mensaje")
        self.assertEqual(motor.servicio.enviar.await_count, 1)

    def test_respuesta_positiva_sin_id_es_incierta(self):
        servicio = WhatsAppService.__new__(WhatsAppService)
        cliente = MagicMock()
        cliente.enviar = AsyncMock(return_value={"messages": [{}]})
        with patch("whatsapp_service.esta_suprimido", return_value=False), \
             patch.object(WhatsAppService, "cliente", new_callable=lambda: property(lambda _self: cliente)):
            with self.assertRaises(ResultadoEnvioIncierto):
                asyncio.run(servicio.enviar("56900000001", "mensaje"))
        self.assertEqual(cliente.enviar.await_count, 1)
