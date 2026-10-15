import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import groq_service as ia
import main
from routes import plantillas as rutas_plantillas


INSTRUCCION = "Recordatorio de cita, tono amable y breve"
SESION = {"rol": "administrador", "usuario": "admin@example.com"}


class ValidacionIATests(unittest.TestCase):
    def test_instruccion_vacia_larga_y_datos_evidentes(self):
        for texto in (" ", "x" * 401, "Llama al +56912345678", "Escribe a a@b.cl",
                      "Recordatorio para David Araya", "nombre: David",
                      "Recordatorio para paciente Juan Pérez con diagnóstico de depresión"):
            with self.subTest(texto=texto[:30]), self.assertRaises(HTTPException) as error:
                ia.validar_instruccion(texto)
            self.assertEqual(error.exception.status_code, 422)

    def test_borrador_y_variables(self):
        self.assertEqual(ia.validar_borrador("  Hola {{nombre}} {apellido}, confirma tu cita.  "),
                         "Hola {nombre} {apellido},\n\nconfirma tu cita.")
        self.assertEqual(ia.validar_borrador("Hola {nombre},\n\nRevisa la promoción."),
                         "Hola {nombre},\n\nRevisa la promoción.")
        self.assertEqual(ia.validar_borrador("Hola {nombre},\n\nTenemos un beneficio para ti 💚"),
                         "Hola {nombre},\n\nTenemos un beneficio para ti 💚")
        promocion = ("Hola {nombre}\n\nCuidar de ti es lo más importante. "
                     "Por eso, hoy queremos ofrecerte un 20% de descuento en tus próximas "
                     "atenciones de salud mental. Escríbenos para aprovechar este beneficio.")
        self.assertEqual(ia.validar_borrador(promocion), promocion)
        self.assertFalse(ia.contiene_datos_sensibles(promocion))
        self.assertTrue(ia.contiene_datos_sensibles("Hola David Araya, tenemos una promoción."))
        for texto in ("", "Hola {edad}", "Hola {nombre", "https://ejemplo.com", "Hola Ana Pérez", "x" * 1025):
            with self.subTest(texto=texto[:20]), self.assertRaises(HTTPException) as error:
                ia.validar_borrador(texto)
            self.assertEqual(error.exception.status_code, 502)

    def test_clave_en_bd_y_valores_heredados(self):
        with patch.object(ia, "config_get", return_value=""):
            with self.assertRaises(HTTPException) as error:
                ia.leer_clave()
            self.assertEqual(error.exception.status_code, 503)
        with patch.object(ia, "config_get", return_value="clave-de-prueba"):
            self.assertEqual(ia.leer_clave(), "clave-de-prueba")
        with patch.object(ia, "config_get", return_value="dpapi:cifrada"):
            with self.assertRaises(HTTPException) as error:
                ia.leer_clave()
            self.assertEqual(error.exception.status_code, 503)

    def test_cuota_compartida_por_bd(self):
        cursor = MagicMock()
        cursor.fetchone.side_effect = [{"solicitudes": 1}, {"solicitudes": 2}, {"solicitudes": 3}]
        conexion = MagicMock()
        conexion.__enter__.return_value = conexion
        conexion.cursor.return_value.__enter__.return_value = cursor
        with patch.object(ia, "conectar", return_value=conexion), \
             patch.object(ia, "config_get", return_value="2"), \
             patch.object(ia.time, "time", return_value=3601):
            ia.consumir_cuota("admin@example.com")
            ia.consumir_cuota("admin@example.com")
            with self.assertRaises(HTTPException) as error:
                ia.consumir_cuota("admin@example.com")
        self.assertEqual(error.exception.status_code, 429)
        self.assertEqual(cursor.execute.call_count, 6)
        self.assertTrue(all("groq_cuotas" in c.args[0] for c in cursor.execute.call_args_list))

    def test_cuota_distingue_usuario_y_ventana(self):
        cursor = MagicMock()
        cursor.fetchone.side_effect = [{"solicitudes": 1}] * 3
        conexion = MagicMock()
        conexion.__enter__.return_value = conexion
        conexion.cursor.return_value.__enter__.return_value = cursor
        with patch.object(ia, "conectar", return_value=conexion), \
             patch.object(ia, "config_get", return_value="2"), \
             patch.object(ia.time, "time", side_effect=[3601, 3601, 7201]):
            ia.consumir_cuota("uno@example.com")
            ia.consumir_cuota("dos@example.com")
            ia.consumir_cuota("uno@example.com")
        inserciones = [c.args[1] for c in cursor.execute.call_args_list if "INSERT INTO" in c.args[0]]
        self.assertNotEqual(inserciones[0][0], inserciones[1][0])
        self.assertEqual(inserciones[0][0], inserciones[2][0])
        self.assertNotEqual(inserciones[0][1], inserciones[2][1])


class ProveedorIATests(unittest.IsolatedAsyncioTestCase):
    async def _llamar(self, respuesta=None, error=None):
        cliente = AsyncMock()
        if error:
            cliente.post.side_effect = error
        else:
            cliente.post.return_value = respuesta
        contexto = MagicMock()
        contexto.__aenter__ = AsyncMock(return_value=cliente)
        contexto.__aexit__ = AsyncMock(return_value=None)
        with patch.object(ia, "leer_clave", return_value="clave-falsa"), \
             patch.object(ia.httpx, "AsyncClient", return_value=contexto):
            resultado = await ia.generar_borrador(INSTRUCCION)
        return resultado, cliente

    async def test_exito_no_expone_clave_ni_envia_pacientes(self):
        respuesta = MagicMock(status_code=200)
        respuesta.json.return_value = {"choices": [{"message": {
            "content": "Hola {nombre} {apellido}, te recordamos tu cita."}, "finish_reason": "stop"}]}
        borrador, cliente = await self._llamar(respuesta)
        self.assertIn("{nombre}", borrador)
        argumentos = cliente.post.call_args
        self.assertEqual(argumentos.args[0], ia.URL_GROQ)
        self.assertEqual(argumentos.kwargs["headers"]["Authorization"], "Bearer clave-falsa")
        self.assertNotIn("clave-falsa", str(argumentos.kwargs["json"]))
        self.assertNotIn("David", str(argumentos.kwargs["json"]))
        self.assertIn("emojis", argumentos.kwargs["json"]["messages"][0]["content"])
        self.assertIn("responder exactamente «Me interesa»", argumentos.kwargs["json"]["messages"][0]["content"])
        self.assertEqual(argumentos.kwargs["json"]["model"], ia.MODELO)
        self.assertEqual(argumentos.kwargs["json"]["max_completion_tokens"], 1536)
        self.assertEqual(argumentos.kwargs["json"]["reasoning_effort"], "low")

    async def test_timeout_y_cuota(self):
        with self.assertRaises(HTTPException) as error:
            await self._llamar(error=httpx.ReadTimeout("timeout"))
        self.assertEqual(error.exception.status_code, 504)
        with self.assertRaises(HTTPException) as error:
            await self._llamar(MagicMock(status_code=429))
        self.assertEqual(error.exception.status_code, 429)

    async def test_clave_y_modelo_invalidos_no_exponen_el_secreto(self):
        for estado, fragmento in ((401, "clave"), (403, "clave"), (404, "modelo")):
            with self.subTest(estado=estado), self.assertRaises(HTTPException) as error:
                await self._llamar(MagicMock(status_code=estado))
            self.assertEqual(error.exception.status_code, 503)
            self.assertIn(fragmento, error.exception.detail.lower())
            self.assertNotIn("clave-falsa", error.exception.detail)

    async def test_modelo_saturado_usa_respaldo(self):
        saturado = MagicMock(status_code=503)
        respaldo = MagicMock(status_code=200)
        respaldo.json.return_value = {"choices": [{"message": {
            "content": "Hola {nombre}, tenemos una promoción para ti."}, "finish_reason": "stop"}]}
        cliente = AsyncMock()
        cliente.post.side_effect = [saturado, respaldo]
        contexto = MagicMock()
        contexto.__aenter__ = AsyncMock(return_value=cliente)
        contexto.__aexit__ = AsyncMock(return_value=None)
        with patch.object(ia, "leer_clave", return_value="clave-falsa"), \
             patch.object(ia.httpx, "AsyncClient", return_value=contexto):
            borrador = await ia.generar_borrador("Promoción general")
        self.assertIn("promoción", borrador)
        self.assertEqual(cliente.post.await_count, 2)
        self.assertEqual(cliente.post.await_args_list[0].kwargs["json"]["model"], ia.MODELO)
        self.assertEqual(cliente.post.await_args_list[1].kwargs["json"]["model"], ia.MODELO_RESPALDO)

    async def test_no_muestra_respuesta_cortada_por_tokens(self):
        respuesta = MagicMock(status_code=200)
        respuesta.json.return_value = {"choices": [{
            "finish_reason": "length",
            "message": {"content": "Hola {nombre},\n\nCuidar de tu bienestar emocional"},
        }]}
        with self.assertRaises(HTTPException) as error:
            await self._llamar(respuesta)
        self.assertEqual(error.exception.status_code, 502)
        self.assertIn("no terminó", error.exception.detail)

    async def test_saturacion_persistente_muestra_causa(self):
        cliente = AsyncMock()
        cliente.post.side_effect = [MagicMock(status_code=503), MagicMock(status_code=503)]
        contexto = MagicMock()
        contexto.__aenter__ = AsyncMock(return_value=cliente)
        contexto.__aexit__ = AsyncMock(return_value=None)
        with patch.object(ia, "leer_clave", return_value="clave-falsa"), \
             patch.object(ia.httpx, "AsyncClient", return_value=contexto), \
             self.assertRaises(HTTPException) as error:
            await ia.generar_borrador("Promoción general")
        self.assertEqual(error.exception.status_code, 503)
        self.assertIn("saturado", error.exception.detail)

    async def test_respuesta_invalida(self):
        respuesta = MagicMock(status_code=200)
        respuesta.json.return_value = {"choices": []}
        with self.assertRaises(HTTPException) as error:
            await self._llamar(respuesta)
        self.assertEqual(error.exception.status_code, 502)


class EndpointIATests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(rutas_plantillas.registrar(vars(main)))
        self.app = app
        self.cliente = TestClient(app)

    def test_sin_autorizacion(self):
        with patch.object(main, "generar_borrador", new_callable=AsyncMock) as proveedor:
            respuesta = self.cliente.post("/api/plantillas/generar-con-ia", json={"instruccion": INSTRUCCION})
        self.assertEqual(respuesta.status_code, 401)
        proveedor.assert_not_awaited()

    def test_sin_permiso_de_edicion(self):
        self.app.dependency_overrides[main.sesion_actual] = lambda: {
            "rol": "usuario", "usuario": "lector@example.com", "permisos": ["mensajeria"]}
        with patch.object(main, "generar_borrador", new_callable=AsyncMock) as proveedor:
            respuesta = self.cliente.post("/api/plantillas/generar-con-ia", json={"instruccion": INSTRUCCION})
        self.assertEqual(respuesta.status_code, 403)
        proveedor.assert_not_awaited()

    def test_exito_no_guarda_ni_aprueba(self):
        self.app.dependency_overrides[main.sesion_actual] = lambda: SESION
        with patch.object(main, "consumir_cuota") as cuota, \
             patch.object(main, "generar_borrador", new_callable=AsyncMock,
                          return_value="Hola {nombre}, te recordamos tu cita.") as proveedor, \
             patch.object(main, "escribir_plantillas") as guardar, \
             patch.object(main, "_registrar_template_meta") as meta:
            respuesta = self.cliente.post("/api/plantillas/generar-con-ia", json={"instruccion": INSTRUCCION})
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json(), {"borrador": "Hola {nombre}, te recordamos tu cita."})
        cuota.assert_called_once_with(SESION["usuario"])
        proveedor.assert_awaited_once_with(INSTRUCCION)
        guardar.assert_not_called()
        meta.assert_not_called()

    def test_configuracion_guarda_y_muestra_la_clave(self):
        with patch.object(main, "config_set") as guardar, \
             patch.object(main, "config_all", return_value={"groq_api_key": "clave-de-prueba"}), \
             patch.object(main, "config_get", return_value="clave-de-prueba"):
            respuesta = main.actualizar_configuracion_completa(
                main.ConfigTodoIn(cambios={"groq_api_key": "clave-de-prueba"}),
                sesion={"rol": "desarrollador"})
        self.assertEqual(guardar.call_args.args[0]["groq_api_key"], "clave-de-prueba")
        self.assertEqual(respuesta["valores"]["groq_api_key"], "clave-de-prueba")
        self.assertTrue(respuesta["groq_api_key_configurada"])
        self.assertEqual(main.CONFIG_DEFAULTS["groq_limite_hora"], "12")

    def test_configuracion_muestra_credenciales_a_desarrolladores(self):
        with patch.object(main, "config_all", return_value={
            "wa_token": "meta-prueba", "wa_verify_token": "verify-prueba",
            "wa_app_secret": "app-prueba", "smtp_pass": "correo-prueba",
            "groq_api_key": "groq-prueba",
        }):
            values = main._config_valores()
        self.assertEqual(values["wa_token"], "meta-prueba")
        self.assertEqual(values["groq_api_key"], "groq-prueba")


if __name__ == "__main__":
    unittest.main()
