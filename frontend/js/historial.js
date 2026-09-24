const API_HISTORIAL = "api/notificaciones/historial";

function authHeaders(extra = {}) {
  return { Authorization: "Bearer " + (localStorage.getItem("snw_token") || ""), ...extra };
}

if (!localStorage.getItem("snw_token")) location.replace("login.html");

let registros = [];
let filtro = "";
let ambienteDetalle = "produccion";
let especialidadDetalle = null; // especialidad del envío abierto (para sus mensajes)
let pacienteMsgActual = null; // { id, ambiente, interesado }

const $ = (sel) => document.querySelector(sel);

let especialidadesHist = [];
const selEspecialidadHist = $("#selEspecialidadHist");
const selBaseHist = $("#selBaseHist");
if (selBaseHist) {
  selBaseHist.value = localStorage.getItem("snw_base_historial") || "todos";
}

// Solo admin/dev eligen base de datos (dev/prod/todas). El usuario normal
// solo ve sus especialidades asignadas: se le oculta ese selector.
const ES_PRIV_HIST = !!window.snwEsPrivilegiado;
if (!ES_PRIV_HIST && selBaseHist) {
  selBaseHist.hidden = true;
  const lblBase = document.querySelector('label[for="selBaseHist"]');
  if (lblBase) lblBase.hidden = true;
}

function especialidadHistActual() {
  const v = selEspecialidadHist ? selEspecialidadHist.value : "";
  return v ? Number(v) : null;
}

async function cargarEspecialidadesHist() {
  if (!selEspecialidadHist) return;
  try {
    const r = await fetch("api/especialidades/mias", { headers: authHeaders(), cache: "no-store" });
    if (!r.ok) throw new Error();
    especialidadesHist = await r.json();
  } catch {
    especialidadesHist = [];
  }
  const guardada = localStorage.getItem("snw_esp_historial") || "";
  selEspecialidadHist.innerHTML =
    `<option value="">${ES_PRIV_HIST ? "Todas" : "Mis especialidades"}</option>` +
    especialidadesHist.map((e) => `<option value="${e.id}">${escaparHtml(e.nombre_visible)} (${escaparHtml(e.nombre_tabla_base)})</option>`).join("");
  if (guardada && especialidadesHist.some((e) => String(e.id) === guardada)) {
    selEspecialidadHist.value = guardada;
  } else {
    localStorage.removeItem("snw_esp_historial");
  }
  if (selEspecialidadHist) selEspecialidadHist.hidden = !especialidadesHist.length;
}

if (selEspecialidadHist) selEspecialidadHist.addEventListener("change", () => {
  const v = selEspecialidadHist.value;
  if (v) {
    localStorage.setItem("snw_esp_historial", v);
    // La especialidad trae su propia base: se vuelve a "Todas".
    if (selBaseHist) {
      selBaseHist.value = "todos";
      localStorage.removeItem("snw_base_historial");
    }
  } else {
    localStorage.removeItem("snw_esp_historial");
  }
  cargar();
});

if (selBaseHist) selBaseHist.addEventListener("change", () => {
  const v = selBaseHist.value;
  if (v && v !== "todos") {
    localStorage.setItem("snw_base_historial", v);
    // Una base concreta excluye especialidades: se limpia ese filtro.
    if (selEspecialidadHist) {
      selEspecialidadHist.value = "";
      localStorage.removeItem("snw_esp_historial");
    }
  } else {
    localStorage.removeItem("snw_base_historial");
  }
  cargar();
});

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
    const esp = especialidadHistActual();
    const base = (ES_PRIV_HIST && selBaseHist && selBaseHist.value) || "todos";
    const qs = `ambiente=${base}` + (esp ? `&especialidad_id=${esp}` : "");
    const [rh, rc] = await Promise.all([
      fetch(`${API_HISTORIAL}?${qs}`, { headers: authHeaders(), cache: "no-store" }),
      fetch(`api/configuracion?ambiente=produccion`, { headers: authHeaders(), cache: "no-store" }),
    ]);
    if (rh.status === 401 || rc.status === 401) { window.snwSesionExpirada(); return; }
    if (!rh.ok || !rc.ok) throw new Error();
    registros = await rh.json();
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
    tr.dataset.id = String(r.id);
    tr.dataset.amb = (r.base_datos ?? "").includes("prod") ? "produccion" : "desarrollo";
    tr.dataset.base = r.base_datos ?? "";
    tr.dataset.esp = r.especialidad_id != null ? String(r.especialidad_id) : "";
    const total = r.total_pacientes ?? 0;
    const enviados = r.enviados ?? 0;
    const fallidos = r.fallidos ?? 0;
    const invalidos = r.invalidos ?? 0;
    const estado = r.estado || "completado";
    const estadoLabel =
      { cancelado: "Cancelado", rechazado: "Rechazado" }[estado] || "Completado";
    const comentario = (r.comentario ?? "").trim();
    const notaRechazo = estado === "rechazado"
      ? `<div class="hist-rechazo">Rechazado por el supervisor${comentario ? `: «${escaparHtml(comentario)}»` : " (sin comentario)"}</div>`
      : "";
    tr.innerHTML =
      `<td class="campo-fecha">${escaparHtml(r.fecha)}</td>` +
      `<td><span class="badge badge--db">${escaparHtml(r.base_datos ?? "—")}</span></td>` +
      `<td>${escaparHtml(r.plantilla_nombre ?? r.plantilla_clave ?? "—")}${notaRechazo}</td>` +
      `<td><span class="estado-envio estado-envio--${escaparHtml(estado)}">${escaparHtml(estadoLabel)}</span></td>` +
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
  const tr = e.target.closest("tr[data-id]");
  if (!tr) return;
  const envioId = tr.dataset.id;
  const amb = tr.dataset.amb || "produccion";
  const envio = registros.find((r) => String(r.id) === envioId && ((r.base_datos ?? "").includes("prod") ? "produccion" : "desarrollo") === amb)
    || registros.find((r) => String(r.id) === envioId);
  ambienteDetalle = amb;
  especialidadDetalle = (envio && envio.especialidad_id != null) ? envio.especialidad_id : (tr.dataset.esp ? Number(tr.dataset.esp) : null);

  try {
    const r = await fetch(`api/notificaciones/historial/${envioId}/detalle?ambiente=${amb}`, {
      headers: authHeaders(), cache: "no-store"
    });
    if (r.status === 401) { window.snwSesionExpirada(); return; }
    if (!r.ok) throw new Error();
    const detalle = await r.json();
    abrirDetalle(envio, detalle);
  } catch {
    toast("Error al cargar el detalle.", "error");
  }
});

function abrirDetalle(envio, detalle) {
  const plantilla = envio?.plantilla_nombre ?? envio?.plantilla_clave ?? "";
  $("#detalleTitulo").textContent = plantilla
    ? `Pacientes del envío · ${plantilla}`
    : "Pacientes del envío";

  const body = $("#detalleBody");
  body.innerHTML = "";

  // Columna extra "Detalle" con el botón «Ver mensajes».
  const thMsgs = $("#thDetalleMensajes");
  if (thMsgs) thMsgs.hidden = false;
  const cols = 4;

  if ((envio?.estado || "") === "rechazado") {
    const c = (envio.comentario ?? "").trim();
    body.innerHTML =
      `<tr><td colspan="${cols}" class="hist-rechazo-detalle">` +
      `Envío rechazado por el supervisor. No se envió ningún mensaje.` +
      (c ? `<br><span>Comentario: «${escaparHtml(c)}»</span>` : "") +
      `</td></tr>`;
    $("#modalDetalle").hidden = false;
    return;
  }

  if (!detalle.length) {
    body.innerHTML = `<tr><td colspan="${cols}" style="text-align:center;color:#66757f;">Sin detalle individual registrado.</td></tr>`;
  } else {
    const pesoRespuesta = { respondio: 0, baja: 1, pendiente: 2 };
    const orden = [...detalle].sort(
      (x, y) => (pesoRespuesta[x.respuesta] ?? 3) - (pesoRespuesta[y.respuesta] ?? 3) || (x.id - y.id)
    );
    for (const d of orden) {
      const r = d.respuesta ?? "pendiente";
      const msg = (d.mensaje_respuesta ?? "").trim();
      const msgHtml = msg
        ? `<div class="hist-msg${d.respuesta_interes ? " hist-msg--interes" : ""}">«${escaparHtml(msg)}»</div>`
        : "";
      const marcaInteres = d.interesado
        ? `<span class="hist-tag-interes" title="Marcado como interesado">interesado</span>`
        : "";
      const celdaMsgs = d.paciente_id
        ? `<td class="hist-col-msgs"><button type="button" class="btn btn--sm btn--ghost" data-mensajes="${d.paciente_id}">Ver mensajes</button></td>`
        : `<td class="hist-col-msgs">—</td>`;
      const tr = document.createElement("tr");
      tr.innerHTML =
        `<td class="campo-nombre">${escaparHtml(d.nombre_paciente ?? "—")}${marcaInteres}</td>` +
        `<td><span class="respuesta-badge respuesta-${escaparHtml(r)}">${escaparHtml(respuestaLabel(r))}</span>${msgHtml}</td>` +
        `<td class="campo-fecha">${escaparHtml(d.fecha ?? "—")}</td>` +
        celdaMsgs;
      body.appendChild(tr);
    }
  }

  $("#modalDetalle").hidden = false;
}

function respuestaLabel(r) {
  return { pendiente: "Sin respuesta", respondio: "Respondió", baja: "Se dio de baja" }[r] ?? r;
}

$("#btnCerrarDetalle").addEventListener("click", () => ($("#modalDetalle").hidden = true));
$("#modalDetalle").addEventListener("click", (e) => { if (e.target === $("#modalDetalle")) $("#modalDetalle").hidden = true; });

/* ---------- Mensajes del paciente ---------- */

$("#detalleBody").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-mensajes]");
  if (btn) abrirMensajes(btn.dataset.mensajes);
});

async function abrirMensajes(pacienteId) {
  try {
    const qsEsp = especialidadDetalle != null ? `&especialidad_id=${especialidadDetalle}` : "";
    const r = await fetch(`api/pacientes/${pacienteId}/mensajes?ambiente=${ambienteDetalle}${qsEsp}`, {
      headers: authHeaders(), cache: "no-store",
    });
    if (r.status === 401) { window.snwSesionExpirada(); return; }
    if (!r.ok) throw new Error();
    const data = await r.json();
    renderMensajes(data);
  } catch {
    toast("No se pudieron cargar los mensajes del paciente.", "error");
  }
}

function renderMensajes(data) {
  const pac = data.paciente || {};
  pacienteMsgActual = { id: pac.id, ambiente: ambienteDetalle, interesado: !!pac.interesado };

  const nombre = [pac.nombre, pac.apellido].filter(Boolean).join(" ") || "Paciente";
  $("#mensajesTitulo").textContent = `Mensajes · ${nombre}`;

  const estado = pac.respuesta === "baja" ? "Se dio de baja"
    : pac.respuesta === "respondio" ? "Respondió" : "Sin respuesta";
  $("#msgPacienteCab").innerHTML =
    `<span>${escaparHtml(pac.telefono ?? "")}</span>` +
    `<span class="msg-cab__estado">${escaparHtml(estado)}</span>` +
    (pac.interesado ? `<span class="hist-tag-interes">interesado</span>` : "");

  const hilo = $("#msgHilo");
  const msgs = data.mensajes || [];
  if (!msgs.length) {
    hilo.innerHTML = `<p class="msg-vacio">El paciente todavía no ha escrito ningún mensaje.</p>`;
  } else {
    hilo.innerHTML = msgs.map((m) => {
      const texto = m.texto || (m.direccion === "saliente" ? `(plantilla: ${m.plantilla ?? "—"})` : "");
      const meta = `${m.direccion === "entrante" ? "Paciente" : "Sistema"} · ${escaparHtml(m.fecha)}` +
        (m.interes ? " · interés" : "") +
        (m.estado === "error" ? " · no entregado" : "");
      return `<div class="msg-burbuja msg-burbuja--${m.direccion}${m.interes ? " msg-burbuja--interes" : ""}">` +
        `<div class="msg-texto">${escaparHtml(texto)}</div>` +
        `<div class="msg-meta">${meta}</div></div>`;
    }).join("");
  }
  const estadoInteres = $("#msgInteresEstado");
  if (estadoInteres) {
    estadoInteres.querySelector("span").textContent =
      pac.interesado ? "Interés: sí (detectado automáticamente)" : "Interés: no";
    estadoInteres.classList.toggle("msg-interes-estado--si", !!pac.interesado);
  }

  $("#modalMensajes").hidden = false;
  // Ya visible: salta al final (mensajes más recientes). Se difiere para que
  // el navegador calcule la altura real del hilo.
  requestAnimationFrame(() => { hilo.scrollTop = hilo.scrollHeight; });
}

$("#btnCerrarMensajes").addEventListener("click", () => ($("#modalMensajes").hidden = true));
$("#modalMensajes").addEventListener("click", (e) => { if (e.target === $("#modalMensajes")) $("#modalMensajes").hidden = true; });

buscadorEl.addEventListener("input", () => {
  filtro = buscadorEl.value;
  render();
});

window.snwConCooldown($("#btnActualizar"), cargar);

let toastTimer;
function toast(msg, tipo = "ok") {
  clearTimeout(toastTimer);
  toastEl.textContent = msg;
  toastEl.className = `toast visible toast--${tipo}`;
  toastTimer = setTimeout(() => toastEl.classList.remove("visible"), 3200);
}

/* ---------- Panel "Registro de respuestas de call center" ---------- */
/* null si no se tiene el permiso `call_center_registro` (data-perm en el HTML) */
const panelCCLogEl = $("#panelCCRegistro");

async function cargarLogCC() {
  if (!panelCCLogEl) return;
  const body = $("#ccLogBody");
  const cont = $("#ccLogContadores");
  body.innerHTML = `<tr><td colspan="4" style="text-align:center;color:var(--texto-suave);">Cargando…</td></tr>`;
  try {
    const r = await fetch("api/call-center/log", { headers: authHeaders(), cache: "no-store" });
    if (r.status === 401) { window.snwSesionExpirada(); return; }
    if (!r.ok) throw new Error();
    const data = await r.json();
    const c = data.contadores || {};
    cont.innerHTML = Object.keys(c).length
      ? "Usos por número: " + Object.entries(c).map(([n, u]) => `<strong>+${escaparHtml(n)}</strong> ${u}`).join(" · ")
      : "Todavía no se ha usado ningún número.";
    const filas = data.entradas || [];
    body.innerHTML = filas.length
      ? filas.map((f) => {
          const est = f.estado === "error"
            ? `<span class="respuesta-badge respuesta-baja">error</span>`
            : `<span class="respuesta-badge respuesta-respondio">enviado</span>`;
          return `<tr>` +
            `<td class="campo-fecha">${escaparHtml(f.fecha)}</td>` +
            `<td class="campo-nombre">${escaparHtml(f.nombre_paciente ?? "—")}<br><span class="campo-tel">${escaparHtml(f.numero_paciente ?? "")}</span></td>` +
            `<td class="campo-tel">+${escaparHtml(f.numero_call_center)}</td>` +
            `<td>${est}${f.descripcion_error ? `<div class="hist-msg">${escaparHtml(f.descripcion_error)}</div>` : ""}</td>` +
            `</tr>`;
        }).join("")
      : `<tr><td colspan="4" style="text-align:center;color:var(--texto-suave);">Todavía no se ha enviado ninguna respuesta de call center.</td></tr>`;
  } catch {
    body.innerHTML = `<tr><td colspan="4" style="text-align:center;color:var(--danger-fg);">No se pudo cargar el registro.</td></tr>`;
  }
}

if (panelCCLogEl) {
  window.snwConCooldown($("#btnActualizarCCLog"), cargarLogCC);
}

cargarEspecialidadesHist();
cargar();
cargarLogCC();
