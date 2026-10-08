import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import supresion
import whatsapp_service
import servicio_areas
import main
from motor_envio import MotorSimulado


TEL = "+56912345678"


class SupresionTests(unittest.TestCase):
    def _valor(self, texto):
        return {"contacts": [{"wa_id": TEL.lstrip("+")}],
                "messages": [{"type": "text", "text": {"body": texto}}]}

    def test_eliminar_acepta_variantes_y_no_frases(self):
        for texto in ("ELIMINAR", " eliminar ", "Elíminar", "e l i m i n a r"):
            self.assertTrue(whatsapp_service.es_solicitud_eliminar(texto))
        self.assertFalse(whatsapp_service.es_solicitud_eliminar("quiero eliminar una cita"))

    def test_huella_estable_sin_numero_recuperable(self):
        with patch("supresion._clave_hmac", return_value=b"k" * 32):
            a = supresion.huella_telefono(TEL)
            self.assertEqual(a, supresion.huella_telefono("9 1234 5678"))
            self.assertNotEqual(a, supresion.huella_telefono("+56987654321"))
            self.assertNotIn("912345678", a)

    def test_eliminar_con_aviso_no_registra_respuesta_ni_reactiva(self):
        servicio = whatsapp_service.WhatsAppService()
        with patch("whatsapp_service.esta_suprimido", return_value=False), \
                patch("whatsapp_service.es_exento", return_value=False), \
                patch("whatsapp_service.es_numero_prueba", return_value=False), \
                patch("whatsapp_service.oferta_eliminacion_enviada", return_value=True), \
                patch("whatsapp_service.solicitar_eliminacion") as eliminar, \
                patch.object(servicio, "_estaba_opt_out", return_value=True), \
                patch.object(servicio, "_registrar_respuesta") as respuesta, \
                patch.object(servicio, "_registrar_retractacion") as retractacion:
            acciones = servicio._procesar_mensajes(self._valor("  Elíminar  "))
        self.assertEqual(acciones, ["eliminacion_solicitada"])
        eliminar.assert_called_once_with(TEL)
        respuesta.assert_not_called()
        retractacion.assert_not_called()

    def test_sin_aviso_previo_no_borra(self):
        servicio = whatsapp_service.WhatsAppService()
        with patch("whatsapp_service.esta_suprimido", return_value=False), \
                patch("whatsapp_service.es_exento", return_value=False), \
                patch("whatsapp_service.es_numero_prueba", return_value=False), \
                patch("whatsapp_service.oferta_eliminacion_enviada", return_value=False), \
                patch("whatsapp_service.solicitar_eliminacion") as eliminar, \
                patch("whatsapp_service.al_detectar_baja") as aviso, \
                patch.object(servicio, "_estaba_opt_out", return_value=True), \
                patch.object(servicio, "_registrar_baja") as baja:
            acciones = servicio._procesar_mensajes(self._valor("eliminar"))
        self.assertEqual(acciones, ["baja_sin_aviso_de_eliminacion"])
        baja.assert_called_once_with(TEL, aviso=False)
        aviso.assert_called_once_with(TEL)
        eliminar.assert_not_called()

    def test_otro_texto_reincorpora_tras_la_baja(self):
        servicio = whatsapp_service.WhatsAppService()
        with patch("whatsapp_service.esta_suprimido", return_value=False), \
                patch("whatsapp_service.es_exento", return_value=False), \
                patch("whatsapp_service.es_numero_prueba", return_value=False), \
                patch.object(servicio, "_estaba_opt_out", side_effect=[True, False]), \
                patch.object(servicio, "_registrar_respuesta", return_value="registrada"), \
                patch.object(servicio, "_registrar_retractacion") as retractacion, \
                patch("whatsapp_service.al_detectar_retractacion") as aviso:
            acciones = servicio._procesar_mensajes(self._valor("hola"))
        retractacion.assert_called_once_with(TEL, aviso=False)
        aviso.assert_called_once_with(TEL)
        self.assertIn("retractacion", acciones)

    def test_otra_baja_no_reincorpora(self):
        servicio = whatsapp_service.WhatsAppService()
        with patch("whatsapp_service.esta_suprimido", return_value=False), \
                patch("whatsapp_service.es_exento", return_value=False), \
                patch("whatsapp_service.es_numero_prueba", return_value=False), \
                patch("whatsapp_service.oferta_eliminacion_enviada", return_value=True), \
                patch.object(servicio, "_estaba_opt_out", return_value=True), \
                patch.object(servicio, "_registrar_respuesta", return_value="registrada"), \
                patch.object(servicio, "_registrar_baja") as baja, \
                patch.object(servicio, "_registrar_retractacion") as retractacion:
            servicio._procesar_mensajes(self._valor("BAJA"))
        baja.assert_called_once_with(TEL, aviso=False)
        retractacion.assert_not_called()

    def test_peticion_ambigua_de_borrado_no_reincorpora(self):
        self.assertTrue(whatsapp_service.WhatsAppService._es_baja(
            "quiero eliminar mis datos"))

    def test_numero_de_prueba_simula_eliminar_y_conserva_datos(self):
        servicio = whatsapp_service.WhatsAppService()
        with patch("whatsapp_service.esta_suprimido", return_value=False), \
                patch("whatsapp_service.es_exento", return_value=True), \
                patch("whatsapp_service.es_numero_prueba", return_value=True), \
                patch("whatsapp_service.solicitar_eliminacion") as eliminar, \
                patch("whatsapp_service.al_detectar_baja") as aviso, \
                patch.object(servicio, "_registrar_baja") as baja, \
                patch.object(servicio, "_registrar_respuesta") as respuesta:
            acciones = servicio._procesar_mensajes(self._valor("ELIMINAR"))
        self.assertEqual(acciones, ["eliminacion_simulada"])
        baja.assert_called_once_with(TEL, aviso=False, simulada=True)
        eliminar.assert_not_called()
        aviso.assert_not_called()
        respuesta.assert_not_called()

    def test_eliminacion_simulada_deja_marcador_reversible(self):
        servicio = whatsapp_service.WhatsAppService()
        conexion = MagicMock()
        cur = conexion.__enter__.return_value.cursor.return_value.__enter__.return_value
        with patch("whatsapp_service.conectar", return_value=conexion), \
                patch.object(servicio, "_pacientes_con_tel", return_value=[{
                    "tabla": "pacientes_dev", "id": 7, "whatsapp_opt_out": 0,
                }]), \
                patch.object(servicio, "_cond_tabla", return_value=("", ())):
            servicio._registrar_baja(TEL, aviso=False, simulada=True)
        sentencias = [c.args[0] for c in cur.execute.call_args_list]
        self.assertTrue(any("'eliminacion_simulada'" in s and "INSERT" in s
                            for s in sentencias))
        self.assertTrue(any("whatsapp_opt_out = 1" in s for s in sentencias))
        self.assertTrue(any("tipo = 'reintegro'" in s and "DELETE" in s
                            for s in sentencias))
        conexion.__enter__.return_value.commit.assert_called_once()

    def test_baja_real_conserva_aviso_de_reincorporacion_ya_enviado(self):
        servicio = whatsapp_service.WhatsAppService()
        conexion = MagicMock()
        cur = conexion.__enter__.return_value.cursor.return_value.__enter__.return_value
        with patch("whatsapp_service.conectar", return_value=conexion), \
                patch("whatsapp_service.es_numero_prueba", return_value=False), \
                patch.object(servicio, "_pacientes_con_tel", return_value=[{
                    "tabla": "pacientes_prod", "id": 7, "whatsapp_opt_out": 0,
                }]), \
                patch.object(servicio, "_cond_tabla", return_value=("", ())):
            servicio._registrar_baja(TEL, aviso=False)
        sentencias = [c.args[0] for c in cur.execute.call_args_list]
        self.assertTrue(any("whatsapp_opt_out = 1" in s for s in sentencias))
        self.assertFalse(any("tipo = 'reintegro'" in s and "DELETE" in s
                             for s in sentencias))
        conexion.__enter__.return_value.commit.assert_called_once()

    def test_baja_de_numero_configurado_para_pruebas_reinicia_aviso(self):
        servicio = whatsapp_service.WhatsAppService()
        conexion = MagicMock()
        cur = conexion.__enter__.return_value.cursor.return_value.__enter__.return_value
        with patch("whatsapp_service.conectar", return_value=conexion), \
                patch("whatsapp_service.es_numero_prueba", return_value=True), \
                patch.object(servicio, "_pacientes_con_tel", return_value=[{
                    "tabla": "pacientes_dev", "id": 7, "whatsapp_opt_out": 0,
                }]), \
                patch.object(servicio, "_cond_tabla", return_value=("", ())):
            servicio._registrar_baja(TEL, aviso=False)
        sentencias = [c.args[0] for c in cur.execute.call_args_list]
        self.assertTrue(any("tipo = 'reintegro'" in s and "DELETE" in s
                            for s in sentencias))

    def test_reincorporacion_quita_marcador_simulado(self):
        servicio = whatsapp_service.WhatsAppService()
        conexion = MagicMock()
        cur = conexion.__enter__.return_value.cursor.return_value.__enter__.return_value
        with patch("whatsapp_service.conectar", return_value=conexion), \
                patch.object(servicio, "_pacientes_con_tel", return_value=[{
                    "tabla": "pacientes_dev", "id": 7, "whatsapp_opt_out": 1,
                }]), \
                patch.object(servicio, "_cond_tabla", return_value=("", ())):
            servicio._registrar_retractacion(TEL, aviso=False)
        sentencias = [c.args[0] for c in cur.execute.call_args_list]
        self.assertTrue(any("whatsapp_opt_out = 0" in s for s in sentencias))
        self.assertTrue(any("'eliminacion_simulada'" in s and "DELETE" in s
                            for s in sentencias))
        conexion.__enter__.return_value.commit.assert_called_once()

    def test_prueba_puede_reincorporarse_despues_de_eliminar_simulado(self):
        servicio = whatsapp_service.WhatsAppService()
        estado = {"baja": False}

        def baja(*_args, **_kwargs):
            estado["baja"] = True

        def volver(*_args, **_kwargs):
            estado["baja"] = False

        with patch("whatsapp_service.esta_suprimido", return_value=False), \
                patch("whatsapp_service.es_exento", return_value=False), \
                patch("whatsapp_service.es_numero_prueba", return_value=True), \
                patch.object(servicio, "_estaba_opt_out", side_effect=lambda _: estado["baja"]), \
                patch.object(servicio, "_registrar_baja", side_effect=baja), \
                patch.object(servicio, "_registrar_retractacion", side_effect=volver) as retractacion, \
                patch.object(servicio, "_registrar_respuesta", return_value="registrada"), \
                patch("whatsapp_service.al_detectar_retractacion"):
            self.assertEqual(servicio._procesar_mensajes(self._valor("ELIMINAR")),
                             ["eliminacion_simulada"])
            self.assertTrue(estado["baja"])
            self.assertIn("retractacion", servicio._procesar_mensajes(self._valor("hola")))
        self.assertFalse(estado["baja"])
        retractacion.assert_called_once_with(TEL, aviso=False)

    def test_lista_de_prueba_acepta_ambos_entornos_y_formatos(self):
        with patch("supresion.config_get", side_effect=lambda clave, _: {
                "numeros_prueba_dev": "56912345678", "numeros_prueba_prod": ""}[clave]):
            self.assertTrue(supresion.es_numero_prueba(TEL))
        with patch("supresion.config_get", side_effect=lambda clave, _: {
                "numeros_prueba_dev": "", "numeros_prueba_prod": "9 1234 5678"}[clave]):
            self.assertTrue(supresion.es_numero_prueba(TEL))

    def test_numero_exento_ignora_eliminar_sin_ofrecer_borrado(self):
        servicio = whatsapp_service.WhatsAppService()
        with patch("whatsapp_service.es_exento", return_value=True), \
                patch("whatsapp_service.es_numero_prueba", return_value=False), \
                patch("whatsapp_service.esta_suprimido", return_value=False), \
                patch("whatsapp_service.solicitar_eliminacion") as eliminar, \
                patch.object(servicio, "_registrar_baja") as baja:
            acciones = servicio._procesar_mensajes(self._valor("ELIMINAR"))
        self.assertEqual(acciones, ["eliminacion_exenta"])
        eliminar.assert_not_called()
        baja.assert_not_called()

    def test_cohorte_con_baja_no_sale_tras_quedar_en_cola(self):
        conexion = MagicMock()
        cur = conexion.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = {"telefono": TEL, "whatsapp_opt_out": 1}
        with patch("main.conectar", return_value=conexion):
            self.assertFalse(main._paciente_habilitado_antes_de_enviar(
                7, TEL, "desarrollo", "pacientes_dev"))

    def test_aviso_de_prueba_no_promete_borrado_real(self):
        with patch("main.esta_suprimido", return_value=False), \
                patch("main.eliminacion_simulada_activa", return_value=False), \
                patch("main.es_numero_prueba", return_value=True), \
                patch("main.es_exento", return_value=False), \
                patch("main._reservar_aviso_unico", return_value=True) as reservar, \
                patch("main._enviar_mensaje_directo", return_value=True) as enviar:
            main._avisar_baja(TEL)
        mensaje = enviar.call_args.args[1]
        self.assertIn("simular", mensaje)
        self.assertIn("reincorporarte", mensaje)
        self.assertIn("sin borrar tus datos", mensaje)
        reservar.assert_called_once_with(TEL, "baja_prueba")

    def test_eliminacion_simulada_silencia_avisos_pendientes(self):
        with patch("main.esta_suprimido", return_value=False), \
                patch("main.eliminacion_simulada_activa", return_value=True), \
                patch("main._enviar_mensaje_directo") as enviar:
            main._avisar_baja(TEL)
            main._avisar_retractacion(TEL)
        enviar.assert_not_called()

    def test_aviso_real_anticipa_borrado_y_reincorporacion(self):
        self.assertIn("ELIMINAR", main._MENSAJE_BAJA_DESPEDIDA)
        self.assertIn("reincorporarte", main._MENSAJE_BAJA_DESPEDIDA)
        self.assertIn("sistema de origen", main._MENSAJE_BAJA_DESPEDIDA)

    def test_solicitud_exenta_no_reintroduce_telefono_ni_inicia_worker(self):
        conexion = MagicMock()
        cur = conexion.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = {"estado": "exento"}
        with patch("supresion.conectar", return_value=conexion), \
                patch("supresion.huella_telefono", return_value="x" * 64), \
                patch("supresion.config_get", return_value="id-test"), \
                patch("supresion.threading.Thread") as hilo:
            self.assertEqual(supresion.solicitar_eliminacion(TEL), "x" * 64)
        hilo.assert_not_called()
        self.assertTrue(any("estado IN ('completo','exento')" in c.args[0]
                            for c in cur.execute.call_args_list))

    def test_motor_simulado_tambien_rechaza_suprimidos(self):
        with patch("motor_envio.esta_suprimido", return_value=True):
            ok, _, motivo = MotorSimulado().enviar(TEL, "mensaje")
        self.assertFalse(ok)
        self.assertIn("suprimido", motivo)

    def test_csv_no_reincorpora_numero_suprimido(self):
        conexion = MagicMock()
        cur = conexion.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value = [{"huella": "x" * 64}]
        csv = b"nombre,apellido,telefono\nMaria,Perez,912345678\n"
        with patch("servicio_areas.obtener_area", return_value={"nombre_tabla_base": "pacientes_test"}), \
                patch("servicio_areas.tabla_valida", return_value=True), \
                patch("servicio_areas.conectar", return_value=conexion), \
                patch("servicio_areas.indice_pacientes_por_telefono", return_value={}), \
                patch("supresion.huella_telefono", return_value="x" * 64):
            informe = servicio_areas.importar_pacientes_csv(1, csv, 2)
        self.assertEqual(informe["omitidos_eliminados"], 1)
        self.assertEqual(informe["insertados"], 0)
        self.assertFalse(any("INSERT INTO pacientes_test" in c.args[0]
                             for c in cur.execute.call_args_list))

    def test_bloqueo_meta_exige_confirmacion_del_numero(self):
        cliente = MagicMock()
        api = cliente.__enter__.return_value
        api.post.return_value.status_code = 200
        api.post.return_value.json.return_value = {
            "block_users": {"added_users": [{"input": TEL.lstrip("+"),
                                               "wa_id": TEL.lstrip("+")}]}}
        with patch("supresion.config_get", side_effect=lambda k: {
                "wa_token": "token-ficticio", "wa_graph_version": "v26.0"}.get(k)), \
                patch("supresion.httpx.Client", return_value=cliente):
            self.assertTrue(supresion._bloquear_en_meta(TEL, "id-ficticio"))
        self.assertIn("/id-ficticio/block_users", api.post.call_args.args[0])
        self.assertEqual(api.post.call_args.kwargs["json"]["block_users"],
                         [{"user": TEL.lstrip("+")}])

    def test_limpieza_conserva_fila_de_costo_sin_datos_completos(self):
        cur = MagicMock()

        def ejecutar(sql, *args):
            if sql.startswith("SELECT id, nombre, apellido, telefono FROM pacientes_prod"):
                cur.fetchall.return_value = [{"id": 7, "nombre": "María", "apellido": "Pérez", "telefono": TEL}]
            elif sql.startswith("SELECT pd.prog_id"):
                cur.fetchall.return_value = [{"prog_id": 4, "paciente_id": 7,
                                               "telefono": TEL, "tabla": "pacientes_prod"}]
            elif sql.startswith("SELECT id, paciente_id, base_datos, numero_paciente"):
                cur.fetchall.return_value = [{"id": 3, "paciente_id": 7,
                                               "base_datos": "pacientes_prod",
                                               "numero_paciente": TEL}]
            elif sql.startswith("SELECT telefono, tipo"):
                cur.fetchall.return_value = [{"telefono": TEL, "tipo": "baja_eliminar"}]
            elif sql.startswith("SELECT id, paciente_id, area_id, tabla_pacientes"):
                cur.fetchall.return_value = [{"id": 8, "paciente_id": 7,
                                               "area_id": None,
                                               "tabla_pacientes": "pacientes_prod",
                                               "nombre_paciente": "María Pérez",
                                               "numero_telefono": TEL}]
            elif sql.startswith("SELECT id, payload FROM whatsapp_eventos"):
                cur.fetchall.return_value = [{"id": 5, "payload": "{'wa_id': '56912345678'}"}]

        cur.execute.side_effect = ejecutar
        with patch("servicio_areas.tablas_de_pacientes", return_value=["pacientes_prod"]), \
                patch("servicio_areas.tabla_valida", return_value=True), \
                patch("servicio_areas.listar_tablas_areas", return_value=[]):
            self.assertEqual(supresion._limpiar_datos(cur, TEL), (1, 1))
        consultas = cur.execute.call_args_list
        self.assertTrue(any("DELETE FROM pacientes_prod" in c.args[0] for c in consultas))
        self.assertTrue(any("DELETE FROM programado_destinatarios" in c.args[0] for c in consultas))
        self.assertTrue(any("DELETE FROM call_center_log" in c.args[0] for c in consultas))
        self.assertTrue(any("UPDATE whatsapp_eventos SET payload" in c.args[0] for c in consultas))
        actualizacion = next(c for c in consultas if "UPDATE log_envios SET" in c.args[0])
        self.assertEqual(actualizacion.args[1][:2], ("Ma Pé", "****5678"))

    def test_area_pequena_no_conserva_nombre_ni_telefono(self):
        cur = MagicMock()

        def ejecutar(sql, *args):
            if sql.startswith("SELECT id, nombre, apellido, telefono FROM pacientes_area"):
                cur.fetchall.return_value = [{"id": 2, "nombre": "María",
                                               "apellido": "Pérez", "telefono": TEL}]
            elif sql.startswith("SELECT id, paciente_id, area_id, tabla_pacientes"):
                cur.fetchall.return_value = [{"id": 10, "paciente_id": 2,
                                               "area_id": 3, "tabla_pacientes": "pacientes_area",
                                               "nombre_paciente": "María Pérez", "numero_telefono": TEL}]
            elif sql.startswith("SELECT "):
                cur.fetchall.return_value = []

        cur.execute.side_effect = ejecutar
        with patch("servicio_areas.tablas_de_pacientes", return_value=["pacientes_area"]), \
                patch("servicio_areas.tabla_valida", return_value=True), \
                patch("servicio_areas.listar_tablas_areas", return_value=[
                    {"id": 3, "nombre_tabla_base": "pacientes_area"}]):
            supresion._limpiar_datos(cur, TEL)
        actualizacion = next(c for c in cur.execute.call_args_list
                             if "UPDATE log_envios SET" in c.args[0])
        self.assertEqual(actualizacion.args[1][:2], (None, None))

    def test_id_compartido_no_redacta_otro_numero(self):
        cur = MagicMock()

        def ejecutar(sql, *args):
            if sql.startswith("SELECT id, nombre, apellido, telefono FROM pacientes_prod"):
                cur.fetchall.return_value = [{"id": 7, "nombre": "María",
                                               "apellido": "Pérez", "telefono": TEL}]
            elif sql.startswith("SELECT pd.prog_id"):
                cur.fetchall.return_value = [{"prog_id": 4, "paciente_id": 7,
                                               "telefono": "+56999999999",
                                               "tabla": "pacientes_prod"}]
            elif sql.startswith("SELECT id, paciente_id, area_id, tabla_pacientes"):
                cur.fetchall.return_value = [{"id": 8, "paciente_id": 7,
                                               "area_id": None,
                                               "tabla_pacientes": "pacientes_prod",
                                               "nombre_paciente": "Otra Persona",
                                               "numero_telefono": "+56999999999"}]
            elif sql.startswith("SELECT "):
                cur.fetchall.return_value = []

        cur.execute.side_effect = ejecutar
        with patch("servicio_areas.tablas_de_pacientes", return_value=["pacientes_prod"]), \
                patch("servicio_areas.tabla_valida", return_value=True), \
                patch("servicio_areas.listar_tablas_areas", return_value=[]):
            supresion._limpiar_datos(cur, TEL)
        consultas = [c.args[0] for c in cur.execute.call_args_list]
        self.assertFalse(any("DELETE FROM programado_destinatarios WHERE" in q for q in consultas))
        self.assertFalse(any("UPDATE log_envios SET" in q for q in consultas))

    def test_cohorte_sin_telefono_se_limpia_por_tabla_e_id(self):
        cur = MagicMock()

        def ejecutar(sql, *args):
            if sql.startswith("SELECT id, nombre, apellido, telefono FROM pacientes_prod"):
                cur.fetchall.return_value = [{"id": 7, "nombre": "María",
                                               "apellido": "Pérez", "telefono": TEL}]
            elif sql.startswith("SELECT pd.prog_id"):
                cur.fetchall.return_value = [{"prog_id": 4, "paciente_id": 7,
                                               "telefono": "", "tabla": "pacientes_prod"}]
            elif sql.startswith("SELECT "):
                cur.fetchall.return_value = []

        cur.execute.side_effect = ejecutar
        with patch("servicio_areas.tablas_de_pacientes", return_value=["pacientes_prod"]), \
                patch("servicio_areas.tabla_valida", return_value=True), \
                patch("servicio_areas.listar_tablas_areas", return_value=[]):
            supresion._limpiar_datos(cur, TEL)
        self.assertTrue(any("DELETE FROM programado_destinatarios WHERE" in c.args[0]
                            for c in cur.execute.call_args_list))

    def test_meta_sin_confirmar_mantiene_pendiente_y_no_borra(self):
        conexion = MagicMock()
        cur = conexion.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.rowcount = 1
        cur.fetchone.return_value = {"telefono_pendiente": TEL, "wa_phone_id": "id-test"}
        with patch("supresion.conectar", return_value=conexion), \
                patch("supresion._bloquear_en_meta", return_value=False), \
                patch("supresion._limpiar_datos") as limpiar:
            self.assertFalse(supresion.procesar_pendiente("x" * 64))
        limpiar.assert_not_called()
        self.assertTrue(any("SET estado = 'pendiente'" in c.args[0]
                            for c in cur.execute.call_args_list))

    def test_meta_confirmado_borra_y_descarta_numero_pendiente(self):
        conexion = MagicMock()
        cur = conexion.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.rowcount = 1
        cur.fetchone.return_value = {"telefono_pendiente": TEL, "wa_phone_id": "id-test"}
        with patch("supresion.conectar", return_value=conexion), \
                patch("supresion._bloquear_en_meta", return_value=True), \
                patch("supresion._limpiar_datos", return_value=(2, 3)) as limpiar:
            self.assertTrue(supresion.procesar_pendiente("x" * 64))
        limpiar.assert_called_once_with(cur, TEL)
        self.assertTrue(any("telefono_pendiente = NULL" in c.args[0]
                            for c in cur.execute.call_args_list))


if __name__ == "__main__":
    unittest.main()
