-- ============================================================
-- SNW - Base de datos única (snw_base)
-- ============================================================
-- Infraestructura de la base de datos con 10 tablas:
--   - pacientes_dev   : números autorizados para pruebas de desarrollo
--   - pacientes_prod  : números autorizados (sin datos ficticios)
--   - envios          : lotes de envío (una fila por "Iniciar envío")
--   - log_envios      : historial individual de mensajes
--   - whatsapp_eventos: eventos del webhook (idempotencia)
--   - configuracion   : ajustes editables de la app (entorno, método de envío, etc.)
--   - tarifas_whatsapp: rate card de Meta (tarifas por mensaje, para costos)
--   - call_center_log : respuestas enviadas a pacientes interesados + número de call center asignado
--   - usuarios        : cuentas de la app (rol + permisos, clave SHA-256)
--   - password_resets : enlaces temporales de "Olvidé mi contraseña" (2 h)
--
-- Es idempotente (IF NOT EXISTS / INSERT IGNORE): crea la estructura y
-- siembra los 2 números autorizados y las cuentas por defecto
-- (admin/admin123, usuario/usuario123, dev/dev123). Está pensado para
-- ejecutarse una sola vez (INICIAR_SNW.bat lo omite si la base ya existe).
-- ============================================================

CREATE DATABASE IF NOT EXISTS snw_base CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE snw_base;

-- ------------------------------------------------------------
-- Tablas de pacientes (desarrollo y producción)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pacientes_dev (
  id INT AUTO_INCREMENT PRIMARY KEY,
  nombre VARCHAR(150) NOT NULL,
  apellido VARCHAR(150) NOT NULL DEFAULT '',
  telefono VARCHAR(20) NOT NULL,
  estado ENUM('pendiente','enviado','error') NOT NULL DEFAULT 'pendiente',
  whatsapp_opt_out TINYINT(1) NOT NULL DEFAULT 0,
  respuesta_manual VARCHAR(12) DEFAULT NULL,
  interesado TINYINT(1) NOT NULL DEFAULT 0,
  fecha_actualizacion DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pacientes_prod (
  id INT AUTO_INCREMENT PRIMARY KEY,
  nombre VARCHAR(150) NOT NULL,
  apellido VARCHAR(150) NOT NULL DEFAULT '',
  telefono VARCHAR(20) NOT NULL,
  estado ENUM('pendiente','enviado','error') NOT NULL DEFAULT 'pendiente',
  whatsapp_opt_out TINYINT(1) NOT NULL DEFAULT 0,
  respuesta_manual VARCHAR(12) DEFAULT NULL,
  interesado TINYINT(1) NOT NULL DEFAULT 0,
  fecha_actualizacion DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- ------------------------------------------------------------
-- Tablas auxiliares (una sola por sistema)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS envios (
  id INT AUTO_INCREMENT PRIMARY KEY,
  base_datos VARCHAR(50) DEFAULT NULL,
  plantilla_clave VARCHAR(50) DEFAULT NULL,
  plantilla_nombre VARCHAR(100) DEFAULT NULL,
  total_pacientes INT DEFAULT 0,
  enviados INT DEFAULT 0,
  fallidos INT DEFAULT 0,
  invalidos INT DEFAULT 0,
  estado ENUM('completado','cancelado','rechazado') NOT NULL DEFAULT 'completado',
  comentario VARCHAR(255) DEFAULT NULL,
  fecha_hora DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS log_envios (
  id INT AUTO_INCREMENT PRIMARY KEY,
  envio_id INT DEFAULT NULL,
  paciente_id INT DEFAULT NULL,
  nombre_paciente VARCHAR(150) DEFAULT NULL,
  numero_telefono VARCHAR(20) DEFAULT NULL,
  mensaje TEXT,
  plantilla_clave VARCHAR(50) DEFAULT NULL,
  estado_envio ENUM('enviado','error','numero_invalido') NOT NULL,
  respuesta ENUM('pendiente','respondio','baja') DEFAULT 'pendiente',
  whatsapp_message_id VARCHAR(255) DEFAULT NULL,
  estado_whatsapp ENUM('sent','delivered','read','failed') DEFAULT NULL,
  descripcion_error VARCHAR(255) DEFAULT NULL,
  fecha_hora DATETIME DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_envio (envio_id),
  INDEX idx_paciente (paciente_id)
);

CREATE TABLE IF NOT EXISTS whatsapp_eventos (
  id INT AUTO_INCREMENT PRIMARY KEY,
  clave VARCHAR(64) NOT NULL,
  payload TEXT,
  recibido DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_clave (clave)
);

-- TODA la configuración de la app. En .env solo quedan las credenciales 
-- de la base de datos (DB_*), que se necesitan para llegar aquí.
CREATE TABLE IF NOT EXISTS configuracion (
  clave VARCHAR(60) PRIMARY KEY,
  valor TEXT,
  actualizada DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) CHARACTER SET utf8mb4;

INSERT IGNORE INTO configuracion (clave, valor) VALUES
  -- App / envío
  ('entorno', 'desarrollo'),
  ('metodo_envio', 'simulado'),
  ('numeros_prueba_dev', ''),
  ('numeros_prueba_prod', ''),
  ('intervalo_ms', '1000'),
  ('sesion_expira_horas', '5'),
  ('url_base', ''),
  ('call_center_url', 'https://saludmentalparatodos.cl/telefonosmpt.php'),
  ('call_center_numeros', ''),
  ('call_center_auto_segundos', '4'),
  ('call_center_boton_mensaje', 'Hola, estoy interesado/a en la información que me enviaron.'),
  ('call_center_boton_mensaje_oferta', 'Hola, estoy interesado/a en la oferta que me enviaron.'),
  -- Correo (confirmación de envíos en producción)
  ('smtp_host', ''),
  ('smtp_port', '587'),
  ('smtp_user', ''),
  ('smtp_pass', ''),
  ('smtp_tls', 'true'),
  ('correo_emisor', ''),
  ('correo_destino', ''),
  -- WhatsApp Business Cloud API (Meta)
  ('wa_token', ''),
  ('wa_phone_id', ''),
  ('wa_business_account_id', ''),
  ('wa_verify_token', ''),
  ('wa_template_nombre', ''),
  ('wa_template_lang', 'es'),
  ('wa_webhook_path', '/api/whatsapp/webhook'),
  ('wa_graph_version', 'v26.0'),
  ('wa_moneda', 'USD'),
  ('plantillas_revision_minutos', '2'),
  ('plantillas_badge_aprobada_minutos', '2'),
  -- Límites de envío de Meta (Graph API rate limits, throughput y messaging limit)
  ('wa_rate_limit_activo', 'true'),
  ('wa_rate_limit_umbral_pct', '80'),
  ('wa_rate_limit_pausa_max_s', '30'),
  ('wa_rate_limit_espera_defecto_s', '60'),
  ('wa_rate_limit_reintentos', '3'),
  ('wa_throughput_mps', '10'),
  ('wa_messaging_limit_24h', '2000');
  
-- Rate card de WhatsApp: tarifas por mensaje (USD) descargadas de la página
-- de precios de Meta. Cada rate card distinto se guarda una vez (uq_hash).
CREATE TABLE IF NOT EXISTS tarifas_whatsapp (
  id INT AUTO_INCREMENT PRIMARY KEY,
  pais VARCHAR(60) NOT NULL DEFAULT 'Chile',
  moneda VARCHAR(8) DEFAULT 'USD',
  marketing DECIMAL(12,6) NULL,
  utility DECIMAL(12,6) NULL,
  authentication DECIMAL(12,6) NULL,
  service DECIMAL(12,6) NULL,
  efectiva_desde DATE NULL,
  hash CHAR(64) NOT NULL,
  fuente VARCHAR(255) NULL,
  csv_texto MEDIUMTEXT NULL,
  descargada DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_hash (hash)
) CHARACTER SET utf8mb4;

-- Log de las respuestas enviadas a pacientes interesados (mensaje de call
-- center). Se guarda qué número de call center se asignó.
CREATE TABLE IF NOT EXISTS call_center_log (
  id INT AUTO_INCREMENT PRIMARY KEY,
  paciente_id INT DEFAULT NULL,
  nombre_paciente VARCHAR(150) DEFAULT NULL,
  numero_paciente VARCHAR(20) DEFAULT NULL,
  numero_call_center VARCHAR(20) NOT NULL,
  plantilla_clave VARCHAR(50) DEFAULT NULL,
  automatico TINYINT(1) NOT NULL DEFAULT 0,
  estado ENUM('enviado','error') NOT NULL DEFAULT 'enviado',
  descripcion_error VARCHAR(255) DEFAULT NULL,
  base_datos VARCHAR(50) DEFAULT NULL,
  fecha_hora DATETIME DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_numero (numero_call_center),
  INDEX idx_fecha (fecha_hora)
) CHARACTER SET utf8mb4;

-- Cuentas de la aplicación. `usuario` es el correo (o un identificador corto
-- para las cuentas semilla); `permisos` es una lista separada por comas y solo
-- aplica al rol `usuario` (admin y desarrollador tienen acceso implícito).
-- La página de Configuración es exclusiva del rol `desarrollador`.
CREATE TABLE IF NOT EXISTS usuarios (
  id INT AUTO_INCREMENT PRIMARY KEY,
  usuario VARCHAR(150) NOT NULL,
  nombre VARCHAR(150) NOT NULL DEFAULT '',
  rol ENUM('usuario','administrador','desarrollador') NOT NULL DEFAULT 'usuario',
  permisos VARCHAR(500) NOT NULL DEFAULT '',
  clave_hash CHAR(64) NOT NULL,
  correo_recuperacion VARCHAR(150) NOT NULL DEFAULT '',
  creado DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_usuario (usuario)
) CHARACTER SET utf8mb4;

-- Cuentas por defecto (clave = SHA-256). El backend además garantiza `dev`
-- en cada arranque si falta. Máximo 4 cuentas de rol `desarrollador`.
-- admin/dev no tienen correo de recuperación: lo cargan desde «Mi cuenta».
INSERT IGNORE INTO usuarios (usuario, nombre, rol, permisos, clave_hash) VALUES
  ('admin', 'Administrador', 'administrador', '',
   '240be518fabd2724ddb6f04eeb1da5967448d7e831c08c8fa822809f74c720a9'),
  ('usuario', 'Usuario Final', 'usuario', 'mensajeria,historial,estadisticas,plantillas_editar',
   'dfa7a2273567dcd1efffb9a46308e91c20fa13c44c3441bc69cd6a7869b3f7fd'),
  ('dev', 'Desarrollador', 'desarrollador', '',
   '87274af01876341455b32d805946f272871bb42effa6604dccf28bb027afa82b');

-- Enlaces temporales de "Olvidé mi contraseña" (token de 2 horas).
CREATE TABLE IF NOT EXISTS password_resets (
  token CHAR(64) PRIMARY KEY,
  usuario VARCHAR(150) NOT NULL,
  creado DATETIME DEFAULT CURRENT_TIMESTAMP,
  expira DATETIME NOT NULL,
  usado TINYINT(1) NOT NULL DEFAULT 0,
  INDEX idx_pr_usuario (usuario)
) CHARACTER SET utf8mb4;

-- ------------------------------------------------------------
-- Datos: pacientes_dev (números autorizados)
-- ------------------------------------------------------------
INSERT IGNORE INTO pacientes_dev (id, nombre, apellido, telefono, estado) VALUES
(1, 'David', 'Araya', '+56993921740', 'pendiente'),
(2, 'Sergio', 'Madariaga', '+56941508435', 'pendiente');

-- ------------------------------------------------------------
-- Datos: pacientes_prod (solo números autorizados)
-- ------------------------------------------------------------
INSERT IGNORE INTO pacientes_prod (id, nombre, apellido, telefono, estado) VALUES
(1, 'David', 'Araya', '+56993921740', 'pendiente'),
(2, 'Sergio', 'Madariaga', '+56941508435', 'pendiente');
