const API_HISTORIAL = "api/notificaciones/historial";

function authHeaders(extra = {}) {
  return { Authorization: "Bearer " + (localStorage.getItem("snw_token") || ""), ...extra };
}

if (!localStorage.getItem("snw_token")) location.replace("login.html");

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
      fetch(`${API_HISTORIAL}?ambiente=todos`, { headers: authHeaders(), cache: "no-store" }),
      fetch(`api/configuracion?ambiente=produccion`, { headers: authHeaders(), cache: "no-store" }),
    ]);
    if (rh.status === 401 || rc.status === 401) { window.snwSalir(); return; }
    if (!rh.ok || !rc.ok) throw new Error();
    registros = await rh.json();
    $("#badgeEntorno").textContent = "Todas las bases de datos";
    render();
  } catch {
    toast("Error al conectar con el servidor.", "error");
  }
}

function render() {
  const q = filtro.trim().toLowerCase();
  const visibles = registros.filter(
    (r) => !q ||
      [r.base_datos, r.plantilla_clave, r.plantilla_nombre]
        .filter(Boolean)
        .some((v) => v.toLowerCase().includes(q))
  );

  tbodyEl.innerHTML = "";

  for (const r of visibles) {
    const tr = document.createElement("tr");
    tr.dataset.id = r.id;
    const total = r.total_pacientes ?? 0;
    const enviados = r.enviados ?? 0;
    const fallidos = r.fallidos ?? 0;
    const invalidos = r.invalidos ?? 0;
    tr.innerHTML =
      `<td class="campo-fecha">${escaparHtml(r.fecha)}</td>` +
      `<td><span class="badge badge--db">${escaparHtml(r.base_datos ?? "—")}</span></td>` +
      `<td>${escaparHtml(r.plantilla_nombre ?? r.plantilla_clave ?? "—")}</td>` +
      `<td class="campo-num">${total}</td>` +
      `<td class="campo-num campo-num--ok">${enviados}</td>` +
      `<td class="campo-num campo-num--error">${fallidos}</td>` +
      `<td class="campo-num campo-num--invalido">${invalidos}</td>` +
      `<td><button class="btn btn--sm btn--ghost" data-detalle="${r.id}">Ver detalle</button></td>`;
    tbodyEl.appendChild(tr);
  }

  vacioEl.hidden = visibles.length > 0;
  contadorEl.textContent = `${visibles.length} envío${visibles.length === 1 ? "" : "s"}`;

  const totalEnvios = registros.length;
  statsEl.innerHTML =
    `<button type="button" class="stat stat--total activo" data-estado="todos">Total envíos <strong>${totalEnvios}</strong></button>`;
}

tbodyEl.addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-detalle]");
  if (!btn) return;
  const envioId = btn.dataset.detalle;
  const envio = registros.find((r) => r.id === Number(envioId));
  const amb = (envio?.base_datos ?? "").includes("prod") ? "produccion" : "desarrollo";

  try {
    const r = await fetch(`api/notificaciones/historial/${envioId}/detalle?ambiente=${amb}`, {
      headers: authHeaders(), cache: "no-store"
    });
    if (r.status === 401) { window.snwSalir(); return; }
    if (!r.ok) throw new Error();
    const detalle = await r.json();
    abrirDetalle(envioId, detalle);
  } catch {
    toast("Error al cargar el detalle.", "error");
  }
});

function abrirDetalle(envioId, detalle) {
  const envio = registros.find((r) => r.id === Number(envioId));
  const campos = [
    ["Fecha", envio?.fecha ?? "—"],
    ["Base de datos", envio?.base_datos ?? "—"],
    ["Plantilla", envio?.plantilla_nombre ?? envio?.plantilla_clave ?? "—"],
    ["Total pacientes", envio?.total_pacientes ?? "—"],
    ["Enviados", envio?.enviados ?? 0],
    ["Fallidos", envio?.fallidos ?? 0],
    ["Inválidos", envio?.invalidos ?? 0],
  ];

  $("#detalleCampos").innerHTML = campos
    .map(([k, v]) => `<div class="detalle-fila"><dt>${escaparHtml(k)}</dt><dd>${escaparHtml(String(v))}</dd></div>`)
    .join("");

  const body = $("#detalleBody");
  body.innerHTML = "";
  if (!detalle.length) {
    body.innerHTML = '<tr><td colspan="5" style="text-align:center;color:#66757f;">Sin detalle individual registrado.</td></tr>';
  } else {
    for (const d of detalle) {
      const tr = document.createElement("tr");
      tr.innerHTML =
        `<td class="campo-nombre">${escaparHtml(d.nombre_paciente ?? "—")}</td>` +
        `<td class="campo-tel">${escaparHtml(d.numero_telefono ?? "—")}</td>` +
        `<td><span class="estado-badge estado-${escaparHtml(d.estado_envio)}">${escaparHtml(d.estado_envio)}</span></td>` +
        `<td><span class="respuesta-badge respuesta-${escaparHtml(d.respuesta ?? 'pendiente')}">${escaparHtml(respuestaLabel(d.respuesta))}</span></td>` +
        `<td class="campo-info">${escaparHtml(d.descripcion_error ?? "")}</td>`;
      body.appendChild(tr);
    }
  }

  $("#modalDetalle").hidden = false;
}

function respuestaLabel(r) {
  return { pendiente: "Sin respuesta", click: "Hizo click", respondio: "Respondió", baja: "Se dio de baja" }[r] ?? r;
}

$("#btnCerrarDetalle").addEventListener("click", () => ($("#modalDetalle").hidden = true));
$("#modalDetalle").addEventListener("click", (e) => { if (e.target === $("#modalDetalle")) $("#modalDetalle").hidden = true; });

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
  toastTimer = setTimeout(() => toastEl.classList.remove("visible"), 3200);
}

cargar();
