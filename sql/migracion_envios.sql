-- Migración para ambas bases de datos
-- Ejecutar una base a la vez

-- ============================================================
-- SNW_PACIENTES (desarrollo)
-- ============================================================

CREATE TABLE IF NOT EXISTS snw_pacientes.envios (
  id INT AUTO_INCREMENT PRIMARY KEY,
  base_datos VARCHAR(100) NOT NULL,
  plantilla_clave VARCHAR(50) DEFAULT NULL,
  plantilla_nombre VARCHAR(100) DEFAULT NULL,
  total_pacientes INT NOT NULL DEFAULT 0,
  enviados INT NOT NULL DEFAULT 0,
  fallidos INT NOT NULL DEFAULT 0,
  invalidos INT NOT NULL DEFAULT 0,
  fecha_hora DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- SNW_PACIENTES_PROD (producción)
-- ============================================================

CREATE TABLE IF NOT EXISTS snw_pacientes_prod.envios (
  id INT AUTO_INCREMENT PRIMARY KEY,
  base_datos VARCHAR(100) NOT NULL,
  plantilla_clave VARCHAR(50) DEFAULT NULL,
  plantilla_nombre VARCHAR(100) DEFAULT NULL,
  total_pacientes INT NOT NULL DEFAULT 0,
  enviados INT NOT NULL DEFAULT 0,
  fallidos INT NOT NULL DEFAULT 0,
  invalidos INT NOT NULL DEFAULT 0,
  fecha_hora DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- Agregar envio_id a log_envios (ambas bases)
-- ============================================================

-- desarrollo
SET @existe = (SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA='snw_pacientes' AND TABLE_NAME='log_envios' AND COLUMN_NAME='envio_id');
SET @sql = IF(@existe = 0, 'ALTER TABLE snw_pacientes.log_envios ADD COLUMN envio_id INT DEFAULT NULL AFTER id', 'SELECT "ya existe envio_id en desarrollo"');
PREPARE s1 FROM @sql; EXECUTE s1; DEALLOCATE PREPARE s1;

-- producción
SET @existe = (SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA='snw_pacientes_prod' AND TABLE_NAME='log_envios' AND COLUMN_NAME='envio_id');
SET @sql = IF(@existe = 0, 'ALTER TABLE snw_pacientes_prod.log_envios ADD COLUMN envio_id INT DEFAULT NULL AFTER id', 'SELECT "ya existe envio_id en produccion"');
PREPARE s2 FROM @sql; EXECUTE s2; DEALLOCATE PREPARE s2;

-- ============================================================
-- Verificar
-- ============================================================
DESCRIBE snw_pacientes.envios;
SELECT COLUMN_NAME, TABLE_SCHEMA FROM information_schema.COLUMNS WHERE TABLE_NAME='log_envios' AND COLUMN_NAME='envio_id';
