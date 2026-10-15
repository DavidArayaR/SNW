import datetime
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import main


class HistorialOrigenTests(unittest.TestCase):
    def test_tabla_dice_tipo_y_muestra_registro_call_center(self):
        raiz = Path(__file__).resolve().parents[1] / "frontend"
        pagina = (raiz / "historial.html").read_text(encoding="utf-8")
        script = (raiz / "js" / "historial.js").read_text(encoding="utf-8")
        self.assertIn("<th>Tipo</th>", pagina)
        self.assertIn("panelCCRegistro", pagina)
        self.assertIn("tablaCCDerivaciones", pagina)
        self.assertIn("api/call-center/log", script)

    def test_detalle_permite_actualizar_sin_cerrar_el_modal(self):
        raiz = Path(__file__).resolve().parents[1] / "frontend"
        pagina = (raiz / "historial.html").read_text(encoding="utf-8")
        script = (raiz / "js" / "historial.js").read_text(encoding="utf-8")
        self.assertIn('id="btnActualizarDetalle"', pagina)
        self.assertIn('id="tablaDetallePacientes"', pagina)
        self.assertIn('$("#btnActualizarDetalle").addEventListener("click", cargarDetalle)', script)
        self.assertIn('cache: "no-store"', script)
        self.assertIn('tabla.setAttribute("aria-busy", "true")', script)

    def test_ventana_de_mensajes_permite_etiquetar_sin_cambiar_estado(self):
        raiz = Path(__file__).resolve().parents[1] / "frontend"
        pagina = (raiz / "historial.html").read_text(encoding="utf-8")
        script = (raiz / "js" / "historial.js").read_text(encoding="utf-8")
        self.assertIn("msg-etiquetas-ayuda", pagina)
        self.assertIn("data-etiqueta-id", script)
        self.assertIn("/etiquetas?ambiente=", script)

    def test_etiquetas_solo_para_respuesta_y_autor_del_csv(self):
        body = main.EtiquetasRespuestaIn(etiquetas=["consulta", "requiere_revision"])
        conexion = MagicMock()
        cur = conexion.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = {"id": 17}
        kwargs = dict(paciente_id=4, mensaje_id=17, body=body,
                      ambiente="produccion", area_id=None, sesion={"usuario": "autor"})
        with patch.object(main, "tiene_permiso", return_value=True), \
             patch.object(main, "_resolver_tabla_pacientes", return_value=("pacientes_prod", None)), \
             patch.object(main, "_ids_csv_propios", return_value={4}), \
             patch.object(main, "_cond_tabla_log_segura", return_value=("", ())), \
             patch.object(main, "conectar", return_value=conexion), \
             patch.object(main, "auditoria_registrar") as auditar:
            respuesta = main.etiquetar_mensaje_paciente(**kwargs)
        self.assertEqual(respuesta["etiquetas"], ["consulta", "requiere_revision"])
        self.assertIn("plantilla_clave = 'respuesta'", cur.execute.call_args_list[0].args[0])
        self.assertIn("INSERT INTO respuesta_etiquetas", cur.execute.call_args_list[1].args[0])
        auditar.assert_called_once()
        with patch.object(main, "tiene_permiso", return_value=True), \
             patch.object(main, "_resolver_tabla_pacientes", return_value=("pacientes_prod", None)), \
             patch.object(main, "_ids_csv_propios", return_value=set()):
            with self.assertRaises(HTTPException) as error:
                main.etiquetar_mensaje_paciente(**kwargs)
        self.assertEqual(error.exception.status_code, 403)
        with self.assertRaises(HTTPException) as error:
            main.etiquetar_mensaje_paciente(**{**kwargs,
                "body": main.EtiquetasRespuestaIn(etiquetas=["etiqueta_inventada"])})
        self.assertEqual(error.exception.status_code, 422)

    def test_vaciar_etiquetas_se_conserva_como_revision_manual(self):
        conexion = MagicMock()
        cur = conexion.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = {"id": 17}
        with patch.object(main, "tiene_permiso", return_value=True), \
             patch.object(main, "_resolver_tabla_pacientes", return_value=("pacientes_prod", None)), \
             patch.object(main, "_ids_csv_propios", return_value={4}), \
             patch.object(main, "_cond_tabla_log_segura", return_value=("", ())), \
             patch.object(main, "conectar", return_value=conexion), \
             patch.object(main, "auditoria_registrar"):
            respuesta = main.etiquetar_mensaje_paciente(
                paciente_id=4, mensaje_id=17,
                body=main.EtiquetasRespuestaIn(etiquetas=[]),
                ambiente="produccion", area_id=None, sesion={"usuario": "autor"})
        self.assertEqual(respuesta["origen_etiquetas"], "manual")
        self.assertEqual(respuesta["etiquetas"], [])
        self.assertIn("INSERT INTO respuesta_etiquetas", cur.execute.call_args_list[1].args[0])
        self.assertFalse(any("DELETE FROM respuesta_etiquetas" in c.args[0]
                             for c in cur.execute.call_args_list))

    def _consultar(self, origen, lotes, programados=()):
        conexion = MagicMock()
        cur = conexion.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.fetchall.side_effect = [lotes] + ([list(programados)] if origen != "manual" else [])
        with patch.object(main, "conectar", return_value=conexion), \
                patch.object(main, "columnas_tabla", return_value={"programado_id", "area_id", "tabla_pacientes"}), \
                patch.object(main, "_es_privilegiado", return_value=True), \
                patch.object(main, "_prog_visible", return_value=True), \
                patch.object(main, "_prog_a_respuesta", return_value={"puede_cancelar": False}):
            resultado = main.listar_historial(
                q=None, estado=None, ambiente="todos", area_id=None, tabla=None,
                origen=origen, sesion={"rol": "administrador"})
        return resultado, [c.args[0] for c in cur.execute.call_args_list]

    def test_manuales_no_incluye_programados(self):
        fecha = datetime.datetime(2026, 10, 8, 10)
        filas, consultas = self._consultar("manual", [{"id": 1, "fecha_hora": fecha,
            "programado_id": None, "programado_creado": None, "base_datos": "pacientes_prod"}])
        self.assertEqual([f["origen"] for f in filas], ["manual"])
        self.assertIn("programado_id IS NULL", consultas[0])
        self.assertEqual(len(consultas), 1)

    def test_programados_ejecutados_y_pendientes_sin_duplicados(self):
        fecha = datetime.datetime(2026, 10, 8, 10)
        creado = datetime.datetime(2026, 10, 7, 9)
        lote = {"id": 2, "fecha_hora": fecha, "programado_id": 5,
                "programado_creado": creado, "base_datos": "pacientes_prod"}
        pendiente = {"id": 6, "creado": fecha, "programado_para": fecha,
                     "tabla": "pacientes_prod", "ambiente": "produccion",
                     "estado": "pendiente", "area_id": None}
        filas, consultas = self._consultar("programado", [lote], [pendiente])
        self.assertEqual([f["id"] for f in filas], ["prog-6", 2])
        self.assertEqual([f["origen"] for f in filas], ["programado", "programado"])
        self.assertEqual(filas[1]["fecha"], "07-10-2026 09:00")
        self.assertIn("programado_id IS NOT NULL", consultas[0])
        self.assertIn("NOT EXISTS", consultas[1])

    def test_valor_desconocido_rechazado(self):
        with self.assertRaises(HTTPException) as error:
            main.listar_historial(origen="otro", sesion={})
        self.assertEqual(error.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
