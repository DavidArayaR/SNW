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
assert.equal(todos.resumen.leidos_con_respuesta, 25); // Incluye respuestas sin interés; solo lecturas confirmadas.
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
assert.equal(vistos.resumen.leidos_con_respuesta, 0);
assert.equal(vistos.botones.length, 0);
assert.equal(filtrar(datos, "entregados").resumen.validos, 85); // Incluye lecturas.
const vacio = filtrar(datos, "sin_confirmacion", "interes");
assert.equal(vacio.resumen.validos, 0);
assert.equal(vacio.resumen.leidos_con_respuesta, 0);
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

// La cifra principal conserva el envío completo aunque se explore un segmento.
const fs = require("node:fs");
const vm = require("node:vm");
const codigo = fs.readFileSync(require("node:path").join(__dirname, "../frontend/js/estadisticas.js"), "utf8");
const controles = {
  "#filtroEntregaCampana": { value: "todos" },
  "#filtroResultadoCampana": { value: "todos" },
  "#comparacionCampana": { value: "general" },
  "#resumenFiltrosCampana": {},
  "#filtrosCampana": { classList: { toggle() {} } },
  "#detalleCampana": {},
  "#campanaDetalleResultados": { innerHTML: "", querySelectorAll: () => [], querySelector: () => ({ addEventListener() {} }) },
};
const contexto = {
  window, document: { querySelectorAll: () => [] },
  $: selector => controles[selector], num: String, escaparHtml: String,
  fmtMoneda: importe => String(importe),
  resultadosCampana: { innerHTML: "" }, estadoCampana: {},
  datosCampana: { ...datos, envio: { id: 1, plantilla_nombre: "Prueba", centro: "Clínica", inicio: "2026-11-02" } },
};
vm.createContext(contexto);
vm.runInContext(codigo.slice(codigo.indexOf("function pintarCampana()"), codigo.indexOf('selectorCampana.addEventListener("toggle"')), contexto);
contexto.pintarCampana();
const resumenCompleto = contexto.resultadosCampana.innerHTML;
assert.equal((resumenCompleto.match(/<strong>/g) || []).length, 3);
assert.ok(resumenCompleto.includes("10 de 100 pacientes mostraron interés"));
assert.ok(resumenCompleto.includes("<strong>25</strong>"));
assert.ok(!resumenCompleto.includes("stats-campanas__torta"));
controles["#filtroResultadoCampana"].value = "interes";
contexto.pintarCampana();
assert.equal(contexto.resultadosCampana.innerHTML, resumenCompleto);
assert.ok(controles["#campanaDetalleResultados"].innerHTML.includes("Se muestran 10 de 100 pacientes"));
contexto.datosCampana = { ...contexto.datosCampana, segmentos: [], botones: [] };
contexto.pintarCampana();
assert.ok(contexto.resultadosCampana.innerHTML.includes("<strong>—</strong>"));
console.log("Resumen estable de tres cifras y detalle filtrado independiente: OK");

const { combinar } = window.SNWEstadisticasCampana;
const primero = { ...structuredClone(datos), envio: { id: 1, centro: "Clínica A" } };
const segundo = { ...structuredClone(datos), envio: { id: 2, centro: "Clínica B" },
  segmentos: [{ cantidad: 10, resultado: "interes", entrega: "leidos", visto_sin_respuesta: false, costo: 100, facturables: 10 }],
  botones: [{ boton: "Me interesa", accion: "interes", resultado: "interes", entrega: "read", clics: 12, pacientes: 10 }],
};
const conjunto = combinar([primero, segundo, primero]);
const suma = filtrar(conjunto);
assert.equal(conjunto.envios.length, 2); // Un ID repetido no duplica sus resultados.
assert.equal(suma.resumen.validos, 110);
assert.equal(suma.resumen.tasa_interes, 18.2); // 20/110, nunca promedio de 10 % y 100 %.
assert.equal(suma.resumen.leidos_con_respuesta, 35);
assert.equal(suma.costo.importe, 950);
assert.equal(suma.botones.find(b => b.accion === "interes").pacientes, 20);
assert.equal(filtrar(conjunto, "todos", "interes").resumen.validos, 20);
assert.equal(filtrar(combinar([primero, { ...segundo, costo: { moneda: "USD" } }])).costo.importe, null);
assert.throws(() => combinar([primero, { ...segundo, simulacion: true }]), /por separado/);
assert.throws(() => combinar([]), /Selecciona/);
assert.equal(combinar([primero]), primero); // Los enlaces de un único envío siguen funcionando.
contexto.datosCampana = conjunto;
contexto.pintarCampana();
assert.ok(contexto.resultadosCampana.innerHTML.includes("20 de 110 registros de pacientes"));
assert.ok(contexto.resultadosCampana.innerHTML.includes("si participó en varios, cuenta en cada uno"));
assert.ok(!contexto.resultadosCampana.innerHTML.includes("Envío #0"));
assert.ok(controles["#campanaDetalleResultados"].innerHTML.includes("Envíos incluidos"));
console.log("Selección múltiple: ponderación, deduplicación de IDs, monedas y separación de simulaciones: OK");
