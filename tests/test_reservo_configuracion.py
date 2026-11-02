"""Protección y persistencia de credenciales Reservo en Configuración."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import db
with patch.object(db, "asegurar_tabla_config"):
    import main

from reservo_catalogo import (CLAVES_TOKEN, RESERVO_CLINICAS, clave_estado_atendido,
                              clave_estado_verificado, clave_token, clave_tratamiento)


class TestConfiguracionReservo(unittest.TestCase):
    TRATAMIENTO = "aabbccdd-1111-2222-3333-001122334455"

    def configuracion(self):
        datos = dict(main.CONFIG_DEFAULTS)
        for codigo in RESERVO_CLINICAS:
            datos[clave_token(codigo)] = f"token-privado-{codigo}"
            datos[clave_tratamiento(codigo)] = self.TRATAMIENTO
            datos[clave_estado_atendido(codigo)] = "REALIZADA"
            datos[clave_estado_verificado(codigo)] = "true"
        return datos

    def test_get_config_nunca_devuelve_tokens_reversibles(self):
        datos = self.configuracion()
        valores = main._config_valores(datos)
        self.assertEqual(CLAVES_TOKEN, {clave_token(codigo) for codigo in RESERVO_CLINICAS})
        for clave in CLAVES_TOKEN:
            self.assertEqual(valores[clave], "")
            self.assertNotIn(datos[clave], valores.values())

    def test_respuesta_completa_de_configuracion_no_incluye_tokens_ni_su_estado(self):
        datos = self.configuracion()
        with patch.object(main, "config_all", return_value=datos), \
             patch.object(main, "config_get", return_value=""), \
             patch.object(main, "listar_canales", return_value=[]), \
             patch.object(main, "resumen_supresion", return_value={}):
            respuesta = main.obtener_configuracion_completa(sesion={"rol": "desarrollador"})
        for clave in CLAVES_TOKEN:
            self.assertEqual(respuesta["valores"][clave], "")
            self.assertNotIn(datos[clave], respuesta.values())
        self.assertNotIn("reservo_tokens_guardados", respuesta)

    def test_campo_token_vacio_no_borra_credencial_guardada(self):
        datos = self.configuracion()
        clave = clave_token("SMPT")
        with patch.object(main, "config_all", return_value=datos), \
             patch.object(main, "config_set") as guardar, \
             patch.object(main, "listar_canales", return_value=[]), \
             patch.object(main, "config_get", return_value=""):
            main.actualizar_configuracion_completa(
                main.ConfigTodoIn(cambios={clave: ""}), sesion={"rol": "desarrollador"})
        guardar.assert_not_called()

    def test_token_nuevo_invalida_confirmacion_de_estado_atendido(self):
        datos = self.configuracion()
        clave = clave_token("MS")
        with patch.object(main, "config_all", return_value=datos), \
             patch.object(main, "config_set") as guardar, \
             patch.object(main, "listar_canales", return_value=[]), \
             patch.object(main, "config_get", return_value=""):
            main.actualizar_configuracion_completa(
                main.ConfigTodoIn(cambios={clave: "token-rotado-sintetico"}),
                sesion={"rol": "desarrollador"})
        cambios = guardar.call_args.args[0]
        self.assertEqual(cambios[clave], "token-rotado-sintetico")
        self.assertEqual(cambios[clave_estado_verificado("MS")], "false")


if __name__ == "__main__":
    unittest.main()
