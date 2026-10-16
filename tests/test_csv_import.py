import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import servicio_areas
import main


class CsvImportTests(unittest.TestCase):
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
        self.assertEqual(insercion.args[1], ("David", "Araya", "+56993921740"))
        self.assertEqual(aplicar.call_args.args[3:5], ("respondio", None))


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
