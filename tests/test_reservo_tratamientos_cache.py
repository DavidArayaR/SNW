"""La caché de tratamientos evita repetir consultas completas de citas."""

import datetime as dt
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import db
with patch.object(db, "asegurar_tabla_config"):
    import main
import reservo_pacientes as rp


class TestCacheTratamientos(unittest.TestCase):
    ITEMS = [{"uuid": "aabbccdd-1111-2222-3333-001122334455", "nombre": "Consulta"}]

    def body(self):
        return main.ReservoBuscarTratamientosIn(
            clinica_codigo="SMPT", fecha_desde="2026-10-01", fecha_hasta="2026-10-15")

    def test_resultado_vigente_no_contacta_a_reservo(self):
        with (patch.object(main, "config_all", return_value={
                  "SMPT_RESERVO_API_TOKEN": "token-falso"}),
              patch.object(rp, "obtener_tratamientos_cache", return_value=self.ITEMS),
              patch.object(rp, "paginas_citas_cache", return_value=2),
              patch.object(rp, "ClienteReservo") as cliente,
              patch.object(main, "auditoria_registrar")):
            respuesta = main.listar_tratamientos_reservo(self.body(), sesion={"usuario": "admin"})
        self.assertEqual(respuesta, {"items": self.ITEMS})
        cliente.assert_not_called()

    def test_sin_cache_consulta_y_guarda_resultado(self):
        with (patch.object(main, "config_all", return_value={
                  "SMPT_RESERVO_API_TOKEN": "token-falso",
                  "reservo_tratamientos_cache_minutos": "30"}),
              patch.object(rp, "obtener_tratamientos_cache", return_value=None),
              patch.object(rp, "preparar_cache_citas", return_value=True),
              patch.object(rp, "guardar_pagina_citas_cache"),
              patch.object(rp, "ClienteReservo") as cliente,
              patch.object(rp, "guardar_tratamientos_cache") as guardar,
              patch.object(main, "auditoria_registrar")):
            def listar(_desde, _hasta, *, al_recibir_pagina):
                al_recibir_pagina(_desde, _hasta, 1, [])
                return self.ITEMS
            cliente.return_value.listar_tratamientos.side_effect = listar
            respuesta = main.listar_tratamientos_reservo(self.body(), sesion={"usuario": "admin"})
        self.assertEqual(respuesta, {"items": self.ITEMS})
        cliente.return_value.listar_tratamientos.assert_called_once()
        guardar.assert_called_once_with("SMPT", dt.date(2026, 10, 1),
                                       dt.date(2026, 10, 15), "token-falso", self.ITEMS, 30,
                                       1, "")

    def test_huella_cambia_al_rotar_token(self):
        self.assertNotEqual(rp._huella_token("token-uno"), rp._huella_token("token-dos"))
        self.assertNotIn("token-uno", rp._huella_token("token-uno"))

    def test_cita_cache_conserva_solo_campos_necesarios(self):
        cita = {"estado": {"codigo": "A"},
                "tratamientos": [self.ITEMS[0]],
                "cliente": {"uuid": "cliente-1", "nombre": "Paciente", "apellido_paterno": "Prueba",
                            "telefono_1": "+56911111111", "mail": "no-guardar@ejemplo.cl",
                            "direccion": {"calle": "No guardar"}}}
        minimo = rp._cita_para_cache(cita, "A")
        self.assertEqual(minimo["cliente"]["telefono_1"], "+56911111111")
        self.assertNotIn("no-guardar@ejemplo.cl", str(minimo))
        self.assertNotIn("No guardar", str(minimo))

    def test_sincronizacion_usa_paginas_cache_sin_cliente_http(self):
        from reservo_catalogo import clave_estado_atendido, clave_estado_verificado, clave_token
        config = {clave_token("SMPT"): "token-falso",
                  clave_estado_atendido("SMPT"): "A",
                  clave_estado_verificado("SMPT"): "true"}
        job = {"id": 7, "clinica_codigo": "SMPT", "fecha_desde": dt.date(2026, 10, 1),
               "fecha_hasta": dt.date(2026, 10, 1), "estado_atendido_codigo": "A",
               "tratamientos_json": '[{"uuid":"aabbccdd-1111-2222-3333-001122334455","nombre":"Consulta"},'
                                    '{"uuid":"ffeeddcc-1111-2222-3333-001122334455","nombre":"Control"}]',
               "creado_por": "admin"}
        citas = [
            {"estado": {"codigo": "A"}, "tratamientos": self.ITEMS,
             "cliente": {"uuid": "cliente-1", "nombre": "Paciente", "telefono_1": "+56911111111"}},
            {"estado": {"codigo": "A"}, "tratamientos": [
                {"uuid": "ffeeddcc-1111-2222-3333-001122334455", "nombre": "Control"}],
             "cliente": {"uuid": "cliente-2", "nombre": "Paciente", "telefono_1": "+56922222222"}},
            {"estado": {"codigo": "A"}, "tratamientos": [
                {"uuid": "00000000-1111-2222-3333-001122334455", "nombre": "Otra"}],
             "cliente": {"uuid": "cliente-3", "nombre": "Paciente", "telefono_1": "+56933333333"}},
        ]
        with (patch.object(db, "config_all", return_value=config),
              patch.object(rp, "paginas_citas_cache", return_value=1),
              patch.object(rp, "iterar_citas_cache", return_value=iter([
                  (job["fecha_desde"], job["fecha_hasta"], 1, citas)])) as paginas,
              patch.object(rp, "ClienteReservo") as cliente_http,
              patch.object(rp, "_guardar_pagina", return_value=(1, 0)) as guardar,
              patch.object(rp, "_actualizar_progreso"),
              patch.object(rp, "_auditar_sincronizacion")):
            rp._procesar(job)
        paginas.assert_called_once()
        cliente_http.assert_not_called()
        self.assertEqual([fila[0]["uuid"] for fila in guardar.call_args.args[2]],
                         ["cliente-1", "cliente-2"])

    def test_tabla_filtra_por_consulta_iniciada(self):
        conexion = MagicMock()
        cursor = conexion.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = {"total": 0}
        cursor.fetchall.return_value = []
        with patch.object(main, "conectar", return_value=conexion):
            resultado = main.listar_pacientes_reservo(
                clinica=None, tratamiento=None, sincronizacion_id=7,
                desde=None, hasta=None, q=None, pagina=1, por_pagina=25, sesion={})
        self.assertEqual(resultado["total"], 0)
        sql, parametros = cursor.execute.call_args_list[0].args
        self.assertIn("reservo_sincronizacion_pacientes", sql)
        self.assertEqual(parametros, [7])


if __name__ == "__main__":
    unittest.main()
