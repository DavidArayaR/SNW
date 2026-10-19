import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import db
import main


class PermisosEnviosTests(unittest.TestCase):
    def test_migracion_acciones_conserva_capacidades_sin_reotorgar_por_rol(self):
        migrados = set(db._migrar_permisos_acciones_legacy(
            "mensajeria,plantillas_editar,call_center_registro,pacientes_ver_datos",
            "supervisor").split(","))
        self.assertTrue({"pacientes_vaciar", "envios_cancelar", "envios_decidir",
                         "plantillas_eliminar", "call_center_reintentar"} <= migrados)
        self.assertNotIn("envios_decidir", db._migrar_permisos_acciones_legacy(
            "mensajeria", "usuario").split(","))

    def test_acciones_sensibles_no_heredan_permisos_generales(self):
        sesion = {"rol": "usuario", "usuario": "test@ejemplo.cl", "area_ids": [9],
                  "permisos": ["mensajeria", "plantillas_editar", "call_center_registro",
                               "pacientes_ver_datos"]}
        main.app.dependency_overrides[main.sesion_actual] = lambda: sesion
        cliente = TestClient(main.app)
        try:
            with patch.object(main.servicio_areas, "obtener_area", return_value={"id": 9}):
                self.assertEqual(cliente.delete("/api/areas/9/tabla/datos").status_code, 403)
            self.assertEqual(cliente.delete("/api/plantillas/1").status_code, 403)
            self.assertEqual(cliente.post("/api/notificaciones/jobs/job/cancelar").status_code, 403)
            self.assertEqual(cliente.post("/api/notificaciones/programados/1/aprobar").status_code, 403)
            self.assertEqual(cliente.post("/api/call-center/derivaciones/1/reintentar").status_code, 403)
        finally:
            main.app.dependency_overrides.clear()

    def test_vaciar_area_funciona_con_permiso_especifico_y_area_asignada(self):
        sesion = {"rol": "usuario", "usuario": "test@ejemplo.cl", "area_ids": [9],
                  "permisos": ["pacientes_vaciar"]}
        main.app.dependency_overrides[main.sesion_actual] = lambda: sesion
        try:
            with patch.object(main.servicio_areas, "obtener_area", return_value={
                    "id": 9, "nombre_visible": "Prueba"}), \
                 patch.object(main.servicio_areas, "usuario_id_por_correo", return_value=3), \
                 patch.object(main.servicio_areas, "puede_acceder_area", return_value=True), \
                 patch.object(main.servicio_areas, "vaciar_tabla_area", return_value={
                    "pacientes_eliminados": 2, "nombre_tabla_base": "pacientes_prueba"}), \
                 patch.object(main, "auditoria_registrar"):
                respuesta = TestClient(main.app).delete("/api/areas/9/tabla/datos")
            self.assertEqual(respuesta.status_code, 200)
            self.assertEqual(respuesta.json()["pacientes_eliminados"], 2)
        finally:
            main.app.dependency_overrides.clear()

    def test_control_job_requiere_permiso_autor_y_area(self):
        job = {"usuario": "dueño@ejemplo.cl", "area_id": 9}
        sesion = {"rol": "usuario", "usuario": "dueño@ejemplo.cl", "area_ids": [9],
                  "permisos": ["envios_cancelar"]}
        self.assertTrue(main._puede_controlar_job(sesion, job))
        self.assertFalse(main._puede_controlar_job({**sesion, "area_ids": [8]}, job))
        self.assertFalse(main._puede_controlar_job({**sesion, "usuario": "otro@ejemplo.cl"}, job))
        self.assertFalse(main._puede_controlar_job({**sesion, "permisos": ["mensajeria"]}, job))

    def test_decidir_programado_requiere_permiso_y_area(self):
        f = {"area_id": 9}
        sesion = {"rol": "supervisor", "area_ids": [9], "permisos": ["envios_decidir"]}
        main._exigir_prog_decisor(sesion, f)
        for sin_permiso in ({**sesion, "permisos": []}, {**sesion, "area_ids": [8]}):
            with self.assertRaises(main.HTTPException) as error:
                main._exigir_prog_decisor(sin_permiso, f)
            self.assertEqual(error.exception.status_code, 403)

    def test_correo_de_aprobacion_solo_a_supervisores_con_permiso(self):
        destinatarios = [
            {"usuario": "con@ejemplo.cl", "correo": "con@ejemplo.cl"},
            {"usuario": "sin@ejemplo.cl", "correo": "sin@ejemplo.cl"},
        ]
        cuentas = {
            "con@ejemplo.cl": {"rol": "supervisor", "permisos": ["envios_decidir"]},
            "sin@ejemplo.cl": {"rol": "supervisor", "permisos": ["mensajeria"]},
        }
        with patch.object(main.servicio_areas, "correos_supervisores_area",
                          return_value=destinatarios), \
             patch.object(main, "usuario_buscar", side_effect=cuentas.get):
            self.assertEqual(main._correos_supervisores_decisores(9), ["con@ejemplo.cl"])

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
