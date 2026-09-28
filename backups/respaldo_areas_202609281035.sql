-- Respaldo previo a renombre especialidades -> areas
-- 2026-09-28 10:35:15
CREATE TABLE `especialidades` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `nombre_visible` varchar(150) NOT NULL,
  `nombre_tabla_base` varchar(64) NOT NULL,
  `fecha_creacion` datetime DEFAULT current_timestamp(),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_tabla_base` (`nombre_tabla_base`)
) ENGINE=InnoDB AUTO_INCREMENT=5 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
INSERT INTO `especialidades` (`id`, `nombre_visible`, `nombre_tabla_base`, `fecha_creacion`) VALUES (1, 'hol', 'pacientes_hola', '2026-09-24 17:08:29');
INSERT INTO `especialidades` (`id`, `nombre_visible`, `nombre_tabla_base`, `fecha_creacion`) VALUES (2, 'kine1', 'pacientes_kine1', '2026-09-24 17:14:16');
-- especialidades: 2 filas
CREATE TABLE `roles_especialidad` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `especialidad_id` int(11) NOT NULL,
  `nombre` varchar(150) NOT NULL,
  `descripcion` varchar(255) NOT NULL DEFAULT '',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_especialidad` (`especialidad_id`),
  KEY `idx_rol_nombre` (`nombre`)
) ENGINE=InnoDB AUTO_INCREMENT=5 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
INSERT INTO `roles_especialidad` (`id`, `especialidad_id`, `nombre`, `descripcion`) VALUES (1, 1, 'hol', 'Acceso a la especialidad hol');
INSERT INTO `roles_especialidad` (`id`, `especialidad_id`, `nombre`, `descripcion`) VALUES (2, 2, 'kine1', 'Acceso a la especialidad kine1');
-- roles_especialidad: 2 filas
CREATE TABLE `usuario_especialidad_roles` (
  `usuario_id` int(11) NOT NULL,
  `rol_id` int(11) NOT NULL,
  `creado` datetime DEFAULT current_timestamp(),
  PRIMARY KEY (`usuario_id`,`rol_id`),
  KEY `idx_uer_rol` (`rol_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
INSERT INTO `usuario_especialidad_roles` (`usuario_id`, `rol_id`, `creado`) VALUES (2, 1, '2026-09-25 13:28:01');
INSERT INTO `usuario_especialidad_roles` (`usuario_id`, `rol_id`, `creado`) VALUES (29, 2, '2026-09-25 14:23:26');
-- usuario_especialidad_roles: 2 filas
-- areas: no existe, se omite
-- roles_area: no existe, se omite
-- usuario_area_roles: no existe, se omite
