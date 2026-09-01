-- Simular 3 pacientes enviados sin respuesta (estado pendiente -> enviado, respuesta "sin respuesta").
-- Pacientes: 11 Patricia Vargas, 12 Miguel Torres, 13 Cristina Reyes.

-- Base desarrollo
UPDATE snw_pacientes.pacientes
SET estado = 'enviado'
WHERE id IN (11, 12, 13)
  AND estado = 'pendiente';

INSERT INTO snw_pacientes.log_envios (envio_id, paciente_id, nombre_paciente, numero_telefono, mensaje, plantilla_clave, estado_envio, respuesta)
SELECT NULL, p.id, CONCAT(p.nombre, ' ', p.apellido), p.telefono, '', 'default', 'enviado', 'pendiente'
FROM snw_pacientes.pacientes p
WHERE p.id IN (11, 12, 13);

-- Base producción
UPDATE snw_pacientes_prod.pacientes
SET estado = 'enviado'
WHERE id IN (11, 12, 13)
  AND estado = 'pendiente';

INSERT INTO snw_pacientes_prod.log_envios (envio_id, paciente_id, nombre_paciente, numero_telefono, mensaje, plantilla_clave, estado_envio, respuesta)
SELECT NULL, p.id, CONCAT(p.nombre, ' ', p.apellido), p.telefono, '', 'default', 'enviado', 'pendiente'
FROM snw_pacientes_prod.pacientes p
WHERE p.id IN (11, 12, 13);
