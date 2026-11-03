"""La caché de tratamientos evita repetir consultas completas de citas."""

import datetime as dt
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

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
              patch.object(rp, "ClienteReservo") as cliente,
              patch.object(rp, "guardar_tratamientos_cache") as guardar,
              patch.object(main, "auditoria_registrar")):
            cliente.return_value.listar_tratamientos.return_value = self.ITEMS
            respuesta = main.listar_tratamientos_reservo(self.body(), sesion={"usuario": "admin"})
        self.assertEqual(respuesta, {"items": self.ITEMS})
        cliente.return_value.listar_tratamientos.assert_called_once_with(
            dt.date(2026, 10, 1), dt.date(2026, 10, 15))
        guardar.assert_called_once_with("SMPT", dt.date(2026, 10, 1),
                                       dt.date(2026, 10, 15), "token-falso", self.ITEMS, 30)

    def test_huella_cambia_al_rotar_token(self):
        self.assertNotEqual(rp._huella_token("token-uno"), rp._huella_token("token-dos"))
        self.assertNotIn("token-uno", rp._huella_token("token-uno"))


if __name__ == "__main__":
    unittest.main()
