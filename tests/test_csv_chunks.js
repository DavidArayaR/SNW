const assert = require("node:assert/strict");
const test = require("node:test");
const { fragmentarCsv, leerEncabezadosCsv, sugerirMapeoCsv, validarMapeoCsv,
  MAX_FILAS_BLOQUE_CSV } = require("../frontend/js/csv-chunks.js");

test("lee encabezados entre comillas y propone columnas sin cargar todo el archivo", async () => {
  const encabezados = await leerEncabezadosCsv(new Blob([
    '"Nombres","Apellidos","Número de teléfono","Respuesta externa"\nAna,Sol,93921740,Respondio\n',
  ]));
  assert.deepEqual(encabezados,
    ["Nombres", "Apellidos", "Número de teléfono", "Respuesta externa"]);
  assert.deepEqual(sugerirMapeoCsv(encabezados), {
    nombre: "Nombres", apellido: "Apellidos", telefono: "Número de teléfono",
  });
  assert.equal(validarMapeoCsv({ nombre: "Nombres", apellido: "Apellidos",
    telefono: "Número de teléfono" }, encabezados), "");
  assert.match(validarMapeoCsv({ nombre: "Nombres", apellido: "Nombres",
    telefono: "Número de teléfono" }, encabezados), /un solo campo/);
});

test("rechaza encabezados vacíos o repetidos", async () => {
  await assert.rejects(leerEncabezadosCsv(new Blob(["A,A,C\n1,2,3\n"])), /repetidos/);
  await assert.rejects(leerEncabezadosCsv(new Blob(["A,,C\n1,2,3\n"])), /vacías/);
});

test("sugiere columnas de última atención", () => {
  const encabezados = ["Nombre", "Apellido", "Teléfono", "Tratamiento última atención",
    "Fecha última atención"];
  const mapeo = sugerirMapeoCsv(encabezados);
  assert.equal(mapeo.tratamiento_ultima_atencion, "Tratamiento última atención");
  assert.equal(mapeo.fecha_ultima_atencion, "Fecha última atención");
  assert.equal(validarMapeoCsv(mapeo, encabezados), "");
});

test("lee CSV Windows-1252 con punto y coma y nombre completo", async () => {
  const csv = Buffer.from("nombre_paciente;telefono_paciente;fecha_ultima_atencion;tratamiento_ultima_atencion\nJosé Pérez;93921740;03-10-2026 14:30;Control\n", "latin1");
  const archivo = new Blob([csv]);
  const encabezados = await leerEncabezadosCsv(archivo);
  assert.equal(encabezados.length, 4);
  const mapeo = sugerirMapeoCsv(encabezados);
  assert.equal(mapeo.nombre, "nombre_paciente");
  assert.equal(mapeo.telefono, "telefono_paciente");
  assert.equal(validarMapeoCsv(mapeo, encabezados), "");
  const partes = await bloques(archivo);
  assert.equal(partes.length, 1);
  assert.match(partes[0].contenido, /José Pérez/);
});

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
