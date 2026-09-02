-- ============================================================
-- Tablas auxiliares del sistema SNW (sin tocar la tabla 'pacientes')
-- ============================================================
-- Este script crea únicamente las tablas que el sistema SNW necesita
-- para su funcionamiento (historial, lotes de envío e idempotencia del
-- webhook). NO modifica ni recrea la tabla 'pacientes', por lo que es
-- seguro aplicarlo sobre una base de datos de producción ya existente
-- que contenga una tabla de pacientes con esquema propio.
--
-- Cómo usarlo:
--   mysql -u <usuario> -p <nombre_de_la_base> < tablas_auxiliares.sql
--
-- O, si ya estás conectado a la base:
--   SOURCE tablas_auxiliares.sql;
--
-- Es idempotente (usa IF NOT EXISTS), por lo que se puede re-ejecutar
-- sin riesgo de duplicar datos ni de alterar estructuras existentes.
-- ============================================================

-- 1) Historial individual de mensajes (relaciona cada mensaje con su lote y paciente)
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

-- 2) Lotes de envío (una fila por cada "Iniciar envío")
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

-- 3) Eventos del webhook de WhatsApp (para idempotencia / evitar duplicados)
CREATE TABLE IF NOT EXISTS whatsapp_eventos (
  id INT AUTO_INCREMENT PRIMARY KEY,
  clave VARCHAR(64) NOT NULL,
  payload TEXT,
  recibido DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_clave (clave)
);
