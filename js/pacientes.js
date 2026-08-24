const API_URL = "api/pacientes.php";

let pacientes = [];
let filtro = "";
let filtroEstado = "todos";

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
  const visibles = pacientes.filter(
    (p) =>
      (filtroEstado === "todos" || p.estado === filtroEstado) &&
      (!q ||
        [p.nombre, p.telefono, p.info_extra]
          .filter(Boolean)
          .some((v) => v.toLowerCase().includes(q)))
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

  const activo = (estado) => (filtroEstado === estado ? " activo" : "");
  statsEl.innerHTML =
    `<button type="button" class="stat stat--total${activo("todos")}" data-estado="todos">Total <strong>${conteo.total}</strong></button>` +
    `<button type="button" class="stat stat--pendiente${activo("pendiente")}" data-estado="pendiente">Pendientes <strong>${conteo.pendiente}</strong></button>` +
    `<button type="button" class="stat stat--enviado${activo("enviado")}" data-estado="enviado">Enviados <strong>${conteo.enviado}</strong></button>` +
    `<button type="button" class="stat stat--error${activo("error")}" data-estado="error">Errores <strong>${conteo.error}</strong></button>`;

  document.querySelectorAll(".filtro-btn").forEach((b) => {
    b.classList.toggle("activo", b.dataset.estado === filtroEstado);
  });
}

function setFiltro(estado) {
  filtroEstado = estado;
  render();
}

buscadorEl.addEventListener("input", () => {
  filtro = buscadorEl.value;
  render();
});

document.querySelectorAll(".filtro-btn").forEach((btn) => {
  btn.addEventListener("click", () => setFiltro(btn.dataset.estado));
});

statsEl.addEventListener("click", (e) => {
  const btn = e.target.closest(".stat");
  if (!btn) return;
  setFiltro(btn.dataset.estado);
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
