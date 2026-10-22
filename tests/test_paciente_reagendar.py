"""CSV remoto de citas: sin mensajes reales ni datos de pacientes."""

import sys
import hashlib
import asyncio
import unittest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import main
import paciente_reagendar as reagendar
from db import conectar


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
            self.assertEqual(rows[5]["estado_envio"], "PENDIENTE")
        finally:
            with conectar() as conn, conn.cursor() as cur:
                cur.execute("DELETE FROM paciente_reagendar WHERE reservo_cita_uuid LIKE %s",
                            (prefijo + "-%",))
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
                    cur.execute("DELETE FROM paciente_reagendar WHERE reservo_cita_uuid=%s", (key,))
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
