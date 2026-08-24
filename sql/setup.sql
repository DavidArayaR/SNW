ALTER TABLE plantillas ADD COLUMN nombre VARCHAR(100) NOT NULL DEFAULT '' AFTER clave;
UPDATE plantillas SET nombre = CONCAT(UCASE(LEFT(REPLACE(clave, '_', ' '), 1)), SUBSTRING(REPLACE(clave, '_', ' '), 2)) WHERE nombre = '';
