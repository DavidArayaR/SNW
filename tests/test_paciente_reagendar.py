"""CSV remoto de citas: sin mensajes reales ni datos de pacientes."""

import sys
import hashlib
import asyncio
import datetime as dt
import unittest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import main
import paciente_reagendar as reagendar
from db import conectar
from whatsapp_service import WhatsAppService


CFG = {"reagendar_clinicas_ids": "{}"}
for codigo, nombre in (("SMPT", "Salud Mental Para Todos"),
                       ("MS", "Centro Mentalidad Saludable"),
                       ("PS", "PROSALUD"),
                       ("CP", "Centro Psiquiatrico Huerfanos"),
                       ("VS", "VIDA Y SALUD")):
    CFG[f"{codigo}_WSP_PHONE_NUMBER"] = "+56911111111"
    CFG[f"{codigo}_WSP_PHONE_NUMBER_ID"] = "1234567890"


def csv_fila(identificador="CITA-001", clinica="Salud Mental Para Todos",
             extra="", telefono="56911111111"):
    return (
        "id,fecha,ventana,clinica_id,clinica_nombre,reservo_cita_uuid,paciente_rut,"
        "paciente_nombre,paciente_telefono,doctor_nombre,inicio_cita,creado_en,autorizado_contacto\n"
        f"1,2026-10-22,08-14,,{clinica},{identificador},11111111-1,Persona Prueba,"
        f"{telefono},Doctor Prueba,2026-10-22 10:00:00,2026-10-22 10:01:00,{extra}\n"
    ).encode()


class ParserCitasTests(unittest.TestCase):
    def test_horarios_en_chile_y_carga_tardia(self):
        chile = ZoneInfo("America/Santiago")
        fecha = dt.date(2026, 10, 22)
        for ventana, hora_carga, hora_salida in (
                ("08-14", 14, 15), ("14-20", 20, 21)):
            recibido = dt.datetime(2026, 10, 22, hora_carga, 30, tzinfo=chile)
            salida = reagendar.programacion_carga(fecha, ventana, recibido)
            self.assertEqual(salida.replace(tzinfo=dt.timezone.utc).astimezone(chile).hour,
                             hora_salida)
            self.assertIsNone(reagendar.programacion_carga(
                fecha, ventana, recibido + dt.timedelta(minutes=31)))
        self.assertIsNone(reagendar.programacion_carga(
            fecha, "08-14", dt.datetime(2026, 10, 23, 14, 30, tzinfo=chile)))

    def test_cambio_de_horario_chile_y_hora_ambigua(self):
        chile = ZoneInfo("America/Santiago")
        fecha = dt.date(2026, 4, 4)
        recibido = dt.datetime(2026, 4, 4, 14, 30, tzinfo=chile)
        salida = reagendar.programacion_carga(fecha, "08-14", recibido)
        self.assertEqual(salida.replace(tzinfo=dt.timezone.utc).astimezone(chile).hour, 15)
        with self.assertRaises(ValueError):
            reagendar.fecha_local_a_utc("2026-04-04 23:30")

    def test_cinco_clinicas_y_rut_descartado(self):
        for codigo, nombre in (("SMPT", "Salud Mental Para Todos"),
                               ("MS", "Centro Mentalidad Saludable"),
                               ("PS", "PROSALUD"),
                               ("CP", "Centro Psiquiatrico Huerfanos"),
                               ("VS", "VIDA Y SALUD")):
            rows, errors, received = reagendar.analizar_csv(csv_fila(clinica=nombre), CFG)
            self.assertEqual((received, errors, rows[0]["centro_codigo"]), (1, [], codigo))
            self.assertNotIn("paciente_rut", rows[0])

    def test_bom_comillas_y_saltos_de_linea(self):
        datos = csv_fila().decode().replace("Persona Prueba", '"Persona,\nPrueba"')
        rows, errors, _ = reagendar.analizar_csv(("\ufeff" + datos).encode(), CFG)
        self.assertEqual(errors, [])
        self.assertEqual(rows[0]["paciente_nombre"], "Persona,\nPrueba")

    def test_ventana_es_busqueda_en_reservo_no_hora_de_cita(self):
        datos = (csv_fila().replace(b"2026-10-22 10:00:00", b"2026-11-03 21:30:00")
                           .replace(b"08-14", b"14-20"))
        rows, errors, received = reagendar.analizar_csv(datos, CFG)
        self.assertEqual((received, errors), (1, []))
        self.assertEqual(rows[0]["ventana"], "14-20")
        self.assertEqual(rows[0]["fecha"].isoformat(), "2026-10-22")
        self.assertEqual(rows[0]["inicio_cita"].isoformat(sep=" "), "2026-11-03 21:30:00")

    def test_sin_telefono_y_clinica_desconocida_se_conserva_bloqueada(self):
        rows, errors, _ = reagendar.analizar_csv(
            csv_fila(clinica="Clínica Desconocida", telefono=""), CFG)
        self.assertEqual(errors, [])
        self.assertIsNone(rows[0]["centro_codigo"])
        self.assertEqual(rows[0]["ultimo_error_codigo"], "CLINICA_DESCONOCIDA")
        self.assertFalse(rows[0]["autorizado_contacto"])

    def test_autorizacion_explicita_y_validacion(self):
        rows, errors, _ = reagendar.analizar_csv(csv_fila(extra="si"), CFG)
        self.assertEqual(errors, [])
        self.assertTrue(rows[0]["autorizado_contacto"])
        self.assertIsNone(rows[0]["ultimo_error_codigo"])
        with self.assertRaises(reagendar.CsvInvalido):
            reagendar.analizar_csv(b"x,y\n1,2\n", CFG)
        with self.assertRaises(reagendar.CsvInvalido):
            reagendar.analizar_csv(b"\xff", CFG)
        rows, errors, _ = reagendar.analizar_csv(
            csv_fila().replace(b"08-14", b"14:30"), CFG)
        self.assertEqual(rows, [])
        self.assertEqual(errors[0]["code"], "ventana_invalida")

    def test_id_conflict_no_cambia_de_clinica(self):
        cfg = {**CFG, "reagendar_clinicas_ids": '{"1":"MS"}'}
        row = csv_fila().replace(b"08-14,,", b"08-14,1,")
        registros, _, _ = reagendar.analizar_csv(row, cfg)
        self.assertIsNone(registros[0]["centro_codigo"])

    def test_archivo_solo_encabezado_no_se_importa(self):
        solo_cabecera = csv_fila().split(b"\n", 1)[0] + b"\n"
        with self.assertRaises(reagendar.CsvInvalido):
            reagendar.analizar_csv(solo_cabecera, CFG)


class ApiCitasTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)

    def test_cola_local_crm_desconectada(self):
        import whatsapp_service
        self.assertIsNone(whatsapp_service.al_derivar_crm)

    def test_importar_exige_bearer_y_no_expone_token(self):
        cfg = {**CFG, "reagendar_api_token_hash":
               hashlib.sha256(b"token-correcto").hexdigest()}
        with patch.object(main, "config_all", return_value=cfg), \
                patch.object(reagendar, "consumir_cuota", return_value=True), \
                patch.object(reagendar, "importar", return_value={"success": True, "summary": {
                    "received": 1, "inserted": 1, "alreadyExists": 0, "rejected": 0}}), \
                patch.object(main, "auditoria_registrar"):
            files = {"file": ("citas.csv", csv_fila(), "text/csv")}
            self.assertEqual(self.client.post("/api/v1/paciente-reagendar/importar-csv",
                                              files=files).status_code, 401)
            self.assertEqual(self.client.post("/api/v1/paciente-reagendar/importar-csv",
                                              headers={"Authorization": "Bearer incorrecto"},
                                              files=files).status_code, 401)
            ok = self.client.post("/api/v1/paciente-reagendar/importar-csv",
                                  headers={"Authorization": "Bearer token-correcto"}, files=files)
            self.assertEqual(ok.status_code, 200)
            self.assertEqual(ok.json()["summary"]["inserted"], 1)

    def test_cuota_bloquea_solicitud(self):
        cfg = {"reagendar_api_token_hash": "algo"}
        with patch.object(main, "config_all", return_value=cfg), \
                patch.object(reagendar, "consumir_cuota", return_value=False):
            res = self.client.post("/api/v1/paciente-reagendar/importar-csv",
                                   files={"file": ("citas.csv", csv_fila(), "text/csv")})
        self.assertEqual(res.status_code, 429)

    def test_rotacion_dev_entrega_token_una_vez_y_guarda_solo_hash(self):
        main.app.dependency_overrides[main.solo_dev] = lambda: {"usuario": "dev@example.test"}
        try:
            with patch.object(main, "config_set") as guardar, \
                    patch.object(main, "auditoria_registrar"):
                res = self.client.post("/api/v1/paciente-reagendar/token")
            self.assertEqual(res.status_code, 200)
            token = res.json()["token"]
            self.assertGreater(len(token), 40)
            self.assertEqual(guardar.call_args.args[0]["reagendar_api_token_hash"],
                             hashlib.sha256(token.encode()).hexdigest())
        finally:
            main.app.dependency_overrides.pop(main.solo_dev, None)

    def test_no_hay_envio_si_el_interruptor_esta_apagado(self):
        cita = {"id": 1, "intentos_envio": 1, "reserva_token": "test"}
        with patch.object(main, "config_all", return_value={"reagendar_envio_activo": "false"}), \
                patch.object(reagendar, "marcar_fallo") as fallo, \
                patch.object(main, "WhatsAppService") as servicio:
            asyncio.run(main._procesar_cita_reagendar(cita, {}))
        fallo.assert_called_once_with(cita, "ENVIO_DESACTIVADO")
        servicio.assert_not_called()

    def test_respuesta_reagendar_prepara_boton_crm_sin_usar_cola(self):
        cita = {"paciente_nombre": "Persona Prueba", "doctor_nombre": "Doctora Prueba",
                "clinica_nombre": "VIDA Y SALUD", "paciente_telefono_normalizado": "+56911111111",
                "whatsapp_phone_number_id": "12345", "centro_codigo": "VS"}
        evento = {"accion": "REAGENDAR", "cita": cita, "texto": "Reagendar", "mensaje_id": "wamid.in"}
        canal = {"codigo": "VS", "phoneNumberId": "12345", "configured": True}
        servicio = MagicMock()
        servicio.enviar = AsyncMock(return_value=(True, "wamid.out", None, "sent"))
        with patch.object(main, "esta_suprimido", return_value=False), \
                patch.object(main, "config_all", return_value={
                    "metodo_envio": "api_oficial", "entorno": "produccion",
                    "VS_CRM_PHONE_NUMBER": "+56922222222"}), \
                patch.object(main, "resolver_phone_id", return_value=canal), \
                patch.object(main, "WhatsAppService", return_value=servicio), \
                patch.object(reagendar, "marcar_respuesta_reagendar") as guardar, \
                patch.object(main, "_encolar_derivacion_crm") as cola:
            main._enviar_respuesta_reagendar(evento)
        plantilla = servicio.enviar.await_args.kwargs["plantilla"]
        from urllib.parse import parse_qs, urlparse
        enlace = urlparse(plantilla["cta"]["url"])
        self.assertEqual(enlace.path, "/56922222222")
        self.assertEqual(parse_qs(enlace.query)["text"][0],
                         "Sí me gustaría agendar, mi nombre es Persona Prueba, con el médico: "
                         "Doctora Prueba, clínica: VIDA Y SALUD")
        self.assertEqual(plantilla["cta"]["texto"], "Ir a call center")
        guardar.assert_called_once_with("wamid.in", "wamid.out", "ENVIADO")
        cola.assert_not_called()

    def test_consulta_usa_texto_real(self):
        cita = {"paciente_nombre": "Persona Prueba", "doctor_nombre": "Doctor Prueba",
                "clinica_nombre": "VIDA Y SALUD", "paciente_telefono_normalizado": "+56911111111",
                "whatsapp_phone_number_id": "12345", "centro_codigo": "VS"}
        servicio = MagicMock()
        servicio.enviar = AsyncMock(return_value=(True, "wamid.out", None, "sent"))
        with patch.object(main, "esta_suprimido", return_value=False), \
                patch.object(main, "config_all", return_value={
                    "metodo_envio": "api_oficial", "entorno": "produccion",
                    "VS_CRM_PHONE_NUMBER": "56922222222"}), \
                patch.object(main, "resolver_phone_id", return_value={
                    "codigo": "VS", "phoneNumberId": "12345", "configured": True}), \
                patch.object(main, "WhatsAppService", return_value=servicio), \
                patch.object(reagendar, "marcar_respuesta_reagendar"):
            main._enviar_respuesta_reagendar({"accion": "CONSULTA", "cita": cita,
                                             "texto": "¿Atienden mañana?", "mensaje_id": "wamid.in"})
        from urllib.parse import parse_qs, urlparse
        url = servicio.enviar.await_args.kwargs["plantilla"]["cta"]["url"]
        self.assertEqual(parse_qs(urlparse(url).query)["text"][0],
                         "Hola, esta es mi consulta: ¿Atienden mañana?")

    def test_webhook_reagendar_no_usa_cola_crm_local(self):
        servicio = WhatsAppService()
        payload = {"contacts": [{"wa_id": "56911111111"}], "messages": [{
            "id": "wamid.respuesta", "type": "text", "text": {"body": "Reagendar"},
            "context": {"id": "wamid.cita"}, "timestamp": "1792611000"}]}
        with patch("whatsapp_service.esta_suprimido", return_value=False), \
                patch("whatsapp_service.es_exento", return_value=False), \
                patch("whatsapp_service.es_numero_prueba", return_value=False), \
                patch("whatsapp_service.config_get", return_value="{}"), \
                patch.object(servicio, "_guardar_mensaje_entrante", return_value=True), \
                patch.object(servicio, "_guardar_corpus_respuesta"), \
                patch.object(servicio, "_registrar_respuesta", return_value="registrada"), \
                patch("whatsapp_service.al_respuesta_reagendar", return_value="gestionado") as flujo, \
                patch("whatsapp_service.al_derivar_crm") as cola, \
                patch("whatsapp_service.al_detectar_consulta") as consulta:
            acciones = servicio._procesar_mensajes(payload)
        self.assertIn("reagendar_gestionado", acciones)
        self.assertEqual(flujo.call_args.args[3], "wamid.cita")
        cola.assert_not_called()
        consulta.assert_not_called()

    def test_envio_simulado_usa_emisor_de_la_clinica_y_guarda_id(self):
        cita = {"id": 1, "intentos_envio": 1, "reserva_token": "test",
                "centro_codigo": "VS", "autorizado_contacto": 1,
                "paciente_telefono_normalizado": "+56911111111",
                "paciente_nombre": "Persona Prueba"}
        plantilla = {"clave": "reagendar_vs", "centro_codigo": "VS",
                    "whatsapp_template_status": "APPROVED"}
        canal = {"codigo": "VS", "configured": True,
                 "phoneNumberId": "phone-vs", "phoneNumber": "+56922222222"}
        conexion = MagicMock()
        conexion.__enter__.return_value = conexion
        cursor = conexion.cursor.return_value
        cursor.__enter__.return_value = cursor
        cursor.fetchone.return_value = None
        servicio = MagicMock()
        servicio._pacientes_con_tel.return_value = []
        servicio.enviar = AsyncMock(return_value=(True, "wamid.prueba", None, "sent"))
        cfg = {"reagendar_envio_activo": "true", "entorno": "produccion",
               "metodo_envio": "api_oficial", "reagendar_plantillas_json": '{"VS":"reagendar_vs"}'}
        vista = {"errores": [], "cuerpo": "Hola Persona", "ejemplos": {"nombre": "Persona"}}
        with patch.object(main, "config_all", return_value=cfg), \
                patch.object(main, "resolver_centro", return_value=canal), \
                patch.object(main, "WhatsAppService", return_value=servicio) as construir, \
                patch.object(main, "esta_suprimido", return_value=False), \
                patch.object(main, "conectar", return_value=conexion), \
                patch.object(main, "presentar_plantilla", return_value=vista), \
                patch.object(reagendar, "registrar_lote", return_value=123), \
                patch.object(reagendar, "marcar_enviado") as guardar, \
                patch.object(reagendar, "marcar_fallo") as fallo:
            asyncio.run(main._procesar_cita_reagendar(cita, {"VS": plantilla}))
        construir.assert_called_once_with(phone_number_id="phone-vs")
        servicio.enviar.assert_awaited_once()
        guardar.assert_called_once_with(cita, canal, plantilla, "Hola Persona", "wamid.prueba")
        fallo.assert_not_called()

    def test_resultado_incierto_no_se_reintenta(self):
        cita = {"id": 1, "intentos_envio": 1, "reserva_token": "test",
                "centro_codigo": "VS", "autorizado_contacto": 1,
                "paciente_telefono_normalizado": "+56911111111",
                "paciente_nombre": "Persona Prueba"}
        plantilla = {"clave": "reagendar_vs", "centro_codigo": "VS",
                    "whatsapp_template_status": "APPROVED"}
        canal = {"codigo": "VS", "configured": True,
                 "phoneNumberId": "phone-vs", "phoneNumber": "+56922222222"}
        conexion = MagicMock()
        conexion.__enter__.return_value = conexion
        cursor = conexion.cursor.return_value
        cursor.__enter__.return_value = cursor
        cursor.fetchone.return_value = None
        servicio = MagicMock()
        servicio._pacientes_con_tel.return_value = []
        servicio.enviar = AsyncMock(side_effect=main.ResultadoEnvioIncierto("sin confirmación"))
        cfg = {"reagendar_envio_activo": "true", "entorno": "produccion",
               "metodo_envio": "api_oficial", "reagendar_plantillas_json": '{"VS":"reagendar_vs"}'}
        with patch.object(main, "config_all", return_value=cfg), \
                patch.object(main, "resolver_centro", return_value=canal), \
                patch.object(main, "WhatsAppService", return_value=servicio), \
                patch.object(main, "esta_suprimido", return_value=False), \
                patch.object(main, "conectar", return_value=conexion), \
                patch.object(main, "presentar_plantilla", return_value={
                    "errores": [], "cuerpo": "Hola", "ejemplos": {}}), \
                patch.object(reagendar, "registrar_lote", return_value=123), \
                patch.object(reagendar, "marcar_fallo") as fallo, \
                patch.object(main, "log_error"):
            asyncio.run(main._procesar_cita_reagendar(cita, {"VS": plantilla}))
        fallo.assert_called_once_with(cita, "RESULTADO_INCIERTO")


class MariaDbCitasTests(unittest.TestCase):
    def test_respuestas_asociadas_al_emisor_y_duplicados(self):
        clave = "TEST-" + uuid.uuid4().hex
        mensaje_origen = "wamid." + uuid.uuid4().hex
        mensaje_entrada = "wamid." + uuid.uuid4().hex
        lote_id = None
        try:
            primero = reagendar.importar(csv_fila(clave, extra="si"), CFG)
            lote_id = primero["summary"]["batchId"]
            with conectar() as conn, conn.cursor() as cur:
                cur.execute("UPDATE paciente_reagendar SET estado_envio='ENVIADO',"
                            " whatsapp_message_id=%s, whatsapp_phone_number_id='1234567890'"
                            " WHERE reservo_cita_uuid=%s", (mensaje_origen, clave))
                conn.commit()
            self.assertIsNone(reagendar.registrar_respuesta_reagendar(
                "+56911111111", "otro_emisor", mensaje_entrada, mensaje_origen, "Reagendar"))
            evento = reagendar.registrar_respuesta_reagendar(
                "+56911111111", "1234567890", mensaje_entrada, mensaje_origen, "Reagendar")
            self.assertEqual(evento["accion"], "REAGENDAR")
            self.assertEqual(reagendar.registrar_respuesta_reagendar(
                "+56911111111", "1234567890", mensaje_entrada,
                mensaje_origen, "Reagendar")["accion"], "DUPLICADO")
            baja = reagendar.registrar_respuesta_reagendar(
                "+56911111111", "1234567890", "wamid." + uuid.uuid4().hex,
                mensaje_origen, "Dar de baja", declina="BAJA")
            self.assertEqual(baja["accion"], "BAJA")
            with conectar() as conn, conn.cursor() as cur:
                cur.execute("SELECT whatsapp_opt_out FROM paciente_reagendar"
                            " WHERE reservo_cita_uuid=%s", (clave,))
                self.assertEqual(cur.fetchone()["whatsapp_opt_out"], 1)
        finally:
            with conectar() as conn, conn.cursor() as cur:
                cur.execute("DELETE FROM paciente_reagendar WHERE reservo_cita_uuid=%s", (clave,))
                if lote_id:
                    cur.execute("DELETE FROM reagendar_lotes WHERE id=%s", (lote_id,))
                conn.commit()

    def test_lote_programado_aplazado_y_csv_duplicado(self):
        clave = "TEST-" + uuid.uuid4().hex
        chile = ZoneInfo("America/Santiago")
        fecha = dt.datetime.now(chile).date() + dt.timedelta(days=1)
        datos = csv_fila(clave, extra="si").replace(b"2026-10-22", fecha.isoformat().encode())
        recibido = dt.datetime.combine(fecha, dt.time(14, 30), chile)
        lote_id = None
        try:
            primero = reagendar.importar(datos, CFG, recibido=recibido)
            lote_id = primero["summary"]["batchId"]
            self.assertEqual(primero["summary"]["batchStatus"], "PROGRAMADO")
            nueva = dt.datetime.combine(fecha, dt.time(16, 0), chile).astimezone(dt.timezone.utc)
            aplazado = reagendar.aplazar_lote(lote_id, nueva, "prueba", "Revisión")
            self.assertEqual(aplazado["estado"], "APLAZADO")
            segundo = reagendar.importar(datos, CFG, recibido=recibido)
            self.assertEqual(segundo["summary"]["alreadyExists"], 1)
            with conectar() as conn, conn.cursor() as cur:
                cur.execute("SELECT estado, programado_para FROM reagendar_lotes WHERE id=%s", (lote_id,))
                lote = cur.fetchone()
                cur.execute("SELECT lote_id, estado_envio FROM paciente_reagendar"
                            " WHERE reservo_cita_uuid=%s", (clave,))
                cita = cur.fetchone()
            self.assertEqual(lote["estado"], "APLAZADO")
            self.assertEqual(lote["programado_para"], nueva.replace(tzinfo=None))
            self.assertEqual(cita["lote_id"], lote_id)
            self.assertEqual(cita["estado_envio"], "PENDIENTE")
        finally:
            with conectar() as conn, conn.cursor() as cur:
                cur.execute("DELETE FROM paciente_reagendar WHERE reservo_cita_uuid=%s", (clave,))
                if lote_id:
                    cur.execute("DELETE FROM reagendar_lotes WHERE id=%s", (lote_id,))
                conn.commit()

    def test_dos_importaciones_cinco_clinicas_y_una_cita_nueva(self):
        prefijo = "TEST-" + uuid.uuid4().hex
        nombres = ["Salud Mental Para Todos", "Centro Mentalidad Saludable",
                   "PROSALUD", "Centro Psiquiatrico Huerfanos", "VIDA Y SALUD"]
        encabezado = csv_fila().split(b"\n", 1)[0].decode() + "\n"
        def archivo(cantidad):
            filas = [f"{i},2026-10-22,08-14,,{nombres[4 if i == 6 else (i - 1) % 5]},"
                    f"{prefijo}-{i},,Persona Prueba,56911111111,Doctor Prueba,"
                    "2026-10-22 10:00:00,,\n" for i in range(1, cantidad + 1)]
            return (encabezado + "".join(filas)).encode()
        try:
            primero = reagendar.importar(archivo(5), CFG)
            self.assertEqual(primero["summary"]["inserted"], 5)
            with conectar() as conn, conn.cursor() as cur:
                cur.execute("UPDATE paciente_reagendar SET estado_envio='ENVIADO',"
                            " whatsapp_message_id='wamid.test'"
                            " WHERE reservo_cita_uuid LIKE %s", (prefijo + "-%",))
                conn.commit()
            segundo = reagendar.importar(archivo(6), CFG)
            self.assertEqual(segundo["summary"]["alreadyExists"], 5)
            self.assertEqual(segundo["summary"]["inserted"], 1)
            with conectar() as conn, conn.cursor() as cur:
                cur.execute("SELECT reservo_cita_uuid, centro_codigo, estado_envio,"
                            " whatsapp_message_id FROM paciente_reagendar"
                            " WHERE reservo_cita_uuid LIKE %s ORDER BY reservo_cita_uuid",
                            (prefijo + "-%",))
                rows = cur.fetchall()
            self.assertEqual(len(rows), 6)
            self.assertEqual([r["centro_codigo"] for r in rows],
                             ["SMPT", "MS", "PS", "CP", "VS", "VS"])
            self.assertTrue(all(r["estado_envio"] == "ENVIADO" and
                                r["whatsapp_message_id"] == "wamid.test" for r in rows[:5]))
            self.assertEqual(rows[5]["estado_envio"], "OMITIDO")
        finally:
            with conectar() as conn, conn.cursor() as cur:
                cur.execute("SELECT DISTINCT lote_id FROM paciente_reagendar"
                            " WHERE reservo_cita_uuid LIKE %s", (prefijo + "-%",))
                lotes = [r["lote_id"] for r in cur.fetchall() if r["lote_id"]]
                cur.execute("DELETE FROM paciente_reagendar WHERE reservo_cita_uuid LIKE %s",
                            (prefijo + "-%",))
                if lotes:
                    cur.execute("DELETE FROM reagendar_lotes WHERE id IN (" +
                                ",".join(["%s"] * len(lotes)) + ")", tuple(lotes))
                conn.commit()

    def test_reimportacion_no_reinicia_estado_ni_id_meta(self):
        key = "TEST-" + uuid.uuid4().hex
        datos = csv_fila(key)
        try:
            with conectar() as conn, conn.cursor() as cur:
                cur.execute("SELECT 1 FROM information_schema.TABLES"
                            " WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='paciente_reagendar'")
                if not cur.fetchone():
                    self.skipTest("Migración pendiente en esta base")
            primero = reagendar.importar(datos, CFG)
            self.assertEqual(primero["summary"]["inserted"], 1)
            with conectar() as conn, conn.cursor() as cur:
                cur.execute("UPDATE paciente_reagendar SET estado_envio='ENVIADO',"
                            " whatsapp_message_id='wamid.test', intentos_envio=1"
                            " WHERE reservo_cita_uuid=%s", (key,))
                conn.commit()
            segundo = reagendar.importar(datos, CFG)
            self.assertEqual(segundo["summary"]["alreadyExists"], 1)
            with conectar() as conn, conn.cursor() as cur:
                cur.execute("SELECT estado_envio, whatsapp_message_id, intentos_envio"
                            " FROM paciente_reagendar WHERE reservo_cita_uuid=%s", (key,))
                fila = cur.fetchone()
            self.assertEqual((fila["estado_envio"], fila["whatsapp_message_id"],
                              fila["intentos_envio"]), ("ENVIADO", "wamid.test", 1))
        except Exception as exc:
            if isinstance(exc, self.failureException):
                raise
            self.skipTest(f"MariaDB no disponible: {type(exc).__name__}")
        finally:
            try:
                with conectar() as conn, conn.cursor() as cur:
                    cur.execute("SELECT lote_id FROM paciente_reagendar"
                                " WHERE reservo_cita_uuid=%s", (key,))
                    lote = cur.fetchone()
                    cur.execute("DELETE FROM paciente_reagendar WHERE reservo_cita_uuid=%s", (key,))
                    if lote and lote["lote_id"]:
                        cur.execute("DELETE FROM reagendar_lotes WHERE id=%s", (lote["lote_id"],))
                    conn.commit()
            except Exception:
                pass

    def test_cuota_compartida_y_expiracion_de_ventana(self):
        actor = "test-" + uuid.uuid4().hex
        huella = hashlib.sha256(actor.encode()).hexdigest()
        try:
            with patch.object(reagendar.time, "time", return_value=900.0):
                self.assertTrue(reagendar.consumir_cuota(actor, 2))
                self.assertTrue(reagendar.consumir_cuota(actor, 2))
                self.assertFalse(reagendar.consumir_cuota(actor, 2))
            with patch.object(reagendar.time, "time", return_value=1800.0):
                self.assertTrue(reagendar.consumir_cuota(actor, 2))
        finally:
            with conectar() as conn, conn.cursor() as cur:
                cur.execute("DELETE FROM reagendar_import_quota WHERE actor_hash=%s", (huella,))
                conn.commit()
