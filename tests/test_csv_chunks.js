const assert = require("node:assert/strict");
const test = require("node:test");
const { fragmentarCsv, MAX_FILAS_BLOQUE_CSV } = require("../frontend/js/csv-chunks.js");

async function bloques(texto, maxBytes) {
  const resultado = [];
  for await (const bloque of fragmentarCsv(new Blob([texto]), maxBytes)) resultado.push(bloque);
  return resultado;
}

test("divide en registros completos, incluso con saltos dentro de comillas", async () => {
  const csv = "nombre,apellido,telefono\r\n" +
    'Ana,"Pérez\nLópez",+56911111111\r\n' +
    'Beto,Soto,+56922222222\r\n';
  const partes = await bloques(csv, 75);
  assert.equal(partes.length, 2);
  assert.deepEqual(partes.map((p) => p.filaInicial), [2, 3]);
  assert.deepEqual(partes.map((p) => p.cantidad), [1, 1]);
  assert.match(partes[0].contenido, /"Pérez\nLópez"/);
  assert.match(partes[1].contenido, /^nombre,apellido,telefono\nBeto/);
  for (const parte of partes) assert.ok(new TextEncoder().encode(parte.contenido).length <= 75);
});

test("rechaza una sola fila que excede el bloque", async () => {
  await assert.rejects(async () => {
    for await (const _ of fragmentarCsv(new Blob(["nombre,apellido,telefono\nAna," + "x".repeat(90) + ",+56911111111"]), 70)) {}
  }, /fila 2/);
});

test("rechaza comillas sin cerrar y CSV vacío", async () => {
  await assert.rejects(async () => { for await (const _ of fragmentarCsv(new Blob([]))) {} }, /vacío/);
  await assert.rejects(async () => {
    for await (const _ of fragmentarCsv(new Blob(['nombre,apellido,telefono\n"Ana,Sol,+56911111111']))) {}
  }, /comillas sin cerrar/);
});

test("un archivo mayor a cinco MB produce partes menores a dos MB", async () => {
  const fila = `Ana,${"x".repeat(2000)},+56911111111\n`;
  const csv = `nombre,apellido,telefono\n${fila.repeat(2700)}`;
  assert.ok(new TextEncoder().encode(csv).length > 5 * 1024 * 1024);
  const partes = await bloques(csv, 2 * 1024 * 1024);
  assert.ok(partes.length >= 3);
  assert.equal(partes.reduce((total, parte) => total + parte.cantidad, 0), 2700);
  for (const parte of partes) {
    assert.ok(new TextEncoder().encode(parte.contenido).length <= 2 * 1024 * 1024);
  }
});

test("cuarenta mil filas compactas se suben en veinte partes", async () => {
  const fila = "Ana,Sol,+56911111111\n";
  const partes = await bloques(`nombre,apellido,telefono\n${fila.repeat(40000)}`);
  assert.equal(MAX_FILAS_BLOQUE_CSV, 2000);
  assert.equal(partes.length, 20);
  assert.equal(partes.reduce((total, parte) => total + parte.cantidad, 0), 40000);
  assert.deepEqual(partes.map((parte) => parte.filaInicial),
    Array.from({ length: 20 }, (_, indice) => 2 + indice * 2000));
  assert.ok(partes.every((parte) => parte.cantidad === 2000));
});
