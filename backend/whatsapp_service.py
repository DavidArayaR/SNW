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
from datetime import datetime

import httpx

from db import conectar, config_get, log_error, tabla_pacientes
import servicio_especialidades
from wa_rate_limit import (
    gobernador, es_error_throttle, clasificar_error, reintentos_throttle, TOPE_ESPERA_S,
)

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
    "no me manden", "no me envien","no me envíen", "no enviar mas", "dejen de enviar",
    "dejar de recibir", "dejen de mandar", "no molestar", "no me contacten",
    "ya no quiero", "no quiero saber", "borrame", "borrenme", "borrarme",
    "sacame de", "sacarme de", "no quiero que me escriban",
    "quitar de la lista", "sacar de la lista", "borrar mi numero", "eliminar mi numero",
    "detener promociones", "detener promo", "stop promotions", "no promociones",
    "cancelar suscripcion", "cancelar suscripción",
)

# Mensajes que niegan interés en la oferta puntual ("no me interesa", "no
# estoy interesada"…). A diferencia de una baja, el paciente sigue pudiendo
# recibir futuras plantillas (ver _registrar_no_interes). Si el mismo mensaje
# también encaja como baja explícita (arriba), gana la baja.
NO_INTERES_EXACTAS = (
    "no me interesa", "no interesa", "no interesado", "no interesada",
    "no estoy interesado", "no estoy interesada",
)
NO_INTERES_FRASES = (
    "no me interesa", "no me interesaria", "no me interesaría",
    "no estoy interesad", "no esta interesad", "no está interesad",
    "ya no me interesa", "ya no estoy interesad", "sigo sin interes",
    "no tengo interes", "no tengo interés", "no me llama la atencion",
    "no me llama la atención",
)
# Respaldo: cualquier negación cerca de una forma del verbo "interesar"
# ("no", "nunca", "jamás", "tampoco"… seguido de "interesa/interesado/…").
_RE_NO_INTERESAR = re.compile(
    r"\b(no+|nunca|jamas|jamás|tampoco)\b[^.!?\n]{0,20}"
    r"\binteres(a|an|ada|ado|adas|ados|aria|aría|arme|e|o|ó)\b"
)


def es_mensaje_no_interes(texto: str) -> bool:
    """True si el mensaje niega interés en la oferta puntual (no es una baja:
    el paciente sigue pudiendo recibir futuras plantillas)."""
    t = re.sub(r"[^\wáéíóúñ\s]", " ", (texto or "").lower())
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return False
    if t in NO_INTERES_EXACTAS or any(f in t for f in NO_INTERES_FRASES):
        return True
    return bool(_RE_NO_INTERESAR.search(t))

# Mensajes del paciente que muestran interés / intención positiva. Se comparan
# igual que las de baja (texto en minúsculas, sin signos). Si el mensaje también
# encaja como baja, gana la baja.
INTERES_EXACTAS = (
    "si", "sí", "sip", "ok", "oka", "okey", "okok", "dale", "listo", "bueno",
    "claro", "dale gracias", "confirmo", "confirmar", "confirmado", "acepto",
    "me interesa", "interesa", "interesado", "interesada", "quiero", "perfecto",
    "correcto", "afirmativo", "por supuesto", "obvio", "de una",
)
INTERES_FRASES = (
    "me interesa", "si me interesa", "sí me interesa", "me interesaria",
    "me interesaría", "estoy interesad", "si estoy interesad", "sí estoy interesad",
    "estoy bien interesad", "estoy muy interesad", "estoy super interesad",
    "bien interesad", "muy interesad", "super interesad", "re interesad",
    "quede interesad", "quedé interesad", "sigo interesad", "aun interesad",
    "aún interesad", "todavia interesad", "todavía interesad", "claro que me interesa",
    "obvio que me interesa", "por supuesto me interesa", "me interesa mucho",
    "me interesa bastante", "me interesa harto", "me interesa el",
    "me gustaria", "me gustaría", "quiero saber mas", "quiero saber más",
    "mas informacion", "más información", "mas info", "más info",
    "quiero agendar", "quiero reservar", "quiero una hora", "quiero la hora",
    "necesito una hora", "necesito hora", "puedo agendar", "como agendo",
    "cómo agendo", "donde agendo", "dónde agendo", "quiero confirmar",
    "confirmo mi", "confirmo la", "confirmo asistencia", "confirmo la hora",
    "voy a ir", "si asistire", "si voy", "sí voy", "cuando puedo",
    "cuándo puedo", "me sirve", "si me sirve", "de acuerdo", "esta bien",
    "está bien", "si quiero", "sí quiero", "si porfavor", "si por favor",
    "sí por favor", "quiero mas informacion", "quiero más información",
)

# Palabra de negación como palabra suelta ("no", "nooo", "nunca", "jamás"…):
# el mensaje NO cuenta como interés aunque contenga "interesa/interesado/interesada".
_RE_NEGACION = re.compile(r"^(no+|nop+|nel|nunca|jamas|jamás|tampoco|negativo)$")
_RE_INTERESAR = re.compile(r"interes(a|an|ada|ado|adas|ados|aria|aría|arme|e|o|ó)\b")


def es_mensaje_interes(texto: str) -> bool:
    """True si el mensaje del paciente muestra interés ('me interesa', 'sí',
    'quiero agendar'…). Una negación ('no', 'nunca'…) o una baja lo anulan
    aunque el texto contenga 'interesa'."""
    t = re.sub(r"[^\wáéíóúñ\s]", " ", (texto or "").lower())
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return False
    if any(_RE_NEGACION.match(w) for w in t.split()):
        return False
    if t in BAJA_EXACTAS or any(f in t for f in BAJA_FRASES):
        return False
    if t in INTERES_EXACTAS:
        return True
    if any(f in t for f in INTERES_FRASES):
        return True
    # Cualquier forma del verbo "interesar" (sin negación de por medio) cuenta.
    return bool(_RE_INTERESAR.search(t))


# Estados de entrega que Meta reporta por webhook.
ESTADO_WHATSAPP = {"sent", "delivered", "read", "failed"}

# Callback opcional que main.py registra: se llama con el teléfono del paciente
# cuando el webhook detecta un mensaje de interés, para programar el envío
# automático del mensaje de call center.
al_detectar_interes = None

# Callback opcional que main.py registra: se llama con el teléfono del paciente
# cuando el webhook detecta que se dio de baja (por botón o por texto libre),
# para mandarle el mensaje de despedida.
al_detectar_baja = None

# Callback opcional que main.py registra: se llama con el teléfono del paciente
# cuando escribe estando dado de baja, para reactivarlo y mandarle un mensaje
# de bienvenida de vuelta.
al_detectar_retractacion = None

# Texto exacto de los 3 botones de respuesta rápida que se agregan a toda
# plantilla normal (no de call center). Los presses de estos botones se
# identifican por su texto exacto, ANTES de pasar por los clasificadores de
# texto libre (es_mensaje_interes/_es_baja) — importante porque "no me
# interesa" ya es una de las BAJA_FRASES de arriba.
BOTON_TEXTO_INTERES = "Me interesa"
BOTON_TEXTO_NO_INTERES = "No me interesa"
BOTON_TEXTO_BAJA = "Dar de baja"
BOTONES_RESPUESTA = [
    {"type": "QUICK_REPLY", "text": BOTON_TEXTO_INTERES},
    {"type": "QUICK_REPLY", "text": BOTON_TEXTO_NO_INTERES},
    {"type": "QUICK_REPLY", "text": BOTON_TEXTO_BAJA},
]


def _hoy_es() -> str:
    """Fecha de hoy en formato dd-mm-aaaa, para los mensajes descriptivos de
    interés/no-interés que quedan en el historial del paciente."""
    return datetime.now().strftime("%d-%m-%Y")


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

    async def _peticion(self, metodo: str, url: str, *, json: dict | None = None,
                        params: dict | None = None, throughput: bool = False) -> dict:
        """Hace una llamada a la Graph API respetando los límites de Meta.

        - Antes de cada llamada espera lo que sugiera el gobernador (consumo de
          cuota que Meta reportó en las cabeceras anteriores) y, si `throughput`,
          también un turno para no pasarse de `wa_throughput_mps` msg/s.
        - Ante un throttle GLOBAL (HTTP 429 / códigos 4, 17, 32, 613, 80007,
          80008, 130429, 131048, 131057, 368) espera el tiempo de recuperación
          que indica Meta y reintenta hasta `wa_rate_limit_reintentos` veces.
        - Ante un límite POR DESTINATARIO (131056, 131049, 130497) no reintenta
          ni frena al resto: falla solo ese mensaje.
        """
        reintentos = reintentos_throttle()
        async with httpx.AsyncClient(timeout=30) as cliente:
            for intento in range(reintentos + 1):
                espera = gobernador.pausa_antes_de_enviar()
                if espera > 0:
                    await asyncio.sleep(min(espera, TOPE_ESPERA_S))
                if throughput:
                    turno = gobernador.reservar_turno_throughput()
                    if turno > 0:
                        await asyncio.sleep(turno)
                resp = await cliente.request(metodo, url, json=json, params=params,
                                             headers=self._headers())
                gobernador.registrar_respuesta(resp.headers)
                if resp.status_code in (200, 201):
                    return resp.json()

                err = {}
                try:
                    err = resp.json().get("error", {}) or {}
                except Exception:
                    pass
                tipo_limite = clasificar_error(resp.status_code, err.get("code"),
                                               err.get("error_subcode"))
                if tipo_limite == "global":
                    segundos = gobernador.registrar_error(
                        err.get("code"), err.get("error_subcode"),
                        resp.headers, resp.headers.get("retry-after"))
                    if intento < reintentos:
                        log_error(f"Meta rate limit ({metodo} {url}, código {err.get('code')}): "
                                  f"espera {int(segundos)} s y reintenta ({intento + 1}/{reintentos})")
                        await asyncio.sleep(min(segundos, TOPE_ESPERA_S))
                        continue
                return self._procesar_respuesta(resp)   # levanta ErrorWhatsApp
        return self._procesar_respuesta(resp)

    async def enviar(self, payload: dict) -> dict:
        url = f"{graph_url()}/{self.phone_number_id}/messages"
        return await self._peticion("POST", url, json=payload, throughput=True)

    async def crear_template(self, waba_id: str, payload: dict) -> dict:
        """Crea un template de mensaje en Meta (POST /{waba_id}/message_templates)."""
        url = f"{graph_url()}/{waba_id}/message_templates"
        return await self._peticion("POST", url, json=payload)

    async def editar_template(self, template_id: str, payload: dict) -> dict:
        """Edita un template EXISTENTE en Meta (POST /{template_id}).

        A diferencia de crear_template, este endpoint se identifica por el ID
        propio del template (no por el waba_id) y no acepta 'name' ni
        'language': esos dos campos son inmutables una vez creado el template.
        """
        url = f"{graph_url()}/{template_id}"
        return await self._peticion("POST", url, json=payload)

    async def obtener_template(self, template_id: str) -> dict:
        """Obtiene los datos actuales de un template por su propio ID."""
        url = f"{graph_url()}/{template_id}"
        return await self._peticion("GET", url, params={"fields": "category,status,name,language"})

    async def listar_templates(self, waba_id: str) -> dict:
        """Lista los templates de mensaje de la WABA, con paginación."""
        url = f"{graph_url()}/{waba_id}/message_templates"
        params: dict | None = {
            "fields": "id,name,language,status,category,components,rejected_reason",
            "limit": 100,
        }
        templates: list = []
        while url:
            data = await self._peticion("GET", url, params=params)
            templates += data.get("data", [])
            url = (data.get("paging") or {}).get("next")
            params = None  # la URL "next" ya trae todos los query params
        return {"data": templates}

    async def buscar_template(self, waba_id: str, nombre: str) -> dict:
        """Busca un template por nombre (puede devolver varias variantes de idioma)."""
        url = f"{graph_url()}/{waba_id}/message_templates"
        return await self._peticion("GET", url, params={"name": nombre})

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
        return await self._peticion("DELETE", url, params=params)

    def _procesar_respuesta(self, resp) -> dict:
        if resp.status_code in (200, 201):
            return resp.json()
        detalle = ""
        subcodigo = None
        try:
            err = resp.json().get("error", {})
            detalle = (err.get("error_user_msg")
                       or err.get("error_user_title")
                       or err.get("message")
                       or json_short(resp.text))
            codigo_msg = err.get("code", resp.status_code)
            subcodigo = err.get("error_subcode")
            tipo = err.get("type", "permanent")
        except Exception as e:
            log_error(f"_procesar_respuesta: no se pudo parsear el error de Meta (HTTP {resp.status_code})", e)
            err = {}
            codigo_msg = resp.status_code
            tipo = "permanent"
            detalle = json_short(resp.text)
        # Códigos 4xx con "temporales" y los de rate limit se tratan como transitorios.
        es_temporal = (tipo.lower() == "transient"
                       or resp.status_code in (429, 5)
                       or es_error_throttle(resp.status_code, codigo_msg, subcodigo))
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
    }

    def convertir_texto_meta(self, texto: str) -> tuple[str, list[str]]:
        """Convierte los comodines {nombre}/{apellido} del sistema
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

        components = [componente_body]
        if category != "AUTHENTICATION":
            components.append({"type": "BUTTONS", "buttons": BOTONES_RESPUESTA})

        payload = {
            "name": nombre,
            "category": category,
            "language": lang,
            "components": components,
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

        components = [componente_body]
        if categoria_real != "AUTHENTICATION":
            components.append({"type": "BUTTONS", "buttons": BOTONES_RESPUESTA})

        payload = {"category": categoria_real, "components": components}
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
                payload_sin_categoria = {"components": components}
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

    def construir_payload_cta_url(self, telefono: str, mensaje: str,
                                  boton_texto: str, url: str) -> tuple:
        """Payload de mensaje interactivo con un botón que abre una URL
        (válido dentro de la ventana de 24 h). Meta: interactive / cta_url."""
        numero = telefono.lstrip("+")
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": numero,
            "type": "interactive",
            "interactive": {
                "type": "cta_url",
                "body": {"text": (mensaje or "")[:1024]},
                "action": {
                    "name": "cta_url",
                    "parameters": {
                        "display_text": (boton_texto or "Abrir")[:20],
                        "url": url,
                    },
                },
            },
        }
        return payload, "cta_url"

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
        """Devuelve el orden de aparición de los comodines {nombre}/{apellido}
        en el texto, para construir los parámetros del template en el orden correcto."""
        orden: list[str] = []
        for m in re.finditer(r"\{([a-z_]+)\}", texto or ""):
            clave = m.group(1)
            if clave in ("nombre", "apellido") and clave not in orden:
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
        cta = msg.get("cta") if isinstance(msg, dict) else None

        if nombre_template:
            orden = self.extraer_orden_comodines(msg.get("texto", "") or "")
            vdict = variables or {}
            valores = [vdict.get(clave, "") or "" for clave in orden]
            payload_, _ = self.construir_payload_template(
                telefono, nombre_template, idioma, variables=valores,
            )
        elif cta and cta.get("url"):
            payload_, _ = self.construir_payload_cta_url(
                telefono, mensaje, cta.get("texto") or "Abrir", cta["url"],
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
        """Mensajes entrantes del cliente (respuestas, bajas, etc.).

        Los avisos de reactivación ("bienvenido de vuelta") y de despedida se
        deciden al final, con el estado opt-out con que termina el evento, no
        mensaje a mensaje: así un paciente ya de baja que escribe de nuevo una
        baja (o una retractación seguida de una baja en el mismo evento) no
        recibe "bienvenido" + "lamentamos", sino ninguna respuesta."""
        acciones = []
        # Meta manda el wa_id sin '+' (ej. "56993921740"); lo normalizamos.
        crudo = (valor.get("contacts") or [{}])[0].get("wa_id", "")
        telefono = _normalizar_telefono(crudo) or crudo
        opt_out_inicial = self._estaba_opt_out(telefono)
        hubo_retract = False
        hubo_baja = False
        hubo_interes = False
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
            cuerpo = texto or extra

            # Los 3 botones de la plantilla normal se identifican por su texto
            # exacto; el resto pasa por los clasificadores de texto libre.
            es_boton = tipo in ("button", "interactive")
            boton = (texto or "").strip().casefold()
            interes = es_mensaje_interes(cuerpo)
            baja = self._es_baja(texto) or self._es_baja(extra)
            fue_interes = interes or (es_boton and boton == BOTON_TEXTO_INTERES.casefold())
            fue_baja = baja or (es_boton and boton == BOTON_TEXTO_BAJA.casefold())
            # "no me interesa" / "no estoy interesada"…: NO es una baja, el
            # paciente sigue pudiendo recibir futuras plantillas. Una baja
            # explícita en el mismo mensaje gana sobre esto.
            no_interes = (not interes) and (not baja) and (
                es_mensaje_no_interes(texto) or es_mensaje_no_interes(extra)
            )

            # Si el paciente escribe estando dado de baja (sea explícita o
            # puesta a mano), se le reactivan las notificaciones antes de
            # clasificar este mismo mensaje. El aviso se decide al final del
            # evento: si este termina de nuevo de baja, no sale ninguna
            # respuesta (la baja pesa más que la reactivación inmediata).
            estaba_opt_out = self._estaba_opt_out(telefono)
            if estaba_opt_out and not fue_interes and not fue_baja:
                self._registrar_retractacion(telefono, aviso=False)
                hubo_retract = True
                acciones.append("retractacion")

            info = self._registrar_respuesta(telefono, cuerpo, mensaje.get("timestamp"))
            acciones.append(f"respuesta_{info}")

            if es_boton and boton == BOTON_TEXTO_INTERES.casefold():
                self._registrar_interes(telefono)
                hubo_interes = True
                acciones.append("interes_boton")
                if callable(al_detectar_interes):
                    try:
                        al_detectar_interes(telefono)
                    except Exception as e:
                        log_error(f"al_detectar_interes({telefono})", e)
                continue
            if es_boton and boton == BOTON_TEXTO_NO_INTERES.casefold():
                self._registrar_no_interes(telefono)
                acciones.append("no_interes_boton")
                continue
            if es_boton and boton == BOTON_TEXTO_BAJA.casefold():
                self._registrar_baja(telefono, aviso=False)
                hubo_baja = True
                acciones.append("baja_boton")
                continue

            if interes:
                # El interés manda: si el paciente se había dado de baja y ahora
                # dice que le interesa, se revierte la baja.
                self._registrar_interes(telefono)
                hubo_interes = True
                acciones.append("interes")
                if callable(al_detectar_interes):
                    try:
                        al_detectar_interes(telefono)
                    except Exception as e:
                        log_error(f"al_detectar_interes({telefono})", e)
            elif baja:
                self._registrar_baja(telefono, aviso=False)
                hubo_baja = True
                acciones.append("baja")
            elif no_interes:
                self._registrar_no_interes(telefono)
                acciones.append("no_interes")

        # Avisos decididos con el estado opt-out FINAL del evento:
        #  - termina de baja habiéndolo estado al inicio   -> nada (baja repetida).
        #  - termina de baja sin estarlo al inicio         -> despedida (baja nueva).
        #  - termina reactivado tras una retractación      -> "bienvenido de vuelta".
        #  - algún interés en el evento                    -> solo la respuesta de
        #    call center (ya programada arriba), sin avisos.
        opt_out_final = self._estaba_opt_out(telefono)
        if opt_out_final:
            if not opt_out_inicial and hubo_baja:
                if callable(al_detectar_baja):
                    try:
                        al_detectar_baja(telefono)
                    except Exception as e:
                        log_error(f"al_detectar_baja({telefono})", e)
        elif hubo_retract and not hubo_interes:
            if callable(al_detectar_retractacion):
                try:
                    al_detectar_retractacion(telefono)
                except Exception as e:
                    log_error(f"al_detectar_retractacion({telefono})", e)
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
    @staticmethod
    def _tablas_pac() -> tuple:
        """Tablas legacy (dev + prod) más las de especialidades registradas."""
        base = (tabla_pacientes("desarrollo"), tabla_pacientes("produccion"))
        try:
            extra = tuple(r["nombre_tabla_base"]
                          for r in servicio_especialidades.listar_tablas_especialidades())
        except Exception as e:
            log_error("_tablas_pac: especialidades", e)
            extra = ()
        return base + extra

    @staticmethod
    def _cond_tabla(tabla: str, alias: str = "") -> tuple[str, tuple]:
        """Aísla filas de log_envios por tabla de origen (ver main.py)."""
        pref = f"{alias}." if alias else ""
        if tabla in ("pacientes_dev", "pacientes_prod"):
            return f"AND COALESCE({pref}tabla_pacientes, '') IN ('', %s)", (tabla,)
        return f"AND {pref}tabla_pacientes = %s", (tabla,)

    def _pacientes_con_tel(self, cur, telefono: str) -> list[dict]:
        """Filas (id, nombre, telefono, whatsapp_opt_out, tabla) de los
        pacientes cuyo teléfono coincide, en las tablas legacy y en las de
        especialidades. log_envios es compartida."""
        match = _TEL_MATCH.format(col="telefono")
        hallados = []
        for t in self._tablas_pac():
            try:
                cur.execute(
                    f"SELECT id, nombre, telefono, whatsapp_opt_out FROM {t} WHERE {match} LIMIT 1",
                    (telefono,),
                )
            except Exception:
                continue  # tabla aún no creada o sin la columna
            row = cur.fetchone()
            if row:
                row["tabla"] = t
                hallados.append(row)
        return hallados

    def _estaba_opt_out(self, telefono: str) -> bool:
        try:
            with conectar() as conn, conn.cursor() as cur:
                pacientes = self._pacientes_con_tel(cur, telefono)
                return any(p.get("whatsapp_opt_out") for p in pacientes)
        except Exception as e:
            log_error(f"_estaba_opt_out({telefono})", e)
            return False

    def _ultima_plantilla_ofertada(self, cur, paciente_id, tabla=None) -> str | None:
        """Clave de la última plantilla normal (no de sistema) que se le envió
        realmente al paciente; sirve para registrar a qué oferta respondió."""
        cond_t, args_t = self._cond_tabla(tabla, "") if tabla else ("", ())
        cur.execute(
            "SELECT plantilla_clave FROM log_envios WHERE paciente_id = %s"
            f"  {cond_t}"
            "  AND estado_envio = 'enviado'"
            "  AND COALESCE(plantilla_clave, '') NOT IN"
            "      ('respuesta', 'ajuste_manual', 'call_center', 'interes_boton',"
            "       'no_interes_boton', 'baja_aviso', 'retractacion_aviso')"
            " ORDER BY id DESC LIMIT 1",
            (paciente_id, *args_t),
        )
        fila = cur.fetchone()
        return (fila or {}).get("plantilla_clave") or None

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
                esp0 = servicio_especialidades.especialidad_por_tabla(p0["tabla"])
                cur.execute(
                    "INSERT INTO log_envios (envio_id, paciente_id, nombre_paciente, numero_telefono,"
                    " mensaje, plantilla_clave, estado_envio, respuesta, descripcion_error,"
                    " especialidad_id, tabla_pacientes)"
                    " VALUES (NULL, %s, %s, %s, %s, 'respuesta', 'enviado', 'respondio', NULL, %s, %s)",
                    (p0["id"], p0["nombre"], p0["telefono"], (texto or "")[:2000],
                     esp0["id"] if esp0 else None, p0["tabla"] if esp0 else None),
                )
                conn.commit()
            return "registrada"
        except Exception as e:
            log_error(f"_registrar_respuesta({telefono})", e)
            return "error"

    def _reintegro_reciente(self, cur, p: dict) -> bool:
        """True si el paciente se reintegró (una retractación o interés volvió
        a activar sus notificaciones) hace menos de 24 h. Se usa para impedir
        la alternancia baja -> reintegración repetida: el paciente que se dio
        de baja y volvió no puede darse de baja otra vez hasta que pasen 24 h."""
        try:
            cur.execute(f"SELECT ultimo_reintegro FROM {p['tabla']} WHERE id = %s", (p["id"],))
            ultimo = (cur.fetchone() or {}).get("ultimo_reintegro")
        except Exception:
            return False  # esquema sin la columna: nunca bloquea
        if not ultimo:
            return False
        cur.execute("SELECT %s >= (NOW() - INTERVAL 24 HOUR) AS reciente", (ultimo,))
        return bool((cur.fetchone() or {}).get("reciente"))

    def _bloquea_flip_flop(self, cur, p: dict) -> bool:
        """Anti flip-flop: en producción y en especialidades SIEMPRE; en la
        base de desarrollo legacy solo si la opción `anti_flip_flop_dev` está
        activa (desactivarla permite probar el flujo sin límite en desarrollo)."""
        if p["tabla"] == tabla_pacientes("desarrollo"):
            valor = str(config_get("anti_flip_flop_dev", "true")).strip().lower()
            if valor not in ("true", "1", "on", "si", "sí"):
                return False
        return self._reintegro_reciente(cur, p)

    def _registrar_baja(self, telefono: str, aviso: bool | None = None) -> None:
        match = _TEL_MATCH.format(col="telefono")
        try:
            with conectar() as conn, conn.cursor() as cur:
                pacientes = self._pacientes_con_tel(cur, telefono)
                if not pacientes:
                    return
                # Si ya estaba de baja, esto es un webhook repetido (Meta
                # garantiza entrega "al menos una vez", no exactamente una) o
                # una baja repetida — no es una baja nueva, así que por defecto
                # no se reenvía el aviso de despedida.
                ya_de_baja = all(p.get("whatsapp_opt_out") for p in pacientes)
                for p in pacientes:
                    # Anti flip-flop: si el paciente apenas se reintegró (últimas
                    # 24 h), no puede darse de baja otra vez. Solo una baja por
                    # cada 24 h desde su último regreso.
                    if self._bloquea_flip_flop(cur, p):
                        log_error(
                            f"_registrar_baja({telefono}): baja ignorada, se reintegró"
                            f" hace menos de 24 h (tabla {p['tabla']})"
                        )
                        continue
                    cur.execute(f"UPDATE {p['tabla']} SET whatsapp_opt_out = 1 WHERE {match}", (telefono,))
                    # Baja pedida con sus propias palabras por WhatsApp: queda
                    # bloqueada para edición manual hasta que el paciente se
                    # retracte (ver _registrar_interes / _registrar_retractacion).
                    try:
                        cur.execute(f"UPDATE {p['tabla']} SET opt_out_explicito = 1 WHERE {match}", (telefono,))
                    except Exception:
                        pass  # esquema sin la columna
                    # Al darse de baja se quita la marca de interés (si la tenía).
                    try:
                        cur.execute(f"UPDATE {p['tabla']} SET interesado = 0, no_interesado = 0 WHERE {match}", (telefono,))
                    except Exception:
                        pass  # esquema sin la columna
                    cond_t, args_t = self._cond_tabla(p["tabla"])
                    cur.execute(
                        "UPDATE log_envios SET respuesta = 'baja'"
                        f" WHERE paciente_id = %s {cond_t} ORDER BY id DESC LIMIT 1",
                        (p["id"], *args_t),
                    )
                conn.commit()
        except Exception as e:
            log_error(f"_registrar_baja({telefono})", e)
            return
        if aviso is True:
            enviar = True
        elif aviso is False:
            enviar = False
        else:
            enviar = not ya_de_baja
        if enviar and callable(al_detectar_baja):
            try:
                al_detectar_baja(telefono)
            except Exception as e:
                log_error(f"al_detectar_baja({telefono})", e)

    def _registrar_retractacion(self, telefono: str, aviso: bool | None = None) -> None:
        """El paciente escribe estando dado de baja: se reactivan sus
        notificaciones (sin tocar interesado/no_interesado), sin importar si
        la baja fue explícita o puesta a mano por un admin/dev."""
        match = _TEL_MATCH.format(col="telefono")
        try:
            with conectar() as conn, conn.cursor() as cur:
                pacientes = self._pacientes_con_tel(cur, telefono)
                if not pacientes:
                    return
                # Igual que en _registrar_baja: si nadie estaba de baja, esto
                # es un webhook repetido o un segundo mensaje del mismo
                # paciente — no hay nada que reactivar ni aviso que reenviar.
                nadie_de_baja = not any(p.get("whatsapp_opt_out") for p in pacientes)
                for p in pacientes:
                    cur.execute(f"UPDATE {p['tabla']} SET whatsapp_opt_out = 0 WHERE {match}", (telefono,))
                    try:
                        cur.execute(f"UPDATE {p['tabla']} SET opt_out_explicito = 0 WHERE {match}", (telefono,))
                    except Exception:
                        pass  # esquema sin la columna
                    # Marca la fecha del regreso: da pie al anti flip-flop de
                    # 24 h (producción siempre; desarrollo según la opción). Solo
                    # en la fila que realmente estaba de baja.
                    if p.get("whatsapp_opt_out"):
                        try:
                            cur.execute(f"UPDATE {p['tabla']} SET ultimo_reintegro = NOW() WHERE {match}", (telefono,))
                        except Exception:
                            pass  # esquema sin la columna
                    cur.execute(
                        "UPDATE log_envios SET respuesta = 'respondio'"
                        f" WHERE paciente_id = %s AND respuesta = 'baja' {self._cond_tabla(p['tabla'])[0]}",
                        (p["id"], *self._cond_tabla(p["tabla"])[1]),
                    )
                conn.commit()
        except Exception as e:
            log_error(f"_registrar_retractacion({telefono})", e)
            return
        if aviso is True:
            enviar = True
        elif aviso is False:
            enviar = False
        else:
            enviar = not nadie_de_baja
        if enviar and callable(al_detectar_retractacion):
            try:
                al_detectar_retractacion(telefono)
            except Exception as e:
                log_error(f"al_detectar_retractacion({telefono})", e)

    def _registrar_interes(self, telefono: str) -> None:
        """Marca al paciente como interesado y revierte cualquier baja previa
        (opt-out, corrección manual y señal 'pegajosa' de baja en el historial).
        Deja registrado a qué oferta/plantilla respondió, para el historial y
        para volver a poder mandarle plantillas al cabo de una semana."""
        match = _TEL_MATCH.format(col="telefono")
        try:
            with conectar() as conn, conn.cursor() as cur:
                pacientes = self._pacientes_con_tel(cur, telefono)
                if not pacientes:
                    return
                for p in pacientes:
                    clave_oferta = self._ultima_plantilla_ofertada(cur, p["id"], p["tabla"])
                    venia_de_baja = bool(p.get("whatsapp_opt_out"))
                    for sql, params in (
                        (f"UPDATE {p['tabla']} SET interesado = 1, no_interesado = 0 WHERE {match}", (telefono,)),
                        (f"UPDATE {p['tabla']} SET whatsapp_opt_out = 0 WHERE {match}", (telefono,)),
                        # El propio paciente se retracta: se libera el bloqueo.
                        (f"UPDATE {p['tabla']} SET opt_out_explicito = 0 WHERE {match}", (telefono,)),
                        (f"UPDATE {p['tabla']} SET interes_plantilla_clave = %s, interes_fecha = NOW() WHERE {match}",
                         (clave_oferta, telefono)),
                    ):
                        try:
                            cur.execute(sql, params)
                        except Exception:
                            pass  # esquema sin esa columna
                    # Anti flip-flop: solo cuenta como "regreso" si el paciente
                    # realmente venía de baja; un interés de alguien que nunca se
                    # dio de baja no le impide luego darse de baja.
                    if venia_de_baja:
                        try:
                            cur.execute(f"UPDATE {p['tabla']} SET ultimo_reintegro = NOW() WHERE {match}", (telefono,))
                        except Exception:
                            pass  # esquema sin la columna
                    # Anula la señal 'pegajosa' de baja del historial del paciente.
                    cond_t, args_t = self._cond_tabla(p["tabla"])
                    cur.execute(
                        "UPDATE log_envios SET respuesta = 'respondio'"
                        f" WHERE paciente_id = %s AND respuesta = 'baja' {cond_t}",
                        (p["id"], *args_t),
                    )
                # Una sola fila descriptiva (log_envios no distingue ambiente;
                # mismo criterio que _registrar_respuesta, que también solo
                # inserta con los datos del primer paciente encontrado).
                p0 = pacientes[0]
                clave_oferta0 = self._ultima_plantilla_ofertada(cur, p0["id"], p0["tabla"])
                esp0 = servicio_especialidades.especialidad_por_tabla(p0["tabla"])
                cur.execute(
                    "INSERT INTO log_envios (envio_id, paciente_id, nombre_paciente, numero_telefono,"
                    " mensaje, plantilla_clave, estado_envio, respuesta, descripcion_error,"
                    " especialidad_id, tabla_pacientes)"
                    " VALUES (NULL, %s, %s, %s, %s, 'interes_boton', 'enviado', 'respondio', NULL, %s, %s)",
                    (p0["id"], p0["nombre"], p0["telefono"],
                     f"Interesado en la oferta del {_hoy_es()}, plantilla '{clave_oferta0 or '—'}'.",
                     esp0["id"] if esp0 else None, p0["tabla"] if esp0 else None),
                )
                conn.commit()
        except Exception as e:
            log_error(f"_registrar_interes({telefono})", e)

    def _registrar_no_interes(self, telefono: str) -> None:
        """El paciente indicó (botón 'No me interesa') que esta oferta puntual
        no le interesa: a diferencia de una baja, sigue pudiendo recibir
        futuras plantillas (al cabo de una semana)."""
        match = _TEL_MATCH.format(col="telefono")
        try:
            with conectar() as conn, conn.cursor() as cur:
                pacientes = self._pacientes_con_tel(cur, telefono)
                if not pacientes:
                    return
                for p in pacientes:
                    clave_oferta = self._ultima_plantilla_ofertada(cur, p["id"], p["tabla"])
                    for sql, params in (
                        (f"UPDATE {p['tabla']} SET no_interesado = 1, interesado = 0 WHERE {match}", (telefono,)),
                        (f"UPDATE {p['tabla']} SET interes_plantilla_clave = %s, interes_fecha = NOW() WHERE {match}",
                         (clave_oferta, telefono)),
                    ):
                        try:
                            cur.execute(sql, params)
                        except Exception:
                            pass  # esquema sin esa columna
                # Una sola fila descriptiva (log_envios no distingue ambiente;
                # mismo criterio que _registrar_respuesta).
                p0 = pacientes[0]
                clave_oferta0 = self._ultima_plantilla_ofertada(cur, p0["id"], p0["tabla"])
                esp0 = servicio_especialidades.especialidad_por_tabla(p0["tabla"])
                cur.execute(
                    "INSERT INTO log_envios (envio_id, paciente_id, nombre_paciente, numero_telefono,"
                    " mensaje, plantilla_clave, estado_envio, respuesta, descripcion_error,"
                    " especialidad_id, tabla_pacientes)"
                    " VALUES (NULL, %s, %s, %s, %s, 'no_interes_boton', 'enviado', 'respondio', NULL, %s, %s)",
                    (p0["id"], p0["nombre"], p0["telefono"],
                     f"No interesado en la oferta del {_hoy_es()}, plantilla '{clave_oferta0 or '—'}'.",
                     esp0["id"] if esp0 else None, p0["tabla"] if esp0 else None),
                )
                conn.commit()
        except Exception as e:
            log_error(f"_registrar_no_interes({telefono})", e)

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
