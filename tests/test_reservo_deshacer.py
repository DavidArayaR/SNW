"""Protección de pacientes compartidos al deshacer consultas de Reservo."""

import datetime as dt
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import db
import reservo_pacientes as reservo


class CursorFalso:
    def __init__(self, estado="completado", referencias=1, eliminados=2,
                 solapadas=0, limpiados=0):
        fecha = dt.datetime(2026, 11, 2, 10, 0)
        self.job = {"id": 7, "clinica_codigo": "SMPT", "estado": estado,
                    "iniciado_en": fecha, "terminado_en": fecha + dt.timedelta(minutes=2)}
        self.referencias = referencias
        self.eliminados = eliminados
        self.limpiados = limpiados
        self.solapadas = solapadas
        self.consultas = []
        self.resultado = None
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, parametros=()):
        self.consultas.append((sql, parametros))
        self.rowcount = 0
        if sql.startswith("SELECT id,clinica_codigo,estado"):
            self.resultado = self.job
        elif "AND estado IN ('pendiente','ejecutando')" in sql:
            self.resultado = None
        elif sql.startswith("SELECT COUNT(*) AS total FROM reservo_sincronizacion_pacientes"):
            self.resultado = {"total": self.referencias}
        elif sql.startswith("SELECT COUNT(*) AS total FROM reservo_sincronizaciones"):
            self.resultado = {"total": self.solapadas}
        elif sql.startswith("DELETE p FROM reservo_pacientes p WHERE"):
            self.rowcount = self.limpiados
        elif sql.startswith("DELETE p FROM reservo_pacientes"):
            self.rowcount = self.eliminados

    def fetchone(self):
        return self.resultado


class ConexionFalsa:
    def __init__(self, cursor):
        self.cur = cursor
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self.cur

    def commit(self):
        self.commits += 1


class TestDeshacerReservo(unittest.TestCase):
    def test_nueva_consulta_borra_solo_pacientes_creados_sin_otra_referencia(self):
        cursor = CursorFalso(referencias=3, eliminados=2)
        conexion = ConexionFalsa(cursor)
        with patch.object(db, "conectar", return_value=conexion):
            resultado = reservo._deshacer_sincronizacion_bloqueada(7)
        self.assertEqual(resultado["pacientes_eliminados"], 2)
        borrado = next(sql for sql, _ in cursor.consultas if sql.startswith("DELETE p"))
        self.assertIn("actual.nuevo=1", borrado)
        self.assertIn("otra.paciente_id IS NULL", borrado)
        self.assertEqual(conexion.commits, 1)

    def test_consulta_antigua_solo_borra_creados_en_su_ventana_sin_resincronizar(self):
        cursor = CursorFalso(referencias=0, eliminados=1)
        with patch.object(db, "conectar", return_value=ConexionFalsa(cursor)):
            resultado = reservo._deshacer_sincronizacion_bloqueada(7)
        self.assertEqual(resultado["pacientes_eliminados"], 1)
        borrado = next(sql for sql, _ in cursor.consultas if sql.startswith("DELETE p"))
        self.assertIn("p.primera_sincronizacion_en BETWEEN", borrado)
        self.assertIn("p.ultima_sincronizacion_en BETWEEN", borrado)
        self.assertIn("otra.paciente_id IS NULL", borrado)

    def test_consulta_ya_deshecha_reconcilia_compartidos_de_otras_cargas(self):
        cursor = CursorFalso(estado="deshecho", limpiados=55)
        conexion = ConexionFalsa(cursor)
        with patch.object(db, "conectar", return_value=conexion):
            resultado = reservo._deshacer_sincronizacion_bloqueada(7)
        self.assertEqual(resultado["pacientes_eliminados"], 55)
        borrado = next(sql for sql, _ in cursor.consultas if sql.startswith("DELETE p"))
        self.assertIn("vigente.estado<>'deshecho'", borrado)
        self.assertIn("origen.estado='deshecho'", borrado)
        self.assertEqual(conexion.commits, 1)

    def test_reconciliar_dos_veces_es_idempotente(self):
        cursor = CursorFalso(estado="deshecho", limpiados=0)
        with patch.object(db, "conectar", return_value=ConexionFalsa(cursor)):
            resultado = reservo._deshacer_sincronizacion_bloqueada(7)
        self.assertEqual(resultado["pacientes_eliminados"], 0)

    def test_consulta_antigua_ambigua_no_borra_pacientes(self):
        cursor = CursorFalso(referencias=0, solapadas=1)
        conexion = ConexionFalsa(cursor)
        with patch.object(db, "conectar", return_value=conexion):
            with self.assertRaisesRegex(ValueError, "coincide en horario"):
                reservo._deshacer_sincronizacion_bloqueada(7)
        self.assertFalse(any(sql.startswith("DELETE") for sql, _ in cursor.consultas))
        self.assertEqual(conexion.commits, 0)


if __name__ == "__main__":
    unittest.main()
