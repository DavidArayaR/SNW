import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import db
import main


class PermisosEnviosTests(unittest.TestCase):
    def test_migracion_conserva_carga_y_envios_pero_no_otorga_pii(self):
        permisos = set(db._migrar_permisos_envios_legacy(
            "mensajeria,historial,envio_produccion", "usuario").split(","))
        self.assertTrue({"csv_subir", "envio_manual", "envio_programado",
                         "envio_manual_directo", "envio_programado_directo"} <= permisos)
        self.assertNotIn("envio_produccion", permisos)
        self.assertNotIn("pacientes_ver_datos", permisos)
        sin_directo = set(db._migrar_permisos_envios_legacy("mensajeria", "usuario").split(","))
        self.assertNotIn("envio_manual_directo", sin_directo)
        self.assertNotIn("envio_programado_directo", sin_directo)
        supervisor = set(db._migrar_permisos_envios_legacy("mensajeria", "supervisor").split(","))
        self.assertIn("envio_programado_directo", supervisor)
        self.assertNotIn("envio_manual_directo", supervisor)

    def test_cada_tipo_de_envio_tiene_permiso_y_confirmacion_independiente(self):
        sesion = {"rol": "usuario", "permisos": ["mensajeria", "envio_manual",
                                               "envio_manual_directo", "envio_programado"]}
        self.assertFalse(main._envio_requiere_confirmacion(sesion, "manual", "produccion"))
        self.assertTrue(main._envio_requiere_confirmacion(sesion, "programado", "produccion"))
        self.assertFalse(main._envio_requiere_confirmacion(sesion, "programado", "desarrollo"))
        self.assertFalse(main._envio_requiere_confirmacion({"rol": "administrador"},
                                                           "programado", "produccion"))

    def test_creacion_manual_y_programada_rechazadas_sin_permiso(self):
        sesion = {"rol": "usuario", "permisos": ["mensajeria"]}
        with self.assertRaises(main.HTTPException) as manual:
            main.iniciar_envio(None, None, sesion)
        with self.assertRaises(main.HTTPException) as programado:
            main.crear_programado(None, sesion)
        self.assertEqual(manual.exception.status_code, 403)
        self.assertEqual(programado.exception.status_code, 403)

    def test_csv_rechazado_sin_permiso_antes_de_leer_archivo(self):
        sesion = {"rol": "usuario", "permisos": ["mensajeria"], "area_ids": [9]}
        with patch.object(main.servicio_areas, "obtener_area", return_value={"id": 9}):
            with self.assertRaises(main.HTTPException) as error:
                asyncio.run(main.importar_pacientes_csv(9, None, sesion=sesion))
        self.assertEqual(error.exception.status_code, 403)

    def test_mensajes_rechazados_sin_permiso_de_datos(self):
        with self.assertRaises(main.HTTPException) as error:
            main.mensajes_paciente(7, sesion={"rol": "usuario", "permisos": ["historial"]})
        self.assertEqual(error.exception.status_code, 403)

    def test_permiso_de_datos_no_abre_areas_ajenas(self):
        sesion = {"rol": "usuario", "permisos": ["historial", "pacientes_ver_datos"],
                  "area_ids": []}
        with patch.object(main.servicio_areas, "obtener_area",
                          return_value={"id": 9, "nombre_tabla_base": "pacientes_test"}):
            with self.assertRaises(main.HTTPException) as error:
                main.mensajes_paciente(7, area_id=9, sesion=sesion)
        self.assertEqual(error.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
