CREATE DATABASE IF NOT EXISTS snw_pacientes_prod CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE snw_pacientes_prod;

CREATE TABLE IF NOT EXISTS pacientes (
  id INT AUTO_INCREMENT PRIMARY KEY,
  nombre VARCHAR(150) NOT NULL,
  apellido VARCHAR(150) NOT NULL DEFAULT '',
  telefono VARCHAR(20) NOT NULL,
  info_extra VARCHAR(255) DEFAULT NULL,
  estado ENUM('pendiente','enviado','error') NOT NULL DEFAULT 'pendiente',
  plantilla VARCHAR(50) DEFAULT 'default',
  fecha_creacion DATETIME DEFAULT CURRENT_TIMESTAMP,
  fecha_actualizacion DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS log_envios (
  id INT AUTO_INCREMENT PRIMARY KEY,
  paciente_id INT DEFAULT NULL,
  nombre_paciente VARCHAR(150) DEFAULT NULL,
  numero_telefono VARCHAR(20) DEFAULT NULL,
  mensaje TEXT,
  plantilla_clave VARCHAR(50) DEFAULT NULL,
  estado_envio ENUM('enviado','error','numero_invalido') NOT NULL,
  descripcion_error VARCHAR(255) DEFAULT NULL,
  fecha_hora DATETIME DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO pacientes (nombre, apellido, telefono, info_extra, estado)
VALUES ('Cliente', 'Producción', '+56955500011', 'Atención anual programada', 'pendiente');
