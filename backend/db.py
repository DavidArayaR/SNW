import json
import os
import sys
import traceback
from pathlib import Path

import pymysql
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def log_error(contexto: str, exc: BaseException | None = None) -> None:
    """Registra en consola (stderr) cualquier error, con su traza completa.

    Se usa en todos los bloques try/except del backend para que ningún
    fallo quede silenciado, aunque el flujo continúe."""
    print(f"[ERROR] {contexto}: {exc!r}" if exc is not None else f"[ERROR] {contexto}",
          file=sys.stderr, flush=True)
    if exc is not None and exc.__traceback__ is not None:
        traceback.print_exception(type(exc), exc, exc.__traceback__)
        sys.stderr.flush()

AMBIENTES = {"desarrollo", "produccion"}
TABLAS_PACIENTES = {"desarrollo": "pacientes_dev", "produccion": "pacientes_prod"}


# ---------------------------------------------------------------------------
# TODA la configuración vive en la tabla `configuracion` (clave/valor) y se
# edita desde la página de Configuración. En .env solo quedan las credenciales
# de la BASE DE DATOS (DB_*), porque se necesitan para conectarse a la base
# donde vive la tabla. La primera vez que arranca el backend, la tabla se
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
    # Anti flip-flop: si el paciente se da de baja y luego se reintegra (por
    # retractación o interés), no puede volver a darse de baja hasta que pasen
    # 24 h. Evita que juegue con los botones de baja/reintegrarse. En producción
    # está SIEMPRE activo; `anti_flip_flop_dev` decide si también aplica en la
    # base de desarrollo (desactivarlo permite probar el flujo sin límite).
    "anti_flip_flop_dev": "true",
    "intervalo_ms": "1000",
    "url_base": "",
    # Call center: el número al que lleva el botón de las plantillas de call
    # center se pide en cada respuesta a `call_center_url` (un servicio que
    # devuelve un número —solo dígitos, con código de país— y ya reparte la
    # carga). `call_center_numeros` es un respaldo manual (uno o varios,
    # separados por coma) por si la URL no responde.
    # `call_center_boton_mensaje` / `..._oferta`: texto que se autocompleta en el
    # chat del call center al pulsar el botón. Se usa el de «oferta» cuando la
    # última plantilla enviada al paciente mencionaba un descuento o precio
    # especial; si no, el genérico. El envío automático de call center espera
    # siempre 1s fijo (CALL_CENTER_AUTO_SEGUNDOS en main.py), no es configurable.
    "call_center_url": "https://saludmentalparatodos.cl/telefonosmpt.php",
    "call_center_numeros": "",
    "call_center_boton_mensaje": "Hola, estoy interesado/a en la información que me enviaron.",
    "call_center_boton_mensaje_oferta": "Hola, estoy interesado/a en la oferta que me enviaron.",
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
    "wa_business_account_id": "",
    "wa_verify_token": "",
    "wa_template_nombre": "",
    "wa_template_lang": "es",
    "wa_webhook_path": "/api/whatsapp/webhook",
    "wa_graph_version": "v26.0",
    "wa_moneda": "USD",
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
    # Messaging limit: usuarios ÚNICOS a los que se puede escribir (mensajes
    # iniciados por el negocio) en 24 h. Tiers de Meta: 250 / 2000 / 10000 /
    # 100000 / ilimitado. 0 = sin control. Ajústalo al tier real de la cuenta.
    "wa_messaging_limit_24h": "2000",
}

_config_cache: dict | None = None


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
                "  fecha_hora DATETIME DEFAULT CURRENT_TIMESTAMP,"
                "  INDEX idx_numero (numero_call_center),"
                "  INDEX idx_fecha (fecha_hora)"
                ") CHARACTER SET utf8mb4"
            )
            # Cuentas de la aplicación (antes en data/usuarios.json).
            cur.execute(
                "CREATE TABLE IF NOT EXISTS usuarios ("
                "  id INT AUTO_INCREMENT PRIMARY KEY,"
                "  usuario VARCHAR(150) NOT NULL,"
                "  nombre VARCHAR(150) NOT NULL DEFAULT '',"
                "  rol ENUM('usuario','administrador','desarrollador') NOT NULL DEFAULT 'usuario',"
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
            _sembrar_usuarios(cur)
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
                "     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'envios' AND COLUMN_NAME = 'usuario') AS tiene_usr"
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

            conn.commit()
    except Exception as e:
        log_error("asegurar_tabla_config", e)
    _config_cache = None
    _columnas_cache.clear()


# ---------------------------------------------------------------------------
# Cuentas de usuario
# ---------------------------------------------------------------------------
USUARIOS_JSON = BASE_DIR / "data" / "usuarios.json"

ROLES_USUARIO = ("usuario", "administrador", "desarrollador")
MAX_DESARROLLADORES = 4

# La página de Configuración es exclusiva del rol `desarrollador`:
#   - call_center:           gestionar las plantillas de call center y enviarlas a mano.
#   - call_center_registro:  ver el registro de respuestas de call center (número
#                            asignado, paciente) en la página de Historial.
PERMISOS_VALIDOS = (
    "pacientes", "mensajeria", "historial", "estadisticas",
    "plantillas_editar", "envio_produccion", "tarifas_editar",
    "call_center", "call_center_registro",
)
PERMISOS_BASICOS = ["mensajeria", "historial"]

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
        cur.execute("DELETE FROM usuarios WHERE usuario = %s", (correo,))
        cur.execute("DELETE FROM password_resets WHERE usuario = %s", (correo,))
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
            for row in cur.fetchall():
                valores[row["clave"]] = "" if row["valor"] is None else row["valor"]
        return valores, True
    except Exception as e:
        # 1146 = la tabla no existe todavía (primer arranque): se usan los
        # valores por defecto y asegurar_tabla_config() la creará enseguida.
        if getattr(e, "args", [None])[0] != 1146:
            log_error("_cargar_config (se usan los valores por defecto)", e)
        return valores, False


def config_all() -> dict:
    global _config_cache
    if _config_cache is None:
        valores, ok = _cargar_config()
        # Solo se cachea una lectura real; si la BD falló se reintenta luego.
        if ok:
            _config_cache = valores
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
