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
import json
import random
import re
import threading
import time
import unicodedata
from datetime import datetime

import httpx

from db import conectar, config_all, config_get, log_error, tabla_pacientes
from whatsapp_channels import (CENTROS, EMISOR_WEBHOOK, catalogo_centros,
                               credenciales_centro, listar_canales, resolver_phone_id)
from whatsapp_channel_verification import verificar_canal_meta
from corpus_respuestas import guardar_mensaje_completo
from intenciones_call_center import clasificar, patrones_configurados
from plantilla_media import obtener_media, ruta_media
from plantilla_revision import (botones_para_meta, presentar_plantilla,
                               variables_del_cuerpo, version_opciones)
from supresion import (esta_suprimido, es_exento, es_numero_prueba, solicitar_eliminacion,
                      oferta_eliminacion_enviada)
import servicio_areas
from wa_rate_limit import (
    gobernador, es_error_throttle, clasificar_error, reintentos_throttle, TOPE_ESPERA_S,
)
from wa_dispatch_limit import reservar as reservar_despacho, bloquear as bloquear_despacho
from wa_dispatch_limit import aplazar_pareja, politica as politica_despacho, registrar_aceptado

# Versión de la Graph API de Meta. Configurable en la tabla `configuracion`
# (clave `wa_graph_version`); si no está, se usa este valor por defecto.
GRAPH_VERSION_DEFAULT = "v26.0"
_media_cache: dict[tuple[str, str], tuple[str, float]] = {}
_media_cache_lock = threading.Lock()
_template_aprobada_cache: dict[tuple[str, str, str, str], float] = {}
_template_aprobada_lock = threading.Lock()


def graph_version() -> str:
    return (config_get("wa_graph_version") or GRAPH_VERSION_DEFAULT).strip() or GRAPH_VERSION_DEFAULT


def graph_url() -> str:
    """Base de la Graph API: https://graph.facebook.com/<version>"""
    return f"https://graph.facebook.com/{graph_version()}"


# Palabras que determinan opt-out (baja) según políticas de WhatsApp.
# Detección de baja (opt-out). Se compara sobre el texto en minúsculas y sin
# signos. `BAJA_EXACTAS`: solo si el mensaje ES exactamente eso.
# `BAJA_PALABRAS`: si aparece como palabra suelta. `BAJA_FRASES`: como substring.
BAJA_EXACTAS = ("stop", "baja", "cancelar", "cancelo", "salir",
                "quit", "unsubscribe", "eliminar", "remover", "detener")
BAJA_PALABRAS = ("baja", "stop", "unsubscribe", "cancelar", "cancelo", "detener")
BAJA_FRASES = (
    "dar de baja", "darme de baja", "darse de baja", "me doy de baja", "de baja",
    "quiero darme de baja", "quiero que me den de baja", "deseo darme de baja",
    "no quiero recibir", "no deseo recibir", "no quiero mas mensajes",
    "no me manden", "no me envien","no me envíen", "no me escriban",
    "dejen de escribirme", "no enviar mas", "dejen de enviar",
    "dejar de recibir", "dejen de mandar", "no molestar", "no me contacten",
    "borrame", "borrenme", "borrarme",
    "sacame de", "sacarme de", "no quiero que me escriban",
    "quitar de la lista", "sacar de la lista", "borrar mi numero", "eliminar mi numero",
    "eliminar mis datos", "borra mis datos", "borrar mis datos",
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
    "no me llama la atención", "no gracias", "por ahora no",
    "no por el momento", "gracias pero no", "ya me atendi", "ya me atendí",
    "no necesito", "ya tengo doctor", "ya tengo hora", "ya consegui hora",
    "ya conseguí hora", "no quiero agendar", "en otra oportunidad",
    "no x ahora", "no grax", "estoy bien gracias",
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
    "me interesa", "interesa", "interesado", "interesada",
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
    "quiero pedir una horita", "kiero hora", "quiero reservar", "quiero una cita",
    "me pueden llamar", "necesito que me contacten", "quiero una evaluacion",
    "quiero una evaluación", "quiero atenderme", "quisiera atenderme",
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


def etiquetas_automaticas(texto: str, clasificacion=None) -> tuple[str, ...]:
    """Sugiere etiquetas locales; no ejecuta acciones ni modifica al paciente."""
    c = clasificacion or clasificar(texto)
    if c.automatizacion != "SIN_EVIDENCIA_AUTOMATIZACION":
        return (("respuesta_automatica", "requiere_revision")
                if c.automatizacion == "AUTOMATICO_PROBABLE"
                else ("respuesta_automatica",))
    baja = WhatsAppService._es_baja(texto)
    if c.cancela and any(f in (texto or "").casefold()
                         for f in ("cancelar mi hora", "cancelo mi hora")):
        if not any(f in (texto or "").casefold() for f in BAJA_FRASES):
            baja = False
    if baja:
        return ("baja_solicitada", "lenguaje_ofensivo") if c.ofensa.categoria == "INSULTO_DIRECTO" else ("baja_solicitada",)
    etiquetas = []
    if c.ofensa.categoria in ("INSULTO_DIRECTO", "LENGUAJE_OFENSIVO"):
        etiquetas.append("lenguaje_ofensivo")
    if es_mensaje_no_interes(texto):
        etiquetas.append("desinteres")
    if c.cancela:
        etiquetas.append("cancelacion")
    elif c.confirma:
        etiquetas.append("confirmacion")
    if c.derivar:
        if any(m != "CONSULTA_AMBIGUA" for m in c.motivos):
            etiquetas.append("consulta")
        else:
            etiquetas.append("requiere_revision")
    if not (c.confirma or c.cancela) and es_mensaje_interes(texto):
        etiquetas.append("interes")
    if (c.ofensa.categoria == "INSULTO_DIRECTO" and not c.derivar
            and not es_mensaje_interes(texto) and "desinteres" not in etiquetas):
        etiquetas.append("desinteres")
    return tuple(dict.fromkeys(etiquetas or ["requiere_revision"]))


# Estados de entrega que Meta reporta por webhook.
ESTADO_WHATSAPP = {"sent", "delivered", "read", "failed"}

# Callback opcional que main.py registra: se llama con el teléfono del paciente
# cuando el webhook detecta un mensaje de interés, para programar el envío
# automático del mensaje de call center.
al_detectar_interes = None

# Callback registrado por main.py: persiste una derivación idempotente y
# prepara el botón hacia el número externo del call center.
al_detectar_consulta = None

# Callback opcional que main.py registra: se llama con el teléfono del paciente
# cuando el webhook detecta que se dio de baja (por botón o por texto libre),
# para mandarle el mensaje de despedida.
al_detectar_baja = None

# Callback opcional que main.py registra: se llama con el teléfono del paciente
# cuando escribe estando dado de baja, para reactivarlo y mandarle un mensaje
# de bienvenida de vuelta.
al_detectar_retractacion = None
al_detectar_insulto = None
al_derivar_crm = None
al_respuesta_reagendar = None
al_mostrar_opciones = None
al_responder_opcion = None

# Texto exacto de los 3 botones de respuesta rápida que se agregan a toda
# plantilla normal (no de call center). Los presses de estos botones se
# identifican por su texto exacto, ANTES de pasar por los clasificadores de
# texto libre (es_mensaje_interes/_es_baja) — importante porque "no me
# interesa" ya es una de las BAJA_FRASES de arriba.
BOTON_TEXTO_INTERES = "Me interesa"
BOTON_TEXTO_NO_INTERES = "No me interesa"
BOTON_TEXTO_BAJA = "Dar de baja"
BOTON_TEXTO_EJECUTIVA = "Ir al callcenter"
BOTON_TEXTO_EJECUTIVA_ANTERIOR = "Hablar con ejecutiva"
BOTON_ID_EJECUTIVA = "snw_hablar_ejecutiva"
BOTON_ID_BAJA_INSULTO = "snw_dar_de_baja"
ACCIONES_BOTON_TEMPLATE = {
    "interes": "snw_tpl_interes", "desinteres": "snw_tpl_desinteres",
    "baja": "snw_tpl_baja", "consulta": "snw_tpl_consulta",
}
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
# Compara el teléfono ignorando el formato con que haya quedado guardado en
# una base antigua. El valor recibido por Meta ya se normaliza antes de usarlo.
_TEL_MATCH = (
    "REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE({col}, '+', ''), ' ', ''),"
    " '-', ''), '(', ''), ')', ''), '.', '') = "
    "REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(%s, '+', ''), ' ', ''),"
    " '-', ''), '(', ''), ')', ''), '.', '')"
)


def es_solicitud_eliminar(texto: str) -> bool:
    """Acepta mayúsculas, tildes y espacios sin confundir frases más largas."""
    plano = unicodedata.normalize("NFKD", (texto or "").casefold())
    plano = "".join(c for c in plano if not unicodedata.combining(c))
    return re.sub(r"\s+", "", plano.strip(" .!?¡¿")) == "eliminar"


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


class ResultadoEnvioIncierto(Exception):
    """Meta pudo aceptar la solicitud, pero no hay ID fiable para conciliarla."""


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

        - Antes de cada llamada respeta la cuota de Graph API y, para /messages,
          reserva un turno compartido en MySQL por número y destinatario.
        - Ante un throttle GLOBAL (HTTP 429 / códigos 4, 17, 32, 613, 80007,
          80008, 130429, 131048, 131057, 368) espera el tiempo de recuperación
          que indica Meta y reintenta hasta `wa_rate_limit_reintentos` veces.
        - 131056 aplaza esa pareja y reintenta con backoff; 131049 y 130497
          no reintentan ni bloquean otros destinatarios.
        """
        reglas = politica_despacho(self.phone_number_id) if throughput else None
        destinatario = str((json or {}).get("to") or "") if throughput else ""
        reintentos = reglas["max_retries"] if reglas else reintentos_throttle()
        async with httpx.AsyncClient(timeout=30) as cliente:
            for intento in range(reintentos + 1):
                espera = gobernador.pausa_antes_de_enviar()
                if espera > 0:
                    await asyncio.sleep(min(espera, TOPE_ESPERA_S))
                if throughput:
                    while True:
                        turno = await asyncio.to_thread(
                            reservar_despacho, self.phone_number_id, destinatario)
                        if turno <= 0:
                            break
                        await asyncio.sleep(min(turno, 1.0))
                resp = await cliente.request(metodo, url, json=json, params=params,
                                             headers=self._headers())
                gobernador.registrar_respuesta(resp.headers)
                if resp.status_code in (200, 201):
                    if throughput:
                        try:
                            await asyncio.to_thread(registrar_aceptado, self.phone_number_id)
                        except Exception as e:
                            # La respuesta positiva de Meta prevalece: una métrica
                            # caída jamás convierte un envío aceptado en fallido.
                            log_error("wa_dispatch_limit.registrar_aceptado", e)
                    return resp.json()

                err = {}
                try:
                    err = resp.json().get("error", {}) or {}
                except Exception:
                    pass
                tipo_limite = clasificar_error(resp.status_code, err.get("code"),
                                               err.get("error_subcode"))
                codigo = err.get("code")
                if throughput and codigo in (130429, 131057, 131056):
                    base = reglas["backoff_base"] * (2 ** intento)
                    espera_meta = resp.headers.get("retry-after")
                    try:
                        espera_meta = max(0.0, float(espera_meta))
                    except (TypeError, ValueError):
                        espera_meta = 0.0
                    minimo = (reglas["pair_seconds"] if codigo == 131056 else
                              60 if codigo == 131057 else 0)
                    demora = min(max(reglas["backoff_max"], minimo),
                                 max(base, espera_meta, minimo))
                    demora += random.uniform(0, min(1, demora / 4))
                    if codigo == 131056:
                        await asyncio.to_thread(aplazar_pareja, self.phone_number_id,
                                                destinatario, demora)
                    else:
                        await asyncio.to_thread(bloquear_despacho, self.phone_number_id,
                                                codigo, demora)
                    if intento < reintentos:
                        await asyncio.sleep(min(demora, TOPE_ESPERA_S))
                        continue
                    return self._procesar_respuesta(resp)
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

    async def subir_muestra_template(self, app_id: str, media: dict) -> str:
        """Meta: sesión de upload + bytes para obtener el handle del ejemplo."""
        archivo = ruta_media(media)
        inicio = await self._peticion(
            "POST", f"{graph_url()}/{app_id}/uploads",
            params={"file_name": archivo.name, "file_length": archivo.stat().st_size,
                    "file_type": media["mime_envio"]},
        )
        async with httpx.AsyncClient(timeout=60) as cliente:
            respuesta = await cliente.post(
                f"{graph_url()}/{inicio['id']}",
                headers={"Authorization": f"OAuth {self.token}", "file_offset": "0",
                         "Content-Type": "application/octet-stream"},
                content=archivo.read_bytes(),
            )
        return self._procesar_respuesta(respuesta)["h"]

    async def subir_media_envio(self, media: dict) -> str:
        """Sube el archivo a /media y devuelve su ID para el parámetro HEADER."""
        archivo = ruta_media(media)
        async with httpx.AsyncClient(timeout=60) as cliente:
            with archivo.open("rb") as flujo:
                respuesta = await cliente.post(
                    f"{graph_url()}/{self.phone_number_id}/media",
                    headers={"Authorization": f"Bearer {self.token}"},
                    data={"messaging_product": "whatsapp"},
                    files={"file": (archivo.name, flujo, media["mime_envio"])},
                )
        return self._procesar_respuesta(respuesta)["id"]

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
            "fields": "id,name,language,status,category,components,rejected_reason,quality_score",
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
        return await self._peticion(
            "GET", url,
            params={"name": nombre,
                    "fields": "id,name,language,status,category,rejected_reason,quality_score"},
        )

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
    def __init__(self, phone_number_id: str | None = None):
        self._phone_number_id = phone_number_id

    @property
    def token(self) -> str:
        # Nunca usar otra clínica ni el token global como respaldo implícito.
        if not self.phone_number_id:
            return ""
        return credenciales_centro(self.phone_number_id)["token"]

    @property
    def phone_number_id(self) -> str:
        # Ninguna operación de envío puede caer silenciosamente en el emisor
        # global antiguo. Cada campaña o prueba entrega un ID explícito.
        return (self._phone_number_id or "").strip()

    @property
    def waba_id(self) -> str:
        if self.phone_number_id:
            return credenciales_centro(self.phone_number_id)["waba_id"]
        return ""

    @property
    def app_id(self) -> str:
        return (config_get("wa_app_id") or "").strip()

    async def _id_media_envio(self, media_id: str) -> tuple[str, str]:
        metadata = obtener_media(media_id)
        llave = (self.phone_number_id, media_id)
        with _media_cache_lock:
            anterior = _media_cache.get(llave)
            if anterior and anterior[1] > time.time():
                return anterior[0], metadata["formato_meta"].lower()
        nuevo = await self.cliente.subir_media_envio(metadata)
        with _media_cache_lock:
            _media_cache[llave] = (nuevo, time.time() + 23 * 3600)
        return nuevo, metadata["formato_meta"].lower()

    def _componente_encabezado_meta(self, media_id: str | None) -> dict | None:
        if not media_id:
            return None
        if not self.app_id:
            raise ValueError("Configura el App ID de Meta para registrar el encabezado multimedia.")
        metadata = obtener_media(media_id)
        handle = asyncio.run(self.cliente.subir_muestra_template(self.app_id, metadata))
        return {"type": "HEADER", "format": metadata["formato_meta"],
                "example": {"header_handle": [handle]}}

    def configurada(self) -> bool:
        return bool(self.token and self.phone_number_id and self.waba_id)

    async def _validar_canal_antes_envio(self) -> tuple[bool, str]:
        cfg = config_all(refresh=True)
        canal = resolver_phone_id(self.phone_number_id, cfg)
        if not canal or not canal["configured"]:
            return False, "El centro emisor no está configurado para enviar."
        return await asyncio.to_thread(verificar_canal_meta, canal, cfg)

    async def _plantilla_aprobada_en_waba(self, nombre: str, idioma: str) -> bool:
        clave = (hashlib.sha256(self.token.encode()).hexdigest(), self.waba_id,
                 nombre, idioma)
        with _template_aprobada_lock:
            if _template_aprobada_cache.get(clave, 0) > time.monotonic():
                return True
        try:
            resultado = await self.cliente.buscar_template(self.waba_id, nombre)
        except ErrorWhatsApp:
            return False
        aprobada = any(t.get("name") == nombre and t.get("language") == idioma
                       and t.get("status") == "APPROVED"
                       for t in resultado.get("data") or [])
        if aprobada:
            with _template_aprobada_lock:
                _template_aprobada_cache[clave] = time.monotonic() + 60
        return aprobada

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
                            category: str = "UTILITY", media_id: str | None = None,
                            encabezado_texto: str | None = None,
                            pie_texto: str | None = None,
                            botones: list[dict] | None = None) -> dict:
        """Crea el template en Meta. Devuelve {ok, template_id, status, error}.

        Los templates en Meta quedan en estado PENDING hasta ser aprobados."""
        if not self.waba_id or not self.token:
            return {"ok": False, "template_id": None, "status": None,
                    "error": "Falta la cuenta de WhatsApp Business o el token (configúralos en Configuración)"}

        texto_meta, ejemplo = self.convertir_texto_meta(texto)
        componente_body: dict = {"type": "BODY", "text": texto_meta}
        if ejemplo:
            componente_body["example"] = {"body_text": [ejemplo]}

        try:
            encabezado = self._componente_encabezado_meta(media_id)
        except Exception as exc:
            return {"ok": False, "template_id": None, "status": None, "error": str(exc)}
        if not encabezado and encabezado_texto:
            encabezado = {"type": "HEADER", "format": "TEXT", "text": encabezado_texto.strip()}
        components = ([encabezado] if encabezado else []) + [componente_body]
        if pie_texto:
            components.append({"type": "FOOTER", "text": pie_texto.strip()})
        if category != "AUTHENTICATION":
            vista = presentar_plantilla({"texto": texto, "botones": botones})
            if vista["errores"]:
                return {"ok": False, "template_id": None, "status": None,
                        "error": " ".join(vista["errores"])}
            components.append({"type": "BUTTONS", "buttons": botones_para_meta(vista["botones"])})

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

    def editar_template_meta(self, template_id: str, texto: str, category: str = "UTILITY",
                            media_id: str | None = None,
                            encabezado_texto: str | None = None,
                            pie_texto: str | None = None,
                            botones: list[dict] | None = None) -> dict:
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

        try:
            encabezado = self._componente_encabezado_meta(media_id)
        except Exception as exc:
            return {"ok": False, "template_id": template_id, "status": None, "error": str(exc)}
        if not encabezado and encabezado_texto:
            encabezado = {"type": "HEADER", "format": "TEXT", "text": encabezado_texto.strip()}
        components = ([encabezado] if encabezado else []) + [componente_body]
        if pie_texto:
            components.append({"type": "FOOTER", "text": pie_texto.strip()})
        if categoria_real != "AUTHENTICATION":
            vista = presentar_plantilla({"texto": texto, "botones": botones})
            if vista["errores"]:
                return {"ok": False, "template_id": template_id, "status": None,
                        "error": " ".join(vista["errores"])}
            components.append({"type": "BUTTONS", "buttons": botones_para_meta(vista["botones"])})

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
                              template_id_conocido: str | None = None,
                              media_id: str | None = None,
                              encabezado_texto: str | None = None,
                              pie_texto: str | None = None,
                              botones: list[dict] | None = None) -> dict:
        """Crea el template en Meta, o lo edita si ya existe.

        Si no se pasa un template_id_conocido, primero intenta encontrarlo en
        Meta por nombre+idioma (por si ya se había creado pero se perdió el id
        localmente). Si lo encuentra, edita; si no, crea uno nuevo."""
        tid = template_id_conocido or self.id_template_meta(nombre, lang)
        if tid:
            return self.editar_template_meta(tid, texto, category, media_id,
                                             encabezado_texto, pie_texto, botones)
        return self.crear_template_meta(nombre, texto, lang, category, media_id,
                                        encabezado_texto, pie_texto, botones)

    def estado_template_meta(self, nombre_template: str, lang: str) -> dict:
        """Consulta en Meta el estado actual de un template por nombre + idioma.

        Devuelve {ok, status, category, rejected_reason, quality_score, error}.
        No rompe el flujo si Meta falla o si no encuentra el template: el error
        queda en 'error' para que el llamador decida qué mostrar."""
        if not self.waba_id or not self.token:
            return {"ok": False, "status": None, "category": None, "rejected_reason": None,
                    "quality_score": None,
                    "error": "Falta la cuenta de WhatsApp Business o el token (configúralos en Configuración)"}

        try:
            data = asyncio.run(self.cliente.buscar_template(self.waba_id, nombre_template))
        except ErrorWhatsApp as e:
            log_error(f"estado_template_meta({nombre_template!r}, {lang!r})", e)
            return {"ok": False, "status": None, "category": None, "rejected_reason": None,
                    "quality_score": None,
                    "error": e.message}

        for t in data.get("data", []):
            if t.get("language") == lang:
                return {
                    "ok": True,
                    "status": t.get("status"),
                    "category": t.get("category"),
                    "rejected_reason": t.get("rejected_reason") or t.get("reject_reason"),
                    "quality_score": t.get("quality_score"),
                    "error": None,
                }

        return {"ok": False, "status": None, "category": None, "rejected_reason": None,
                "quality_score": None,
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

    def construir_payload_opciones_insulto(self, telefono: str, mensaje: str) -> tuple:
        """Respuesta de servicio con elección explícita de callcenter o baja."""
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": telefono.lstrip("+"),
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": mensaje[:1024]},
                "action": {"buttons": [
                    {"type": "reply", "reply": {"id": BOTON_ID_EJECUTIVA,
                                                "title": BOTON_TEXTO_EJECUTIVA}},
                    {"type": "reply", "reply": {"id": BOTON_ID_BAJA_INSULTO,
                                                "title": BOTON_TEXTO_BAJA}},
                ]},
            },
        }
        return payload, "botones_servicio"

    def construir_payload_lista(self, telefono: str, plantilla_id: int,
                                boton: dict) -> tuple:
        """Lista interactiva posterior a una respuesta rápida de la plantilla."""
        opciones = boton["opciones"]
        version = version_opciones(opciones)
        payload = {
            "messaging_product": "whatsapp", "recipient_type": "individual",
            "to": telefono.lstrip("+"), "type": "interactive",
            "interactive": {
                "type": "list",
                "body": {"text": "Selecciona una opción para recibir más información."},
                "action": {
                    "button": boton["texto"],
                    "sections": [{"title": "Opciones", "rows": [
                        {"id": f"snw_lista_{plantilla_id}_{version}_{indice}",
                         "title": opcion["texto"]}
                        for indice, opcion in enumerate(opciones)]}],
                },
            },
        }
        return payload, "lista_opciones"

    def construir_payload_template(self, telefono: str, nombre: str, lang: str,
                                   variables: list[str] | None = None,
                                   componentes: list[dict] | None = None) -> tuple:
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
        return [clave for clave in variables_del_cuerpo(texto)
                if clave in ("nombre", "apellido")]

    async def enviar(self, telefono: str, mensaje: str, plantilla: dict | None = None,
                     variables: dict | None = None, es_prueba: bool = False) -> tuple:
        """Envía un mensaje. Devuelve (ok, message_id, error, estado).

        Si la plantilla en el sistema tiene un template Meta configurado
        (campo 'whatsapp_template'), se envía como template aprobado usando los
        valores reales del paciente; en caso contrario, texto libre.
        """
        if esta_suprimido(telefono):
            return False, None, "Número suprimido por solicitud del paciente", "failed"
        autorizado, problema = await self._validar_canal_antes_envio()
        if not autorizado:
            return False, None, problema, "failed"
        msg = plantilla or {}
        nombre_template = msg.get("whatsapp_template")
        idioma = msg.get("whatsapp_template_lang") or config_get("wa_template_lang", "es")
        cta = msg.get("cta") if isinstance(msg, dict) else None

        if nombre_template:
            if not await self._plantilla_aprobada_en_waba(nombre_template, idioma):
                return False, None, "La plantilla no está aprobada para el WABA de esta clínica.", "failed"
            if msg.get("encabezado_media_id") and not msg.get("encabezado_meta_registrado"):
                return False, None, "El encabezado aún no se registró en Meta", "failed"
            revision = presentar_plantilla(msg, variables or {})
            if revision["errores"]:
                return False, None, " ".join(revision["errores"]), "failed"
            orden = self.extraer_orden_comodines(msg.get("texto", "") or "")
            vdict = variables or {}
            valores = [vdict.get(clave, "") or "" for clave in orden]
            componentes = []
            if msg.get("encabezado_media_id"):
                try:
                    id_meta, tipo = await self._id_media_envio(msg["encabezado_media_id"])
                except Exception as exc:
                    log_error("subir encabezado de plantilla para envío", exc)
                    return False, None, f"No se pudo preparar el encabezado: {exc}", "failed"
                componentes.append({"type": "header", "parameters": [
                    {"type": tipo, tipo: {"id": id_meta}}]})
            if valores:
                componentes.append({"type": "body", "parameters": [
                    {"type": "text", "text": valor} for valor in valores]})
            for indice, boton in enumerate(revision.get("botones", [])):
                if boton["tipo"] != "respuesta":
                    continue
                accion = boton.get("accion") or "ninguna"
                if accion == "opciones":
                    if not isinstance(msg.get("id"), int):
                        return False, None, "La plantilla no tiene ID para mostrar opciones", "failed"
                    payload_boton = f"snw_tpl_opciones_{msg['id']}"
                else:
                    payload_boton = ACCIONES_BOTON_TEMPLATE.get(accion, "snw_tpl_respuesta")
                componentes.append({"type": "button", "sub_type": "quick_reply",
                                    "index": str(indice), "parameters": [
                                        {"type": "payload", "payload": payload_boton}]})
            payload_, _ = self.construir_payload_template(
                telefono, nombre_template, idioma, componentes=componentes or None,
            )
        elif msg.get("lista_opciones"):
            lista = msg["lista_opciones"]
            payload_, _ = self.construir_payload_lista(
                telefono, lista["plantilla_id"], lista["boton"])
        elif msg.get("opciones_insulto"):
            payload_, _ = self.construir_payload_opciones_insulto(telefono, mensaje)
        elif cta and cta.get("url"):
            payload_, _ = self.construir_payload_cta_url(
                telefono, mensaje, cta.get("texto") or "Abrir", cta["url"],
            )
        else:
            payload_, _ = self.construir_payload_texto(telefono, mensaje)

        if esta_suprimido(telefono):
            return False, None, "Número suprimido por solicitud del paciente", "failed"
        try:
            data = await self.cliente.enviar(payload_)
        except ErrorWhatsApp as e:
            destino_log = "número de prueba" if es_prueba else telefono
            log_error(f"envío a {destino_log} (template={nombre_template or 'texto libre'})", e)
            return False, None, e.message, "failed" if e.tipo == "permanent" else "sent"

        msg_id = ""
        if data.get("messages"):
            msg_id = data["messages"][0].get("id", "") or ""
        if not msg_id:
            raise ResultadoEnvioIncierto("Meta respondió sin identificador del mensaje")
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
                phone_id = str((valor.get("metadata") or {}).get("phone_number_id") or "").strip()
                config_canal = config_all(refresh=True)
                if len([c for c in listar_canales(config_canal) if c["phoneNumberId"] == phone_id]) > 1:
                    accion.append("emisor_duplicado")
                    continue
                emisor = resolver_phone_id(phone_id, config_canal)
                if not emisor or not emisor.get("phoneNumber"):
                    emisor = self._emisor_historico(phone_id)
                if not emisor or not emisor.get("phoneNumber"):
                    # Nunca registrar respuestas ni emitir avisos por un número
                    # desconocido: el fallback global mezclaría centros.
                    accion.append("emisor_desconocido")
                    continue
                token = EMISOR_WEBHOOK.set(emisor)
                try:
                    if valor.get("messages"):
                        accion += self._procesar_mensajes(valor)
                    if valor.get("statuses"):
                        accion += self._procesar_estados(valor)
                finally:
                    EMISOR_WEBHOOK.reset(token)
        self._guardar_evento(body)
        return accion

    @staticmethod
    def _emisor_historico(phone_id: str) -> dict | None:
        """Conserva respuestas del canal retirado si el historial lo identifica."""
        if not phone_id:
            return None
        try:
            with conectar() as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT centro_codigo, numero_emisor FROM log_envios"
                    " WHERE wa_phone_id = %s AND centro_codigo IS NOT NULL"
                    " AND numero_emisor IS NOT NULL ORDER BY id DESC LIMIT 20",
                    (phone_id,),
                )
                filas = cur.fetchall() or []
            # Una clínica pausada no puede iniciar envíos nuevos, pero aún
            # debemos reconocer las respuestas a mensajes anteriores.
            catalogo = catalogo_centros(incluir_desactivadas=True)
            codigos = {r["centro_codigo"] for r in filas if r.get("centro_codigo") in catalogo}
            if len(codigos) == 1:
                codigo = codigos.pop()
                return {"codigo": codigo, "nombre": catalogo[codigo],
                        "phoneNumber": filas[0]["numero_emisor"],
                        "phoneNumberId": phone_id, "configured": False}
        except Exception as exc:
            log_error("_emisor_historico", exc)
        return None

    def _procesar_mensajes(self, valor: dict) -> list[str]:
        """Mensajes entrantes del cliente (respuestas, bajas, etc.).

        Los avisos de reactivación ("bienvenido de vuelta") y de despedida se
        deciden al final, con el estado opt-out con que termina el evento.
        El borrado real por ELIMINAR solo se acepta tras el aviso de sus
        consecuencias; los números de prueba simulan la baja."""
        acciones = []
        # Meta manda el wa_id sin '+' (ej. "56993921740"); lo normalizamos.
        crudo = ((valor.get("contacts") or [{}])[0].get("wa_id")
                 or (valor.get("messages") or [{}])[0].get("from", ""))
        telefono = _normalizar_telefono(crudo) or crudo
        exento = es_exento(telefono)
        numero_prueba = es_numero_prueba(telefono)
        if esta_suprimido(telefono):
            # Si el bloqueo externo sigue pendiente, reintenta sin responder.
            solicitar_eliminacion(telefono)
            return ["numero_suprimido"]
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
                # Los adjuntos sin texto también cuentan en el corpus, pero
                # nunca se copia el archivo ni su identificador de Meta.
                pie = (mensaje.get(tipo) or {}).get("caption", "") if tipo in (
                    "image", "video", "document") else ""
                if not self._guardar_mensaje_entrante(mensaje, telefono, pie):
                    continue
                if not es_solicitud_eliminar(pie):
                    self._guardar_corpus_respuesta(pie, telefono)
                    if callable(al_derivar_crm):
                        try:
                            al_derivar_crm(telefono, pie, mensaje.get("id"), ("GENERAL",),
                                          tipo=tipo, timestamp=mensaje.get("timestamp"),
                                          referencia_meta_id=(mensaje.get("context") or {}).get("id"))
                        except Exception as exc:
                            log_error("al_derivar_crm: adjunto", exc)
                continue
            cuerpo = texto or extra
            if not self._guardar_mensaje_entrante(mensaje, telefono, cuerpo):
                acciones.append("mensaje_duplicado")
                continue

            if es_solicitud_eliminar(texto) or es_solicitud_eliminar(extra):
                if numero_prueba:
                    # Prueba reversible: misma exclusión de envíos, sin borrar
                    # datos, crear huella de supresión ni bloquear en Meta.
                    self._registrar_baja(telefono, aviso=False, simulada=True)
                    acciones.append("eliminacion_simulada")
                    return acciones
                if exento:
                    acciones.append("eliminacion_exenta")
                    return acciones
                if oferta_eliminacion_enviada(telefono):
                    solicitar_eliminacion(telefono)
                    acciones.append("eliminacion_solicitada")
                    return acciones
                # No borrar sin el aviso previo que explica las consecuencias.
                self._registrar_baja(telefono, aviso=False)
                hubo_baja = True
                acciones.append("baja_sin_aviso_de_eliminacion")
                continue

            # El mensaje íntegro se guarda separado de su huella técnica para
            # poder retirarlo de todas las áreas al recibir ELIMINAR.
            self._guardar_corpus_respuesta(cuerpo, telefono)

            # El payload identifica la acción de botones personalizados;
            # el texto exacto conserva compatibilidad con botones antiguos.
            es_boton = tipo in ("button", "interactive")
            boton = (texto or "").strip().casefold()
            clasificacion = clasificar(cuerpo, patrones_configurados(
                config_get("call_center_patrones_consulta", "{}"))) if tipo == "text" else None
            if clasificacion and clasificacion.automatizacion != "SIN_EVIDENCIA_AUTOMATIZACION":
                # No reactivar bajas, marcar interés, derivar ni contestar a una
                # posible autorespuesta. El original permanece en el historial.
                info = self._registrar_respuesta(telefono, cuerpo, mensaje.get("timestamp"),
                                                 automatica=clasificacion.automatizacion)
                acciones.append(f"respuesta_automatica_{info}")
                continue
            # La ofensa es independiente: no omite una consulta ni una baja.
            # El aviso se limita por número en la base compartida.
            if (tipo == "text" and clasificacion
                    and clasificacion.ofensa.categoria == "INSULTO_DIRECTO"
                    and not self._es_baja(cuerpo) and callable(al_detectar_insulto)):
                try:
                    al_detectar_insulto(telefono, mensaje.get("id"))
                    acciones.append("opciones_baja_ofrecidas")
                except Exception as exc:
                    log_error("al_detectar_insulto", exc)
            accion_payload = next((accion for accion, payload in ACCIONES_BOTON_TEMPLATE.items()
                                   if es_boton and extra == payload), None)
            interes = es_mensaje_interes(cuerpo) if accion_payload is None else accion_payload == "interes"
            baja = ((self._es_baja(texto) or self._es_baja(extra)) if accion_payload is None
                    else accion_payload == "baja")
            if clasificacion and clasificacion.cancela and any(
                    frase in cuerpo.casefold() for frase in ("cancelar mi hora", "cancelo mi hora")):
                # «Cancelar mi hora» es una cancelación de cita, no una baja de
                # notificaciones; una baja explícita en el mismo texto sí gana.
                if not any(f in cuerpo.casefold() for f in BAJA_FRASES):
                    baja = False
            fue_interes = interes or (es_boton and accion_payload is None
                                     and boton == BOTON_TEXTO_INTERES.casefold())
            fue_baja = baja or (es_boton and accion_payload is None
                               and boton == BOTON_TEXTO_BAJA.casefold())
            # "no me interesa" / "no estoy interesada"…: NO es una baja, el
            # paciente sigue pudiendo recibir futuras plantillas. Una baja
            # explícita en el mismo mensaje gana sobre esto.
            no_interes = (not fue_interes) and (not fue_baja) and (
                accion_payload == "desinteres" if accion_payload else
                es_mensaje_no_interes(texto) or es_mensaje_no_interes(extra)
            )

            # El reagendamiento tiene su propio contexto de cita y número CRM.
            # No copiarlo a la cola local ni disparar el call center genérico.
            reagendar = None
            estaba_opt_out = self._estaba_opt_out(telefono)
            if callable(al_respuesta_reagendar) and not estaba_opt_out:
                try:
                    reagendar = al_respuesta_reagendar(
                        telefono, cuerpo, mensaje.get("id"),
                        (mensaje.get("context") or {}).get("id"),
                        "BAJA" if fue_baja else "DESINTERES" if no_interes else None)
                except Exception as exc:
                    log_error("al_respuesta_reagendar", exc)
            if reagendar == "gestionado":
                info = self._registrar_respuesta(telefono, cuerpo, mensaje.get("timestamp"))
                acciones.append(f"respuesta_{info}")
                acciones.append("reagendar_gestionado")
                continue

            # La cola CRM registra cualquier respuesta humana, con independencia
            # de si el clasificador también dispara un aviso de call center.
            # ELIMINAR ya salió antes de este punto y nunca se copia a la cola.
            motivos_crm = (("BAJA",) if fue_baja else
                           ("DESINTERES",) if no_interes else
                           ("INTERES",) if fue_interes else
                           ("SOLICITA_CONTACTO",) if es_boton and extra == BOTON_ID_EJECUTIVA else
                           tuple(clasificacion.motivos) if clasificacion and clasificacion.motivos else
                           ("GENERAL",))
            if callable(al_derivar_crm) and reagendar != "declinado":
                try:
                    al_derivar_crm(telefono, "Solicitud de baja" if fue_baja else cuerpo,
                                  mensaje.get("id"), motivos_crm,
                                  tipo=tipo, timestamp=mensaje.get("timestamp"),
                                  referencia_meta_id=(mensaje.get("context") or {}).get("id"))
                except Exception as exc:
                    log_error("al_derivar_crm", exc)

            # El aviso de baja informa que cualquier nueva respuesta distinta
            # de BAJA/ELIMINAR reincorpora al paciente. Una nueva baja jamás
            # se interpreta como reactivación.
            if estaba_opt_out and not fue_baja:
                self._registrar_retractacion(telefono, aviso=False)
                hubo_retract = True
                acciones.append("retractacion")

            info = self._registrar_respuesta(telefono, cuerpo, mensaje.get("timestamp"))
            acciones.append(f"respuesta_{info}")

            if es_boton and re.fullmatch(r"snw_tpl_opciones_\d+", extra or ""):
                if callable(al_mostrar_opciones):
                    try:
                        al_mostrar_opciones(telefono, int(extra.rsplit("_", 1)[1]), mensaje.get("id"))
                        acciones.append("lista_solicitada")
                    except Exception as exc:
                        log_error("mostrar opciones de plantilla", exc)
                continue
            if tipo == "interactive" and (mensaje.get("interactive") or {}).get("type") == "list_reply":
                coincidencia = re.fullmatch(r"snw_lista_(\d+)_([0-9a-f]{12})_(\d+)", extra or "")
                if coincidencia:
                    if fue_baja:
                        self._registrar_baja(telefono, aviso=False)
                        hubo_baja = True
                        acciones.append("baja_lista")
                    elif callable(al_responder_opcion):
                        try:
                            al_responder_opcion(telefono, int(coincidencia[1]), coincidencia[2],
                                               int(coincidencia[3]), mensaje.get("id"))
                            acciones.append("opcion_lista_elegida")
                        except Exception as exc:
                            log_error("responder opción de lista", exc)
                    continue

            if es_boton and (extra == BOTON_ID_EJECUTIVA or
                            (accion_payload is None and boton in (
                                BOTON_TEXTO_EJECUTIVA.casefold(),
                                BOTON_TEXTO_EJECUTIVA_ANTERIOR.casefold()))):
                if callable(al_detectar_consulta):
                    try:
                        resultado = al_detectar_consulta(
                            telefono, "Solicita hablar con callcenter", mensaje.get("id"),
                            ("SOLICITA_CONTACTO",), True)
                        acciones.append(f"derivacion_{resultado}")
                    except Exception as exc:
                        log_error("al_detectar_consulta: ejecutiva", exc)
                continue
            if es_boton and extra == ACCIONES_BOTON_TEMPLATE["consulta"]:
                if callable(al_detectar_consulta):
                    try:
                        resultado = al_detectar_consulta(
                            telefono, cuerpo, mensaje.get("id"), ("SOLICITA_CONTACTO",))
                        acciones.append(f"derivacion_{resultado}")
                    except Exception as exc:
                        log_error("al_detectar_consulta: botón de plantilla", exc)
                continue

            # Las bajas y la supresión siempre tienen prioridad. Una consulta
            # junto a una confirmación conserva el registro de interés, pero
            # evita disparar además el mensaje genérico de call center.
            if clasificacion and clasificacion.derivar and not fue_baja and not no_interes:
                if interes:
                    self._registrar_interes(telefono)
                    hubo_interes = True
                    acciones.append("interes")
                if callable(al_detectar_consulta):
                    try:
                        resultado = al_detectar_consulta(
                            telefono, cuerpo, mensaje.get("id"), clasificacion.motivos)
                        acciones.append(f"derivacion_{resultado}")
                    except Exception as e:
                        log_error("al_detectar_consulta", e)
                        acciones.append("derivacion_error")
                continue

            if es_boton and (accion_payload == "interes" or
                            (accion_payload is None and boton == BOTON_TEXTO_INTERES.casefold())):
                self._registrar_interes(telefono)
                hubo_interes = True
                acciones.append("interes_boton")
                if callable(al_detectar_interes):
                    try:
                        if numero_prueba:
                            al_detectar_interes(telefono, mensaje.get("id"))
                        else:
                            al_detectar_interes(telefono)
                    except Exception as e:
                        log_error(f"al_detectar_interes({telefono})", e)
                continue
            if es_boton and (accion_payload == "desinteres" or
                            (accion_payload is None and boton == BOTON_TEXTO_NO_INTERES.casefold())):
                self._registrar_no_interes(telefono)
                acciones.append("no_interes_boton")
                continue
            if es_boton and (accion_payload == "baja" or
                            (accion_payload is None and boton == BOTON_TEXTO_BAJA.casefold())):
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
                        if numero_prueba:
                            al_detectar_interes(telefono, mensaje.get("id"))
                        else:
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
            if hubo_baja and (numero_prueba or exento or not oferta_eliminacion_enviada(telefono)):
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
            self._actualizar_estado(message_id, estado, detalle_error, s.get("timestamp"))
            acciones.append(f"estado_{estado}")
        return acciones

    # ---------- Persistencia de eventos ----------
    @staticmethod
    def _guardar_mensaje_entrante(mensaje: dict, telefono: str, texto: str) -> bool:
        """Una fila lógica por ID de Meta y por emisor, aun sin campaña previa."""
        emisor = EMISOR_WEBHOOK.get()
        if not emisor:
            return True  # llamadas internas/compatibilidad de pruebas antiguas
        mensaje_id = str(mensaje.get("id") or "").strip()
        if not mensaje_id:
            return False
        try:
            with conectar() as conn, conn.cursor() as cur:
                cur.execute(
                    "INSERT IGNORE INTO whatsapp_conversaciones_mensajes"
                    " (mensaje_id, centro_codigo, wa_phone_id, numero_emisor,"
                    " telefono_paciente, tipo, texto) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (mensaje_id[:255], emisor["codigo"], emisor["phoneNumberId"],
                     emisor["phoneNumber"], telefono, str(mensaje.get("type") or "")[:30],
                     (texto or "")[:2000]),
                )
                nuevo = cur.rowcount == 1
                conn.commit()
                return nuevo
        except Exception as exc:
            log_error("_guardar_mensaje_entrante", exc)
            return False  # sin deduplicación fiable no enviar respuestas automáticas

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
        # El hash basta para deduplicar; el payload puede incluir datos personales.
        texto = "[omitido]"
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
    def _guardar_corpus_respuesta(texto: str, telefono: str) -> None:
        try:
            with conectar() as conn, conn.cursor() as cur:
                guardar_mensaje_completo(cur, texto, telefono)
                conn.commit()
        except Exception as exc:
            # El corpus es auxiliar: no debe impedir una baja o respuesta.
            log_error("guardar corpus de respuestas", exc)

    @staticmethod
    def _tablas_pac() -> tuple:
        """Tablas legacy (dev + prod) más las de areas registradas."""
        base = (tabla_pacientes("desarrollo"), tabla_pacientes("produccion"))
        try:
            extra = tuple(r["nombre_tabla_base"]
                          for r in servicio_areas.listar_tablas_areas())
        except Exception as e:
            log_error("_tablas_pac: areas", e)
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
        areas. log_envios es compartida."""
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
                if any(p.get("whatsapp_opt_out") for p in pacientes):
                    return True
                cur.execute("SELECT 1 FROM paciente_reagendar"
                            " WHERE paciente_telefono_normalizado=%s AND whatsapp_opt_out=1"
                            " LIMIT 1", (telefono,))
                return bool(cur.fetchone())
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
            "       'no_interes_boton', 'baja_aviso', 'retractacion_aviso',"
            "       'lista_opciones', 'lista_respuesta')"
            " ORDER BY id DESC LIMIT 1",
            (paciente_id, *args_t),
        )
        fila = cur.fetchone()
        return (fila or {}).get("plantilla_clave") or None

    def _registrar_respuesta(self, telefono: str, texto: str, timestamp: str,
                            automatica: str = "") -> str:
        match = _TEL_MATCH.format(col="telefono")
        try:
            with conectar() as conn, conn.cursor() as cur:
                pacientes = self._pacientes_con_tel(cur, telefono)
                if not pacientes:
                    return "sin_cliente"
                emisor = EMISOR_WEBHOOK.get()
                if emisor:
                    cur.execute(
                        "SELECT COALESCE(le.tabla_pacientes, e.base_datos) AS tabla_pacientes,"
                        " le.paciente_id FROM log_envios le"
                        " LEFT JOIN envios e ON e.id = le.envio_id"
                        " WHERE le.wa_phone_id = %s AND le.numero_telefono = %s"
                        " AND le.plantilla_clave NOT IN ('respuesta','call_center',"
                        " 'baja_aviso','retractacion_aviso','ajuste_manual')"
                        " ORDER BY le.id DESC LIMIT 1",
                        (emisor["phoneNumberId"], telefono),
                    )
                    origen = cur.fetchone() or {}
                    if origen:
                        pacientes = [p for p in pacientes if p["tabla"] == origen.get("tabla_pacientes")
                                     and p["id"] == origen.get("paciente_id")]
                    elif len(pacientes) > 1:
                        # Conversación directa sin campaña: se conservó arriba,
                        # pero no se atribuye arbitrariamente a una base.
                        return "sin_base_inequivoca"
                for p in pacientes:
                    if not automatica:
                        cur.execute(f"UPDATE {p['tabla']} SET estado = 'enviado' WHERE {match}", (telefono,))
                    # Una respuesta real deja sin efecto una corrección manual previa.
                    if not automatica:
                        try:
                            cur.execute(
                                f"UPDATE {p['tabla']} SET respuesta_manual = NULL"
                                f" WHERE {match} AND respuesta_manual IS NOT NULL",
                                (telefono,),
                            )
                        except Exception:
                            pass  # esquema sin la columna
                    # El mismo teléfono puede estar en varias bases. Cada una
                    # necesita su propia señal para la ficha y estadísticas.
                    esp = servicio_areas.area_por_tabla(p["tabla"])
                    emisor = EMISOR_WEBHOOK.get()
                    cur.execute(
                        "INSERT INTO log_envios (envio_id, paciente_id, nombre_paciente, numero_telefono,"
                        " mensaje, plantilla_clave, estado_envio, respuesta, descripcion_error,"
                        " area_id, tabla_pacientes, centro_codigo, wa_phone_id, numero_emisor)"
                        " VALUES (NULL, %s, %s, %s, %s, 'respuesta', 'enviado', %s, %s, %s, %s, %s, %s, %s)",
                        (p["id"], p["nombre"], p["telefono"], (texto or "")[:2000],
                         "pendiente" if automatica else "respondio",
                         automatica if automatica else None,
                         esp["id"] if esp else None, p["tabla"],
                         (emisor or {}).get("codigo"), (emisor or {}).get("phoneNumberId"),
                         (emisor or {}).get("phoneNumber")),
                    )
                    respuesta_id = cur.lastrowid
                    try:
                        clasificacion = clasificar(texto)
                        ofensa = clasificacion.ofensa
                        detalle = json.dumps({
                            "categoria": ofensa.categoria,
                            "confianza": ofensa.confianza,
                            "terminos": ofensa.terminos,
                            "accion_recomendada": ofensa.accion_recomendada,
                            "motivos_consulta": clasificacion.motivos,
                            "interes": es_mensaje_interes(texto),
                            "baja": self._es_baja(texto),
                        }, ensure_ascii=False)
                        cur.execute(
                            "INSERT IGNORE INTO respuesta_etiquetas"
                            " (log_envio_id, etiquetas, origen, actualizado_por, ofensa_detalle)"
                            " VALUES (%s, %s, 'automatico', 'sistema', %s)",
                            (respuesta_id, json.dumps(etiquetas_automaticas(texto, clasificacion),
                                                     ensure_ascii=False), detalle),
                        )
                    except Exception as exc:
                        # La anotación no debe impedir conservar la respuesta.
                        log_error("etiquetas_automaticas: no se pudo guardar", exc)
                conn.commit()
            return "registrada"
        except Exception as e:
            log_error("_registrar_respuesta", e)
            return "error"

    def _registrar_baja(self, telefono: str, aviso: bool | None = None,
                       simulada: bool = False) -> None:
        match = _TEL_MATCH.format(col="telefono")
        try:
            from paciente_reagendar import marcar_opt_out_reagendar
            marcar_opt_out_reagendar(telefono, True)
            with conectar() as conn, conn.cursor() as cur:
                pacientes = self._pacientes_con_tel(cur, telefono)
                if not pacientes:
                    return
                # Si ya estaba de baja, esto es un webhook repetido (Meta
                # garantiza entrega "al menos una vez", no exactamente una) o
                # una baja repetida — no es una baja nueva, así que por defecto
                # no se reenvía el aviso de despedida.
                ya_de_baja = all(p.get("whatsapp_opt_out") for p in pacientes)
                if simulada:
                    # Marcador reversible, sin crear huella de supresión real.
                    # Evita que un aviso de baja encolado salga después de ELIMINAR.
                    cur.execute("INSERT INTO whatsapp_avisos_unicos (telefono, tipo)"
                                " VALUES (%s, 'eliminacion_simulada')"
                                " ON DUPLICATE KEY UPDATE enviado = NOW()", (telefono,))
                for p in pacientes:
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
                if not ya_de_baja and (simulada or es_numero_prueba(telefono)):
                    # Solo las pruebas pueden repetir el ciclo de avisos. En
                    # números reales, conservar 'reintegro' evita respuestas
                    # automáticas en cada alternancia entre baja y alta.
                    cur.execute("DELETE FROM whatsapp_avisos_unicos"
                                " WHERE telefono = %s AND tipo = 'reintegro'", (telefono,))
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
            from paciente_reagendar import marcar_opt_out_reagendar
            marcar_opt_out_reagendar(telefono, False)
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
                    # Conserva la fecha del regreso para trazabilidad. Solo en
                    # la fila que realmente estaba de baja.
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
                if not nadie_de_baja:
                    # Permite repetir el aviso de prueba si vuelve a darse de
                    # baja en otra ronda de la simulación.
                    cur.execute("DELETE FROM whatsapp_avisos_unicos"
                                " WHERE telefono = %s AND tipo IN"
                                " ('baja_prueba', 'eliminacion_simulada')", (telefono,))
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
                    # Solo registra la fecha si el paciente realmente venía de
                    # baja; un interés de alguien que nunca se dio de baja no
                    # necesita ese dato de trazabilidad.
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
                # La oferta se documenta una vez; la respuesta en sí ya queda
                # registrada por tabla en _registrar_respuesta.
                p0 = pacientes[0]
                clave_oferta0 = self._ultima_plantilla_ofertada(cur, p0["id"], p0["tabla"])
                esp0 = servicio_areas.area_por_tabla(p0["tabla"])
                cur.execute(
                    "INSERT INTO log_envios (envio_id, paciente_id, nombre_paciente, numero_telefono,"
                    " mensaje, plantilla_clave, estado_envio, respuesta, descripcion_error,"
                    " area_id, tabla_pacientes)"
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
                esp0 = servicio_areas.area_por_tabla(p0["tabla"])
                cur.execute(
                    "INSERT INTO log_envios (envio_id, paciente_id, nombre_paciente, numero_telefono,"
                    " mensaje, plantilla_clave, estado_envio, respuesta, descripcion_error,"
                    " area_id, tabla_pacientes)"
                    " VALUES (NULL, %s, %s, %s, %s, 'no_interes_boton', 'enviado', 'respondio', NULL, %s, %s)",
                    (p0["id"], p0["nombre"], p0["telefono"],
                     f"No interesado en la oferta del {_hoy_es()}, plantilla '{clave_oferta0 or '—'}'.",
                     esp0["id"] if esp0 else None, p0["tabla"] if esp0 else None),
                )
                conn.commit()
        except Exception as e:
            log_error(f"_registrar_no_interes({telefono})", e)

    def _actualizar_estado(self, message_id: str, estado: str, detalle_error: str | None = None,
                           timestamp: str | None = None) -> None:
        emisor = EMISOR_WEBHOOK.get()
        filtro_emisor = " AND (wa_phone_id = %s OR wa_phone_id IS NULL)" if emisor else ""
        args_emisor = (emisor["phoneNumberId"],) if emisor else ()
        entrega = None
        if estado in ("delivered", "read"):
            try:
                entrega = datetime.fromtimestamp(int(timestamp)) if timestamp else datetime.now()
            except (TypeError, ValueError, OverflowError, OSError):
                entrega = datetime.now()
        for ambiente in ("desarrollo", "produccion"):
            try:
                with conectar(ambiente) as conn, conn.cursor() as cur:
                    cur.execute(
                        "UPDATE log_envios SET estado_whatsapp = CASE"
                        " WHEN estado_whatsapp IN ('delivered','read') AND %s = 'sent'"
                        " THEN estado_whatsapp"
                        " WHEN estado_whatsapp = 'read' AND %s = 'delivered'"
                        " THEN estado_whatsapp ELSE %s END,"
                        " entregado_en = COALESCE(entregado_en, %s)"
                        " WHERE whatsapp_message_id = %s" + filtro_emisor,
                        (estado, estado, estado, entrega, message_id, *args_emisor),
                    )
                    if estado == "sent":
                        cur.execute(
                            "UPDATE log_envios SET estado_envio = 'enviado'"
                            " WHERE whatsapp_message_id = %s" + filtro_emisor,
                            (message_id, *args_emisor),
                        )
                    if estado == "failed":
                        cur.execute(
                            "UPDATE log_envios SET estado_envio = 'error',"
                            " descripcion_error = COALESCE(%s, descripcion_error)"
                            " WHERE whatsapp_message_id = %s" + filtro_emisor,
                            (detalle_error[:255] if detalle_error else None, message_id, *args_emisor)
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
