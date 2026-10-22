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
assert.doesNotMatch(html, /id="selCentroPrueba"/);
assert.doesNotMatch(app, /selCentroPrueba/);
assert.match(app, /!centroDePlantillaActiva\(\)/);
assert.match(app, /body: JSON\.stringify\(\{ telefono, ejemplos: ejemplosPreview\(\) \}\)/);
const descripcionCuenta = app.split("function descripcionCuentaPlantilla(codigo) {")[1]?.split("function pintarCuentaPlantillaEditor()")[0];
assert(descripcionCuenta, "Debe existir la descripción del centro de la plantilla");
assert.match(descripcionCuenta, /cuenta\.nombre.*cuenta\.phoneNumber/);
assert.doesNotMatch(descripcionCuenta, /wabaId|WABA ID/);
assert.doesNotMatch(html, /La plantilla se registra en la WABA|Selecciona la WABA/);
console.log("Orden y exclusividad del encabezado: OK");
