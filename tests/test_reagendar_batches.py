"""Agrupación persistente por carga y clínica, incluida concurrencia real en MariaDB."""
import sys
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import main
import paciente_reagendar as reagendar
import reagendar_batches as batches
from db import conectar
from tests.test_paciente_reagendar import CFG, csv_fila, telefono_prueba


class BatchesReagendarTests(unittest.TestCase):
    def setUp(self):
        self.prefijo = "TEST-BATCH-" + uuid.uuid4().hex
        datos = [csv_fila(self.prefijo + str(i), clinica=clinica,
                         telefono=telefono_prueba(self.prefijo + str(i))).decode().splitlines()
                 for i, clinica in enumerate(("VIDA Y SALUD", "VIDA Y SALUD", "Salud Mental Para Todos"))]
        csv = (datos[0][0] + "\n" + "\n".join(filas[1] for filas in datos) + "\n").encode()
        self.lote = reagendar.importar(csv, CFG)["summary"]["batchId"]
        self.envios = set()
        with conectar() as conn, conn.cursor() as cur:
            cur.execute("UPDATE paciente_reagendar SET estado_envio='PROCESANDO',"
                        " reserva_token='token-test', reservado_en=UTC_TIMESTAMP() WHERE lote_id=%s", (self.lote,))
            cur.execute("SELECT * FROM paciente_reagendar WHERE lote_id=%s ORDER BY id", (self.lote,))
            self.citas = cur.fetchall()
            conn.commit()

    def tearDown(self):
        with conectar() as conn, conn.cursor() as cur:
            cur.execute("SELECT envio_id FROM reagendar_batches WHERE lote_id=%s", (self.lote,))
            self.envios.update(f["envio_id"] for f in cur.fetchall() if f["envio_id"])
            cur.execute("SELECT envio_id FROM paciente_reagendar WHERE lote_id=%s", (self.lote,))
            self.envios.update(f["envio_id"] for f in cur.fetchall() if f["envio_id"])
            for envio in self.envios:
                cur.execute("DELETE FROM reagendar_envios_agrupados WHERE envio_id=%s OR batch_envio_id=%s", (envio, envio))
                cur.execute("DELETE FROM log_envios WHERE envio_id=%s", (envio,))
            cur.execute("DELETE d FROM reagendar_destinatarios d JOIN paciente_reagendar r ON r.id=d.cita_id"
                        " WHERE r.lote_id=%s", (self.lote,))
            cur.execute("DELETE FROM paciente_reagendar WHERE lote_id=%s", (self.lote,))
            cur.execute("DELETE FROM reagendar_batches WHERE lote_id=%s", (self.lote,))
            cur.execute("DELETE FROM reagendar_lotes WHERE id=%s", (self.lote,))
            for envio in self.envios:
                cur.execute("DELETE FROM envios WHERE id=%s", (envio,))
            conn.commit()

    def registrar(self, cita):
        codigo = cita["centro_codigo"]
        return reagendar.registrar_lote(cita, {"clave": "test_batch", "nombre": "Prueba"},
            {"codigo": codigo, "phoneNumberId": "test-phone-" + codigo, "phoneNumber": "+56900000000"})

    def test_workers_comparten_batch_sin_mezclar_clinicas(self):
        with ThreadPoolExecutor(max_workers=3) as pool:
            ids = list(pool.map(self.registrar, self.citas))
        self.envios.update(ids)
        self.assertEqual(ids[0], ids[1])
        self.assertNotEqual(ids[0], ids[2])
        with conectar() as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM reagendar_batches WHERE lote_id=%s", (self.lote,))
            self.assertEqual(cur.fetchone()["n"], 2)
            resumen = batches.resumen(cur, ids[0])
        self.assertEqual(resumen["total_pacientes"], 2)
        self.assertEqual(resumen["estado"], "en_proceso")
        for cita, envio in zip(self.citas, ids):
            cita["envio_id"] = envio
            codigo = cita["centro_codigo"]
            reagendar.marcar_enviado(cita, {"codigo": codigo, "phoneNumberId": "test-phone-" + codigo,
                                           "phoneNumber": "+56900000000"},
                                    {"clave": "test_batch"}, "Prueba", self.prefijo + str(cita["id"]))
        with conectar() as conn, conn.cursor() as cur:
            cur.execute("UPDATE reagendar_lotes SET estado='EN_CURSO' WHERE id=%s", (self.lote,))
            conn.commit()
        reagendar.completar_lote_si_corresponde(self.lote)
        with conectar() as conn, conn.cursor() as cur:
            cur.execute("SELECT total_pacientes,enviados,estado FROM envios WHERE id=%s", (ids[0],))
            final = cur.fetchone()
        self.assertEqual((final["total_pacientes"], final["enviados"], final["estado"]), (2, 2, "completado"))

    def test_historicos_conservan_mensajes_y_redireccion_de_ids(self):
        antiguos = []
        with conectar() as conn, conn.cursor() as cur:
            for cita in self.citas[:2]:
                cur.execute("INSERT INTO envios (base_datos,plantilla_clave,plantilla_nombre,total_pacientes,estado,centro_codigo)"
                            " VALUES ('paciente_reagendar','test_batch','Prueba',1,'completado','VS')")
                envio = cur.lastrowid
                antiguos.append(envio)
                self.envios.add(envio)
                cur.execute("INSERT INTO log_envios (envio_id,nombre_paciente,numero_telefono,plantilla_clave,"
                            " estado_envio,estado_whatsapp,whatsapp_message_id,wa_phone_id)"
                            " VALUES (%s,'Prueba',%s,'test_batch','enviado','delivered',%s,'test-phone-VS')",
                            (envio, cita["paciente_telefono_normalizado"], self.prefijo + str(cita["id"])))
                log_id = cur.lastrowid
                cur.execute("UPDATE paciente_reagendar SET envio_id=%s,log_envio_id=%s,estado_envio='ENVIADO' WHERE id=%s",
                            (envio, log_id, cita["id"]))
            conn.commit()
        with conectar() as conn, conn.cursor() as cur:
            canonico = batches.asegurar_grupo(cur, self.lote, "VS")
            conn.commit()
        self.assertEqual(canonico, antiguos[0])
        self.assertEqual(batches.resolver_envio(antiguos[1]), canonico)
        with conectar() as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM log_envios WHERE envio_id=%s", (canonico,))
            self.assertEqual(cur.fetchone()["n"], 2)
            cur.execute("SELECT COUNT(*) AS n FROM envios WHERE id IN (%s,%s)", tuple(antiguos))
            self.assertEqual(cur.fetchone()["n"], 2)
            resumen = batches.resumen(cur, canonico)
        self.assertEqual((resumen["total_pacientes"], resumen["enviados"], resumen["estado"]),
                         (2, 2, "completado"))
        # El historial y los conteos sobreviven a quitar registros de prueba.
        with conectar() as conn, conn.cursor() as cur:
            cur.execute("DELETE d FROM reagendar_destinatarios d JOIN paciente_reagendar r ON r.id=d.cita_id"
                        " WHERE r.lote_id=%s AND r.centro_codigo='VS'", (self.lote,))
            cur.execute("DELETE FROM paciente_reagendar WHERE lote_id=%s AND centro_codigo='VS'", (self.lote,))
            conn.commit()
            conservado = batches.resumen(cur, canonico)
        self.assertEqual((conservado["total_pacientes"], conservado["enviados"]), (2, 2))


if __name__ == "__main__":
    unittest.main()
