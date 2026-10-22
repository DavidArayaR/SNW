import sys
import datetime
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import main
import whatsapp_service


class CursorFalso:
    def __init__(self, filas=None):
        self.consultas = []
        self.respuestas = iter(({}, {"n": 0}, {"n": 0}, {"n": 0}, {"n": 0, "ult": None}))
        self.filas = iter(filas or ())

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def cursor(self):
        return self

    def execute(self, sql, parametros=None):
        # PyMySQL interpola con % cuando recibe parámetros: esto reproduce
        # el fallo que causaba DATE_FORMAT(..., '%Y-%m-01') al elegir área.
        if parametros:
            sql % tuple(repr(valor) for valor in parametros)
        self.consultas.append((sql, parametros))

    def fetchone(self):
        return next(self.respuestas)

    def fetchall(self):
        return next(self.filas)


class EstadisticasAreaTests(unittest.TestCase):
    def test_selector_estadisticas_oculta_clinicas_sin_campanas_enviadas(self):
        cursor = CursorFalso(filas=([{"centro_codigo": "SMPT"}],))
        canales = [{"codigo": "SMPT", "nombre": "Salud Mental Para Todos"},
                   {"codigo": "CABC1234", "nombre": "asd"},
                   {"codigo": "CDEF5678", "nombre": "Mas Vida y Salud"}]
        with patch.object(main, "config_all", return_value={}), \
             patch.object(main, "listar_canales", return_value=canales) as listar, \
             patch.object(main, "conectar", return_value=cursor):
            visibles = main.listar_centros_whatsapp(incluir_inactivas=True,
                                                    solo_con_envios=True, sesion={})
        self.assertEqual([c["codigo"] for c in visibles], ["SMPT"])
        listar.assert_called_once_with({}, incluir_inactivas=True)
        self.assertIn("envio_id IS NOT NULL", cursor.consultas[0][0])
        self.assertIn("estado_envio = 'enviado'", cursor.consultas[0][0])

    def test_resumen_filtra_clinica_inactiva_y_todas_no_filtran(self):
        catalogo = {"SMPT": "Salud Mental Para Todos"}
        conexiones = []
        def nueva_conexion():
            cursor = CursorFalso()
            conexiones.append(cursor)
            return cursor
        with patch.object(main, "catalogo_centros", return_value=catalogo), \
             patch.object(main, "conectar", side_effect=nueva_conexion), \
             patch.object(main.servicio_areas, "listar_tablas_areas", return_value=[]), \
             patch.object(main, "_pacientes_por_respuesta", return_value={"total": 0}), \
             patch.object(main, "_pacientes_por_interes", return_value={"total": 0}):
            filtrado = main.estadisticas(area_id=None, base="produccion", centro="SMPT", sesion={})
            total = main.estadisticas(area_id=None, base="produccion", centro="todos", sesion={})
        self.assertEqual(filtrado["centro"], "SMPT")
        self.assertEqual(total["centro"], "todos")
        self.assertIn("centro_codigo = %s", conexiones[0].consultas[0][0])
        self.assertEqual(conexiones[0].consultas[0][1], ("%Y-%m-01", "SMPT"))
        self.assertNotIn("centro_codigo = %s", conexiones[1].consultas[0][0])

    def test_calendario_por_clinica_no_mezcla_fechas(self):
        cursor = CursorFalso(filas=(
            [{"dia": "2026-10-03"}],
            [{"periodo": "2026-10", "enviados": 2}],
        ))
        with patch.object(main, "catalogo_centros", return_value={"SMPT": "SMPT"}), \
             patch.object(main, "conectar", return_value=cursor):
            resultado = main.estadisticas_envios(granularidad="mes", area_id=None,
                                                  base="produccion", centro="SMPT",
                                                  periodos=None, sesion={})
        self.assertEqual(resultado["dias_disponibles"], ["2026-10-03"])
        self.assertEqual(resultado["total"], 2)
        self.assertTrue(all("centro_codigo = %s" in sql for sql, _ in cursor.consultas))
        self.assertEqual(cursor.consultas[0][1], ("%Y-%m-%d", "SMPT"))

    def test_respuestas_de_todas_las_clinicas_incluyen_bases_de_area(self):
        areas = [{"nombre_tabla_base": "pacientes_psicologia"},
                 {"nombre_tabla_base": "pacientes_psiquiatria"}]
        respuestas = [
            {"pendiente": 1, "respondio": 2, "baja": 0, "total": 3},
            {"pendiente": 0, "respondio": 3, "baja": 1, "total": 4},
            {"pendiente": 2, "respondio": 0, "baja": 0, "total": 2},
        ]
        interes = [
            {"interesado": 1, "no_interesado": 1, "sin_clasificar": 1, "total": 3},
            {"interesado": 3, "no_interesado": 0, "sin_clasificar": 1, "total": 4},
            {"interesado": 0, "no_interesado": 0, "sin_clasificar": 2, "total": 2},
        ]
        with patch.object(main.servicio_areas, "listar_tablas_areas", return_value=areas), \
             patch.object(main.servicio_areas, "tabla_valida", return_value=True), \
             patch.object(main, "_pacientes_por_respuesta", side_effect=respuestas) as por_respuesta, \
             patch.object(main, "_pacientes_por_interes", side_effect=interes):
            r, i = main._resumen_pacientes_estadisticas("produccion", None, "SMPT")
        self.assertEqual(r, {"pendiente": 3, "respondio": 5, "baja": 1, "total": 9})
        self.assertEqual(i, {"interesado": 4, "no_interesado": 1,
                             "sin_clasificar": 4, "total": 9})
        self.assertEqual([c.kwargs["tabla"] for c in por_respuesta.call_args_list],
                         [None, "pacientes_psicologia", "pacientes_psiquiatria"])
        self.assertTrue(all(c.kwargs["centro_codigo"] == "SMPT"
                            for c in por_respuesta.call_args_list))

    def test_desarrollo_no_se_combina_con_area(self):
        with self.assertRaises(main.HTTPException) as error:
            main._alcance_estadisticas({}, 7, "desarrollo")
        self.assertEqual(error.exception.status_code, 400)

    def test_respuestas_de_pacientes_dev_no_usan_logs_de_produccion_con_igual_id(self):
        cursor = CursorFalso(filas=([],))
        with patch.object(main, "conectar", return_value=cursor), \
             patch.object(main, "columna_existe", return_value=False), \
             patch.object(main, "columnas_tabla", return_value={"tabla_pacientes"}):
            main._pacientes_por_respuesta("desarrollo")
        self.assertIn("le.tabla_pacientes = 'pacientes_dev'", cursor.consultas[0][0])

    def test_resumen_desarrollo_cuenta_solo_sus_lotes_y_respuestas(self):
        cursor = CursorFalso()
        with patch.object(main, "conectar", return_value=cursor), \
             patch.object(main, "_pacientes_por_respuesta", return_value={"total": 2}) as respuestas, \
             patch.object(main, "_pacientes_por_interes", return_value={"total": 2}) as interes:
            resultado = main.estadisticas(area_id=None, base="desarrollo", sesion={})

        self.assertEqual(resultado["base"], "desarrollo")
        self.assertIn("base_datos = 'pacientes_dev'", cursor.consultas[0][0])
        self.assertIn("tabla_pacientes = 'pacientes_dev'", cursor.consultas[2][0])
        self.assertIn("base_datos = 'pacientes_dev'", cursor.consultas[3][0])
        respuestas.assert_called_once_with("desarrollo", tabla=None, centro_codigo=None)
        interes.assert_called_once_with("desarrollo", tabla=None, centro_codigo=None)

    def test_calendario_desarrollo_solo_tiene_sus_envios(self):
        cursor = CursorFalso(filas=(
            [{"dia": "2026-10-08"}],
            [{"periodo": "2026-10", "enviados": 1}],
        ))
        with patch.object(main, "conectar", return_value=cursor):
            resultado = main.estadisticas_envios(granularidad="mes", area_id=None,
                                                  base="desarrollo", periodos=None, sesion={})
        self.assertEqual(resultado["dias_disponibles"], ["2026-10-08"])
        self.assertEqual(resultado["total"], 1)
        self.assertTrue(all("base_datos = 'pacientes_dev'" in sql
                            for sql, _ in cursor.consultas))

    def test_costos_desarrollo_no_mezclan_simulados_y_comparten_cupo_servicio(self):
        cursor = CursorFalso(filas=(
            [],
            [{"id": 1, "base_lote": "pacientes_prod", "tabla_pacientes": "pacientes_prod",
              "wa_phone_id": "empresa", "area_id": None, "envio_id": 10,
              "fecha_entrega": datetime.datetime(2026, 10, 8)},
             {"id": 2, "base_lote": "pacientes_dev", "tabla_pacientes": "pacientes_dev",
              "wa_phone_id": "empresa", "area_id": None, "envio_id": 11,
              "fecha_entrega": datetime.datetime(2026, 10, 8)}],
        ))
        with patch.object(main, "conectar", return_value=cursor), \
             patch.object(main, "_moneda_cuenta", return_value="USD"), \
             patch.object(main, "_tarifas_guardadas", return_value=[]), \
             patch.object(main, "_tarifa_vigente", return_value=None), \
             patch.object(main, "_categorias_por_clave", return_value={"baja_aviso": "service"}), \
             patch.object(main, "columnas_tabla", return_value={"whatsapp_message_id"}), \
             patch.object(main, "clasificar_entregas_servicio",
                          side_effect=lambda filas: [(fila, True) for fila in filas]):
            resultado = main.estadisticas_costos(granularidad="mes", area_id=None,
                                                  base="desarrollo", categoria=None,
                                                  fecha=None, periodos=None, sesion={})
        self.assertEqual(resultado["base"], "desarrollo")
        self.assertEqual(resultado["total"]["mensajes"], 1)
        self.assertEqual(resultado["servicio"]["entregados"], 1)
        self.assertIn("base_datos = 'pacientes_dev'", cursor.consultas[0][0])
        self.assertIn("base_lote", cursor.consultas[1][0])
        self.assertNotIn("base_datos = 'pacientes_dev'", cursor.consultas[1][0])

    def test_costos_filtran_clinica_sin_descontar_su_cupo_compartido(self):
        cursor = CursorFalso(filas=(
            [],
            [{"id": 1, "base_lote": "pacientes_prod", "tabla_pacientes": "pacientes_prod",
              "centro_codigo": "SMPT", "wa_phone_id": "emisor-a", "area_id": None,
              "envio_id": 10, "fecha_entrega": datetime.datetime(2026, 10, 8)},
             {"id": 2, "base_lote": "pacientes_prod", "tabla_pacientes": "pacientes_prod",
              "centro_codigo": "MS", "wa_phone_id": "emisor-b", "area_id": None,
              "envio_id": 11, "fecha_entrega": datetime.datetime(2026, 10, 8)}],
        ))
        with patch.object(main, "catalogo_centros", return_value={"SMPT": "SMPT"}), \
             patch.object(main, "conectar", return_value=cursor), \
             patch.object(main, "_moneda_cuenta", return_value="USD"), \
             patch.object(main, "_tarifas_guardadas", return_value=[]), \
             patch.object(main, "_tarifa_vigente", return_value=None), \
             patch.object(main, "_categorias_por_clave", return_value={"baja_aviso": "service"}), \
             patch.object(main, "columnas_tabla", return_value={"whatsapp_message_id"}), \
             patch.object(main, "clasificar_entregas_servicio",
                          side_effect=lambda filas: [(fila, True) for fila in filas]):
            resultado = main.estadisticas_costos(granularidad="mes", area_id=None,
                                                  base="produccion", centro="SMPT",
                                                  categoria=None, fecha=None, periodos=None,
                                                  sesion={})
        self.assertEqual(resultado["total"]["mensajes"], 1)
        self.assertIn("centro_codigo = %s", cursor.consultas[0][0])
        self.assertNotIn("centro_codigo = %s", cursor.consultas[1][0])

    def test_resumen_con_area_no_interpreta_formato_fecha_como_parametro(self):
        cursor = CursorFalso()
        area = {"id": 7, "nombre_visible": "Área 7", "nombre_tabla_base": "pacientes_7"}
        with patch.object(main, "conectar", return_value=cursor), \
             patch.object(main, "_exigir_area", return_value=area), \
             patch.object(main, "columnas_tabla", return_value={"interesado", "interes_fecha"}), \
             patch.object(main, "_pacientes_por_respuesta", return_value={"total": 0}), \
             patch.object(main, "_pacientes_por_interes", return_value={"total": 0}):
            resultado = main.estadisticas(area_id=7, sesion={})

        self.assertEqual(cursor.consultas[0][1], ("%Y-%m-01", 7, 7))
        self.assertIn("area_id = %s OR (area_id IS NULL", cursor.consultas[0][0])
        self.assertIn("plantilla_clave = 'respuesta'", cursor.consultas[2][0])
        self.assertIn("COLLATE utf8mb4_unicode_ci FROM pacientes_7", cursor.consultas[2][0])
        self.assertEqual(cursor.consultas[2][1], ("%Y-%m-01", 7, 7, "%Y-%m-01"))
        self.assertEqual(resultado["area"]["id"], 7)

    def test_calendario_solo_ofrece_dias_con_envios_del_area(self):
        cursor = CursorFalso(filas=(
            [{"dia": "2026-10-03"}, {"dia": "2026-10-05"}, {"dia": "2027-01-01"}],
            [{"periodo": "2026-10", "enviados": 2}],
        ))
        area = {"id": 7, "nombre_visible": "Área 7"}
        with patch.object(main, "conectar", return_value=cursor), \
             patch.object(main, "_exigir_area", return_value=area):
            resultado = main.estadisticas_envios(granularidad="mes", area_id=7,
                                                  periodos=None, sesion={})

        self.assertEqual(resultado["dias_disponibles"],
                         ["2026-10-03", "2026-10-05", "2027-01-01"])
        self.assertEqual(resultado["meses_disponibles"], ["2026-10", "2027-01"])
        self.assertEqual(resultado["anios_disponibles"], [2026, 2027])
        self.assertEqual(cursor.consultas[0][1], ("%Y-%m-%d", 7, 7))

    def test_respuesta_de_interes_antigua_cuenta_como_respondida(self):
        expresion = main.expr_respuesta_efectiva(
            "p", tabla_log="pacientes_test", tiene_interes=True)
        self.assertIn("le.tabla_pacientes = 'pacientes_test'", expresion)
        self.assertIn("p.interesado, 0) = 1", expresion)
        self.assertIn("p.no_interesado, 0) = 1", expresion)
        self.assertLess(expresion.index("le.respuesta = 'baja'"),
                        expresion.index("p.interesado, 0) = 1"))

    def test_no_interesado_se_distingue_en_vistas_sin_alterar_estadisticas(self):
        vista = main.expr_respuesta_efectiva(
            "p", tiene_interes=True, mostrar_no_interesado=True)
        estadisticas = main.expr_respuesta_efectiva("p", tiene_interes=True)
        self.assertIn("THEN 'no_interesado'", vista)
        self.assertIn("p.interesado, 0) = 0", vista)
        self.assertLess(vista.index("le.respuesta = 'baja'"),
                        vista.index("THEN 'no_interesado'"))
        self.assertLess(vista.index("THEN 'no_interesado'"),
                        vista.index("le.respuesta = 'respondio'"))
        self.assertNotIn("THEN 'no_interesado'", estadisticas)
        self.assertIn("p.no_interesado, 0) = 1 THEN 'respondio'", estadisticas)

    def test_webhook_registra_respuesta_en_cada_base(self):
        class ConexionFalsa:
            def __init__(self):
                self.consultas = []
                self.commits = 0
                self.lastrowid = 0

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def cursor(self):
                return self

            def execute(self, sql, parametros=None):
                self.consultas.append((sql, parametros))
                if sql.startswith("INSERT INTO log_envios"):
                    self.lastrowid += 1

            def commit(self):
                self.commits += 1

        conexion = ConexionFalsa()
        pacientes = [
            {"id": 1, "nombre": "A", "telefono": "test", "tabla": "pacientes_prod"},
            {"id": 2, "nombre": "B", "telefono": "test", "tabla": "pacientes_test"},
        ]
        servicio = whatsapp_service.WhatsAppService()
        with patch.object(whatsapp_service, "conectar", return_value=conexion), \
             patch.object(servicio, "_pacientes_con_tel", return_value=pacientes), \
             patch.object(whatsapp_service.servicio_areas, "area_por_tabla",
                          side_effect=lambda tabla: {"id": 9} if tabla == "pacientes_test" else None):
            resultado = servicio._registrar_respuesta("test", "hola", "2026-10-07")

        registros = [params for sql, params in conexion.consultas if sql.startswith("INSERT INTO log_envios")]
        self.assertEqual(resultado, "registrada")
        self.assertEqual(conexion.commits, 1)
        self.assertEqual([(r[0], r[6], r[7]) for r in registros],
                         [(1, None, "pacientes_prod"), (2, 9, "pacientes_test")])
        etiquetas = [params for sql, params in conexion.consultas
                     if sql.startswith("INSERT IGNORE INTO respuesta_etiquetas")]
        self.assertEqual([r[0] for r in etiquetas], [1, 2])


if __name__ == "__main__":
    unittest.main()
