import asyncio
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import main
import plantilla_media
from plantilla_revision import botones_para_meta, componentes_para_vista, presentar_plantilla, variables_del_cuerpo
from routes import plantillas as rutas_plantillas
from whatsapp_service import WhatsAppApiClient, WhatsAppService


PLANTILLA = {
    "id": 7, "clave": "saludo", "nombre": "Saludo", "texto": "Hola {nombre}\n¿Cómo estás?",
    "whatsapp_template": "saludo", "whatsapp_template_status": "APPROVED",
    "aprobacion_estado": "aprobada", "area_id": None, "centro_codigo": "SMPT",
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
                                              {"type": "URL", "text": "Abrir", "url": "https://example.org"},
                                              {"type": "QUICK_REPLY", "text": "Dar de baja"}]},
        ])
        vista = presentar_plantilla({"texto": "Hola", **partes})
        self.assertEqual(vista["encabezado"]["texto"], "Salud Mental")
        self.assertEqual(vista["pie"], "Responde BAJA")
        self.assertEqual([b["texto"] for b in vista["botones"]], ["Sí", "Abrir", "Dar de baja"])
        self.assertFalse(vista["errores"])

    def test_multimedia_importado_sin_archivo_se_bloquea(self):
        vista = presentar_plantilla({"texto": "Hola", "whatsapp_header_format": "IMAGE"})
        self.assertTrue(any("falta el archivo" in e for e in vista["errores"]))

    def test_hello_world_no_inventa_botones(self):
        vista = presentar_plantilla({"texto": "Welcome", "whatsapp_template": "hello_world"})
        self.assertEqual(vista["botones"], [])

    def test_baja_es_obligatoria_y_debe_ser_respuesta_rapida(self):
        for botones in ([], [{"tipo": "url", "texto": "Baja", "url": "https://example.org"}],
                        [{"tipo": "respuesta", "texto": "Salir", "accion": "ninguna"}]):
            with self.subTest(botones=botones):
                vista = presentar_plantilla({"texto": "Hola", "botones": botones})
                self.assertTrue(any("acción Baja" in e for e in vista["errores"]))
                with self.assertRaises(HTTPException) as error:
                    main._exigir_revision_valida({"texto": "Hola", "botones": botones})
                self.assertEqual(error.exception.status_code, 400)

    def test_baja_personalizada_con_enlace_y_llamada(self):
        botones = [{"tipo": "url", "texto": "Sitio", "url": "https://example.org"},
                   {"tipo": "telefono", "texto": "Llamar", "telefono": "+56912345678"},
                   {"tipo": "respuesta", "texto": "Salir", "accion": "baja"}]
        vista = presentar_plantilla({"texto": "Hola", "botones": botones})
        self.assertFalse(vista["errores"])
        self.assertEqual(botones_para_meta(vista["botones"]), [
            {"type": "URL", "text": "Sitio", "url": "https://example.org"},
            {"type": "PHONE_NUMBER", "text": "Llamar", "phone_number": "+56912345678"},
            {"type": "QUICK_REPLY", "text": "Salir"},
        ])

    def test_no_interesa_se_guarda_como_respuesta_de_desinteres(self):
        botones = [{"tipo": "respuesta", "texto": "No me interesa", "accion": "desinteres"},
                   {"tipo": "respuesta", "texto": "Dar de baja", "accion": "baja"}]
        vista = presentar_plantilla({"texto": "Hola", "botones": botones})
        self.assertFalse(vista["errores"])
        self.assertEqual(vista["botones"][0]["accion"], "desinteres")
        self.assertEqual(botones_para_meta(vista["botones"])[0],
                         {"type": "QUICK_REPLY", "text": "No me interesa"})

    def test_rechaza_mas_de_tres_botones(self):
        botones = [{"tipo": "respuesta", "texto": str(n), "accion": "baja"}
                   for n in range(4)]
        vista = presentar_plantilla({"texto": "Hola", "botones": botones})
        self.assertTrue(any("máximo 3" in e for e in vista["errores"]))

    def test_no_permite_usar_comando_de_borrado_como_boton(self):
        vista = presentar_plantilla({"texto": "Hola", "botones": [
            {"tipo": "respuesta", "texto": "ELIMINAR", "accion": "baja"}]})
        self.assertTrue(any("reservado" in e for e in vista["errores"]))

    def test_botones_malformados_no_causan_error_interno(self):
        vista = presentar_plantilla({"texto": "Hola", "botones": [
            {"tipo": "respuesta", "texto": 123, "accion": "baja"}]})
        self.assertTrue(any("no son válidos" in e for e in vista["errores"]))

    def test_enlace_dinamico_no_soportado_se_advierte(self):
        vista = presentar_plantilla({"texto": "Hola", "botones": [{
            "tipo": "url", "texto": "Abrir", "url": "https://example.org/{{1}}"}]})
        self.assertTrue(any("parámetros" in e for e in vista["errores"]))

    def test_guardado_rechaza_variable_sin_ejemplo(self):
        with self.assertRaises(HTTPException) as error:
            main._exigir_revision_valida({"texto": "Hola {nombre}"}, {"nombre": ""})
        self.assertEqual(error.exception.status_code, 400)

    def test_encabezado_y_pie_editables_se_previsualizan_y_validan(self):
        vista = presentar_plantilla({"texto": "Hola", "encabezado_texto": "Promoción",
                                    "pie_texto": "Salud Mental"})
        self.assertEqual(vista["encabezado"], {"tipo": "texto", "texto": "Promoción"})
        self.assertEqual(vista["pie"], "Salud Mental")
        self.assertFalse(vista["errores"])
        errores = presentar_plantilla({"texto": "Hola", "encabezado_texto": "x" * 61,
                                      "pie_texto": "{nombre}"})["errores"]
        self.assertTrue(any("60 caracteres" in error for error in errores))
        self.assertTrue(any("variables" in error for error in errores))


class ComponentesMetaEditablesTests(unittest.TestCase):
    def test_creacion_envia_encabezado_y_pie_a_meta(self):
        with patch("whatsapp_service.credenciales_centro", return_value={
                 "token": "token-test", "waba_id": "waba-test"}), \
             patch.object(WhatsAppService, "waba_id", "waba-test"), \
             patch.object(WhatsAppApiClient, "crear_template", new_callable=AsyncMock,
                          return_value={"id": "meta-7", "status": "PENDING"}) as crear:
            resultado = WhatsAppService(phone_number_id="phone-test").crear_template_meta(
                "saludo", "Hola", "es", "MARKETING", encabezado_texto="Oferta",
                pie_texto="Notificaciones SNW")
        self.assertTrue(resultado["ok"])
        componentes = crear.await_args.args[1]["components"]
        self.assertEqual([c["type"] for c in componentes],
                         ["HEADER", "BODY", "FOOTER", "BUTTONS"])
        self.assertEqual(componentes[0], {"type": "HEADER", "format": "TEXT", "text": "Oferta"})
        self.assertEqual(componentes[2]["text"], "Notificaciones SNW")

    def test_edicion_reenvia_encabezado_y_pie_a_meta(self):
        with patch("whatsapp_service.credenciales_centro", return_value={
                 "token": "token-test", "waba_id": "waba-test"}), \
             patch.object(WhatsAppApiClient, "obtener_template", new_callable=AsyncMock,
                          return_value={"category": "MARKETING"}), \
             patch.object(WhatsAppApiClient, "editar_template", new_callable=AsyncMock,
                          return_value={"status": "PENDING"}) as editar:
            resultado = WhatsAppService(phone_number_id="phone-test").editar_template_meta(
                "meta-7", "Hola", "MARKETING", encabezado_texto="Nueva oferta",
                pie_texto="Nuevo pie")
        self.assertTrue(resultado["ok"])
        componentes = editar.await_args.args[1]["components"]
        self.assertEqual(componentes[0]["text"], "Nueva oferta")
        self.assertEqual(componentes[2]["text"], "Nuevo pie")

    def test_previsualizacion_conserva_componentes_existentes_si_no_se_envian(self):
        app = FastAPI()
        app.include_router(rutas_plantillas.registrar(vars(main)))
        app.dependency_overrides[main.sesion_actual] = lambda: SESION
        plantilla = {**PLANTILLA, "encabezado_texto": "Oferta",
                    "pie_texto": "Notificaciones SNW", "whatsapp_header_format": "TEXT"}
        with patch.object(main, "leer_plantillas", return_value=[plantilla]):
            respuesta = TestClient(app).post("/api/plantillas/previsualizar", json={
                "plantilla_id": 7, "texto": plantilla["texto"]})
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json()["encabezado"]["texto"], "Oferta")
        self.assertEqual(respuesta.json()["pie"], "Notificaciones SNW")
        self.assertNotIn("Estos cambios requieren guardar", " ".join(respuesta.json()["avisos"]))

    def test_actualizacion_guarda_y_registra_encabezado_y_pie(self):
        app = FastAPI()
        app.include_router(rutas_plantillas.registrar(vars(main)))
        app.dependency_overrides[main.sesion_actual] = lambda: SESION
        plantilla = {**PLANTILLA, "whatsapp_template_lang": "es",
                    "whatsapp_template_categoria": "MARKETING", "whatsapp_template_id": "meta-7"}
        with patch.object(main, "leer_plantillas", return_value=[plantilla]), \
             patch.object(main, "escribir_plantillas") as escribir, \
             patch.object(main, "_registrar_template_meta", side_effect=lambda p, *args: p) as registrar:
            respuesta = TestClient(app).put("/api/plantillas/7", json={
                "nombre": "Saludo", "texto": plantilla["texto"],
                "whatsapp_template_lang": "es", "whatsapp_template_categoria": "MARKETING",
                "encabezado_texto": "Oferta", "pie_texto": "Notificaciones SNW",
                "ejemplos": {"nombre": "Ana"}})
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(plantilla["encabezado_texto"], "Oferta")
        self.assertEqual(plantilla["pie_texto"], "Notificaciones SNW")
        registrar.assert_called_once()
        escribir.assert_called_once()


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


class SincronizarAreaTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(rutas_plantillas.registrar(vars(main)))
        self.sesion = dict(SESION)
        app.dependency_overrides[main.sesion_actual] = lambda: self.sesion
        self.cliente = TestClient(app)
        cfg = {"wa_token": "global", "SMPT_WSP_WABA_ID": "111111"}
        canal = {"codigo": "SMPT", "nombre": "Salud Mental Para Todos",
                 "phoneNumberId": "1234500001", "configured": True, "problemas": []}
        for parche in (patch.object(main, "config_all", return_value=cfg),
                       patch.object(main, "resolver_centro", return_value=canal),
                       patch.object(main, "verificar_canal_meta", return_value=(True, "ok"))):
            parche.start()
            self.addCleanup(parche.stop)
        obtener = self.cliente.get
        enviar = self.cliente.post
        ruta = "/api/plantillas/sincronizar-meta"
        self.cliente.get = lambda url, *args, **kwargs: obtener(
            url + "?centro=SMPT" if url == ruta else url, *args, **kwargs)
        self.cliente.post = lambda url, *args, **kwargs: enviar(
            url, *args, **({**kwargs, "json": {"centro": "SMPT", **kwargs["json"]}}
                         if url == ruta and isinstance(kwargs.get("json"), dict) else kwargs))

    def test_eliminada_en_meta_se_previsualiza_y_se_quita_solo_al_confirmar(self):
        viva = {"id": "meta-viva", "name": "saludo", "language": "es",
                "status": "APPROVED", "components": [{"type": "BODY", "text": "Hola"}]}
        eliminada = {**PLANTILLA, "id": 8, "clave": "eliminada", "nombre": "Eliminada",
                     "whatsapp_template": "eliminada", "whatsapp_template_id": "meta-eliminada",
                     "whatsapp_template_status": "PENDING"}
        borrador = {**PLANTILLA, "id": 9, "clave": "borrador", "nombre": "Borrador",
                    "whatsapp_template": "borrador", "whatsapp_template_id": None,
                    "whatsapp_template_status": None, "aprobacion_estado": "pendiente"}
        almacen = [dict(PLANTILLA), eliminada, borrador]
        with patch.object(main, "leer_plantillas", return_value=almacen), \
             patch.object(main, "escribir_plantillas") as guardar, \
             patch.object(main, "_resguardar_plantillas_antes_limpieza") as respaldo, \
             patch.object(main, "auditoria_registrar") as auditoria, \
             patch.object(main, "_detener_jobs_plantilla_no_aprobada") as detener, \
             patch.object(main.WhatsAppService, "listar_templates_meta", return_value={
                 "ok": True, "templates": [viva]}):
            previa = self.cliente.get("/api/plantillas/sincronizar-meta")
            self.assertEqual(previa.status_code, 200)
            self.assertEqual(previa.json()["eliminadas"], [{"id": 8, "nombre": "Eliminada"}])
            guardar.assert_not_called()
            respuesta = self.cliente.post("/api/plantillas/sincronizar-meta", json={
                "asignaciones": {}, "eliminadas": [8]})
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json()["eliminadas"], 1)
        self.assertEqual({p["id"] for p in guardar.call_args.args[0]}, {7, 9})
        respaldo.assert_called_once()
        detener.assert_called_once_with(eliminada, None)
        auditoria.assert_called_once()
        self.assertEqual(auditoria.call_args.args[1], "plantilla_ausente_meta_eliminada")

    def test_no_borra_sin_confirmacion_actual_ni_si_meta_devuelve_lista_vacia(self):
        eliminada = {**PLANTILLA, "whatsapp_template_id": "meta-eliminada"}
        with patch.object(main, "leer_plantillas", return_value=[eliminada]), \
             patch.object(main, "escribir_plantillas") as guardar, \
             patch.object(main.WhatsAppService, "listar_templates_meta", return_value={
                 "ok": True, "templates": [{"id": "otra", "name": "otra", "language": "es"}]}):
            respuesta = self.cliente.post("/api/plantillas/sincronizar-meta", json={
                "asignaciones": {"otra": None}})
        self.assertEqual(respuesta.status_code, 409)
        guardar.assert_not_called()
        with patch.object(main, "leer_plantillas", return_value=[eliminada]), \
             patch.object(main, "escribir_plantillas") as guardar, \
             patch.object(main.WhatsAppService, "listar_templates_meta", return_value={
                 "ok": True, "templates": []}):
            respuesta = self.cliente.get("/api/plantillas/sincronizar-meta")
        self.assertEqual(respuesta.status_code, 502)
        guardar.assert_not_called()

    def test_fallo_de_respaldo_impide_eliminacion(self):
        eliminada = {**PLANTILLA, "whatsapp_template_id": "meta-eliminada"}
        with patch.object(main, "leer_plantillas", return_value=[eliminada]), \
             patch.object(main, "escribir_plantillas") as guardar, \
             patch.object(main, "_resguardar_plantillas_antes_limpieza",
                          side_effect=HTTPException(500, "Sin respaldo")), \
             patch.object(main.WhatsAppService, "listar_templates_meta", return_value={
                 "ok": True, "templates": [{"id": "otra", "name": "otra", "language": "es"}]}):
            respuesta = self.cliente.post("/api/plantillas/sincronizar-meta", json={
                "asignaciones": {"otra": None}, "eliminadas": [7]})
        self.assertEqual(respuesta.status_code, 500)
        guardar.assert_not_called()

    def test_eliminacion_requiere_permiso_de_edicion(self):
        self.sesion.update(rol="usuario", permisos=["mensajeria"], area_ids=[8])
        eliminada = {**PLANTILLA, "whatsapp_template_id": "meta-eliminada"}
        with patch.object(main, "leer_plantillas", return_value=[eliminada]), \
             patch.object(main, "escribir_plantillas") as guardar, \
             patch.object(main, "_exigir_area", return_value={"id": 8}), \
             patch.object(main.WhatsAppService, "listar_templates_meta", return_value={
                 "ok": True, "templates": [{"id": "otra", "name": "otra", "language": "es"}]}):
            respuesta = self.cliente.post("/api/plantillas/sincronizar-meta", json={
                "asignaciones": {"otra": 8}, "eliminadas": [7]})
            self.assertEqual(respuesta.status_code, 403)
            guardar.assert_not_called()
            respuesta = self.cliente.post("/api/plantillas/sincronizar-meta", json={
                "asignaciones": {"otra": 8}, "eliminadas": []})
        self.assertEqual(respuesta.status_code, 200)
        self.assertIn(7, {p["id"] for p in guardar.call_args.args[0]})
        self.assertEqual(respuesta.json()["eliminadas"], 0)

    def test_otro_idioma_con_mismo_nombre_no_oculta_la_eliminacion(self):
        local = {**PLANTILLA, "whatsapp_template_id": "meta-es",
                 "whatsapp_template_lang": "es"}
        variantes = [{"id": "meta-en", "name": "saludo", "language": "en_US"}]
        self.assertIsNone(main._coincidencia_template_meta(local, variantes))
        self.assertEqual(main._plantillas_ausentes_meta([local], variantes), [local])

    def test_error_previo_de_ausencia_se_puede_limpiar_sin_id_meta(self):
        local = {**PLANTILLA, "whatsapp_template_id": None,
                 "whatsapp_template_status": None,
                 "whatsapp_template_error": "No se encontró el template 'saludo' (idioma 'es') en Meta."}
        variantes = [{"id": "otro", "name": "otra", "language": "es"}]
        self.assertEqual(main._plantillas_ausentes_meta([local], variantes), [local])

    def test_importacion_no_reutiliza_id_de_plantilla_eliminada(self):
        eliminada = {**PLANTILLA, "id": 20, "whatsapp_template_id": "meta-vieja"}
        nueva = {"id": "meta-nueva", "name": "nueva", "language": "es",
                 "status": "APPROVED", "components": [{"type": "BODY", "text": "Hola"}]}
        with patch.object(main, "leer_plantillas", return_value=[eliminada]), \
             patch.object(main, "escribir_plantillas") as guardar, \
             patch.object(main, "_resguardar_plantillas_antes_limpieza"), \
             patch.object(main, "auditoria_registrar"), \
             patch.object(main.WhatsAppService, "listar_templates_meta", return_value={
                 "ok": True, "templates": [nueva]}):
            respuesta = self.cliente.post("/api/plantillas/sincronizar-meta", json={
                "asignaciones": {"meta-nueva": None}, "eliminadas": [20]})
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual([p["id"] for p in guardar.call_args.args[0]], [21])

    def test_importacion_asigna_area_y_repeticion_conserva_la_existente(self):
        meta = {"id": "meta-sync", "name": "saludo_area", "language": "es",
                "status": "APPROVED", "category": "UTILITY",
                "components": [{"type": "BODY", "text": "Hola"}]}
        otra = {**meta, "id": "meta-otra", "name": "otra_area"}
        almacen = []
        with patch.object(main, "leer_plantillas", return_value=almacen), \
                patch.object(main, "escribir_plantillas") as guardar, \
                patch.object(main, "_exigir_area", side_effect=lambda s, area: {"id": area}), \
                patch.object(main.WhatsAppService, "listar_templates_meta",
                             return_value={"ok": True, "templates": [meta, otra]}):
            previa = self.cliente.get("/api/plantillas/sincronizar-meta")
            self.assertEqual(previa.status_code, 200)
            self.assertEqual(len(previa.json()["nuevas"]), 2)
            guardar.assert_not_called()
            self.assertEqual(almacen, [])
            primera = self.cliente.post("/api/plantillas/sincronizar-meta", json={
                "asignaciones": {"meta-sync": 8, "meta-otra": 9}})
            self.assertEqual(primera.status_code, 200)
            self.assertEqual(primera.json()["creadas"], 2)
            self.assertEqual(almacen[0]["area_id"], 8)
            self.assertEqual(almacen[1]["area_id"], 9)
            segunda = self.cliente.post("/api/plantillas/sincronizar-meta", json={
                "asignaciones": {"meta-sync": 9, "meta-otra": 8}})
            self.assertEqual(segunda.status_code, 200)
            self.assertEqual(segunda.json()["creadas"], 0)
            self.assertEqual(almacen[0]["area_id"], 8)
            self.assertEqual(almacen[1]["area_id"], 9)
            self.assertEqual(self.cliente.get("/api/plantillas/sincronizar-meta").json()["nuevas"], [])
            self.assertEqual(guardar.call_count, 2)

    def test_plantilla_historica_se_vincula_solo_por_id_meta(self):
        local = {**PLANTILLA, "centro_codigo": None,
                 "whatsapp_template_id": "meta-7", "texto": "Hola"}
        meta = {"id": "meta-7", "name": "saludo", "language": "es",
                "status": "APPROVED", "components": [{"type": "BODY", "text": "Hola"}]}
        with patch.object(main, "leer_plantillas", return_value=[local]), \
             patch.object(main, "escribir_plantillas") as guardar, \
             patch.object(main.WhatsAppService, "listar_templates_meta", return_value={
                 "ok": True, "templates": [meta]}):
            respuesta = self.cliente.post("/api/plantillas/sincronizar-meta", json={
                "asignaciones": {}, "eliminadas": []})
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(local["centro_codigo"], "SMPT")
        self.assertEqual(respuesta.json()["creadas"], 0)
        guardar.assert_called_once()

    def test_sincronizar_conserva_accion_baja_de_boton_personalizado(self):
        local = {**PLANTILLA, "whatsapp_template_id": "meta-7", "texto": "Hola",
                 "botones": [{"tipo": "respuesta", "texto": "Salir", "accion": "baja",
                              "url": None, "telefono": None}]}
        meta = {"id": "meta-7", "name": "saludo", "language": "es", "status": "APPROVED",
                "components": [{"type": "BODY", "text": "Hola"},
                               {"type": "BUTTONS", "buttons": [
                                   {"type": "QUICK_REPLY", "text": "Salir"}]}]}
        with patch.object(main, "leer_plantillas", return_value=[local]), \
             patch.object(main, "escribir_plantillas"), \
             patch.object(main.WhatsAppService, "listar_templates_meta", return_value={
                 "ok": True, "templates": [meta]}):
            respuesta = self.cliente.post("/api/plantillas/sincronizar-meta", json={
                "asignaciones": {}})
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(local["botones"][0]["accion"], "baja")
        self.assertFalse(presentar_plantilla(local)["errores"])

    def test_sincronizar_conserva_opciones_locales(self):
        opciones = [{"texto": "Horarios", "mensaje": "Atendemos de lunes a viernes."}]
        local = {**PLANTILLA, "whatsapp_template_id": "meta-7", "texto": "Hola",
                 "botones": [{"tipo": "respuesta", "texto": "Ver opciones", "accion": "opciones",
                              "opciones": opciones},
                             {"tipo": "respuesta", "texto": "Dar de baja", "accion": "baja"}]}
        meta = {"id": "meta-7", "name": "saludo", "language": "es", "status": "APPROVED",
                "components": [{"type": "BODY", "text": "Hola"},
                               {"type": "BUTTONS", "buttons": [
                                   {"type": "QUICK_REPLY", "text": "Ver opciones"},
                                   {"type": "QUICK_REPLY", "text": "Dar de baja"}]}]}
        with patch.object(main, "leer_plantillas", return_value=[local]), \
             patch.object(main, "escribir_plantillas"), \
             patch.object(main.WhatsAppService, "listar_templates_meta", return_value={
                 "ok": True, "templates": [meta]}):
            respuesta = self.cliente.post("/api/plantillas/sincronizar-meta", json={
                "asignaciones": {}})
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(local["botones"][0]["opciones"], opciones)
        self.assertEqual(local["botones"][0]["accion"], "opciones")

    def test_sincronizacion_descarga_imagen_y_video_y_reutiliza_archivos(self):
        imagen = io.BytesIO()
        Image.new("RGB", (4, 4), "green").save(imagen, format="PNG")
        video = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 24
        plantillas_meta = [
            {"id": "img", "name": "imagen", "language": "es", "status": "APPROVED",
             "components": [{"type": "HEADER", "format": "IMAGE",
                             "example": {"header_handle": ["https://scontent.whatsapp.net/imagen"]}},
                            {"type": "BODY", "text": "Hola"}]},
            {"id": "vid", "name": "video", "language": "es", "status": "APPROVED",
             "components": [{"type": "HEADER", "format": "VIDEO",
                             "example": {"header_handle": ["https://scontent.whatsapp.net/video"]}},
                            {"type": "BODY", "text": "Hola"}]},
        ]
        almacen = []
        with tempfile.TemporaryDirectory() as temporal, \
             patch.object(plantilla_media, "MEDIA_DIR", Path(temporal)), \
             patch.object(main, "leer_plantillas", return_value=almacen), \
             patch.object(main, "escribir_plantillas"), \
             patch.object(main, "_exigir_area", side_effect=lambda s, area: {"id": area}), \
             patch.object(main, "descargar_media_meta", side_effect=lambda url: (
                 imagen.getvalue() if url.endswith("imagen") else video)), \
             patch.object(main.WhatsAppService, "listar_templates_meta", return_value={
                 "ok": True, "templates": plantillas_meta}):
            respuesta = self.cliente.post("/api/plantillas/sincronizar-meta", json={
                "asignaciones": {"img": 8, "vid": 9}})
            self.assertEqual(respuesta.status_code, 200)
            self.assertEqual(respuesta.json()["creadas"], 2)
            ids = [p["encabezado_media_id"] for p in almacen]
            self.assertTrue(all(ids))
            self.assertTrue(all(p["encabezado_meta_registrado"] for p in almacen))
            self.assertEqual(plantilla_media.obtener_media(ids[0])["formato_meta"], "IMAGE")
            self.assertEqual(plantilla_media.obtener_media(ids[1])["formato_meta"], "VIDEO")
            segunda = self.cliente.post("/api/plantillas/sincronizar-meta", json={
                "asignaciones": {}})
            self.assertEqual(segunda.status_code, 200)
            self.assertEqual([p["encabezado_media_id"] for p in almacen], ids)

    def test_fallo_de_descarga_no_guarda_sincronizacion(self):
        meta = {"id": "img", "name": "imagen", "language": "es", "status": "APPROVED",
                "components": [{"type": "HEADER", "format": "IMAGE",
                                "example": {"header_handle": ["https://scontent.whatsapp.net/imagen"]}}]}
        almacen = []
        with patch.object(main, "leer_plantillas", return_value=almacen), \
             patch.object(main, "escribir_plantillas") as guardar, \
             patch.object(main, "_exigir_area", side_effect=lambda s, area: {"id": area}), \
             patch.object(main, "descargar_media_meta", side_effect=HTTPException(502, "Fallo de Meta")), \
             patch.object(main.WhatsAppService, "listar_templates_meta", return_value={
                 "ok": True, "templates": [meta]}):
            respuesta = self.cliente.post("/api/plantillas/sincronizar-meta", json={
                "asignaciones": {"img": 8}})
        self.assertEqual(respuesta.status_code, 502)
        self.assertEqual(almacen, [])
        guardar.assert_not_called()

    def test_sincronizacion_recupera_encabezado_de_plantilla_existente(self):
        imagen = io.BytesIO()
        Image.new("RGB", (4, 4), "green").save(imagen, format="PNG")
        meta = {"id": "meta-7", "name": "saludo", "language": "es", "status": "APPROVED",
                "components": [{"type": "HEADER", "format": "IMAGE",
                                "example": {"header_handle": ["https://scontent.whatsapp.net/imagen"]}},
                               {"type": "BODY", "text": "Hola"}]}
        almacen = [{**PLANTILLA, "whatsapp_template_lang": "es"}]
        with tempfile.TemporaryDirectory() as temporal, \
             patch.object(plantilla_media, "MEDIA_DIR", Path(temporal)), \
             patch.object(main, "leer_plantillas", return_value=almacen), \
             patch.object(main, "escribir_plantillas"), \
             patch.object(main, "descargar_media_meta", return_value=imagen.getvalue()), \
             patch.object(main.WhatsAppService, "listar_templates_meta", return_value={
                 "ok": True, "templates": [meta]}):
            respuesta = self.cliente.post("/api/plantillas/sincronizar-meta", json={
                "asignaciones": {}})
            self.assertEqual(respuesta.status_code, 200)
            self.assertEqual(respuesta.json()["creadas"], 0)
            self.assertEqual(respuesta.json()["actualizadas"], 1)
            self.assertTrue(almacen[0]["encabezado_meta_registrado"])
            self.assertEqual(plantilla_media.obtener_media(
                almacen[0]["encabezado_media_id"])["formato_meta"], "IMAGE")

    def test_usuario_no_puede_importar_en_area_ajena(self):
        self.sesion.update(rol="usuario", permisos=["mensajeria"], area_ids=[8])
        with patch.object(main, "leer_plantillas", return_value=[]), \
                patch.object(main.servicio_areas, "obtener_area", return_value={"id": 9}), \
                patch.object(main.WhatsAppService, "listar_templates_meta", return_value={
                    "ok": True, "templates": [{"id": "meta-sync", "name": "nueva"}]}), \
                patch.object(main, "escribir_plantillas") as guardar:
            respuesta = self.cliente.post("/api/plantillas/sincronizar-meta", json={
                "asignaciones": {"meta-sync": 9}})
        self.assertEqual(respuesta.status_code, 403)
        guardar.assert_not_called()

    def test_usuario_debe_elegir_area(self):
        self.sesion.update(rol="usuario", permisos=["mensajeria"], area_ids=[8])
        with patch.object(main.WhatsAppService, "listar_templates_meta") as consultar:
            respuesta = self.cliente.post("/api/plantillas/sincronizar-meta")
        self.assertEqual(respuesta.status_code, 422)
        consultar.assert_not_called()

    def test_asignacion_faltante_no_importa_ni_actualiza(self):
        almacen = [{**PLANTILLA, "whatsapp_template_status": "PAUSED"}]
        with patch.object(main, "leer_plantillas", return_value=almacen), \
                patch.object(main.WhatsAppService, "listar_templates_meta", return_value={
                    "ok": True, "templates": [
                        {"id": "existente", "name": "saludo", "language": "es", "status": "APPROVED"},
                        {"id": "nueva", "name": "otra", "language": "es"}]}), \
                patch.object(main, "escribir_plantillas") as guardar:
            respuesta = self.cliente.post("/api/plantillas/sincronizar-meta", json={"asignaciones": {}})
        self.assertEqual(respuesta.status_code, 409)
        self.assertEqual(almacen[0]["whatsapp_template_status"], "PAUSED")
        guardar.assert_not_called()

    def test_global_explicito_solo_para_roles_autorizados(self):
        with patch.object(main, "leer_plantillas", side_effect=lambda: []), \
                patch.object(main.WhatsAppService, "listar_templates_meta", return_value={
                    "ok": True, "templates": [{"id": "nueva", "name": "otra", "language": "es"}]}), \
                patch.object(main, "escribir_plantillas") as guardar:
            respuesta = self.cliente.post("/api/plantillas/sincronizar-meta", json={
                "asignaciones": {"nueva": None}})
            self.assertEqual(respuesta.status_code, 200)
            self.assertIsNone(respuesta.json()["plantillas"][0]["area_id"])
            guardar.reset_mock()
            self.sesion.update(rol="usuario", permisos=["mensajeria"], area_ids=[8])
            respuesta = self.cliente.post("/api/plantillas/sincronizar-meta", json={
                "asignaciones": {"nueva": None}})
            self.assertEqual(respuesta.status_code, 422)
            guardar.assert_not_called()


class AreaDuranteCooldownTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(rutas_plantillas.registrar(vars(main)))
        app.dependency_overrides[main.sesion_actual] = lambda: SESION
        self.cliente = TestClient(app)
        self.plantilla = {**PLANTILLA, "whatsapp_template_lang": "es",
                          "whatsapp_template_categoria": "UTILITY",
                          "ultima_edicion": int(main.time.time() * 1000)}
        self.body = {"nombre": self.plantilla["nombre"], "texto": self.plantilla["texto"],
                     "whatsapp_template_lang": "es", "whatsapp_template_categoria": "UTILITY",
                     "area_id": 8, "ejemplos": {"nombre": "Ana"}}

    def test_area_se_guarda_sin_meta_ni_reiniciar_cooldown(self):
        ultima_edicion = self.plantilla["ultima_edicion"]
        with patch.object(main, "leer_plantillas", return_value=[self.plantilla]), \
                patch.object(main, "escribir_plantillas") as guardar, \
                patch.object(main, "_exigir_area", return_value={"id": 8}), \
                patch.object(main, "_registrar_template_meta") as registrar:
            respuesta = self.cliente.put("/api/plantillas/7", json=self.body)
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json()["area_id"], 8)
        self.assertEqual(respuesta.json()["ultima_edicion"], ultima_edicion)
        self.assertEqual(respuesta.json()["whatsapp_template_status"], "APPROVED")
        guardar.assert_called_once()
        registrar.assert_not_called()

    def test_cambiar_area_y_contenido_no_elude_cooldown(self):
        self.body["texto"] = "Otro texto"
        with patch.object(main, "leer_plantillas", return_value=[self.plantilla]), \
                patch.object(main, "escribir_plantillas") as guardar, \
                patch.object(main, "_registrar_template_meta") as registrar:
            respuesta = self.cliente.put("/api/plantillas/7", json=self.body)
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn("24 horas", respuesta.json()["detail"])
        self.assertIsNone(self.plantilla["area_id"])
        guardar.assert_not_called()
        registrar.assert_not_called()

    def test_area_inexistente_no_se_guarda(self):
        with patch.object(main, "leer_plantillas", return_value=[self.plantilla]), \
                patch.object(main.servicio_areas, "obtener_area", return_value=None), \
                patch.object(main, "escribir_plantillas") as guardar:
            respuesta = self.cliente.put("/api/plantillas/7", json=self.body)
        self.assertEqual(respuesta.status_code, 404)
        guardar.assert_not_called()


class EndpointPruebaPlantillaTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(rutas_plantillas.registrar(vars(main)))
        app.dependency_overrides[main.sesion_actual] = lambda: SESION
        self.cliente = TestClient(app)
        main._pruebas_plantilla_ultima.clear()
        main._pruebas_plantilla_en_curso.clear()
        emisor = patch.object(main, "_exigir_centro_envio", return_value={
            "codigo": "SMPT", "phoneNumberId": "123456789", "phoneNumber": "+56900000001"})
        emisor.start()
        self.addCleanup(emisor.stop)

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

    def test_preview_ignora_campos_opcionales_vacios_de_botones_guardados(self):
        botones_editor = [
            {"tipo": "respuesta", "texto": "Me interesa", "accion": "interes"},
            {"tipo": "respuesta", "texto": "No me interesa", "accion": "desinteres"},
            {"tipo": "respuesta", "texto": "Dar de baja", "accion": "baja"},
        ]
        botones_guardados = [{**boton, "url": None, "telefono": None, "opciones": None}
                            for boton in botones_editor]
        plantilla = {**PLANTILLA, "botones": botones_guardados}
        with patch.object(main, "leer_plantillas", return_value=[plantilla]), \
             patch.object(main, "config_get", side_effect=self._config):
            respuesta = self.cliente.post("/api/plantillas/previsualizar", json={
                "plantilla_id": 7, "texto": plantilla["texto"],
                "ejemplos": {"nombre": "Ana", "apellido": "Pérez"},
                "botones": botones_editor})
        self.assertEqual(respuesta.status_code, 200)
        self.assertNotIn("Estos cambios requieren guardar", " ".join(respuesta.json()["avisos"]))
        self.assertTrue(respuesta.json()["puede_enviar_prueba"])

    def test_preview_sigue_detectando_cambios_reales_en_botones(self):
        guardados = [
            {"tipo": "respuesta", "texto": "Me interesa", "accion": "interes"},
            {"tipo": "respuesta", "texto": "Dar de baja", "accion": "baja"},
        ]
        plantilla = {**PLANTILLA, "botones": guardados}
        cambiados = [{**guardados[0], "texto": "Quiero información"}, guardados[1]]
        with patch.object(main, "leer_plantillas", return_value=[plantilla]), \
             patch.object(main, "config_get", side_effect=self._config):
            respuesta = self.cliente.post("/api/plantillas/previsualizar", json={
                "plantilla_id": 7, "texto": plantilla["texto"],
                "ejemplos": {"nombre": "Ana", "apellido": "Pérez"},
                "botones": cambiados})
        self.assertEqual(respuesta.status_code, 200)
        self.assertIn("Estos cambios requieren guardar", " ".join(respuesta.json()["avisos"]))
        self.assertFalse(respuesta.json()["puede_enviar_prueba"])

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
        self.assertEqual(respuesta.json()["centro"], "SMPT")
        self.assertEqual(enviar.await_count, 1)
        self.assertEqual(enviar.await_args.kwargs["variables"]["nombre"], "Ana")
        self.assertTrue(enviar.await_args.kwargs["es_prueba"])
        auditoria.assert_called_once()

    def test_prueba_rechaza_plantilla_sin_centro_asociado(self):
        sin_centro = {**PLANTILLA, "centro_codigo": None}
        with patch.object(main, "leer_plantillas", return_value=[sin_centro]), \
             patch.object(main, "config_get", side_effect=self._config), \
             patch.object(main, "_numeros_prueba_editables", return_value={TELEFONO}), \
             patch.object(main.WhatsAppService, "enviar", new_callable=AsyncMock) as enviar:
            respuesta = self.cliente.post("/api/plantillas/7/prueba", json={"telefono": TELEFONO})
        self.assertEqual(respuesta.status_code, 409)
        self.assertIn("no tiene un centro emisor asociado", respuesta.json()["detail"])
        enviar.assert_not_awaited()

    def test_prueba_ignora_centro_enviado_por_cliente(self):
        with patch.object(main, "leer_plantillas", return_value=[PLANTILLA]), \
             patch.object(main, "config_get", side_effect=self._config), \
             patch.object(main, "_numeros_prueba_editables", return_value={TELEFONO}), \
             patch.object(main, "_exigir_centro_envio", return_value={
                 "codigo": "SMPT", "phoneNumberId": "123456789",
                 "phoneNumber": "+56900000001"}) as exigir_centro, \
             patch.object(main.WhatsAppService, "configurada", return_value=True), \
             patch.object(main.WhatsAppService, "enviar", new_callable=AsyncMock,
                          return_value=(True, "wamid-prueba", None, "sent")):
            respuesta = self.cliente.post("/api/plantillas/7/prueba", json={
                "telefono": TELEFONO, "centro": "OTRO"})
        self.assertEqual(respuesta.status_code, 200)
        exigir_centro.assert_called_once_with("SMPT")

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
        main._pruebas_plantilla_ultima[(7, TELEFONO, "SMPT")] = main.time.monotonic() - 29.5
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
