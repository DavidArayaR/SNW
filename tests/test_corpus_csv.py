"""Exportación del corpus: bloques, texto íntegro y acceso restringido."""
import csv
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import corpus_respuestas as corpus
import main
from routes.configuracion import RUTAS


class CorpusCsvTests(unittest.TestCase):
    def conexion(self):
        conn = MagicMock()
        cur = conn.__enter__.return_value.cursor.return_value.__enter__.return_value
        return conn, cur

    def leer_csv(self, contenido):
        return list(csv.reader(io.StringIO(contenido.lstrip("\ufeff"), newline="")))

    def test_texto_completo_con_tildes_comillas_y_saltos_de_linea(self):
        conn, cur = self.conexion()
        texto = 'Hola, quisiera atención\ncon "psicóloga" 💙'
        cur.fetchone.return_value = {"ultimo": 1}
        cur.fetchall.return_value = [{"id": 1, "mensaje": texto}]
        with patch.object(corpus, "conectar", return_value=conn):
            contenido = "".join(corpus.preparar_csv("completo"))
        self.assertTrue(contenido.startswith("\ufeff"))
        self.assertEqual(self.leer_csv(contenido), [
            ["corpus", "mensaje", "tipo_respuesta"], ["completo", texto, ""]])
        sql = " ".join(c.args[0] for c in cur.execute.call_args_list)
        self.assertNotIn("corpus_respuestas_origen", sql)
        self.assertNotIn("telefono", sql)

    def test_ambos_corpus_por_bloques_y_con_limite_capturado(self):
        conn, cur = self.conexion()
        cur.fetchone.side_effect = [{"ultimo": 502}, {"ultimo": 3}]
        cur.fetchall.side_effect = [
            [{"id": n, "mensaje": "mensaje"} for n in range(1, 501)],
            [{"id": 502, "mensaje": "último"}],
            [{"id": 3, "mensaje": "hola [otro]"}],
        ]
        with patch.object(corpus, "conectar", return_value=conn):
            filas = self.leer_csv("".join(corpus.preparar_csv("ambos")))
        self.assertEqual(len(filas), 503)
        self.assertEqual(filas[-1], ["desidentificado", "hola [otro]", ""])
        lecturas = [c.args[1] for c in cur.execute.call_args_list if "LIMIT %s" in c.args[0]]
        self.assertEqual(lecturas, [(0, 502, 500), (500, 502, 500), (0, 3, 500)])

    def test_csv_vacio_y_origen_invalido(self):
        conn, cur = self.conexion()
        cur.fetchone.return_value = {"ultimo": 0}
        with patch.object(corpus, "conectar", return_value=conn):
            self.assertEqual(len(self.leer_csv("".join(corpus.preparar_csv("completo")))), 1)
        cur.fetchall.assert_not_called()
        with patch.object(corpus, "conectar") as conectar:
            with self.assertRaises(ValueError):
                corpus.preparar_csv("otra_tabla")
            conectar.assert_not_called()

    def test_neutraliza_formulas_al_abrir_csv(self):
        conn, cur = self.conexion()
        textos = ["=1+1", " +SUM(A1)", "-1", "@SUM(A1)", "\ttexto", "\ntexto"]
        cur.fetchone.return_value = {"ultimo": len(textos)}
        cur.fetchall.return_value = [{"id": i, "mensaje": t} for i, t in enumerate(textos, 1)]
        with patch.object(corpus, "conectar", return_value=conn):
            filas = self.leer_csv("".join(corpus.preparar_csv("completo")))
        self.assertEqual([f[1] for f in filas[1:]], ["'" + t for t in textos])

    def test_ruta_autorizacion_descarga_y_auditoria_sin_textos(self):
        app = FastAPI()
        ruta = next(r for r in RUTAS if r[2] == "descargar_corpus_csv")
        app.add_api_route(ruta[0], main.descargar_corpus_csv, methods=[ruta[1]])
        with TestClient(app) as cliente, \
             patch.object(corpus, "preparar_csv", return_value=iter(["\ufeffcorpus,mensaje,tipo_respuesta\r\n"])) as preparar, \
             patch.object(main, "auditoria_registrar") as auditar:
            def sin_sesion():
                raise HTTPException(401, "Inicia sesión")
            app.dependency_overrides[main.sesion_actual] = sin_sesion
            self.assertEqual(cliente.get(ruta[0]).status_code, 401)
            for rol in ("usuario", "supervisor", "administrador"):
                app.dependency_overrides[main.sesion_actual] = lambda rol=rol: {"rol": rol}
                self.assertEqual(cliente.get(ruta[0]).status_code, 403)
            preparar.assert_not_called()
            app.dependency_overrides[main.sesion_actual] = lambda: {"rol": "desarrollador", "usuario": "dev"}
            self.assertEqual(cliente.get(ruta[0] + "?origen=invalido").status_code, 422)
            preparar.assert_not_called()
            respuesta = cliente.get(ruta[0])
            self.assertEqual(respuesta.status_code, 200)
            self.assertIn("attachment", respuesta.headers["content-disposition"])
            self.assertIn("no-store", respuesta.headers["cache-control"])
            self.assertIn("text/csv", respuesta.headers["content-type"])
            self.assertTrue(respuesta.content.startswith(b"\xef\xbb\xbf"))
            auditar.assert_called_once_with("dev", "corpus_csv_exportado", "completo")


if __name__ == "__main__":
    unittest.main()
