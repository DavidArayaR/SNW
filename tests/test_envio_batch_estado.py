"""Persistencia del estado y conteos de los lotes de campañas."""

import datetime
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pymysql

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import db
import main


def conexion_falsa():
    conn = MagicMock()
    conn.__enter__.return_value = conn
    cur = conn.cursor.return_value
    cur.__enter__.return_value = cur
    return conn, cur


class EstadoBatchTests(unittest.TestCase):
    def test_historial_error_si_fallidos_superan_la_mitad_del_total(self):
        for total, fallidos, invalidos, estado, esperado in (
                (100, 51, 0, "completado", "error"),
                (100, 50, 0, "completado", "completado"),
                (100, 49, 0, "completado", "completado"),
                (3, 2, 0, "completado", "error"),
                (100, 60, 0, "sin_confirmar", "error"),
                (100, 60, 0, "cancelado", "cancelado"),
                (100, 60, 0, "en_proceso", "en_proceso"),
                (100, 60, 0, "pausado", "pausado"),
                (100, 0, 60, "completado", "completado"),
                (0, 0, 0, "completado", "completado")):
            with self.subTest(total=total, fallidos=fallidos, estado=estado):
                fila = {"id": 7, "_orden": None, "estado": estado,
                        "total_pacientes": total, "fallidos": fallidos,
                        "invalidos": invalidos, "enviados": max(0, total - fallidos - invalidos)}
                with patch.dict(main.JOBS, {}, clear=True), \
                     patch.dict(main.PENDIENTES, {}, clear=True), \
                     patch.object(main, "catalogo_centros", return_value={}):
                    resultado = main._presentar_historial([fila], {})[0]
                self.assertEqual(resultado["estado"], esperado)

    def test_historial_no_oculta_job_activo_con_mayoria_de_errores(self):
        fila = {"id": 7, "_orden": None, "estado": "completado", "total_pacientes": 100}
        with patch.dict(main.JOBS, {"activo": {"envio_id": 7, "estado": "en_proceso", "fallidos": 60}}, clear=True), \
             patch.dict(main.PENDIENTES, {}, clear=True), \
             patch.object(main, "catalogo_centros", return_value={}), \
             patch.object(main, "_puede_controlar_job", return_value=False):
            self.assertEqual(main._presentar_historial([fila], {})[0]["estado"], "en_progreso")

    def test_lote_nuevo_no_nace_completado(self):
        conn, cur = conexion_falsa()
        cur.lastrowid = 7
        with patch.object(main, "conectar", return_value=conn), \
                patch.object(main, "columnas_tabla", return_value=set()):
            self.assertEqual(main.crear_envio_batch("pacientes_prod", "tpl", "Plantilla", 286,
                                                     "produccion"), 7)
        self.assertEqual(cur.execute.call_args.args[1][4], "en_proceso")

    def test_resultado_final_guarda_conteos_absolutos_y_estado(self):
        conn, cur = conexion_falsa()
        with patch.object(main, "conectar", return_value=conn):
            main.actualizar_envio_batch(7, "produccion", enviados=20, fallidos=2,
                                        estado="error")
        sql, args = cur.execute.call_args.args
        self.assertIn("enviados = %s", sql)
        self.assertIn("fallidos = %s", sql)
        self.assertNotIn("invalidos", sql)
        self.assertNotIn("+", sql)
        self.assertEqual(args, (20, 2, "error", 7))
        conn.commit.assert_called_once()

    def test_reintento_deadlock_no_duplica_conteos(self):
        conn, cur = conexion_falsa()
        cur.execute.side_effect = [pymysql.err.OperationalError(1213, "Deadlock"), None]
        with patch.object(main, "conectar", return_value=conn), \
                patch.object(main.time, "sleep"):
            main.actualizar_envio_batch(7, "produccion", enviados=20, estado="error")
        self.assertEqual(cur.execute.call_count, 2)
        self.assertEqual(cur.execute.call_args_list[0].args,
                         cur.execute.call_args_list[1].args)
        conn.commit.assert_called_once()

    def test_historial_usa_error_del_job_si_fallo_guardar_el_lote(self):
        fila = {"id": 7, "_orden": datetime.datetime(2026, 10, 29, 15, 29),
                "centro_codigo": "VS", "estado": "completado", "total_pacientes": 286,
                "enviados": 0, "fallidos": 0, "invalidos": 0}
        trabajo = {"envio_id": 7, "estado": "error", "enviados": 20, "fallidos": 2}
        with patch.dict(main.JOBS, {"prueba": trabajo}, clear=True), \
                patch.dict(main.PENDIENTES, {}, clear=True), \
                patch.object(main, "catalogo_centros", return_value={"VS": "Vida y Salud"}):
            resultado = main._presentar_historial([fila], {})[0]
        self.assertEqual((resultado["estado"], resultado["enviados"], resultado["fallidos"]),
                         ("error", 20, 2))

    def test_lote_antiguo_sin_resultados_no_se_muestra_completado(self):
        fila = {"id": 7, "_orden": datetime.datetime(2026, 10, 29, 15, 29),
                "centro_codigo": "VS", "estado": "completado", "total_pacientes": 286,
                "enviados": 0, "fallidos": 0, "invalidos": 0}
        with patch.dict(main.JOBS, {}, clear=True), patch.dict(main.PENDIENTES, {}, clear=True), \
                patch.object(main, "catalogo_centros", return_value={}):
            resultado = main._presentar_historial([fila], {})[0]
        self.assertEqual(resultado["estado"], "sin_confirmar")

    def test_lote_antiguo_con_solo_invalidos_no_se_muestra_completado(self):
        fila = {"id": 7, "_orden": datetime.datetime(2026, 10, 29, 15, 29),
                "centro_codigo": "VS", "estado": "completado", "total_pacientes": 286,
                "enviados": 0, "fallidos": 0, "invalidos": 2}
        with patch.dict(main.JOBS, {}, clear=True), patch.dict(main.PENDIENTES, {}, clear=True), \
                patch.object(main, "catalogo_centros", return_value={}):
            resultado = main._presentar_historial([fila], {})[0]
        self.assertEqual(resultado["estado"], "sin_confirmar")

    def test_reconciliacion_recupera_parcial_y_marca_error(self):
        cur = MagicMock()
        cur.fetchall.return_value = [{"id": 7, "total_pacientes": 286}]
        cur.fetchone.return_value = {"enviados": 20, "fallidos": 2, "invalidos": 1}
        db._reconciliar_lotes_sin_conteos(cur)
        self.assertEqual(cur.execute.call_args.args[1], (20, 2, 1, "error", 7))

    def test_reconciliacion_no_confunde_invalidos_previos_con_envio(self):
        cur = MagicMock()
        cur.fetchall.return_value = [{"id": 7, "total_pacientes": 286}]
        cur.fetchone.return_value = {"enviados": 0, "fallidos": 0, "invalidos": 1}
        db._reconciliar_lotes_sin_conteos(cur)
        self.assertEqual(cur.execute.call_count, 2)

    def test_reconciliacion_marca_error_si_todos_fallaron(self):
        cur = MagicMock()
        cur.fetchall.return_value = [{"id": 7, "total_pacientes": 2}]
        cur.fetchone.return_value = {"enviados": 0, "fallidos": 2, "invalidos": 0}
        db._reconciliar_lotes_sin_conteos(cur)
        self.assertEqual(cur.execute.call_args.args[1], (0, 2, 0, "error", 7))


if __name__ == "__main__":
    unittest.main()
