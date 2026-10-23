const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const vm = require("node:vm");

const raiz = join(__dirname, "..");
const html = readFileSync(join(raiz, "frontend", "pacientes.html"), "utf8");
const script = readFileSync(join(raiz, "frontend", "js", "pacientes-tabs.js"), "utf8");
const reagendarScript = readFileSync(join(raiz, "frontend", "js", "reagendar.js"), "utf8");
const css = readFileSync(join(raiz, "frontend", "css", "pacientes.css"), "utf8");
assert.match(html, /id="pacientes-tab-pacientes"[^>]*aria-controls="panelBaseDatos"/);
assert.match(html, /id="pacientes-tab-reagendar"[^>]*aria-controls="cardReagendar"[^>]*hidden/);
assert.match(html, /id="panelBaseDatos"[^>]*role="tabpanel"/);
assert.match(html, /id="cardReagendar"[^>]*role="tabpanel"[^>]*hidden/);
assert.match(html, /id="reagendarAplazarForm"[^>]*role="dialog"/);
assert.match(html, /Archivos recibidos y envíos/);
assert.match(html, /id="reagendarVerTodas"[^>]*hidden/);
assert.match(css, /#reagendarVerTodas\s*\{\s*background:\s*#f3d88e/);
assert.match(css, /:root\[data-tema="oscuro"\].*#reagendarVerTodas/);
assert.match(html, /Mensajes aceptados/);
assert.match(reagendarScript, /document\.body\.appendChild\(formularioAplazar\)/);
assert.match(reagendarScript, /boton\.top - alto - 10/);
assert.match(reagendarScript, /PROGRAMADO: \["Programado"/);
assert.match(reagendarScript, /REVISION: \["Requiere revisión"/);
assert.match(reagendarScript, /loteAplazarId = lote\.id/);
assert.match(reagendarScript, /loteActual = null; pagina = 1;/);
assert.match(css, /\.reagendar__aplazar\s*\{\s*position:\s*fixed/);
assert.match(css, /backdrop-filter:\s*blur\(/);

function probar(privilegiado) {
  const cargas = { pacientes: 0, reagendar: 0 };
  let cierresAplazamiento = 0;
  const estilo = { setProperty() {} };
  function boton(tipo) {
    const clases = new Set(tipo === "pacientes" ? ["activo"] : []);
    return {
      dataset: { pacientesTab: tipo }, hidden: tipo === "reagendar", tabIndex: 0,
      style: estilo, eventos: {},
      classList: { toggle(clase, activar) { activar ? clases.add(clase) : clases.delete(clase); },
        contains(clase) { return clases.has(clase); } },
      setAttribute(nombre, valor) { this[nombre] = valor; },
      addEventListener(nombre, fn) { this.eventos[nombre] = fn; },
      getBoundingClientRect() { return { left: tipo === "pacientes" ? 10 : 110, width: 100 }; },
      focus() {},
    };
  }
  const pacientes = boton("pacientes");
  const reagendar = boton("reagendar");
  const barra = {
    clientLeft: 0, scrollLeft: 0, style: estilo,
    querySelectorAll() { return [pacientes, reagendar]; },
    querySelector(selector) {
      if (selector === ".msg-tab.activo") {
        return pacientes.classList.contains("activo") ? pacientes : reagendar;
      }
      return selector.includes('"reagendar"') ? reagendar : pacientes;
    },
    getBoundingClientRect() { return { left: 0 }; },
  };
  const base = { hidden: false, style: estilo };
  const citas = { hidden: true, style: estilo };
  const elementos = { panelBaseDatos: base, cardReagendar: citas,
    "pacientes-tab-reagendar": reagendar };
  const document = { querySelector: () => barra, getElementById: id => elementos[id] };
  const window = { snwEsPrivilegiado: privilegiado, addEventListener() {}, scrollTo() {},
    snwCerrarAplazamiento() { cierresAplazamiento++; },
    snwCargarPacientes() { cargas.pacientes++; },
    ...(privilegiado ? { snwCargarReagendar() { cargas.reagendar++; } } : {}) };
  vm.runInNewContext(script, { document, window });
  assert.equal(cargas.pacientes, 1);
  assert.equal(cargas.reagendar, 0);
  assert.equal(reagendar.hidden, !privilegiado);
  reagendar.eventos.click();
  assert.equal(citas.hidden, !privilegiado);
  assert.equal(base.hidden, privilegiado);
  assert.equal(cargas.reagendar, privilegiado ? 1 : 0);
  if (privilegiado) {
    pacientes.eventos.click();
    assert.equal(base.hidden, false);
    assert.equal(citas.hidden, true);
    assert.equal(cargas.pacientes, 1);
    assert.equal(cierresAplazamiento, 1);
  }
}

probar(true);
probar(false);
console.log("Pestañas de Base de datos: OK");
