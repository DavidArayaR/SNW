ALTER TABLE pacientes
  ADD COLUMN IF NOT EXISTS apellido VARCHAR(150) NOT NULL DEFAULT '' AFTER nombre;

INSERT INTO pacientes (nombre, apellido, telefono, info_extra, estado, plantilla)
VALUES ('David', 'Araya', '+5693921740', 'Control Médico de rutina - Consulta Nº 4', 'pendiente', 'default');

INSERT INTO pacientes (nombre, apellido, telefono, info_extra, estado, plantilla)
VALUES ('Sergio', 'Madariaga', '941508435', 'Control general', 'pendiente', 'default');