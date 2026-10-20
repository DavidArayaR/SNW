"""Regresiones del registro de emisores y del enrutamiento del webhook."""

import sys
import datetime
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from whatsapp_channels import CENTROS, EMISOR_WEBHOOK, listar_canales, resolver_phone_id
from whatsapp_service import WhatsAppService
import main


def configuracion():
    valores = {}
    for i, codigo in enumerate(CENTROS, 1):
        valores[f"{codigo}_WSP_PHONE_NUMBER"] = f"+5690000000{i}"
        valores[f"{codigo}_WSP_PHONE_NUMBER_ID"] = f"123450000{i}"
    return valores


class MultiNumerosTests(unittest.TestCase):
    def test_secciones_de_centros_usan_icono_de_marca(self):
        for codigo in CENTROS:
            seccion = next(s for s in main._CONFIG_SECCIONES
                           if s["id"] == f"centro_{codigo.lower()}")
            self.assertEqual(seccion["icono"], "fa-whatsapp")
            self.assertTrue(seccion["marca"])

    def test_catalogo_valida_cuatro_centros_y_resuelve_id(self):
        canales = listar_canales(configuracion())
        self.assertEqual([c["codigo"] for c in canales], list(CENTROS))
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
