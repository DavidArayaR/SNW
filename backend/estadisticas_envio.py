"""Resultados agregados por campaña, sin devolver datos de pacientes."""


def ventana_respuesta(campania="le", mensaje="cm", telefono="telefono_paciente", fecha="recibido"):
    return (
        f"{mensaje}.wa_phone_id=BINARY {campania}.wa_phone_id"
        f" AND {mensaje}.{telefono}=BINARY {campania}.numero_telefono"
        f" AND {mensaje}.{fecha}>={campania}.fecha_hora"
        " AND NOT EXISTS (SELECT 1 FROM log_envios siguiente"
        f" WHERE siguiente.wa_phone_id={campania}.wa_phone_id"
        f" AND siguiente.numero_telefono={campania}.numero_telefono"
        f" AND siguiente.envio_id IS NOT NULL AND siguiente.envio_id<>{campania}.envio_id"
        f" AND (siguiente.fecha_hora>{campania}.fecha_hora"
        f" OR (siguiente.fecha_hora={campania}.fecha_hora AND siguiente.id>{campania}.id))"
        f" AND siguiente.fecha_hora<={mensaje}.{fecha})"
    )


def resumen(cur, envio):
    # Los reintentos de un batch no multiplican sus destinatarios.
    ultimos = ("le.envio_id=%s AND NOT EXISTS (SELECT 1 FROM log_envios nuevo"
               " WHERE nuevo.envio_id=le.envio_id"
               " AND nuevo.numero_telefono=le.numero_telefono AND nuevo.id>le.id)")
    cur.execute(
        "SELECT le.estado_envio, le.estado_whatsapp, cb.texto AS boton, cb.accion_boton AS accion,"
        " le.plantilla_clave AS clave, DATE(COALESCE(le.entregado_en,le.fecha_hora)) AS fecha,"
        " le.precio_unitario, le.precio_moneda, le.precio_categoria, le.precio_tarifa_fecha,"
        " COUNT(*) AS pacientes, COUNT(DISTINCT CASE WHEN le.estado_whatsapp IN ('delivered','read')"
        " THEN le.whatsapp_message_id END) AS facturables, (EXISTS(SELECT 1"
        " FROM whatsapp_conversaciones_mensajes cm WHERE " + ventana_respuesta()
        + ") OR EXISTS(SELECT 1 FROM log_envios r WHERE r.plantilla_clave='respuesta'"
        " AND r.respuesta='respondio' AND "
        + ventana_respuesta(mensaje="r", telefono="numero_telefono", fecha="fecha_hora")
        + ")) AS respondio FROM log_envios le LEFT JOIN whatsapp_conversaciones_mensajes cb"
        " ON cb.mensaje_id=(SELECT cm.mensaje_id FROM whatsapp_conversaciones_mensajes cm WHERE "
        + ventana_respuesta() + " AND cm.tipo IN ('button','interactive')"
        " ORDER BY cm.recibido DESC, cm.mensaje_id DESC LIMIT 1) WHERE " + ultimos
        + " GROUP BY le.estado_envio, le.estado_whatsapp, cb.texto, cb.accion_boton,"
        " le.plantilla_clave, DATE(COALESCE(le.entregado_en,le.fecha_hora)), respondio,"
        " le.precio_unitario, le.precio_moneda, le.precio_categoria, le.precio_tarifa_fecha",
        (envio["id"],))
    cubo = list(cur.fetchall())
    datos = dict.fromkeys(("intentados", "entregados", "leidos", "errores", "invalidos",
                          "validos", "vistos_sin_respuesta", "sin_confirmacion", "respuestas"), 0)
    decisiones, costes = [], []
    for celda in cubo:
        n = int(celda["pacientes"])
        estado, entrega = celda["estado_envio"], celda.get("estado_whatsapp")
        valido = estado == "enviado" and entrega != "failed"
        celda["validos"] = valido
        datos["intentados"] += n
        datos["validos"] += n if valido else 0
        datos["errores"] += n if estado == "error" or entrega == "failed" else 0
        datos["invalidos"] += n if estado == "numero_invalido" else 0
        datos["respuestas"] += n if celda["respondio"] else 0
        if valido:
            datos["entregados"] += n if entrega in ("delivered", "read") else 0
            datos["leidos"] += n if entrega == "read" else 0
            datos["vistos_sin_respuesta"] += n if entrega == "read" and not celda["respondio"] else 0
            datos["sin_confirmacion"] += n if entrega not in ("delivered", "read") else 0
            decisiones.append(celda)
            if celda["facturables"]:
                costes.append({"clave": celda["clave"], "fecha": celda["fecha"], "mensajes": celda["facturables"]})
    datos["total"] = max(int(envio.get("total_pacientes") or 0), datos["intentados"])
    datos["sin_intento"] = max(0, datos["total"] - datos["intentados"])
    datos["sin_respuesta"] = max(0, datos["intentados"] - datos["respuestas"])
    datos["tasa_respuesta"] = round(100 * datos["respuestas"] / datos["intentados"], 1) if datos["intentados"] else 0
    cur.execute(
        "SELECT cm.texto AS boton, cm.accion_boton AS accion, le.estado_whatsapp AS entrega,"
        " cb.texto AS final_boton, cb.accion_boton AS final_accion, COUNT(*) AS clics,"
        " COUNT(DISTINCT le.numero_telefono) AS pacientes FROM log_envios le"
        " JOIN whatsapp_conversaciones_mensajes cm ON " + ventana_respuesta()
        + " LEFT JOIN whatsapp_conversaciones_mensajes cb ON cb.mensaje_id="
        " (SELECT ultima.mensaje_id FROM whatsapp_conversaciones_mensajes ultima WHERE "
        + ventana_respuesta(mensaje="ultima") + " AND ultima.tipo IN ('button','interactive')"
        " ORDER BY ultima.recibido DESC, ultima.mensaje_id DESC LIMIT 1)"
        + " WHERE " + ultimos + " AND cm.tipo IN ('button','interactive')"
        " AND le.estado_envio='enviado' AND COALESCE(le.estado_whatsapp,'')<>'failed'"
        " GROUP BY cm.texto, cm.accion_boton, le.estado_whatsapp, cb.texto, cb.accion_boton"
        " ORDER BY clics DESC, cm.texto LIMIT 250", (envio["id"],))
    botones = list(cur.fetchall())
    return {"resumen": datos, "botones": botones, "decisiones": decisiones,
            "entregas_coste": costes, "segmentos": cubo}


def clasificar_decisiones(datos, resolver_accion):
    """Solo selecciones de botones; el texto libre no convierte en interesado."""
    conteos = {"interes": 0, "desinteres": 0, "baja": 0}
    for fila in datos.pop("decisiones"):
        accion = resolver_accion(fila)
        fila["resultado"] = accion if accion in conteos else "sin_seleccion"
        if accion in conteos:
            conteos[accion] += int(fila["pacientes"])
    clasificados = sum(conteos.values())
    validos = datos["resumen"].get("validos", 0)
    datos["interacciones"] = {k: {"pacientes": v,
        "porcentaje": round(100 * v / validos, 1) if validos else 0}
        for k, v in conteos.items()}
    datos["resumen"]["clasificados"] = clasificados
    datos["resumen"]["sin_seleccion"] = max(0, validos - clasificados)
    datos["resumen"]["tasa_interes"] = round(100 * conteos["interes"] / validos, 1) if validos else 0
    for boton in datos["botones"]:
        boton["accion"] = resolver_accion(boton)
        final = resolver_accion({"accion": boton.get("final_accion"), "boton": boton.get("final_boton")})
        boton["resultado"] = final if final in conteos else "sin_seleccion"
        boton["porcentaje"] = round(100 * int(boton.get("pacientes") or 0) / validos, 1) if validos else 0
