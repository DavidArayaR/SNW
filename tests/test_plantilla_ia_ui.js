const assert = require("node:assert/strict");
const elegir = require("../frontend/js/plantilla-ia.js");

const original = "Mensaje escrito por una persona";
assert.equal(elegir(original, "Hola {nombre}", () => false), null);
assert.equal(original, "Mensaje escrito por una persona");
assert.equal(elegir(original, "Hola {nombre}", () => true), "Hola {nombre}");
assert.equal(elegir("", "Hola {nombre}", () => { throw new Error("No debe confirmar"); }), "Hola {nombre}");
assert.equal(elegir("", "", () => true), null);
console.log("Pruebas de reemplazo del borrador: OK");
