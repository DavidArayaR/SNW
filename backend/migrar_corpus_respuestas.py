"""Importa una sola vez respuestas históricas al corpus desidentificado.

Ejecutar desde la raíz: python backend/migrar_corpus_respuestas.py
No modifica log_envios ni copia identificadores al corpus.
"""

from corpus_respuestas import desidentificar_mensaje
from db import conectar
from whatsapp_service import es_solicitud_eliminar


def migrar() -> int:
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("SELECT valor FROM configuracion WHERE clave = %s FOR UPDATE",
                    ("corpus_respuestas_migrado_v1",))
        if cur.fetchone():
            return 0
        cur.execute("SELECT valor FROM configuracion WHERE clave = %s",
                    ("corpus_respuestas_corte_v1",))
        fila = cur.fetchone()
        if not fila:
            raise RuntimeError("Reinicia SNW para preparar el límite de importación del corpus.")
        limite = int(fila["valor"])
        ultimo = 0
        total = 0
        while ultimo < limite:
            cur.execute(
                "SELECT id, mensaje FROM log_envios"
                " WHERE id > %s AND id <= %s AND plantilla_clave = 'respuesta'"
                " AND mensaje IS NOT NULL AND mensaje <> ''"
                " ORDER BY id LIMIT 500", (ultimo, limite),
            )
            filas = cur.fetchall()
            if not filas:
                break
            for fila in filas:
                if es_solicitud_eliminar(fila["mensaje"]):
                    continue
                cur.execute(
                    "INSERT INTO corpus_respuestas (texto_desidentificado) VALUES (%s)",
                    (desidentificar_mensaje(fila["mensaje"]),),
                )
                total += 1
            ultimo = filas[-1]["id"]
        cur.execute(
            "INSERT INTO configuracion (clave, valor) VALUES (%s, '1')",
            ("corpus_respuestas_migrado_v1",),
        )
        conn.commit()
    return total


if __name__ == "__main__":
    print(f"Respuestas históricas incorporadas: {migrar()}")
