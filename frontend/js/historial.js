const API_HISTORIAL = "api/notificaciones/historial";

function authHeaders(extra = {}) {
  return { Authorization: "Bearer " + (localStorage.getItem("snw_token") || ""), ...extra };
}

if (!localStorage.getItem("snw_token")) location.replace("login.html");

let registros = [];
let totalHistorial = 0;
let consultaHistorialAnterior = null;
let cargaHistSecuencia = 0;
let cargandoCambioBase = false;
let controlCargaHistorial = null;
let filtro = "";
let paginaHist = 1;
let pageSizeHist = Math.min(100, Math.max(10, Number(localStorage.getItem("snw_page_size_hist")) || 10));

const $ = (sel) => document.querySelector(sel);

function inicialesPaciente(nombre) {
  const letras = String(nombre ?? "").trim().split(/\s+/)
    .map((parte) => Array.from(parte).find((caracter) => /\p{L}/u.test(caracter)))
    .filter(Boolean);
  return letras.length ? letras.map((letra) => `${letra.toLocaleUpperCase("es")}.`).join(" ") : "—";
}

function telefonoEnmascarado(numero) {
  const digitos = String(numero ?? "").replace(/\D/g, "");
  if (!/^(?:569\d{8}|56\d{8}|9\d{7,8})$/.test(digitos)) return "—";
  return `+569 **** *${digitos.slice(-3)}`;
}

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
  const controlaPausa = it.job_id && it.puede_pausar && ["en_proceso", "pausado"].includes(it.estado);
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
    ((controlaPausa || cancelaJob || cancelaProg)
      ? `<div class="prog-item__acciones">` +
        (controlaPausa
          ? `<button type="button" class="btn btn--sm btn--ghost" data-pausa-job="${escaparHtml(it.job_id)}" data-pausado="${it.estado === "pausado"}">${it.estado === "pausado" ? "Reanudar" : "Pausar"}</button>`
          : "") +
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
    consultaHistorialAnterior = null;
    cargar();
  } catch (err) {
    console.error("[historial.js cancelarEnProgreso()]", err);
    toast(err.message || "No se pudo cancelar.", "error");
    btn.disabled = false;
  }
}

async function cambiarPausaEnProgreso(btn) {
  btn.disabled = true;
  const pausar = btn.dataset.pausado !== "true";
  try {
    const r = await fetch(`api/notificaciones/jobs/${encodeURIComponent(btn.dataset.pausaJob)}/${pausar ? "pausa" : "reanudar"}`, {
      method: "POST", headers: authHeaders(), cache: "no-store",
    });
    if (r.status === 401) { window.snwSesionExpirada(); return; }
    const data = await r.json().catch(() => ({}));
    if (!r.ok || !data.ok) throw new Error(data.detail || "No se pudo cambiar el estado del envío.");
    toast(pausar ? "Envío pausado." : "Envío reanudado.", "ok");
    await cargarEnProgreso();
  } catch (err) {
    toast(err.message || "No se pudo cambiar el estado del envío.", "error");
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
  if (firmaProgreso !== null && firmaProgreso !== firma) {
    consultaHistorialAnterior = null;
    cargar(true);
  }
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
  const pausa = e.target.closest("#listaEnProgreso [data-pausa-job]");
  const bj = e.target.closest("#listaEnProgreso [data-cancel-job]");
  const bp = e.target.closest("#listaEnProgreso [data-cancel-prog]");
  if (pausa) {
    cambiarPausaEnProgreso(pausa);
  } else if (bj || bp) {
    const boton = bj || bp;
    const modal = $("#modalCancelarProgreso");
    modal.dataset.urlCancelar = bj
      ? `api/notificaciones/jobs/${encodeURIComponent(bj.dataset.cancelJob)}/cancelar`
      : `api/notificaciones/programados/${encodeURIComponent(bp.dataset.cancelProg)}/cancelar`;
    modal._botonCancelar = boton;
    modal.hidden = false;
  }
});

$("#btnNoCancelarProgreso").addEventListener("click", () => {
  $("#modalCancelarProgreso").hidden = true;
});
$("#btnConfirmarCancelarProgreso").addEventListener("click", async () => {
  const modal = $("#modalCancelarProgreso");
  const boton = modal._botonCancelar;
  const url = modal.dataset.urlCancelar;
  modal.hidden = true;
  if (boton && url) await cancelarEnProgreso(url, boton);
});
let ambienteDetalle = "produccion";
let areaDetalle = null; // area del envío abierto (para sus mensajes)
let detalleActual = null;
let cargaDetalleSecuencia = 0;
let pacienteMsgActual = null; // { id, ambiente, interesado }

let basesHist = [];
const selBaseHist = $("#selBaseHist");
const selOrigenHist = $("#selOrigenHist");
if (selBaseHist) {
  selBaseHist.value = localStorage.getItem("snw_base_historial") || "todos";
}
if (selOrigenHist) {
  const guardado = localStorage.getItem("snw_origen_historial") || "todos";
  selOrigenHist.value = ["todos", "manual", "programado"].includes(guardado) ? guardado : "todos";
  selOrigenHist.addEventListener("change", () => {
    if (selOrigenHist.value === "todos") localStorage.removeItem("snw_origen_historial");
    else localStorage.setItem("snw_origen_historial", selOrigenHist.value);
    paginaHist = 1;
    cargar(false, false, true);
  });
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
  cargar(false, false, true);
});

const pagPrimHist = $("#pagPrimHist");
const pagAntHist = $("#pagAntHist");
const pagSigHist = $("#pagSigHist");
const pagUltHist = $("#pagUltHist");
const pagIrHist = $("#pagIrHist");
function irPaginaHist(valor) {
  const numero = Number(valor);
  if (!Number.isSafeInteger(numero) || numero < 1) {
    if (pagIrHist) pagIrHist.value = String(paginaHist);
    return;
  }
  const destino = Math.min(numero, totalPaginasHist(totalHistorial));
  if (destino === paginaHist) {
    if (pagIrHist) pagIrHist.value = String(paginaHist);
    return;
  }
  paginaHist = destino;
  cargar(true, false, true);
}
if (pagPrimHist) pagPrimHist.addEventListener("click", () => irPaginaHist(1));
if (pagAntHist) pagAntHist.addEventListener("click", () => irPaginaHist(paginaHist - 1));
if (pagSigHist) pagSigHist.addEventListener("click", () => irPaginaHist(paginaHist + 1));
if (pagUltHist) pagUltHist.addEventListener("click", () => irPaginaHist(totalPaginasHist(totalHistorial)));
if (pagIrHist) {
  pagIrHist.addEventListener("focus", () => pagIrHist.select());
  pagIrHist.addEventListener("change", () => irPaginaHist(pagIrHist.value));
  pagIrHist.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); irPaginaHist(pagIrHist.value); pagIrHist.blur(); }
  });
}
const selPageSizeHist = $("#selPageSizeHist");
if (selPageSizeHist) {
  selPageSizeHist.value = String(pageSizeHist);
  selPageSizeHist.addEventListener("change", () => {
    pageSizeHist = Math.min(100, Math.max(10, Number(selPageSizeHist.value) || 10));
    localStorage.setItem("snw_page_size_hist", String(pageSizeHist));
    paginaHist = 1;
    cargar(true, false, true);
  });
}

const tbodyEl = $("#tablaHistorial tbody");
const vacioEl = $("#tablaVacia");
const tablaHistorialEl = $("#tablaHistorial");
const tablaHistorialWrap = $("#tablaHistorialWrap");
const tablaCargandoHist = $("#tablaCargandoHist");
const tablaCargandoHistTexto = $("#tablaCargandoHistTexto");
const buscadorEl = $("#buscador");
const statsEl = $("#stats");
const contadorEl = $("#contador");
const toastEl = $("#toast");

function escaparHtml(texto) {
  const div = document.createElement("div");
  div.textContent = texto ?? "";
  return div.innerHTML;
}

async function cargar(mantenerPagina = false, mostrarFeedback = false, mostrarCarga = false) {
  if (cargandoCambioBase) mostrarCarga = true;
  const secuencia = ++cargaHistSecuencia;
  if (controlCargaHistorial) controlCargaHistorial.abort();
  const control = new AbortController();
  controlCargaHistorial = control;
  if (!mantenerPagina) paginaHist = 1;
  const inicioCarga = performance.now();
  tablaCargandoHist.hidden = false;
  tablaHistorialWrap.classList.add("is-loading");
  tablaHistorialEl.setAttribute("aria-busy", "true");
  vacioEl.hidden = true;
  if (mostrarCarga) {
    cargandoCambioBase = true;
    registros = [];
    tbodyEl.innerHTML = "";
    contadorEl.textContent = "Actualizando…";
    statsEl.innerHTML = "";
    $("#pagInfoHist").textContent = "Actualizando…";
    $("#pagPrimHist").disabled = true;
    $("#pagAntHist").disabled = true;
    $("#pagIrHist").disabled = true;
    $("#pagSigHist").disabled = true;
    $("#pagUltHist").disabled = true;
  }
  try {
    const base = (selBaseHist && selBaseHist.value) || "todos";
    const params = new URLSearchParams(base !== "todos" ? { tabla: base } : { ambiente: "todos" });
    params.set("origen", (selOrigenHist && selOrigenHist.value) || "todos");
    params.set("q", filtro.trim().slice(0, 100));
    params.set("limite", String(pageSizeHist));
    const claveConsulta = `${base}|${params.get("origen")}|${params.get("q")}`;
    const requiereTotal = claveConsulta !== consultaHistorialAnterior || mostrarFeedback;
    params.set("incluir_total", String(requiereTotal));
    let respuesta;
    do {
      params.set("pagina", String(paginaHist));
      tablaCargandoHistTexto.textContent =
        `Cargando historial… página ${paginaHist} de ${requiereTotal ? "…" : totalPaginasHist(totalHistorial)}`;
      const rh = await fetch(`${API_HISTORIAL}/pagina?${params}`, {
        headers: authHeaders(), cache: "no-store", signal: control.signal,
      });
      if (rh.status === 401) { window.snwSesionExpirada(); return; }
      if (!rh.ok) throw new Error();
      respuesta = await rh.json();
      if (secuencia !== cargaHistSecuencia) return;
      if (respuesta.total !== null) totalHistorial = Number(respuesta.total) || 0;
      if (paginaHist > totalPaginasHist(totalHistorial)) {
        paginaHist = totalPaginasHist(totalHistorial);
        params.set("incluir_total", "false");
      } else break;
    } while (true);
    tablaCargandoHistTexto.textContent =
      `Cargando historial… página ${paginaHist} de ${totalPaginasHist(totalHistorial)}`;
    const restante = 250 - (performance.now() - inicioCarga);
    if (restante > 0) await new Promise((resolver) => setTimeout(resolver, restante));
    if (secuencia !== cargaHistSecuencia) return;
    registros = respuesta.registros || [];
    consultaHistorialAnterior = claveConsulta;
    render();
    if (mostrarFeedback) toast("Historial actualizado correctamente.");
  } catch (err) {
    if (secuencia !== cargaHistSecuencia) return;
    if (err?.name === "AbortError") return;
    console.error("[historial.js cargar()]");
    if (mostrarCarga || !registros.length) {
      vacioEl.textContent = "No se pudo actualizar el historial. Inténtalo de nuevo.";
      vacioEl.hidden = false;
      contadorEl.textContent = "—";
      $("#pagInfoHist").textContent = "No disponible";
    } else {
      vacioEl.hidden = true;
    }
    if (mostrarCarga || !mantenerPagina) toast("Error al conectar con el servidor.", "error");
    pintarPaginadorHist(totalHistorial);
  } finally {
    if (secuencia === cargaHistSecuencia) {
      controlCargaHistorial = null;
      tablaCargandoHist.hidden = true;
      tablaHistorialWrap.classList.remove("is-loading");
      tablaHistorialEl.setAttribute("aria-busy", "false");
      cargandoCambioBase = false;
    }
  }
}

function totalPaginasHist(n) {
  return Math.max(1, Math.ceil(n / pageSizeHist));
}

function pintarPaginadorHist(total) {
  const info = $("#pagInfoHist");
  const btnPrim = pagPrimHist;
  const btnAnt = pagAntHist;
  const inpIr = pagIrHist;
  const btnSig = pagSigHist;
  const btnUlt = pagUltHist;
  const selTam = $("#selPageSizeHist");
  if (!info && !btnAnt && !btnSig && !selTam) return; // HTML antiguo en caché
  const paginas = totalPaginasHist(total);
  if (info) info.textContent = `Página ${paginaHist} de ${paginas} · ${total} envío${total === 1 ? "" : "s"}`;
  if (btnPrim) btnPrim.disabled = paginaHist <= 1;
  if (btnAnt) btnAnt.disabled = paginaHist <= 1;
  if (inpIr) {
    inpIr.max = String(paginas);
    inpIr.value = String(paginaHist);
    inpIr.disabled = paginas <= 1;
    inpIr.setAttribute("aria-label", `Ir a la página (1 a ${paginas})`);
  }
  if (btnSig) btnSig.disabled = paginaHist >= paginas;
  if (btnUlt) btnUlt.disabled = paginaHist >= paginas;
  if (selTam) selTam.value = String(pageSizeHist);
}

function render() {
  vacioEl.textContent = "Sin envíos registrados.";
  const paginas = totalPaginasHist(totalHistorial);
  if (paginaHist > paginas) paginaHist = paginas;
  if (paginaHist < 1) paginaHist = 1;
  const parte = registros;

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

  vacioEl.hidden = registros.length > 0;
  contadorEl.textContent = `${totalHistorial} envío${totalHistorial === 1 ? "" : "s"}`;
  pintarPaginadorHist(totalHistorial);

  const totalEnvios = totalHistorial;
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
  detalleActual = { envioId, ambiente: amb, envio };
  await cargarDetalle();
});

async function cargarDetalle() {
  const objetivo = detalleActual;
  if (!objetivo) return;
  const secuencia = ++cargaDetalleSecuencia;
  const btn = $("#btnActualizarDetalle");
  const tabla = $("#tablaDetallePacientes");
  btn.disabled = true;
  btn.querySelector("i").classList.add("fa-spin");
  tabla.classList.add("detalle-actualizando");
  tabla.setAttribute("aria-busy", "true");
  try {
    const r = await fetch(`api/notificaciones/historial/${objetivo.envioId}/detalle?ambiente=${objetivo.ambiente}`, {
      headers: authHeaders(), cache: "no-store"
    });
    if (r.status === 401) { window.snwSesionExpirada(); return; }
    if (!r.ok) throw new Error();
    const detalle = await r.json();
    if (secuencia !== cargaDetalleSecuencia) return;
    abrirDetalle(objetivo.envio, detalle);
  } catch {
    if (secuencia === cargaDetalleSecuencia) {
      console.error("[historial.js cargarDetalle()]");
      toast("No se pudo actualizar la lista de pacientes.", "error");
    }
  } finally {
    if (secuencia === cargaDetalleSecuencia) {
      btn.disabled = false;
      btn.querySelector("i").classList.remove("fa-spin");
      tabla.classList.remove("detalle-actualizando");
      tabla.removeAttribute("aria-busy");
    }
  }
}

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
    const pesoRespuesta = { respondio: 0, no_interesado: 1, baja: 2, pendiente: 3 };
    const orden = [...detalle].sort(
      (x, y) => (pesoRespuesta[x.respuesta] ?? 4) - (pesoRespuesta[y.respuesta] ?? 4) || (x.id - y.id)
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
      const celdaMsgs = d.paciente_id && d.datos_completos
        ? `<td class="hist-col-msgs"><button type="button" class="btn btn--sm btn--ghost" data-mensajes="${d.paciente_id}">Ver mensajes</button></td>`
        : `<td class="hist-col-msgs">—</td>`;
      const tr = document.createElement("tr");
      tr.innerHTML =
        `<td class="campo-nombre">${escaparHtml(inicialesPaciente(d.nombre_paciente))}${marcaInteres}</td>` +
        `<td><span class="respuesta-badge respuesta-${escaparHtml(r)}">${escaparHtml(respuestaLabel(r))}</span>${msgHtml}</td>` +
        `<td class="campo-fecha">${escaparHtml(d.fecha ?? "—")}</td>` +
        celdaMsgs;
      body.appendChild(tr);
    }
  }

  $("#modalDetalle").hidden = false;
}

function respuestaLabel(r) {
  return { pendiente: "Sin respuesta", respondio: "Respondió", no_interesado: "No interesado", baja: "Se dio de baja" }[r] ?? r;
}

function cerrarDetalle() {
  $("#modalDetalle").hidden = true;
  detalleActual = null;
  ++cargaDetalleSecuencia;
  $("#btnActualizarDetalle").disabled = false;
  $("#btnActualizarDetalle i").classList.remove("fa-spin");
  $("#tablaDetallePacientes").classList.remove("detalle-actualizando");
  $("#tablaDetallePacientes").removeAttribute("aria-busy");
}

$("#btnActualizarDetalle").addEventListener("click", cargarDetalle);
$("#btnCerrarDetalle").addEventListener("click", cerrarDetalle);
$("#modalDetalle").addEventListener("click", (e) => { if (e.target === $("#modalDetalle")) cerrarDetalle(); });

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
  mensajesVistaActual = data;
  const pac = data.paciente || {};
  pacienteMsgActual = { id: pac.id, ambiente: ambienteDetalle, interesado: !!pac.interesado };

  const iniciales = inicialesPaciente([pac.nombre, pac.apellido].filter(Boolean).join(" "));
  $("#mensajesTitulo").textContent = iniciales === "—" ? "Mensajes del paciente" : `Mensajes · ${iniciales}`;

  const estado = respuestaLabel(pac.respuesta || "pendiente");
  $("#msgPacienteCab").innerHTML =
    `<span>${escaparHtml(telefonoEnmascarado(pac.telefono))}</span>` +
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
      const etiquetas = m.direccion === "entrante" ?
        `<div class="msg-etiquetas" aria-label="Etiquetas de esta respuesta">` +
        `<span class="msg-etiquetas-origen" title="Puedes corregir las etiquetas sugeridas sin alterar el estado del paciente">` +
        `${m.origen_etiquetas === "manual" ? "Revisadas" : "Sugeridas automáticamente"}</span>` +
        Object.entries(ETIQUETAS_MENSAJE).map(([clave, nombre]) =>
          `<button type="button" class="msg-etiqueta${(m.etiquetas || []).includes(clave) ? " msg-etiqueta--activa" : ""}"` +
          ` data-etiqueta-id="${Number(m.id)}" data-etiqueta="${clave}"` +
          ` aria-pressed="${(m.etiquetas || []).includes(clave)}">${nombre}</button>`).join("") +
        `</div>` : "";
      return `<div class="msg-burbuja msg-burbuja--${m.direccion}${m.interes ? " msg-burbuja--interes" : ""}">` +
        `<div class="msg-texto">${escaparHtml(texto)}</div>` +
        `<div class="msg-meta">${meta}</div>${etiquetas}</div>`;
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

const ETIQUETAS_MENSAJE = {
  interes: "Interés", consulta: "Consulta", desinteres: "Desinterés",
  lenguaje_ofensivo: "Lenguaje ofensivo",
  baja_solicitada: "Baja solicitada", respuesta_automatica: "Bot",
  requiere_revision: "Revisar", confirmacion: "Confirmación",
  cancelacion: "Cancelación", resuelto: "Resuelto",
};
let mensajesVistaActual = null;
const guardandoEtiquetas = new Set();

$("#msgHilo").addEventListener("click", async (event) => {
  const boton = event.target.closest("[data-etiqueta-id]");
  if (!boton || !mensajesVistaActual || !pacienteMsgActual) return;
  const id = Number(boton.dataset.etiquetaId);
  const etiqueta = boton.dataset.etiqueta;
  const mensaje = (mensajesVistaActual.mensajes || []).find((m) => m.id === id && m.direccion === "entrante");
  if (!mensaje || !Object.hasOwn(ETIQUETAS_MENSAJE, etiqueta) || guardandoEtiquetas.has(id)) return;
  const previas = mensaje.etiquetas || [];
  const nuevas = previas.includes(etiqueta)
    ? previas.filter((e) => e !== etiqueta)
    : etiqueta === "requiere_revision"
      ? ["requiere_revision"]
      : [...previas.filter((e) => e !== "requiere_revision"), etiqueta];
  guardandoEtiquetas.add(id);
  const grupo = boton.closest(".msg-etiquetas");
  grupo.querySelectorAll("button").forEach((b) => { b.disabled = true; });
  try {
    const area = areaDetalle != null ? `&area_id=${areaDetalle}` : "";
    const r = await fetch(
      `api/pacientes/${pacienteMsgActual.id}/mensajes/${id}/etiquetas?ambiente=${ambienteDetalle}${area}`,
      { method: "PUT", headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ etiquetas: nuevas }) },
    );
    if (r.status === 401) { window.snwSesionExpirada(); return; }
    if (!r.ok) throw new Error();
    const guardado = await r.json();
    mensaje.etiquetas = guardado.etiquetas;
    mensaje.origen_etiquetas = "manual";
    grupo.querySelectorAll("[data-etiqueta]").forEach((b) => {
      const activa = guardado.etiquetas.includes(b.dataset.etiqueta);
      b.classList.toggle("msg-etiqueta--activa", activa);
      b.setAttribute("aria-pressed", String(activa));
    });
    grupo.querySelector(".msg-etiquetas-origen").textContent = "Revisadas";
  } catch {
    toast("No se pudieron guardar las etiquetas.", "error");
  } finally {
    guardandoEtiquetas.delete(id);
    grupo.querySelectorAll("button").forEach((b) => { b.disabled = false; });
  }
});

$("#btnCerrarMensajes").addEventListener("click", () => ($("#modalMensajes").hidden = true));
$("#modalMensajes").addEventListener("click", (e) => { if (e.target === $("#modalMensajes")) $("#modalMensajes").hidden = true; });

let timerBusquedaHist = null;
buscadorEl.addEventListener("input", () => {
  filtro = buscadorEl.value;
  paginaHist = 1;
  clearTimeout(timerBusquedaHist);
  timerBusquedaHist = setTimeout(() => cargar(false, false, true), 250);
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

/* ---------- Historial de respuestas de call center ---------- */
const panelCCLogEl = $("#panelCCRegistro");
const etiquetasMotivoCC = {
  CONSULTA_HORAS: "Horas",
  CONSULTA_MEDICO: "Profesionales",
  CONSULTA_PRECIOS: "Precios y servicios",
  SOLICITA_CONTACTO: "Contacto",
  SOLICITA_INFORMACION: "Información",
  CONSULTA_AMBIGUA: "Consulta general"
};
const etiquetasEstadoCC = {
  pendiente: "Pendiente",
  notificado: "Botón enviado",
  error: "Error de envío",
  finalizado: "Finalizada"
};

function badgeEstadoCC(estado) {
  const clave = Object.hasOwn(etiquetasEstadoCC, estado) ? estado : "pendiente";
  return `<span class="historial__cc-estado-badge historial__cc-estado-badge--${clave}">${etiquetasEstadoCC[clave]}</span>`;
}

function numeroCCVisible(numero) {
  const valor = String(numero ?? "").trim();
  return valor ? (valor.startsWith("+") ? valor : `+${valor}`) : "—";
}

const vistasAvisoCC = new Map();

function abrirAvisoCC(id) {
  const fila = vistasAvisoCC.get(Number(id));
  if (!fila?.vista_mensaje) return;
  const vista = fila.vista_mensaje;
  $("#avisoCCEstado").textContent = fila.estado === "enviado"
    ? "Vista del aviso registrado al enviarlo." : "Aviso preparado; el envío registró un error.";
  $("#avisoCCTexto").textContent = vista.texto || "—";
  $("#avisoCCBoton").textContent = vista.boton_texto || "Ir al call center";
  $("#avisoCCDestino").textContent = `El botón abre el chat con ${numeroCCVisible(fila.numero_call_center)}.`;
  $("#avisoCCPreparado").textContent = vista.mensaje_preparado
    ? `Mensaje preparado al pulsar: ${vista.mensaje_preparado}`
    : "El botón no tenía un texto preparado.";
  $("#modalAvisoCC").hidden = false;
}

$("#btnCerrarAvisoCC").addEventListener("click", () => { $("#modalAvisoCC").hidden = true; });
$("#modalAvisoCC").addEventListener("click", (evento) => {
  if (evento.target === $("#modalAvisoCC")) $("#modalAvisoCC").hidden = true;
});

async function cargarLogCC(mostrarFeedback = false) {
  if (!panelCCLogEl) return;
  const body = $("#ccLogBody");
  const derivacionesBody = $("#ccDerivacionesBody");
  const cont = $("#ccLogContadores");
  body.innerHTML = `<tr><td colspan="6" class="historial__cc-estado">Cargando historial…</td></tr>`;
  derivacionesBody.innerHTML = `<tr><td colspan="6" class="historial__cc-estado">Cargando consultas…</td></tr>`;
  try {
    const r = await fetch("api/call-center/log", { headers: authHeaders(), cache: "no-store" });
    if (r.status === 401) { window.snwSesionExpirada(); return; }
    if (r.status === 403) { panelCCLogEl.remove(); return; }
    if (!r.ok) throw new Error();
    const data = await r.json();
    const contadores = Object.entries(data.contadores || {});
    const totalUsos = contadores.reduce((total, [, usos]) => total + (Number(usos) || 0), 0);
    cont.innerHTML = contadores.length
      ? `<div class="historial__cc-uso historial__cc-uso--total"><span>Total de usos</span><strong>${totalUsos.toLocaleString("es-CL")}</strong></div>` +
        contadores.map(([numero, usos]) =>
          `<div class="historial__cc-uso"><span>${escaparHtml(numeroCCVisible(numero))}</span>` +
          `<strong>${(Number(usos) || 0).toLocaleString("es-CL")} uso${Number(usos) === 1 ? "" : "s"}</strong></div>`
        ).join("")
      : `<p class="historial__cc-sin-usos">Todavía no hay usos registrados.</p>`;

    const filas = data.entradas || [];
    vistasAvisoCC.clear();
    filas.forEach((fila) => { if (fila.vista_mensaje) vistasAvisoCC.set(Number(fila.id), fila); });
    const derivaciones = data.derivaciones || [];
    derivacionesBody.innerHTML = derivaciones.length
      ? derivaciones.map((d) => `<tr>` +
          `<td class="campo-fecha">${escaparHtml(d.fecha ?? "—")}</td>` +
          `<td class="campo-tel">${escaparHtml(d.telefono ?? "—")}</td>` +
          `<td class="historial__cc-mensaje">${escaparHtml(d.mensaje ?? "—")}</td>` +
          `<td><div class="historial__cc-motivos">${(d.motivos || []).map((motivo) =>
            `<span class="historial__cc-motivo">${escaparHtml(etiquetasMotivoCC[motivo] || motivo)}</span>`
          ).join("") || "—"}</div></td>` +
          `<td class="campo-tel">${escaparHtml(numeroCCVisible(d.numero_call_center))}</td>` +
          `<td>${badgeEstadoCC(d.estado)}` +
          (d.puede_reintentar ? ` <button type="button" class="btn btn--ghost historial__cc-reintentar" data-reintentar-derivacion="${Number(d.id)}">Reintentar</button>` : "") +
          `</td>` +
          `</tr>`).join("")
      : `<tr><td colspan="6" class="historial__cc-estado">Todavía no hay consultas derivadas.</td></tr>`;
    body.innerHTML = filas.length
      ? filas.map((f) => {
          const enviado = f.estado === "enviado";
          const estado = enviado ? "Enviado" : "Error";
          return `<tr>` +
            `<td class="campo-fecha">${escaparHtml(f.fecha ?? "—")}</td>` +
            `<td class="campo-nombre">${escaparHtml(f.nombre_paciente ?? "—")}</td>` +
            `<td class="campo-tel">${escaparHtml(f.numero_paciente ?? "—")}</td>` +
            `<td>${escaparHtml(f.base_datos ?? "—")}</td>` +
            `<td class="campo-tel">${escaparHtml(numeroCCVisible(f.numero_call_center))}</td>` +
            `<td><span class="historial__cc-estado-badge historial__cc-estado-badge--${enviado ? "notificado" : "error"}">${estado}</span>` +
            (f.vista_mensaje ? ` <button type="button" class="btn btn--ghost btn--sm historial__cc-ver-aviso" data-ver-aviso-cc="${Number(f.id)}">Ver mensaje</button>` : "") +
            (f.descripcion_error ? `<div class="hist-msg">${escaparHtml(f.descripcion_error)}</div>` : "") +
            `</td></tr>`;
        }).join("")
      : `<tr><td colspan="6" class="historial__cc-estado">Todavía no hay respuestas de call center registradas.</td></tr>`;
    if (mostrarFeedback) toast("Historial de call center actualizado.");
  } catch (err) {
    console.error("[historial.js cargarLogCC()]", err);
    cont.innerHTML = "";
    body.innerHTML = `<tr><td colspan="6" class="historial__cc-estado historial__cc-estado--error">No se pudo cargar el historial de call center.</td></tr>`;
    derivacionesBody.innerHTML = `<tr><td colspan="6" class="historial__cc-estado historial__cc-estado--error">No se pudieron cargar las consultas.</td></tr>`;
  }
}

if (panelCCLogEl) {
  window.snwConCooldown($("#btnActualizarCCLog"), () => cargarLogCC(true));
  $("#ccLogBody").addEventListener("click", (evento) => {
    const boton = evento.target.closest("[data-ver-aviso-cc]");
    if (boton) abrirAvisoCC(boton.dataset.verAvisoCc);
  });
  $("#ccDerivacionesBody").addEventListener("click", async (evento) => {
    const boton = evento.target.closest("[data-reintentar-derivacion]");
    if (!boton || boton.disabled) return;
    boton.disabled = true;
    try {
      const id = Number(boton.dataset.reintentarDerivacion);
      const r = await fetch(`api/call-center/derivaciones/${id}/reintentar`, {
        method: "POST", headers: authHeaders()
      });
      if (!r.ok) throw new Error("No se pudo reintentar la derivación.");
      toast("Aviso de derivación reprogramado.");
      await cargarLogCC();
    } catch (err) {
      toast(err.message || "No se pudo reintentar.", "error");
      boton.disabled = false;
    }
  });
}

tablaCargandoHist.hidden = false;
tablaHistorialWrap.classList.add("is-loading");
tablaHistorialEl.setAttribute("aria-busy", "true");
(async () => { await cargarBasesHist(); cargar(); })();
cargarLogCC();
cargarEnProgreso();
