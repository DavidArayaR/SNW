"""El detalle de Historial muestra fallos de Meta sin exponer mensajes sin permiso."""

import datetime
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import main
from fastapi import HTTPException


class HistorialEntregasTests(unittest.TestCase):
    def _detalle(self, puede_ver_datos):
        conn = MagicMock()
        conn.__enter__.return_value = conn
        cur = conn.cursor.return_value
        cur.__enter__.return_value = cur
        cur.fetchone.return_value = {"area_id": None, "tabla_pacientes": None}
        cur.fetchall.return_value = [{
            "id": 8, "paciente_id": 10, "nombre_paciente": "María Pérez",
            "numero_telefono": "+56912345678", "estado_envio": "error",
            "estado_whatsapp": "failed", "mensaje_enviado": "Hola María",
            "descripcion_error": "131042 | Business eligibility payment issue",
            "fecha_hora": datetime.datetime(2026, 10, 29, 17, 51),
            "mensaje_respuesta": None, "interesado": 0, "respuesta": "pendiente",
        }]
        with patch.object(main, "conectar", return_value=conn), \
                patch.object(main, "columnas_tabla", side_effect=lambda t, _a: (
                    {"area_id", "tabla_pacientes"} if t == "envios" else set())), \
                patch.object(main, "columna_existe", return_value=False), \
                patch.object(main, "expr_respuesta_efectiva", return_value="'pendiente'"), \
                patch.object(main, "tabla_pacientes", return_value="pacientes_prod"), \
                patch.object(main, "_es_privilegiado", return_value=True), \
                patch.object(main, "tiene_permiso", return_value=puede_ver_datos):
            filas = main.detalle_historial("7", ambiente="produccion", sesion={"rol": "admin"})
        self.assertIn("le.estado_whatsapp", cur.execute.call_args_list[-1].args[0])
        return filas[0]

    def test_sin_permiso_ve_estado_y_codigo_pero_no_texto_ni_error_crudo(self):
        fila = self._detalle(False)
        self.assertEqual(fila["estado_whatsapp"], "failed")
        self.assertEqual(fila["codigo_error"], "131042")
        self.assertIsNone(fila["mensaje_enviado"])
        self.assertIsNone(fila["descripcion_error"])
        self.assertNotEqual(fila["numero_telefono"], "+56912345678")

    def test_con_permiso_ve_mensaje_y_motivo(self):
        fila = self._detalle(True)
        self.assertEqual(fila["mensaje_enviado"], "Hola María")
        self.assertIn("131042", fila["descripcion_error"])


class MarcarFallidosPendientesTests(unittest.TestCase):
    def test_ruta_esta_publicada(self):
        rutas = [ruta for incluido in main.app.routes
                 if hasattr(incluido, "original_router")
                 for ruta in incluido.original_router.routes]
        self.assertTrue(any(
            getattr(r, "path", None) == "/api/notificaciones/historial/{envio_id}/marcar-pendientes"
            and "POST" in getattr(r, "methods", set()) for r in rutas))

    def test_exito_tardio_no_pisa_fallo_de_meta(self):
        conn = MagicMock()
        conn.__enter__.return_value = conn
        cur = conn.cursor.return_value
        cur.__enter__.return_value = cur
        with patch.object(main, "conectar", return_value=conn), \
                patch.object(main, "columna_existe", return_value=True):
            main.actualizar_estado_paciente(10, "enviado", "produccion",
                                            tabla="pacientes_prod", whatsapp_message_id="wamid-prueba")
        self.assertIn("NOT EXISTS", cur.execute.call_args.args[0])
        self.assertEqual(cur.execute.call_args.args[1], ("enviado", 10, "wamid-prueba"))

    def _ejecutar(self, *, ultimo=8, opt_out=0, estado="enviado", suprimido=False,
                  rol="administrador"):
        conn = MagicMock()
        conn.__enter__.return_value = conn
        cur = conn.cursor.return_value
        cur.__enter__.return_value = cur
        cur.fetchone.side_effect = [
            {"id": 7, "base_datos": "pacientes_prod", "tabla_pacientes": None},
            {"ultimo": ultimo},
            {"telefono": "+56912345678", "estado": estado, "whatsapp_opt_out": opt_out},
        ]
        cur.fetchall.return_value = [{
            "id": 8, "paciente_id": 10, "numero_telefono": "+56912345678",
            "estado_envio": "error", "estado_whatsapp": "failed",
            "tabla_pacientes": None,
        }]
        with patch.object(main, "conectar", return_value=conn), \
                patch.object(main.servicio_areas, "tabla_valida", return_value=True), \
                patch.object(main.servicio_areas, "tabla_fisica_existe", return_value=True), \
                patch.object(main, "columna_existe", return_value=True), \
                patch.object(main, "esta_suprimido", return_value=suprimido), \
                patch.object(main, "auditoria_registrar") as auditoria:
            resultado = main.marcar_fallidos_pendientes(
                7, main.FallidosPendientesIn(registros=[8]),
                sesion={"rol": rol, "usuario": "admin"})
        return resultado, cur, auditoria

    def test_fallo_confirmado_vuelve_a_pendiente_sin_tocar_el_log(self):
        resultado, cur, auditoria = self._ejecutar()
        self.assertEqual(resultado["actualizados"], 1)
        self.assertTrue(any("UPDATE pacientes_prod SET estado = 'pendiente'" in c.args[0]
                            for c in cur.execute.call_args_list))
        self.assertFalse(any("UPDATE log_envios SET" in c.args[0]
                             for c in cur.execute.call_args_list))
        auditoria.assert_called_once()

    def test_no_cambia_si_hubo_otro_envio_posterior(self):
        resultado, cur, _ = self._ejecutar(ultimo=9)
        self.assertEqual((resultado["actualizados"], resultado["omitidos"]), (0, 1))
        self.assertFalse(any("UPDATE pacientes_prod" in c.args[0]
                             for c in cur.execute.call_args_list))

    def test_no_cambia_baja_ni_paciente_ya_pendiente(self):
        for opciones in ({"opt_out": 1}, {"estado": "pendiente"}, {"suprimido": True}):
            with self.subTest(opciones=opciones):
                resultado, _, _ = self._ejecutar(**opciones)
                self.assertEqual((resultado["actualizados"], resultado["omitidos"]), (0, 1))

    def test_requiere_administrador_o_desarrollador(self):
        with self.assertRaises(HTTPException) as error:
            main.marcar_fallidos_pendientes(
                7, main.FallidosPendientesIn(registros=[8]),
                sesion={"rol": "usuario"})
        self.assertEqual(error.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
