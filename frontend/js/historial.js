const API_HISTORIAL = "api/notificaciones/historial";

function authHeaders(extra = {}) {
  return { Authorization: "Bearer " + (localStorage.getItem("snw_token") || ""), ...extra };
}

if (!localStorage.getItem("snw_token")) location.replace("login.html");

let registros = [];
let filtro = "";
let paginaHist = 1;
let pageSizeHist = Math.min(100, Math.max(10, Number(localStorage.getItem("snw_page_size_hist")) || 10));

const $ = (sel) => document.querySelector(sel);

/* ---------- Envíos en progreso (manual + programados activos) ---------- */
let firmaProgreso = null; // firma de la última foto (origen:id:estado) para detectar términos
const ESTADO_PROG_LABEL = {
  en_proceso: "En proceso", pausado: "Pausado", pendiente: "Pendiente",
  aprobado: "Aprobado", enviando: "Enviando", enviado: "Enviado",
  error: "Error", cancelado: "Cancelado", rechazado: "Rechazado",
};

function progEnProgresoHtml(it) {
  const hechos = (it.enviados || 0) + (it.fallidos || 0);
  const total = it.total || 0;
  const pct = total > 0 ? Math.min(100, Math.round((hechos / total) * 100)) : 0;
  const barra = total > 0
    ? `<div class="progreso-info"><span>${hechos}/${total}</span>` +
      `<span class="progreso-numeros">${pct}%</span></div>` +
      `<div class="barra"><div class="barra__fill" style="width:${pct}%"></div></div>`
    : (it.programado_para ? `<div class="prog-item__meta">Programado para ${escaparHtml(it.programado_para)}</div>` : "");
  const cancelaJob = it.job_id && it.puede_cancelar;
  const cancelaProg = !it.job_id && it.origen === "programado" && it.puede_cancelar && it.id != null;
  return `<div class="prog-item" data-prog-item="${it.origen}-${it.id ?? it.job_id}">` +
    `<div class="prog-item__cab">` +
      `<span class="prog-origen prog-origen--${it.origen}">${it.origen === "manual" ? "Manual" : "Programado"}</span>` +
      `<span class="prog-estado prog-estado--${it.estado}">${ESTADO_PROG_LABEL[it.estado] || it.estado}</span>` +
      `<span>${escaparHtml(it.plantilla || "—")}</span></div>` +
    `<div class="prog-item__meta">` +
      (it.quien_envio ? `por ${escaparHtml(it.quien_envio)}` : "") +
      (it.quien_aprobo ? ` · aprobado por ${escaparHtml(it.quien_aprobo)}` : "") +
      (it.area ? ` · ${escaparHtml(it.area)}` : (it.base ? ` · ${escaparHtml(it.base)}` : "")) +
    `</div>` + barra +
    ((cancelaJob || cancelaProg)
      ? `<div class="prog-item__acciones">` +
        (cancelaJob
          ? `<button type="button" class="btn btn--sm btn--danger-ghost" data-cancel-job="${escaparHtml(it.job_id)}">Cancelar envío</button>`
          : `<button type="button" class="btn btn--sm btn--danger-ghost" data-cancel-prog="${it.id}">Cancelar envío</button>`) +
        `</div>`
      : "") +
    `</div>`;
}

async function cancelarEnProgreso(url, btn) {
  btn.disabled = true;
  try {
    const r = await fetch(url, { method: "POST", headers: authHeaders(), cache: "no-store" });
    const data = await r.json().catch(() => ({}));
    if (r.status === 401) { window.snwSesionExpirada(); return; }
    if (!r.ok) throw new Error(data.detail || "No se pudo cancelar.");
    toast("Envío cancelado.", "ok");
    cargarEnProgreso();
    cargar();
  } catch (err) {
    console.error("[historial.js cancelarEnProgreso()]", err);
    toast(err.message || "No se pudo cancelar.", "error");
    btn.disabled = false;
  }
}

function firmaItemProgreso(it) {
  return `${it.origen}:${it.id ?? it.job_id}:${it.estado}:${it.enviados || 0}:${it.fallidos || 0}:${it.total || 0}`;
}

function asegurarContenedorProgreso(lista) {
  let cont = lista.querySelector(":scope > .prog-lista-scroll");
  if (!cont) {
    lista.innerHTML = `<div class="prog-lista-scroll"></div>`;
    cont = lista.querySelector(":scope > .prog-lista-scroll");
  }
  const vacio = lista.querySelector(":scope > .prog-vacio");
  if (vacio) vacio.remove();
  return cont;
}

function mostrarVacioProgreso(lista) {
  if (!lista.querySelector(":scope > .prog-vacio")) {
    lista.innerHTML =
      `<div class="prog-vacio"><i class="fa-solid fa-paper-plane"></i><p>Sin envíos en progreso.</p></div>`;
  }
}

// Cadencia del sondeo de «Envíos en progreso»: cada 3 s mientras hay algo vivo
// (para ver el avance al instante) y un latido lento cuando la lista está
// vacía, que es lo único que detecta un envío iniciado en otro equipo.
const INTERVALO_PROGRESO_VIVO = 3000;
const INTERVALO_PROGRESO_LATIDO = 60000;
let timerProgreso = null;
let hayProgresoVivo = false;
let cargandoEnProgreso = false;

function programarProgreso() {
  if (timerProgreso) clearInterval(timerProgreso);
  const ms = hayProgresoVivo ? INTERVALO_PROGRESO_VIVO : INTERVALO_PROGRESO_LATIDO;
  timerProgreso = setInterval(() => {
    if (!document.hidden) cargarEnProgreso();
  }, ms);
}

async function cargarEnProgreso(mostrarFeedback = false) {
  const lista = $("#listaEnProgreso");
  if (!lista) return;
  if (cargandoEnProgreso) return; // no apilar sondeos si uno quedó colgado
  cargandoEnProgreso = true;
  let items;
  try {
    const r = await fetch("api/notificaciones/envios-en-progreso", {
      headers: authHeaders(), cache: "no-store",
    });
    if (r.status === 401) { window.snwSesionExpirada(); return; }
    if (!r.ok) throw new Error();
    items = await r.json();
  } catch {
    console.error("[historial.js cargarEnProgreso()]");
    return; // se conserva lo último mostrado, sin mensajes ni saltos
  } finally {
    cargandoEnProgreso = false;
  }
  // Si algo terminó o cambió, la tabla se recarga sola (sin mover página ni avisar).
  const firma = items.map((it) => `${it.origen}:${it.id ?? it.job_id}:${it.estado}`).join("|");
  if (firmaProgreso !== null && firmaProgreso !== firma) cargar(true);
  firmaProgreso = firma;
  // La cadencia sigue a la lista: rápida con envíos vivos, lenta si no hay nada.
  if (hayProgresoVivo !== !!items.length) {
    hayProgresoVivo = !!items.length;
    programarProgreso();
  }
  if (!items.length) {
    mostrarVacioProgreso(lista);
    if (mostrarFeedback) toast("Progreso actualizado correctamente.");
    return;
  }
  // Actualización quirúrgica: solo se toca la fila que cambió; el resto del
  // DOM (y el alto de la card) queda intacto.
  const cont = asegurarContenedorProgreso(lista);
  const vistos = new Set();
  for (const it of items) {
    const llave = `${it.origen}:${it.id ?? it.job_id}`;
    const firmaFila = firmaItemProgreso(it);
    vistos.add(llave);
    let fila = cont.querySelector(`[data-k="${llave}"]`);
    if (fila && fila.dataset.firma !== firmaFila) {
      const nueva = document.createElement("div");
      nueva.innerHTML = progEnProgresoHtml(it);
      const reemplazo = nueva.firstElementChild;
      reemplazo.dataset.k = llave;
      reemplazo.dataset.firma = firmaFila;
      fila.replaceWith(reemplazo);
    } else if (!fila) {
      const tmp = document.createElement("div");
      tmp.innerHTML = progEnProgresoHtml(it);
      fila = tmp.firstElementChild;
      fila.dataset.k = llave;
      fila.dataset.firma = firmaFila;
      cont.appendChild(fila);
    } else {
      cont.appendChild(fila); // mantiene el orden de respuesta sin parpadeo
    }
  }
  cont.querySelectorAll(".prog-item").forEach((el) => {
    if (!vistos.has(el.dataset.k)) el.remove();
  });
  if (mostrarFeedback) toast("Progreso actualizado correctamente.");
}

window.snwConCooldown($("#btnActualizarProgreso"), () => cargarEnProgreso(true));
programarProgreso();
// Al volver a la pestaña no se espera al siguiente sondeo: si el envío empezó
// en otro equipo mientras tanto, aparece de inmediato.
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) cargarEnProgreso();
});

document.addEventListener("click", (e) => {
  const bj = e.target.closest("#listaEnProgreso [data-cancel-job]");
  const bp = e.target.closest("#listaEnProgreso [data-cancel-prog]");
  if (bj) {
    cancelarEnProgreso(`api/notificaciones/jobs/${encodeURIComponent(bj.dataset.cancelJob)}/cancelar`, bj);
  } else if (bp) {
    cancelarEnProgreso(`api/notificaciones/programados/${bp.dataset.cancelProg}/cancelar`, bp);
  }
});
let ambienteDetalle = "produccion";
let areaDetalle = null; // area del envío abierto (para sus mensajes)
let pacienteMsgActual = null; // { id, ambiente, interesado }

let basesHist = [];
const selBaseHist = $("#selBaseHist");
if (selBaseHist) {
  selBaseHist.value = localStorage.getItem("snw_base_historial") || "todos";
}

// Selector único de base de datos: admin/dev ven todas (legacy dev/prod +
// tablas de áreas); el resto solo las tablas de sus áreas asignadas.
const ES_PRIV_HIST = !!window.snwEsPrivilegiado;

async function cargarBasesHist() {
  if (!selBaseHist) return;
  let areas = [];
  try {
    const r = await fetch("api/areas/mias", { headers: authHeaders(), cache: "no-store" });
    if (r.ok) areas = await r.json();
  } catch {
    console.error("[historial.js cargarBasesHist()]");
    areas = [];
  }
  basesHist = [];
  if (ES_PRIV_HIST) {
    basesHist.push(
      { tabla: "pacientes_dev", nombre: "Desarrollo (pacientes_dev)" },
      { tabla: "pacientes_prod", nombre: "Producción (pacientes_prod)" },
    );
  }
  for (const a of areas) {
    if (a.nombre_tabla_base) {
      basesHist.push({
        tabla: a.nombre_tabla_base,
        nombre: `${a.nombre_visible} (${a.nombre_tabla_base})`,
      });
    }
  }
  const guardada = localStorage.getItem("snw_base_historial") || "todos";
  selBaseHist.innerHTML = `<option value="todos">Todas</option>` +
    basesHist.map((b) => `<option value="${escaparHtml(b.tabla)}">${escaparHtml(b.nombre)}</option>`).join("");
  if (guardada !== "todos" && basesHist.some((b) => b.tabla === guardada)) {
    selBaseHist.value = guardada;
  } else {
    selBaseHist.value = "todos";
    localStorage.removeItem("snw_base_historial");
  }
  localStorage.removeItem("snw_esp_historial"); // llave anterior (filtro por área)
}

if (selBaseHist) selBaseHist.addEventListener("change", () => {
  const v = selBaseHist.value;
  if (v && v !== "todos") {
    localStorage.setItem("snw_base_historial", v);
  } else {
    localStorage.removeItem("snw_base_historial");
  }
  paginaHist = 1;
  cargar();
});

const pagAntHist = $("#pagAntHist");
if (pagAntHist) pagAntHist.addEventListener("click", () => {
  if (paginaHist > 1) { paginaHist -= 1; render(); }
});
const pagSigHist = $("#pagSigHist");
if (pagSigHist) pagSigHist.addEventListener("click", () => {
  paginaHist += 1;
  render();
});
const selPageSizeHist = $("#selPageSizeHist");
if (selPageSizeHist) {
  selPageSizeHist.value = String(pageSizeHist);
  selPageSizeHist.addEventListener("change", () => {
    pageSizeHist = Math.min(100, Math.max(10, Number(selPageSizeHist.value) || 10));
    localStorage.setItem("snw_page_size_hist", String(pageSizeHist));
    paginaHist = 1;
    render();
  });
}

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

async function cargar(mantenerPagina = false, mostrarFeedback = false) {
  try {
    const base = (selBaseHist && selBaseHist.value) || "todos";
    const qs = base !== "todos" ? `tabla=${encodeURIComponent(base)}` : "ambiente=todos";
    const rh = await fetch(`${API_HISTORIAL}?${qs}`, {
      headers: authHeaders(), cache: "no-store",
    });
    if (rh.status === 401) { window.snwSesionExpirada(); return; }
    if (!rh.ok) throw new Error();
    registros = await rh.json();
    if (!mantenerPagina) paginaHist = 1;
    render();
    if (mostrarFeedback) toast("Historial actualizado correctamente.");
  } catch {
    console.error("[historial.js cargar()]");
    if (!mantenerPagina) toast("Error al conectar con el servidor.", "error");
  }
}

function totalPaginasHist(n) {
  return Math.max(1, Math.ceil(n / pageSizeHist));
}

function pintarPaginadorHist(total) {
  const info = $("#pagInfoHist");
  const btnAnt = $("#pagAntHist");
  const btnSig = $("#pagSigHist");
  const selTam = $("#selPageSizeHist");
  if (!info && !btnAnt && !btnSig && !selTam) return; // HTML antiguo en caché
  const paginas = totalPaginasHist(total);
  if (info) info.textContent = `Página ${paginaHist} de ${paginas} · ${total} envío${total === 1 ? "" : "s"}`;
  if (btnAnt) btnAnt.disabled = paginaHist <= 1;
  if (btnSig) btnSig.disabled = paginaHist >= paginas;
  if (selTam) selTam.value = String(pageSizeHist);
}

function render() {
  const q = filtro.trim().toLowerCase();
  const visibles = registros.filter(
    (r) => !q ||
      [r.base_datos, r.plantilla_clave, r.plantilla_nombre]
        .filter(Boolean)
        .some((v) => v.toLowerCase().includes(q))
  );

  const paginas = totalPaginasHist(visibles.length);
  if (paginaHist > paginas) paginaHist = paginas;
  if (paginaHist < 1) paginaHist = 1;
  const parte = visibles.slice((paginaHist - 1) * pageSizeHist, paginaHist * pageSizeHist);

  tbodyEl.innerHTML = "";

  for (const r of parte) {
    const tr = document.createElement("tr");
    tr.dataset.id = String(r.id);
    tr.dataset.amb = (r.base_datos ?? "").includes("prod") ? "produccion" : "desarrollo";
    tr.dataset.base = r.base_datos ?? "";
    tr.dataset.esp = r.area_id != null ? String(r.area_id) : "";
    const total = r.total_pacientes ?? 0;
    const enviados = r.enviados ?? 0;
    const fallidos = r.fallidos ?? 0;
    const invalidos = r.invalidos ?? 0;
    const estado = r.estado || "completado";
    const estadoLabel =
      { cancelado: "Cancelado", rechazado: "Rechazado", pendiente: "Pendiente",
        aprobado: "Aprobado", enviando: "Enviando", enviado: "Enviado",
        en_progreso: "En progreso" }[estado] || "Completado";
    const comentario = (r.comentario ?? "").trim();
    const notaRechazo = estado === "rechazado"
      ? `<div class="hist-rechazo">Rechazado por el supervisor${comentario ? `: «${escaparHtml(comentario)}»` : " (sin comentario)"}</div>`
      : "";
    tr.innerHTML =
      `<td class="campo-fecha">${escaparHtml(r.fecha)}</td>` +
      `<td><span class="badge badge--origen">${r.origen === "programado" ? "Programado" : "Manual"}</span></td>` +
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
  pintarPaginadorHist(visibles.length);

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
  areaDetalle = (envio && envio.area_id != null) ? envio.area_id : (tr.dataset.esp ? Number(tr.dataset.esp) : null);

  try {
    const r = await fetch(`api/notificaciones/historial/${envioId}/detalle?ambiente=${amb}`, {
      headers: authHeaders(), cache: "no-store"
    });
    if (r.status === 401) { window.snwSesionExpirada(); return; }
    if (!r.ok) throw new Error();
    const detalle = await r.json();
    abrirDetalle(envio, detalle);
  } catch {
    console.error("[historial.js render()]");
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
    const qsEsp = areaDetalle != null ? `&area_id=${areaDetalle}` : "";
    const r = await fetch(`api/pacientes/${pacienteId}/mensajes?ambiente=${ambienteDetalle}${qsEsp}`, {
      headers: authHeaders(), cache: "no-store",
    });
    if (r.status === 401) { window.snwSesionExpirada(); return; }
    if (!r.ok) throw new Error();
    const data = await r.json();
    renderMensajes(data);
  } catch {
    console.error("[historial.js abrirMensajes()]");
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
  paginaHist = 1;
  render();
});

window.snwConCooldown($("#btnActualizar"), () => cargar(false, true));

let toastTimer;
function toast(msg, tipo = "ok") {
  clearTimeout(toastTimer);
  toastEl.textContent = msg;
  toastEl.className = `toast visible toast--${tipo}`;
  // Los errores no se desvanecen solos: se cierran con click para leerlos bien.
  if (tipo === "ok") {
    toastTimer = setTimeout(() => toastEl.classList.remove("visible"), 3200);
  }
}

// Los errores no se desvanecen solos: se cierran con click para leerlos bien.
toastEl.addEventListener("click", () => toastEl.classList.remove("visible"));

/* ---------- Panel "Registro de respuestas de call center" ---------- */
/* null si no se tiene el permiso `call_center_registro` (data-perm en el HTML) */
const panelCCLogEl = $("#panelCCRegistro");

async function cargarLogCC(mostrarFeedback = false) {
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
    if (mostrarFeedback) toast("Registro de call center actualizado correctamente.");
  } catch {
    console.error("[historial.js cargarLogCC()]");
    body.innerHTML = `<tr><td colspan="4" style="text-align:center;color:var(--danger-fg);">No se pudo cargar el registro.</td></tr>`;
  }
}

if (panelCCLogEl) {
  window.snwConCooldown($("#btnActualizarCCLog"), () => cargarLogCC(true));
}

cargarBasesHist();
cargar();
cargarLogCC();
cargarEnProgreso();
