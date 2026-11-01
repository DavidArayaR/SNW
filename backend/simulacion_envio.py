"""Campaña de demostración persistente: solo agregados, sin pacientes ni Meta."""
import datetime as dt
import json

from db import conectar


def datos_ejemplo(envio_id, fecha):
    segmentos = []
    for cantidad, resultado, entrega, visto in (
        (75, "interes", "leidos", False), (15, "interes", "entregados", False),
        (35, "desinteres", "leidos", False), (10, "desinteres", "entregados", False),
        (10, "baja", "leidos", False), (5, "baja", "entregados", False),
        (80, "sin_seleccion", "leidos", True), (20, "sin_seleccion", "leidos", False),
        (20, "sin_seleccion", "entregados", False), (30, "sin_seleccion", "sin_confirmacion", False)):
        facturables = cantidad if entrega != "sin_confirmacion" else 0
        segmentos.append(dict(cantidad=cantidad, resultado=resultado, entrega=entrega,
            visto_sin_respuesta=visto, facturables=facturables, costo=facturables * 60))
    botones = []
    for texto, accion, resultado, entrega, pacientes, clics in (
        ("Me interesa", "interes", "interes", "read", 75, 94),
        ("Me interesa", "interes", "interes", "delivered", 15, 20),
        ("Me interesa", "interes", "desinteres", "read", 10, 12),
        ("Me interesa", "interes", "desinteres", "delivered", 5, 6),
        ("No me interesa", "desinteres", "desinteres", "read", 35, 42),
        ("No me interesa", "desinteres", "desinteres", "delivered", 10, 11),
        ("Dar de baja", "baja", "baja", "read", 10, 12),
        ("Dar de baja", "baja", "baja", "delivered", 5, 6)):
        botones.append(dict(boton=texto, accion=accion, resultado=resultado, entrega=entrega,
                            pacientes=pacientes, clics=clics))
    return {"simulacion": True,
        "envio": {"id": envio_id, "plantilla_clave": "simulacion_350", "plantilla_nombre": "SIMULACIÓN · 350 mensajes",
            "centro": "Clínica simulada", "centro_codigo": None, "numero_emisor": "Sin número real",
            "base_datos": "simulacion", "total_pacientes": 350, "inicio": fecha.strftime("%d-%m-%Y %H:%M")},
        "resumen": dict(total=350, intentados=350, validos=300, entregados=270, leidos=220,
            errores=40, invalidos=10, vistos_sin_respuesta=80, sin_confirmacion=30,
            respuestas=170, sin_respuesta=180, sin_intento=0, clasificados=150, sin_seleccion=150, tasa_interes=30),
        "interacciones": {k: {"pacientes": n, "porcentaje": p} for k, n, p in
            (("interes", 90, 30), ("desinteres", 45, 15), ("baja", 15, 5))},
        "costo": {"importe": 16200, "moneda": "CLP", "entregas": 270, "por_interesado": 180},
        "segmentos": segmentos, "botones": botones}


def crear(solicitud_id: str, usuario: str):
    """Idempotente por solicitud. No modifica configuraciones ni datos de pacientes."""
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("INSERT IGNORE INTO envios_simulaciones (solicitud_id,usuario) VALUES (%s,%s)",
                    (solicitud_id, usuario))
        cur.execute("SELECT envio_id,usuario,datos_json FROM envios_simulaciones"
                    " WHERE solicitud_id=%s FOR UPDATE", (solicitud_id,))
        anterior = cur.fetchone()
        if anterior["usuario"] != usuario:
            raise ValueError("La solicitud pertenece a otra cuenta.")
        if anterior["envio_id"]:
            conn.commit()
            return json.loads(anterior["datos_json"])
        fecha = dt.datetime.now()
        cur.execute("INSERT INTO envios (base_datos,plantilla_clave,plantilla_nombre,total_pacientes,"
                    "enviados,fallidos,invalidos,estado,usuario,comentario,fecha_hora)"
                    " VALUES ('simulacion','simulacion_350','SIMULACIÓN · 350 mensajes',350,300,40,10,"
                    "'completado',%s,'Datos ficticios. Sin pacientes, mensajes reales ni cobros.',%s)", (usuario, fecha))
        datos = datos_ejemplo(cur.lastrowid, fecha)
        cur.execute("UPDATE envios_simulaciones SET envio_id=%s,datos_json=%s WHERE solicitud_id=%s",
                    (datos["envio"]["id"], json.dumps(datos, ensure_ascii=False), solicitud_id))
        conn.commit()
    return datos


def obtener(envio_id):
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("SELECT datos_json FROM envios_simulaciones WHERE envio_id=%s", (envio_id,))
        fila = cur.fetchone()
    return json.loads(fila["datos_json"]) if fila else None
