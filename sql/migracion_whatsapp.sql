-- Migración: integración WhatsApp Business API (idempotente)
-- Agrega columnas para el WhatsApp Message ID, estado de WhatsApp y opt-out.

-- Base desarrollo
ALTER TABLE snw_pacientes.log_envios
  ADD COLUMN IF NOT EXISTS whatsapp_message_id VARCHAR(255) DEFAULT NULL AFTER respuesta,
  ADD COLUMN IF NOT EXISTS estado_whatsapp ENUM('sent','delivered','read','failed') DEFAULT NULL AFTER whatsapp_message_id;

ALTER TABLE snw_pacientes.pacientes
  ADD COLUMN IF NOT EXISTS whatsapp_opt_out TINYINT(1) NOT NULL DEFAULT 0 AFTER estado;

CREATE TABLE IF NOT EXISTS snw_pacientes.whatsapp_eventos (
  id INT AUTO_INCREMENT PRIMARY KEY,
  clave VARCHAR(64) NOT NULL,
  payload TEXT,
  recibido DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_clave (clave)
);

-- Base producción
ALTER TABLE snw_pacientes_prod.log_envios
  ADD COLUMN IF NOT EXISTS whatsapp_message_id VARCHAR(255) DEFAULT NULL AFTER respuesta,
  ADD COLUMN IF NOT EXISTS estado_whatsapp ENUM('sent','delivered','read','failed') DEFAULT NULL AFTER whatsapp_message_id;

ALTER TABLE snw_pacientes_prod.pacientes
  ADD COLUMN IF NOT EXISTS whatsapp_opt_out TINYINT(1) NOT NULL DEFAULT 0 AFTER estado;

CREATE TABLE IF NOT EXISTS snw_pacientes_prod.whatsapp_eventos (
  id INT AUTO_INCREMENT PRIMARY KEY,
  clave VARCHAR(64) NOT NULL,
  payload TEXT,
  recibido DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_clave (clave)
);
