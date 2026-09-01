-- Resetea a 'pendiente' los pacientes 'enviado' que están "sin respuesta",
-- EXCEPTO los pacientes de demostración (11, 12, 13) que deben permanecer
-- como enviados sin respuesta.

UPDATE snw_pacientes.pacientes p
LEFT JOIN snw_pacientes.log_envios l ON l.id = (
  SELECT l2.id FROM snw_pacientes.log_envios l2 WHERE l2.paciente_id = p.id ORDER BY l2.id DESC LIMIT 1
)
SET p.estado = 'pendiente'
WHERE p.estado = 'enviado'
  AND COALESCE(l.respuesta, 'pendiente') = 'pendiente'
  AND p.id NOT IN (11, 12, 13);

UPDATE snw_pacientes_prod.pacientes p
LEFT JOIN snw_pacientes_prod.log_envios l ON l.id = (
  SELECT l2.id FROM snw_pacientes_prod.log_envios l2 WHERE l2.paciente_id = p.id ORDER BY l2.id DESC LIMIT 1
)
SET p.estado = 'pendiente'
WHERE p.estado = 'enviado'
  AND COALESCE(l.respuesta, 'pendiente') = 'pendiente'
  AND p.id NOT IN (11, 12, 13);
