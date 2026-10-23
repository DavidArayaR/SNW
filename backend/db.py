import json
import logging
import os
import re
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pymysql
from dotenv import load_dotenv
from legacy_config_secrets import leer_credencial_antigua
from telefono import normalizar_telefono
from whatsapp_channels import CENTROS, claves_centro

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")
LOGS_DIR = BASE_DIR / "logs"
_LOG_LOCK = threading.Lock()
_ZONA_LOG = ZoneInfo("America/Santiago")
_FECHA_LOG_ACTUAL = None
_NOMBRE_LOG_ACTUAL = None


def _nombre_log_para(ahora: datetime) -> str:
    """Un archivo por proceso y día, fechado a la hora de su primer error."""
    global _FECHA_LOG_ACTUAL, _NOMBRE_LOG_ACTUAL
    if _FECHA_LOG_ACTUAL != ahora.date():
        _FECHA_LOG_ACTUAL = ahora.date()
        _NOMBRE_LOG_ACTUAL = f"errores-{ahora:%Y-%m-%d_%H-%M-%S-%f}-{os.getpid()}.log"
    return _NOMBRE_LOG_ACTUAL


def _texto_log_seguro(valor: str) -> str:
    """Reduce PII y secretos antes de persistir un diagnóstico."""
    texto = re.sub(r"(?i)\bBearer\s+\S+", "Bearer [oculto]", str(valor))
    texto = re.sub(r"(?i)\b(token|secret|password|clave|api[_-]?key)\s*[:=]\s*[^\s,;]+",
                   r"\1=[oculto]", texto)
    texto = re.sub(r"(?i)[\w.+-]+@[\w.-]+\.[a-z]{2,}", "[correo]", texto)
    texto = re.sub(r"(?<!\d)\+?\d[\d ().-]{8,}\d(?!\d)", "[numero]", texto)
    return texto[:1500]


def _guardar_error_archivo(contexto: str, exc: BaseException | None = None) -> None:
    """Anexa al archivo fechado de este proceso; crea logs/ al necesitarlo."""
    ahora = datetime.now(_ZONA_LOG)
    mensaje = f"[{ahora:%Y-%m-%d %H:%M:%S%z}] [ERROR] {_texto_log_seguro(contexto)}"
    if exc is not None:
        codigo = exc.args[0] if exc.args and isinstance(exc.args[0], int) else None
        mensaje += f" | {type(exc).__name__}" + (f" ({codigo})" if codigo is not None else "")
        if exc.__traceback__ is not None:
            # format_tb incluye líneas de código que pueden contener valores
            # sensibles; conservar solo la ubicación de cada frame.
            for frame in traceback.extract_tb(exc.__traceback__):
                mensaje += (f"\n  {Path(frame.filename).name}:{frame.lineno} "
                            f"en {frame.name}")
    entrada = (mensaje + "\n").encode("utf-8", errors="replace")
    try:
        with _LOG_LOCK:
            LOGS_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
            ruta = LOGS_DIR / _nombre_log_para(ahora)
            descriptor = os.open(ruta, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                while entrada:
                    entrada = entrada[os.write(descriptor, entrada):]
            finally:
                os.close(descriptor)
    except OSError as error:
        print(f"[ERROR] No se pudo guardar el archivo de errores ({type(error).__name__}).",
              file=sys.stderr, flush=True)


class _ErroresEstandarArchivo(logging.Handler):
    """Guarda también errores emitidos por logging/ASGI sin alterar su consola."""

    def __init__(self):
        super().__init__(level=logging.ERROR)

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(record, "_snw_archivado", False):
            return
        record._snw_archivado = True
        excepcion = record.exc_info[1] if record.exc_info else None
        _guardar_error_archivo(f"{record.name}: {record.getMessage()}", excepcion)


_ARCHIVO_ERRORES_HANDLER = _ErroresEstandarArchivo()


def instalar_registro_errores() -> None:
    """Cubre errores del logger estándar y de Uvicorn en este proceso."""
    for nombre in ("", "uvicorn.error"):
        logger = logging.getLogger(nombre)
        if _ARCHIVO_ERRORES_HANDLER not in logger.handlers:
            logger.addHandler(_ARCHIVO_ERRORES_HANDLER)


def log_error(contexto: str, exc: BaseException | None = None,
              *, solo_tipo: bool = False) -> None:
    """Registra en consola (stderr) cualquier error, con su traza completa.

    Se usa en todos los bloques try/except del backend para que ningún
    fallo quede silenciado, aunque el flujo continúe."""
    descripcion = type(exc).__name__ if solo_tipo and exc is not None else repr(exc)
    print(f"[ERROR] {contexto}: {descripcion}" if exc is not None else f"[ERROR] {contexto}",
          file=sys.stderr, flush=True)
    if exc is not None and exc.__traceback__ is not None:
        if solo_tipo:
            traceback.print_tb(exc.__traceback__)
        else:
            traceback.print_exception(type(exc), exc, exc.__traceback__)
        sys.stderr.flush()
    _guardar_error_archivo(contexto, exc)


def _migrar_numeros_prueba_a_produccion(cur) -> None:
    """Copia autorizaciones antiguas de desarrollo una sola vez."""
    marcador = "numeros_prueba_prod_sync_v1"
    cur.execute("INSERT INTO configuracion (clave, valor) VALUES (%s, '0')"
                " ON DUPLICATE KEY UPDATE valor = valor", (marcador,))
    cur.execute("SELECT valor FROM configuracion WHERE clave = %s FOR UPDATE", (marcador,))
    if (cur.fetchone() or {}).get("valor") == "1":
        return
    cur.execute("SELECT clave, valor FROM configuracion"
                " WHERE clave IN ('numeros_prueba_dev', 'numeros_prueba_prod')"
                " ORDER BY clave FOR UPDATE")
    listas = {fila["clave"]: [n.strip() for n in (fila.get("valor") or "").split(",") if n.strip()]
              for fila in cur.fetchall()}
    produccion = listas.get("numeros_prueba_prod", [])
    existentes = {normalizar_telefono(n) for n in produccion}
    for valor in listas.get("numeros_prueba_dev", []):
        numero = normalizar_telefono(valor)
        if numero and numero not in existentes:
            produccion.append(numero)
            existentes.add(numero)
    cur.execute("UPDATE configuracion SET valor = %s WHERE clave = 'numeros_prueba_prod'",
                (",".join(produccion),))
    cur.execute("UPDATE configuracion SET valor = '1' WHERE clave = %s", (marcador,))

AMBIENTES = {"desarrollo", "produccion"}
TABLAS_PACIENTES = {"desarrollo": "pacientes_dev", "produccion": "pacientes_prod"}


def _migrar_permisos_envios_legacy(permisos: str, rol: str) -> str:
    """Conserva capacidades antiguas sin reotorgarlas tras la migración única."""
    anteriores = [p for p in (permisos or "").split(",") if p]
    nuevos = list(dict.fromkeys(p for p in anteriores if p != "envio_produccion"))
    def agregar(permiso):
        if permiso not in nuevos:
            nuevos.append(permiso)
    if "mensajeria" in anteriores:
        for permiso in ("csv_subir", "envio_manual", "envio_programado"):
            agregar(permiso)
    # No otorgar acceso ampliado a PII durante la migración: antes solo se
    # podían ver datos completos de los pacientes cargados por la propia cuenta.
    if "envio_produccion" in anteriores:
        agregar("envio_manual_directo")
        agregar("envio_programado_directo")
    elif rol == "supervisor" and "envio_programado" in nuevos:
        agregar("envio_programado_directo")
    return ",".join(nuevos)


def _migrar_permisos_acciones_legacy(permisos: str, rol: str) -> str:
    """Una sola vez conserva capacidades existentes al separar acciones sensibles."""
    anteriores = [p for p in (permisos or "").split(",") if p]
    nuevos = list(dict.fromkeys(anteriores))
    def agregar(permiso):
        if permiso not in nuevos:
            nuevos.append(permiso)
    if "mensajeria" in anteriores:
        agregar("pacientes_vaciar")
        agregar("envios_cancelar")
        if rol == "supervisor":
            agregar("envios_decidir")
    if "plantillas_editar" in anteriores:
        agregar("plantillas_eliminar")
    if "call_center_registro" in anteriores and "pacientes_ver_datos" in anteriores:
        agregar("call_center_reintentar")
    return ",".join(nuevos)


# ---------------------------------------------------------------------------
# TODA la configuración vive en la tabla `configuracion` (clave/valor) y se
# edita desde la página de Configuración. En .env quedan solo las credenciales
# de la BASE DE DATOS (DB_*), necesarias para consultar la configuración.
# La primera vez que arranca el backend, la tabla se
# siembra con estos valores por defecto.
# ---------------------------------------------------------------------------
CONFIG_DEFAULTS = {
    # App / envío
    "entorno": "desarrollo",
    "metodo_envio": "simulado",
    "numeros_prueba_dev": "",
    "numeros_prueba_prod": "",
    # Sesiones: horas de INACTIVIDAD tras las que una sesión expira sola
    # (se renueva con cada acción; 0 = no expiran).
    "sesion_expira_horas": "5",
    # Clave histórica conservada para no romper configuraciones existentes.
    # La baja y la reincorporación ahora pueden repetirse sin límite.
    "anti_flip_flop_dev": "true",
    "intervalo_ms": "1000",
    "url_base": "",
    # Claves históricas de call center: se conservan para no borrar datos de
    # instalaciones anteriores, pero el botón actual usa *_CRM_PHONE_NUMBER
    # del emisor y el mensaje preparado fijo.
    "call_center_url": "https://saludmentalparatodos.cl/telefonosmpt.php",
    "call_center_numeros": "",
    "call_center_boton_mensaje": "Hola, estoy interesado/a en la información que me enviaron.",
    "call_center_boton_mensaje_oferta": "Hola, estoy interesado/a en la oferta que me enviaron.",
    "call_center_aviso_consulta": (
        "🤖 Mensaje automático: Recibimos tu consulta. Pulsa «Ir a call center» "
        "y envía el mensaje preparado para que una persona pueda atenderte."
    ),
    "call_center_patrones_consulta": "{}",
    # Correo (confirmación de envíos en producción)
    "smtp_host": "",
    "smtp_port": "587",
    "smtp_user": "",
    "smtp_pass": "",
    "smtp_tls": "true",
    "correo_emisor": "",
    "correo_destino": "",
    # WhatsApp Business Cloud API (Meta)
    "wa_token": "",
    "wa_phone_id": "",
    "wa_clinicas_adicionales": "[]",
    # Integración de citas atendidas: la credencial se genera por API y solo
    # su huella se conserva aquí. El despacho permanece apagado por defecto.
    "reagendar_api_token_hash": "",
    "reagendar_cors_origen": "https://clientes.crmaxion.cl",
    "reagendar_envio_activo": "false",
    "reagendar_plantillas_json": "{}",
    "reagendar_clinicas_ids": "{}",
    "wa_app_id": "",
    "wa_verify_token": "",
    "wa_app_secret": "",
    "wa_template_nombre": "",
    "wa_template_lang": "es",
    "wa_webhook_path": "/api/whatsapp/webhook",
    "wa_graph_version": "v26.0",
    "wa_moneda": "USD",
    # La clave de Groq vive en configuracion. Conservamos las claves antiguas de
    # Gemini para no borrar credenciales ni preferencias durante la migración.
    "gemini_api_key": "",
    "gemini_limite_hora": "12",
    "groq_api_key": "",
    "groq_limite_hora": "12",
    # Cada tantos minutos se revisa en Meta el estado de las plantillas que
    # todavía no están aprobadas (para detectar la aprobación/el rechazo
    # solas, sin que alguien tenga que consultarlo a mano). Al arrancar el
    # servidor el primer chequeo sale rápido (20 s), no espera el intervalo
    # completo. 0 = desactivado.
    "plantillas_revision_minutos": "2",
    # Cuánto tiempo (minutos) se muestra el aviso «Aprobada recientemente»
    # (lista y editor de plantillas) después de detectar la aprobación.
    "plantillas_badge_aprobada_minutos": "10",
    # Rate limits de la Graph API de Meta. El sistema lee las cabeceras de uso de
    # cuota (`X-App-Usage` / `X-Business-Use-Case-Usage`) y frena los envíos antes
    # de chocar con el límite; ante un 429 espera el tiempo que indica Meta.
    #   wa_rate_limit_activo         : frenado proactivo on/off (el 429 se respeta igual).
    #   wa_rate_limit_umbral_pct     : a partir de este % de cuota se empieza a esperar.
    #   wa_rate_limit_pausa_max_s    : espera máxima entre mensajes al 100% de cuota.
    #   wa_rate_limit_espera_defecto_s: espera tras un 429 sin dato de recuperación.
    #   wa_rate_limit_reintentos     : reintentos de una misma llamada tras un 429.
    "wa_rate_limit_activo": "true",
    "wa_rate_limit_umbral_pct": "80",
    "wa_rate_limit_pausa_max_s": "30",
    "wa_rate_limit_espera_defecto_s": "60",
    "wa_rate_limit_reintentos": "3",
    # Throughput de la Cloud API (msg/s por número; Meta permite 80 por defecto,
    # cuenta entrantes + salientes). Se deja margen. 0 = sin límite de ritmo.
    "wa_throughput_mps": "10",
    # Presupuesto compartido entre procesos por Phone Number ID. La capacidad
    # ampliada nunca se activa automáticamente; el techo seguro sigue en 60/s.
    "wa_dispatch_pair_seconds": "6",
    "wa_dispatch_reserved_percent": "25",
    "wa_dispatch_max_retries": "3",
    "wa_dispatch_backoff_base_seconds": "2",
    "wa_dispatch_backoff_max_seconds": "60",
    # Messaging limit: usuarios ÚNICOS a los que se puede escribir (mensajes
    # iniciados por el negocio) en 24 h. Tiers de Meta: 250 / 2000 / 10000 /
    # 100000 / ilimitado. 0 = sin control. Ajústalo al tier real de la cuenta.
    "wa_messaging_limit_24h": "2000",
}
for _centro in CENTROS:
    for _clave in claves_centro(_centro):
        CONFIG_DEFAULTS[_clave] = ""
    CONFIG_DEFAULTS[f"{_centro}_WSP_RATE_LIMIT_MPS"] = "10"
    CONFIG_DEFAULTS[f"{_centro}_WSP_RATE_LIMIT_MODE"] = "estandar"
    CONFIG_DEFAULTS[f"{_centro}_WSP_RATE_LIMIT_VERIFIED"] = "false"
    CONFIG_DEFAULTS[f"{_centro}_WSP_WABA_ID"] = ""
    CONFIG_DEFAULTS[f"{_centro}_CRM_PHONE_NUMBER"] = ""

_CLAVES_CENTRO_ANTIGUAS = {
    "SMPT_WSP_PHONE_NUMER": "SMPT_WSP_PHONE_NUMBER",
    "SMPT_WSP_PHONE_NUMER_ID": "SMPT_WSP_PHONE_NUMBER_ID",
    "MS_WSP_CRM_PHONE_NUMER": "MS_WSP_PHONE_NUMBER",
    "MS_WSP_CRM_PHONE_NUMER_ID": "MS_WSP_PHONE_NUMBER_ID",
    "PS_WSP_CRM_PHONE_NUMER": "PS_WSP_PHONE_NUMBER",
    "PS_WSP_CRM_PHONE_NUMER_ID": "PS_WSP_PHONE_NUMBER_ID",
    "CP_WSP_CRM_PHONE_NUMER": "CP_WSP_PHONE_NUMBER",
    "CP_WSP_CRM_PHONE_NUMER_ID": "CP_WSP_PHONE_NUMBER_ID",
}


def _migrar_claves_centro(cur) -> None:
    """Traslada claves mal escritas sin pisar datos ni borrar conflictos."""
    claves = tuple(_CLAVES_CENTRO_ANTIGUAS)
    cur.execute("SELECT clave, valor FROM configuracion WHERE clave IN ("
                + ", ".join("%s" for _ in claves) + ") FOR UPDATE", claves)
    for fila in cur.fetchall():
        vieja = fila["clave"]
        nueva = _CLAVES_CENTRO_ANTIGUAS[vieja]
        valor = (fila.get("valor") or "").strip()
        cur.execute("SELECT valor FROM configuracion WHERE clave = %s FOR UPDATE", (nueva,))
        actual = ((cur.fetchone() or {}).get("valor") or "").strip()
        if actual and valor and actual != valor:
            log_error(f"Migración de {vieja}: conflicto con {nueva}; conservadas ambas claves")
            continue
        if valor and not actual:
            cur.execute("UPDATE configuracion SET valor = %s WHERE clave = %s", (valor, nueva))
        cur.execute("DELETE FROM configuracion WHERE clave = %s", (vieja,))

_config_cache: dict | None = None
_config_cache_cargado: float = 0.0


def asegurar_tabla_config() -> None:
    """Crea la tabla `configuracion` si no existe y la siembra una sola vez."""
    global _config_cache
    try:
        with conectar() as conn, conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS configuracion ("
                "  clave VARCHAR(60) PRIMARY KEY,"
                "  valor TEXT,"
                "  actualizada DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"
                ") CHARACTER SET utf8mb4"
            )
            for clave, valor in CONFIG_DEFAULTS.items():
                cur.execute(
                    "INSERT IGNORE INTO configuracion (clave, valor) VALUES (%s, %s)",
                    (clave, valor),
                )
            # El WABA global ya no participa en ningún flujo. Retirar también
            # la fila heredada para que no reaparezca en configuraciones.
            cur.execute("DELETE FROM configuracion WHERE clave = 'wa_business_account_id'")
            _migrar_claves_centro(cur)
            cur.execute(
                "CREATE TABLE IF NOT EXISTS reagendar_lotes ("
                " id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,"
                " fecha DATE NOT NULL, ventana VARCHAR(5) NOT NULL,"
                " recibido_en DATETIME NOT NULL, programado_para DATETIME NULL,"
                " estado VARCHAR(20) NOT NULL DEFAULT 'REVISION',"
                " aplazado_en DATETIME NULL, aplazado_por VARCHAR(100) NULL,"
                " motivo_aplazamiento VARCHAR(255) NULL,"
                " creados INT UNSIGNED NOT NULL DEFAULT 0,"
                " rechazados INT UNSIGNED NOT NULL DEFAULT 0,"
                " repetidos INT UNSIGNED NOT NULL DEFAULT 0,"
                " recibidos INT UNSIGNED NOT NULL DEFAULT 0,"
                " KEY idx_reagendar_lote_vencimiento (estado, programado_para)"
                ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
            )
            cur.execute("SELECT COLUMN_NAME FROM information_schema.COLUMNS"
                        " WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='reagendar_lotes'")
            _columnas_lote = {r["COLUMN_NAME"] for r in cur.fetchall()}
            for _columna in ("repetidos", "recibidos"):
                if _columna not in _columnas_lote:
                    cur.execute(f"ALTER TABLE reagendar_lotes ADD COLUMN {_columna}"
                                " INT UNSIGNED NOT NULL DEFAULT 0")
            cur.execute(
                "CREATE TABLE IF NOT EXISTS paciente_reagendar ("
                " id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,"
                " lote_id BIGINT UNSIGNED NULL,"
                " fecha DATE NOT NULL, ventana VARCHAR(5) NOT NULL,"
                " clinica_id INT NULL, clinica_nombre VARCHAR(100) NOT NULL,"
                " reservo_cita_uuid VARCHAR(80) COLLATE utf8mb4_bin NOT NULL,"
                " paciente_nombre VARCHAR(200) NOT NULL,"
                " paciente_telefono VARCHAR(40) NULL,"
                " paciente_telefono_normalizado VARCHAR(20) NULL,"
                " doctor_nombre VARCHAR(200) NULL, inicio_cita DATETIME NOT NULL,"
                " centro_codigo VARCHAR(10) NULL,"
                " autorizado_contacto TINYINT(1) NOT NULL DEFAULT 0,"
                " modo_prueba TINYINT(1) NOT NULL DEFAULT 0,"
                " whatsapp_opt_out TINYINT(1) NOT NULL DEFAULT 0,"
                " estado_envio VARCHAR(20) NOT NULL DEFAULT 'PENDIENTE',"
                " intentos_envio INT UNSIGNED NOT NULL DEFAULT 0,"
                " reservado_en DATETIME NULL, reserva_token CHAR(32) NULL,"
                " ultimo_intento_en DATETIME NULL, proximo_reintento_en DATETIME NULL,"
                " enviado_en DATETIME NULL, whatsapp_message_id VARCHAR(255) NULL,"
                " whatsapp_phone_number_id VARCHAR(100) NULL,"
                " whatsapp_numero_emisor VARCHAR(40) NULL,"
                " plantilla_clave VARCHAR(80) NULL, envio_id INT NULL, log_envio_id INT NULL,"
                " ultimo_error_codigo VARCHAR(60) NULL,"
                " ultimo_error_detalle VARCHAR(255) NULL,"
                " importado_en TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,"
                " actualizado_en TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP"
                " ON UPDATE CURRENT_TIMESTAMP,"
                " UNIQUE KEY uq_paciente_reagendar_cita (reservo_cita_uuid),"
                " KEY idx_reagendar_estado (estado_envio, proximo_reintento_en),"
                " KEY idx_reagendar_centro (centro_codigo, estado_envio),"
                " KEY idx_reagendar_telefono (paciente_telefono_normalizado, modo_prueba),"
                " KEY idx_reagendar_fecha (fecha, ventana),"
                " KEY idx_reagendar_wa_id (whatsapp_message_id)"
                ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
            )
            cur.execute("SELECT COLUMN_NAME FROM information_schema.COLUMNS"
                        " WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='paciente_reagendar'"
                        " AND COLUMN_NAME='envio_id'")
            if not cur.fetchone():
                cur.execute("ALTER TABLE paciente_reagendar ADD COLUMN envio_id INT NULL")
            cur.execute("SELECT COLUMN_NAME FROM information_schema.COLUMNS"
                        " WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='paciente_reagendar'"
                        " AND COLUMN_NAME='lote_id'")
            if not cur.fetchone():
                cur.execute("ALTER TABLE paciente_reagendar ADD COLUMN lote_id BIGINT UNSIGNED NULL")
            cur.execute("SELECT COLUMN_NAME FROM information_schema.COLUMNS"
                        " WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='paciente_reagendar'"
                        " AND COLUMN_NAME='whatsapp_opt_out'")
            if not cur.fetchone():
                cur.execute("ALTER TABLE paciente_reagendar ADD COLUMN whatsapp_opt_out"
                            " TINYINT(1) NOT NULL DEFAULT 0")
            cur.execute("SELECT COLUMN_NAME FROM information_schema.COLUMNS"
                        " WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='paciente_reagendar'"
                        " AND COLUMN_NAME='modo_prueba'")
            if not cur.fetchone():
                cur.execute("ALTER TABLE paciente_reagendar ADD COLUMN modo_prueba"
                            " TINYINT(1) NOT NULL DEFAULT 0")
            cur.execute("SELECT INDEX_NAME FROM information_schema.STATISTICS"
                        " WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='paciente_reagendar'"
                        " AND INDEX_NAME='idx_reagendar_lote'")
            if not cur.fetchone():
                cur.execute("ALTER TABLE paciente_reagendar ADD KEY idx_reagendar_lote (lote_id)")
            cur.execute("SELECT INDEX_NAME FROM information_schema.STATISTICS"
                        " WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='paciente_reagendar'"
                        " AND INDEX_NAME='idx_reagendar_telefono'")
            if not cur.fetchone():
                cur.execute("ALTER TABLE paciente_reagendar ADD KEY idx_reagendar_telefono"
                            " (paciente_telefono_normalizado, modo_prueba)")
            cur.execute(
                "CREATE TABLE IF NOT EXISTS reagendar_destinatarios ("
                " telefono VARCHAR(20) NOT NULL,"
                " modo_prueba TINYINT(1) NOT NULL DEFAULT 0,"
                " cita_id BIGINT UNSIGNED NOT NULL,"
                " estado VARCHAR(20) NOT NULL DEFAULT 'PENDIENTE',"
                " PRIMARY KEY (telefono, modo_prueba),"
                " KEY idx_reagendar_destinatario_cita (cita_id)"
                ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
            )
            # Conserva la primera cita enviada si existían duplicados anteriores.
            # La clave única impide nuevos destinatarios duplicados entre instancias.
            cur.execute(
                "INSERT IGNORE INTO reagendar_destinatarios"
                " (telefono, modo_prueba, cita_id, estado)"
                " SELECT paciente_telefono_normalizado, modo_prueba, id,"
                " CASE WHEN estado_envio='ENVIADO' THEN 'ENVIADO' ELSE 'PENDIENTE' END"
                " FROM paciente_reagendar"
                " WHERE paciente_telefono_normalizado IS NOT NULL"
                " ORDER BY CASE WHEN estado_envio='ENVIADO' THEN 0"
                " WHEN estado_envio IN ('PENDIENTE','REINTENTO','PROCESANDO')"
                " AND autorizado_contacto=1 AND centro_codigo IS NOT NULL"
                " AND ultimo_error_codigo IS NULL THEN 1 ELSE 2 END, id"
            )
            cur.execute(
                "UPDATE paciente_reagendar r JOIN reagendar_destinatarios d"
                " ON d.telefono=r.paciente_telefono_normalizado"
                " AND d.modo_prueba=r.modo_prueba"
                " SET r.estado_envio='OMITIDO',"
                " r.ultimo_error_codigo='PACIENTE_DUPLICADO'"
                " WHERE r.id<>d.cita_id"
                " AND r.estado_envio IN ('PENDIENTE','REINTENTO')"
            )
            cur.execute(
                "UPDATE reagendar_destinatarios d JOIN ("
                " SELECT paciente_telefono_normalizado AS telefono, modo_prueba,"
                " MIN(id) AS cita_id FROM paciente_reagendar"
                " WHERE estado_envio='ENVIADO'"
                " AND paciente_telefono_normalizado IS NOT NULL"
                " GROUP BY paciente_telefono_normalizado, modo_prueba"
                " ) enviados ON enviados.telefono=d.telefono"
                " AND enviados.modo_prueba=d.modo_prueba"
                " SET d.estado='ENVIADO', d.cita_id=enviados.cita_id"
                " WHERE d.estado<>'ENVIADO'"
            )
            cur.execute(
                "CREATE TABLE IF NOT EXISTS reagendar_flujo ("
                " cita_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,"
                " estado VARCHAR(30) NOT NULL, actualizado_en DATETIME NOT NULL,"
                " ultimo_mensaje_id VARCHAR(255) NULL,"
                " CONSTRAINT fk_reagendar_flujo_cita FOREIGN KEY (cita_id)"
                " REFERENCES paciente_reagendar(id) ON DELETE CASCADE"
                ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
            )
            cur.execute(
                "CREATE TABLE IF NOT EXISTS reagendar_interacciones ("
                " mensaje_id VARCHAR(255) COLLATE utf8mb4_bin NOT NULL PRIMARY KEY,"
                " cita_id BIGINT UNSIGNED NOT NULL, accion VARCHAR(30) NOT NULL,"
                " texto TEXT NULL, respuesta_id VARCHAR(255) NULL,"
                " respuesta_estado VARCHAR(20) NOT NULL DEFAULT 'PENDIENTE',"
                " creado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,"
                " KEY idx_reagendar_interaccion_cita (cita_id),"
                " CONSTRAINT fk_reagendar_interaccion_cita FOREIGN KEY (cita_id)"
                " REFERENCES paciente_reagendar(id) ON DELETE CASCADE"
                ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
            )
            cur.execute(
                "CREATE TABLE IF NOT EXISTS reagendar_import_quota ("
                " actor_hash CHAR(64) NOT NULL, ventana BIGINT NOT NULL,"
                " intentos INT UNSIGNED NOT NULL DEFAULT 0,"
                " PRIMARY KEY (actor_hash, ventana)"
                ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
            )
            cur.execute(
                "CREATE TABLE IF NOT EXISTS wa_despacho_canales ("
                " phone_id VARCHAR(64) PRIMARY KEY,"
                " proximo_turno DATETIME(6) NULL,"
                " bloqueado_hasta DATETIME(6) NULL,"
                " mps_actual INT NULL,"
                " aceptados_desde_ajuste INT NOT NULL DEFAULT 0,"
                " ajustado_en DATETIME(6) NULL,"
                " errores_130429 BIGINT NOT NULL DEFAULT 0,"
                " errores_131057 BIGINT NOT NULL DEFAULT 0,"
                " enviados BIGINT NOT NULL DEFAULT 0,"
                " actualizado DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)"
                " ON UPDATE CURRENT_TIMESTAMP(6)"
                ") CHARACTER SET utf8mb4"
            )
            cur.execute("SELECT COLUMN_NAME FROM information_schema.COLUMNS"
                        " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'wa_despacho_canales'")
            _columnas_despacho = {r["COLUMN_NAME"] for r in cur.fetchall()}
            for _nombre, _tipo in (("mps_actual", "INT NULL"),
                                   ("aceptados_desde_ajuste", "INT NOT NULL DEFAULT 0"),
                                   ("ajustado_en", "DATETIME(6) NULL")):
                if _nombre not in _columnas_despacho:
                    cur.execute(f"ALTER TABLE wa_despacho_canales ADD COLUMN {_nombre} {_tipo}")
            cur.execute(
                "CREATE TABLE IF NOT EXISTS wa_despacho_pares ("
                " phone_id VARCHAR(64) NOT NULL,"
                " destinatario_hash CHAR(64) NOT NULL,"
                " proximo_turno DATETIME(6) NOT NULL,"
                " PRIMARY KEY (phone_id, destinatario_hash),"
                " INDEX idx_pares_expira (proximo_turno)"
                ") CHARACTER SET utf8mb4"
            )
            cur.execute(
                "CREATE TABLE IF NOT EXISTS gemini_cuotas ("
                "  usuario_hash CHAR(64) NOT NULL,"
                "  ventana_hora BIGINT NOT NULL,"
                "  solicitudes INT NOT NULL DEFAULT 0,"
                "  PRIMARY KEY (usuario_hash, ventana_hora)"
                ") CHARACTER SET utf8mb4"
            )
            cur.execute(
                "CREATE TABLE IF NOT EXISTS groq_cuotas ("
                "  usuario_hash CHAR(64) NOT NULL,"
                "  ventana_hora BIGINT NOT NULL,"
                "  solicitudes INT NOT NULL DEFAULT 0,"
                "  PRIMARY KEY (usuario_hash, ventana_hora)"
                ") CHARACTER SET utf8mb4"
            )
            # Etiquetas de revisión humana: separadas de la respuesta original
            # y de los estados que gobiernan baja, interés y envíos.
            cur.execute(
                "CREATE TABLE IF NOT EXISTS respuesta_etiquetas ("
                "  log_envio_id INT NOT NULL PRIMARY KEY,"
                "  etiquetas VARCHAR(512) NOT NULL,"
                "  origen ENUM('automatico','manual') NOT NULL DEFAULT 'manual',"
                "  actualizado_por VARCHAR(150) NOT NULL,"
                "  ofensa_detalle TEXT NULL,"
                "  actualizado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
                "    ON UPDATE CURRENT_TIMESTAMP"
                ") CHARACTER SET utf8mb4"
            )
            cur.execute(
                "SELECT COLUMN_NAME FROM information_schema.COLUMNS"
                " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'respuesta_etiquetas'"
                " AND COLUMN_NAME = 'origen'"
            )
            if not cur.fetchone():
                # Filas previas fueron creadas por una revisión humana.
                cur.execute(
                    "ALTER TABLE respuesta_etiquetas ADD COLUMN origen"
                    " ENUM('automatico','manual') NOT NULL DEFAULT 'manual'"
                )
            cur.execute(
                "SELECT COLUMN_NAME FROM information_schema.COLUMNS"
                " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'respuesta_etiquetas'"
                " AND COLUMN_NAME = 'ofensa_detalle'"
            )
            if not cur.fetchone():
                cur.execute("ALTER TABLE respuesta_etiquetas"
                            " ADD COLUMN ofensa_detalle TEXT NULL")
            # Corpus sin vínculo al paciente ni metadatos de recepción.
            # Solo almacena la representación de vocabulario cerrado.
            cur.execute(
                "CREATE TABLE IF NOT EXISTS corpus_respuestas ("
                " id BIGINT AUTO_INCREMENT PRIMARY KEY,"
                " texto_desidentificado TEXT NOT NULL"
                ") CHARACTER SET utf8mb4"
            )
            # El corpus anterior se conserva como legado: sus sustituciones
            # [otro] no permiten recuperar el texto original.
            cur.execute(
                "CREATE TABLE IF NOT EXISTS corpus_respuestas_completas ("
                " id BIGINT AUTO_INCREMENT PRIMARY KEY,"
                " mensaje MEDIUMTEXT NOT NULL"
                ") ENGINE=InnoDB CHARACTER SET utf8mb4"
            )
            cur.execute(
                "CREATE TABLE IF NOT EXISTS corpus_respuestas_origen ("
                " corpus_id BIGINT NOT NULL PRIMARY KEY,"
                " huella CHAR(64) NOT NULL,"
                " KEY idx_corpus_origen_huella (huella),"
                " CONSTRAINT fk_corpus_origen_mensaje FOREIGN KEY (corpus_id)"
                " REFERENCES corpus_respuestas_completas(id) ON DELETE CASCADE"
                ") ENGINE=InnoDB CHARACTER SET utf8mb4"
            )
            cur.execute(
                "SELECT 1 FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE()"
                " AND TABLE_NAME = 'log_envios'"
            )
            if cur.fetchone():
                cur.execute("SELECT COALESCE(MAX(id), 0) AS ultimo FROM log_envios")
                corte = (cur.fetchone() or {}).get("ultimo", 0)
                cur.execute(
                    "INSERT IGNORE INTO configuracion (clave, valor) VALUES"
                    " ('corpus_respuestas_corte_v1', %s)", (str(corte),)
                )
                cur.execute(
                    "INSERT IGNORE INTO configuracion (clave, valor) VALUES"
                    " ('corpus_respuestas_corte_v2', %s)", (str(corte),)
                )
            _migrar_numeros_prueba_a_produccion(cur)
            # Rate card de WhatsApp (tarifas por mensaje descargadas de Meta).
            cur.execute(
                "CREATE TABLE IF NOT EXISTS tarifas_whatsapp ("
                "  id INT AUTO_INCREMENT PRIMARY KEY,"
                "  pais VARCHAR(60) NOT NULL DEFAULT 'Chile',"
                "  moneda VARCHAR(8) DEFAULT 'USD',"
                "  marketing DECIMAL(12,6) NULL,"
                "  utility DECIMAL(12,6) NULL,"
                "  authentication DECIMAL(12,6) NULL,"
                "  service DECIMAL(12,6) NULL,"
                "  efectiva_desde DATE NULL,"
                "  hash CHAR(64) NOT NULL,"
                "  fuente VARCHAR(255) NULL,"
                "  csv_texto MEDIUMTEXT NULL,"
                "  descargada DATETIME DEFAULT CURRENT_TIMESTAMP,"
                "  UNIQUE KEY uq_hash (hash)"
                ") CHARACTER SET utf8mb4"
            )
            # Log de las respuestas enviadas a pacientes interesados (mensaje de
            # call center): registra qué número de call center se asignó, para
            # repartir la carga entre los números configurados.
            cur.execute(
                "CREATE TABLE IF NOT EXISTS call_center_log ("
                "  id INT AUTO_INCREMENT PRIMARY KEY,"
                "  paciente_id INT DEFAULT NULL,"
                "  nombre_paciente VARCHAR(150) DEFAULT NULL,"
                "  numero_paciente VARCHAR(20) DEFAULT NULL,"
                "  numero_call_center VARCHAR(20) NOT NULL,"
                "  plantilla_clave VARCHAR(50) DEFAULT NULL,"
                "  automatico TINYINT(1) NOT NULL DEFAULT 0,"
                "  estado ENUM('enviado','error') NOT NULL DEFAULT 'enviado',"
                "  descripcion_error VARCHAR(255) DEFAULT NULL,"
                "  base_datos VARCHAR(50) DEFAULT NULL,"
                "  vista_mensaje TEXT DEFAULT NULL,"
                "  fecha_hora DATETIME DEFAULT CURRENT_TIMESTAMP,"
                "  INDEX idx_numero (numero_call_center),"
                "  INDEX idx_fecha (fecha_hora)"
                ") CHARACTER SET utf8mb4"
            )
            cur.execute(
                "SELECT COLUMN_NAME FROM information_schema.COLUMNS"
                " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'call_center_log'"
                " AND COLUMN_NAME = 'vista_mensaje'"
            )
            if not cur.fetchone():
                cur.execute("ALTER TABLE call_center_log ADD COLUMN vista_mensaje TEXT NULL")
            cur.execute(
                "CREATE TABLE IF NOT EXISTS call_center_prueba_interes ("
                " mensaje_id VARCHAR(255) PRIMARY KEY,"
                " telefono VARCHAR(20) NOT NULL,"
                " creado DATETIME DEFAULT CURRENT_TIMESTAMP,"
                " INDEX idx_cc_prueba_telefono (telefono)"
                ") CHARACTER SET utf8mb4"
            )
            cur.execute(
                "CREATE TABLE IF NOT EXISTS call_center_derivaciones ("
                " id BIGINT AUTO_INCREMENT PRIMARY KEY,"
                " telefono VARCHAR(20) NOT NULL,"
                " mensaje_original TEXT NOT NULL,"
                " motivos VARCHAR(255) NOT NULL,"
                " estado ENUM('pendiente','notificado','error','finalizado') NOT NULL DEFAULT 'pendiente',"
                " numero_call_center VARCHAR(20) DEFAULT NULL,"
                " base_datos VARCHAR(50) DEFAULT NULL,"
                " paciente_id INT DEFAULT NULL,"
                " aviso_id VARCHAR(255) DEFAULT NULL,"
                " centro_codigo VARCHAR(8) DEFAULT NULL,"
                " wa_phone_id VARCHAR(64) DEFAULT NULL,"
                " creado DATETIME DEFAULT CURRENT_TIMESTAMP,"
                " actualizado DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,"
                " INDEX idx_telefono_estado (telefono, estado, creado),"
                " INDEX idx_creado (creado)"
                ") CHARACTER SET utf8mb4"
            )
            cur.execute(
                "SELECT COLUMN_NAME FROM information_schema.COLUMNS"
                " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'call_center_derivaciones'"
                " AND COLUMN_NAME IN ('centro_codigo','wa_phone_id')"
            )
            _cols_derivacion = {r["COLUMN_NAME"] for r in cur.fetchall()}
            if "centro_codigo" not in _cols_derivacion:
                cur.execute("ALTER TABLE call_center_derivaciones ADD COLUMN centro_codigo VARCHAR(8) NULL")
            if "wa_phone_id" not in _cols_derivacion:
                cur.execute("ALTER TABLE call_center_derivaciones ADD COLUMN wa_phone_id VARCHAR(64) NULL")
            cur.execute(
                "CREATE TABLE IF NOT EXISTS call_center_derivacion_mensajes ("
                " mensaje_id VARCHAR(255) PRIMARY KEY,"
                " derivacion_id BIGINT NOT NULL,"
                " creado DATETIME DEFAULT CURRENT_TIMESTAMP,"
                " INDEX idx_derivacion (derivacion_id),"
                " FOREIGN KEY (derivacion_id) REFERENCES call_center_derivaciones(id)"
                " ON DELETE CASCADE"
                ") CHARACTER SET utf8mb4"
            )
            # Cola local para la futura integración CRM. No se despacha nada
            # fuera de SNW hasta que exista contrato de API y autorización.
            cur.execute(
                "CREATE TABLE IF NOT EXISTS crm_derivaciones_pendientes ("
                " id BIGINT AUTO_INCREMENT PRIMARY KEY,"
                " mensaje_id VARCHAR(255) NOT NULL UNIQUE,"
                " telefono VARCHAR(20) NOT NULL,"
                " mensaje TEXT NOT NULL,"
                " motivos VARCHAR(255) NOT NULL,"
                " centro_codigo VARCHAR(8) NOT NULL,"
                " wa_phone_id VARCHAR(64) NOT NULL,"
                " numero_emisor VARCHAR(20) NOT NULL,"
                " contexto_json MEDIUMTEXT NULL,"
                " estado VARCHAR(20) NOT NULL DEFAULT 'pendiente',"
                " creado DATETIME DEFAULT CURRENT_TIMESTAMP,"
                " INDEX idx_crm_centro_estado (centro_codigo, estado, creado)"
                ") CHARACTER SET utf8mb4"
            )
            cur.execute(
                "SELECT COLUMN_NAME FROM information_schema.COLUMNS"
                " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'crm_derivaciones_pendientes'"
                " AND COLUMN_NAME IN ('numero_emisor', 'contexto_json')"
            )
            _crm_cols = {fila["COLUMN_NAME"] for fila in cur.fetchall()}
            if "numero_emisor" not in _crm_cols:
                cur.execute("ALTER TABLE crm_derivaciones_pendientes"
                            " ADD COLUMN numero_emisor VARCHAR(20) NOT NULL DEFAULT ''")
            if "contexto_json" not in _crm_cols:
                cur.execute("ALTER TABLE crm_derivaciones_pendientes"
                            " ADD COLUMN contexto_json MEDIUMTEXT NULL")
            cur.execute(
                "CREATE TABLE IF NOT EXISTS whatsapp_conversaciones_mensajes ("
                " mensaje_id VARCHAR(255) PRIMARY KEY,"
                " centro_codigo VARCHAR(8) NOT NULL,"
                " wa_phone_id VARCHAR(64) NOT NULL,"
                " numero_emisor VARCHAR(20) NOT NULL,"
                " telefono_paciente VARCHAR(20) NOT NULL,"
                " tipo VARCHAR(30) NOT NULL,"
                " texto TEXT NULL,"
                " recibido DATETIME DEFAULT CURRENT_TIMESTAMP,"
                " INDEX idx_conversacion (wa_phone_id, telefono_paciente, recibido)"
                ") CHARACTER SET utf8mb4"
            )
            # Cuentas de la aplicación (antes en data/usuarios.json).
            cur.execute(
                "CREATE TABLE IF NOT EXISTS usuarios ("
                "  id INT AUTO_INCREMENT PRIMARY KEY,"
                "  usuario VARCHAR(150) NOT NULL,"
                "  nombre VARCHAR(150) NOT NULL DEFAULT '',"
                "  rol ENUM('usuario','supervisor','administrador','desarrollador') NOT NULL DEFAULT 'usuario',"
                "  permisos VARCHAR(500) NOT NULL DEFAULT '',"
                "  clave_hash CHAR(64) NOT NULL,"
                "  correo_recuperacion VARCHAR(150) NOT NULL DEFAULT '',"
                "  creado DATETIME DEFAULT CURRENT_TIMESTAMP,"
                "  UNIQUE KEY uq_usuario (usuario)"
                ") CHARACTER SET utf8mb4"
            )
            # Correo al que llega el enlace de «Olvidé mi contraseña». Para las
            # cuentas cuyo usuario ya es un correo se copia ahí; admin/dev lo
            # completan desde «Mi cuenta».
            cur.execute(
                "SELECT COUNT(*) AS n FROM information_schema.COLUMNS"
                " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'usuarios'"
                "   AND COLUMN_NAME = 'correo_recuperacion'"
            )
            if not (cur.fetchone() or {}).get("n"):
                cur.execute("ALTER TABLE usuarios ADD COLUMN correo_recuperacion VARCHAR(150) NOT NULL DEFAULT ''")
            # Cuenta activa/desactivada: un admin/dev puede bloquear el acceso de
            # una cuenta sin borrarla. Todas nacen activas.
            cur.execute(
                "SELECT COUNT(*) AS n FROM information_schema.COLUMNS"
                " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'usuarios'"
                "   AND COLUMN_NAME = 'activo'"
            )
            if not (cur.fetchone() or {}).get("n"):
                cur.execute("ALTER TABLE usuarios ADD COLUMN activo TINYINT(1) NOT NULL DEFAULT 1")
            # Enlaces temporales de restablecimiento de contraseña (2 h).
            cur.execute(
                "CREATE TABLE IF NOT EXISTS password_resets ("
                "  token CHAR(64) PRIMARY KEY,"
                "  usuario VARCHAR(150) NOT NULL,"
                "  creado DATETIME DEFAULT CURRENT_TIMESTAMP,"
                "  expira DATETIME NOT NULL,"
                "  usado TINYINT(1) NOT NULL DEFAULT 0,"
                "  INDEX idx_pr_usuario (usuario)"
                ") CHARACTER SET utf8mb4"
            )
            cur.execute("DELETE FROM password_resets WHERE usado = 1 OR expira < DATE_SUB(NOW(), INTERVAL 7 DAY)")
            # Enlaces temporales de invitación para crear una cuenta (48 h).
            # Los envía un admin/dev desde Usuarios -> Crear usuario.
            cur.execute(
                "CREATE TABLE IF NOT EXISTS account_invites ("
                "  token CHAR(64) PRIMARY KEY,"
                "  correo VARCHAR(150) NOT NULL,"
                "  invitado_por VARCHAR(150) NOT NULL DEFAULT '',"
                "  creado DATETIME DEFAULT CURRENT_TIMESTAMP,"
                "  expira DATETIME NOT NULL,"
                "  usado TINYINT(1) NOT NULL DEFAULT 0,"
                "  INDEX idx_ai_correo (correo)"
                ") CHARACTER SET utf8mb4"
            )
            cur.execute(
                "SELECT COUNT(*) AS n FROM information_schema.COLUMNS"
                " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'account_invites'"
                "   AND COLUMN_NAME = 'invitado_por'"
            )
            if not (cur.fetchone() or {}).get("n"):
                cur.execute("ALTER TABLE account_invites ADD COLUMN invitado_por VARCHAR(150) NOT NULL DEFAULT ''")
            cur.execute("DELETE FROM account_invites WHERE usado = 1 OR expira < DATE_SUB(NOW(), INTERVAL 7 DAY)")
            # Trazabilidad: qué admin/dev invitó, cambió permisos/rol/acceso, etc.
            # a cada cuenta. Se ve en Usuarios -> panel de la cuenta -> Actividad.
            cur.execute(
                "CREATE TABLE IF NOT EXISTS usuarios_auditoria ("
                "  id INT AUTO_INCREMENT PRIMARY KEY,"
                "  fecha_hora DATETIME DEFAULT CURRENT_TIMESTAMP,"
                "  actor VARCHAR(150) NOT NULL,"
                "  accion VARCHAR(30) NOT NULL,"
                "  objetivo VARCHAR(150) NOT NULL,"
                "  detalle VARCHAR(500) NOT NULL DEFAULT '',"
                "  INDEX idx_aud_objetivo (objetivo),"
                "  INDEX idx_aud_fecha (fecha_hora)"
                ") CHARACTER SET utf8mb4"
            )
            # Envíos masivos programados (pestaña Programados en Mensajería):
            # un usuario los crea para su area (requieren aprobación de un
            # superior); admin/supervisor en cualquier area sin confirmación.
            # El scheduler los ejecuta al vencer (solo aprobados).
            cur.execute(
                "CREATE TABLE IF NOT EXISTS envios_programados ("
                "  id INT AUTO_INCREMENT PRIMARY KEY,"
                "  area_id INT NULL,"
                "  tabla VARCHAR(64) NOT NULL,"
                "  ambiente VARCHAR(12) NOT NULL DEFAULT 'produccion',"
                "  plantilla_id INT NOT NULL,"
                "  plantilla_nombre VARCHAR(150) NOT NULL DEFAULT '',"
                "  limite INT NULL,"
                "  programado_para DATETIME NOT NULL,"
                "  estado VARCHAR(12) NOT NULL DEFAULT 'pendiente',"
                "  creador VARCHAR(150) NOT NULL DEFAULT '',"
                "  creador_nombre VARCHAR(150) NOT NULL DEFAULT '',"
                "  creado DATETIME DEFAULT CURRENT_TIMESTAMP,"
                "  decidido_por VARCHAR(150) NOT NULL DEFAULT '',"
                "  motivo VARCHAR(255) NOT NULL DEFAULT '',"
                "  token CHAR(32) NULL,"
                "  job_id VARCHAR(16) NULL,"
                "  INDEX idx_prog_estado_fecha (estado, programado_para),"
                "  INDEX idx_prog_creador (creador),"
                "  INDEX idx_prog_token (token)"
                ") CHARACTER SET utf8mb4"
            )
            # Pacientes preelegidos por cada programado: la lista se congela al
            # crearlo, para que el envío salga a ese grupo y no a quien aparezca
            # pendiente después. Un paciente no puede quedar en dos programados
            # activos de la misma base (ver _preelegir_pacientes_prog).
            cur.execute("""
                CREATE TABLE IF NOT EXISTS programado_destinatarios (
                  prog_id INT NOT NULL,
                  paciente_id INT NOT NULL,
                  nombre VARCHAR(150) NOT NULL DEFAULT '',
                  telefono VARCHAR(32) NOT NULL DEFAULT '',
                  creado DATETIME DEFAULT CURRENT_TIMESTAMP,
                  INDEX idx_pd_prog (prog_id),
                  INDEX idx_pd_paciente (paciente_id)
                ) CHARACTER SET utf8mb4
            """)
            cur.execute(
                "CREATE TABLE IF NOT EXISTS whatsapp_avisos_unicos ("
                "  telefono VARCHAR(32) NOT NULL,"
                "  tipo VARCHAR(32) NOT NULL,"
                "  enviado DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,"
                "  PRIMARY KEY (telefono, tipo)"
                ") CHARACTER SET utf8mb4"
            )
            cur.execute(
                "CREATE TABLE IF NOT EXISTS whatsapp_interacciones_lista ("
                "  mensaje_hash CHAR(64) NOT NULL PRIMARY KEY,"
                "  creado DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,"
                "  KEY idx_interacciones_lista_creado (creado)"
                ") ENGINE=InnoDB CHARACTER SET utf8mb4"
            )
            # Los avisos ya enviados antes de esta migración también cuentan:
            # no deben repetirse solo por agregar el control de una vez.
            try:
                cur.execute(
                    "INSERT IGNORE INTO whatsapp_avisos_unicos (telefono, tipo, enviado) "
                    "SELECT REPLACE(REPLACE(numero_telefono, '+', ''), ' ', ''), "
                    "CASE plantilla_clave WHEN 'baja_aviso' THEN 'baja' "
                    "WHEN 'retractacion_aviso' THEN 'reintegro' END, MIN(fecha_hora) "
                    "FROM log_envios "
                    "WHERE estado_envio = 'enviado' "
                    "AND plantilla_clave IN ('baja_aviso', 'retractacion_aviso') "
                    "AND COALESCE(numero_telefono, '') <> '' "
                    "GROUP BY REPLACE(REPLACE(numero_telefono, '+', ''), ' ', ''), plantilla_clave"
                )
            except Exception as e:
                log_error("migrar_whatsapp_avisos_unicos", e)
            _sembrar_usuarios(cur)
            # Conserva la procedencia de cada carga CSV para auditoría; la
            # visibilidad de datos se controla por permiso y área, no por autor.
            cur.execute(
                "CREATE TABLE IF NOT EXISTS paciente_csv_accesos ("
                "  tabla_pacientes VARCHAR(64) NOT NULL,"
                "  paciente_id INT NOT NULL,"
                "  usuario_id INT NOT NULL,"
                "  PRIMARY KEY (tabla_pacientes, paciente_id, usuario_id),"
                "  INDEX idx_csv_acceso_usuario (usuario_id)"
                ") CHARACTER SET utf8mb4"
            )
            # Si el login ya es un correo y no hay correo de recuperación, se copia.
            cur.execute(
                "UPDATE usuarios SET correo_recuperacion = LOWER(usuario)"
                " WHERE correo_recuperacion = '' AND usuario LIKE '%@_%._%'"
            )
            # Corrección manual de la respuesta del paciente: gana sobre la
            # señal automática 'pegajosa'. NULL = sin corrección.
            # `interesado`: el paciente mostró interés real ("me interesa", "quiero
            # agendar"…). Se marca solo por webhook o a mano; sobreescribe una baja.
            for tp in ("pacientes_dev", "pacientes_prod"):
                cur.execute(
                    "SELECT"
                    "  (SELECT COUNT(*) FROM information_schema.TABLES"
                    "     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s) AS tabla,"
                    "  (SELECT COUNT(*) FROM information_schema.COLUMNS"
                    "     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s"
                    "       AND COLUMN_NAME = 'respuesta_manual') AS col,"
                    "  (SELECT COUNT(*) FROM information_schema.COLUMNS"
                    "     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s"
                    "       AND COLUMN_NAME = 'interesado') AS col_int,"
                    "  (SELECT COUNT(*) FROM information_schema.COLUMNS"
                    "     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s"
                    "       AND COLUMN_NAME = 'opt_out_explicito') AS col_exp,"
                    "  (SELECT COUNT(*) FROM information_schema.COLUMNS"
                    "     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s"
                    "       AND COLUMN_NAME = 'no_interesado') AS col_noint,"
                    "  (SELECT COUNT(*) FROM information_schema.COLUMNS"
                    "     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s"
                    "       AND COLUMN_NAME = 'interes_plantilla_clave') AS col_iplant,"
                    "  (SELECT COUNT(*) FROM information_schema.COLUMNS"
                    "     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s"
                    "       AND COLUMN_NAME = 'interes_fecha') AS col_ifecha,"
                    "  (SELECT COUNT(*) FROM information_schema.COLUMNS"
                    "     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s"
                    "       AND COLUMN_NAME = 'ultimo_reintegro') AS col_ureint",
                    (tp, tp, tp, tp, tp, tp, tp, tp),
                )
                fila = cur.fetchone() or {}
                if not fila.get("tabla"):
                    continue
                if not fila.get("col"):
                    cur.execute(f"ALTER TABLE {tp} ADD COLUMN respuesta_manual VARCHAR(12) NULL")
                    # Traspasa las correcciones manuales antiguas (que antes se
                    # guardaban como filas log 'ajuste_manual') a la nueva columna.
                    cur.execute(
                        f"UPDATE {tp} p SET p.respuesta_manual = ("
                        "  SELECT le.respuesta FROM log_envios le"
                        "  WHERE le.paciente_id = p.id AND le.plantilla_clave = 'ajuste_manual'"
                        "  ORDER BY le.id DESC LIMIT 1)"
                        " WHERE EXISTS (SELECT 1 FROM log_envios le2"
                        "  WHERE le2.paciente_id = p.id AND le2.plantilla_clave = 'ajuste_manual')"
                    )
                if not fila.get("col_int"):
                    cur.execute(f"ALTER TABLE {tp} ADD COLUMN interesado TINYINT(1) NOT NULL DEFAULT 0")
                if not fila.get("col_exp"):
                    # 1 = el paciente pidió la baja con sus propias palabras por
                    # WhatsApp (detectado por el webhook); a diferencia de una
                    # baja puesta a mano, esta no se puede editar en el panel de
                    # Pacientes salvo que el propio paciente se retracte (vuelva
                    # a escribir mostrando interés, lo que la borra solo).
                    cur.execute(f"ALTER TABLE {tp} ADD COLUMN opt_out_explicito TINYINT(1) NOT NULL DEFAULT 0")
                if not fila.get("col_noint"):
                    # "No me interesa" (botón de la plantilla normal): a diferencia
                    # de una baja, el paciente sigue pudiendo recibir plantillas.
                    cur.execute(f"ALTER TABLE {tp} ADD COLUMN no_interesado TINYINT(1) NOT NULL DEFAULT 0")
                if not fila.get("col_iplant"):
                    cur.execute(f"ALTER TABLE {tp} ADD COLUMN interes_plantilla_clave VARCHAR(50) NULL")
                if not fila.get("col_ifecha"):
                    cur.execute(f"ALTER TABLE {tp} ADD COLUMN interes_fecha DATETIME NULL")
                if not fila.get("col_ureint"):
                    # Fecha de la última vez que el paciente volvió desde estar de
                    # baja (retractación o interés). En producción se usa para
                    # impedir la alternancia baja -> reintegración repetida
                    # (máximo una vez cada 24 h); en desarrollo no aplica.
                    cur.execute(f"ALTER TABLE {tp} ADD COLUMN ultimo_reintegro DATETIME NULL")
                cur.execute(f"UPDATE {tp} SET respuesta_manual = 'respondio' WHERE respuesta_manual = 'click'")

            # El tipo de respuesta 'click' se eliminó: quita el valor del ENUM
            # de log_envios.respuesta (una sola vez) y migra las filas antiguas.
            cur.execute(
                "SELECT COLUMN_TYPE AS t FROM information_schema.COLUMNS"
                " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'log_envios'"
                "   AND COLUMN_NAME = 'respuesta'"
            )
            ct = (cur.fetchone() or {}).get("t", "")
            if "'click'" in ct:
                cur.execute("UPDATE log_envios SET respuesta = 'respondio' WHERE respuesta = 'click'")
                cur.execute(
                    "ALTER TABLE log_envios MODIFY COLUMN respuesta"
                    " ENUM('pendiente','respondio','baja') DEFAULT 'pendiente'"
                )

            # Facturación de servicio: la franquicia mensual de Meta es por
            # número emisor y se consume cuando el mensaje fue entregado.
            cur.execute(
                "SELECT COLUMN_NAME FROM information_schema.COLUMNS"
                " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'log_envios'"
                " AND COLUMN_NAME IN ('wa_phone_id', 'entregado_en')"
            )
            cols_facturacion = {f["COLUMN_NAME"] for f in cur.fetchall()}
            if "wa_phone_id" not in cols_facturacion:
                cur.execute("ALTER TABLE log_envios ADD COLUMN wa_phone_id VARCHAR(64) NULL")
            if "entregado_en" not in cols_facturacion:
                cur.execute("ALTER TABLE log_envios ADD COLUMN entregado_en DATETIME NULL")

            # Emisor histórico: NULL en filas anteriores (nunca se infiere a
            # partir de la configuración actual, que puede cambiar).
            for tabla in ("envios", "envios_programados", "log_envios"):
                cur.execute("SELECT COUNT(*) AS n FROM information_schema.TABLES"
                            " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s", (tabla,))
                if not (cur.fetchone() or {}).get("n"):
                    continue
                cur.execute(
                    "SELECT COLUMN_NAME FROM information_schema.COLUMNS"
                    " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s"
                    " AND COLUMN_NAME IN ('centro_codigo','wa_phone_id','numero_emisor')",
                    (tabla,),
                )
                presentes = {fila["COLUMN_NAME"] for fila in cur.fetchall()}
                for columna, tipo in (("centro_codigo", "VARCHAR(8)"),
                                      ("wa_phone_id", "VARCHAR(64)"),
                                      ("numero_emisor", "VARCHAR(32)")):
                    if columna not in presentes:
                        cur.execute(f"ALTER TABLE {tabla} ADD COLUMN {columna} {tipo} NULL")

            # Un envío rechazado por el supervisor queda en el historial con su
            # comentario, en vez de borrarse.
            cur.execute(
                "SELECT"
                "  (SELECT COUNT(*) FROM information_schema.TABLES"
                "     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'envios') AS tabla,"
                "  (SELECT COLUMN_TYPE FROM information_schema.COLUMNS"
                "     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'envios' AND COLUMN_NAME = 'estado') AS estado_tipo,"
                "  (SELECT COUNT(*) FROM information_schema.COLUMNS"
                "     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'envios' AND COLUMN_NAME = 'comentario') AS tiene_com,"
                "  (SELECT COUNT(*) FROM information_schema.COLUMNS"
                "     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'envios' AND COLUMN_NAME = 'usuario') AS tiene_usr,"
                "  (SELECT COUNT(*) FROM information_schema.COLUMNS"
                "     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'envios' AND COLUMN_NAME = 'programado_id') AS tiene_prog"
            )
            e = cur.fetchone() or {}
            if e.get("tabla"):
                if "'rechazado'" not in (e.get("estado_tipo") or ""):
                    cur.execute(
                        "ALTER TABLE envios MODIFY COLUMN estado"
                        " ENUM('completado','cancelado','rechazado') NOT NULL DEFAULT 'completado'"
                    )
                if not e.get("tiene_com"):
                    cur.execute("ALTER TABLE envios ADD COLUMN comentario VARCHAR(255) NULL")
                # Cuenta que inició el envío. NULL en envíos previos a esta
                # columna; se usa en Usuarios -> «Envíos realizados».
                if not e.get("tiene_usr"):
                    cur.execute("ALTER TABLE envios ADD COLUMN usuario VARCHAR(150) NULL")
                    cur.execute("ALTER TABLE envios ADD INDEX idx_envios_usuario (usuario)")
                # Vínculo persistente: permite distinguir lotes programados de
                # manuales sin duplicarlos en la auditoría de la cuenta.
                if not e.get("tiene_prog"):
                    cur.execute("ALTER TABLE envios ADD COLUMN programado_id INT NULL")
                    cur.execute("ALTER TABLE envios ADD INDEX idx_envios_programado (programado_id)")

            # (uno o varios, separados por coma). Se traspasa una vez.
            cur.execute("SELECT clave, valor FROM configuracion WHERE clave IN ('call_center_numero', 'call_center_numeros')")
            cc = {r["clave"]: (r["valor"] or "") for r in cur.fetchall()}
            if cc.get("call_center_numero") and not cc.get("call_center_numeros"):
                cur.execute(
                    "INSERT INTO configuracion (clave, valor) VALUES ('call_center_numeros', %s)"
                    " ON DUPLICATE KEY UPDATE valor = VALUES(valor)",
                    (cc["call_center_numero"],),
                )
            cur.execute("DELETE FROM configuracion WHERE clave = 'call_center_numero'")

            # Modelo multi-área: tablas `areas`,
            # `roles_area` y `usuario_area_roles`. Import
            # diferido porque servicio_especialidades importa este módulo.
            from servicio_areas import asegurar_tablas_areas, migrar_tablas_areas
            migrar_tablas_areas(cur)
            asegurar_tablas_areas(cur)

            # Rol global `supervisor`: acceso acotado a sus especialidades,
            # sin Estadísticas (igual que `usuario`).
            cur.execute(
                "SELECT COLUMN_TYPE AS t FROM information_schema.COLUMNS"
                " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'usuarios'"
                "   AND COLUMN_NAME = 'rol'"
            )
            if "'supervisor'" not in ((cur.fetchone() or {}).get("t") or ""):
                cur.execute(
                    "ALTER TABLE usuarios MODIFY COLUMN rol"
                    " ENUM('usuario','supervisor','administrador','desarrollador')"
                    " NOT NULL DEFAULT 'usuario'"
                )

            # Trazabilidad por área en el historial (NULL = fila legacy de las
            # tablas pacientes_dev/prod, anteriores al modelo).
            for _ht, _hidx in (("envios", "idx_envios_area"),
                               ("log_envios", "idx_log_area")):
                cur.execute(
                    "SELECT COLUMN_NAME FROM information_schema.COLUMNS"
                    " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s"
                    "   AND COLUMN_NAME IN ('especialidad_id', 'area_id', 'tabla_pacientes')",
                    (_ht,),
                )
                _tiene = {r["COLUMN_NAME"] for r in cur.fetchall()}
                if "especialidad_id" in _tiene and "area_id" not in _tiene:
                    cur.execute(f"ALTER TABLE {_ht} CHANGE COLUMN especialidad_id area_id INT NULL")
                    _tiene.discard("especialidad_id")
                    _tiene.add("area_id")
                if "area_id" not in _tiene:
                    cur.execute(f"ALTER TABLE {_ht} ADD COLUMN area_id INT NULL")
                if "tabla_pacientes" not in _tiene:
                    cur.execute(f"ALTER TABLE {_ht} ADD COLUMN tabla_pacientes VARCHAR(64) NULL")
                # Índices con el nombre viejo se renuevan (drop + add).
                for _viejo_idx in ("idx_envios_especialidad", "idx_log_especialidad"):
                    cur.execute(
                        "SELECT COUNT(*) AS n FROM information_schema.STATISTICS"
                        " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND INDEX_NAME = %s",
                        (_ht, _viejo_idx),
                    )
                    if (cur.fetchone() or {}).get("n"):
                        cur.execute(f"ALTER TABLE {_ht} DROP INDEX {_viejo_idx}")
                cur.execute(
                    "SELECT COUNT(*) AS n FROM information_schema.STATISTICS"
                    " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND INDEX_NAME = %s",
                    (_ht, _hidx),
                )
                if not (cur.fetchone() or {}).get("n"):
                    cur.execute(f"ALTER TABLE {_ht} ADD INDEX {_hidx} (area_id)")

            # Migración única: separar creación y aprobación por tipo de envío
            # sin quitar capacidades a las cuentas que ya las tenían. El
            # marcador evita volver a otorgar permisos que después se revoquen.
            cur.execute("SELECT valor FROM configuracion WHERE clave = %s FOR UPDATE",
                        ("permisos_envios_v2",))
            if not cur.fetchone():
                cur.execute("SELECT usuario, rol, permisos FROM usuarios"
                            " WHERE rol NOT IN ('administrador', 'desarrollador')")
                for cuenta in cur.fetchall():
                    anteriores = cuenta.get("permisos") or ""
                    nuevos = _migrar_permisos_envios_legacy(anteriores, cuenta.get("rol"))
                    if nuevos != anteriores:
                        cur.execute("UPDATE usuarios SET permisos = %s WHERE usuario = %s",
                                    (nuevos, cuenta["usuario"]))
                cur.execute("INSERT INTO configuracion (clave, valor) VALUES (%s, '1')",
                            ("permisos_envios_v2",))

            # Mantiene el acceso histórico una sola vez. Después se puede
            # revocar cada acción sin que un reinicio vuelva a concederla.
            cur.execute("SELECT valor FROM configuracion WHERE clave = %s FOR UPDATE",
                        ("permisos_acciones_v1",))
            if not cur.fetchone():
                cur.execute("SELECT usuario, rol, permisos FROM usuarios"
                            " WHERE rol NOT IN ('administrador', 'desarrollador')")
                for cuenta in cur.fetchall():
                    anteriores = cuenta.get("permisos") or ""
                    nuevos = _migrar_permisos_acciones_legacy(anteriores, cuenta.get("rol"))
                    if nuevos != anteriores:
                        cur.execute("UPDATE usuarios SET permisos = %s WHERE usuario = %s",
                                    (nuevos, cuenta["usuario"]))
                cur.execute("INSERT INTO configuracion (clave, valor) VALUES (%s, '1')",
                            ("permisos_acciones_v1",))

            # Estadísticas (y costos) son exclusivas de admin/dev: se retiran
            # esos permisos de las cuentas no privilegiadas que los tuvieran.
            cur.execute(
                "SELECT usuario, permisos FROM usuarios"
                " WHERE rol NOT IN ('administrador', 'desarrollador')"
            )
            for _u in cur.fetchall():
                _perms = [p for p in ((_u.get("permisos") or "").split(",")) if p]
                _recorte = [p for p in _perms if p not in ("estadisticas", "tarifas_editar")]
                if _recorte != _perms:
                    cur.execute("UPDATE usuarios SET permisos = %s WHERE usuario = %s",
                                (",".join(_recorte), _u["usuario"]))

            conn.commit()
    except Exception as e:
        log_error("asegurar_tabla_config", e)
    _config_cache = None
    _columnas_cache.clear()


# ---------------------------------------------------------------------------
# Cuentas de usuario
# ---------------------------------------------------------------------------
USUARIOS_JSON = BASE_DIR / "data" / "usuarios.json"

ROLES_USUARIO = ("usuario", "supervisor", "administrador", "desarrollador")
MAX_DESARROLLADORES = 4

# La página de Configuración es exclusiva del rol `desarrollador`:
#   - call_center:           gestionar las plantillas de call center y enviarlas a mano.
#   - call_center_registro:  ver el registro de respuestas de call center (número
#                            asignado, paciente) en la página de Historial.
PERMISOS_VALIDOS = (
    "pacientes", "mensajeria", "historial", "estadisticas",
    "plantillas_editar", "plantillas_eliminar", "csv_subir", "pacientes_ver_datos",
    "pacientes_vaciar",
    "envio_manual", "envio_manual_directo",
    "envio_programado", "envio_programado_directo", "tarifas_editar",
    "envios_decidir", "envios_cancelar",
    "call_center", "call_center_registro", "call_center_reintentar",
)
PERMISOS_BASICOS = ["mensajeria", "historial", "plantillas_editar"]

# Cuentas creadas automáticamente la primera vez (o si faltan). El hash es
# SHA-256 de la contraseña indicada.
_USUARIOS_SEMILLA = [
    {"usuario": "admin", "nombre": "Administrador", "rol": "administrador",
     "permisos": "", "clave_hash": "240be518fabd2724ddb6f04eeb1da5967448d7e831c08c8fa822809f74c720a9"},
    {"usuario": "usuario", "nombre": "Usuario Final", "rol": "usuario",
     "permisos": ",".join(PERMISOS_BASICOS),
     "clave_hash": "dfa7a2273567dcd1efffb9a46308e91c20fa13c44c3441bc69cd6a7869b3f7fd"},
]
# Cuenta de desarrollador que el sistema garantiza en cada arranque (dev / dev123).
_USUARIO_DEV = {
    "usuario": "dev", "nombre": "Desarrollador", "rol": "desarrollador",
    "permisos": "",
    "clave_hash": "87274af01876341455b32d805946f272871bb42effa6604dccf28bb027afa82b",
}


def _permisos_csv(permisos) -> str:
    if isinstance(permisos, str):
        items = [p.strip() for p in permisos.split(",")]
    else:
        items = list(permisos or [])
    vistos, out = set(), []
    for p in items:
        if p in PERMISOS_VALIDOS and p not in vistos:
            vistos.add(p)
            out.append(p)
    return ",".join(out)


def _sembrar_usuarios(cur) -> None:
    """Puebla la tabla `usuarios` la primera vez y garantiza la cuenta `dev`.

    Se llama dentro de asegurar_tabla_config(), con la transacción abierta."""
    cur.execute("SELECT COUNT(*) AS n FROM usuarios")
    vacia = (cur.fetchone() or {}).get("n", 0) == 0

    if vacia:
        # Migración: si existe data/usuarios.json se importa; si no, semillas.
        importados = []
        try:
            if USUARIOS_JSON.exists():
                datos = json.loads(USUARIOS_JSON.read_text(encoding="utf-8"))
                if isinstance(datos, list):
                    importados = datos
        except Exception as e:
            log_error("_sembrar_usuarios: usuarios.json ilegible", e)
        filas = importados or _USUARIOS_SEMILLA
        for u in filas:
            correo = str(u.get("usuario", "")).strip().lower()
            ch = str(u.get("clave_hash", "")).strip()
            if not correo or not ch:
                continue
            rol = u.get("rol") if u.get("rol") in ROLES_USUARIO else "usuario"
            cur.execute(
                "INSERT IGNORE INTO usuarios (usuario, nombre, rol, permisos, clave_hash)"
                " VALUES (%s, %s, %s, %s, %s)",
                (correo, (u.get("nombre") or correo.split("@")[0])[:150], rol,
                 _permisos_csv(u.get("permisos")), ch),
            )
        if importados and USUARIOS_JSON.exists():
            try:
                USUARIOS_JSON.rename(USUARIOS_JSON.with_suffix(".json.migrado"))
            except Exception as e:
                log_error("_sembrar_usuarios: no se pudo archivar usuarios.json", e)

    # Garantiza la cuenta `dev` en cada arranque (respetando el tope de 4).
    cur.execute("SELECT COUNT(*) AS n FROM usuarios WHERE usuario = 'dev'")
    if (cur.fetchone() or {}).get("n", 0) == 0:
        cur.execute("SELECT COUNT(*) AS n FROM usuarios WHERE rol = 'desarrollador'")
        if (cur.fetchone() or {}).get("n", 0) < MAX_DESARROLLADORES:
            cur.execute(
                "INSERT IGNORE INTO usuarios (usuario, nombre, rol, permisos, clave_hash)"
                " VALUES (%s, %s, %s, %s, %s)",
                (_USUARIO_DEV["usuario"], _USUARIO_DEV["nombre"], _USUARIO_DEV["rol"],
                 _USUARIO_DEV["permisos"], _USUARIO_DEV["clave_hash"]),
            )
        else:
            log_error("_sembrar_usuarios: ya hay 4 desarrolladores; no se creó la cuenta 'dev'")


def _fila_usuario(row: dict) -> dict:
    return {
        "usuario": row["usuario"],
        "nombre": row.get("nombre") or "",
        "rol": row.get("rol") or "usuario",
        "permisos": [p for p in (row.get("permisos") or "").split(",") if p],
        "clave_hash": row.get("clave_hash") or "",
        "correo_recuperacion": (row.get("correo_recuperacion") or "").strip().lower(),
        "activo": bool(row.get("activo", 1)),
    }


_USUARIO_COLS = "usuario, nombre, rol, permisos, clave_hash, correo_recuperacion, activo"


def usuarios_listar() -> list[dict]:
    try:
        with conectar() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT {_USUARIO_COLS} FROM usuarios"
                " ORDER BY FIELD(rol,'desarrollador','administrador','usuario'), usuario"
            )
            return [_fila_usuario(r) for r in cur.fetchall()]
    except Exception as e:
        log_error("usuarios_listar", e)
        return []


def usuario_buscar(correo: str) -> dict | None:
    correo = (correo or "").strip().lower()
    if not correo:
        return None
    try:
        with conectar() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT {_USUARIO_COLS} FROM usuarios WHERE usuario = %s", (correo,))
            row = cur.fetchone()
            return _fila_usuario(row) if row else None
    except Exception as e:
        log_error("usuario_buscar", e)
        return None


def usuario_por_recuperacion(correo: str) -> dict | None:
    """Cuenta cuyo login ES ese correo, o que lo tiene como correo de
    recuperación. La coincidencia exacta de login tiene prioridad."""
    correo = (correo or "").strip().lower()
    if not correo:
        return None
    try:
        with conectar() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT {_USUARIO_COLS} FROM usuarios"
                " WHERE usuario = %s OR correo_recuperacion = %s"
                " ORDER BY (usuario = %s) DESC LIMIT 1",
                (correo, correo, correo),
            )
            row = cur.fetchone()
            return _fila_usuario(row) if row else None
    except Exception as e:
        log_error("usuario_por_recuperacion", e)
        return None


def usuario_set_correo_recuperacion(correo: str, correo_rec: str) -> None:
    with conectar() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE usuarios SET correo_recuperacion = %s WHERE usuario = %s",
            ((correo_rec or "").strip().lower(), (correo or "").strip().lower()),
        )
        conn.commit()


def correo_en_uso(correo: str, excluir_usuario: str | None = None) -> bool:
    """True si `correo` ya es el usuario (login) o el correo de recuperación de
    OTRA cuenta (distinta de `excluir_usuario`)."""
    correo = (correo or "").strip().lower()
    excluir = (excluir_usuario or "").strip().lower()
    if not correo:
        return False
    try:
        with conectar() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS n FROM usuarios"
                " WHERE (usuario = %s OR correo_recuperacion = %s) AND usuario <> %s",
                (correo, correo, excluir),
            )
            return bool((cur.fetchone() or {}).get("n"))
    except Exception as e:
        log_error("correo_en_uso", e)
        return True  # ante la duda, bloquea (no queremos duplicar un correo)


# --- Auditoría de cuentas (trazabilidad de acciones de admin/dev) ----------

def auditoria_registrar(actor: str, accion: str, objetivo: str, detalle: str = "") -> None:
    try:
        with conectar() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO usuarios_auditoria (actor, accion, objetivo, detalle)"
                " VALUES (%s, %s, %s, %s)",
                ((actor or "").strip().lower(), accion, (objetivo or "").strip().lower(), (detalle or "")[:500]),
            )
            conn.commit()
    except Exception as e:
        log_error("auditoria_registrar", e)


def auditoria_listar(valor: str | None = None, limite: int = 200, campo: str = "actor") -> list[dict]:
    """`campo`: sobre qué columna filtrar `valor` — "actor" (qué hizo esta
    cuenta, el comportamiento por defecto) u "objetivo" (qué le hicieron a
    esta cuenta). `campo` no viene del cliente, así que es seguro interpolarlo
    en el SQL (solo hay estos dos valores posibles)."""
    columna = "actor" if campo == "actor" else "objetivo"
    try:
        with conectar() as conn, conn.cursor() as cur:
            if valor:
                cur.execute(
                    f"SELECT fecha_hora, actor, accion, objetivo, detalle FROM usuarios_auditoria"
                    f" WHERE {columna} = %s ORDER BY id DESC LIMIT %s",
                    ((valor or "").strip().lower(), limite),
                )
            else:
                cur.execute(
                    "SELECT fecha_hora, actor, accion, objetivo, detalle FROM usuarios_auditoria"
                    " ORDER BY id DESC LIMIT %s",
                    (limite,),
                )
            return cur.fetchall()
    except Exception as e:
        log_error("auditoria_listar", e)
        return []


def contar_desarrolladores(excluir: str | None = None) -> int:
    try:
        with conectar() as conn, conn.cursor() as cur:
            if excluir:
                cur.execute(
                    "SELECT COUNT(*) AS n FROM usuarios WHERE rol = 'desarrollador' AND usuario <> %s",
                    ((excluir or "").strip().lower(),),
                )
            else:
                cur.execute("SELECT COUNT(*) AS n FROM usuarios WHERE rol = 'desarrollador'")
            return int((cur.fetchone() or {}).get("n", 0))
    except Exception as e:
        log_error("contar_desarrolladores", e)
        return MAX_DESARROLLADORES  # ante la duda, bloquea nuevas promociones


def usuario_crear(correo: str, nombre: str, rol: str, permisos, clave_hash: str) -> None:
    with conectar() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO usuarios (usuario, nombre, rol, permisos, clave_hash)"
            " VALUES (%s, %s, %s, %s, %s)",
            ((correo or "").strip().lower(), (nombre or "")[:150],
             rol if rol in ROLES_USUARIO else "usuario", _permisos_csv(permisos), clave_hash),
        )
        conn.commit()


def usuario_actualizar(correo: str, *, nombre: str | None = None,
                       rol: str | None = None, permisos=None,
                       activo: bool | None = None) -> None:
    sets, params = [], []
    if nombre is not None:
        sets.append("nombre = %s")
        params.append(nombre[:150])
    if rol is not None and rol in ROLES_USUARIO:
        sets.append("rol = %s")
        params.append(rol)
    if permisos is not None:
        sets.append("permisos = %s")
        params.append(_permisos_csv(permisos))
    if activo is not None:
        sets.append("activo = %s")
        params.append(1 if activo else 0)
    if not sets:
        return
    params.append((correo or "").strip().lower())
    with conectar() as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE usuarios SET {', '.join(sets)} WHERE usuario = %s", params)
        conn.commit()


def usuario_cambiar_clave(correo: str, clave_hash: str) -> None:
    with conectar() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE usuarios SET clave_hash = %s WHERE usuario = %s",
            (clave_hash, (correo or "").strip().lower()),
        )
        conn.commit()


def usuario_borrar(correo: str) -> None:
    correo = (correo or "").strip().lower()
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM usuarios WHERE usuario = %s", (correo,))
        fila = cur.fetchone()
        cur.execute("DELETE FROM usuarios WHERE usuario = %s", (correo,))
        cur.execute("DELETE FROM password_resets WHERE usuario = %s", (correo,))
        if fila:
            # Limpia los roles de área de la cuenta eliminada.
            try:
                cur.execute("DELETE FROM usuario_area_roles WHERE usuario_id = %s",
                            (int(fila["id"]),))
            except Exception as e:
                log_error("usuario_borrar: roles de especialidad", e)
        conn.commit()


# --- Restablecimiento de contraseña ("Olvidé mi contraseña") ---------------

def reset_crear(token: str, correo: str, horas: int = 2) -> None:
    correo = (correo or "").strip().lower()
    with conectar() as conn, conn.cursor() as cur:
        # Un solo enlace activo por cuenta: anula los anteriores.
        cur.execute("UPDATE password_resets SET usado = 1 WHERE usuario = %s AND usado = 0", (correo,))
        cur.execute(
            "INSERT INTO password_resets (token, usuario, expira)"
            " VALUES (%s, %s, DATE_ADD(NOW(), INTERVAL %s HOUR))",
            (token, correo, int(horas)),
        )
        conn.commit()


def reset_estado(token: str) -> dict:
    """{'estado': 'ok'|'no_existe'|'usado'|'expirado', 'usuario': <correo>}."""
    try:
        with conectar() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT usuario, usado, (expira <= NOW()) AS venc"
                " FROM password_resets WHERE token = %s",
                (token,),
            )
            row = cur.fetchone()
    except Exception as e:
        log_error("reset_estado", e)
        return {"estado": "no_existe"}
    if not row:
        return {"estado": "no_existe"}
    if row.get("usado"):
        return {"estado": "usado", "usuario": row["usuario"]}
    if row.get("venc"):
        return {"estado": "expirado", "usuario": row["usuario"]}
    return {"estado": "ok", "usuario": row["usuario"]}


def reset_consumir(token: str) -> None:
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("UPDATE password_resets SET usado = 1 WHERE token = %s", (token,))
        conn.commit()


# --- Invitaciones para crear cuenta -----------------------------------------

def invite_crear(token: str, correo: str, invitado_por: str = "", horas: int = 48) -> None:
    correo = (correo or "").strip().lower()
    with conectar() as conn, conn.cursor() as cur:
        # Un solo enlace activo por correo: anula los anteriores.
        cur.execute("UPDATE account_invites SET usado = 1 WHERE correo = %s AND usado = 0", (correo,))
        cur.execute(
            "INSERT INTO account_invites (token, correo, invitado_por, expira)"
            " VALUES (%s, %s, %s, DATE_ADD(NOW(), INTERVAL %s HOUR))",
            (token, correo, (invitado_por or "").strip().lower(), int(horas)),
        )
        conn.commit()


def invite_estado(token: str) -> dict:
    """{'estado': 'ok'|'no_existe'|'usado'|'expirado', 'correo': <correo>, 'invitado_por': <correo>}."""
    try:
        with conectar() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT correo, invitado_por, usado, (expira <= NOW()) AS venc"
                " FROM account_invites WHERE token = %s",
                (token,),
            )
            row = cur.fetchone()
    except Exception as e:
        log_error("invite_estado", e)
        return {"estado": "no_existe"}
    if not row:
        return {"estado": "no_existe"}
    if row.get("usado"):
        return {"estado": "usado", "correo": row["correo"], "invitado_por": row.get("invitado_por") or ""}
    if row.get("venc"):
        return {"estado": "expirado", "correo": row["correo"], "invitado_por": row.get("invitado_por") or ""}
    return {"estado": "ok", "correo": row["correo"], "invitado_por": row.get("invitado_por") or ""}


def invite_consumir(token: str) -> None:
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("UPDATE account_invites SET usado = 1 WHERE token = %s", (token,))
        conn.commit()


def _cargar_config() -> tuple[dict, bool]:
    """Devuelve (valores, ok). ok=False si no se pudo leer la tabla (BD caída),
    para no cachear valores por defecto de forma permanente."""
    valores = dict(CONFIG_DEFAULTS)
    try:
        with conectar() as conn, conn.cursor() as cur:
            cur.execute("SELECT clave, valor FROM configuracion")
            migrados = False
            for row in cur.fetchall():
                clave = row["clave"]
                valor = "" if row["valor"] is None else row["valor"]
                if clave in ("smtp_pass", "wa_token", "wa_verify_token", "wa_app_secret", "gemini_api_key") \
                        and valor.startswith(("dpapi:", "fernet-v1:")):
                    try:
                        descifrado = leer_credencial_antigua(valor)
                    except Exception:
                        # Nunca enviar el texto cifrado como credencial literal.
                        log_error(f"_cargar_config: vuelve a ingresar {clave} desde Configuración")
                        valor = ""
                    else:
                        cur.execute("UPDATE configuracion SET valor = %s WHERE clave = %s AND valor = %s",
                                    (descifrado, clave, valor))
                        valor = descifrado
                        migrados = True
                valores[clave] = valor
            if migrados:
                conn.commit()
        return valores, True
    except Exception as e:
        # 1146 = la tabla no existe todavía (primer arranque): se usan los
        # valores por defecto y asegurar_tabla_config() la creará enseguida.
        if getattr(e, "args", [None])[0] != 1146:
            log_error("_cargar_config (se usan los valores por defecto)", e)
        return valores, False


def config_all(*, refresh: bool = False) -> dict:
    global _config_cache, _config_cache_cargado
    if _config_cache is None or (refresh and time.monotonic() - _config_cache_cargado >= 1):
        valores, ok = _cargar_config()
        # Solo se cachea una lectura real; si la BD falló se reintenta luego.
        if ok:
            _config_cache = valores
            _config_cache_cargado = time.monotonic()
        elif refresh:
            # No devolver datos antiguos si se pidió revalidar un canal retirado.
            raise RuntimeError("No se pudo actualizar la configuración")
        return dict(valores)
    return dict(_config_cache)


def config_get(clave: str, default: str = "") -> str:
    return config_all().get(clave, default)


def config_set(cambios: dict) -> None:
    """Upsert de una o varias claves en la tabla `configuracion`."""
    global _config_cache
    with conectar() as conn, conn.cursor() as cur:
        for clave, valor in cambios.items():
            cur.execute(
                "INSERT INTO configuracion (clave, valor) VALUES (%s, %s)"
                " ON DUPLICATE KEY UPDATE valor = VALUES(valor)",
                (clave, "" if valor is None else str(valor)),
            )
        conn.commit()
    _config_cache = None


def agregar_numero_prueba_ambos(numero: str) -> dict[str, bool]:
    """Autoriza el número en desarrollo y producción en una sola transacción."""
    global _config_cache
    numero = normalizar_telefono(numero)
    if numero is None:
        raise ValueError("Número de prueba inválido")
    agregados = {}
    with conectar() as conn, conn.cursor() as cur:
        for clave, ambiente in (("numeros_prueba_dev", "desarrollo"),
                                ("numeros_prueba_prod", "produccion")):
            cur.execute(
                "INSERT INTO configuracion (clave, valor) VALUES (%s, '')"
                " ON DUPLICATE KEY UPDATE valor = valor", (clave,))
            cur.execute("SELECT valor FROM configuracion WHERE clave = %s FOR UPDATE", (clave,))
            fila = cur.fetchone() or {}
            numeros = [n.strip() for n in (fila.get("valor") or "").split(",") if n.strip()]
            agregado = not any(normalizar_telefono(n) == numero for n in numeros)
            if agregado:
                numeros.append(numero)
                cur.execute("UPDATE configuracion SET valor = %s WHERE clave = %s",
                            (",".join(numeros), clave))
            agregados[ambiente] = agregado
        conn.commit()
    _config_cache = None
    return agregados


def quitar_numero_prueba_ambos(numero: str) -> dict[str, bool]:
    """Retira el número de ambas listas sin eliminar el paciente."""
    global _config_cache
    numero = normalizar_telefono(numero)
    if numero is None:
        raise ValueError("Número de prueba inválido")
    quitados = {}
    with conectar() as conn, conn.cursor() as cur:
        for clave, ambiente in (("numeros_prueba_dev", "desarrollo"),
                                ("numeros_prueba_prod", "produccion")):
            cur.execute(
                "INSERT INTO configuracion (clave, valor) VALUES (%s, '')"
                " ON DUPLICATE KEY UPDATE valor = valor", (clave,))
            cur.execute("SELECT valor FROM configuracion WHERE clave = %s FOR UPDATE", (clave,))
            fila = cur.fetchone() or {}
            numeros = [n.strip() for n in (fila.get("valor") or "").split(",") if n.strip()]
            restantes = [n for n in numeros if normalizar_telefono(n) != numero]
            quitado = len(restantes) != len(numeros)
            if quitado:
                cur.execute("UPDATE configuracion SET valor = %s WHERE clave = %s",
                            (",".join(restantes), clave))
            quitados[ambiente] = quitado
        conn.commit()
    _config_cache = None
    return quitados


def entorno_valido(entorno: str | None = None) -> str:
    ent = (entorno or config_get("entorno", "desarrollo")).strip().lower()
    if ent not in AMBIENTES:
        raise ValueError(f"Entorno desconocido: '{ent}'")
    return ent


def tabla_pacientes(entorno: str | None = None) -> str:
    """Nombre de la tabla de pacientes según el entorno (una única base)."""
    return TABLAS_PACIENTES[entorno_valido(entorno)]


def conectar(entorno: str | None = None):
    return pymysql.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PUERTO", "3306")),
        user=os.getenv("DB_USUARIO", "root"),
        password=os.getenv("DB_CONTRASENA", ""),
        database=os.getenv("DB_NOMBRE", "snw_base"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def nombre_base(entorno: str | None = None) -> str:
    # Identificador de entorno usado como 'base_datos' en envios y config:
    # devuelve la tabla de pacientes ('pacientes_dev' / 'pacientes_prod'),
    # que es lo que permite al frontend distinguir desarrollo de producción.
    return tabla_pacientes(entorno)


_columnas_cache: dict = {}


def columnas_tabla(tabla: str, entorno: str | None = None) -> set:
    """Devuelve el conjunto de columnas reales de una tabla (con caché).

    Permite que el backend se adapte a bases con esquema mínimo, evitando
    fallos al referenciar columnas que no existen.
    """
    clave = f"{entorno_valido(entorno)}:{tabla}"
    if clave not in _columnas_cache:
        try:
            with conectar(entorno) as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT COLUMN_NAME FROM information_schema.COLUMNS"
                    " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
                    (tabla,),
                )
                cols = {f["COLUMN_NAME"] for f in cur.fetchall()}
            # Solo se cachea un resultado real. Si la consulta falla (BD caída)
            # o la tabla aún no existe, se reintenta en la próxima llamada en
            # vez de dejar la caché envenenada con un conjunto vacío.
            if cols:
                _columnas_cache[clave] = cols
            return cols
        except Exception as e:
            log_error(f"columnas_tabla({tabla!r}, {entorno!r})", e)
            return set()
    return _columnas_cache[clave]


def columna_existe(tabla: str, columna: str, entorno: str | None = None) -> bool:
    """Devuelve True si la columna existe en la tabla (cacheado)."""
    return columna in columnas_tabla(tabla, entorno)
