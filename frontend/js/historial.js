const API_HISTORIAL = "api/notificaciones/historial";
const ESTADOS = ["todos", "enviado", "error", "numero_invalido"];

let registros = [];
let filtro = "";
let filtroEstado = "todos";

const $ = (sel) => document.querySelector(sel);

const tbodyEl = $("#tablaHistorial tbody");
const vacioEl = $("#tablaVacia");
const buscadorEl = $("#buscador");
const statsEl = $("#stats");
const contadorEl = $("#contador");
const toastEl = $("#toast");

function escaparHtml(texto) {
  const div = document.createElement("div");
  div.textContent = texto ?? "";
  return div.innerHTML;
}

async function cargar() {
  try {
    const [rh, rc] = await Promise.all([
      fetch(API_HISTORIAL, { cache: "no-store" }),
      fetch("api/configuracion", { cache: "no-store" }),
    ]);
    if (!rh.ok || !rc.ok) throw new Error();
    registros = (await rh.json()).map((r) => ({
      ...r,
      estado_envio: r.estado_envio || "error",
      nombre_paciente: r.nombre_paciente || "(paciente eliminado)",
      descripcion_error: r.descripcion_error || "",
    }));
    const config = await rc.json();
    $("#badgeEntorno").textContent =
      `Ambiente: ${config.entorno === "produccion" ? "Producción" : "Desarrollo"}`;
    render();
  } catch {
    activarDemo();
  }
}

function activarDemo() {
  if (document.getElementById("cintaDemo")) return;
  const cinta = document.createElement("div");
  cinta.id = "cintaDemo";
  cinta.className = "cinta-demo";
  cinta.textContent = "Modo demostración — sin servidor conectado; los datos son de ejemplo.";
  document.body.prepend(cinta);
  $("#badgeEntorno").textContent = "Modo demo";
  registros = [
    { id: 3, paciente_id: 7, nombre_paciente: "David Araya", numero_telefono: "+56993921740", plantilla_clave: "recordatorio_cita", estado_envio: "enviado", descripcion_error: "", fecha: "24-08-2026 15:51", mensaje: "Hola David, le recordamos su cita médica. Control Médico 15:30 hrs. Por favor confirme su asistencia respondiendo este mensaje." },
    { id: 2, paciente_id: 9, nombre_paciente: "Pedro Soto", numero_telefono: "+56988887777", plantilla_clave: "recordatorio_cita", estado_envio: "error", descripcion_error: "Número no autorizado en entorno desarrollo", fecha: "24-08-2026 15:51", mensaje: "" },
    { id: 1, paciente_id: 8, nombre_paciente: "María López", numero_telefono: "912345678", plantilla_clave: "resultado_disponible", estado_envio: "numero_invalido", descripcion_error: "Formato de teléfono inválido: '912345678'", fecha: "23-08-2026 11:02", mensaje: "" },
  ];
  render();
}

function render() {
  const q = filtro.trim().toLowerCase();
  const visibles = registros.filter(
    (r) =>
      (filtroEstado === "todos" || r.estado_envio === filtroEstado) &&
      (!q ||
        [r.nombre_paciente, r.numero_telefono, r.plantilla_clave, r.descripcion_error]
          .filter(Boolean)
          .some((v) => v.toLowerCase().includes(q)))
  );

  tbodyEl.innerHTML = "";

  for (const r of visibles) {
    const tr = document.createElement("tr");
    tr.dataset.id = r.id;
    tr.innerHTML =
      `<td class="campo-fecha">${escaparHtml(r.fecha)}</td>` +
      `<td class="campo-nombre">${escaparHtml(r.nombre_paciente ?? "—")}</td>` +
      `<td class="campo-tel">${escaparHtml(r.numero_telefono ?? "—")}</td>` +
      `<td>${escaparHtml(r.plantilla_clave ?? "—")}</td>` +
      `<td class="campo-mensaje" title="${escaparHtml(r.mensaje ?? "")}">${escaparHtml(r.mensaje ?? "—")}</td>` +
      `<td><span class="estado-badge estado-${escaparHtml(r.estado_envio)}">${escaparHtml(rotular(r.estado_envio))}</span></td>` +
      `<td class="campo-info">${escaparHtml(r.descripcion_error ?? "")}</td>`;
    tbodyEl.appendChild(tr);
  }

  vacioEl.hidden = visibles.length > 0;
  contadorEl.textContent = `${visibles.length} registro${visibles.length === 1 ? "" : "s"}`;

  const conteo = {
    total: registros.length,
    enviado: 0,
    error: 0,
    numero_invalido: 0,
  };
  for (const r of registros) {
    if (conteo[r.estado_envio] !== undefined) conteo[r.estado_envio]++;
  }

  const activo = (estado) => (filtroEstado === estado ? " activo" : "");
  statsEl.innerHTML =
    `<button type="button" class="stat stat--total${activo("todos")}" data-estado="todos">Total <strong>${conteo.total}</strong></button>` +
    `<button type="button" class="stat stat--enviado${activo("enviado")}" data-estado="enviado">Enviados <strong>${conteo.enviado}</strong></button>` +
    `<button type="button" class="stat stat--error${activo("error")}" data-estado="error">Errores <strong>${conteo.error}</strong></button>` +
    `<button type="button" class="stat stat--invalido${activo("numero_invalido")}" data-estado="numero_invalido">Nº inválidos <strong>${conteo.numero_invalido}</strong></button>`;

  document.querySelectorAll(".filtro-btn").forEach((b) => {
    b.classList.toggle("activo", b.dataset.estado === filtroEstado);
  });
}

function rotular(estado) {
  return { enviado: "enviado", error: "error", numero_invalido: "nº inválido" }[estado] ?? estado;
}

function setFiltro(estado) {
  filtroEstado = estado;
  render();
}

buscadorEl.addEventListener("input", () => {
  filtro = buscadorEl.value;
  render();
});

statsEl.addEventListener("click", (e) => {
  const btn = e.target.closest(".stat");
  if (!btn || !ESTADOS.includes(btn.dataset.estado)) return;
  setFiltro(btn.dataset.estado);
});

$("#btnActualizar").addEventListener("click", cargar);

const modalEl = $("#modalDetalle");

tbodyEl.addEventListener("click", (e) => {
  const tr = e.target.closest("tr");
  if (!tr || !tbodyEl.contains(tr)) return;
  const registro = registros.find((r) => r.id === Number(tr.dataset.id));
  if (registro) abrirDetalle(registro);
});

function abrirDetalle(r) {
  const campos = [
    ["Fecha", r.fecha],
    ["Paciente", r.nombre_paciente ?? "—"],
    ["ID paciente", r.paciente_id ?? "—"],
    ["Teléfono", r.numero_telefono ?? "—"],
    ["Plantilla", r.plantilla_clave ?? "—"],
    ["Estado", rotular(r.estado_envio)],
  ];

  $("#detalleCampos").innerHTML = campos
    .map(
      ([k, v]) =>
        `<div class="detalle-fila"><dt>${escaparHtml(k)}</dt><dd>${escaparHtml(v)}</dd></div>`
    )
    .join("");

  $("#detalleMensaje").textContent = r.mensaje || "(sin contenido registrado)";

  const errBox = $("#detalleError");
  errBox.hidden = !r.descripcion_error;
  errBox.textContent = r.descripcion_error ? `Error: ${r.descripcion_error}` : "";

  modalEl.hidden = false;
}

$("#btnCerrarDetalle").addEventListener("click", () => (modalEl.hidden = true));
modalEl.addEventListener("click", (e) => {
  if (e.target === modalEl) modalEl.hidden = true;
});

let toastTimer;
function toast(msg, tipo = "ok") {
  clearTimeout(toastTimer);
  toastEl.textContent = msg;
  toastEl.className = `toast visible toast--${tipo}`;
  toastTimer = setTimeout(() => toastEl.classList.remove("visible"), 3200);
}

cargar();
