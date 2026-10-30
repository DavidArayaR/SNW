"""Reintentos manuales de fallos definitivos sin repetir entregas confirmadas."""
import datetime as dt
import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import main
import paciente_reagendar as reagendar

CITA = {"id": 12, "lote_id": 7, "estado_envio": "ERROR", "modo_prueba": 0,
        "whatsapp_opt_out": 0, "centro_codigo": "VS",
        "paciente_telefono_normalizado": "+56911111111", "paciente_nombre": "Prueba",
        "doctor_nombre": "Doctora", "ultimo_error_codigo": "META_RECHAZO",
        "whatsapp_message_id": None, "estado_whatsapp": None}


class ReintentoReagendarTests(unittest.TestCase):
    def test_permite_rechazo_directo_y_fallo_confirmado_por_webhook(self):
        self.assertIsNone(reagendar.motivo_no_reintentar(CITA))
        self.assertIsNone(reagendar.motivo_no_reintentar({
            **CITA, "estado_envio": "ENVIADO", "whatsapp_message_id": "wamid.fallo",
            "estado_whatsapp": "failed"}))

    def test_bloquea_entregas_incertidumbre_bajas_y_registros_ya_reencolados(self):
        for cambio in ({"estado_whatsapp": "read"}, {"estado_whatsapp": "delivered"},
                       {"ultimo_error_codigo": "RESULTADO_INCIERTO"},
                       {"ultimo_error_codigo": "RESULTADO_INCIERTO", "whatsapp_message_id": "wamid.anterior", "estado_whatsapp": "failed"},
                       {"whatsapp_message_id": "wamid.sin-confirmar", "estado_whatsapp": "sent"},
                       {"whatsapp_opt_out": 1}, {"modo_prueba": 1}, {"estado_envio": "PENDIENTE"}):
            with self.subTest(cambio=cambio):
                self.assertIsNotNone(reagendar.motivo_no_reintentar({**CITA, **cambio}))

    def conexion(self, cita=None, entregado=False):
        conn = MagicMock()
        cur = conn.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value = [cita or CITA]
        cur.fetchone.side_effect = [None, None, {"ok": 1} if entregado else None,
                                   {"cita_id": 12, "estado": "ERROR"},
                                   {"id": 7, "programado_para": dt.datetime(2026, 1, 1)}]
        return conn, cur

    def test_reencola_sin_borrar_historial_ni_perder_datos_de_cita(self):
        conn, cur = self.conexion()
        validar = MagicMock()
        with patch.object(reagendar, "conectar", return_value=conn), \
             patch("supresion.huella_telefono", return_value="huella-prueba"):
            resultado = reagendar.preparar_reintentos_manuales([12], validar)
        self.assertEqual(resultado, {"encolados": 1})
        validar.assert_called_once_with(CITA)
        sql = [c.args[0] for c in cur.execute.call_args_list]
        self.assertIn("FOR UPDATE", sql[0])
        self.assertTrue(any("estado_envio='PENDIENTE'" in s for s in sql))
        self.assertFalse(any("DELETE" in s or "intentos_envio=0" in s for s in sql))
        self.assertFalse(any("whatsapp_message_id=NULL" in s for s in sql))
        conn.__enter__.return_value.commit.assert_called_once()

    def test_rechaza_entrega_previa_sin_modificar_la_cola(self):
        conn, cur = self.conexion(entregado=True)
        with patch.object(reagendar, "conectar", return_value=conn), \
             patch("supresion.huella_telefono", return_value="huella-prueba"), \
             self.assertRaisesRegex(ValueError, "ya recibió"):
            reagendar.preparar_reintentos_manuales([12], lambda c: None)
        self.assertFalse(any(c.args[0].startswith("UPDATE") for c in cur.execute.call_args_list))
        conn.__enter__.return_value.commit.assert_not_called()

    def test_un_segundo_click_no_reencola_un_registro_pendiente(self):
        conn, cur = self.conexion(cita={**CITA, "estado_envio": "PENDIENTE"})
        with patch.object(reagendar, "conectar", return_value=conn), \
             self.assertRaisesRegex(ValueError, "no tiene un error"):
            reagendar.preparar_reintentos_manuales([12], lambda c: None)
        self.assertFalse(any(c.args[0].startswith("UPDATE") for c in cur.execute.call_args_list))

    def test_endpoint_exige_procesamiento_activo(self):
        with patch.object(main, "config_all", return_value={"reagendar_envio_activo": "false"}), \
             patch.object(reagendar, "preparar_reintentos_manuales") as reintentar, \
             self.assertRaises(main.HTTPException) as error:
            main.reintentar_reagendamientos(main.ReintentarReagendarIn(registros=[12]),
                                            sesion={"usuario": "dev"})
        self.assertEqual(error.exception.status_code, 409)
        reintentar.assert_not_called()

    def test_lista_muestra_como_error_un_fallo_recibido_por_webhook(self):
        conn, cur = self.conexion()
        cur.fetchone.side_effect = [{"n": 1}]
        cur.fetchall.return_value = [{**CITA, "estado_envio": "ENVIADO",
                                     "whatsapp_message_id": "wamid.failed", "estado_whatsapp": "failed"}]
        with patch.object(main, "conectar", return_value=conn):
            respuesta = main.listar_citas_reagendar(
                centro=None, lote_id=None, fecha=None, ventana=None, estado="ERROR",
                modo_prueba=None, pagina=1, limite=25, sesion={"usuario": "dev"})
        self.assertEqual(respuesta["items"][0]["estado_envio"], "ERROR")
        self.assertTrue(respuesta["items"][0]["puede_reintentar"])

    def test_entrega_tardia_impide_post_del_reintento(self):
        conn, cur = self.conexion()
        cur.fetchone.side_effect = [None, None, {"ok": 1}, {"ok": 1}]
        servicio = MagicMock()
        servicio._pacientes_con_tel.return_value = []
        servicio.enviar = AsyncMock()
        plantilla = {"clave": "reagendar_vs", "centro_codigo": "VS",
                     "whatsapp_template_status": "APPROVED", "aprobacion_estado": "aprobada"}
        cfg = {"reagendar_envio_activo": "true", "entorno": "produccion", "metodo_envio": "api_oficial",
               "reagendar_plantillas_json": '{"VS":"reagendar_vs"}'}
        with patch.object(main, "conectar", return_value=conn), \
             patch.object(main, "config_all", return_value=cfg), \
             patch.object(main, "resolver_centro", return_value={"codigo": "VS", "configured": True, "phoneNumberId": "12345"}), \
             patch.object(main, "WhatsAppService", return_value=servicio), \
             patch.object(main, "esta_suprimido", return_value=False), \
             patch.object(main, "presentar_plantilla", return_value={"errores": [], "cuerpo": "Prueba", "ejemplos": {}}), \
             patch.object(reagendar, "registrar_lote", return_value=99), \
             patch.object(reagendar, "completar_lote_si_corresponde"), \
             patch.object(reagendar, "marcar_fallo") as fallo:
            asyncio.run(main._procesar_cita_reagendar(
                {**CITA, "reserva_token": "token", "intentos_envio": 2}, {"VS": plantilla}))
        fallo.assert_called_once()
        self.assertEqual(fallo.call_args.args[1], "YA_ENTREGADO")
        servicio.enviar.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
