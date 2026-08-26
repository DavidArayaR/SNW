-- Cambiar todos los pacientes de "enviado" a "pendiente"
-- Ejecutar en la base que se desee (desarrollo, producción o ambas)

-- Base de datos desarrollo
UPDATE snw_pacientes.pacientes SET estado = 'pendiente' WHERE estado = 'enviado';

-- Base de datos producción
UPDATE snw_pacientes_prod.pacientes SET estado = 'pendiente' WHERE estado = 'enviado';

-- Verificar resultado
SELECT estado, COUNT(*) AS total FROM snw_pacientes.pacientes GROUP BY estado;
SELECT estado, COUNT(*) AS total FROM snw_pacientes_prod.pacientes GROUP BY estado;
