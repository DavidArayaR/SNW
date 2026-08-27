UPDATE snw_pacientes.pacientes p
LEFT JOIN snw_pacientes.log_envios l ON l.id = (
  SELECT l2.id FROM snw_pacientes.log_envios l2 WHERE l2.paciente_id = p.id ORDER BY l2.id DESC LIMIT 1
)
SET p.estado = 'pendiente'
WHERE p.estado = 'enviado' AND COALESCE(l.respuesta, 'pendiente') = 'pendiente';

UPDATE snw_pacientes_prod.pacientes p
LEFT JOIN snw_pacientes_prod.log_envios l ON l.id = (
  SELECT l2.id FROM snw_pacientes_prod.log_envios l2 WHERE l2.paciente_id = p.id ORDER BY l2.id DESC LIMIT 1
)
SET p.estado = 'pendiente'
WHERE p.estado = 'enviado' AND COALESCE(l.respuesta, 'pendiente') = 'pendiente';
