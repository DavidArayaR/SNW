import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import servicio_areas
import main
from datetime import date


class CsvImportTests(unittest.TestCase):
    def test_fecha_ultima_atencion_acepta_formatos_no_ambiguos(self):
        self.assertEqual(servicio_areas._fecha_ultima_atencion_csv("2026-10-03"), date(2026, 10, 3))
        self.assertEqual(servicio_areas._fecha_ultima_atencion_csv("03/10/2026"), date(2026, 10, 3))
        with self.assertRaises(ValueError):
            servicio_areas._fecha_ultima_atencion_csv("10/31/2026")

    def test_importa_tratamiento_y_fecha_asignados(self):
        conexion = MagicMock()
        cur = conexion.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value = []
        cur.fetchone.return_value = None
        cur.lastrowid = 7
        csv = ("Paciente,Apellido,Celular,Atención,Fecha\n"
               "Ana,Sol,93921740,Control,03/10/2026\n").encode()
        mapeo = {"nombre": "Paciente", "apellido": "Apellido", "telefono": "Celular",
                 "tratamiento_ultima_atencion": "Atención", "fecha_ultima_atencion": "Fecha"}
        with patch("servicio_areas.obtener_area", return_value={"nombre_tabla_base": "pacientes_test"}), \
                patch("servicio_areas.conectar", return_value=conexion), \
                patch("servicio_areas.indice_pacientes_por_telefono", return_value={}), \
                patch("supresion.huella_telefono", return_value="huella-test"), \
                patch("servicio_areas.aplicar_en_tabla"), \
                patch("servicio_areas.propagar_a_todas_las_bases",
                      return_value={"bases": [], "omitidos": 0}):
            informe = servicio_areas.importar_pacientes_csv(1, csv, 2, mapeo=mapeo)
        self.assertEqual(informe["insertados"], 1)
        insercion = next(c for c in cur.execute.call_args_list
                         if c.args[0].startswith("INSERT INTO pacientes_test"))
        self.assertEqual(insercion.args[1], ("Ana", "Sol", "+56993921740",
                                            "Control", date(2026, 10, 3)))

    def test_fecha_invalida_rechaza_fila_sin_insertarla(self):
        conexion = MagicMock()
        cur = conexion.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value = []
        cur.fetchone.return_value = None
        csv = b"nombre,apellido,telefono,fecha_ultima_atencion\nAna,Sol,93921740,31/02/2026\n"
        with patch("servicio_areas.obtener_area", return_value={"nombre_tabla_base": "pacientes_test"}), \
                patch("servicio_areas.conectar", return_value=conexion), \
                patch("servicio_areas.indice_pacientes_por_telefono", return_value={}), \
                patch("supresion.huella_telefono", return_value="huella-test"):
            informe = servicio_areas.importar_pacientes_csv(1, csv, 2)
        self.assertEqual(informe["rechazados"], 1)
        self.assertFalse(any(c.args[0].startswith("INSERT INTO pacientes_test")
                             for c in cur.execute.call_args_list))

    def test_actualiza_ultima_atencion_sin_duplicar_paciente(self):
        conexion = MagicMock()
        cur = conexion.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value = []
        cur.fetchone.return_value = None
        csv = b"nombre,apellido,telefono,tratamiento_ultima_atencion\nAna,Sol,93921740,Control\n"
        indice = {"+56993921740": {"pacientes_test": [7]}}
        with patch("servicio_areas.obtener_area", return_value={"nombre_tabla_base": "pacientes_test"}), \
                patch("servicio_areas.conectar", return_value=conexion), \
                patch("servicio_areas.indice_pacientes_por_telefono", return_value=indice), \
                patch("supresion.huella_telefono", return_value="huella-test"), \
                patch("servicio_areas.aplicar_en_tabla", return_value=False), \
                patch("servicio_areas._baja_activa", return_value=False):
            informe = servicio_areas.importar_pacientes_csv(1, csv, 2)
        self.assertEqual(informe["insertados"], 0)
        self.assertEqual(informe["actualizados"], 1)
        self.assertTrue(any("tratamiento_ultima_atencion=%s" in c.args[0]
                            for c in cur.execute.call_args_list))

    def test_indice_acotado_conserva_telefonos_antiguos(self):
        cur = MagicMock()
        cur.fetchall.side_effect = [
            [{"id": 1, "telefono": "+56993921740"}],
            [{"id": 2, "telefono": "+56 9 9392 1740"},
             {"id": 3, "telefono": "912345678"}],
        ]
        with patch("servicio_areas.tablas_de_pacientes", return_value=["pacientes_test"]), \
                patch("servicio_areas.tabla_valida", return_value=True):
            indice = servicio_areas.indice_pacientes_por_telefono(
                cur, telefonos_objetivo={"+56993921740"})

        self.assertEqual(indice, {"+56993921740": {"pacientes_test": [1, 2]}})
        self.assertIn("WHERE telefono IN (%s)", cur.execute.call_args_list[0].args[0])
        self.assertIn("WHERE telefono NOT LIKE %s", cur.execute.call_args_list[1].args[0])

    def test_indice_acotado_vacio_no_consulta_tablas(self):
        cur = MagicMock()
        with patch("servicio_areas.tablas_de_pacientes") as tablas:
            self.assertEqual(servicio_areas.indice_pacientes_por_telefono(
                cur, telefonos_objetivo=set()), {})
        tablas.assert_not_called()

    def test_agrupa_accesos_de_varios_pacientes(self):
        conexion = MagicMock()
        cur = conexion.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value = []
        cur.fetchone.return_value = None
        cur.lastrowid = 7
        csv = b"nombre,apellido,telefono\nAna,Sol,93921740\nBea,Luz,912345678\n"

        with patch("servicio_areas.obtener_area",
                   return_value={"nombre_tabla_base": "pacientes_test"}), \
                patch("servicio_areas.tabla_valida", return_value=True), \
                patch("servicio_areas.conectar", return_value=conexion), \
                patch("servicio_areas.indice_pacientes_por_telefono", return_value={}) as indice, \
                patch("supresion.huella_telefono", return_value="huella-test"), \
                patch("servicio_areas.aplicar_en_tabla"), \
                patch("servicio_areas.propagar_a_todas_las_bases",
                      return_value={"bases": [], "omitidos": 0}):
            informe = servicio_areas.importar_pacientes_csv(1, csv, 2)

        self.assertEqual(informe["insertados"], 2)
        self.assertEqual(indice.call_args.kwargs["telefonos_objetivo"],
                         {"+56993921740", "+56912345678"})
        self.assertEqual(cur.executemany.call_count, 1)
        self.assertEqual(len(cur.executemany.call_args.args[1]), 2)

    def test_conserva_numero_de_fila_global_en_un_bloque(self):
        conexion = MagicMock()
        cur = conexion.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value = []
        cur.fetchone.return_value = None
        csv = b"nombre,apellido,telefono\nAna,Sol,invalido\n"

        with patch("servicio_areas.obtener_area",
                   return_value={"nombre_tabla_base": "pacientes_test"}), \
                patch("servicio_areas.tabla_valida", return_value=True), \
                patch("servicio_areas.conectar", return_value=conexion), \
                patch("servicio_areas.indice_pacientes_por_telefono", return_value={}):
            informe = servicio_areas.importar_pacientes_csv(1, csv, 2,
                                                            fila_inicial=502)

        self.assertEqual(informe["rechazados"], 1)
        self.assertEqual(informe["errores"][0]["fila"], 502)

    def test_importa_csv_con_separadores_finales(self):
        conexion = MagicMock()
        cur = conexion.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value = []
        cur.fetchone.return_value = None
        cur.lastrowid = 7
        csv = (
            b"nombre,apellido,telefono;;;;;,respuesta;;;;;\n"
            b"David, Araya, 93921740;;;;;,Respondio;;;;;\n"
            b";;;;;\n"
            b";;;;;\n"
        )

        with patch("servicio_areas.obtener_area",
                   return_value={"nombre_tabla_base": "pacientes_test"}), \
                patch("servicio_areas.tabla_valida", return_value=True), \
                patch("servicio_areas.conectar", return_value=conexion), \
                patch("servicio_areas.indice_pacientes_por_telefono", return_value={}), \
                patch("supresion.huella_telefono", return_value="huella-test"), \
                patch("servicio_areas.aplicar_en_tabla") as aplicar, \
                patch("servicio_areas.propagar_a_todas_las_bases",
                      return_value={"bases": [], "omitidos": 0}):
            informe = servicio_areas.importar_pacientes_csv(1, csv, 2)

        self.assertEqual(informe["procesados"], 1)
        self.assertEqual(informe["insertados"], 1)
        self.assertEqual(informe["rechazados"], 0)
        insercion = next(c for c in cur.execute.call_args_list
                         if c.args[0].startswith("INSERT INTO pacientes_test"))
        self.assertEqual(insercion.args[1], ("David", "Araya", "+56993921740", None, None))
        self.assertEqual(aplicar.call_args.args[3:5], ("respondio", None))

    def test_asigna_columnas_de_encabezados_personalizados(self):
        conexion = MagicMock()
        cur = conexion.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value = []
        cur.fetchone.return_value = None
        cur.lastrowid = 7
        csv = b"Celular,Nombres,Apellidos,Respuesta externa,Estado externo\n93921740,Ana,Sol,Respondio,enviado\n"
        mapeo = {"nombre": "Nombres", "apellido": "Apellidos", "telefono": "Celular",
                 "respuesta": "Respuesta externa", "estado": "Estado externo"}
        with patch("servicio_areas.obtener_area",
                   return_value={"nombre_tabla_base": "pacientes_test"}), \
                patch("servicio_areas.tabla_valida", return_value=True), \
                patch("servicio_areas.conectar", return_value=conexion), \
                patch("servicio_areas.indice_pacientes_por_telefono", return_value={}), \
                patch("supresion.huella_telefono", return_value="huella-test"), \
                patch("servicio_areas.aplicar_en_tabla") as aplicar, \
                patch("servicio_areas.propagar_a_todas_las_bases",
                      return_value={"bases": [], "omitidos": 0}):
            informe = servicio_areas.importar_pacientes_csv(1, csv, 2, mapeo=mapeo)
        self.assertEqual(informe["insertados"], 1)
        insercion = next(c for c in cur.execute.call_args_list
                         if c.args[0].startswith("INSERT INTO pacientes_test"))
        self.assertEqual(insercion.args[1], ("Ana", "Sol", "+56993921740", None, None))
        self.assertEqual(aplicar.call_args.args[3:5], ("respondio", "enviado"))

    def test_rechaza_asignacion_ausente_repetida_o_inexistente(self):
        csv = b"A,B,C\nAna,Sol,93921740\n"
        with patch("servicio_areas.obtener_area",
                   return_value={"nombre_tabla_base": "pacientes_test"}), \
                patch("servicio_areas.tabla_valida", return_value=True), \
                patch("servicio_areas.conectar") as conectar:
            casos = [
                ({"nombre": "A", "apellido": "B"}, "columnas:telefono"),
                ({"nombre": "A", "apellido": "A", "telefono": "C"}, "mapeo_duplicado"),
                ({"nombre": "A", "apellido": "B", "telefono": "Otro"}, "mapeo_columna"),
                ({"nombre": "A", "apellido": "B", "telefono": "C", "id": "A"}, "mapeo_invalido"),
            ]
            for mapeo, codigo in casos:
                with self.subTest(codigo=codigo), self.assertRaisesRegex(ValueError, codigo):
                    servicio_areas.importar_pacientes_csv(1, csv, 2, mapeo=mapeo)
            conectar.assert_not_called()


class CsvUploadTests(unittest.IsolatedAsyncioTestCase):
    async def test_lectura_acotada_y_desplazamiento_de_filas(self):
        archivo = SimpleNamespace(read=AsyncMock(return_value=b"nombre,apellido,telefono\n"))
        with patch("servicio_areas.obtener_area", return_value={"nombre_visible": "Test"}), \
                patch("main._es_privilegiado", return_value=True), \
                patch("servicio_areas.usuario_id_por_correo", return_value=2), \
                patch("servicio_areas.puede_acceder_area", return_value=True), \
                patch("servicio_areas.importar_pacientes_csv",
                      return_value={"insertados": 0, "procesados": 0}) as importar, \
                patch("main.auditoria_registrar"):
            await main.importar_pacientes_csv(1, archivo, fila_inicial=502,
                                              sesion={"usuario": "test"})
        archivo.read.assert_awaited_once_with(servicio_areas.CSV_MAX_BYTES + 1)
        importar.assert_called_once_with(1, b"nombre,apellido,telefono\n", 2,
                                         fila_inicial=502)

    async def test_envia_asignacion_validada_al_importador(self):
        archivo = SimpleNamespace(read=AsyncMock(return_value=b"A,B,C\nAna,Sol,93921740\n"))
        mapeo = '{"nombre":"A","apellido":"B","telefono":"C"}'
        with patch("servicio_areas.obtener_area", return_value={"nombre_visible": "Test"}), \
                patch("main._es_privilegiado", return_value=True), \
                patch("servicio_areas.usuario_id_por_correo", return_value=2), \
                patch("servicio_areas.puede_acceder_area", return_value=True), \
                patch("servicio_areas.importar_pacientes_csv",
                      return_value={"insertados": 1, "procesados": 1}) as importar, \
                patch("main.auditoria_registrar"):
            await main.importar_pacientes_csv(1, archivo, mapeo_csv=mapeo, fila_inicial=2,
                                              sesion={"usuario": "test"})
        importar.assert_called_once_with(1, b"A,B,C\nAna,Sol,93921740\n", 2,
                                         fila_inicial=2,
                                         mapeo={"nombre": "A", "apellido": "B", "telefono": "C"})

    async def test_rechaza_peticion_mayor_a_cinco_mb(self):
        archivo = SimpleNamespace(read=AsyncMock(
            return_value=b"x" * (servicio_areas.CSV_MAX_BYTES + 1)))
        with patch("servicio_areas.obtener_area", return_value={"nombre_visible": "Test"}), \
                patch("main._es_privilegiado", return_value=True), \
                patch("servicio_areas.usuario_id_por_correo", return_value=2), \
                patch("servicio_areas.puede_acceder_area", return_value=True):
            with self.assertRaises(main.HTTPException) as error:
                await main.importar_pacientes_csv(1, archivo, fila_inicial=2,
                                                  sesion={"usuario": "test"})
        self.assertEqual(error.exception.status_code, 413)


if __name__ == "__main__":
    unittest.main()
