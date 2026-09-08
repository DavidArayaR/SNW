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
import re

import httpx

from db import conectar, config_get, log_error, tabla_pacientes

# Versión de la Graph API de Meta. Configurable en la tabla `configuracion`
# (clave `wa_graph_version`); si no está, se usa este valor por defecto.
GRAPH_VERSION_DEFAULT = "v26.0"


def graph_version() -> str:
    return (config_get("wa_graph_version") or GRAPH_VERSION_DEFAULT).strip() or GRAPH_VERSION_DEFAULT


def graph_url() -> str:
    """Base de la Graph API: https://graph.facebook.com/<version>"""
    return f"https://graph.facebook.com/{graph_version()}"


# Palabras que determinan opt-out (baja) según políticas de WhatsApp.
# Detección de baja (opt-out). Se compara sobre el texto en minúsculas y sin
# signos. `BAJA_EXACTAS`: solo si el mensaje ES exactamente eso.
# `BAJA_PALABRAS`: si aparece como palabra suelta. `BAJA_FRASES`: como substring.
BAJA_EXACTAS = ("no", "no.", "stop", "baja", "cancelar", "cancelo", "salir",
                "quit", "unsubscribe", "eliminar", "remover", "detener")
BAJA_PALABRAS = ("baja", "stop", "unsubscribe", "cancelar", "cancelo", "detener")
BAJA_FRASES = (
    "dar de baja", "darme de baja", "darse de baja", "me doy de baja", "de baja",
    "quiero darme de baja", "quiero que me den de baja", "deseo darme de baja",
    "no quiero recibir", "no deseo recibir", "no quiero mas mensajes",
    "no me manden", "no me envien", "no enviar mas", "dejen de enviar",
    "dejar de recibir", "dejen de mandar", "no molestar", "no me contacten",
    "no me interesa", "ya no quiero", "no quiero saber", "borrame", "borrenme",
    "sacame de", "sacarme de", "no quiero que me escriban",
    "quitar de la lista", "sacar de la lista", "borrar mi numero", "eliminar mi numero",
    "detener promociones", "detener promo", "stop promotions", "no promociones",
    "cancelar suscripcion", "cancelar suscripción",
)

# Estados de entrega que Meta reporta por webhook.
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


# Compara teléfonos ignorando el '+' inicial y los espacios (Meta manda el
# wa_id como "56993921740"; la BD los guarda como "+56993921740").
_TEL_MATCH = "REPLACE(REPLACE({col}, '+', ''), ' ', '') = REPLACE(REPLACE(%s, '+', ''), ' ', '')"


def _hash_evento(payload: dict) -> str:
    """Clave de idempotencia a partir del payload+timestamp de Meta."""
    raw = str(payload.get("entry")) + "|" + str(payload.get("timestamp", ""))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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
        url = f"{graph_url()}/{self.phone_number_id}/messages"
        async with httpx.AsyncClient(timeout=30) as cliente:
            resp = await cliente.post(url, json=payload, headers=self._headers())
        return self._procesar_respuesta(resp)

    async def crear_template(self, waba_id: str, payload: dict) -> dict:
        """Crea un template de mensaje en Meta (POST /{waba_id}/message_templates)."""
        url = f"{graph_url()}/{waba_id}/message_templates"
        async with httpx.AsyncClient(timeout=30) as cliente:
            resp = await cliente.post(url, json=payload, headers=self._headers())
        return self._procesar_respuesta(resp)

    async def editar_template(self, template_id: str, payload: dict) -> dict:
        """Edita un template EXISTENTE en Meta (POST /{template_id}).

        A diferencia de crear_template, este endpoint se identifica por el ID
        propio del template (no por el waba_id) y no acepta 'name' ni
        'language': esos dos campos son inmutables una vez creado el template.
        """
        url = f"{graph_url()}/{template_id}"
        async with httpx.AsyncClient(timeout=30) as cliente:
            resp = await cliente.post(url, json=payload, headers=self._headers())
        return self._procesar_respuesta(resp)

    async def obtener_template(self, template_id: str) -> dict:
        """Obtiene los datos actuales de un template por su propio ID."""
        url = f"{graph_url()}/{template_id}"
        async with httpx.AsyncClient(timeout=30) as cliente:
            resp = await cliente.get(
                url, params={"fields": "category,status,name,language"}, headers=self._headers()
            )
        return self._procesar_respuesta(resp)

    async def listar_templates(self, waba_id: str) -> dict:
        """Lista los templates de mensaje de la WABA, con paginación."""
        url = f"{graph_url()}/{waba_id}/message_templates"
        params = {
            "fields": "id,name,language,status,category,components,rejected_reason",
            "limit": 100,
        }
        templates: list = []
        async with httpx.AsyncClient(timeout=30) as cliente:
            while url:
                resp = await cliente.get(url, params=params, headers=self._headers())
                data = self._procesar_respuesta(resp)
                templates += data.get("data", [])
                url = (data.get("paging") or {}).get("next")
                params = None  # la URL "next" ya trae todos los query params
        return {"data": templates}

    async def buscar_template(self, waba_id: str, nombre: str) -> dict:
        """Busca un template por nombre (puede devolver varias variantes de idioma)."""
        url = f"{graph_url()}/{waba_id}/message_templates"
        async with httpx.AsyncClient(timeout=30) as cliente:
            resp = await cliente.get(url, params={"name": nombre}, headers=self._headers())
        return self._procesar_respuesta(resp)

    async def eliminar_template(self, waba_id: str, nombre: str,
                                template_id: str | None = None) -> dict:
        """Borra un template en Meta (DELETE /{waba_id}/message_templates).

        Con solo `name` se borran todas las variantes de idioma de ese nombre.
        Si se pasa `template_id` (hsm_id) se borra únicamente esa variante.
        """
        params = {"name": nombre}
        if template_id:
            params["hsm_id"] = template_id
        url = f"{graph_url()}/{waba_id}/message_templates"
        async with httpx.AsyncClient(timeout=30) as cliente:
            resp = await cliente.request("DELETE", url, params=params, headers=self._headers())
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
        except Exception as e:
            log_error(f"_procesar_respuesta: no se pudo parsear el error de Meta (HTTP {resp.status_code})", e)
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

    # Se leen de la tabla `configuracion` en cada acceso, para que un cambio
    # (p. ej. rotar el token) surta efecto sin reconstruir el servicio.
    @property
    def token(self) -> str:
        return (config_get("wa_token") or "").strip()

    @property
    def phone_number_id(self) -> str:
        return (config_get("wa_phone_id") or "").strip()

    @property
    def waba_id(self) -> str:
        return (config_get("wa_business_account_id") or "").strip()

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
                    "error": "Falta la cuenta de WhatsApp Business o el token (configúralos en Configuración)"}

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
            log_error(f"crear_template_meta({nombre!r})", e)
            return {"ok": False, "template_id": None, "status": None, "error": e.message}

        tid = data.get("id") or ""
        status = data.get("status") or "PENDING"
        return {"ok": True, "template_id": tid, "status": status, "error": None}

    def editar_template_meta(self, template_id: str, texto: str, category: str = "UTILITY") -> dict:
        """Edita un template ya existente en Meta usando su ID.

        Importante: Meta NO permite cambiar la categoría de un template ya
        APPROVED bajo ninguna circunstancia. Para evitar el error, primero
        consultamos la categoría real que tiene el template en Meta y la
        usamos tal cual en el payload de edición, sin importar qué categoría
        tengamos guardada localmente. Además, Meta solo permite editar un
        template como máximo una vez cada 24h, y al editar uno ya APPROVED,
        vuelve a quedar en PENDING hasta que Meta lo re-revise."""
        if not self.token:
            return {"ok": False, "template_id": template_id, "status": None,
                    "error": "Falta el token de WhatsApp (configúralo en Configuración)"}
        if not template_id:
            return {"ok": False, "template_id": None, "status": None,
                    "error": "No hay un template_id conocido para editar"}

        categoria_real = category
        try:
            info = asyncio.run(self.cliente.obtener_template(template_id))
            categoria_real = info.get("category") or category
        except ErrorWhatsApp as e:
            # Si falla la consulta (p. ej. permisos), seguimos con la
            # categoría local como mejor esfuerzo; el reintento de abajo
            # cubre el caso de que igual sea rechazada.
            log_error(f"editar_template_meta: no se pudo consultar la categoría real de {template_id}", e)

        texto_meta, ejemplo = self.convertir_texto_meta(texto)
        componente_body: dict = {"type": "BODY", "text": texto_meta}
        if ejemplo:
            componente_body["example"] = {"body_text": [ejemplo]}

        payload = {"category": categoria_real, "components": [componente_body]}
        aviso_categoria = None
        if categoria_real != category:
            aviso_categoria = (
                f"El texto se actualizó. La categoría se mantuvo en"
                f" '{categoria_real}' porque Meta no permite cambiar la"
                f" categoría de un template ya aprobado."
            )

        try:
            data = asyncio.run(self.cliente.editar_template(template_id, payload))
        except ErrorWhatsApp as e:
            log_error(f"editar_template_meta({template_id})", e)
            mensaje = (e.message or "").lower()
            if "categor" in mensaje:
                # Último recurso: reintentar sin mandar el campo de categoría.
                payload_sin_categoria = {"components": [componente_body]}
                try:
                    data = asyncio.run(self.cliente.editar_template(template_id, payload_sin_categoria))
                    aviso_categoria = (
                        "El texto se actualizó, pero Meta no permite cambiar la"
                        " categoría de un template ya aprobado (se mantuvo la"
                        " categoría original en Meta)."
                    )
                except ErrorWhatsApp as e2:
                    log_error(f"editar_template_meta({template_id}) reintento sin categoría", e2)
                    return {"ok": False, "template_id": template_id, "status": None, "error": e2.message}
            else:
                return {"ok": False, "template_id": template_id, "status": None, "error": e.message}

        status = data.get("status") or "PENDING"
        return {
            "ok": True,
            "template_id": template_id,
            "status": status,
            "error": aviso_categoria,
            "category_real": categoria_real,
        }

    def id_template_meta(self, nombre_template: str, lang: str) -> str | None:
        """Busca en Meta el ID real de un template por nombre+idioma.

        Sirve como respaldo cuando no tenemos el template_id guardado
        localmente (por ejemplo, plantillas creadas antes de este cambio)."""
        if not self.waba_id or not self.token:
            return None
        try:
            data = asyncio.run(self.cliente.buscar_template(self.waba_id, nombre_template))
        except ErrorWhatsApp as e:
            log_error(f"id_template_meta({nombre_template!r}, {lang!r})", e)
            return None
        for t in data.get("data", []):
            if t.get("language") == lang:
                return t.get("id")
        return None

    def guardar_template_meta(self, nombre: str, texto: str, lang: str = "es",
                              category: str = "UTILITY",
                              template_id_conocido: str | None = None) -> dict:
        """Crea el template en Meta, o lo edita si ya existe.

        Si no se pasa un template_id_conocido, primero intenta encontrarlo en
        Meta por nombre+idioma (por si ya se había creado pero se perdió el id
        localmente). Si lo encuentra, edita; si no, crea uno nuevo."""
        tid = template_id_conocido or self.id_template_meta(nombre, lang)
        if tid:
            return self.editar_template_meta(tid, texto, category)
        return self.crear_template_meta(nombre, texto, lang, category)

    def estado_template_meta(self, nombre_template: str, lang: str) -> dict:
        """Consulta en Meta el estado actual de un template por nombre + idioma.

        Devuelve {ok, status, category, rejected_reason, error}. No rompe el
        flujo si Meta falla o si no encuentra el template: el error queda en
        el campo 'error' para que el llamador decida qué mostrar."""
        if not self.waba_id or not self.token:
            return {"ok": False, "status": None, "category": None, "rejected_reason": None,
                    "error": "Falta la cuenta de WhatsApp Business o el token (configúralos en Configuración)"}

        try:
            data = asyncio.run(self.cliente.buscar_template(self.waba_id, nombre_template))
        except ErrorWhatsApp as e:
            log_error(f"estado_template_meta({nombre_template!r}, {lang!r})", e)
            return {"ok": False, "status": None, "category": None, "rejected_reason": None,
                    "error": e.message}

        for t in data.get("data", []):
            if t.get("language") == lang:
                return {
                    "ok": True,
                    "status": t.get("status"),
                    "category": t.get("category"),
                    "rejected_reason": t.get("rejected_reason") or t.get("reject_reason"),
                    "error": None,
                }

        return {"ok": False, "status": None, "category": None, "rejected_reason": None,
                "error": f"No se encontró el template '{nombre_template}' en idioma '{lang}' en Meta"}

    def listar_templates_meta(self) -> dict:
        """Lista TODOS los templates que existen en Meta para la WABA configurada
        (fuente de verdad cuando la cuenta no tiene permiso para crear/editar
        templates vía API: los templates se gestionan a mano en Meta y este
        sistema solo los lee). Devuelve {ok, templates, error}."""
        if not self.waba_id or not self.token:
            return {"ok": False, "templates": [], "error": "Falta la cuenta de WhatsApp Business o el token (configúralos en Configuración)"}
        try:
            data = asyncio.run(self.cliente.listar_templates(self.waba_id))
        except ErrorWhatsApp as e:
            log_error("listar_templates_meta", e)
            return {"ok": False, "templates": [], "error": e.message}
        return {"ok": True, "templates": data.get("data", []), "error": None}

    def eliminar_template_meta(self, nombre_template: str, template_id: str | None = None) -> dict:
        """Borra el template en Meta. Devuelve {ok, error}.

        Si Meta responde que el template no existe, se considera OK (ya no está,
        que es justo lo que se busca). Cualquier otro fallo se reporta sin
        romper el flujo: la plantilla local se borra igual."""
        nombre_template = (nombre_template or "").strip()
        if not nombre_template:
            return {"ok": True, "error": None}
        if not self.waba_id or not self.token:
            return {"ok": False,
                    "error": "Falta la cuenta de WhatsApp Business o el token (configúralos en Configuración)"}
        try:
            asyncio.run(self.cliente.eliminar_template(self.waba_id, nombre_template, template_id))
            return {"ok": True, "error": None}
        except ErrorWhatsApp as e:
            msg = (e.message or "").lower()
            if e.codigo in (100, 2593002) or "does not exist" in msg or "no existe" in msg or "not found" in msg:
                return {"ok": True, "error": None}
            log_error(f"eliminar_template_meta({nombre_template!r})", e)
            return {"ok": False, "error": e.message}

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
        idioma = msg.get("whatsapp_template_lang") or config_get("wa_template_lang", "es")

        if nombre_template:
            orden = self.extraer_orden_comodines(msg.get("texto", "") or "")
            vdict = variables or {}
            valores = [vdict.get(clave, "") or "" for clave in orden]
            payload_, _ = self.construir_payload_template(
                telefono, nombre_template, idioma, variables=valores,
            )
        else:
            payload_, _ = self.construir_payload_texto(telefono, mensaje)

        try:
            data = await self.cliente.enviar(payload_)
        except ErrorWhatsApp as e:
            log_error(f"envío a {telefono} (template={nombre_template or 'texto libre'})", e)
            return False, None, e.message, "failed" if e.tipo == "permanent" else "sent"

        msg_id = ""
        if data.get("messages"):
            msg_id = data["messages"][0].get("id", "") or ""
        if not msg_id:
            return False, "", "La API no devolvió message id", "failed"
        return True, msg_id, None, "sent"

    # ---------- Webhook ----------
    def verificar_webhook(self, mode: str, verify_token: str, challenge: str) -> bool | str:
        """Verificación inicial del webhook solicitada por Meta."""
        esperado = (config_get("wa_verify_token") or "").strip()
        if mode == "subscribe" and esperado and verify_token == esperado:
            return challenge
        return False

    def procesar_evento(self, body: dict) -> list[str]:
        """Procesa un payload de webhook. Devuelve lista de acciones ejecutadas.

        Meta manda TODO (mensajes entrantes y cambios de estado de entrega) bajo
        `field: "messages"`; se distinguen por el contenido de `value`:
          - `value.messages[]`  -> mensaje entrante del cliente (respuesta / baja)
          - `value.statuses[]`  -> cambio de estado (sent/delivered/read/failed)
        """
        accion = []
        if self._evento_duplicado(body):
            accion.append("duplicado_omitido")
            return accion

        for entry in body.get("entry", []):
            for change in (entry.get("changes") or []):
                campo = change.get("field", "")
                valor = change.get("value", {}) or {}
                if campo == "message_template_status_update":
                    accion.append(f"template_{valor.get('event', '?')}")
                    continue
                if valor.get("messages"):
                    accion += self._procesar_mensajes(valor)
                if valor.get("statuses"):
                    accion += self._procesar_estados(valor)
        self._guardar_evento(body)
        return accion

    def _procesar_mensajes(self, valor: dict) -> list[str]:
        """Mensajes entrantes del cliente (respuestas, bajas, etc.)."""
        acciones = []
        # Meta manda el wa_id sin '+' (ej. "56993921740"); lo normalizamos.
        crudo = (valor.get("contacts") or [{}])[0].get("wa_id", "")
        telefono = _normalizar_telefono(crudo) or crudo
        for mensaje in valor.get("messages", []):
            tipo = mensaje.get("type", "")
            texto = ""
            extra = ""  # payload de botón, etc. (también se revisa para baja)
            if tipo == "text":
                texto = (mensaje.get("text") or {}).get("body", "")
            elif tipo == "button":
                b = mensaje.get("button") or {}
                texto = b.get("text", "")
                extra = b.get("payload", "")
            elif tipo == "interactive":
                inter = mensaje.get("interactive", {})
                br = inter.get("button_reply") or inter.get("list_reply") or {}
                texto = br.get("title", "")
                extra = br.get("id", "") or br.get("description", "")
            if not texto and not extra:
                continue
            info = self._registrar_respuesta(telefono, texto or extra, mensaje.get("timestamp"))
            acciones.append(f"respuesta_{info}")

            if self._es_baja(texto) or self._es_baja(extra):
                self._registrar_baja(telefono)
                acciones.append("baja")
        return acciones

    @staticmethod
    def _detalle_errores(errores) -> str:
        partes = []
        for err in (errores or []):
            data_err = err.get("error_data") or {}
            partes.append(" - ".join(str(x) for x in (
                err.get("code"), err.get("title"), err.get("message"),
                data_err.get("details"),
            ) if x))
        return " | ".join(p for p in partes if p) or "Meta reportó el mensaje como fallido sin detalle"

    def _procesar_estados(self, valor: dict) -> list[str]:
        """Cambios de estado de entrega: sent, delivered, read, failed.
        `value.statuses` es una lista; puede traer varios en un mismo evento."""
        acciones = []
        for s in valor.get("statuses", []):
            estado = s.get("status", "")
            message_id = s.get("id", "") or s.get("message_id", "")
            telefono = s.get("recipient_id", "") or s.get("phone_number", "")
            if estado not in ESTADO_WHATSAPP or not message_id:
                continue
            detalle_error = None
            if estado == "failed":
                detalle_error = self._detalle_errores(s.get("errors"))
                log_error(f"webhook: mensaje {message_id} a {telefono} falló - {detalle_error}")
            self._actualizar_estado(message_id, estado, detalle_error)
            acciones.append(f"estado_{estado}")
        return acciones

    # ---------- Persistencia de eventos ----------
    def _evento_duplicado(self, body: dict) -> bool:
        try:
            with conectar("desarrollo") as conn, conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n FROM whatsapp_eventos WHERE clave = %s",
                            (_hash_evento(body),))
                return (cur.fetchone() or {}).get("n", 0) > 0
        except Exception as e:
            log_error("_evento_duplicado", e)
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
            except Exception as e:
                log_error(f"_guardar_evento({ambiente})", e)

    # ---------- Registro de respuestas y estados ----------
    _TABLAS_PAC = (tabla_pacientes("desarrollo"), tabla_pacientes("produccion"))

    def _pacientes_con_tel(self, cur, telefono: str) -> list[dict]:
        """Filas (id, nombre, telefono, tabla) de los pacientes cuyo teléfono
        coincide, en ambas tablas (dev + prod). log_envios es compartida."""
        match = _TEL_MATCH.format(col="telefono")
        hallados = []
        for t in self._TABLAS_PAC:
            cur.execute(f"SELECT id, nombre, telefono FROM {t} WHERE {match} LIMIT 1", (telefono,))
            row = cur.fetchone()
            if row:
                row["tabla"] = t
                hallados.append(row)
        return hallados

    def _registrar_respuesta(self, telefono: str, texto: str, timestamp: str) -> str:
        match = _TEL_MATCH.format(col="telefono")
        try:
            with conectar() as conn, conn.cursor() as cur:
                pacientes = self._pacientes_con_tel(cur, telefono)
                if not pacientes:
                    return "sin_cliente"
                for p in pacientes:
                    cur.execute(f"UPDATE {p['tabla']} SET estado = 'enviado' WHERE {match}", (telefono,))
                    # Una respuesta real deja sin efecto una corrección manual previa.
                    try:
                        cur.execute(
                            f"UPDATE {p['tabla']} SET respuesta_manual = NULL"
                            f" WHERE {match} AND respuesta_manual IS NOT NULL",
                            (telefono,),
                        )
                    except Exception:
                        pass  # esquema sin la columna
                p0 = pacientes[0]
                cur.execute(
                    "INSERT INTO log_envios (envio_id, paciente_id, nombre_paciente, numero_telefono,"
                    " mensaje, plantilla_clave, estado_envio, respuesta, descripcion_error)"
                    " VALUES (NULL, %s, %s, %s, %s, 'respuesta', 'enviado', 'respondio', NULL)",
                    (p0["id"], p0["nombre"], p0["telefono"], (texto or "")[:2000]),
                )
                conn.commit()
            return "registrada"
        except Exception as e:
            log_error(f"_registrar_respuesta({telefono})", e)
            return "error"

    def _registrar_baja(self, telefono: str) -> None:
        match = _TEL_MATCH.format(col="telefono")
        try:
            with conectar() as conn, conn.cursor() as cur:
                pacientes = self._pacientes_con_tel(cur, telefono)
                if not pacientes:
                    return
                for p in pacientes:
                    cur.execute(f"UPDATE {p['tabla']} SET whatsapp_opt_out = 1 WHERE {match}", (telefono,))
                    cur.execute(
                        "UPDATE log_envios SET respuesta = 'baja'"
                        " WHERE paciente_id = %s ORDER BY id DESC LIMIT 1",
                        (p["id"],),
                    )
                conn.commit()
        except Exception as e:
            log_error(f"_registrar_baja({telefono})", e)

    def _actualizar_estado(self, message_id: str, estado: str, detalle_error: str | None = None) -> None:
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
                            "UPDATE log_envios SET estado_envio = 'error',"
                            " descripcion_error = COALESCE(%s, descripcion_error)"
                            " WHERE whatsapp_message_id = %s", (detalle_error[:255] if detalle_error else None, message_id)
                        )
                    conn.commit()
            except Exception as e:
                log_error(f"_actualizar_estado({ambiente}, msg={message_id}, estado={estado})", e)
                continue

    @staticmethod
    def _es_baja(texto: str) -> bool:
        """True si el mensaje del paciente pide dejar de recibir mensajes."""
        t = re.sub(r"[^\wáéíóúñ\s]", " ", (texto or "").lower())
        t = re.sub(r"\s+", " ", t).strip()
        if not t:
            return False
        if t in BAJA_EXACTAS:
            return True
        if set(t.split()) & set(BAJA_PALABRAS):
            return True
        return any(f in t for f in BAJA_FRASES)


class WebhookHandler:
    """Capa delgada que une el router HTTP con el procesador de eventos."""

    def __init__(self):
        self.servicio = WhatsAppService()

    def verificar(self, mode: str, verify_token: str, challenge: str):
        return self.servicio.verificar_webhook(mode, verify_token, challenge)

    def procesar(self, payload: dict) -> list[str]:
        return self.servicio.procesar_evento(payload)