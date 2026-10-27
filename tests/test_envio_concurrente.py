"""Regresiones del despacho concurrente sin llamadas reales a Meta."""

import asyncio
import copy
import sys
import threading
import time
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import main
import whatsapp_service
from motor_envio import MotorApiOficial, ResultadoEnvioIncierto


class MotorFalso(MotorApiOficial):
    def __init__(self, enviar):
        self.servicio = SimpleNamespace(cliente_compartido_envio=lambda continuar=None: nullcontext())
        self._enviar = enviar

    def disponible(self):
        return True

    def enviar(self, telefono, mensaje, plantilla=None, variables=None):
        return self._enviar(telefono)


class DespachoConcurrenteTests(unittest.TestCase):
    def preparar(self, enviar, cantidad=10):
        job = {
            "ambiente": "desarrollo", "tabla_area": None, "area_id": None,
            "emisor": {"codigo": "SMPT", "phoneNumberId": "id-prueba-concurrente",
                       "phoneNumber": "+56900000001"},
            "plantilla": {"clave": "prueba"}, "envio_id": 123,
            "destinatarios": [
                {"id": i, "nombre": f"Paciente {i}", "telefono": f"5690000{i:04d}",
                 "mensaje": "prueba"} for i in range(cantidad)
            ],
            "enviados": 0, "fallidos": 0, "errores": [], "estado": "en_proceso",
            "actual": "", "detalle": "", "pausado": False,
        }
        canal = MotorFalso(enviar)
        parches = [
            patch.dict(main.JOBS, {"test_concurrente": job}),
            patch.object(main, "leer_config", return_value={"metodo_envio": "api_oficial"}),
            patch.object(main, "_emisor_vigente", return_value=True),
            patch.object(main, "obtener_canal", return_value=canal),
            patch.object(main, "_restriccion_envio_desarrollo", return_value=None),
            patch.object(main, "esta_suprimido", return_value=False),
            patch.object(main, "_paciente_habilitado_antes_de_enviar", return_value=True),
            patch.object(main, "actualizar_envio_batch"),
            patch.object(main, "actualizar_estado_paciente"),
            patch.object(main, "limpiar_interes_paciente"),
            patch.object(main, "registrar_historial"),
            patch.object(main, "log_error"),
        ]
        mocks = [p.start() for p in parches]
        self.addCleanup(lambda: [p.stop() for p in reversed(parches)])
        return job, mocks

    def test_limita_solicitudes_por_numero_y_registra_todos(self):
        activos = maximos = 0
        lock = threading.Lock()

        def enviar(telefono):
            nonlocal activos, maximos
            with lock:
                activos += 1
                maximos = max(maximos, activos)
            time.sleep(0.025)
            with lock:
                activos -= 1
            return True, f"wamid-{telefono}", None

        job, mocks = self.preparar(enviar)
        main._procesar_job("test_concurrente")
        self.assertEqual(job["enviados"], 10)
        self.assertEqual(job["estado"], "completado")
        self.assertGreater(maximos, 1)
        self.assertLessEqual(maximos, main.MAX_SOLICITUDES_SIMULTANEAS_POR_NUMERO)
        self.assertEqual(mocks[10].call_count, 10)  # historial

    def test_dos_campanas_del_mismo_numero_comparten_cupo(self):
        activos = maximos = 0
        lock = threading.Lock()

        def enviar(telefono):
            nonlocal activos, maximos
            with lock:
                activos += 1
                maximos = max(maximos, activos)
            time.sleep(0.035)
            with lock:
                activos -= 1
            return True, f"wamid-{telefono}", None

        job, _ = self.preparar(enviar)
        otro = copy.deepcopy(job)
        otro["envio_id"] = 124
        main.JOBS["test_concurrente_2"] = otro
        self.addCleanup(lambda: main.JOBS.pop("test_concurrente_2", None))
        hilos = [threading.Thread(target=main._procesar_job, args=(id_job,))
                 for id_job in ("test_concurrente", "test_concurrente_2")]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join(5)
            self.assertFalse(hilo.is_alive())
        self.assertEqual(job["enviados"], 10)
        self.assertEqual(otro["enviados"], 10)
        self.assertLessEqual(maximos, main.MAX_SOLICITUDES_SIMULTANEAS_POR_NUMERO)

    def test_resultado_incierto_detiene_nuevos_y_registra_los_en_vuelo(self):
        llamados = []
        lock = threading.Lock()

        def enviar(telefono):
            with lock:
                llamados.append(telefono)
            if telefono.endswith("0000"):
                raise ResultadoEnvioIncierto("sin confirmación")
            time.sleep(0.05)
            return True, f"wamid-{telefono}", None

        job, mocks = self.preparar(enviar)
        main._procesar_job("test_concurrente")
        self.assertEqual(job["estado"], "error")
        self.assertLessEqual(len(llamados), main.MAX_SOLICITUDES_SIMULTANEAS_POR_NUMERO)
        self.assertEqual(len(set(llamados)), len(llamados))
        self.assertEqual(mocks[10].call_count, job["enviados"])

    def test_pausa_frena_nuevos_y_reanudar_completa(self):
        comenzaron = threading.Event()
        soltar = threading.Event()
        llamados = []
        lock = threading.Lock()

        def enviar(telefono):
            with lock:
                llamados.append(telefono)
                if len(llamados) == main.MAX_SOLICITUDES_SIMULTANEAS_POR_NUMERO:
                    comenzaron.set()
            soltar.wait(2)
            return True, f"wamid-{telefono}", None

        job, _ = self.preparar(enviar)
        hilo = threading.Thread(target=main._procesar_job, args=("test_concurrente",))
        hilo.start()
        try:
            self.assertTrue(comenzaron.wait(2))
            job["pausado"] = True
            soltar.set()
            time.sleep(0.3)
            self.assertEqual(len(llamados), main.MAX_SOLICITUDES_SIMULTANEAS_POR_NUMERO)
            job["pausado"] = False
            hilo.join(3)
            self.assertFalse(hilo.is_alive())
            self.assertEqual(job["enviados"], 10)
        finally:
            job["pausado"] = False
            soltar.set()
            hilo.join(3)

    def test_cancelar_no_inicia_mas_y_conserva_resultados_en_vuelo(self):
        comenzaron = threading.Event()
        soltar = threading.Event()
        llamados = []
        lock = threading.Lock()

        def enviar(telefono):
            with lock:
                llamados.append(telefono)
                if len(llamados) == main.MAX_SOLICITUDES_SIMULTANEAS_POR_NUMERO:
                    comenzaron.set()
            soltar.wait(2)
            return True, f"wamid-{telefono}", None

        job, _ = self.preparar(enviar)
        hilo = threading.Thread(target=main._procesar_job, args=("test_concurrente",))
        hilo.start()
        try:
            self.assertTrue(comenzaron.wait(2))
            job["cancelado"] = True
            soltar.set()
            hilo.join(3)
            self.assertFalse(hilo.is_alive())
            self.assertEqual(len(llamados), main.MAX_SOLICITUDES_SIMULTANEAS_POR_NUMERO)
            self.assertEqual(job["enviados"], len(llamados))
            self.assertEqual(job["estado"], "cancelado")
        finally:
            job["cancelado"] = True
            soltar.set()
            hilo.join(3)


class ClienteCompartidoTests(unittest.TestCase):
    def test_cancelacion_antes_de_http_no_envia(self):
        cliente = MagicMock()
        api = whatsapp_service.WhatsAppApiClient(
            "token-prueba", "id-prueba", cliente, continuar_envio=lambda: False)
        with patch.object(whatsapp_service, "politica_despacho",
                          return_value={"max_retries": 0}):
            with self.assertRaises(whatsapp_service.EnvioInterrumpido):
                asyncio.run(api._peticion("POST", "https://example.test/messages",
                                         json={"to": "56900000000"}, throughput=True))
        cliente.request.assert_not_called()

    def test_reutiliza_cliente_y_no_abre_un_asyncclient_por_mensaje(self):
        cliente = MagicMock()
        respuesta = MagicMock(status_code=200, headers={})
        respuesta.json.return_value = {"ok": True}
        cliente.request.return_value = respuesta
        api = whatsapp_service.WhatsAppApiClient("token-prueba", "id-prueba", cliente)
        with patch.object(whatsapp_service.httpx, "AsyncClient") as nuevo, \
             patch.object(whatsapp_service.gobernador, "pausa_antes_de_enviar", return_value=0), \
             patch.object(whatsapp_service.gobernador, "registrar_respuesta"):
            asyncio.run(api._peticion("GET", "https://example.test/uno"))
            asyncio.run(api._peticion("GET", "https://example.test/dos"))
        self.assertEqual(cliente.request.call_count, 2)
        nuevo.assert_not_called()


if __name__ == "__main__":
    unittest.main()
