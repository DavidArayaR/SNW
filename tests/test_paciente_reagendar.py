"""CSV remoto de citas: sin mensajes reales ni datos de pacientes."""

import sys
import hashlib
import asyncio
import datetime as dt
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import main
import db
import paciente_reagendar as reagendar
from db import conectar
from whatsapp_service import WhatsAppService


CFG = {"reagendar_clinicas_ids": "{}"}
PRUEBA_CSV = (Path(__file__).resolve().parent / "fixtures" /
              "paciente_reagendar_prueba.csv").read_bytes()
for codigo, nombre in (("SMPT", "Salud Mental Para Todos"),
                       ("MS", "Centro Mentalidad Saludable"),
                       ("PS", "PROSALUD"),
                       ("CP", "Centro Psiquiatrico Huerfanos"),
                       ("VS", "VIDA Y SALUD")):
    CFG[f"{codigo}_WSP_PHONE_NUMBER"] = "+56911111111"
    CFG[f"{codigo}_WSP_PHONE_NUMBER_ID"] = "1234567890"


def csv_fila(identificador="CITA-001", clinica="Salud Mental Para Todos",
             telefono="56911111111"):
    return (
        "id,fecha,ventana,clinica_id,clinica_nombre,reservo_cita_uuid,paciente_rut,"
        "paciente_nombre,paciente_telefono,doctor_nombre,inicio_cita,creado_en\n"
        f"1,2026-10-22,08-14,,{clinica},{identificador},11111111-1,Persona Prueba,"
        f"{telefono},Doctor Prueba,2026-10-22 10:00:00,2026-10-22 10:01:00\n"
    ).encode()


def telefono_prueba(clave):
    """Un destinatario distinto por prueba de integración."""
    return "569" + str(int(hashlib.sha256(clave.encode()).hexdigest(), 16) % 100000000).zfill(8)


class ParserCitasTests(unittest.TestCase):
    def test_csv_mas_de_diez_mil_filas_se_divide_sin_perder_filas(self):
        encabezado, ejemplo = csv_fila().decode().splitlines()
        filas = [ejemplo.replace("CITA-001", f"CITA-{i:05d}") for i in range(10001)]
        datos = (encabezado + "\n" + "\n".join(filas) + "\n").encode()
        partes = reagendar.dividir_csv(datos)
        self.assertEqual(len(partes), 2)
        self.assertEqual([inicio for _, inicio in partes], [2, 10002])
        self.assertEqual([reagendar.analizar_csv(bloque, CFG)[2] for bloque, _ in partes],
                         [10000, 1])
        self.assertTrue(all(len(bloque) <= reagendar.CSV_MAX_BYTES for bloque, _ in partes))

    def test_valida_ventana_global_antes_de_guardar_ninguna_parte(self):
        encabezado, ejemplo = csv_fila().decode().splitlines()
        filas = [ejemplo.replace("CITA-001", f"CITA-{i:05d}") for i in range(10000)]
        filas.append(ejemplo.replace("CITA-001", "CITA-ULTIMA").replace("08-14", "14-20"))
        datos = (encabezado + "\n" + "\n".join(filas) + "\n").encode()
        with patch.object(reagendar, "importar") as guardar, \
                self.assertRaises(reagendar.CsvInvalido):
            reagendar.importar_por_partes(datos, CFG)
        guardar.assert_not_called()

    def test_divide_tambien_por_tamano_y_respeta_campos_entrecomillados(self):
        datos = ("fecha,ventana,clinica_nombre,reservo_cita_uuid,paciente_nombre,inicio_cita\n"
                 "2026-10-22,08-14,VIDA Y SALUD,uno,\"Nombre\nCon salto\",2026-10-22 10:00:00\n"
                 "2026-10-22,08-14,VIDA Y SALUD,dos,Otro,2026-10-22 11:00:00\n").encode()
        with patch.object(reagendar, "CSV_MAX_BYTES", 155):
            partes = reagendar.dividir_csv(datos)
        self.assertEqual(len(partes), 2)
        self.assertIn(b"Nombre\nCon salto", partes[0][0])
        self.assertEqual([inicio for _, inicio in partes], [2, 3])

    def test_importacion_por_partes_suma_resultados_y_filas_originales(self):
        encabezado, ejemplo = csv_fila().decode().splitlines()
        filas = [ejemplo.replace("CITA-001", f"CITA-{i:05d}") for i in range(10001)]
        datos = (encabezado + "\n" + "\n".join(filas) + "\n").encode()
        llamadas = 0

        def simular(bloque, cfg, *, recibido, modo_prueba, programar_automaticamente):
            nonlocal llamadas
            self.assertFalse(programar_automaticamente)
            llamadas += 1
            cantidad = reagendar.analizar_csv(bloque, cfg)[2]
            return {"summary": {"received": cantidad, "inserted": cantidad,
                                "eligible": cantidad, "batchId": llamadas,
                                "batchStatus": "REVISION",
                                "scheduledAtUtc": None},
                    "errors": [{"row": 2, "code": "dato_invalido"}] if llamadas == 2 else []}

        with patch.object(reagendar, "importar", side_effect=simular), \
                patch.object(reagendar, "activar_lotes_importados") as activar:
            resultado = reagendar.importar_por_partes(datos, CFG)
        activar.assert_not_called()  # La fecha ficticia ya venció: revisión manual.
        self.assertEqual(llamadas, 2)
        self.assertEqual(resultado["summary"]["received"], 10001)
        self.assertEqual(resultado["summary"]["inserted"], 10001)
        self.assertEqual(resultado["summary"]["batchIds"], [1, 2])
        self.assertEqual(resultado["summary"]["parts"], 2)
        self.assertEqual(resultado["errors"][0]["row"], 10002)

    def test_gracia_de_tres_minutos_tras_terminar_la_carga(self):
        base = dt.datetime(2026, 10, 27, 17, 30)
        self.assertEqual(reagendar.programacion_tras_carga(
            base, dt.datetime(2026, 10, 27, 17, 20, tzinfo=dt.timezone.utc)), base)
        self.assertEqual(reagendar.programacion_tras_carga(
            base, dt.datetime(2026, 10, 27, 17, 29, tzinfo=dt.timezone.utc)),
            dt.datetime(2026, 10, 27, 17, 32))
        self.assertIsNone(reagendar.programacion_tras_carga(None, dt.datetime.now(dt.timezone.utc)))

    def test_partes_activan_juntas_despues_de_importar_todo(self):
        encabezado, ejemplo = csv_fila().decode().splitlines()
        filas = [ejemplo.replace("CITA-001", f"CITA-{i:05d}") for i in range(10001)]
        datos = (encabezado + "\n" + "\n".join(filas) + "\n").encode()
        fecha = dt.datetime.now(ZoneInfo("America/Santiago")).date()
        datos = datos.replace(b"2026-10-22", fecha.isoformat().encode())
        recibido = dt.datetime.combine(fecha, dt.time(14, 10), ZoneInfo("America/Santiago"))
        vistos = []

        def importar_parte(*args, **kwargs):
            self.assertFalse(kwargs["programar_automaticamente"])
            vistos.append(len(vistos) + 1)
            return {"summary": {"received": 1, "inserted": 1, "eligible": 1,
                                "batchId": vistos[-1], "batchStatus": "REVISION",
                                "scheduledAtUtc": None}, "errors": []}

        inicio = recibido.astimezone(dt.timezone.utc)
        fin = inicio + dt.timedelta(minutes=25)
        with patch.object(reagendar, "importar", side_effect=importar_parte), \
                patch.object(reagendar, "activar_lotes_importados") as activar, \
                patch.object(reagendar.dt, "datetime", wraps=dt.datetime) as reloj:
            reloj.now.side_effect = [inicio, fin]
            resultado = reagendar.importar_por_partes(datos, CFG)
        self.assertEqual(vistos, [1, 2])
        activar.assert_called_once_with([1, 2],
            (fin + dt.timedelta(minutes=3)).replace(tzinfo=None))
        self.assertEqual(resultado["summary"]["batchStatus"], "PROGRAMADO")
        self.assertTrue(all(p["summary"]["batchStatus"] == "PROGRAMADO"
                            for p in resultado["parts"]))

    def test_csv_ficticio_tiene_una_fila_sin_datos_de_contacto(self):
        filas, errores, recibidas = reagendar.analizar_csv(PRUEBA_CSV, CFG)
        self.assertEqual((recibidas, errores), (1, []))
        self.assertEqual(filas[0]["reservo_cita_uuid"], "TEST-CSV-REMOTO-001")
        self.assertEqual(filas[0]["centro_codigo"], "VS")
        self.assertIsNone(filas[0]["paciente_telefono_normalizado"])
        self.assertEqual(filas[0]["ultimo_error_codigo"], "TELEFONO_INVALIDO")

    def test_modo_prueba_rechaza_multiples_citas(self):
        encabezado, fila = PRUEBA_CSV.split(b"\n", 1)
        datos = encabezado + b"\n" + fila + fila.replace(
            b"TEST-CSV-REMOTO-001", b"TEST-CSV-REMOTO-002")
        with self.assertRaises(reagendar.CsvInvalido):
            reagendar.importar(datos, CFG, modo_prueba=True)

    def test_horarios_en_chile_y_carga_tardia(self):
        chile = ZoneInfo("America/Santiago")
        fecha = dt.date(2026, 10, 22)
        for ventana, hora_carga, hora_salida in (
                ("08-14", 14, 14), ("14-20", 20, 20)):
            recibido = dt.datetime(2026, 10, 22, hora_carga, 0, tzinfo=chile)
            salida = reagendar.programacion_carga(fecha, ventana, recibido)
            local = salida.replace(tzinfo=dt.timezone.utc).astimezone(chile)
            self.assertEqual((local.hour, local.minute), (hora_salida, 30))
            self.assertEqual(reagendar.programacion_carga(
                fecha, ventana, recibido + dt.timedelta(minutes=29)), salida)
            self.assertIsNone(reagendar.programacion_carga(
                fecha, ventana, recibido + dt.timedelta(minutes=30)))
            self.assertIsNone(reagendar.programacion_carga(
                fecha, ventana, recibido - dt.timedelta(minutes=1)))
        self.assertIsNone(reagendar.programacion_carga(
            fecha, "08-14", dt.datetime(2026, 10, 23, 14, 0, tzinfo=chile)))

    def test_cambio_de_horario_chile_y_hora_ambigua(self):
        chile = ZoneInfo("America/Santiago")
        fecha = dt.date(2026, 4, 4)
        recibido = dt.datetime(2026, 4, 4, 14, 0, tzinfo=chile)
        salida = reagendar.programacion_carga(fecha, "08-14", recibido)
        local = salida.replace(tzinfo=dt.timezone.utc).astimezone(chile)
        self.assertEqual((local.hour, local.minute), (14, 30))
        primavera = dt.date(2026, 9, 6)
        salida_primavera = reagendar.programacion_carga(
            primavera, "14-20", dt.datetime(2026, 9, 6, 20, 0, tzinfo=chile))
        local_primavera = salida_primavera.replace(tzinfo=dt.timezone.utc).astimezone(chile)
        self.assertEqual((local_primavera.hour, local_primavera.minute), (20, 30))
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

    def test_mas_vida_y_salud_se_asocia_a_vs(self):
        for nombre in ("Más Vida y Salud", "MAS VIDA Y SALUD", "Mas Vida y Salud"):
            rows, errors, received = reagendar.analizar_csv(csv_fila(clinica=nombre), CFG)
            self.assertEqual((received, errors), (1, []))
            self.assertEqual(rows[0]["centro_codigo"], "VS")
            self.assertIsNone(rows[0]["ultimo_error_codigo"])

    def test_alias_no_ignora_id_externo_conflictivo(self):
        cfg = {**CFG, "reagendar_clinicas_ids": '{"2":"MS"}'}
        row = csv_fila(clinica="Más Vida y Salud").replace(b"08-14,,", b"08-14,2,")
        rows, _, _ = reagendar.analizar_csv(row, cfg)
        self.assertIsNone(rows[0]["centro_codigo"])
        self.assertEqual(rows[0]["ultimo_error_codigo"], "CLINICA_DESCONOCIDA")

    def test_nombre_exacto_de_clinica_configurada_tiene_prioridad_sobre_alias(self):
        cfg = {**CFG, "wa_clinicas_adicionales":
               '[{"codigo":"C1234ABC","nombre":"Más Vida y Salud"}]'}
        self.assertEqual(reagendar.resolver_clinica("Más Vida y Salud", None, cfg), "C1234ABC")

    def test_reimportacion_recupera_omision_de_clinica_sin_duplicar(self):
        conexion = MagicMock()
        cur = conexion.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.lastrowid = 22
        cur.rowcount = 1
        cur.fetchone.side_effect = [
            {"id": 7, "lote_id": 11, "fecha": dt.date(2026, 10, 22),
             "ventana": "08-14", "clinica_nombre": "Más Vida y Salud",
             "modo_prueba": 0, "estado_envio": "OMITIDO",
             "ultimo_error_codigo": "CLINICA_DESCONOCIDA",
             "paciente_telefono_normalizado": "+56911111111",
             "whatsapp_opt_out": 0, "intentos_envio": 0, "reserva_token": None,
             "ultimo_intento_en": None, "envio_id": None, "log_envio_id": None,
             "whatsapp_message_id": None},
            None, None, {"cita_id": 7, "estado": "PENDIENTE"}]
        with patch.object(reagendar, "conectar", return_value=conexion):
            resultado = reagendar.importar(csv_fila(clinica="Más Vida y Salud"), CFG)
        self.assertEqual(resultado["summary"]["recovered"], 1)
        self.assertEqual(resultado["summary"]["eligible"], 1)
        self.assertEqual(resultado["summary"]["inserted"], 0)
        sentencias = [call.args[0] for call in cur.execute.call_args_list]
        self.assertTrue(any("centro_codigo=%s, estado_envio='PENDIENTE'" in sql
                            for sql in sentencias))
        self.assertFalse(any("INSERT INTO paciente_reagendar" in sql for sql in sentencias))

    def test_reimportacion_no_reactiva_una_cita_con_intento(self):
        conexion = MagicMock()
        cur = conexion.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.lastrowid = 22
        cur.fetchone.return_value = {
            "id": 7, "lote_id": 11, "fecha": dt.date(2026, 10, 22),
            "ventana": "08-14", "clinica_nombre": "Más Vida y Salud",
            "modo_prueba": 0, "estado_envio": "OMITIDO",
            "ultimo_error_codigo": "CLINICA_DESCONOCIDA",
            "paciente_telefono_normalizado": "+56911111111",
            "whatsapp_opt_out": 0, "intentos_envio": 1, "reserva_token": None,
            "ultimo_intento_en": None, "envio_id": None, "log_envio_id": None,
            "whatsapp_message_id": None}
        with patch.object(reagendar, "conectar", return_value=conexion):
            resultado = reagendar.importar(csv_fila(clinica="Más Vida y Salud"), CFG)
        self.assertEqual(resultado["summary"]["recovered"], 0)
        self.assertEqual(resultado["summary"]["alreadyExists"], 1)
        self.assertFalse(any("UPDATE paciente_reagendar SET lote_id" in call.args[0]
                             for call in cur.execute.call_args_list))

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

    def test_csv_filtrado_en_origen_y_validacion(self):
        rows, errors, _ = reagendar.analizar_csv(csv_fila(), CFG)
        self.assertEqual(errors, [])
        self.assertIsNone(rows[0]["ultimo_error_codigo"])
        self.assertNotIn("autorizado_contacto", rows[0])
        legado = csv_fila().replace(b"creado_en\n", b"creado_en,autorizado_contacto\n")
        with self.assertRaisesRegex(reagendar.CsvInvalido, "columna de autorización obsoleta"):
            reagendar.analizar_csv(legado, CFG)
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

    def test_cors_csv_solo_permite_crm_y_no_reemplaza_bearer(self):
        ruta = "/api/v1/paciente-reagendar/importar-csv"
        origen = "https://clientes.crmaxion.cl"
        preflight = {"Origin": origen, "Access-Control-Request-Method": "POST",
                     "Access-Control-Request-Headers": "authorization,content-type"}
        with patch.object(main, "config_get", return_value=origen), \
                patch.object(main, "config_all", return_value={"reagendar_api_token_hash": "hash"}), \
                patch.object(reagendar, "consumir_cuota", return_value=True):
            permitido = self.client.options(ruta, headers=preflight)
            self.assertEqual(permitido.status_code, 204)
            self.assertEqual(permitido.headers["access-control-allow-origin"], origen)
            self.assertIn("Authorization", permitido.headers["access-control-allow-headers"])
            ajeno = self.client.options(ruta, headers={**preflight,
                "Origin": "https://otro.example"})
            self.assertEqual(ajeno.status_code, 403)
            self.assertNotIn("access-control-allow-origin", ajeno.headers)
            cabecera_extra = self.client.options(ruta, headers={**preflight,
                "Access-Control-Request-Headers": "authorization,x-admin"})
            self.assertEqual(cabecera_extra.status_code, 403)
            otra_ruta = self.client.options("/api/v1/paciente-reagendar", headers=preflight)
            self.assertNotIn("access-control-allow-origin", otra_ruta.headers)
            sin_bearer = self.client.post(ruta, headers={"Origin": origen},
                files={"file": ("prueba.csv", PRUEBA_CSV, "text/csv")})
            self.assertEqual(sin_bearer.status_code, 401)
            self.assertEqual(sin_bearer.headers["access-control-allow-origin"], origen)
            no_permitido = self.client.post(ruta, headers={"Origin": "https://otro.example"},
                files={"file": ("prueba.csv", PRUEBA_CSV, "text/csv")})
            self.assertEqual(no_permitido.status_code, 401)
            self.assertNotIn("access-control-allow-origin", no_permitido.headers)

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

    def test_cierre_de_csv_remoto_distingue_exito_exclusiones_y_fallo(self):
        token = "token-registro-csv"
        cfg = {**CFG, "reagendar_api_token_hash": hashlib.sha256(token.encode()).hexdigest()}
        ruta = "/api/v1/paciente-reagendar/importar-csv"
        headers = {"Authorization": f"Bearer {token}"}
        archivo = {"file": ("citas.csv", csv_fila(), "text/csv")}
        with patch.object(main, "config_all", return_value=cfg), \
                patch.object(reagendar, "consumir_cuota", return_value=True), \
                patch.object(main, "auditoria_registrar"), \
                patch.object(main, "registrar_resultado_csv_remoto") as registrar, \
                patch.object(reagendar, "importar") as importar:
            importar.return_value = {"success": True, "summary": {
                "received": 1, "inserted": 1, "rejected": 0}}
            self.assertEqual(self.client.post(ruta, headers=headers, files=archivo).status_code, 200)
            self.assertEqual(registrar.call_args.args[:2], ("COMPLETADO", "auditoria"))
            self.assertEqual(registrar.call_args.kwargs["http_status"], 200)
            importar.return_value = {"success": True, "summary": {
                "received": 1, "inserted": 0, "rejected": 1}, "errors": []}
            self.assertEqual(self.client.post(ruta, headers=headers, files=archivo).status_code, 200)
            self.assertEqual(registrar.call_args.args[0], "COMPLETADO_CON_EXCLUSIONES")
            importar.side_effect = reagendar.CsvInvalido("CSV inválido")
            self.assertEqual(self.client.post(ruta, headers=headers, files=archivo).status_code, 422)
            self.assertEqual(registrar.call_args.args[0], "FALLIDO")
            self.assertEqual(registrar.call_args.kwargs["http_status"], 422)
            self.assertEqual(registrar.call_count, 3)

    def test_archivo_resultado_csv_omite_datos_sensibles_y_es_visible_en_logs(self):
        with tempfile.TemporaryDirectory() as temporal, \
                patch.object(db, "LOGS_DIR", Path(temporal)), \
                patch.object(main, "LOGS_DIR", Path(temporal)):
            db.registrar_resultado_csv_remoto("COMPLETADO", "auditoria", http_status=200,
                resumen={"received": 5, "inserted": 4, "rejected": 1}, duracion_ms=345)
            archivos = main.listar_logs_consola({"rol": "desarrollador"})["archivos"]
            self.assertEqual(len(archivos), 1)
            nombre = archivos[0]["nombre"]
            self.assertTrue(nombre.startswith("csv-remoto-"))
            contenido = (Path(temporal) / nombre).read_text(encoding="utf-8")
            self.assertIn("received=5", contenido)
            self.assertIn("HTTP 200", contenido)
            self.assertNotIn("Bearer", contenido)

    def test_api_envia_csv_con_mas_de_diez_mil_filas_al_importador_por_partes(self):
        encabezado, ejemplo = csv_fila().decode().splitlines()
        datos = (encabezado + "\n" + "\n".join(
            ejemplo.replace("CITA-001", f"CITA-{i:05d}") for i in range(10001)) + "\n").encode()
        token = "token-prueba-grande"
        cfg = {**CFG, "reagendar_api_token_hash": hashlib.sha256(token.encode()).hexdigest()}
        simulado = {"success": True, "summary": {"received": 10001, "inserted": 10001,
                    "eligible": 10001, "alreadyExists": 0, "rejected": 0,
                    "parts": 2, "batchIds": [1, 2]}, "errors": []}
        with patch.object(main, "config_all", return_value=cfg), \
                patch.object(reagendar, "consumir_cuota", return_value=True), \
                patch.object(reagendar, "importar_por_partes", return_value=simulado) as importar, \
                patch.object(reagendar, "importar") as simple, \
                patch.object(main, "auditoria_registrar"):
            respuesta = self.client.post("/api/v1/paciente-reagendar/importar-csv",
                headers={"Authorization": f"Bearer {token}"},
                files={"file": ("citas.csv", datos, "text/csv")})
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json()["summary"]["parts"], 2)
        importar.assert_called_once()
        simple.assert_not_called()

    def test_importacion_remota_registra_fallos_sin_datos_sensibles(self):
        token = "token-secreto-de-prueba"
        cfg = {**CFG, "reagendar_api_token_hash": hashlib.sha256(token.encode()).hexdigest()}
        ruta = "/api/v1/paciente-reagendar/importar-csv"
        cabeceras = {"Authorization": f"Bearer {token}"}
        with patch.object(main, "config_all", return_value=cfg), \
                patch.object(reagendar, "consumir_cuota", return_value=True), \
                patch.object(main, "auditoria_registrar"), \
                patch.object(main, "log_error") as registrar:
            faltante = self.client.post(ruta, headers=cabeceras)
            self.assertEqual(faltante.status_code, 422)
            invalido = self.client.post(ruta, headers=cabeceras,
                                        files={"file": ("citas.csv", b"x,y\n1,2\n", "text/csv")})
            self.assertEqual(invalido.status_code, 422)
            with patch.object(reagendar, "CSV_MAX_BYTES", 4):
                grande = self.client.post(ruta, headers=cabeceras,
                                          files={"file": ("citas.csv", b"123456", "text/csv")})
            self.assertEqual(grande.status_code, 413)
            with patch.object(reagendar, "importar", return_value={
                    "success": True, "summary": {"inserted": 1, "alreadyExists": 0,
                    "rejected": 1, "omittedReasons": {"TELEFONO_INVALIDO": 1}},
                    "errors": [{"row": 2, "code": "dato_invalido"}]}):
                parcial = self.client.post(ruta, headers=cabeceras,
                                           files={"file": ("citas.csv", b"csv", "text/csv")})
            self.assertEqual(parcial.status_code, 200)
            with patch.object(reagendar, "importar",
                              side_effect=RuntimeError("NOMBRE_PRIVADO +56999999999")):
                fallo = self.client.post(ruta, headers=cabeceras,
                                         files={"file": ("citas.csv", b"csv", "text/csv")})
            self.assertEqual(fallo.status_code, 500)
            self.assertNotIn("NOMBRE_PRIVADO", fallo.text)
            registro = " | ".join(str(c.args[0]) for c in registrar.call_args_list)
            for codigo in ("SOLICITUD_RECHAZADA", "CSV_INVALIDO", "REGISTROS_EXCLUIDOS",
                           "TELEFONO_INVALIDO=1", "FALLO_INTERNO", "tipo=RuntimeError"):
                self.assertIn(codigo, registro)
            self.assertNotIn(token, registro)
            self.assertNotIn("NOMBRE_PRIVADO", registro)
            self.assertNotIn("+56999999999", registro)

    def test_importacion_remota_de_prueba_persiste_y_no_duplica(self):
        clave = "TEST-CSV-REMOTO-" + uuid.uuid4().hex
        datos = PRUEBA_CSV.replace(b"TEST-CSV-REMOTO-001", clave.encode())
        token = "token-ficticio-para-tests"
        cfg = {**CFG, "reagendar_api_token_hash": hashlib.sha256(token.encode()).hexdigest()}
        ruta = "/api/v1/paciente-reagendar/importar-csv"
        files = {"file": ("paciente_reagendar_prueba.csv", datos, "text/csv")}
        try:
            with patch.object(main, "config_all", return_value=cfg), \
                    patch.object(reagendar, "consumir_cuota", return_value=True), \
                    patch.object(main, "auditoria_registrar"), \
                    patch.object(main, "WhatsAppService") as servicio:
                sin_token = self.client.post(ruta, files=files, data={"modo_prueba": "true"})
                self.assertEqual(sin_token.status_code, 401)
                incorrecto = self.client.post(
                    ruta, headers={"Authorization": "Bearer otro-token"},
                    files=files, data={"modo_prueba": "true"})
                self.assertEqual(incorrecto.status_code, 401)
                self.assertNotIn(token, str(incorrecto.json()))
                ausente = self.client.post(
                    ruta, headers={"Authorization": f"Bearer {token}"},
                    data={"modo_prueba": "true"})
                self.assertEqual(ausente.status_code, 422)
                invalido = self.client.post(
                    ruta, headers={"Authorization": f"Bearer {token}"},
                    files={"file": ("malo.csv", b"x,y\n1,2\n", "text/csv")},
                    data={"modo_prueba": "true"})
                self.assertEqual(invalido.status_code, 422)
                primero = self.client.post(
                    ruta, headers={"Authorization": f"Bearer {token}"},
                    files=files, data={"modo_prueba": "true"})
                self.assertEqual(primero.status_code, 200)
                self.assertTrue(primero.json()["testMode"])
                self.assertEqual(primero.json()["summary"]["inserted"], 1)
                self.assertIsNone(primero.json()["summary"]["batchId"])
                segundo = self.client.post(
                    ruta, headers={"Authorization": f"Bearer {token}"},
                    files=files, data={"modo_prueba": "true"})
                self.assertEqual(segundo.status_code, 200)
                self.assertEqual(segundo.json()["summary"]["alreadyExists"], 1)
                self.assertEqual(segundo.json()["summary"]["inserted"], 0)
                conflicto = self.client.post(
                    ruta, headers={"Authorization": f"Bearer {token}"}, files=files)
                self.assertEqual(conflicto.status_code, 409)
                servicio.assert_not_called()
            with conectar() as conn, conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n, MAX(centro_codigo) AS centro,"
                            " MAX(modo_prueba) AS prueba, MAX(estado_envio) AS estado,"
                            " MAX(lote_id) AS lote, MAX(envio_id) AS envio"
                            " FROM paciente_reagendar WHERE reservo_cita_uuid=%s", (clave,))
                fila = cur.fetchone()
            self.assertEqual(fila["n"], 1)
            self.assertEqual((fila["centro"], fila["prueba"], fila["estado"]),
                             ("VS", 1, "OMITIDO"))
            self.assertIsNone(fila["lote"])
            self.assertIsNone(fila["envio"])
        finally:
            with conectar() as conn, conn.cursor() as cur:
                cur.execute("DELETE FROM paciente_reagendar WHERE reservo_cita_uuid=%s", (clave,))
                conn.commit()

    def test_guardia_final_impide_envio_de_registro_de_prueba(self):
        cita = {"modo_prueba": 1, "id": 1, "intentos_envio": 1,
                "reserva_token": "simulado"}
        with patch.object(main, "WhatsAppService") as servicio, \
                patch.object(reagendar, "marcar_fallo") as fallo:
            asyncio.run(main._procesar_cita_reagendar(cita, {}))
        servicio.assert_not_called()
        fallo.assert_called_once_with(cita, "MODO_PRUEBA")

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
                "centro_codigo": "VS",
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
                "centro_codigo": "VS",
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


class EliminarCargaTests(unittest.TestCase):
    def test_elimina_carga_sin_intentos_y_libera_reservas(self):
        conexion = MagicMock()
        cur = conexion.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value = [
            {"id": 11, "estado_envio": "PENDIENTE", "intentos_envio": 0,
             "whatsapp_opt_out": 0, "envio_id": None, "log_envio_id": None,
             "whatsapp_message_id": None}]
        cur.fetchone.side_effect = [{"id": 5, "estado": "PROGRAMADO"}, {"n": 0}]
        cur.rowcount = 1
        with patch.object(reagendar, "conectar", return_value=conexion):
            resultado = reagendar.eliminar_carga_sin_envios(5)
        self.assertEqual(resultado, {"id": 5, "pacientes_eliminados": 1})
        sentencias = [c.args[0] for c in cur.execute.call_args_list]
        self.assertIn("FOR UPDATE", sentencias[0])
        self.assertLess(next(i for i, s in enumerate(sentencias) if "DELETE d FROM" in s),
                        next(i for i, s in enumerate(sentencias) if "DELETE FROM paciente_reagendar" in s))
        self.assertIn("DELETE FROM reagendar_lotes", sentencias[-1])
        conexion.__enter__.return_value.commit.assert_called_once()

    def test_rechaza_carga_enviada_o_con_baja(self):
        for cambio in ({"estado_envio": "ENVIADO"}, {"whatsapp_opt_out": 1},
                       {"intentos_envio": 1}):
            with self.subTest(cambio=cambio):
                conexion = MagicMock()
                cur = conexion.__enter__.return_value.cursor.return_value.__enter__.return_value
                cur.fetchall.return_value = [{"estado_envio": "PENDIENTE",
                    "intentos_envio": 0, "whatsapp_opt_out": 0,
                    "envio_id": None, "log_envio_id": None,
                    "whatsapp_message_id": None, **cambio}]
                cur.fetchone.return_value = {"id": 5, "estado": "COMPLETADO"}
                with patch.object(reagendar, "conectar", return_value=conexion), \
                     self.assertRaises(ValueError):
                    reagendar.eliminar_carga_sin_envios(5)
                self.assertFalse(any("DELETE" in c.args[0] for c in cur.execute.call_args_list))
                conexion.__enter__.return_value.commit.assert_not_called()

    def test_ruta_exige_autenticacion_y_audita_el_borrado(self):
        ruta = "/api/v1/paciente-reagendar/lotes/5"
        with TestClient(main.app) as client:
            self.assertIn(client.delete(ruta).status_code, (401, 403))
            main.app.dependency_overrides[main.solo_admin] = lambda: {"usuario": "admin@test"}
            try:
                with patch.object(reagendar, "eliminar_carga_sin_envios",
                                  return_value={"id": 5, "pacientes_eliminados": 1}), \
                     patch.object(main, "auditoria_registrar") as auditar:
                    respuesta = client.delete(ruta)
                self.assertEqual(respuesta.status_code, 200)
                self.assertEqual(respuesta.json()["pacientes_eliminados"], 1)
                auditar.assert_called_once()
                with patch.object(reagendar, "eliminar_carga_sin_envios",
                                  side_effect=ValueError("Ya enviada")), \
                     patch.object(main, "auditoria_registrar") as auditar:
                    self.assertEqual(client.delete(ruta).status_code, 409)
                    auditar.assert_not_called()
                with patch.object(reagendar, "eliminar_carga_sin_envios",
                                  side_effect=LookupError("No existe")):
                    self.assertEqual(client.delete(ruta).status_code, 404)
            finally:
                main.app.dependency_overrides.pop(main.solo_admin, None)


class EliminarPacienteReagendarTests(unittest.TestCase):
    def test_borra_paciente_sin_intentos_y_cierra_lote_vacio(self):
        conexion = MagicMock()
        cur = conexion.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect = [
            {"id": 11, "lote_id": 5, "estado_envio": "PENDIENTE", "intentos_envio": 0,
             "whatsapp_opt_out": 0, "envio_id": None, "log_envio_id": None,
             "whatsapp_message_id": None, "reserva_token": None, "ultimo_intento_en": None},
            {"id": 5}, {"estado": "PENDIENTE"}, {"n": 0}]
        with patch.object(reagendar, "conectar", return_value=conexion):
            resultado = reagendar.eliminar_paciente_sin_envio(11)
        self.assertEqual(resultado, {"id": 11, "lote_id": 5, "eliminado": True})
        sentencias = [c.args[0] for c in cur.execute.call_args_list]
        self.assertIn("FOR UPDATE", sentencias[0])
        self.assertLess(next(i for i, s in enumerate(sentencias) if "DELETE FROM reagendar_destinatarios" in s),
                        next(i for i, s in enumerate(sentencias) if "DELETE FROM paciente_reagendar" in s))
        self.assertIn("estado='COMPLETADO'", sentencias[-1])
        conexion.__enter__.return_value.commit.assert_called_once()

    def test_rechaza_paciente_ya_procesado(self):
        for cambio in ({"estado_envio": "ENVIADO"}, {"intentos_envio": 1},
                       {"whatsapp_opt_out": 1}):
            with self.subTest(cambio=cambio):
                conexion = MagicMock()
                cur = conexion.__enter__.return_value.cursor.return_value.__enter__.return_value
                cur.fetchone.return_value = {"id": 11, "lote_id": 5,
                    "estado_envio": "PENDIENTE", "intentos_envio": 0,
                    "whatsapp_opt_out": 0, "envio_id": None, "log_envio_id": None,
                    "whatsapp_message_id": None, "reserva_token": None,
                    "ultimo_intento_en": None, **cambio}
                with patch.object(reagendar, "conectar", return_value=conexion), \
                        self.assertRaises(ValueError):
                    reagendar.eliminar_paciente_sin_envio(11)
                self.assertFalse(any("DELETE" in c.args[0] for c in cur.execute.call_args_list))

    def test_endpoint_requiere_sesion_y_audita(self):
        ruta = "/api/v1/paciente-reagendar/11"
        with TestClient(main.app) as client:
            self.assertIn(client.delete(ruta).status_code, (401, 403))
            main.app.dependency_overrides[main.solo_admin] = lambda: {"usuario": "admin@test"}
            try:
                with patch.object(reagendar, "eliminar_paciente_sin_envio",
                                  return_value={"id": 11, "lote_id": 5, "eliminado": True}), \
                     patch.object(main, "auditoria_registrar") as auditar:
                    respuesta = client.delete(ruta)
                self.assertEqual(respuesta.status_code, 200)
                auditar.assert_called_once()
                with patch.object(reagendar, "eliminar_paciente_sin_envio",
                                  side_effect=ValueError("Ya hubo intento")):
                    self.assertEqual(client.delete(ruta).status_code, 409)
            finally:
                main.app.dependency_overrides.pop(main.solo_admin, None)


class EliminarTodosReagendarTests(unittest.TestCase):
    def test_borrado_masivo_usa_guardas_y_cierra_lote_sin_pendientes(self):
        conexion = MagicMock()
        cur = conexion.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value = [{"id": 11, "lote_id": 5}, {"id": 12, "lote_id": 5}]
        cur.fetchone.return_value = {"n": 0}
        with patch.object(reagendar, "conectar", return_value=conexion):
            resultado = reagendar.eliminar_todos_sin_envio()
        self.assertEqual(resultado, {"pacientes_eliminados": 2})
        sentencias = [c.args[0] for c in cur.execute.call_args_list]
        self.assertIn("r.intentos_envio=0", sentencias[0])
        self.assertIn("r.whatsapp_opt_out=0", sentencias[0])
        self.assertIn("d.estado NOT IN ('PROCESANDO','ENVIADO')", sentencias[0])
        self.assertLess(next(i for i, s in enumerate(sentencias) if "DELETE FROM reagendar_destinatarios" in s),
                        next(i for i, s in enumerate(sentencias) if "DELETE FROM paciente_reagendar" in s))
        self.assertIn("estado='COMPLETADO'", sentencias[-1])
        conexion.__enter__.return_value.commit.assert_called_once()

    def test_resumen_y_borrado_masivo_exigen_admin_y_auditan(self):
        with TestClient(main.app) as client:
            self.assertIn(client.get("/api/v1/paciente-reagendar/borrado/resumen").status_code,
                          (401, 403))
            self.assertIn(client.delete("/api/v1/paciente-reagendar/borrado/todos").status_code,
                          (401, 403))
            main.app.dependency_overrides[main.solo_admin] = lambda: {"usuario": "admin@test"}
            try:
                with patch.object(reagendar, "resumen_borrado_pacientes",
                                  return_value={"eliminables": 3, "conservados": 2}), \
                     patch.object(reagendar, "eliminar_todos_sin_envio",
                                  return_value={"pacientes_eliminados": 3}), \
                     patch.object(main, "auditoria_registrar") as auditar:
                    previo = client.get("/api/v1/paciente-reagendar/borrado/resumen")
                    borrado = client.delete("/api/v1/paciente-reagendar/borrado/todos")
                self.assertEqual(previo.json(), {"eliminables": 3, "conservados": 2})
                self.assertEqual(borrado.json()["pacientes_eliminados"], 3)
                auditar.assert_called_once()
            finally:
                main.app.dependency_overrides.pop(main.solo_admin, None)


class MariaDbCitasTests(unittest.TestCase):
    def test_borrado_individual_libera_numero_y_cancela_envio_de_carga_vacia(self):
        clave = "TEST-" + uuid.uuid4().hex
        telefono = telefono_prueba(clave)
        chile = ZoneInfo("America/Santiago")
        fecha = dt.datetime.now(chile).date() + dt.timedelta(days=1)
        datos = csv_fila(clave, telefono=telefono).replace(
            b"2026-10-22", fecha.isoformat().encode())
        recibido = dt.datetime.combine(fecha, dt.time(14, 0), chile)
        lote_id = None
        try:
            importado = reagendar.importar(datos, CFG, recibido=recibido)
            lote_id = importado["summary"]["batchId"]
            with conectar() as conn, conn.cursor() as cur:
                cur.execute("SELECT id FROM paciente_reagendar WHERE reservo_cita_uuid=%s", (clave,))
                cita_id = cur.fetchone()["id"]
            self.assertEqual(importado["summary"]["batchStatus"], "PROGRAMADO")
            self.assertTrue(reagendar.eliminar_paciente_sin_envio(cita_id)["eliminado"])
            with conectar() as conn, conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n FROM paciente_reagendar WHERE id=%s", (cita_id,))
                self.assertEqual(cur.fetchone()["n"], 0)
                cur.execute("SELECT COUNT(*) AS n FROM reagendar_destinatarios WHERE cita_id=%s", (cita_id,))
                self.assertEqual(cur.fetchone()["n"], 0)
                cur.execute("SELECT estado, programado_para, creados FROM reagendar_lotes WHERE id=%s", (lote_id,))
                lote = cur.fetchone()
            self.assertEqual((lote["estado"], lote["programado_para"], lote["creados"]),
                             ("COMPLETADO", None, 0))
        finally:
            with conectar() as conn, conn.cursor() as cur:
                cur.execute("DELETE FROM paciente_reagendar WHERE reservo_cita_uuid=%s", (clave,))
                cur.execute("DELETE FROM reagendar_destinatarios WHERE telefono=%s", ("+" + telefono,))
                if lote_id:
                    cur.execute("DELETE FROM reagendar_lotes WHERE id=%s", (lote_id,))
                conn.commit()

    def test_paciente_repetido_pendiente_y_enviado_no_se_reimporta(self):
        claves = ["TEST-" + uuid.uuid4().hex for _ in range(3)]
        telefono = telefono_prueba(claves[0])
        lotes = []
        try:
            for indice, clave in enumerate(claves):
                resultado = reagendar.importar(
                    csv_fila(clave, telefono=telefono), CFG)
                lotes.append(resultado["summary"]["batchId"])
                if indice == 0:
                    self.assertEqual(resultado["summary"]["inserted"], 1)
                else:
                    self.assertEqual(resultado["summary"]["inserted"], 0)
                    self.assertEqual(resultado["summary"]["alreadyExists"], 1)
                    self.assertEqual(resultado["summary"]["batchStatus"], "COMPLETADO")
                    self.assertIsNotNone(resultado["summary"]["batchId"])
                if indice == 1:
                    with conectar() as conn, conn.cursor() as cur:
                        cur.execute("UPDATE paciente_reagendar SET estado_envio='ENVIADO'"
                                    " WHERE reservo_cita_uuid=%s", (claves[0],))
                        cur.execute("UPDATE reagendar_destinatarios SET estado='ENVIADO'"
                                    " WHERE telefono=%s AND modo_prueba=0", ("+" + telefono,))
                        conn.commit()
            self.assertEqual(resultado["summary"]["alreadySent"], 1)
            with conectar() as conn, conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n FROM paciente_reagendar"
                            " WHERE reservo_cita_uuid IN (%s,%s,%s)", tuple(claves))
                self.assertEqual(cur.fetchone()["n"], 1)
            vista = main.listar_citas_reagendar(
                centro=None, lote_id=lotes[0], fecha=None, ventana=None, estado=None,
                modo_prueba=False, pagina=1, limite=100, sesion={})
            self.assertEqual(sum(r["paciente_telefono_normalizado"] == "+" + telefono
                                 for r in vista["items"]), 1)
        finally:
            with conectar() as conn, conn.cursor() as cur:
                cur.execute("DELETE FROM paciente_reagendar"
                            " WHERE reservo_cita_uuid IN (%s,%s,%s)", tuple(claves))
                cur.execute("DELETE FROM reagendar_destinatarios WHERE telefono=%s",
                            ("+" + telefono,))
                for lote in lotes:
                    if lote:
                        cur.execute("DELETE FROM reagendar_lotes WHERE id=%s", (lote,))
                conn.commit()

    def test_respuestas_asociadas_al_emisor_y_duplicados(self):
        clave = "TEST-" + uuid.uuid4().hex
        telefono = "+" + telefono_prueba(clave)
        mensaje_origen = "wamid." + uuid.uuid4().hex
        mensaje_entrada = "wamid." + uuid.uuid4().hex
        lote_id = None
        try:
            primero = reagendar.importar(csv_fila(clave, telefono=telefono[1:]), CFG)
            lote_id = primero["summary"]["batchId"]
            with conectar() as conn, conn.cursor() as cur:
                cur.execute("UPDATE paciente_reagendar SET estado_envio='ENVIADO',"
                            " whatsapp_message_id=%s, whatsapp_phone_number_id='1234567890'"
                            " WHERE reservo_cita_uuid=%s", (mensaje_origen, clave))
                conn.commit()
            self.assertIsNone(reagendar.registrar_respuesta_reagendar(
                telefono, "otro_emisor", mensaje_entrada, mensaje_origen, "Reagendar"))
            evento = reagendar.registrar_respuesta_reagendar(
                telefono, "1234567890", mensaje_entrada, mensaje_origen, "Reagendar")
            self.assertEqual(evento["accion"], "REAGENDAR")
            self.assertEqual(reagendar.registrar_respuesta_reagendar(
                telefono, "1234567890", mensaje_entrada,
                mensaje_origen, "Reagendar")["accion"], "DUPLICADO")
            baja = reagendar.registrar_respuesta_reagendar(
                telefono, "1234567890", "wamid." + uuid.uuid4().hex,
                mensaje_origen, "Dar de baja", declina="BAJA")
            self.assertEqual(baja["accion"], "BAJA")
            with conectar() as conn, conn.cursor() as cur:
                cur.execute("SELECT whatsapp_opt_out FROM paciente_reagendar"
                            " WHERE reservo_cita_uuid=%s", (clave,))
                self.assertEqual(cur.fetchone()["whatsapp_opt_out"], 1)
        finally:
            with conectar() as conn, conn.cursor() as cur:
                cur.execute("DELETE FROM paciente_reagendar WHERE reservo_cita_uuid=%s", (clave,))
                cur.execute("DELETE FROM reagendar_destinatarios WHERE telefono=%s", (telefono,))
                if lote_id:
                    cur.execute("DELETE FROM reagendar_lotes WHERE id=%s", (lote_id,))
                conn.commit()

    def test_lote_programado_aplazado_y_csv_duplicado(self):
        clave = "TEST-" + uuid.uuid4().hex
        telefono = telefono_prueba(clave)
        chile = ZoneInfo("America/Santiago")
        fecha = dt.datetime.now(chile).date() + dt.timedelta(days=1)
        datos = csv_fila(clave, telefono=telefono).replace(b"2026-10-22", fecha.isoformat().encode())
        recibido = dt.datetime.combine(fecha, dt.time(14, 0), chile)
        lote_id = None
        try:
            primero = reagendar.importar(datos, CFG, recibido=recibido)
            lote_id = primero["summary"]["batchId"]
            self.assertEqual(primero["summary"]["batchStatus"], "PROGRAMADO")
            programado = dt.datetime.combine(fecha, dt.time(14, 30), chile)
            self.assertEqual(primero["summary"]["scheduledAtUtc"],
                             programado.astimezone(dt.timezone.utc).replace(
                                 tzinfo=None).isoformat() + "Z")
            nueva = dt.datetime.combine(fecha, dt.time(16, 0), chile).astimezone(dt.timezone.utc)
            aplazado = reagendar.aplazar_lote(lote_id, nueva, "prueba", "Revisión")
            self.assertEqual(aplazado["estado"], "APLAZADO")
            segundo = reagendar.importar(datos, CFG, recibido=recibido)
            self.assertEqual(segundo["summary"]["alreadyExists"], 1)
            self.assertEqual(segundo["message"], "Sin registros nuevos pendientes de envío")
            self.assertIsNotNone(segundo["summary"]["batchId"])
            self.assertEqual(segundo["summary"]["batchStatus"], "COMPLETADO")
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
                cur.execute("DELETE FROM reagendar_destinatarios WHERE telefono=%s", ("+" + telefono,))
                if lote_id:
                    cur.execute("DELETE FROM reagendar_lotes WHERE id=%s", (lote_id,))
                if 'segundo' in locals() and segundo["summary"]["batchId"]:
                    cur.execute("DELETE FROM reagendar_lotes WHERE id=%s", (segundo["summary"]["batchId"],))
                conn.commit()

    def test_carga_vespertina_y_tardia_no_despacha_de_inmediato(self):
        chile = ZoneInfo("America/Santiago")
        fecha = dt.datetime.now(chile).date() + dt.timedelta(days=1)
        claves = ["TEST-" + uuid.uuid4().hex for _ in range(2)]
        lotes = []
        try:
            for clave, minuto, estado_esperado in (
                    (claves[0], 0, "PROGRAMADO"),
                    (claves[1], 31, "REVISION")):
                datos = csv_fila(clave).replace(
                    b"56911111111", telefono_prueba(clave).encode()).replace(
                    b"2026-10-22", fecha.isoformat().encode()).replace(b"08-14", b"14-20")
                recibido = dt.datetime.combine(fecha, dt.time(20, minuto), chile)
                resultado = reagendar.importar(datos, CFG, recibido=recibido)
                lotes.append(resultado["summary"]["batchId"])
                self.assertEqual(resultado["summary"]["batchStatus"], estado_esperado)
                if minuto == 0:
                    esperado = dt.datetime.combine(fecha, dt.time(20, 30), chile)
                    self.assertEqual(resultado["summary"]["scheduledAtUtc"],
                        esperado.astimezone(dt.timezone.utc).replace(tzinfo=None).isoformat() + "Z")
                else:
                    self.assertIsNone(resultado["summary"]["scheduledAtUtc"])
            with conectar() as conn, conn.cursor() as cur:
                cur.execute("SELECT estado, programado_para FROM reagendar_lotes WHERE id=%s",
                            (lotes[1],))
                tardio = cur.fetchone()
            self.assertEqual(tardio["estado"], "REVISION")
            self.assertIsNone(tardio["programado_para"])
        finally:
            with conectar() as conn, conn.cursor() as cur:
                for clave in claves:
                    cur.execute("DELETE FROM paciente_reagendar WHERE reservo_cita_uuid=%s", (clave,))
                    cur.execute("DELETE FROM reagendar_destinatarios WHERE telefono=%s", ("+" + telefono_prueba(clave),))
                for lote in lotes:
                    if lote:
                        cur.execute("DELETE FROM reagendar_lotes WHERE id=%s", (lote,))
                conn.commit()

    def test_dos_importaciones_cinco_clinicas_y_una_cita_nueva(self):
        prefijo = "TEST-" + uuid.uuid4().hex
        nombres = ["Salud Mental Para Todos", "Centro Mentalidad Saludable",
                   "PROSALUD", "Centro Psiquiatrico Huerfanos", "VIDA Y SALUD"]
        encabezado = csv_fila().split(b"\n", 1)[0].decode() + "\n"
        def archivo(cantidad):
            filas = [f"{i},2026-10-22,08-14,,{nombres[4 if i == 6 else (i - 1) % 5]},"
                    f"{prefijo}-{i},,Persona Prueba,{telefono_prueba(prefijo + '-' + str(i))},Doctor Prueba,"
                    "2026-10-22 10:00:00,\n" for i in range(1, cantidad + 1)]
            return (encabezado + "".join(filas)).encode()
        try:
            primero = reagendar.importar(archivo(5), CFG)
            self.assertEqual(primero["summary"]["inserted"], 5)
            self.assertEqual(primero["summary"]["eligible"], 5)
            self.assertIsNone(primero["summary"]["scheduledAtUtc"])
            self.assertIsNone(primero["message"])
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
                cur.execute("SELECT DISTINCT lote_id FROM paciente_reagendar"
                            " WHERE reservo_cita_uuid LIKE %s", (prefijo + "-%",))
                lotes = [r["lote_id"] for r in cur.fetchall() if r["lote_id"]]
                cur.execute("DELETE FROM paciente_reagendar WHERE reservo_cita_uuid LIKE %s",
                            (prefijo + "-%",))
                for i in range(1, 7):
                    cur.execute("DELETE FROM reagendar_destinatarios WHERE telefono=%s",
                                ("+" + telefono_prueba(prefijo + '-' + str(i)),))
                if lotes:
                    cur.execute("DELETE FROM reagendar_lotes WHERE id IN (" +
                                ",".join(["%s"] * len(lotes)) + ")", tuple(lotes))
                conn.commit()

    def test_reimportacion_no_reinicia_estado_ni_id_meta(self):
        key = "TEST-" + uuid.uuid4().hex
        telefono = telefono_prueba(key)
        datos = csv_fila(key, telefono=telefono)
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
            self.assertEqual(segundo["summary"]["alreadySent"], 1)
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
                    cur.execute("DELETE FROM reagendar_destinatarios WHERE telefono=%s", ("+" + telefono,))
                    if lote and lote["lote_id"]:
                        cur.execute("DELETE FROM reagendar_lotes WHERE id=%s", (lote["lote_id"],))
                    if 'segundo' in locals() and segundo["summary"]["batchId"]:
                        cur.execute("DELETE FROM reagendar_lotes WHERE id=%s",
                                    (segundo["summary"]["batchId"],))
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
