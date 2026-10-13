const assert = require("node:assert/strict");
const elegir = require("../frontend/js/plantilla-ia.js");

const original = "Mensaje escrito por una persona";
assert.equal(elegir("Hola {nombre}"), "Hola {nombre}");
assert.equal(original, "Mensaje escrito por una persona");
assert.equal(elegir("  Hola {nombre}  "), "Hola {nombre}");
assert.equal(elegir(""), null);
console.log("Pruebas de reemplazo del borrador: OK");
