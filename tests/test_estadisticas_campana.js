/* Ejecutar: node tests/test_estadisticas_campana.js */
const assert = require("node:assert/strict");
global.window = {};
require("../frontend/js/estadisticas-campana.js");
const { filtrar, comparar } = window.SNWEstadisticasCampana;
const datos = {
  resumen: { validos: 100 }, costo: { moneda: "CLP" },
  segmentos: [
    { cantidad: 10, resultado: "interes", entrega: "leidos", costo: 100, facturables: 10 },
    { cantidad: 20, resultado: "desinteres", entrega: "entregados", costo: 200, facturables: 20 },
    { cantidad: 5, resultado: "baja", entrega: "leidos", costo: 50, facturables: 5 },
    { cantidad: 40, resultado: "sin_seleccion", entrega: "leidos", visto_sin_respuesta: true, costo: 400, facturables: 40 },
    { cantidad: 10, resultado: "sin_seleccion", entrega: "leidos", visto_sin_respuesta: false, costo: 100, facturables: 10 },
    { cantidad: 15, resultado: "sin_seleccion", entrega: "sin_confirmacion", costo: 0, facturables: 0 },
  ],
  botones: [{ boton: "Me interesa", accion: "interes", resultado: "interes", entrega: "read", clics: 15, pacientes: 10 },
    { boton: "No me interesa", accion: "desinteres", resultado: "desinteres", entrega: "delivered", clics: 25, pacientes: 20 }],
};
const todos = filtrar(datos);
assert.equal(todos.resumen.tasa_interes, 10);
assert.equal(todos.resumen.vistos_sin_respuesta, 40); // Respuestas malas no cuentan como interés ni como visto.
assert.equal(todos.costo.importe, 850);
assert.equal(todos.costo.por_interesado, 85);
const leidos = filtrar(datos, "leidos");
assert.equal(leidos.resumen.validos, 65);
assert.equal(leidos.resumen.tasa_interes, 15.4);
assert.equal(leidos.costo.importe, 650);
assert.equal(leidos.botones.length, 1);
assert.equal(leidos.botones[0].porcentaje, 15.4);
const vistos = filtrar(datos, "todos", "vistos");
assert.equal(vistos.resumen.validos, 40);
assert.equal(vistos.resumen.tasa_interes, 0);
assert.equal(vistos.botones.length, 0);
assert.equal(filtrar(datos, "entregados").resumen.validos, 85); // Incluye lecturas.
const vacio = filtrar(datos, "sin_confirmacion", "interes");
assert.equal(vacio.resumen.validos, 0);
assert.equal(vacio.resumen.tasa_interes, 0);
assert.equal(vacio.costo.por_interesado, null);
assert.equal(comparar(todos, "general").reduce((n, s) => n + s.cantidad, 0), 100);
const versus = comparar(todos, "interes-vistos");
assert.deepEqual(versus.map(s => s.cantidad), [10, 40, 50]); // Conserva denominador, sin inflar el interés a 20 %.
assert.equal(comparar(todos, "entregas").reduce((n, s) => n + s.cantidad, 0), 100);
const sinTarifa = structuredClone(datos);
sinTarifa.segmentos[0].costo = null;
assert.equal(filtrar(sinTarifa).costo.importe, null);
assert.equal(filtrar(sinTarifa, "todos", "desinteres").costo.importe, 200);
assert.equal(datos.resumen.validos, 100); // No mutar el resultado original al filtrar.
console.log("Segmentación, comparaciones, porcentajes y costos: OK");
