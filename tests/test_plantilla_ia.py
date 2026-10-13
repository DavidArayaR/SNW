import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import gemini_service as ia
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
        for texto in ("", "Hola {edad}", "Hola {nombre", "https://ejemplo.com", "Hola Ana Pérez", "x" * 1025):
            with self.subTest(texto=texto[:20]), self.assertRaises(HTTPException) as error:
                ia.validar_borrador(texto)
            self.assertEqual(error.exception.status_code, 502)

    def test_clave_ausente_y_no_cifrada(self):
        for valor in ("", "clave-en-plano"):
            with patch.object(ia, "config_get", return_value=valor), self.assertRaises(HTTPException) as error:
                ia.leer_clave()
            self.assertEqual(error.exception.status_code, 503)
            if valor:
                self.assertNotIn(valor, error.exception.detail)

    @unittest.skipUnless(sys.platform == "win32", "DPAPI solo está disponible en Windows")
    def test_clave_cifrada_con_cuenta_windows(self):
        cifrada = ia.cifrar_clave("clave-de-prueba")
        self.assertTrue(cifrada.startswith("dpapi:"))
        self.assertNotIn("clave-de-prueba", cifrada)
        with patch.object(ia, "config_get", return_value=cifrada):
            self.assertEqual(ia.leer_clave(), "clave-de-prueba")

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
        respuesta.json.return_value = {"candidates": [{"content": {"parts": [
            {"text": "Hola {nombre} {apellido}, te recordamos tu cita."}]}}]}
        borrador, cliente = await self._llamar(respuesta)
        self.assertIn("{nombre}", borrador)
        argumentos = cliente.post.call_args
        self.assertEqual(argumentos.kwargs["headers"]["x-goog-api-key"], "clave-falsa")
        self.assertNotIn("clave-falsa", str(argumentos.kwargs["json"]))
        self.assertNotIn("David", str(argumentos.kwargs["json"]))

    async def test_timeout_y_cuota(self):
        with self.assertRaises(HTTPException) as error:
            await self._llamar(error=httpx.ReadTimeout("timeout"))
        self.assertEqual(error.exception.status_code, 504)
        with self.assertRaises(HTTPException) as error:
            await self._llamar(MagicMock(status_code=429))
        self.assertEqual(error.exception.status_code, 429)

    async def test_modelo_saturado_usa_respaldo(self):
        saturado = MagicMock(status_code=503)
        respaldo = MagicMock(status_code=200)
        respaldo.json.return_value = {"candidates": [{"content": {"parts": [
            {"text": "Hola {nombre}, tenemos una promoción para ti."}]}}]}
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
        self.assertIn(ia.MODELO, cliente.post.await_args_list[0].args[0])
        self.assertIn(ia.MODELO_RESPALDO, cliente.post.await_args_list[1].args[0])

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
        respuesta.json.return_value = {"candidates": []}
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

    @unittest.skipUnless(sys.platform == "win32", "DPAPI solo está disponible en Windows")
    def test_configuracion_cifra_y_oculta_la_clave(self):
        with patch.object(main, "config_set") as guardar, \
             patch.object(main, "config_all", return_value={"gemini_api_key": "dpapi:cifrada"}), \
             patch.object(main, "config_get", return_value="dpapi:cifrada"):
            respuesta = main.actualizar_configuracion_completa(
                main.ConfigTodoIn(cambios={"gemini_api_key": "clave-de-prueba"}),
                sesion={"rol": "desarrollador"})
        cifrada = guardar.call_args.args[0]["gemini_api_key"]
        self.assertTrue(cifrada.startswith("dpapi:"))
        self.assertNotIn("clave-de-prueba", cifrada)
        self.assertEqual(respuesta["valores"]["gemini_api_key"], "")
        self.assertTrue(respuesta["gemini_api_key_configurada"])


if __name__ == "__main__":
    unittest.main()
