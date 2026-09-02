"""
Capa de integración con WhatsApp Business Platform (Meta).

Contiene la lógica pura de integración: construcción de requests a la Graph API,
procesamiento de eventos de webhook, mapeo de estados de WhatsApp y guardado en
las tablas existentes del sistema (log_envios / pacientes). No toca la lógica
de negocio del módulo de mensajería; se invoca desde motor_envio.py y main.py
o desde el router de webhook.

"""

import asyncio
import hashlib
import os
import re
import time
from pathlib import Path

import httpx

from db import conectar, columna_existe, tabla_pacientes

BASE_DIR = Path(__file__).resolve().parent.parent
GRAPHVERSION = "v21.0"
GRAPH_URL = f"https://graph.facebook.com/{GRAPHVERSION}"

# Palabras que determinan opt-out (baja) según políticas de WhatsApp.
BAJA_KEYWORDS = ("baja", "cancelar", "no", "stop", "salir", "quit", "unsubscribe")

# Mapeo oficial de estados de Meta -> estados internos de log_envios.
ESTADO_DIRECTO = {"sent": "enviado"}
ESTADO_WHATSAPP = {"sent", "delivered", "read", "failed"}


def _normalizar_telefono(crudo: str) -> str | None:
    limpio = re.sub(r"[^\d+]", "", (crudo or "").strip())
    digitos = re.sub(r"\D", "", limpio)
    if limpio.startswith("+"):
        if len(digitos) == 11 and digitos.startswith("569"):
            return "+" + digitos
        if len(digitos) == 10 and digitos.startswith("56"):
            return "+569" + digitos[2:]
        if len(digitos) == 9 and digitos.startswith("9"):
            return "+56" + digitos
        return None
    if len(digitos) == 11 and digitos.startswith("569"):
        return "+" + digitos
    if len(digitos) == 10 and digitos.startswith("56"):
        return "+569" + digitos[2:]
    if len(digitos) == 9 and digitos.startswith("9"):
        return "+56" + digitos
    if len(digitos) == 8 and digitos.startswith("9"):
        return "+569" + digitos
    return None


def _detectar_ambiente_por_telefono(telefono: str) -> str | None:
    """Busca el teléfono en pacientes_dev y pacientes_prod y devuelve 'desarrollo' o 'produccion'."""
    for ambiente in ("desarrollo", "produccion"):
        t = tabla_pacientes(ambiente)
        try:
            with conectar(ambiente) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT id FROM {t} WHERE telefono = %s LIMIT 1", (telefono,))
                if cur.fetchone():
                    return ambiente
        except Exception:
            continue
    return None


def _hash_evento(payload: dict) -> str:
    """Clave de idempotencia a partir del payload+timestamp de Meta."""
    raw = str(payload.get("entry")) + "|" + str(payload.get("timestamp", ""))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


_MOTORES_HASH = None


class ErrorWhatsApp(Exception):
    """Error controlado de la integración con Meta. No rompe el sistema."""

    def __init__(self, codigo: int, tipo: str, mensaje: str):
        self.codigo = codigo
        self.tipo = tipo
        self.message = mensaje
        super().__init__(f"Tipo {tipo} (código {codigo}): {mensaje}")


class WhatsAppApiClient:
    """Cliente HTTP para el envío de mensajes vía Graph API."""

    def __init__(self, token: str, phone_number_id: str):
        self.token = token
        self.phone_number_id = phone_number_id

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    async def enviar(self, payload: dict) -> dict:
        url = f"{GRAPH_URL}/{self.phone_number_id}/messages"
        async with httpx.AsyncClient(timeout=30) as cliente:
            resp = await cliente.post(url, json=payload, headers=self._headers())
        return self._procesar_respuesta(resp)

    async def crear_template(self, waba_id: str, payload: dict) -> dict:
        """Crea un template de mensaje en Meta (POST /{waba_id}/message_templates)."""
        url = f"{GRAPH_URL}/{waba_id}/message_templates"
        async with httpx.AsyncClient(timeout=30) as cliente:
            resp = await cliente.post(url, json=payload, headers=self._headers())
        return self._procesar_respuesta(resp)

    async def listar_templates(self, waba_id: str) -> dict:
        """Lista los templates de mensaje de la WABA."""
        url = f"{GRAPH_URL}/{waba_id}/message_templates"
        async with httpx.AsyncClient(timeout=30) as cliente:
            resp = await cliente.get(url, headers=self._headers())
        return self._procesar_respuesta(resp)

    def _procesar_respuesta(self, resp) -> dict:
        if resp.status_code in (200, 201):
            return resp.json()
        detalle = ""
        try:
            err = resp.json().get("error", {})
            detalle = (err.get("error_user_msg")
                       or err.get("error_user_title")
                       or err.get("message")
                       or json_short(resp.text))
            codigo_msg = err.get("code", resp.status_code)
            tipo = err.get("type", "permanent")
        except Exception:
            err = {}
            codigo_msg = resp.status_code
            tipo = "permanent"
            detalle = json_short(resp.text)
        # Códigos 4xx con "temporales" se tratan como transitorios.
        es_temporal = tipo.lower() == "transient" or resp.status_code in (429, 5)
        raise ErrorWhatsApp(codigo_msg, "transient" if es_temporal else "permanent", detalle)


def json_short(texto: str) -> str:
    return (texto or "")[:200]


class WhatsAppService:
    """Orquesta la integración: envío, guardado de message_id y procesamiento de webhooks.

    La lógica de negocio del sistema (plantillas, destinatarios, confirmación de
    supervisor) vive en main.py. Esta clase solo conecta con WhatsApp y persiste
    los datos derivados en las tablas existentes.
    """

    def __init__(self):
        self.token = os.getenv("SNW_WA_TOKEN", "").strip()
        self.phone_number_id = os.getenv("SNW_WA_PHONE_ID", "").strip()
        self.waba_id = os.getenv("SNW_WA_BUSINESS_ACCOUNT_ID", "").strip()

    def configurada(self) -> bool:
        return bool(self.token) and bool(self.phone_number_id)

    @property
    def cliente(self) -> WhatsAppApiClient:
        return WhatsAppApiClient(self.token, self.phone_number_id)

    # ---------- Plantillas (templates) ----------
    COMODINES = {
        "nombre": "David",
        "apellido": "Araya",
        "info_extra": "su cita programada",
    }

    def convertir_texto_meta(self, texto: str) -> tuple[str, list[str]]:
        """Convierte los comodines {nombre}/{apellido}/{info_extra} del sistema
        a placeholders secuenciales de Meta ({{1}}, {{2}}, ...) y devuelve el
        texto convertido junto con los valores de ejemplo en ese orden."""
        orden: list[str] = []

        def reemplazo(m) -> str:
            clave = m.group(1)
            if clave in self.COMODINES and clave not in orden:
                orden.append(clave)
            idx = orden.index(clave) + 1 if clave in orden else 1
            return f"{{{{{idx}}}}}"

        texto_meta = re.sub(r"\{([a-z_]+)\}", reemplazo, texto)
        ejemplo = [self.COMODINES[c] for c in orden if c in self.COMODINES]
        return texto_meta, ejemplo

    def crear_template_meta(self, nombre: str, texto: str, lang: str = "es",
                            category: str = "UTILITY") -> dict:
        """Crea el template en Meta. Devuelve {ok, template_id, status, error}.

        Los templates en Meta quedan en estado PENDING hasta ser aprobados."""
        if not self.waba_id or not self.token:
            return {"ok": False, "template_id": None, "status": None,
                    "error": "Faltan SNW_WA_BUSINESS_ACCOUNT_ID o SNW_WA_TOKEN"}

        texto_meta, ejemplo = self.convertir_texto_meta(texto)
        componente_body: dict = {"type": "BODY", "text": texto_meta}
        if ejemplo:
            componente_body["example"] = {"body_text": [ejemplo]}

        payload = {
            "name": nombre,
            "category": category,
            "language": lang,
            "components": [componente_body],
        }

        try:
            data = asyncio.run(self.cliente.crear_template(self.waba_id, payload))
        except ErrorWhatsApp as e:
            return {"ok": False, "template_id": None, "status": None, "error": e.message}

        tid = data.get("id") or ""
        status = data.get("status") or "PENDING"
        return {"ok": True, "template_id": tid, "status": status, "error": None}

    # ---------- Envío ----------
    def construir_payload_texto(self, telefono: str, mensaje: str, preview_url: bool = False) -> tuple:
        """Payload para texto libre (válido dentro de la ventana de 24h)."""
        numero = telefono.lstrip("+")
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": numero,
            "type": "text",
            "text": {"preview_url": preview_url, "body": mensaje},
        }
        return payload, "texto"

    def construir_payload_template(self, telefono: str, nombre: str, lang: str,
                                   variables: list[str] | None = None, componentes: dict | None = None) -> tuple:
        """Payload para un template aprobado de Meta."""
        numero = telefono.lstrip("+")
        template: dict = {"name": nombre, "language": {"code": lang}}
        if componentes:
            template["components"] = componentes
        elif variables:
            template["components"] = [{
                "type": "body",
                "parameters": [{"type": "text", "text": v} for v in variables],
            }]
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": numero,
            "type": "template",
            "template": template,
        }
        return payload, "template"

    def extraer_orden_comodines(self, texto: str) -> list[str]:
        """Devuelve el orden de aparición de los comodines {nombre}/{apellido}/{info_extra}
        en el texto, para construir los parámetros del template en el orden correcto."""
        orden: list[str] = []
        for m in re.finditer(r"\{([a-z_]+)\}", texto or ""):
            clave = m.group(1)
            if clave in ("nombre", "apellido", "info_extra") and clave not in orden:
                orden.append(clave)
        return orden

    async def enviar(self, telefono: str, mensaje: str, plantilla: dict | None = None,
                     variables: dict | None = None) -> tuple:
        """Envía un mensaje. Devuelve (ok, message_id, error, estado).

        Si la plantilla en el sistema tiene un template Meta configurado
        (campo 'whatsapp_template'), se envía como template aprobado usando los
        valores reales del paciente; en caso contrario, texto libre.
        """
        msg = plantilla or {}
        nombre_template = msg.get("whatsapp_template")
        idioma = msg.get("whatsapp_template_lang") or os.getenv("SNW_WA_TEMPLATE_LANG", "es")

        if nombre_template:
            orden = self.extraer_orden_comodines(msg.get("texto", "") or "")
            vdict = variables or {}
            valores = [vdict.get(clave, "") or "" for clave in orden]
            payload_, tipo = self.construir_payload_template(
                telefono, nombre_template, idioma, variables=valores,
            )
        else:
            payload_, tipo = self.construir_payload_texto(telefono, mensaje)

        try:
            data = await self.cliente.enviar(payload_)
        except ErrorWhatsApp as e:
            return False, None, e.message, "failed" if e.tipo == "permanent" else "sent"

        msg_id = ""
        if data.get("messages"):
            msg_id = data["messages"][0].get("id", "") or ""
        if not msg_id:
            return False, "", "La API no devolvió message id", "failed"
        return True, msg_id, None, "sent"

    # ---------- Persistencia de message id ----------
    def guardar_message_id(self, telefono: str, message_id: str, estado_envio: str,
                           ambiente: str, descripcion_error: str | None = None) -> bool:
        """Relaciona el message id de Meta con el último log del paciente."""
        numero = telefono if telefono.startswith("+") else f"+{telefono}"
        t = tabla_pacientes(ambiente)
        try:
            with conectar(ambiente) as conn, conn.cursor() as cur:
                cur.execute(
                    "UPDATE log_envios SET whatsapp_message_id = %s, estado_whatsapp = 'sent',"
                    " estado_envio = %s, descripcion_error = %s"
                    f" WHERE paciente_id = (SELECT id FROM {t} WHERE telefono = %s LIMIT 1)"
                    " ORDER BY id DESC LIMIT 1",
                    (message_id, estado_envio, descripcion_error, numero),
                )
                conn.commit()
                return cur.rowcount > 0
        except Exception as e:
            print(f"[WHATSAPP] No se pudo guardar message id: {e}")
            return False

    # ---------- Webhook ----------
    def verificar_webhook(self, mode: str, verify_token: str, challenge: str) -> bool | str:
        """Verificación inicial del webhook solicitada por Meta."""
        esperado = os.getenv("SNW_WA_VERIFY_TOKEN", "").strip()
        if mode == "subscribe" and esperado and verify_token == esperado:
            return challenge
        return False

    def procesar_evento(self, body: dict) -> list[str]:
        """Procesa un payload de webhook. Devuelve lista de acciones ejecutadas."""
        accion = []
        if self._evento_duplicado(body):
            accion.append("duplicado_omitido")
            return accion

        for entry in body.get("entry", []):
            cambia = entry.get("changes") or []
            for change in cambia:
                campo = change.get("field", "")
                valor = change.get("value", {})
                if campo == "messages":
                    accion += self._procesar_mensajes(valor)
                elif campo == "status":
                    accion += self._procesar_estados(valor)
        self._guardar_evento(body)
        return accion

    def _procesar_mensajes(self, valor: dict) -> list[str]:
        """Mensajes entrantes del cliente (respuestas, bajas, etc.)."""
        acciones = []
        telefono = (valor.get("contacts") or [{}])[0].get("wa_id", "")
        for mensaje in valor.get("messages", []):
            tipo = mensaje.get("type", "")
            texto = ""
            if tipo == "text":
                texto = (mensaje.get("text") or {}).get("body", "")
            elif tipo == "button":
                texto = (mensaje.get("button") or {}).get("text", "")
            elif tipo == "interactive":
                inter = mensaje.get("interactive", {})
                texto = (inter.get("button_reply") or inter.get("list_reply") or {}).get("title", "")
            if not texto:
                continue
            info = self._registrar_respuesta(telefono, texto, mensaje.get("timestamp"))
            acciones.append(f"respuesta_{info}")

            if self._es_baja(texto):
                self._registrar_baja(telefono)
                acciones.append("baja")
        return acciones

    def _procesar_estados(self, valor: dict) -> list[str]:
        """Cambios de estado: sent, delivered, read, failed."""
        acciones = []
        estado = valor.get("status", "")
        message_id = valor.get("message_id", "") or valor.get("wamid", "")
        telefono = valor.get("phone_number", "") or valor.get("recipient_id", "")
        if estado not in ESTADO_WHATSAPP or not message_id:
            return acciones
        self._actualizar_estado(message_id, estado)
        acciones.append(f"estado_{estado}")
        return acciones

    # ---------- Persistencia de eventos ----------
    def _evento_duplicado(self, body: dict) -> bool:
        try:
            with conectar("desarrollo") as conn, conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n FROM whatsapp_eventos WHERE clave = %s",
                            (_hash_evento(body),))
                return (cur.fetchone() or {}).get("n", 0) > 0
        except Exception:
            return False

    def _guardar_evento(self, body: dict) -> None:
        clave = _hash_evento(body)
        texto = str(body)[:2000]
        for ambiente in ("desarrollo", "produccion"):
            try:
                with conectar(ambiente) as conn, conn.cursor() as cur:
                    cur.execute(
                        "INSERT IGNORE INTO whatsapp_eventos (clave, payload) VALUES (%s, %s)",
                        (clave, texto),
                    )
                    conn.commit()
            except Exception:
                pass

    # ---------- Registro de respuestas y estados ----------
    def _registrar_respuesta(self, telefono: str, texto: str, timestamp: str) -> str:
        amb = _detectar_ambiente_por_telefono(telefono)
        if not amb:
            return "sin_cliente"
        t = tabla_pacientes(amb)
        try:
            with conectar(amb) as conn, conn.cursor() as cur:
                if columna_existe(t, "estado", amb):
                    cur.execute(
                        f"UPDATE {t} SET estado = 'enviado' WHERE telefono = %s", (telefono,)
                    )
                cur.execute(
                    "INSERT INTO log_envios (envio_id, paciente_id, nombre_paciente, numero_telefono,"
                    " mensaje, plantilla_clave, estado_envio, respuesta, descripcion_error)"
                    f" SELECT NULL, id, nombre, telefono, %s, 'respuesta', 'enviado', 'respondio', NULL"
                    f" FROM {t} WHERE telefono = %s LIMIT 1",
                    (texto, telefono),
                )
                conn.commit()
            return "registrada"
        except Exception as e:
            print(f"[WHATSAPP] No se pudo registrar respuesta: {e}")
            return "error"

    def _registrar_baja(self, telefono: str) -> None:
        amb = _detectar_ambiente_por_telefono(telefono)
        if not amb:
            return
        t = tabla_pacientes(amb)
        try:
            with conectar(amb) as conn, conn.cursor() as cur:
                if columna_existe(t, "whatsapp_opt_out", amb):
                    cur.execute(
                        f"UPDATE {t} SET whatsapp_opt_out = 1 WHERE telefono = %s", (telefono,)
                    )
                cur.execute(
                    "UPDATE log_envios SET respuesta = 'baja' WHERE paciente_id ="
                    f" (SELECT id FROM {t} WHERE telefono = %s LIMIT 1)"
                    " ORDER BY id DESC LIMIT 1",
                    (telefono,),
                )
                conn.commit()
        except Exception as e:
            print(f"[WHATSAPP] No se pudo registrar baja: {e}")

    def _actualizar_estado(self, message_id: str, estado: str) -> None:
        for ambiente in ("desarrollo", "produccion"):
            try:
                with conectar(ambiente) as conn, conn.cursor() as cur:
                    cur.execute(
                        "UPDATE log_envios SET estado_whatsapp = %s"
                        " WHERE whatsapp_message_id = %s",
                        (estado, message_id),
                    )
                    if estado == "sent":
                        cur.execute(
                            "UPDATE log_envios SET estado_envio = 'enviado'"
                            " WHERE whatsapp_message_id = %s", (message_id,)
                        )
                    if estado == "failed":
                        cur.execute(
                            "UPDATE log_envios SET estado_envio = 'error'"
                            " WHERE whatsapp_message_id = %s", (message_id,)
                        )
                    conn.commit()
            except Exception:
                continue

    def _es_baja(self, texto: str) -> bool:
        t = re.sub(r"[^\wáéíóúñ\s]", "", texto.lower()).strip()
        return t in BAJA_KEYWORDS or t in ("quiero darme de baja", "darme de baja", "no quiero recibir mas")


class WebhookHandler:
    """Capa delgada que une el router HTTP con el procesador de eventos."""

    def __init__(self):
        self.servicio = WhatsAppService()

    def verificar(self, mode: str, verify_token: str, challenge: str):
        return self.servicio.verificar_webhook(mode, verify_token, challenge)

    def procesar(self, payload: dict) -> list[str]:
        return self.servicio.procesar_evento(payload)
