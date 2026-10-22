const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");

const raiz = join(__dirname, "..");
const html = readFileSync(join(raiz, "frontend", "mensajeria.html"), "utf8");
const app = readFileSync(join(raiz, "frontend", "js", "app.js"), "utf8");

const orden = ["inpNombre", "inpArea", "checkEncabezadoMultimedia", "inpMensaje", "inpPieTexto"];
const posiciones = orden.map((id) => html.indexOf(`id="${id}"`));
assert(posiciones.every((posicion) => posicion >= 0));
assert(posiciones.every((posicion, i) => i === 0 || posicion > posiciones[i - 1]));
assert.match(html, /id="grupoEncabezadoMedia"[^>]* hidden/);
assert.match(app, /encabezado_media_id: encabezadoMediaActivo\(\)/);
assert.match(app, /encabezado_texto: encabezadoTextoActivo\(\)/);
assert.match(app, /encabezadoMediaActivo\(\), encabezadoTextoActivo\(\)/);
const bloqueo = app.split("function actualizarBloqueoCampos() {")[1]?.split("function ")[0];
assert(bloqueo, "Debe existir el control de bloqueo del editor");
assert.equal((bloqueo.match(/selCentroPrueba\.disabled = false;/g) || []).length, 2,
  "El centro emisor de prueba debe quedar habilitado tanto en solo lectura como al editar");
console.log("Orden y exclusividad del encabezado: OK");
