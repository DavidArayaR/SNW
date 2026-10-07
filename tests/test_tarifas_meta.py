import io
import sys
import unittest
import urllib.parse
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import main


def tarifario_xlsx_de_prueba():
    nombres = [
        "Cost per message in USD, effective October 1, 2026",
        "Market", "Currency", "Marketing", "Utility", "Authentication",
        "Service", "Chile", "$US",
    ]
    etiquetas = "".join(f"<si><t>{nombre}</t></si>" for nombre in nombres)
    cadenas = ("<sst xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\">"
               f"{etiquetas}</sst>")
    celdas = (
        '<row r="1"><c r="A1" t="s"><v>0</v></c></row>'
        '<row r="2">' + "".join(
            f'<c r="{col}2" t="s"><v>{indice}</v></c>'
            for col, indice in zip("ABCDEF", range(1, 7))) + '</row>'
        '<row r="3"><c r="A3" t="s"><v>7</v></c>'
        '<c r="B3" t="s"><v>8</v></c>'
        '<c r="C3"><v>0.0889</v></c><c r="D3"><v>0.0200</v></c>'
        '<c r="E3"><v>0.0200</v></c><c r="F3"><v>0.0200</v></c></row>'
    )
    hoja = ("<worksheet xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\">"
            f"<sheetData>{celdas}</sheetData></worksheet>")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archivo:
        archivo.writestr("xl/sharedStrings.xml", cadenas)
        archivo.writestr("xl/worksheets/sheet1.xml", hoja)
    return buffer.getvalue()


class TarifasMetaTests(unittest.TestCase):
    def test_xlsx_publicado_como_csv_se_convierte_en_tarifa_chilena(self):
        archivo = tarifario_xlsx_de_prueba()
        destino = "https://scontent.example/rates.csv?token=temporal"
        enlace = "https://l.facebook.com/l.php?u=" + urllib.parse.quote(destino, safe="") + "&amp;h=abc"
        respuesta = MagicMock()
        respuesta.text = f'<a href="{enlace}">Rate card</a>'
        cliente = MagicMock()
        cliente.__enter__.return_value.get.return_value = respuesta
        with patch.object(main.httpx, "Client", return_value=cliente), \
             patch.object(main, "_bajar_tarifarios", new=AsyncMock(return_value=[archivo])):
            tarifas = main._fetch_tarifas_meta()

        self.assertEqual(len(tarifas), 1)
        self.assertEqual(tarifas[0]["moneda"], "USD")
        self.assertEqual(tarifas[0]["efectiva_desde"], "2026-10-01")
        self.assertEqual(tarifas[0]["service"], 0.02)
        self.assertIn("Chile", tarifas[0]["csv_texto"])

    def test_502_sin_tarifas_se_registra_en_consola(self):
        with patch.object(main, "_fetch_tarifas_meta", return_value=[]), \
             patch.object(main, "log_error") as registrar:
            with self.assertRaises(HTTPException) as error:
                main.actualizar_tarifas(sesion={})
        self.assertEqual(error.exception.status_code, 502)
        registrar.assert_called_once()


if __name__ == "__main__":
    unittest.main()
