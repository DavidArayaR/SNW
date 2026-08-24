const API_URL = "api/pacientes.php";

let pacientes = [];
let filtro = "";

const $ = (sel) => document.querySelector(sel);

const tbodyEl = $("#tablaPacientes tbody");
const vacioEl = $("#tablaVacia");
const buscadorEl = $("#buscador");
const statsEl = $("#stats");
const toastEl = $("#toast");

function escaparHtml(texto) {
  const div = document.createElement("div");
  div.textContent = texto ?? "";
  return div.innerHTML;
}

async function cargar() {
  try {
    const res = await fetch(API_URL, { cache: "no-store" });
    if (!res.ok) throw new Error(res.status);
    pacientes = await res.json();
    render();
  } catch {
    toast("No se pudo conectar con la base de datos.", "error");
  }
}

function render() {
  const q = filtro.trim().toLowerCase();
  const visibles = !q
    ? pacientes
    : pacientes.filter((p) =>
        [p.nombre,p.apellido, p.telefono, p.info_extra]
          .filter(Boolean)
          .some((v) => v.toLowerCase().includes(q))
      );

  tbodyEl.innerHTML = "";

  for (const p of visibles) {
    const tr = document.createElement("tr");
    tr.innerHTML =
      `<td class="campo-id">${p.id}</td>` +
      `<td class="campo-nombre">${escaparHtml(p.nombre)}</td>` +
      `<td class="campo-apellido">${escaparHtml(p.apellido)}</td>` +
      `<td class="campo-tel">${escaparHtml(p.telefono)}</td>` +
      `<td><span class="estado-badge estado-${escaparHtml(p.estado)}">${escaparHtml(p.estado)}</span></td>` +
      `<td class="campo-info">${escaparHtml(p.info_extra ?? "—")}</td>` +
      `<td class="campo-fecha">${escaparHtml(p.actualizado)}</td>`;
    tbodyEl.appendChild(tr);
  }

  vacioEl.hidden = visibles.length > 0;

  const conteo = { total: pacientes.length, pendiente: 0, enviado: 0, error: 0 };
  for (const p of pacientes) {
    if (conteo[p.estado] !== undefined) conteo[p.estado]++;
  }

  statsEl.innerHTML =
    `<span class="stat stat--total">Total <strong>${conteo.total}</strong></span>` +
    `<span class="stat stat--pendiente">Pendientes <strong>${conteo.pendiente}</strong></span>` +
    `<span class="stat stat--enviado">Enviados <strong>${conteo.enviado}</strong></span>` +
    `<span class="stat stat--error">Errores <strong>${conteo.error}</strong></span>`;
}

buscadorEl.addEventListener("input", () => {
  filtro = buscadorEl.value;
  render();
});

$("#btnActualizar").addEventListener("click", cargar);

let toastTimer;
function toast(msg, tipo = "ok") {
  clearTimeout(toastTimer);
  toastEl.textContent = msg;
  toastEl.className = `toast visible toast--${tipo}`;
  toastTimer = setTimeout(() => toastEl.classList.remove("visible"), 2800);
}

cargar();
