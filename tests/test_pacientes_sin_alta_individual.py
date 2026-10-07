import sys
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from main import app


class PacientesSinAltaIndividualTests(unittest.TestCase):
    def test_api_no_expone_alta_individual_y_mantiene_lectura_y_csv(self):
        rutas = app.openapi()["paths"]
        self.assertNotIn("post", rutas["/api/pacientes"])
        self.assertIn("get", rutas["/api/pacientes"])
        self.assertIn("post", rutas["/api/areas/{area_id}/pacientes/csv"])
        respuesta = TestClient(app).post(
            "/api/pacientes",
            json={"nombre": "Persona", "apellido": "Ejemplo", "telefono": "+56911111111"},
        )
        self.assertEqual(respuesta.status_code, 405)

    def test_pagina_no_ofrece_formulario_individual(self):
        pagina = (ROOT / "frontend" / "pacientes.html").read_text(encoding="utf-8")
        self.assertNotIn('id="formPacienteIndividual"', pagina)
        self.assertIn('id="csvArchivo"', pagina)


if __name__ == "__main__":
    unittest.main()
