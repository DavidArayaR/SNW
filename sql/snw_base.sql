-- ============================================================
-- SNW - Base de datos única (snw_base)
-- ============================================================
-- Infraestructura de UNA sola base de datos con 5 tablas:
--   - pacientes_dev   : números autorizados para pruebas de desarrollo
--   - pacientes_prod  : números autorizados (sin datos ficticios)
--   - envios          : lotes de envío (una fila por "Iniciar envío")
--   - log_envios      : historial individual de mensajes
--   - whatsapp_eventos: eventos del webhook (idempotencia)
--
-- Es idempotente (IF NOT EXISTS / INSERT IGNORE): crea la estructura y
-- siembra solo los 2 números autorizados. Está pensado para ejecutarse
-- una sola vez (INICIAR_SNW.bat lo omite si la base ya existe).
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
  info_extra VARCHAR(255) DEFAULT NULL,
  estado ENUM('pendiente','enviado','error') NOT NULL DEFAULT 'pendiente',
  whatsapp_opt_out TINYINT(1) NOT NULL DEFAULT 0,
  fecha_actualizacion DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pacientes_prod (
  id INT AUTO_INCREMENT PRIMARY KEY,
  nombre VARCHAR(150) NOT NULL,
  apellido VARCHAR(150) NOT NULL DEFAULT '',
  telefono VARCHAR(20) NOT NULL,
  info_extra VARCHAR(255) DEFAULT NULL,
  estado ENUM('pendiente','enviado','error') NOT NULL DEFAULT 'pendiente',
  whatsapp_opt_out TINYINT(1) NOT NULL DEFAULT 0,
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
  estado ENUM('completado','cancelado') NOT NULL DEFAULT 'completado',
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
  respuesta ENUM('pendiente','click','respondio','baja') DEFAULT 'pendiente',
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

-- ------------------------------------------------------------
-- Datos: pacientes_dev (números autorizados)
-- ------------------------------------------------------------
INSERT IGNORE INTO pacientes_dev (id, nombre, apellido, telefono, info_extra, estado) VALUES
(1, 'David', 'Araya', '+56993921740', 'Control mensual', 'pendiente'),
(2, 'Sergio', 'Madariaga', '+56941508435', 'Atención anual programada', 'pendiente');

-- ------------------------------------------------------------
-- Datos: pacientes_prod (solo números autorizados)
-- ------------------------------------------------------------
INSERT IGNORE INTO pacientes_prod (id, nombre, apellido, telefono, info_extra, estado) VALUES
(1, 'David', 'Araya', '+56993921740', 'Control mensual', 'pendiente'),
(2, 'Sergio', 'Madariaga', '+56941508435', 'Atención anual programada', 'pendiente');
