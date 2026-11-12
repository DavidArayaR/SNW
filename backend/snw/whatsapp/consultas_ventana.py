"""Agrupa textos entrantes sin copiar mensajes ni datos del paciente."""

from datetime import timedelta

from db import conectar


DURACION = timedelta(minutes=2)


def programar(phone_id: str, telefono: str, mensaje_id: str, referencia: str | None) -> bool:
    with conectar() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT recibido,consulta_agrupado FROM whatsapp_conversaciones_mensajes"
            " WHERE mensaje_id=%s AND wa_phone_id=%s AND telefono_paciente=%s AND tipo='text'"
            " AND recibido BETWEEN DATE_SUB(NOW(), INTERVAL 24 HOUR) AND NOW() FOR UPDATE",
            (mensaje_id, phone_id, telefono),
        )
        mensaje = cur.fetchone()
        if not mensaje:
            return False
        if mensaje["consulta_agrupado"]:
            return True
        recibido = mensaje["recibido"]
        cur.execute(
            "SELECT MIN(recibido) AS inicio FROM whatsapp_conversaciones_mensajes"
            " WHERE wa_phone_id=%s AND telefono_paciente=%s"
            " AND tipo IN ('text','audio','image','document','video','sticker') AND consulta_agrupado=0"
            " AND recibido>%s AND recibido<=%s",
            (phone_id, telefono, recibido - DURACION, recibido),
        )
        vence = (cur.fetchone()["inicio"] or recibido) + DURACION
        cur.execute(
            "UPDATE whatsapp_conversaciones_mensajes SET consulta_vence=%s,consulta_referencia=%s"
            " WHERE mensaje_id=%s",
            (vence, referencia, mensaje_id),
        )
        conn.commit()
        return True


def tomar_ventana():
    """Devuelve una ventana vencida una vez, incluso con varios workers."""
    with conectar() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT mensaje_id,wa_phone_id,telefono_paciente,consulta_vence,NOW() AS ahora"
            " FROM whatsapp_conversaciones_mensajes"
            " WHERE consulta_vence<=NOW() ORDER BY consulta_vence,mensaje_id LIMIT 1 FOR UPDATE"
        )
        ventana = cur.fetchone()
        if not ventana:
            return None
        inicio = ventana["consulta_vence"] - DURACION
        phone_id, telefono = ventana["wa_phone_id"], ventana["telefono_paciente"]
        cur.execute(
            "SELECT mensaje_id,tipo,texto,recibido,consulta_referencia FROM whatsapp_conversaciones_mensajes"
            " WHERE wa_phone_id=%s AND telefono_paciente=%s"
            " AND tipo IN ('text','audio','image','document','video','sticker') AND consulta_agrupado=0"
            " AND recibido>=%s AND recibido<%s ORDER BY recibido,mensaje_id FOR UPDATE",
            (phone_id, telefono, inicio, ventana["consulta_vence"]),
        )
        mensajes = cur.fetchall()
        if mensajes and mensajes[0]["recibido"] > inicio:
            vence = mensajes[0]["recibido"] + DURACION
            cur.execute("UPDATE whatsapp_conversaciones_mensajes SET consulta_vence=%s WHERE mensaje_id=%s",
                        (vence, ventana["mensaje_id"]))
            conn.commit()
            return {}
        cur.executemany(
            "UPDATE whatsapp_conversaciones_mensajes SET consulta_agrupado=1,"
            " consulta_vence=NULL,consulta_referencia=NULL WHERE mensaje_id=%s",
            [(m["mensaje_id"],) for m in mensajes] or [(ventana["mensaje_id"],)],
        )
        conn.commit()
        if ventana["ahora"] - inicio >= timedelta(hours=24):
            return {}
        return {"phone_id": phone_id, "telefono": telefono, "mensajes": mensajes}
