"""Regresiones del registro de emisores y del enrutamiento del webhook."""

import sys
import asyncio
import datetime
import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from whatsapp_channels import (CENTROS, EMISOR_WEBHOOK, app_id_centro, catalogo_centros,
                               credenciales_centro, listar_canales, resolver_phone_id)
from whatsapp_service import WhatsAppService
from whatsapp_channel_verification import verificar_canal_meta
from wa_dispatch_limit import politica
from routes.configuracion import RUTAS as RUTAS_CONFIGURACION
import main


def configuracion():
    valores = {"wa_token": "global"}
    for i, codigo in enumerate(CENTROS, 1):
        valores[f"{codigo}_WSP_PHONE_NUMBER"] = f"+5690000000{i}"
        valores[f"{codigo}_WSP_PHONE_NUMBER_ID"] = f"123450000{i}"
        valores[f"{codigo}_WSP_WABA_ID"] = f"223450000{i}"
        valores[f"{codigo}_WSP_ACCESS_TOKEN"] = f"token-{codigo}"
        valores[f"{codigo}_WSP_VERIFY_TOKEN"] = f"verify-{codigo}"
    return valores


class MultiNumerosTests(unittest.TestCase):
    def test_app_id_de_clinica_sin_respaldo_global(self):
        cfg = configuracion()
        cfg["wa_app_id"] = "1392977325249373"
        cfg["SMPT_WSP_APP_ID"] = "1392977325249373"
        cfg["MS_WSP_APP_ID"] = "987654321012345"
        self.assertEqual(app_id_centro("SMPT", cfg), "1392977325249373")
        self.assertEqual(app_id_centro("MS", cfg), "987654321012345")
        self.assertEqual(app_id_centro("PS", cfg), "")
        self.assertEqual(app_id_centro(None, cfg), "")
        with patch("db.config_all", return_value=cfg):
            self.assertEqual(WhatsAppService(phone_number_id=cfg["SMPT_WSP_PHONE_NUMBER_ID"]).app_id,
                             "1392977325249373")
            self.assertEqual(WhatsAppService(phone_number_id=cfg["MS_WSP_PHONE_NUMBER_ID"]).app_id,
                             "987654321012345")

    def test_migracion_ids_globales_a_smpt_no_pisa_valores(self):
        from db import _migrar_ids_globales_smpt

        class Cursor:
            def __init__(self, valores):
                self.valores = valores
                self.clave = None

            def execute(self, sql, params):
                self.clave = params[-1]
                if sql.startswith("UPDATE"):
                    self.valores[self.clave] = params[0]
                elif sql.startswith("DELETE"):
                    self.valores.pop(self.clave, None)

            def fetchone(self):
                return {"valor": self.valores[self.clave]} if self.clave in self.valores else None

        cur = Cursor({"wa_phone_id": "123", "SMPT_WSP_PHONE_NUMBER_ID": "",
                      "wa_app_id": "456", "SMPT_WSP_APP_ID": "456"})
        _migrar_ids_globales_smpt(cur)
        self.assertEqual(cur.valores["SMPT_WSP_PHONE_NUMBER_ID"], "123")
        self.assertEqual(cur.valores["SMPT_WSP_APP_ID"], "456")
        self.assertNotIn("wa_phone_id", cur.valores)
        self.assertNotIn("wa_app_id", cur.valores)

        cur = Cursor({"wa_phone_id": "otro", "SMPT_WSP_PHONE_NUMBER_ID": "123"})
        with patch("db.log_error") as registrar:
            _migrar_ids_globales_smpt(cur)
        self.assertEqual(cur.valores["SMPT_WSP_PHONE_NUMBER_ID"], "123")
        self.assertEqual(cur.valores["wa_phone_id"], "otro")
        registrar.assert_called_once()

    def test_cinco_clinicas_tienen_waba_y_token_propios(self):
        cfg = configuracion()
        canales = listar_canales(cfg)
        self.assertEqual({c["codigo"] for c in canales}, {"SMPT", "MS", "PS", "CP", "VS"})
        self.assertEqual(len({c["wabaId"] for c in canales}), 5)
        self.assertEqual(len({cfg[f"{c['codigo']}_WSP_ACCESS_TOKEN"] for c in canales}), 5)
        self.assertTrue(all(c["configured"] for c in canales))
        for canal in canales:
            self.assertEqual(credenciales_centro(canal["phoneNumberId"], cfg),
                             {"token": f"token-{canal['codigo']}", "waba_id": canal["wabaId"]})
        with patch("db.config_all", return_value=cfg):
            self.assertEqual(WhatsAppService(phone_number_id=canales[0]["phoneNumberId"]).token,
                             f"token-{canales[0]['codigo']}")
            self.assertEqual(WhatsAppService(phone_number_id=canales[1]["phoneNumberId"]).token,
                             f"token-{canales[1]['codigo']}")

    def test_global_no_sustituye_token_faltante_de_clinica(self):
        cfg = configuracion()
        cfg.pop("SMPT_WSP_ACCESS_TOKEN")
        canal = next(c for c in listar_canales(cfg) if c["codigo"] == "SMPT")
        self.assertFalse(canal["configured"])
        self.assertIn("Falta access token de la clínica", canal["problemas"])
        self.assertEqual(credenciales_centro(canal["phoneNumberId"], cfg)["token"], "")

    def test_configuracion_rechaza_tokens_repetidos_entre_clinicas(self):
        cfg = configuracion()
        with patch("main.config_all", return_value=cfg), \
             patch("main.config_set") as guardar:
            for clave, duplicado in (
                    ("MS_WSP_ACCESS_TOKEN", cfg["SMPT_WSP_ACCESS_TOKEN"]),
                    ("MS_WSP_VERIFY_TOKEN", cfg["SMPT_WSP_VERIFY_TOKEN"])):
                with self.subTest(clave=clave), self.assertRaises(main.HTTPException) as error:
                    main.actualizar_configuracion_completa(
                        main.ConfigTodoIn(cambios={clave: duplicado}), sesion={})
                self.assertEqual(error.exception.status_code, 422)
            guardar.assert_not_called()

    def test_estadisticas_pueden_elegir_clinica_inactiva_con_historial(self):
        cfg = configuracion()
        cfg["wa_clinicas_adicionales"] = json.dumps([
            {"codigo": "CA123456", "nombre": "Clínica histórica", "activo": False}
        ])
        self.assertNotIn("CA123456", {c["codigo"] for c in listar_canales(cfg)})
        canales = listar_canales(cfg, incluir_inactivas=True)
        historica = next(c for c in canales if c["codigo"] == "CA123456")
        self.assertFalse(historica["activa"])
        self.assertEqual(historica["nombre"], "Clínica histórica")

    def test_falta_waba_bloquea_solo_clinica_afectada(self):
        cfg = configuracion()
        cfg.pop("SMPT_WSP_WABA_ID")
        canales = {c["codigo"]: c for c in listar_canales(cfg)}
        self.assertFalse(canales["SMPT"]["configured"])
        self.assertIn("Falta WABA ID", canales["SMPT"]["problemas"])
        self.assertTrue(canales["VS"]["configured"])

    def test_sincronizacion_no_mezcla_plantillas_entre_waba(self):
        templates = [{"id": "meta-smpt", "name": "saludo"}]
        self.assertTrue(main._plantilla_en_clinica({"centro_codigo": "SMPT"}, "SMPT", templates))
        self.assertFalse(main._plantilla_en_clinica({"centro_codigo": "VS"}, "SMPT", templates))
        self.assertTrue(main._plantilla_en_clinica(
            {"whatsapp_template_id": "meta-smpt"}, "SMPT", templates))
        self.assertFalse(main._plantilla_en_clinica(
            {"whatsapp_template_id": "meta-otra", "whatsapp_template": "saludo"},
            "SMPT", templates))
        self.assertFalse(main._plantilla_en_clinica({}, "SMPT", templates))

    def test_servicio_sin_clinica_no_usa_waba_global_heredada(self):
        with patch("whatsapp_service.config_get", return_value="123456789") as leer:
            self.assertEqual(WhatsAppService().waba_id, "")
        leer.assert_not_called()

    def test_moneda_automatica_exige_acuerdo_entre_waba(self):
        cfg = configuracion()
        canales = listar_canales(cfg)
        respuestas = []
        for moneda in ("CLP", "USD"):
            respuesta = MagicMock(status_code=200)
            respuesta.json.return_value = {"currency": moneda}
            respuestas.append(respuesta)
        with patch.object(main, "config_all", return_value=cfg), \
             patch.object(main, "listar_canales", return_value=canales[:2]), \
             patch.object(main.httpx, "Client") as fabrica:
            fabrica.return_value.__enter__.return_value.get.side_effect = respuestas
            self.assertIsNone(main._obtener_moneda_meta())
            self.assertEqual(fabrica.return_value.__enter__.return_value.get.call_count, 2)
            llamadas = fabrica.return_value.__enter__.return_value.get.call_args_list
            self.assertEqual([llamada.kwargs["headers"]["Authorization"] for llamada in llamadas],
                             [f"Bearer token-{canal['codigo']}" for canal in canales[:2]])

    def test_meta_confirma_asociacion_waba_numero_con_token_de_clinica(self):
        cfg = configuracion()
        canal = listar_canales(cfg)[0]
        respuesta = MagicMock(status_code=200)
        respuesta.json.return_value = {"data": [{"id": canal["phoneNumberId"],
                                                 "display_phone_number": canal["phoneNumber"]}]}
        with patch("whatsapp_channel_verification.httpx.Client") as fabrica:
            fabrica.return_value.__enter__.return_value.get.return_value = respuesta
            ok, _ = verificar_canal_meta(canal, cfg)
        self.assertTrue(ok)
        llamada = fabrica.return_value.__enter__.return_value.get.call_args
        self.assertIn(canal["wabaId"], llamada.args[0])
        self.assertEqual(llamada.kwargs["headers"]["Authorization"], "Bearer token-SMPT")

    def test_meta_bloquea_waba_sin_permiso_o_numero_ajeno(self):
        cfg = configuracion()
        cfg["MS_WSP_ACCESS_TOKEN"] = "secreto-prueba"
        canal = listar_canales(cfg)[1]
        respuesta = MagicMock(status_code=403)
        with patch("whatsapp_channel_verification.httpx.Client") as fabrica:
            fabrica.return_value.__enter__.return_value.get.return_value = respuesta
            ok, mensaje = verificar_canal_meta(canal, cfg)
        self.assertFalse(ok)
        self.assertNotIn("secreto-prueba", mensaje)
        cfg["MS_WSP_ACCESS_TOKEN"] = "secreto-prueba-2"
        respuesta.status_code = 200
        respuesta.json.return_value = {"data": [{"id": "9999999999"}]}
        with patch("whatsapp_channel_verification.httpx.Client") as fabrica:
            fabrica.return_value.__enter__.return_value.get.return_value = respuesta
            ok, mensaje = verificar_canal_meta(canal, cfg)
        self.assertFalse(ok)
        self.assertIn("no pertenece", mensaje)

    def test_meta_indica_terminaciones_si_phone_id_tiene_otro_numero(self):
        cfg = configuracion()
        cfg["SMPT_WSP_ACCESS_TOKEN"] = "token-para-comparacion"
        canal = listar_canales(cfg)[0]
        respuesta = MagicMock(status_code=200)
        respuesta.json.return_value = {"data": [{"id": canal["phoneNumberId"],
                                                 "display_phone_number": "+56 9 7777 4321"}]}
        with patch("whatsapp_channel_verification.httpx.Client") as fabrica:
            fabrica.return_value.__enter__.return_value.get.return_value = respuesta
            ok, mensaje = verificar_canal_meta(canal, cfg)
        self.assertFalse(ok)
        self.assertIn("4321", mensaje)
        self.assertIn(canal["phoneNumber"][-4:], mensaje)
        self.assertNotIn("token-para-comparacion", mensaje)
        self.assertNotIn("56977774321", mensaje)
        with patch("whatsapp_channel_verification.httpx.Client") as fabrica:
            fabrica.return_value.__enter__.return_value.get.return_value = respuesta
            ok, detalle = verificar_canal_meta(canal, cfg, mostrar_numeros=True)
        self.assertFalse(ok)
        self.assertIn("Meta: +56977774321", detalle)
        self.assertIn(f"SNW: {canal['phoneNumber']}", detalle)
        self.assertNotIn("token-para-comparacion", detalle)

    def test_vs_usa_su_crm_sin_caer_en_el_de_smpt(self):
        cfg = configuracion()
        cfg["SMPT_CRM_PHONE_NUMBER"] = "56988887777"
        vs = next(c for c in listar_canales(cfg) if c["codigo"] == "VS")
        self.assertIsNone(vs["crmPhoneNumber"])
        self.assertIn("Número CRM pendiente", vs["avisos"])
        self.assertTrue(vs["configured"])

    def test_envio_oficial_falla_antes_de_meta_si_canal_no_autorizado(self):
        servicio = WhatsAppService(phone_number_id="1234500001")
        with patch("whatsapp_service.esta_suprimido", return_value=False), \
             patch.object(servicio, "_validar_canal_antes_envio", return_value=(False, "Sin permiso")):
            resultado = asyncio.run(servicio.enviar("56900000001", "Hola"))
        self.assertEqual(resultado[0], False)
        self.assertEqual(resultado[2], "Sin permiso")

    def test_clinica_adicional_requiere_token_propio(self):
        valores = configuracion()
        valores.update({
            "wa_clinicas_adicionales": json.dumps([{"codigo": "C1A2B3C4", "nombre": "Clínica Norte"}]),
            "C1A2B3C4_WSP_PHONE_NUMBER": "+56988887777",
            "C1A2B3C4_WSP_PHONE_NUMBER_ID": "9876543210",
        })
        self.assertEqual(catalogo_centros(valores)["C1A2B3C4"], "Clínica Norte")
        self.assertEqual(resolver_phone_id("9876543210", valores)["codigo"], "C1A2B3C4")
        self.assertEqual(credenciales_centro("9876543210", valores),
                         {"token": "", "waba_id": ""})
        valores["C1A2B3C4_WSP_ACCESS_TOKEN"] = "específico"
        valores["C1A2B3C4_WSP_WABA_ID"] = "222222"
        self.assertEqual(credenciales_centro("9876543210", valores),
                         {"token": "específico", "waba_id": "222222"})
        self.assertEqual(politica("9876543210", valores)["centro"], "C1A2B3C4")
        with patch("whatsapp_service.credenciales_centro",
                   return_value=credenciales_centro("9876543210", valores)):
            servicio = WhatsAppService(phone_number_id="9876543210")
            self.assertEqual((servicio.token, servicio.waba_id), ("específico", "222222"))

    def test_crear_clinica_no_modifica_las_existentes(self):
        with patch("main.config_all", return_value={"wa_clinicas_adicionales": "[]"}), \
             patch("main.config_set") as guardar, \
             patch("main.auditoria_registrar"):
            creado = main.agregar_clinica_whatsapp(main.NuevaClinicaIn(nombre="Clínica Sur"), sesion={})
        self.assertEqual(creado["nombre"], "Clínica Sur")
        self.assertTrue(creado["codigo"].startswith("C"))
        self.assertEqual(json.loads(guardar.call_args.args[0]["wa_clinicas_adicionales"]), [creado])

    def test_eliminar_clinica_retira_canal_y_conserva_nombre_historico(self):
        codigo = "C1A2B3C4"
        valores = configuracion()
        valores.update({
            "wa_clinicas_adicionales": json.dumps([{"codigo": codigo, "nombre": "Clínica Norte"}]),
            f"{codigo}_WSP_PHONE_NUMBER": "+56988887777",
            f"{codigo}_WSP_PHONE_NUMBER_ID": "9876543210",
            f"{codigo}_WSP_ACCESS_TOKEN": "secreto",
        })
        conexion = MagicMock()
        cursor = conexion.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchone.side_effect = [{"n": 0}, {"n": 0}]
        with patch("main.config_all", return_value=valores), \
             patch("main.conectar", return_value=conexion), \
             patch("main.config_set") as guardar, \
             patch("main.auditoria_registrar"):
            respuesta = main.eliminar_clinica_whatsapp(codigo, sesion={})
        self.assertTrue(respuesta["eliminada"])
        cambios = guardar.call_args.args[0]
        self.assertEqual(cambios[f"{codigo}_WSP_ACCESS_TOKEN"], "")
        valores.update(cambios)
        self.assertNotIn(codigo, catalogo_centros(valores))
        self.assertNotIn(codigo, catalogo_centros(valores, incluir_desactivadas=True))
        self.assertEqual(catalogo_centros(valores, incluir_inactivas=True)[codigo], "Clínica Norte")
        self.assertIsNone(resolver_phone_id("9876543210", valores))

    def test_eliminar_clinica_original_retira_canal_y_tarjeta_sin_perder_historial(self):
        valores = configuracion()
        valores["wa_clinicas_adicionales"] = "[]"
        conexion = MagicMock()
        cursor = conexion.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchone.side_effect = [{"n": 0}, {"n": 0}]
        with patch("main.config_all", return_value=valores), \
             patch("main.conectar", return_value=conexion), \
             patch("main.config_set") as guardar, \
             patch("main.auditoria_registrar"):
            respuesta = main.eliminar_clinica_whatsapp("SMPT", sesion={})
        self.assertTrue(respuesta["eliminada"])
        cambios = guardar.call_args.args[0]
        self.assertEqual(json.loads(cambios["wa_clinicas_adicionales"]), [
            {"codigo": "SMPT", "nombre": CENTROS["SMPT"],
             "activo": False, "eliminada": True}])
        valores.update(cambios)
        self.assertNotIn("SMPT", catalogo_centros(valores))
        self.assertEqual(catalogo_centros(valores, incluir_inactivas=True)["SMPT"],
                         CENTROS["SMPT"])
        self.assertIsNone(resolver_phone_id("1234500001", valores))
        with patch("main.config_all", return_value=valores), \
             patch("main.listar_canales", return_value=listar_canales(valores)), \
             patch("main.resumen_supresion", return_value={}):
            config = main.obtener_configuracion_completa(sesion={})
        self.assertNotIn("centro_smpt", {s["id"] for s in config["secciones"]})

    def test_desactivar_y_reactivar_clinica_conserva_credenciales(self):
        valores = configuracion()
        valores["wa_clinicas_adicionales"] = "[]"
        valores["SMPT_WSP_ACCESS_TOKEN"] = "secreto"
        conexion = MagicMock()
        cursor = conexion.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchone.side_effect = [{"n": 0}, {"n": 0}]
        with patch("main.config_all", return_value=valores), \
             patch("main.conectar", return_value=conexion), \
             patch("main.config_set") as guardar, \
             patch("main.auditoria_registrar") as auditar:
            respuesta = main.cambiar_estado_clinica_whatsapp(
                "SMPT", main.ClinicaEstadoIn(activa=False), sesion={"usuario": "dev"})
        self.assertFalse(respuesta["activa"])
        cambios = guardar.call_args.args[0]
        self.assertEqual(set(cambios), {"wa_clinicas_adicionales"})
        self.assertEqual(auditar.call_args.args[1], "clinica_wa_desactivada")
        valores.update(cambios)
        self.assertNotIn("SMPT", catalogo_centros(valores))
        self.assertIn("SMPT", catalogo_centros(valores, incluir_desactivadas=True))
        self.assertIsNone(resolver_phone_id("1234500001", valores))
        with patch("main.config_all", return_value=valores), \
             patch("main.resumen_supresion", return_value={}):
            config = main.obtener_configuracion_completa(sesion={})
        seccion = next(s for s in config["secciones"] if s["id"] == "centro_smpt")
        self.assertFalse(seccion["activa"])
        self.assertEqual(config["valores"]["SMPT_WSP_ACCESS_TOKEN"], "secreto")
        with patch("main.config_all", return_value=valores), \
             patch("main.config_set") as guardar, \
             patch("main.auditoria_registrar") as auditar:
            respuesta = main.cambiar_estado_clinica_whatsapp(
                "SMPT", main.ClinicaEstadoIn(activa=True), sesion={"usuario": "dev"})
        self.assertTrue(respuesta["activa"])
        self.assertEqual(auditar.call_args.args[1], "clinica_wa_activada")
        valores.update(guardar.call_args.args[0])
        self.assertEqual(resolver_phone_id("1234500001", valores)["codigo"], "SMPT")

    def test_desactivar_clinica_bloquea_programados_y_no_reactiva_eliminadas(self):
        valores = configuracion()
        valores["wa_clinicas_adicionales"] = "[]"
        conexion = MagicMock()
        cursor = conexion.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = {"n": 1}
        with patch("main.config_all", return_value=valores), \
             patch("main.conectar", return_value=conexion), \
             patch("main.config_set") as guardar:
            with self.assertRaises(main.HTTPException) as programados:
                main.cambiar_estado_clinica_whatsapp(
                    "SMPT", main.ClinicaEstadoIn(activa=False), sesion={})
        self.assertEqual(programados.exception.status_code, 409)
        guardar.assert_not_called()
        valores["wa_clinicas_adicionales"] = json.dumps([
            {"codigo": "SMPT", "nombre": CENTROS["SMPT"], "activo": False,
             "eliminada": True}])
        with patch("main.config_all", return_value=valores), \
             patch("main.config_set") as guardar:
            with self.assertRaises(main.HTTPException) as eliminada:
                main.cambiar_estado_clinica_whatsapp(
                    "SMPT", main.ClinicaEstadoIn(activa=True), sesion={})
        self.assertEqual(eliminada.exception.status_code, 404)
        guardar.assert_not_called()
        self.assertIn(("/api/configuracion/clinicas/{codigo}/estado", "PUT",
                       "cambiar_estado_clinica_whatsapp", None), RUTAS_CONFIGURACION)

    def test_respuestas_de_clinica_desactivada_conservan_emisor_historico(self):
        valores = configuracion()
        valores["wa_clinicas_adicionales"] = json.dumps([
            {"codigo": "SMPT", "nombre": CENTROS["SMPT"], "activo": False,
             "eliminada": False}])
        conexion = MagicMock()
        cursor = conexion.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = [{"centro_codigo": "SMPT", "numero_emisor": "+56900000001"}]
        with patch("whatsapp_service.conectar", return_value=conexion), \
             patch("whatsapp_service.catalogo_centros",
                   return_value=catalogo_centros(valores, incluir_desactivadas=True)) as catalogo:
            emisor = WhatsAppService._emisor_historico("1234500001")
        self.assertEqual(emisor["codigo"], "SMPT")
        catalogo.assert_called_once_with(incluir_desactivadas=True)

    def test_eliminar_clinica_bloquea_programados(self):
        valores = {"wa_clinicas_adicionales": json.dumps([
            {"codigo": "C1A2B3C4", "nombre": "Clínica Norte"}])}
        conexion = MagicMock()
        cursor = conexion.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = {"n": 1}
        with patch("main.config_all", return_value=valores), \
             patch("main.conectar", return_value=conexion), \
             patch("main.config_set") as guardar:
            with self.assertRaises(main.HTTPException) as programados:
                main.eliminar_clinica_whatsapp("C1A2B3C4", sesion={})
        self.assertEqual(programados.exception.status_code, 409)
        guardar.assert_not_called()

    def test_secciones_de_centros_usan_icono_de_marca(self):
        for codigo in CENTROS:
            seccion = next(s for s in main._CONFIG_SECCIONES
                           if s["id"] == f"centro_{codigo.lower()}")
            self.assertEqual(seccion["icono"], "fa-whatsapp")
            self.assertTrue(seccion["marca"])
            self.assertTrue(seccion["eliminable"])

    def test_catalogo_valida_cinco_centros_y_resuelve_id(self):
        canales = listar_canales(configuracion())
        self.assertEqual([c["codigo"] for c in canales], list(CENTROS))
        self.assertEqual(CENTROS["VS"], "VIDA Y SALUD")
        self.assertTrue(all(c["configured"] for c in canales))
        with patch("whatsapp_channels.listar_canales", return_value=canales):
            self.assertEqual(resolver_phone_id("1234500002")["codigo"], "MS")

    def test_catalogo_bloquea_ids_duplicados_y_configuracion_incompleta(self):
        valores = configuracion()
        valores["PS_WSP_PHONE_NUMBER_ID"] = valores["MS_WSP_PHONE_NUMBER_ID"]
        valores["CP_WSP_PHONE_NUMBER"] = ""
        canales = {c["codigo"]: c for c in listar_canales(valores)}
        self.assertFalse(canales["MS"]["configured"])
        self.assertFalse(canales["PS"]["configured"])
        self.assertFalse(canales["CP"]["configured"])
        self.assertTrue(canales["SMPT"]["configured"])

    def test_webhook_no_atribuye_emisor_desconocido(self):
        servicio = WhatsAppService()
        payload = {"entry": [{"changes": [{"field": "messages", "value": {
            "metadata": {"phone_number_id": "999999"},
            "messages": [{"id": "wamid.1", "from": "56912345678",
                          "type": "text", "text": {"body": "hola"}}],
        }}]}]}
        with patch.object(servicio, "_evento_duplicado", return_value=False), \
             patch.object(servicio, "_guardar_evento"), \
             patch("whatsapp_service.listar_canales", return_value=[]), \
             patch.object(servicio, "_emisor_historico", return_value=None), \
             patch("whatsapp_service.resolver_phone_id", return_value=None), \
             patch.object(servicio, "_procesar_mensajes") as procesar:
            self.assertEqual(servicio.procesar_evento(payload), ["emisor_desconocido"])
        procesar.assert_not_called()

    def test_webhook_enruta_por_phone_id_y_restaurar_contexto(self):
        servicio = WhatsAppService()
        emisor = listar_canales(configuracion())[1]
        payload = {"entry": [{"changes": [{"field": "messages", "value": {
            "metadata": {"phone_number_id": emisor["phoneNumberId"]},
            "messages": [{"id": "wamid.2"}],
        }}]}]}
        def comprobar(_valor):
            self.assertEqual(EMISOR_WEBHOOK.get()["codigo"], "MS")
            return ["procesado"]
        with patch.object(servicio, "_evento_duplicado", return_value=False), \
             patch.object(servicio, "_guardar_evento"), \
             patch("whatsapp_service.listar_canales", return_value=[emisor]), \
             patch("whatsapp_service.resolver_phone_id", return_value=emisor), \
             patch.object(servicio, "_procesar_mensajes", side_effect=comprobar):
            self.assertEqual(servicio.procesar_evento(payload), ["procesado"])
        self.assertIsNone(EMISOR_WEBHOOK.get())

    def test_envio_exige_centro_configurado_sin_fallback_global(self):
        with patch("main.resolver_centro", return_value={"configured": False,
                                                         "nombre": "PROSALUD", "problemas": ["Falta Phone Number ID"]}):
            with self.assertRaises(main.HTTPException):
                main._exigir_centro_envio("PS")

    def test_envio_manual_rechaza_emisor_distinto_al_de_la_plantilla(self):
        plantilla = {"id": 7, "centro_codigo": "SMPT", "clave": "promocion"}
        with patch.object(main, "tiene_permiso", return_value=True), \
             patch.object(main, "_exigir_centro_envio", return_value={"codigo": "MS"}), \
             patch.object(main, "_exigir_base_desarrollo"), \
             patch.object(main, "_envio_en_curso", return_value=None), \
             patch.object(main, "config_get", return_value="produccion"), \
             patch.object(main, "leer_config", return_value={}), \
             patch.object(main, "_numeros_prueba_editables", return_value=set()), \
             patch.object(main, "leer_plantillas", return_value=[plantilla]), \
             patch.object(main, "conectar") as conectar:
            with self.assertRaises(main.HTTPException) as error:
                main.iniciar_envio(main.EnvioIn(
                    plantilla_id=7, ambiente="produccion", centro="MS"),
                    main.BackgroundTasks(), {"rol": "administrador"})
        self.assertEqual(error.exception.status_code, 422)
        conectar.assert_not_called()

    def test_prevision_rechaza_emisor_distinto_sin_consultar_pacientes(self):
        plantilla = {"id": 7, "centro_codigo": "SMPT"}
        with patch.object(main, "_exigir_base_desarrollo"), \
             patch.object(main, "leer_plantillas", return_value=[plantilla]), \
             patch.object(main, "_contar_elegibles_tabla") as contar:
            with self.assertRaises(main.HTTPException) as error:
                main.contar_destinatarios(main.DestinosIn(
                    plantilla_id=7, ambiente="produccion", centro="MS"),
                    {"rol": "administrador"})
        self.assertEqual(error.exception.status_code, 422)
        contar.assert_not_called()

    def test_programado_rechaza_emisor_distinto_al_de_la_plantilla(self):
        plantilla = {"id": 7, "centro_codigo": "SMPT"}
        fecha = (datetime.datetime.now() + datetime.timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")
        with patch.object(main, "tiene_permiso", return_value=True), \
             patch.object(main, "_exigir_centro_envio", return_value={
                 "codigo": "MS", "phoneNumberId": "1234500002"}), \
             patch.object(main, "_exigir_base_desarrollo"), \
             patch.object(main, "config_get", return_value="produccion"), \
             patch.object(main, "_contar_elegibles_tabla", return_value=(10, 10)), \
             patch.object(main, "_contar_reservados_prog", return_value=0), \
             patch.object(main, "leer_plantillas", return_value=[plantilla]), \
             patch.object(main, "_validar_plantilla_programable", return_value=plantilla), \
             patch.object(main, "conectar") as conectar:
            with self.assertRaises(main.HTTPException) as error:
                main.crear_programado(main.ProgCrearIn(
                    plantilla_id=7, ambiente="produccion", centro="MS",
                    programado_para=fecha, limite=1), {"rol": "administrador"})
        self.assertEqual(error.exception.status_code, 422)
        conectar.assert_not_called()

    def test_plantilla_sin_clinica_no_se_envia(self):
        with self.assertRaises(main.HTTPException) as error:
            main._exigir_emisor_de_plantilla({"id": 7}, "SMPT")
        self.assertEqual(error.exception.status_code, 409)

    def test_historial_filtra_ambas_fuentes_por_centro(self):
        sesion = {"rol": "administrador", "area_ids": []}
        with patch.object(main, "_es_privilegiado", return_value=True):
            sql, args = main._fuentes_historial_pagina(
                sesion, "todos", None, None, "todos", "", {"programado_id", "area_id"}, "SMPT")
        self.assertIn("e.centro_codigo = %s", sql)
        self.assertIn("p.centro_codigo = %s", sql)
        self.assertEqual(args.count("SMPT"), 2)

    def test_cupo_de_meta_separa_envios_y_reservas_por_phone_id(self):
        conexion = MagicMock()
        cursor = conexion.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = {"n": 0}
        with patch.object(main, "conectar", return_value=conexion), \
             patch.object(main, "_limite_mensajeria_tier", return_value=1000):
            info = main._limite_mensajeria_fecha(datetime.date(2026, 10, 20), "1234500001")
        self.assertEqual(info["disponibles"], 1000)
        consultas = cursor.execute.call_args_list
        self.assertEqual(len(consultas), 2)
        self.assertIn("wa_phone_id = %s", consultas[0].args[0])
        self.assertIn("ep.wa_phone_id = %s", consultas[1].args[0])
        self.assertIn("le.wa_phone_id = %s", consultas[1].args[0])
        self.assertEqual(consultas[1].args[1].count("1234500001"), 2)


if __name__ == "__main__":
    unittest.main()
