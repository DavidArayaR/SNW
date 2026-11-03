import asyncio
import csv
import datetime as dt
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import main


class ExportarConsultaReservoTests(unittest.TestCase):
    def test_consulta_no_disponible_no_genera_csv(self):
        conexion = MagicMock()
        conexion.__enter__.return_value = conexion
        with patch.object(main, "conectar", return_value=conexion), \
             patch.object(main.reservo_pacientes, "_obtener_job_base", side_effect=ValueError("Consulta no terminada")):
            with self.assertRaises(main.HTTPException) as error:
                main.descargar_sincronizacion_reservo_csv(5, sesion={"usuario": "admin"})
        self.assertEqual(error.exception.status_code, 409)

    def test_csv_compatible_filtra_telefonos_y_conserva_unicode(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = {"existe": 1}
        cursor.fetchmany.side_effect = [[
            {"paciente_nombre": "Muñoz", "paciente_apellido": "Pérez",
             "paciente_telefono": "+56912345678", "tratamiento": "Atención psicológica",
             "fecha_ultima_atencion": dt.datetime(2026, 10, 1, 1, 30)},
            {"paciente_nombre": "Inválido", "paciente_telefono": "+34123456789"},
        ], []]
        conexion = MagicMock()
        conexion.__enter__.return_value = conexion
        conexion.cursor.return_value.__enter__.return_value = cursor

        async def leer(respuesta):
            partes = []
            async for parte in respuesta.body_iterator:
                partes.append(parte if isinstance(parte, bytes) else parte.encode("utf-8"))
            return b"".join(partes).decode("utf-8-sig")

        with patch.object(main, "conectar", return_value=conexion), \
             patch.object(main.reservo_pacientes, "_obtener_job_base", return_value={}), \
             patch.object(main.reservo_pacientes, "_detalle_tratamientos", return_value=([{"uuid": "t1"}], "")), \
             patch.object(main, "auditoria_registrar") as auditoria:
            respuesta = main.descargar_sincronizacion_reservo_csv(5, sesion={"usuario": "admin"})
            filas = list(csv.DictReader(io.StringIO(asyncio.run(leer(respuesta)))))

        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0]["nombre"], "Muñoz")
        self.assertEqual(filas[0]["apellido"], "Pérez")
        self.assertEqual(filas[0]["telefono"], "56912345678")
        self.assertEqual(filas[0]["tratamiento_ultima_atencion"], "Atención psicológica")
        self.assertEqual(filas[0]["fecha_ultima_atencion"], "2026-09-30")
        auditoria.assert_called_once_with("admin", "reservo_consulta_csv_descargado", "5")


if __name__ == "__main__":
    unittest.main()
