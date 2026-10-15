"""Copia respuestas históricas aún identificables al corpus de texto íntegro.

Ejecutar una vez, después de reiniciar SNW: python backend/migrar_corpus_completo.py
No recupera palabras [otro] del corpus legado ni mensajes ya eliminados.
"""

from corpus_respuestas import guardar_mensaje_completo
from db import conectar
from telefono import normalizar_telefono
from whatsapp_service import es_solicitud_eliminar


def migrar() -> int:
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("SELECT valor FROM configuracion WHERE clave = %s FOR UPDATE",
                    ("corpus_respuestas_migrado_v2",))
        if cur.fetchone():
            return 0
        cur.execute("SELECT valor FROM configuracion WHERE clave = %s",
                    ("corpus_respuestas_corte_v2",))
        fila = cur.fetchone()
        if not fila:
            raise RuntimeError("Reinicia SNW antes de importar el corpus completo.")
        limite = int(fila["valor"])
        ultimo = 0
        total = 0
        while ultimo < limite:
            cur.execute(
                "SELECT id, mensaje, numero_telefono FROM log_envios"
                " WHERE id > %s AND id <= %s AND plantilla_clave = 'respuesta'"
                " AND mensaje IS NOT NULL AND mensaje <> ''"
                " ORDER BY id LIMIT 500 FOR UPDATE", (ultimo, limite),
            )
            filas = cur.fetchall()
            if not filas:
                break
            for fila in filas:
                tel = normalizar_telefono(fila.get("numero_telefono") or "")
                if not tel or es_solicitud_eliminar(fila["mensaje"]):
                    continue
                guardar_mensaje_completo(cur, fila["mensaje"], tel)
                total += 1
            ultimo = filas[-1]["id"]
        cur.execute(
            "INSERT INTO configuracion (clave, valor) VALUES (%s, '1')",
            ("corpus_respuestas_migrado_v2",),
        )
        conn.commit()
    return total


if __name__ == "__main__":
    print(f"Respuestas históricas incorporadas al corpus completo: {migrar()}")
