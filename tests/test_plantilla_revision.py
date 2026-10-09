import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import main
from plantilla_revision import componentes_para_vista, presentar_plantilla, variables_del_cuerpo
from routes import plantillas as rutas_plantillas
from whatsapp_service import WhatsAppApiClient


PLANTILLA = {
    "id": 7, "clave": "saludo", "nombre": "Saludo", "texto": "Hola {nombre}\n¿Cómo estás?",
    "whatsapp_template": "saludo", "whatsapp_template_status": "APPROVED",
    "aprobacion_estado": "aprobada", "area_id": None,
}
TELEFONO = "+56911112222"
SESION = {"rol": "administrador", "usuario": "admin@example.com"}


class PresentacionPlantillaTests(unittest.TestCase):
    def test_variables_en_orden_y_saltos(self):
        vista = presentar_plantilla({"texto": "Hola {apellido}, {nombre}\n{apellido}"},
                                   {"nombre": "Ana", "apellido": "Pérez"})
        self.assertEqual(vista["cuerpo"], "Hola Pérez, Ana\nPérez")
        self.assertEqual(vista["variables"], ["apellido", "nombre"])
        self.assertEqual(variables_del_cuerpo("{apellido} {nombre} {apellido}"),
                         ["apellido", "nombre"])
        self.assertEqual(len(vista["botones"]), 3)

    def test_variable_sin_ejemplo_y_desconocida(self):
        vista = presentar_plantilla({"texto": "Hola {nombre} {edad}"}, {"nombre": ""})
        self.assertTrue(any("Falta un ejemplo" in e for e in vista["errores"]))
        self.assertTrue(any("{edad}" in e for e in vista["errores"]))
        self.assertIn("sin ejemplo", vista["cuerpo"])

    def test_llaves_y_url_invalida(self):
        vista = presentar_plantilla({"texto": "Hola {nombre",
                                    "botones": [{"tipo": "url", "texto": "Sitio", "url": "javascript:alert(1)"}]})
        self.assertTrue(any("llaves" in e for e in vista["errores"]))
        self.assertTrue(any("enlace" in e for e in vista["errores"]))

    def test_componentes_importados_de_meta(self):
        partes = componentes_para_vista([
            {"type": "HEADER", "format": "TEXT", "text": "Salud Mental"},
            {"type": "FOOTER", "text": "Responde BAJA"},
            {"type": "BUTTONS", "buttons": [{"type": "QUICK_REPLY", "text": "Sí"},
                                              {"type": "URL", "text": "Abrir", "url": "https://example.org"}]},
        ])
        vista = presentar_plantilla({"texto": "Hola", **partes})
        self.assertEqual(vista["encabezado"]["texto"], "Salud Mental")
        self.assertEqual(vista["pie"], "Responde BAJA")
        self.assertEqual([b["texto"] for b in vista["botones"]], ["Sí", "Abrir"])
        self.assertFalse(vista["errores"])

    def test_multimedia_importado_sin_archivo_se_bloquea(self):
        vista = presentar_plantilla({"texto": "Hola", "whatsapp_header_format": "IMAGE"})
        self.assertTrue(any("falta el archivo" in e for e in vista["errores"]))

    def test_hello_world_no_inventa_botones(self):
        vista = presentar_plantilla({"texto": "Welcome", "whatsapp_template": "hello_world"})
        self.assertEqual(vista["botones"], [])

    def test_enlace_dinamico_no_soportado_se_advierte(self):
        vista = presentar_plantilla({"texto": "Hola", "botones": [{
            "tipo": "url", "texto": "Abrir", "url": "https://example.org/{{1}}"}]})
        self.assertTrue(any("parámetros" in e for e in vista["errores"]))

    def test_guardado_rechaza_variable_sin_ejemplo(self):
        with self.assertRaises(HTTPException) as error:
            main._exigir_revision_valida({"texto": "Hola {nombre}"}, {"nombre": ""})
        self.assertEqual(error.exception.status_code, 400)


class EstadoMetaPlantillaTests(unittest.TestCase):
    def test_consultas_meta_solicitan_quality_score(self):
        cliente = WhatsAppApiClient("token-test", "phone-test")
        with patch.object(cliente, "_peticion", new_callable=AsyncMock,
                          side_effect=[{"data": []}, {"data": []}]) as peticion:
            asyncio.run(cliente.listar_templates("waba-test"))
            asyncio.run(cliente.buscar_template("waba-test", "saludo"))

        self.assertEqual(peticion.await_count, 2)
        for llamada in peticion.await_args_list:
            self.assertIn("quality_score", llamada.kwargs["params"]["fields"])

    def test_paused_se_puede_editar_pero_no_programar(self):
        pausada = {**PLANTILLA, "whatsapp_template_status": "PAUSED"}
        self.assertIn("PAUSED", main.ESTADOS_TEMPLATE_EDITABLES)
        with self.assertRaises(HTTPException) as error:
            main._validar_plantilla_programable(pausada, None)
        self.assertIn("Meta pausó", error.exception.detail)
        self.assertIn("programar envíos", error.exception.detail)

        deshabilitada = {**PLANTILLA, "whatsapp_template_status": "DISABLED"}
        self.assertIn("DISABLED", main.ESTADOS_TEMPLATE_EDITABLES)
        with self.assertRaises(HTTPException) as error:
            main._validar_plantilla_programable(deshabilitada, None)
        self.assertIn("Meta deshabilitó", error.exception.detail)

    def test_rating_no_bloquea_envio_si_meta_mantiene_approved(self):
        for score in ("GREEN", "YELLOW", "RED", "UNKNOWN"):
            with self.subTest(score=score):
                plantilla = {
                    **PLANTILLA,
                    "whatsapp_template_status": "APPROVED",
                    "whatsapp_template_quality_score": {"score": score},
                }
                self.assertIs(main._validar_plantilla_programable(plantilla, None), plantilla)

    def test_sondeo_detecta_paused_y_detiene_lote_activo(self):
        plantilla = {
            **PLANTILLA,
            "whatsapp_template_id": "meta-7",
            "whatsapp_template_quality_score": {"score": "GREEN"},
        }
        job = {"estado": "en_proceso", "plantilla": dict(plantilla)}
        meta = {
            "id": "meta-7", "name": "saludo", "language": "es",
            "status": "PAUSED", "quality_score": {"score": "RED", "date": 1758754645},
        }
        with patch.object(main, "leer_plantillas", return_value=[plantilla]), \
                patch.object(main, "escribir_plantillas") as escribir, \
                patch.object(main.WhatsAppService, "listar_templates_meta",
                             return_value={"ok": True, "templates": [meta]}), \
                patch.object(main, "_programar_revision_plantillas"), \
                patch.dict(main.JOBS, {"job-paused-test": job}):
            main._revisar_plantillas_pendientes()

        self.assertEqual(plantilla["whatsapp_template_status"], "PAUSED")
        self.assertEqual(plantilla["whatsapp_template_quality_score"]["score"], "RED")
        self.assertTrue(job["cancelado"])
        self.assertIn("Meta pausó", job["cancelado_motivo"])
        escribir.assert_called_once_with([plantilla])


class EndpointPruebaPlantillaTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(rutas_plantillas.registrar(vars(main)))
        app.dependency_overrides[main.sesion_actual] = lambda: SESION
        self.cliente = TestClient(app)
        main._pruebas_plantilla_ultima.clear()
        main._pruebas_plantilla_en_curso.clear()

    def _config(self, clave, *args):
        return {"entorno": "desarrollo", "metodo_envio": "api_oficial"}.get(clave, "")

    def test_preview_muestra_errores_antes_del_envio(self):
        with patch.object(main, "leer_plantillas", return_value=[PLANTILLA]):
            respuesta = self.cliente.post("/api/plantillas/previsualizar", json={
                "plantilla_id": 7, "texto": "Hola {nombre} {edad}",
                "ejemplos": {"nombre": ""}})
        self.assertEqual(respuesta.status_code, 200)
        self.assertFalse(respuesta.json()["puede_enviar_prueba"])
        self.assertTrue(respuesta.json()["errores"])

    def test_destinatarios_son_solo_los_de_prueba_del_entorno(self):
        with patch.object(main, "config_get", side_effect=self._config), \
             patch.object(main, "_numeros_prueba_editables", return_value={TELEFONO}) as numeros:
            respuesta = self.cliente.get("/api/plantillas/prueba/destinatarios")
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json(), {"ambiente": "desarrollo", "telefonos": [TELEFONO]})
        numeros.assert_called_once_with("desarrollo")

    def test_prueba_unitaria_sin_campana_ni_bd(self):
        with patch.object(main, "leer_plantillas", return_value=[PLANTILLA]), \
             patch.object(main, "config_get", side_effect=self._config), \
             patch.object(main, "_numeros_prueba_editables", return_value={TELEFONO}), \
             patch.object(main.WhatsAppService, "configurada", return_value=True), \
             patch.object(main.WhatsAppService, "enviar", new_callable=AsyncMock,
                          return_value=(True, "wamid-prueba", None, "sent")) as enviar, \
             patch.object(main, "auditoria_registrar") as auditoria, \
             patch.object(main, "conectar", side_effect=AssertionError("No debe consultar campañas")):
            respuesta = self.cliente.post("/api/plantillas/7/prueba", json={
                "telefono": TELEFONO, "ejemplos": {"nombre": "Ana"}})
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json()["message_id"], "wamid-prueba")
        self.assertEqual(enviar.await_count, 1)
        self.assertEqual(enviar.await_args.kwargs["variables"]["nombre"], "Ana")
        self.assertTrue(enviar.await_args.kwargs["es_prueba"])
        auditoria.assert_called_once()

    def test_rechaza_destinatario_no_autorizado(self):
        with patch.object(main, "leer_plantillas", return_value=[PLANTILLA]), \
             patch.object(main, "config_get", side_effect=self._config), \
             patch.object(main, "_numeros_prueba_editables", return_value={TELEFONO}), \
             patch.object(main.WhatsAppService, "enviar", new_callable=AsyncMock) as enviar:
            respuesta = self.cliente.post("/api/plantillas/7/prueba", json={
                "telefono": "+56999998888", "ejemplos": {"nombre": "Ana"}})
        self.assertEqual(respuesta.status_code, 403)
        enviar.assert_not_awaited()

    def test_rechaza_plantilla_pendiente(self):
        pendiente = {**PLANTILLA, "whatsapp_template_status": "PENDING"}
        with patch.object(main, "leer_plantillas", return_value=[pendiente]), \
             patch.object(main.WhatsAppService, "enviar", new_callable=AsyncMock) as enviar:
            respuesta = self.cliente.post("/api/plantillas/7/prueba", json={
                "telefono": TELEFONO})
        self.assertEqual(respuesta.status_code, 400)
        enviar.assert_not_awaited()

    def test_limita_repeticiones_de_prueba(self):
        with patch.object(main, "leer_plantillas", return_value=[PLANTILLA]), \
             patch.object(main, "config_get", side_effect=self._config), \
             patch.object(main, "_numeros_prueba_editables", return_value={TELEFONO}), \
             patch.object(main.WhatsAppService, "configurada", return_value=True), \
             patch.object(main.WhatsAppService, "enviar", new_callable=AsyncMock,
                          return_value=(True, "wamid-prueba", None, "sent")) as enviar, \
             patch.object(main, "auditoria_registrar"):
            primera = self.cliente.post("/api/plantillas/7/prueba", json={"telefono": TELEFONO})
            segunda = self.cliente.post("/api/plantillas/7/prueba", json={"telefono": TELEFONO})
        self.assertEqual(primera.status_code, 200)
        self.assertEqual(segunda.status_code, 429)
        restantes = int(segunda.headers["Retry-After"])
        self.assertGreater(restantes, 0)
        self.assertLessEqual(restantes, 30)
        self.assertIn(f"Espera {restantes} segundos", segunda.json()["detail"])
        self.assertEqual(enviar.await_count, 1)

    def test_aviso_indica_tiempo_restante_y_singular(self):
        main._pruebas_plantilla_ultima[(7, TELEFONO)] = main.time.monotonic() - 29.5
        with patch.object(main, "leer_plantillas", return_value=[PLANTILLA]), \
             patch.object(main, "config_get", side_effect=self._config), \
             patch.object(main, "_numeros_prueba_editables", return_value={TELEFONO}), \
             patch.object(main.WhatsAppService, "configurada", return_value=True):
            respuesta = self.cliente.post("/api/plantillas/7/prueba", json={"telefono": TELEFONO})
        self.assertEqual(respuesta.status_code, 429)
        self.assertEqual(respuesta.headers["Retry-After"], "1")
        self.assertEqual(respuesta.json()["detail"], "Espera 1 segundo antes de repetir esta prueba.")

    def test_error_meta_es_comprensible_y_no_confunde_con_exito(self):
        with patch.object(main, "leer_plantillas", return_value=[PLANTILLA]), \
             patch.object(main, "config_get", side_effect=self._config), \
             patch.object(main, "_numeros_prueba_editables", return_value={TELEFONO}), \
             patch.object(main.WhatsAppService, "configurada", return_value=True), \
             patch.object(main.WhatsAppService, "enviar", new_callable=AsyncMock,
                          return_value=(False, None, "Plantilla no aprobada", "failed")), \
             patch.object(main, "auditoria_registrar") as auditoria, \
             patch.object(main, "log_error"):
            respuesta = self.cliente.post("/api/plantillas/7/prueba", json={"telefono": TELEFONO})
        self.assertEqual(respuesta.status_code, 502)
        self.assertIn("Plantilla no aprobada", respuesta.json()["detail"])
        auditoria.assert_not_called()


if __name__ == "__main__":
    unittest.main()
