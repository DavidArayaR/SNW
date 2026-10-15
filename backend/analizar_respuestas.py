"""Análisis local de expresiones entrantes, sin imprimir mensajes ni PII.

Ejecutar desde la raíz: python backend/analizar_respuestas.py
Solo lee las últimas 5000 respuestas; ninguna propuesta se activa sola.
"""

from collections import Counter

from db import conectar
from intenciones_call_center import clasificar, normalizar

_CANDIDATAS = (
    "tienen hora", "hay hora", "quedan cupos", "hora para manana",
    "me pueden llamar", "quiero hablar", "mas informacion", "mas info",
    "cuanto cuesta", "cual es el valor", "que especialidades", "una consulta",
    "puedo agendar", "tienen psicologo", "tienen psiquiatra", "necesito ayuda",
)


def analizar() -> dict:
    resumen = Counter()
    candidatas = Counter()
    vocabulario = Counter()
    with conectar() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT mensaje FROM log_envios WHERE plantilla_clave = %s"
            " ORDER BY id DESC LIMIT 5000", ("respuesta",))
        for fila in cur.fetchall():
            texto = fila.get("mensaje") or ""
            if not texto:
                continue
            resultado = clasificar(texto)
            resumen["total"] += 1
            if resultado.motivos:
                for motivo in resultado.motivos:
                    resumen[motivo] += 1
            else:
                resumen["sin_clasificar"] += 1
                limpio = normalizar(texto)
                for frase in _CANDIDATAS:
                    if frase in limpio:
                        candidatas[frase] += 1
            limpio = normalizar(texto)
            for palabra in ("hola", "consulta", "hora", "horas", "cupo", "doctor",
                            "doctora", "medico", "precio", "valor", "informacion",
                            "llamar", "agendar", "confirmo", "interesa", "gracias"):
                if palabra in limpio.split():
                    vocabulario[palabra] += 1
    # Vocabulario cerrado y umbral: no se muestran frases libres de pacientes.
    return {"resumen": dict(resumen),
            "candidatas_para_revision": {k: v for k, v in candidatas.items() if v >= 3},
            "vocabulario_seguro": {k: v for k, v in vocabulario.items() if v >= 3}}


if __name__ == "__main__":
    import json
    print(json.dumps(analizar(), ensure_ascii=False, indent=2))
