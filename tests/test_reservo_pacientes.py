import datetime as dt
import sys
import unittest
from pathlib import Path

import httpx
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import reservo_pacientes as rp


class TestReservoPeriodos(unittest.TestCase):
    def test_divide_rango_inclusivo_en_bloques_maximo_31_dias(self):
        bloques = rp.dividir_periodo(dt.date(2026, 1, 1), dt.date(2026, 2, 3))
        self.assertEqual(bloques, [
            (dt.date(2026, 1, 1), dt.date(2026, 1, 31)),
            (dt.date(2026, 2, 1), dt.date(2026, 2, 3)),
        ])
        self.assertEqual(sum((fin - inicio).days + 1 for inicio, fin in bloques), 34)

    def test_rechaza_rango_invertido(self):
        with self.assertRaises(ValueError):
            rp.dividir_periodo(dt.date(2026, 2, 1), dt.date(2026, 1, 1))

    def test_valida_uuid_con_y_sin_guiones(self):
        self.assertEqual(rp.validar_uuid("AABBCCDD-1111-2222-3333-001122334455"),
                         "aabbccdd-1111-2222-3333-001122334455")
        self.assertEqual(rp.validar_uuid("aabbccdd111122223333001122334455"),
                         "aabbccdd111122223333001122334455")
        with self.assertRaises(ValueError):
            rp.validar_uuid("no-es-un-uuid")


class TestFiltrosReservo(unittest.TestCase):
    TRATAMIENTO = "aabbccdd-1111-2222-3333-001122334455"

    def test_solo_coincide_codigo_atendido_y_uuid_exacto(self):
        atendida = {"estado": {"codigo": "REALIZADA"},
                    "tratamientos": [{"uuid": self.TRATAMIENTO.upper()}]}
        confirmada = {"estado": {"codigo": "CONFIRMADA"},
                      "tratamientos": [{"uuid": self.TRATAMIENTO}]}
        otro_tratamiento = {"estado": {"codigo": "REALIZADA"},
                            "tratamientos": [{"uuid": "ffeeddcc-1111-2222-3333-001122334455"}]}
        pagada = {"estado": {"codigo": "PAGADA"},
                  "tratamientos": [{"uuid": self.TRATAMIENTO}]}
        cancelada = {"estado": {"codigo": "CANCELADA"},
                     "tratamientos": [{"uuid": self.TRATAMIENTO}]}
        self.assertTrue(rp.coincide_cita(atendida, "REALIZADA", self.TRATAMIENTO))
        self.assertFalse(rp.coincide_cita(confirmada, "REALIZADA", self.TRATAMIENTO))
        self.assertFalse(rp.coincide_cita(otro_tratamiento, "REALIZADA", self.TRATAMIENTO))
        self.assertFalse(rp.coincide_cita(pagada, "REALIZADA", self.TRATAMIENTO))
        self.assertFalse(rp.coincide_cita(cancelada, "REALIZADA", self.TRATAMIENTO))

    def test_acepta_cualquiera_de_varios_tratamientos_sin_coincidencia_parcial(self):
        cita = {"estado": {"codigo": "REALIZADA"}, "tratamientos": [
            {"uuid": "ffeeddcc-1111-2222-3333-001122334455"},
            {"uuid": self.TRATAMIENTO.upper()},
        ]}
        self.assertTrue(rp.coincide_cita(cita, "REALIZADA", self.TRATAMIENTO))
        self.assertFalse(rp.coincide_cita(cita, "REALIZADA", self.TRATAMIENTO[:10]))

    def test_estado_sin_codigo_y_cliente_sin_uuid_no_se_aceptan(self):
        self.assertEqual(rp.codigo_estado({"estado": {"descripcion": "Atendido"}}), "")
        self.assertIsNone(rp.cliente_valido({"cliente": {"nombre": "Paciente"}}))

    def test_dedup_se_aisla_por_codigo_y_uuid_de_cliente(self):
        datos = [
            {"estado": {"codigo": "10"}, "tratamientos": [{"uuid": self.TRATAMIENTO}],
             "cliente": {"uuid": "PACIENTE-1"}},
            {"estado": {"codigo": "10"}, "tratamientos": [{"uuid": self.TRATAMIENTO}],
             "cliente": {"uuid": "PACIENTE-1"}},
            {"estado": {"codigo": "10"}, "tratamientos": [{"uuid": self.TRATAMIENTO}],
             "cliente": {"uuid": "PACIENTE-2"}},
        ]
        vistos = set()
        unicos = []
        duplicados = 0
        for cita in datos:
            if not rp.coincide_cita(cita, "10", self.TRATAMIENTO):
                continue
            cliente = rp.cliente_valido(cita)
            if cliente["uuid"] in vistos:
                duplicados += 1
                continue
            vistos.add(cliente["uuid"])
            unicos.append(cliente["uuid"])
        self.assertEqual(unicos, ["PACIENTE-1", "PACIENTE-2"])
        self.assertEqual(duplicados, 1)

    def test_no_prepara_sync_si_estado_atendido_no_fue_verificado(self):
        from reservo_catalogo import (clave_estado_atendido, clave_estado_verificado,
                                      clave_token, clave_tratamiento)
        cfg = {
            clave_token("SMPT"): "token-local-falso",
            clave_tratamiento("SMPT"): self.TRATAMIENTO,
            clave_estado_atendido("SMPT"): "10",
            clave_estado_verificado("SMPT"): "false",
        }
        with self.assertRaisesRegex(ValueError, "verifica el código"):
            rp.preparar_sincronizacion("SMPT", dt.date(2026, 1, 1), dt.date(2026, 1, 2), cfg)

    def test_tokens_y_tratamientos_se_seleccionan_de_forma_independiente_por_clinica(self):
        from reservo_catalogo import (clave_estado_atendido, clave_estado_verificado,
                                      clave_token, clave_tratamiento)
        cfg = {}
        for codigo, token in (("SMPT", "token-smpt"), ("MS", "token-ms"), ("MVYS", "token-mvys")):
            cfg[clave_token(codigo)] = token
            cfg[clave_tratamiento(codigo)] = self.TRATAMIENTO
            cfg[clave_estado_atendido(codigo)] = "REALIZADA"
            cfg[clave_estado_verificado(codigo)] = "true"
        for codigo, token in (("SMPT", "token-smpt"), ("MS", "token-ms"), ("MVYS", "token-mvys")):
            self.assertEqual(rp.preparar_sincronizacion(
                codigo, dt.date(2026, 1, 1), dt.date(2026, 1, 2), cfg)["token"], token)
        with self.assertRaisesRegex(ValueError, "token API"):
            rp.preparar_sincronizacion("MS", dt.date(2026, 1, 1), dt.date(2026, 1, 2), {
                clave_token("SMPT"): "solo-smpt",
                clave_tratamiento("MS"): self.TRATAMIENTO,
                clave_estado_atendido("MS"): "REALIZADA",
                clave_estado_verificado("MS"): "true",
            })

    def test_uuid_de_cliente_hexadecimal_se_normaliza_sin_alterar_otros_ids(self):
        self.assertEqual(rp.normalizar_cliente_uuid("AABBCCDD-1111-2222-3333-001122334455"),
                         "aabbccdd-1111-2222-3333-001122334455")
        self.assertEqual(rp.normalizar_cliente_uuid("Cliente-ABC"), "Cliente-ABC")

    def test_error_de_base_de_datos_no_expone_mensaje_con_datos(self):
        error = RuntimeError("Paciente Nombre, teléfono +56912345678, SQL token=secreto")
        resumen = rp.resumen_error_seguro(error)
        self.assertNotIn("Nombre", resumen)
        self.assertNotIn("56912345678", resumen)
        self.assertNotIn("secreto", resumen)
        self.assertEqual(resumen, "Error interno de sincronización (RuntimeError).")


class TestClienteHttpReservo(unittest.TestCase):
    def test_recorrido_pagina_y_auth_token_sin_logica_de_pacientes(self):
        peticiones = []

        def responder(request):
            peticiones.append(request)
            pagina = request.url.params.get("pagina")
            if pagina == "1":
                return httpx.Response(200, json={"resultados": [{"estado": {"codigo": 10}}],
                                                 "pagina_siguiente": 2})
            return httpx.Response(200, json={"resultados": [{"estado": {"codigo": 20}}],
                                             "pagina_siguiente": None})

        cliente = rp.ClienteReservo("token-falso-no-real", transport=httpx.MockTransport(responder), sleeper=lambda _s: None)
        try:
            citas = list(cliente.iterar_citas(dt.date(2026, 1, 2), dt.date(2026, 1, 2)))
        finally:
            cliente.close()
        self.assertEqual(len(citas), 2)
        self.assertTrue(all(p.headers["Authorization"] == "Token token-falso-no-real" for p in peticiones))
        self.assertTrue(all(p.url.path.endswith("/citas/") for p in peticiones))

    def test_error_http_no_incluye_cuerpo_potencialmente_personal(self):
        cliente = rp.ClienteReservo(
            "token-falso", transport=httpx.MockTransport(
                lambda _request: httpx.Response(403, json={"detalle": "Paciente Nombre 56912345678"})),
            sleeper=lambda _s: None,
        )
        try:
            with self.assertRaises(rp.ReservoError) as contexto:
                cliente.obtener_pagina(dt.date(2026, 1, 1), dt.date(2026, 1, 1))
        finally:
            cliente.close()
        self.assertNotIn("Paciente", str(contexto.exception))
        self.assertEqual(contexto.exception.status_code, 403)

    def test_paginacion_no_sigue_a_host_ajeno(self):
        def responder(_request):
            return httpx.Response(200, json={"resultados": [],
                                             "pagina_siguiente": "https://example.com/citas/?pagina=2"})
        cliente = rp.ClienteReservo("token-falso", transport=httpx.MockTransport(responder), sleeper=lambda _s: None)
        try:
            with self.assertRaisesRegex(rp.ReservoError, "paginación inesperada"):
                list(cliente.iterar_citas(dt.date(2026, 1, 1), dt.date(2026, 1, 1)))
        finally:
            cliente.close()


class _CursorFalso:
    def __init__(self, *, fallar_tratamiento=False):
        self.consultas = []
        self.resultado = None
        self.lastrowid = None
        self.fallar_tratamiento = fallar_tratamiento

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, parametros=()):
        self.consultas.append((sql, parametros))
        if sql.startswith("SELECT id FROM reservo_pacientes"):
            self.resultado = None
        elif sql.startswith("INSERT INTO reservo_pacientes"):
            self.lastrowid = 44
        elif sql.startswith("INSERT INTO reservo_paciente_tratamientos") and self.fallar_tratamiento:
            raise RuntimeError("Fallo sintético en relación de tratamiento")

    def fetchone(self):
        return self.resultado


class _ConexionFalsa:
    def __init__(self, *, fallar_tratamiento=False):
        self.cur = _CursorFalso(fallar_tratamiento=fallar_tratamiento)
        self.commits = 0
        self.rollbacks = 0

    def __enter__(self):
        return self

    def __exit__(self, tipo, *_args):
        if tipo:
            self.rollbacks += 1
        return False

    def cursor(self):
        return self.cur

    def commit(self):
        self.commits += 1


class TestPersistenciaAtomica(unittest.TestCase):
    def test_pacientes_relaciones_y_progreso_se_confirman_en_una_transaccion(self):
        import db

        conexion = _ConexionFalsa()
        tratamiento = "aabbccdd-1111-2222-3333-001122334455"
        cliente = {"uuid": "AABBCCDD-1111-2222-3333-001122334455",
                   "nombre": "Paciente prueba", "telefono": "+56912345678"}
        with patch.object(db, "conectar", return_value=conexion):
            nuevos, actualizados = rp._guardar_pagina(
                9, "SMPT", tratamiento, [(cliente, "Tratamiento de prueba")], {
                    "paginas_consultadas": 1, "citas_recibidas": 2,
                    "citas_atendidas": 1, "citas_tratamiento": 1,
                    "pacientes_unicos": 1, "pacientes_descartados": 0,
                    "pacientes_duplicados": 0,
                })
        self.assertEqual((nuevos, actualizados), (1, 0))
        self.assertEqual(conexion.commits, 1)
        self.assertEqual(conexion.rollbacks, 0)
        parametros_insert = next(params for sql, params in conexion.cur.consultas
                                 if sql.startswith("INSERT INTO reservo_pacientes"))
        self.assertEqual(parametros_insert[1], "aabbccdd-1111-2222-3333-001122334455")
        self.assertTrue(any("pacientes_nuevos=pacientes_nuevos+%s" in sql
                            for sql, _ in conexion.cur.consultas))

    def test_fallo_de_una_relacion_revierte_toda_la_pagina(self):
        import db

        conexion = _ConexionFalsa(fallar_tratamiento=True)
        cliente = {"uuid": "cliente-44", "nombre": "Paciente prueba"}
        with patch.object(db, "conectar", return_value=conexion):
            with self.assertRaisesRegex(RuntimeError, "Fallo sintético"):
                rp._guardar_pagina(9, "SMPT", "tratamiento-1", [(cliente, None)], {
                    "paginas_consultadas": 1, "citas_recibidas": 1,
                    "citas_atendidas": 1, "citas_tratamiento": 1,
                    "pacientes_unicos": 1, "pacientes_descartados": 0,
                    "pacientes_duplicados": 0,
                })
        self.assertEqual(conexion.commits, 0)
        self.assertEqual(conexion.rollbacks, 1)


class TestFalloParcial(unittest.TestCase):
    def test_error_en_pagina_intermedia_no_marca_completado_y_sanitiza_error(self):
        import db
        from reservo_catalogo import (clave_estado_atendido, clave_estado_verificado,
                                      clave_token, clave_tratamiento)

        tratamiento = "aabbccdd-1111-2222-3333-001122334455"
        config = {
            clave_token("SMPT"): "token-falso",
            clave_tratamiento("SMPT"): tratamiento,
            clave_estado_atendido("SMPT"): "REALIZADA",
            clave_estado_verificado("SMPT"): "true",
        }
        job = {"id": 99, "clinica_codigo": "SMPT",
               "fecha_desde": dt.date(2026, 1, 1), "fecha_hasta": dt.date(2026, 1, 2),
               "estado_atendido_codigo": "REALIZADA", "tratamiento_uuid": tratamiento,
               "creado_por": "admin"}

        class ClienteFallaEnPaginaDos:
            def __init__(self, _token):
                pass

            def close(self):
                pass

            def iterar_citas(self, _desde, _hasta):
                yield dt.date(2026, 1, 1), dt.date(2026, 1, 2), 1, [{
                    "estado": {"codigo": "REALIZADA"},
                    "tratamientos": [{"uuid": tratamiento, "nombre": "Tratamiento de prueba"}],
                    "cliente": {"uuid": "cliente-sintetico", "nombre": "Paciente ficticio"},
                }]
                raise RuntimeError("Paciente de prueba +56912345678 token=secreto")

        progreso = []
        paginas_guardadas = []

        def guardar_pagina(*args):
            paginas_guardadas.append(args)
            return 1, 0

        with (patch.object(db, "config_all", return_value=config),
              patch.object(db, "log_error"),
              patch.object(db, "auditoria_registrar"),
              patch.object(rp, "ClienteReservo", ClienteFallaEnPaginaDos),
              patch.object(rp, "_actualizar_progreso", side_effect=lambda _id, **v: progreso.append(v)),
              patch.object(rp, "_guardar_pagina", side_effect=guardar_pagina)):
            rp._procesar(job)
        estados = [cambio.get("estado") for cambio in progreso if "estado" in cambio]
        self.assertEqual(estados, ["error"])
        self.assertNotIn("completado", estados)
        error = next(cambio["ultimo_error"] for cambio in progreso if "ultimo_error" in cambio)
        self.assertNotIn("Paciente", error)
        self.assertNotIn("56912345678", error)
        self.assertNotIn("secreto", error)
        self.assertEqual(len(paginas_guardadas), 1)
        self.assertEqual(paginas_guardadas[0][3][0][0]["uuid"], "cliente-sintetico")


if __name__ == "__main__":
    unittest.main()
