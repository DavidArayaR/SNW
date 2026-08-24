const API_PACIENTES = "api/pacientes";
const API_PLANTILLAS = "api/plantillas";
const API_ENVIAR = "api/notificaciones/enviar";

let pacientes = [];
let plantillas = [];
let config = null;
let filtro = "";
let filtroEstado = "todos";
let seleccionados = new Set();
let plantillaId = null;
let timerPolling = null;

const $ = (sel) => document.querySelector(sel);

const tbodyEl = $("#tablaPacientes tbody");
const vacioEl = $("#tablaVacia");
const buscadorEl = $("#buscador");
const statsEl = $("#stats");
const chkTodos = $("#chkTodos");
const contadorSel = $("#contadorSel");
const btnIniciar = $("#btnIniciar");
const modalEl = $("#modalEnvio");
const toastEl = $("#toast");

function escaparHtml(texto) {
  const div = document.createElement("div");
  div.textContent = texto ?? "";
  return div.innerHTML;
}

async function cargar() {
  try {
    const [rp, rt, rc] = await Promise.all([
      fetch(API_PACIENTES, { cache: "no-store" }),
      fetch(API_PLANTILLAS, { cache: "no-store" }),
      fetch("api/configuracion", { cache: "no-store" }),
    ]);
    if (!rp.ok || !rt.ok || !rc.ok) throw new Error();

    pacientes = await rp.json();
    plantillas = await rt.json();
    config = await rc.json();

    $("#badgeEntorno").textContent =
      `Ambiente: ${config.entorno === "produccion" ? "Producción" : "Desarrollo"}`;

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
        [p.nombre, p.apellido, p.telefono, p.info_extra]
          .filter(Boolean)
          .some((v) => v.toLowerCase().includes(q)))
  );

  tbodyEl.innerHTML = "";

  for (const p of visibles) {
    const nombreCompleto = [p.nombre, p.apellido].filter(Boolean).join(" ");
    const tr = document.createElement("tr");
    tr.innerHTML =
      `<td class="col-check"><input type="checkbox" data-id="${p.id}" ${seleccionados.has(p.id) ? "checked" : ""}></td>` +
      `<td class="campo-id">${p.id}</td>` +
      `<td class="campo-nombre">${escaparHtml(nombreCompleto)}</td>` +
      `<td class="campo-tel">${escaparHtml(p.telefono)}</td>` +
      `<td><span class="estado-badge estado-${escaparHtml(p.estado)}">${escaparHtml(p.estado)}</span></td>` +
      `<td class="campo-info">${escaparHtml(p.info_extra ?? "—")}</td>` +
      `<td class="campo-fecha">${escaparHtml(p.actualizado)}</td>`;
    tr.querySelector("input").addEventListener("change", (e) => {
      e.target.checked ? seleccionados.add(p.id) : seleccionados.delete(p.id);
      refrescarSeleccion();
    });
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

  refrescarSeleccion(visibles);
}

function refrescarSeleccion(visibles = null) {
  visibles = visibles ?? pacientes.filter(
    (p) => filtroEstado === "todos" || p.estado === filtroEstado
  );

  contadorSel.textContent =
    seleccionados.size > 0 ? `${seleccionados.size} seleccionado${seleccionados.size === 1 ? "" : "s"}` : "";

  btnIniciar.disabled = seleccionados.size === 0;
  btnIniciar.textContent =
    seleccionados.size > 0 ? `Iniciar envío (${seleccionados.size})` : "Iniciar envío";

  const idsVisibles = visibles.map((p) => p.id);
  chkTodos.checked =
    idsVisibles.length > 0 && idsVisibles.every((id) => seleccionados.has(id));
}

chkTodos.addEventListener("change", () => {
  const q = filtro.trim().toLowerCase();
  const visibles = pacientes.filter(
    (p) =>
      (filtroEstado === "todos" || p.estado === filtroEstado) &&
      (!q ||
        [p.nombre, p.apellido, p.telefono, p.info_extra]
          .filter(Boolean)
          .some((v) => v.toLowerCase().includes(q)))
  );
  for (const p of visibles) {
    chkTodos.checked ? seleccionados.add(p.id) : seleccionados.delete(p.id);
  }
  render();
});

tbodyEl.addEventListener("click", (e) => {
  if (e.target.tagName === "INPUT") return;
  const tr = e.target.closest("tr");
  if (!tr || !tbodyEl.contains(tr)) return;
  const chk = tr.querySelector("input[type=checkbox]");
  if (!chk) return;
  chk.checked = !chk.checked;
  chk.checked ? seleccionados.add(Number(chk.dataset.id)) : seleccionados.delete(Number(chk.dataset.id));
  refrescarSeleccion();
});

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

function setFiltro(estado) {
  filtroEstado = estado;
  render();
}

btnIniciar.addEventListener("click", () => {
  if (!seleccionados.size) return;
  plantillaId = null;
  abrirModal();
});

function abrirModal() {
  renderPlantillasModal();

  $("#resumenLinea").textContent =
    `${seleccionados.size} paciente${seleccionados.size === 1 ? "" : "s"} · método: ${config?.metodo_envio ?? "—"}`;

  const dev = config?.entorno === "desarrollo";
  avisoDevEl.hidden = !dev;
  if (dev) {
    const nums = (config?.numeros_prueba ?? []).join(", ") || "ninguno";
    avisoDevEl.textContent =
      `Entorno desarrollo: solo se enviará a los números de prueba autorizados (${nums}). El resto será descartado.`;
  }

  $("#faseConfig").hidden = false;
  $("#faseProgreso").hidden = true;
  $("#btnLanzarEnvio").hidden = true;
  $("#btnCerrarModal").hidden = true;
  $("#listaRechazados").innerHTML = "";
  $("#listaRechazados").hidden = true;
  $("#barraFill").style.width = "0%";

  modalEl.hidden = false;
}

function renderPlantillasModal() {
  const cont = $("#listaPlantillasModal");
  cont.innerHTML = "";

  for (const t of plantillas) {
    const label = document.createElement("label");
    label.className = "tpl-card" + (t.id === plantillaId ? " seleccionada" : "");
    label.innerHTML =
      `<input type="radio" name="plantillaModal" value="${t.id}" ${t.id === plantillaId ? "checked" : ""}>` +
      `<span class="tpl-card__nombre">${escaparHtml(t.nombre)}</span>` +
      `<span class="tpl-card__texto">${escaparHtml(t.texto.split("\n")[0])}</span>`;
    label.querySelector("input").addEventListener("change", () => {
      plantillaId = t.id;
      cont.querySelectorAll(".tpl-card").forEach((c) => c.classList.remove("seleccionada"));
      label.classList.add("seleccionada");
      $("#btnLanzarEnvio").hidden = false;
    });
    cont.appendChild(label);
  }
}

const avisoDevEl = $("#avisoDev");

$("#btnCancelarEnvio").addEventListener("click", () => {
  clearInterval(timerPolling);
  modalEl.hidden = true;
});
modalEl.addEventListener("click", (e) => {
  if (e.target === modalEl) {
    clearInterval(timerPolling);
    modalEl.hidden = true;
  }
});
$("#btnCerrarModal").addEventListener("click", () => {
  clearInterval(timerPolling);
  seleccionados.clear();
  plantillaId = null;
  modalEl.hidden = true;
  render();
});

$("#btnLanzarEnvio").addEventListener("click", async () => {
  $("#btnLanzarEnvio").hidden = true;

  try {
    const res = await fetch(API_ENVIAR, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pacientes: [...seleccionados], plantilla_id: plantillaId }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail ?? `Error ${res.status}`);

    pintarRechazados(data.rechazados ?? []);

    if (!data.iniciado) {
      toast("Ningún destinatario válido. Envío no iniciado.", "error");
      return;
    }

    $("#faseConfig").hidden = true;
    $("#faseProgreso").hidden = false;
    seguirProgreso(data.job_id, data.total);
  } catch (err) {
    toast(`Error al iniciar el envío: ${err.message}`, "error");
    $("#btnLanzarEnvio").hidden = false;
  }
});

function pintarRechazados(rechazados) {
  const ul = $("#listaRechazados");
  ul.innerHTML = "";
  for (const r of rechazados) {
    const li = document.createElement("li");
    li.textContent = `${r.nombre} (${r.telefono || "sin teléfono"}): ${r.motivo}`;
    ul.appendChild(li);
  }
  ul.hidden = rechazados.length === 0;
}

function seguirProgreso(jobId, total) {
  clearInterval(timerPolling);
  timerPolling = setInterval(async () => {
    try {
      const res = await fetch(`api/notificaciones/jobs/${jobId}`, { cache: "no-store" });
      if (!res.ok) throw new Error();
      const job = await res.json();

      const hechos = job.enviados + job.fallidos;
      const porcentaje = total ? Math.round((hechos / total) * 100) : 100;
      $("#barraFill").style.width = `${porcentaje}%`;
      $("#progresoNumeros").textContent = `${hechos} / ${total}`;
      $("#progresoTexto").textContent =
        job.estado === "completado"
          ? "Envío finalizado."
          : job.actual
            ? `Enviando a ${job.actual}...`
            : "Enviando...";

      if (job.estado === "completado" || job.estado === "error") {
        clearInterval(timerPolling);
        const msg = job.estado === "error"
          ? `Error del canal: ${job.detalle}`
          : `Envío completado: ${job.enviados} enviado(s), ${job.fallidos} fallido(s).`;
        finalizar(msg, job.estado === "error");
        cargar();
      }
    } catch {
      clearInterval(timerPolling);
      finalizar("Se perdió la conexión con el servidor.", true);
    }
  }, 700);
}

function finalizar(mensaje, esError) {
  $("#progresoTexto").textContent = mensaje;
  toast(mensaje, esError ? "error" : "ok");
}

let toastTimer;
function toast(msg, tipo = "ok") {
  clearTimeout(toastTimer);
  toastEl.textContent = msg;
  toastEl.className = `toast visible toast--${tipo}`;
  toastTimer = setTimeout(() => toastEl.classList.remove("visible"), 3200);
}

cargar();
