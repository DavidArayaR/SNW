"""Campañas: atribución temporal y por emisor, audios y reintentos."""
import datetime as dt
import sys
import unittest
import uuid
import copy
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import main
import estadisticas_envio as stats
from db import conectar


class EstadisticasEnvioTests(unittest.TestCase):
    def test_envio_sin_clicks_admite_botones_con_cero_resultados_mysql_tupla(self):
        conn = MagicMock()
        cur = conn.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = {"id": 204, "plantilla_clave": "prueba", "plantilla_nombre": "Prueba",
            "total_pacientes": 10, "centro_codigo": "VS", "fecha_hora": None, "inicio_envio": None}
        cur.fetchall.return_value = ()  # PyMySQL devuelve tupla cuando no hay filas.
        plantilla = {"clave": "prueba", "centro_codigo": "VS", "botones": [
            {"texto": "Me interesa", "accion": "interes", "tipo": "respuesta"},
            {"texto": "Dar de baja", "accion": "baja", "tipo": "respuesta"}]}
        with patch.object(main, "conectar", return_value=conn), \
             patch.object(main.reagendar_batches, "resolver_envio", side_effect=lambda i: i), \
             patch.object(main, "leer_plantillas", return_value=[plantilla]), \
             patch.object(main, "catalogo_centros", return_value={"VS": "Clínica"}), \
             patch.object(main, "_moneda_cuenta", return_value="CLP"), \
             patch.object(main, "_tarifas_guardadas", return_value=[]), \
             patch.object(main, "_categorias_por_clave", return_value={}):
            resultado = main.obtener_estadisticas_envio(204, sesion={"rol": "desarrollador"})
        self.assertIsInstance(resultado["botones"], list)
        self.assertEqual([b["clics"] for b in resultado["botones"]], [0, 0])
        self.assertEqual(resultado["resumen"]["validos"], 0)

    def test_costo_usa_entregas_confirmadas_y_tarifa_historica_sin_inventar_ceros(self):
        conn = MagicMock()
        cur = conn.__enter__.return_value.cursor.return_value.__enter__.return_value
        fecha = dt.datetime(2026, 10, 31, 10)
        cur.fetchone.return_value = {"id": 7, "plantilla_clave": "prueba", "plantilla_nombre": "Prueba",
            "total_pacientes": 2, "centro_codigo": "VS", "fecha_hora": fecha, "inicio_envio": fecha}
        celda = {"validos": True, "accion": "interes", "boton": "Me interesa", "pacientes": 2,
                 "facturables": 2, "fecha": fecha.date(), "clave": "prueba", "estado_whatsapp": "read", "respondio": True}
        datos = {"resumen": {"validos": 2}, "botones": [], "decisiones": [celda],
                 "segmentos": [celda], "entregas_coste": []}
        for tarifas, importe, guardado in (([{"efectiva_desde": "2026-01-01", "marketing": 10, "moneda": "CLP"}], 20, None),
                                           ([], None, None), ([], 15, 7.5),
                                           ([{"marketing": 999, "moneda": "CLP"}], 0, 0)):
            datos_precio = copy.deepcopy(datos)
            if guardado is not None:
                datos_precio["segmentos"][0].update(precio_unitario=guardado, precio_moneda="CLP")
            with patch.object(main, "conectar", return_value=conn), \
                 patch.object(main.reagendar_batches, "resolver_envio", side_effect=lambda i: i), \
                 patch.object(stats, "resumen", return_value=datos_precio), \
                 patch.object(main, "leer_plantillas", return_value=[]), \
                 patch.object(main, "catalogo_centros", return_value={"VS": "Clínica"}), \
                 patch.object(main, "_moneda_cuenta", return_value="CLP"), \
                 patch.object(main, "_tarifas_guardadas", return_value=tarifas), \
                 patch.object(main, "_categorias_por_clave", return_value={"prueba": "marketing"}):
                resultado = main.obtener_estadisticas_envio(7, sesion={"rol": "desarrollador"})
            self.assertEqual(resultado["costo"]["importe"], importe)
            self.assertEqual(resultado["segmentos"][0]["costo"], importe)
            self.assertEqual(resultado["envio"]["inicio"], "31-10-2026 10:00")

    def test_porcentajes_sobre_enviados_sin_error_no_solo_clasificados(self):
        datos = {"resumen": {"validos": 100}, "botones": [], "decisiones": [
            {"accion": "interes", "pacientes": 10}, {"accion": "desinteres", "pacientes": 20},
            {"accion": "baja", "pacientes": 5}, {"accion": "consulta", "pacientes": 8}]}
        stats.clasificar_decisiones(datos, lambda fila: fila["accion"])
        self.assertEqual(datos["interacciones"]["interes"]["porcentaje"], 10)
        self.assertEqual(datos["interacciones"]["desinteres"]["porcentaje"], 20)
        self.assertEqual(datos["interacciones"]["baja"]["porcentaje"], 5)
        self.assertEqual(datos["resumen"]["tasa_interes"], 10)
        self.assertEqual(datos["resumen"]["sin_seleccion"], 65)

    def test_sin_intentos_no_hay_division_por_cero_ni_datos_de_pacientes(self):
        cur = MagicMock()
        cur.fetchone.return_value = dict(intentados=0, entregados=0, leidos=0, errores=0,
                                        invalidos=0, sin_confirmacion=0, respuestas=0)
        cur.fetchall.return_value = []
        resultado = stats.resumen(cur, {"id": 7, "total_pacientes": 10})
        self.assertEqual(resultado["resumen"]["sin_intento"], 10)
        self.assertEqual(resultado["resumen"]["tasa_respuesta"], 0)
        self.assertNotIn("telefono", str(resultado))

    def test_listado_acotado_filtra_entorno_area_clinica_y_busqueda(self):
        conn = MagicMock()
        cur = conn.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = {"n": 51}
        cur.fetchall.return_value = [{"id": i, "fecha_hora": None} for i in range(26)]
        cur.fetchall.return_value[0]["base_datos"] = "paciente_reagendar"
        cur.fetchall.return_value[1]["programado_id"] = 9
        with patch.object(main, "conectar", return_value=conn), \
             patch.object(main, "_alcance_estadisticas"), \
             patch.object(main, "catalogo_centros", return_value={"VS": "Clínica"}):
            resultado = main.listar_envios_estadisticas(base="produccion", area_id=4,
                centro="VS", pagina=2, q="promocion", sesion={"rol": "desarrollador"})
        self.assertEqual(len(resultado["items"]), 25)
        self.assertTrue(resultado["hay_mas"])
        self.assertEqual([f["tipo"] for f in resultado["items"][:3]],
                         ["reagendamiento", "programado", "manual"])
        sql, args = cur.execute.call_args.args
        self.assertIn("e.area_id=%s", sql)
        self.assertIn("e.centro_codigo=%s", sql)
        self.assertIn("reagendar_envios_agrupados", sql)
        self.assertEqual(args, (4, 4, "VS", "%promocion%", "promocion", 25))

    def test_rutas_exigen_la_misma_autorizacion_que_estadisticas(self):
        import inspect
        for funcion in (main.listar_envios_estadisticas, main.obtener_estadisticas_envio):
            dependencia = inspect.signature(funcion).parameters["sesion"].default
            self.assertIs(dependencia.dependency, main.solo_admin)


class EstadisticasEnvioMySQLTests(unittest.TestCase):
    def test_entregas_respuestas_y_botones_sin_mezclar_otras_campanas(self):
        try:
            conn = conectar()
        except Exception:
            self.skipTest("MySQL no disponible")
        prefijo = uuid.uuid4().hex
        phone = "stats-" + prefijo
        telefonos = ["+" + str(int(prefijo[:10], 16)) + str(i) for i in range(5)]
        fecha = dt.datetime(2026, 10, 31, 10)
        try:
            with conn.cursor() as cur:
                cur.execute("SET SESSION sql_mode=CONCAT(@@sql_mode,',ONLY_FULL_GROUP_BY')")
                def envio(total):
                    cur.execute("INSERT INTO envios (base_datos,plantilla_clave,plantilla_nombre,"
                                "total_pacientes,estado,fecha_hora)"
                                " VALUES ('pacientes_prod','stats_test','Prueba',%s,'completado',%s)", (total, fecha))
                    return cur.lastrowid

                a, b = envio(6), envio(1)

                def salida(batch, indice, estado, whatsapp, minutos=0):
                    cur.execute("INSERT INTO log_envios (envio_id,nombre_paciente,numero_telefono,"
                                "estado_envio,estado_whatsapp,wa_phone_id,fecha_hora)"
                                " VALUES (%s,'Prueba',%s,%s,%s,%s,%s)",
                                (batch, telefonos[indice], estado, whatsapp, phone,
                                 fecha + dt.timedelta(minutes=minutos)))

                def entrada(indice, tipo, minutos, emisor=None, texto=None, accion=None):
                    cur.execute("INSERT INTO whatsapp_conversaciones_mensajes"
                                " (mensaje_id,centro_codigo,wa_phone_id,numero_emisor,telefono_paciente,"
                                "tipo,texto,recibido,accion_boton) VALUES (%s,'VS',%s,'+56900000000',%s,%s,%s,%s,%s)",
                                (uuid.uuid4().hex, emisor or phone, telefonos[indice], tipo,
                                 texto if texto is not None else "Me interesa" if tipo == "button" else "",
                                 fecha + dt.timedelta(minutes=minutos), accion))

                salida(a, 0, "error", "failed", -5)  # Intento anterior del mismo batch.
                for i, (estado, whatsapp) in enumerate([
                        ("enviado", "read"), ("enviado", "delivered"), ("error", "failed"),
                        ("numero_invalido", None), ("enviado", "sent")]):
                    salida(a, i, estado, whatsapp)
                entrada(0, "audio", 1)
                entrada(1, "button", 2)
                entrada(1, "button", 3)
                entrada(4, "audio", -2)  # Antes del envío.
                entrada(4, "audio", 4, "otra-" + prefijo)  # Otra clínica.
                salida(b, 2, "enviado", "read", 60)
                entrada(2, "audio", 61)  # Corresponde al envío siguiente.
                datos = stats.resumen(cur, {"id": a, "total_pacientes": 6})
                r = datos["resumen"]
                self.assertEqual((r["intentados"], r["entregados"], r["leidos"]), (5, 2, 1))
                self.assertEqual((r["errores"], r["invalidos"], r["sin_confirmacion"]), (1, 1, 1))
                self.assertEqual((r["respuestas"], r["sin_respuesta"], r["sin_intento"]), (2, 3, 1))
                self.assertEqual(r["tasa_respuesta"], 40)
                self.assertEqual([{k: b[k] for k in ("boton", "clics", "pacientes")} for b in datos["botones"]],
                                 [{"boton": "Me interesa", "clics": 2, "pacientes": 1}])
                self.assertEqual(r["validos"], 3)  # Error e inválido fuera del denominador.
                self.assertEqual(r["vistos_sin_respuesta"], 0)  # El audio evita contar «en visto».
                stats.clasificar_decisiones(datos, lambda fila: "interes" if fila["boton"] == "Me interesa" else "otro")
                self.assertEqual(datos["resumen"]["tasa_interes"], 33.3)
                self.assertEqual(stats.resumen(cur, {"id": b, "total_pacientes": 1})["resumen"]["respuestas"], 1)
                entrada(1, "button", 8, texto="No deseo más avisos", accion="baja")
                entrada(2, "button", 9, accion="interes")  # Falló su envío: no entra al denominador ni al interés.
                nuevo = stats.resumen(cur, {"id": a, "total_pacientes": 6})
                stats.clasificar_decisiones(nuevo, lambda fila: fila.get("accion") or
                    ("interes" if fila.get("boton") == "Me interesa" else "otro"))
                self.assertEqual(nuevo["interacciones"]["baja"]["pacientes"], 1)
                self.assertEqual(nuevo["interacciones"]["interes"]["pacientes"], 0)
                self.assertEqual(nuevo["resumen"]["validos"], 3)
                cur.execute("UPDATE log_envios SET estado_whatsapp='read' WHERE envio_id=%s AND numero_telefono=%s",
                            (a, telefonos[4]))
                self.assertEqual(stats.resumen(cur, {"id": a, "total_pacientes": 6})["resumen"]["vistos_sin_respuesta"], 1)
                # Compatibilidad: respuesta histórica sin copia en tabla de conversaciones.
                cur.execute("INSERT INTO log_envios (nombre_paciente,numero_telefono,wa_phone_id,"
                            "plantilla_clave,estado_envio,respuesta,fecha_hora)"
                            " VALUES ('Prueba',%s,%s,'respuesta','enviado','respondio',%s)",
                            (telefonos[4], phone, fecha + dt.timedelta(minutes=5)))
                resumen = stats.resumen(cur, {"id": a, "total_pacientes": 6})["resumen"]
                self.assertEqual(resumen["vistos_sin_respuesta"], 0)
        finally:
            conn.rollback()  # Solo datos sintéticos, nunca envíos ni datos reales.
            conn.close()


if __name__ == "__main__":
    unittest.main()
