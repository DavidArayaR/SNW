const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const vm = require("node:vm");

const raiz = join(__dirname, "..");
const html = readFileSync(join(raiz, "frontend", "pacientes.html"), "utf8");
const script = readFileSync(join(raiz, "frontend", "js", "pacientes-tabs.js"), "utf8");
assert.match(html, /id="pacientes-tab-pacientes"[^>]*aria-controls="panelBaseDatos"/);
assert.match(html, /id="pacientes-tab-reagendar"[^>]*aria-controls="cardReagendar"[^>]*hidden/);
assert.match(html, /id="panelBaseDatos"[^>]*role="tabpanel"/);
assert.match(html, /id="cardReagendar"[^>]*role="tabpanel"[^>]*hidden/);

function probar(privilegiado) {
  const cargas = { pacientes: 0, reagendar: 0 };
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
  }
}

probar(true);
probar(false);
console.log("Pestañas de Base de datos: OK");
