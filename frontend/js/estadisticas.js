/* Página «Estadísticas» (estadisticas.html, solo admin/dev). */
(function () {
const $ = (sel) => document.querySelector(sel);

function authHeaders() {
  return { Authorization: "Bearer " + (localStorage.getItem("snw_token") || "") };
}

const toastEl = $("#toast");
const cargandoEstadisticasEl = $("#cargandoEstadisticas");
const contenidoEstadisticasEl = $("#contenidoEstadisticas");
let cargasEstadisticas = 0;

function iniciarCargaEstadisticas() {
  cargasEstadisticas += 1;
  cargandoEstadisticasEl.hidden = false;
  contenidoEstadisticasEl.setAttribute("aria-busy", "true");
  return performance.now();
}

async function terminarCargaEstadisticas(inicio) {
  const restante = 250 - (performance.now() - inicio);
  if (restante > 0) await new Promise((resolver) => setTimeout(resolver, restante));
  cargasEstadisticas = Math.max(0, cargasEstadisticas - 1);
  if (cargasEstadisticas) return;
  cargandoEstadisticasEl.hidden = true;
  contenidoEstadisticasEl.setAttribute("aria-busy", "false");
}

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

const MESES = [
  "enero", "febrero", "marzo", "abril", "mayo", "junio",
  "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
];

function nombreMes(ym) {
  const [a, m] = String(ym).split("-").map(Number);
  return `${MESES[(m || 1) - 1]} ${a}`;
}

function fechaDMA(iso) {
  const m = String(iso || "").match(/^(\d{4})-(\d{2})-(\d{2})/);
  return m ? `${m[3]}-${m[2]}-${m[1]}` : iso || "—";
}

const num = (n) => Number(n || 0).toLocaleString("es-CL");
function escaparHtml(valor) {
  const span = document.createElement("span");
  span.textContent = valor ?? "";
  return span.innerHTML;
}

// Alcance global (Entorno + Área): alimenta los tres endpoints. Producción por
// defecto; Desarrollo es la base de pruebas; un id filtra a esa área de prod.
let areasEst = [];
const selEntornoEst = $("#selEntornoEst");
const selAreaEst = $("#selAreaEst");
const selCentroEst = $("#selCentroEst");
const campoAreaEst = $("#campoAreaEst");
let clinicasEst = [];

function entornoEst() {
  return selEntornoEst?.value === "desarrollo" ? "desarrollo" : "produccion";
}

function areaEstActual() {
  if (entornoEst() === "desarrollo") return null;
  const v = selAreaEst ? selAreaEst.value : "";
  return /^\d+$/.test(v) ? Number(v) : null;
}

function qsEspEst() {
  const centro = selCentroEst?.value || "todos";
  const qsCentro = centro !== "todos" ? `&centro=${encodeURIComponent(centro)}` : "";
  if (entornoEst() === "desarrollo") return `&base=desarrollo${qsCentro}`;
  const esp = areaEstActual();
  return (esp ? `&area_id=${esp}` : "") + qsCentro;
}

function nombreAreaEst() {
  if (entornoEst() === "desarrollo") return "";
  const esp = areaEstActual();
  const e = areasEst.find((x) => x.id === esp);
  return e ? e.nombre_visible : "";
}

// El selector de área solo aplica al entorno de producción.
function pintarCampoAreaEst() {
  if (campoAreaEst) campoAreaEst.hidden = entornoEst() === "desarrollo";
}

async function cargarAreasEst() {
  if (!selAreaEst) return;
  try {
    const res = await fetch("api/areas/mias", { headers: authHeaders(), cache: "no-store" });
    if (!res.ok) throw new Error();
    areasEst = await res.json();
  } catch {
    console.error("[estadisticas.js cargarAreasEst()]");
    areasEst = [];
  }
  const entornoGuardado = localStorage.getItem("snw_entorno_est");
  if (selEntornoEst) {
    selEntornoEst.value = entornoGuardado === "desarrollo" ? "desarrollo" : "produccion";
  }
  const areaGuardada = localStorage.getItem("snw_esp_estadisticas") || "";
  selAreaEst.innerHTML =
    `<option value="">Todas las &aacute;reas</option>` +
    areasEst.map((e) => `<option value="${e.id}">${escaparHtml(e.nombre_visible)} (${escaparHtml(e.nombre_tabla_base)})</option>`).join("");
  if (areasEst.some((e) => String(e.id) === areaGuardada)) {
    selAreaEst.value = areaGuardada;
  } else {
    localStorage.removeItem("snw_esp_estadisticas");
  }
  pintarCampoAreaEst();
  selAreaEst.hidden = false;
}

if (selEntornoEst) selEntornoEst.addEventListener("change", () => {
  if (selEntornoEst.value === "desarrollo") localStorage.setItem("snw_entorno_est", "desarrollo");
  else localStorage.removeItem("snw_entorno_est");
  pintarCampoAreaEst();
  limpiarFiltroCalendarioCostos();
  cerrarCalendarioCostos();
  recargarTodo();
});

if (selAreaEst) selAreaEst.addEventListener("change", () => {
  const v = selAreaEst.value;
  if (v) localStorage.setItem("snw_esp_estadisticas", v);
  else localStorage.removeItem("snw_esp_estadisticas");
  limpiarFiltroCalendarioCostos();
  cerrarCalendarioCostos();
  recargarTodo();
});

async function cargarClinicasEst() {
  if (!selCentroEst) return;
  selCentroEst.disabled = true;
  try {
    const res = await fetch("api/whatsapp/centros?incluir_inactivas=true&solo_con_envios=true", {
      headers: authHeaders(), cache: "no-store",
    });
    if (!res.ok) throw new Error();
    clinicasEst = await res.json();
    selCentroEst.innerHTML = '<option value="todos">Todas las clínicas</option>' +
      clinicasEst.map((c) => `<option value="${escaparHtml(c.codigo)}">${escaparHtml(c.nombre)}${c.activa ? "" : " (inactiva)"}</option>`).join("");
    const anterior = localStorage.getItem("snw_centro_est") || "todos";
    if (clinicasEst.some((c) => c.codigo === anterior)) selCentroEst.value = anterior;
    else localStorage.removeItem("snw_centro_est");
  } catch {
    console.error("[estadisticas.js cargarClinicasEst()]");
    toast("No se pudo cargar la lista de clínicas.", "error");
  } finally {
    selCentroEst.disabled = false;
  }
}

if (selCentroEst) {
  selCentroEst.addEventListener("change", () => {
    localStorage.setItem("snw_centro_est", selCentroEst.value);
    limpiarFiltroCalendarioCostos();
    cerrarCalendarioCostos();
    recargarTodo();
  });
}

async function recargarTodo() {
  botonesCargados = false;
  campanasCargadas = false;
  const cargas = [cargar(), cargarEnvios(granEnvios)];
  if ($("#statsTabCampanas")?.classList.contains("activo")) cargas.push(cargarCampanas(true));
  if ($("#statsTabRespuestas")?.classList.contains("activo")) cargas.push(cargarBotones());
  if (ES_ADMIN && $("#panelCostos")) {
    cargas.push(cargarTarifas(), cargarCostos(granCostos));
  }
  await Promise.all(cargas);
}

let botonesCargados = false;
let paginaEstadisticasLista = false;
async function cargarBotones() {
  const el = $("#estadisticasBotones");
  if (!el) return;
  const inicioCarga = iniciarCargaEstadisticas();
  el.textContent = "Cargando clics en botones…";
  try {
    const alcance = qsEspEst();
    const res = await fetch(`api/estadisticas/botones${alcance ? `?${alcance.slice(1)}` : ""}`,
      { headers: authHeaders(), cache: "no-store" });
    if (res.status === 401) { window.snwSesionExpirada(); return; }
    if (!res.ok) throw new Error("No se pudieron cargar los clics en botones.");
    const datos = await res.json();
    botonesCargados = true;
    const filas = datos.filas || [];
    if (!filas.length) {
      el.innerHTML = '<p class="mes-vacio">Aún no hay botones configurados ni pulsaciones registradas para este alcance.</p>';
      return;
    }
    el.innerHTML = `<p class="stats-botones__total">${num(datos.total_clics)} clics registrados</p>` +
      `<div class="stats-botones__tabla-wrap"><table><thead><tr>` +
      `<th>Botón u opción</th><th>Tipo</th><th>Este mes</th><th>Total</th><th>Pacientes</th>` +
      `</tr></thead><tbody>` + filas.map((fila) => {
        const tipo = fila.tipo === "url" ? "Enlace" : fila.tipo === "telefono" ? "Llamada" : "Respuesta";
        const cantidad = (valor) => fila.medible ? num(valor) : "No medible";
        return `<tr><th scope="row">${escaparHtml(fila.boton)}</th><td>${tipo}</td>` +
          `<td>${cantidad(fila.clics_mes)}</td><td><strong>${cantidad(fila.clics)}</strong></td>` +
          `<td>${cantidad(fila.pacientes)}</td></tr>`;
      }).join("") + `</tbody></table></div>`;
  } catch (err) {
    console.error("[estadisticas.js cargarBotones()]", err);
    el.textContent = "No se pudieron cargar los clics en botones.";
  } finally {
    await terminarCargaEstadisticas(inicioCarga);
  }
}

function chip(label, valor, clase, resp) {
  const attr = resp ? ` data-respuesta="${resp}"` : "";
  return `<div class="stat ${clase}"${attr}>${label} <strong>${num(valor)}</strong></div>`;
}

async function cargar(mostrarFeedback = false) {
  // El botón lo deshabilita/rehabilita el cooldown de snwConCooldown, no
  // esta función (ver el addEventListener más abajo).
  const inicioCarga = iniciarCargaEstadisticas();
  try {
    const alcance = qsEspEst();
    const res = await fetch(`api/estadisticas${alcance ? `?${alcance.slice(1)}` : ""}`, { headers: authHeaders(), cache: "no-store" });
    if (res.status === 401) { window.snwSesionExpirada(); return; }
    if (!res.ok) throw new Error();
    render(await res.json());
    if (mostrarFeedback) toast("Estadísticas actualizadas correctamente.");
  } catch {
    console.error("[estadisticas.js cargar()]");
    toast("No se pudieron cargar las estadísticas.", "error");
  } finally {
    await terminarCargaEstadisticas(inicioCarga);
  }
}

function render(d) {
  $("#nombreMes").textContent = nombreMes(d.mes);
  const nombreMesResp = $("#nombreMesResp");
  if (nombreMesResp) nombreMesResp.textContent = nombreMes(d.mes);
  $("#enviadosMes").textContent = num(d.enviados_mes);

  const espNombre = (d.area && d.area.nombre_visible) || nombreAreaEst();
  const clinicaNombre = clinicasEst.find((c) => c.codigo === d.centro)?.nombre || "Todas las clínicas";
  const descripcionAlcance = `${entornoEst() === "desarrollo" ? "Desarrollo" : "Producción"} · ${clinicaNombre}` +
    (espNombre ? ` · ${espNombre}` : "");
  const heroSub = document.querySelector("#stabEnvios .hero-sub");
  if (heroSub) heroSub.textContent = `mensajes enviados este mes · ${descripcionAlcance}`;
  const hintResp = $("#hintRespuestas");
  if (hintResp) {
    const alcance = `de <strong>${escaparHtml(descripcionAlcance)}</strong>`;
    hintResp.innerHTML = `Estado actual de los pacientes ${alcance} ` +
      `<strong>a los que ya se les envió un mensaje</strong>. Sin una señal explícita de interés o rechazo de la oferta quedan «Sin clasificar». ` +
      `En <a href="historial.html">Historial</a> puedes ver quiénes son.`;
  }

  // Pestaña Envíos: eventos de envío del mes.
  $("#statsMes").innerHTML =
    chip("Enviados", d.enviados_mes, "stat--enviado") +
    chip("Fallidos", d.fallidos_mes, "stat--error") +
    chip("Inválidos", d.invalidos_mes, "stat--invalido");

  // Pestaña Respuestas: eventos de respuesta del mes.
  const statsRespMes = $("#statsRespMes");
  if (statsRespMes) {
    statsRespMes.innerHTML =
      chip("Respondieron", d.respondio_mes, "stat--resp", "respondio") +
      chip("Bajas", d.baja_mes, "stat--resp", "baja");
  }

  $("#statsTotal").innerHTML =
    chip("Mensajes enviados", d.total_enviados_historico, "stat--total") +
    chip("Envíos realizados", d.total_batches, "stat--total");

  datosPacientesResp = d.pacientes_por_respuesta || {};
  datosPacientesInteres = d.pacientes_por_interes || {};
  renderRespuestasPacientes();
  renderWebhookSalud(d.webhook || {});
}

function renderWebhookSalud(w) {
  const el = $("#webhookSalud");
  if (!el) return;
  const horas = w.hace_horas;
  const stale = horas == null || horas > 24;
  el.hidden = false;
  el.classList.toggle("webhook-salud--alerta", stale);
  if (!w.total_eventos) {
    el.textContent = "⚠ Nunca se ha recibido un evento del webhook de WhatsApp. Revisa en Meta que el webhook esté configurado y suscrito al campo «messages», y que el servidor esté arriba.";
  } else if (stale) {
    el.textContent = `⚠ El webhook de WhatsApp no recibe eventos desde hace ${horas} h (último: ${w.ultimo_evento}). Si esperabas respuestas, revisa el webhook en Meta y que  el servidor siga corriendo.`;
  } else {
    el.textContent = `Webhook de WhatsApp activo · último evento hace ${horas} h (${w.ultimo_evento}) · ${num(w.total_eventos)} en total.`;
  }
}

const RESP_CATS = [
  { cat: "pendiente", label: "No han respondido" },
  { cat: "respondio", label: "Respondieron" },
  { cat: "baja", label: "Se dieron de baja" },
];
const INTERES_CATS = [
  { cat: "interesado", label: "Interesados" },
  { cat: "no_interesado", label: "No interesados" },
  { cat: "sin_clasificar", label: "Sin clasificar" },
];
let datosPacientesResp = {};
let datosPacientesInteres = {};
let respCatSel = "todos";
const selTipoPacientes = $("#selTipoPacientes");
selTipoPacientes.addEventListener("change", () => {
  respCatSel = "todos";
  renderRespuestasPacientes();
});

function renderRespuestasPacientes() {
  const el = $("#respuestasPacientes");
  const porInteres = selTipoPacientes.value === "interes";
  const categorias = porInteres ? INTERES_CATS : RESP_CATS;
  const r = porInteres ? datosPacientesInteres : datosPacientesResp;
  const total = Number(r.total) || 0;
  if (!total) {
    el.innerHTML = '<p class="mes-vacio" style="padding:14px 18px;">Sin pacientes con envíos registrados.</p>';
    return;
  }
  const maxCat = Math.max(...categorias.map((c) => r[c.cat] || 0), 1);

  const toggles =
    `<button type="button" class="stat" data-cat="todos">Todos <strong>${num(total)}</strong></button>` +
    categorias.map(
      (c) =>
        `<button type="button" class="stat stat--resp" data-respuesta="${c.cat}" data-cat="${c.cat}">` +
        `${c.label} <strong>${num(r[c.cat] || 0)}</strong></button>`
    ).join("");

  const filas = categorias.map((c) => {
    const v = r[c.cat] || 0;
    const pct = Math.round((v / total) * 100);
    return `
      <div class="comp-fila" data-cat="${c.cat}">
        <span class="comp-nombre">${c.label}</span>
        <span class="comp-barra comp-barra--${c.cat}"><span style="width:${Math.round((v / maxCat) * 100)}%"></span></span>
        <span class="comp-num"><strong>${num(v)}</strong> · ${pct}%</span>
      </div>`;
  }).join("");

  el.innerHTML =
    `<div class="resp-toggles" id="respToggles">${toggles}</div>` +
    `<div class="comp" id="respComp">${filas}</div>`;

  document.querySelectorAll("#respToggles .stat").forEach((b) =>
    b.addEventListener("click", () => aplicarRespCat(b.dataset.cat))
  );
  aplicarRespCat(respCatSel);
}

function aplicarRespCat(cat) {
  respCatSel = cat;
  document.querySelectorAll("#respToggles .stat").forEach((b) => {
    const activo = b.dataset.cat === cat;
    b.classList.toggle("activo", activo);
    b.setAttribute("aria-pressed", String(activo));
  });
  document.querySelectorAll("#respComp .comp-fila").forEach((f) =>
    f.classList.toggle("atenuada", cat !== "todos" && f.dataset.cat !== cat)
  );
}

/* ================================================================== *
 *  Gráficos de barras por periodo (día / mes / año)
 * ================================================================== */
const ES_ADMIN = !!window.snwPuede && window.snwPuede("tarifas_editar");

const CAT_LABEL = {
  marketing: "Marketing",
  utility: "Utilidad",
  authentication: "Autenticación",
  service: "Servicio",
};

function fmtMoneda(n, mon) {
  try {
    return new Intl.NumberFormat("es-CL", {
      style: "currency", currency: mon || "USD", maximumFractionDigits: 2,
    }).format(Number(n || 0));
  } catch {
    console.error("[estadisticas.js fmtMoneda()]");
    return `${num(n)} ${mon || ""}`.trim();
  }
}

function etiquetaPeriodo(p, gran) {
  if (gran === "mes") return nombreMes(p);
  if (gran === "anio") return String(p);
  const [a, m, d] = String(p).split("-");
  return `${d} ${(MESES[(Number(m) || 1) - 1] || "").slice(0, 3)} ${a}`;
}

// Tope "redondo" del eje Y, un poco por encima del valor máximo, para que
// la barra más alta y su etiqueta quepan bajo la línea superior.
function techoLindo(v) {
  if (!v || v <= 0) return 0;
  const obj = v * 1.12;
  const mag = Math.pow(10, Math.floor(Math.log10(obj)));
  const n = obj / mag;
  const paso = [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].find((s) => n <= s) || 10;
  return paso * mag;
}

// Etiqueta compacta para el eje del gráfico de barras.
function etiquetaCorta(p, gran) {
  if (gran === "anio") return String(p);
  const [a, m, d] = String(p).split("-");
  const mes = (MESES[(Number(m) || 1) - 1] || "").slice(0, 3);
  return gran === "dia" ? `${Number(d)} ${mes}` : `${mes} ${a}`;
}

/**
 * Dibuja un gráfico de barras verticales con eje Y y líneas de referencia.
 *   el     : contenedor .grafico-barras
 *   items  : [{ periodo, valor, sub? }]
 *   opts   : { gran, fmtValor(v), vacio }
 */
function pintarGrafico(el, items, { gran, fmtValor, vacio }) {
  if (!items.length) {
    el.innerHTML = `<p class="mes-vacio" style="padding:8px 18px;">${vacio || "Sin datos."}</p>`;
    return;
  }
  const max = Math.max(...items.map((i) => i.valor), 0);
  const techo = techoLindo(max);
  const PASOS = 4;

  const eje = [];
  for (let i = PASOS; i >= 0; i--) eje.push(`<span>${fmtValor((techo * i) / PASOS)}</span>`);
  const lineas = Array.from({ length: PASOS + 1 }, () => "<i></i>").join("");

  const cols = items
    .map((it) => {
      const pct = techo ? Math.max(1.5, (it.valor / techo) * 100) : 0;
      const tip = `${etiquetaPeriodo(it.periodo, gran)}: ${fmtValor(it.valor)}` +
        (it.sub ? ` · ${it.sub}` : "");
      return `
      <div class="gb-col" title="${tip}">
        <span class="gb-barra-zona">
          <span class="gb-barra" style="height:${pct}%">
            <span class="gb-valor">${fmtValor(it.valor)}</span>
          </span>
        </span>
        <span class="gb-label">${etiquetaCorta(it.periodo, gran)}</span>
      </div>`;
    })
    .join("");

  el.innerHTML =
    `<div class="gb-eje">${eje.join("")}</div>` +
    `<div class="gb-area"><div class="gb-lineas">${lineas}</div>` +
    `<div class="gb-cols">${cols}</div></div>`;
}

/* -- Mensajes enviados (para todos los usuarios) -------------------- */
let granEnvios = "mes";
let solicitudEnvios = 0;
let aniosConEnvios = new Set();
let mesesConEnvios = new Set();
let diasConEnvios = new Set();

async function cargarEnvios(gran) {
  granEnvios = filtroCalendarioCostos?.modo || gran;
  const solicitud = ++solicitudEnvios;
  const inicioCarga = iniciarCargaEstadisticas();
  try {
    const qsPeriodos = filtroCalendarioCostos
      ? `&periodos=${encodeURIComponent(filtroCalendarioCostos.valores.join(","))}` : "";
    const res = await fetch(`api/estadisticas/envios?granularidad=${granEnvios}${qsEspEst()}${qsPeriodos}`, {
      headers: authHeaders(), cache: "no-store",
    });
    if (res.status === 401) { window.snwSesionExpirada(); return; }
    if (!res.ok) throw new Error();
    const d = await res.json();
    if (solicitud !== solicitudEnvios) return;
    aniosConEnvios = new Set((d.anios_disponibles || []).map(Number));
    mesesConEnvios = new Set(d.meses_disponibles || []);
    diasConEnvios = new Set(d.dias_disponibles || []);
    const anios = [...aniosConEnvios].sort((a, b) => a - b);
    if (anios.length && !aniosConEnvios.has(estadoCalendarioCostos.anio)) {
      estadoCalendarioCostos.anio = anios[anios.length - 1];
      estadoCalendarioCostos.mes = 0;
    }
    const mesesDelAnio = [...mesesConEnvios].filter((m) => m.startsWith(`${estadoCalendarioCostos.anio}-`)).sort();
    const mesVisible = `${estadoCalendarioCostos.anio}-${String(estadoCalendarioCostos.mes + 1).padStart(2, "0")}`;
    if (mesesDelAnio.length && !mesesConEnvios.has(mesVisible)) {
      estadoCalendarioCostos.mes = Number(mesesDelAnio[mesesDelAnio.length - 1].slice(5)) - 1;
    }
    pintarCalendarioCostos();
    pintarGrafico($("#enviosGrafico"), (d.filas || []).map((f) => ({ periodo: f.periodo, valor: f.enviados })), {
      gran: d.granularidad,
      fmtValor: (v) => num(Math.round(v)),
      vacio: d.periodos?.length ? "Sin mensajes enviados en los períodos seleccionados." : "Sin mensajes enviados en este periodo.",
    });
  } catch {
    if (solicitud !== solicitudEnvios) return;
    console.error("[estadisticas.js cargarEnvios()]");
    $("#enviosGrafico").innerHTML =
      '<p class="mes-vacio" style="padding:8px 18px;">No se pudo cargar el gráfico.</p>';
  } finally {
    await terminarCargaEstadisticas(inicioCarga);
  }
}

/* -- Costos de mensajes de WhatsApp (solo administrador) ----------- */
let granCostos = "mes";
let solicitudCostos = 0;
let filtroCalendarioCostos = null;
const hoyCalendario = new Date();
const estadoCalendarioCostos = {
  modo: "dia",
  anio: hoyCalendario.getFullYear(),
  mes: hoyCalendario.getMonth(),
  seleccion: { dia: new Set(), mes: new Set(), anio: new Set() },
};
let catCostos = localStorage.getItem("snw_cat_costos") || "todas";
if (["todas", "marketing", "service"].indexOf(catCostos) === -1) {
  catCostos = "todas";
}

const dosDigitos = (n) => String(n).padStart(2, "0");
const etiquetaModoCalendario = { dia: "días", mes: "meses", anio: "años" };
const etiquetaModoCalendarioSingular = { dia: "día", mes: "mes", anio: "año" };

function cerrarCalendarioCostos(devolverFoco = false) {
  $("#calendarioCostos").hidden = true;
  $("#abrirCalendarioCostos").setAttribute("aria-expanded", "false");
  if (devolverFoco) $("#abrirCalendarioCostos").focus();
}

function actualizarResumenCalendarioCostos() {
  const modo = estadoCalendarioCostos.modo;
  const seleccion = [...estadoCalendarioCostos.seleccion[modo]].sort();
  const resumen = seleccion.slice(0, 4).map((v) =>
    modo === "dia" ? fechaDMA(v) : modo === "mes" ? nombreMes(v) : v
  ).join(" · ");
  $("#calendarioCostosSeleccion").textContent = seleccion.length
    ? `${seleccion.length} ${etiquetaModoCalendario[modo]}: ${resumen}${seleccion.length > 4 ? "…" : ""}`
    : "Selecciona uno o varios períodos.";
}

function pintarCalendarioCostos() {
  const { modo, anio, mes, seleccion } = estadoCalendarioCostos;
  document.querySelectorAll("#calendarioCostos [data-cal-modo]").forEach((b) => {
    b.setAttribute("aria-selected", String(b.dataset.calModo === modo));
  });
  const grid = $("#calendarioCostosGrid");
  grid.className = `costos-calendario__grid costos-calendario__grid--${modo}`;
  const botones = [];
  const anios = [...aniosConEnvios].sort((a, b) => a - b);
  const minimo = anios[0];
  const maximo = anios[anios.length - 1];
  if (modo === "dia") {
    $("#calendarioCostosTitulo").textContent = `${MESES[mes]} ${anio}`;
    ["Lu", "Ma", "Mi", "Ju", "Vi", "Sá", "Do"].forEach((d) =>
      botones.push(`<span class="costos-calendario__semana">${d}</span>`));
    const huecos = (new Date(anio, mes, 1).getDay() + 6) % 7;
    for (let i = 0; i < huecos; i++) botones.push('<span aria-hidden="true"></span>');
    const ultimo = new Date(anio, mes + 1, 0).getDate();
    for (let dia = 1; dia <= ultimo; dia++) {
      const valor = `${anio}-${dosDigitos(mes + 1)}-${dosDigitos(dia)}`;
      const hoy = anio === hoyCalendario.getFullYear() && mes === hoyCalendario.getMonth()
        && dia === hoyCalendario.getDate();
      botones.push(`<button type="button" data-cal-valor="${valor}" aria-label="${dia} de ${MESES[mes]} de ${anio}" ` +
        `aria-pressed="${seleccion.dia.has(valor)}" data-hoy="${hoy}"` +
        `${diasConEnvios.has(valor) ? "" : " disabled"}>${dia}</button>`);
    }
  } else if (modo === "mes") {
    $("#calendarioCostosTitulo").textContent = String(anio);
    MESES.forEach((nombre, i) => {
      const valor = `${anio}-${dosDigitos(i + 1)}`;
      botones.push(`<button type="button" data-cal-valor="${valor}" ` +
        `aria-pressed="${seleccion.mes.has(valor)}"` +
        `${mesesConEnvios.has(valor) ? "" : " disabled"}>${nombre}</button>`);
    });
  } else {
    const inicio = Math.floor(anio / 12) * 12;
    $("#calendarioCostosTitulo").textContent = `${inicio}–${inicio + 11}`;
    for (let i = 0; i < 12; i++) {
      const valor = String(inicio + i);
      botones.push(`<button type="button" data-cal-valor="${valor}" ` +
        `aria-pressed="${seleccion.anio.has(valor)}"` +
        `${aniosConEnvios.has(Number(valor)) ? "" : " disabled"}>${valor}</button>`);
    }
  }
  grid.innerHTML = botones.join("");
  document.querySelectorAll("#calendarioCostos [data-cal-nav]").forEach((b) => {
    const dir = Number(b.dataset.calNav);
    const destino = modo === "dia" ? new Date(anio, mes + dir, 1).getFullYear()
      : modo === "mes" ? anio + dir : Math.floor(anio / 12) * 12 + dir * 12;
    b.disabled = !anios.length || (modo === "anio"
      ? !anios.some((a) => a >= destino && a <= destino + 11)
      : destino < minimo || destino > maximo);
  });
  actualizarResumenCalendarioCostos();
}

function limpiarFiltroCalendarioCostos() {
  filtroCalendarioCostos = null;
  Object.values(estadoCalendarioCostos.seleccion).forEach((s) => s.clear());
  $("#resumenCalendarioCostos").textContent = "Elegir fechas";
  $("#limpiarFechaCostos").hidden = true;
  pintarCalendarioCostos();
}

async function cargarTarifas() {
  const inicioCarga = iniciarCargaEstadisticas();
  try {
    const res = await fetch("api/tarifas", { headers: authHeaders(), cache: "no-store" });
    if (res.status === 401) { window.snwSesionExpirada(); return; }
    if (!res.ok) throw new Error();
    renderTarifas(await res.json());
  } catch {
    console.error("[estadisticas.js cargarTarifas()]");
    $("#tarifasVigente").innerHTML =
      '<p class="mes-vacio" style="padding:8px 0;">No se pudieron cargar las tarifas.</p>';
  } finally {
    await terminarCargaEstadisticas(inicioCarga);
  }
}

// Meta limita a una descarga del CSV de tarifas por día: en vez del
// cooldown fijo de 5s de los demás botones, este se deshabilita según la
// fecha real del último intento (ultimo_intento_ms, del backend) hasta que
// pasen 24h — mismo criterio que el enfriamiento de 24h de plantillas.
// Ojo: es el último INTENTO, no la última fila nueva guardada (esa es
// ultima_descarga_ms) — la tarifa de Meta rara vez cambia, así que con
// INSERT IGNORE casi siempre no hay fila nueva aunque la descarga (la
// scrapeada a la página de Meta) sí se haya repetido.
const MS_24H_TARIFAS = 24 * 3600 * 1000;
let _timerCooldownTarifas = null;
function actualizarBotonTarifas(ultimoIntentoMs) {
  const btn = $("#btnActualizarTarifas");
  if (!btn) return;
  if (_timerCooldownTarifas) { clearInterval(_timerCooldownTarifas); _timerCooldownTarifas = null; }
  const pintar = () => {
    const restante = ultimoIntentoMs ? MS_24H_TARIFAS - (Date.now() - ultimoIntentoMs) : 0;
    if (restante <= 0) {
      if (_timerCooldownTarifas) { clearInterval(_timerCooldownTarifas); _timerCooldownTarifas = null; }
      btn.disabled = false;
      btn.textContent = "Actualizar tarifas";
      btn.title = "";
      return;
    }
    const horas = String(Math.floor(restante / 3600000)).padStart(2, "0");
    const minutos = String(Math.floor((restante % 3600000) / 60000)).padStart(2, "0");
    btn.disabled = true;
    btn.textContent = `Actualizar tarifas (${horas}:${minutos})`;
    btn.title = "Meta solo permite descargar el CSV de tarifas una vez al día.";
  };
  pintar();
  _timerCooldownTarifas = setInterval(pintar, 30000);
}

function renderTarifas(d) {
  actualizarBotonTarifas(d.ultimo_intento_ms);
  const v = d.vigente;
  const alerta = $("#tarifasAlerta");
  alerta.hidden = true;
  alerta.classList.remove("webhook-salud--alerta");

  if (d.nunca_descargada) {
    alerta.hidden = false;
    alerta.classList.add("webhook-salud--alerta");
    alerta.textContent =
      "⚠ Todavía no se han descargado las tarifas de Meta. Pulsa «Actualizar tarifas».";
  } else if (d.proxima) {
    alerta.hidden = false;
    alerta.classList.add("webhook-salud--alerta");
    alerta.textContent =
      `⚠ Meta publicó una tarifa nueva que entra en vigor el ${fechaDMA(d.proxima.efectiva_desde)}: ` +
      `Marketing ${fmtMoneda(d.proxima.marketing, d.proxima.moneda)}` +
      (d.proxima.service > 0 ? ` · Servicio ${fmtMoneda(d.proxima.service, d.proxima.moneda)}` : "") +
      ".";
  }

  if (!v) {
    $("#tarifasVigente").innerHTML =
      '<p class="mes-vacio" style="padding:8px 0;">Sin tarifas guardadas todavía.</p>';
    $("#tarifasPie").textContent = "";
    return;
  }

  const cats = ["marketing", "service"];
  // Servicio: tarifa explícita si trae valor; si no, la de utility
  // (regla de Meta: el servicio cuesta lo mismo que utility/auth).
  const srvExplicito = v.service > 0;
  const srvRate = srvExplicito ? v.service
    : (v.utility > 0 ? v.utility : (v.authentication > 0 ? v.authentication : null));
  const precio = (c) => {
    if (c === "service") return srvRate != null ? fmtMoneda(srvRate, v.moneda) : "—";
    return v[c] != null ? fmtMoneda(v[c], v.moneda) : "—";
  };
  $("#tarifasVigente").innerHTML = cats
    .map(
      (c) => `
      <div class="tarifa-card">
        <span class="tarifa-card__cat">${CAT_LABEL[c]}</span>
        <span class="tarifa-card__precio">${precio(c)}</span>
        <span class="tarifa-card__unidad">por mensaje${c === "service" && !srvExplicito && srvRate != null ? " (= Utility)" : ""}</span>
      </div>`
    )
    .join("");

  $("#tarifasPie").innerHTML =
    `Moneda de facturación: <strong>${d.moneda}</strong> · ` +
    `vigente desde ${fechaDMA(v.efectiva_desde)} · ` +
    `actualizado ${d.ultima_descarga || "—"} · ` +
    '<a href="#" id="btnCsvChile">Descargar CSV de Chile</a>' +
    (!srvExplicito && srvRate != null
      ? '<br><span class="costos-nota">Servicio sin tarifa propia en este card: se calcula con la de Utility (regla de Meta; cobra desde el 01-10-2026).</span>'
      : "");
  $("#btnCsvChile").addEventListener("click", descargarCsvChile);
}

async function descargarCsvChile(e) {
  e.preventDefault();
  try {
    const res = await fetch("api/tarifas/chile.csv", { headers: authHeaders() });
    if (!res.ok) throw new Error();
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "whatsapp_tarifas_chile.csv";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  } catch {
    console.error("[estadisticas.js descargarCsvChile()]");
    toast("No se pudo descargar el CSV.", "error");
  }
}

async function actualizarTarifas() {
  const inicioCarga = iniciarCargaEstadisticas();
  const btn = $("#btnActualizarTarifas");
  const textoOriginal = btn.textContent;
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Actualizando…';
  try {
    const res = await fetch("api/tarifas/actualizar", { method: "POST", headers: authHeaders() });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) {
      const error = new Error(d.detail || `HTTP ${res.status}`);
      error.status = res.status;
      throw error;
    }
    toast(
      d.cambio
        ? `Tarifas actualizadas: ${d.nuevas} nueva(s). Moneda: ${d.moneda}.`
        : `Las tarifas ya estaban al día. Moneda: ${d.moneda}.`,
      "ok"
    );
    // cargarTarifas() -> renderTarifas() -> actualizarBotonTarifas() deja el
    // botón en su estado final real (habilitado, o en cooldown de 24h con
    // la cuenta regresiva), así que acá no hace falta restaurarlo a mano.
    await cargarTarifas();
    await cargarCostos(granCostos);
  } catch (err) {
    console.error("[estadisticas.js actualizarTarifas()]", {
      estado: err.status || "sin respuesta HTTP",
      detalle: err.message,
    }, err);
    toast("No se pudieron actualizar las tarifas. " + (err.message || ""), "error");
    btn.disabled = false;
    btn.textContent = textoOriginal;
  } finally {
    await terminarCargaEstadisticas(inicioCarga);
  }
}

async function cargarCostos(gran) {
  granCostos = filtroCalendarioCostos?.modo || gran;
  const solicitud = ++solicitudCostos;
  const inicioCarga = iniciarCargaEstadisticas();
  try {
    const qsCat = catCostos !== "todas" ? `&categoria=${encodeURIComponent(catCostos)}` : "";
    const qsPeriodos = filtroCalendarioCostos
      ? `&periodos=${encodeURIComponent(filtroCalendarioCostos.valores.join(","))}` : "";
    const res = await fetch(`api/estadisticas/costos?granularidad=${granCostos}${qsEspEst()}${qsCat}${qsPeriodos}`, {
      headers: authHeaders(), cache: "no-store",
    });
    if (res.status === 401) { window.snwSesionExpirada(); return; }
    if (!res.ok) throw new Error();
    const datos = await res.json();
    if (solicitud !== solicitudCostos) return;
    renderCostos(datos);
  } catch {
    if (solicitud !== solicitudCostos) return;
    console.error("[estadisticas.js cargarCostos()]");
    $("#costosBarras").innerHTML =
      '<p class="mes-vacio" style="padding:8px 18px;">No se pudieron cargar los costos.</p>';
    $("#costosTotal").textContent = "";
  } finally {
    await terminarCargaEstadisticas(inicioCarga);
  }
}

function renderCostos(d) {
  const filas = Array.isArray(d.filas) ? d.filas : [];
  const mon = d.moneda;
  const servicio = d.servicio || { entregados: 0, gratuitos: 0, pagados: 0 };
  const t = d.total || { costo: 0, mensajes: 0, excluidos: 0, por_categoria: {} };
  const filasResumen = [
    ["Costo total estimado", d.sin_tarifas ? "Sin tarifas disponibles" : fmtMoneda(t.costo, mon)],
    ["Mensajes registrados", num(t.mensajes)],
    ...Object.entries(t.por_categoria || {}).filter(([, n]) => n)
      .map(([categoria, cantidad]) => [CAT_LABEL[categoria] || categoria, num(cantidad)]),
  ];
  if (servicio.entregados) {
    filasResumen.push(["Servicio entregado", num(servicio.entregados)]);
    filasResumen.push(["Servicio gratuito", `${num(servicio.gratuitos)} de 1.000 por mes`]);
    filasResumen.push(["Servicio pagado", num(servicio.pagados)]);
  }
  if (servicio.emisor_desconocido) {
    filasResumen.push(["Entregas antiguas sin emisor", `${num(servicio.emisor_desconocido)} · ${servicio.emisor_unico_inferido
      ? "atribuidas al único número de empresa registrado"
      : "agrupadas por separado; cifra estimada"}`]);
  }
  if (t.excluidos) filasResumen.push(["Sin categoría de facturación", `${num(t.excluidos)} · no incluidos en el costo`]);
  // El motor simulado no deja message_id y por eso no se factura; el backend
  // ya calcula cuántos hubo en el período y aquí se hace explícito.
  if (t.simulados) filasResumen.push(["Envíos simulados", `${num(t.simulados)} · no facturables (motor simulado)`]);
  $("#costosTotal").innerHTML = `<table><caption>Resumen del período seleccionado</caption><thead><tr><th scope="col">Concepto</th><th scope="col">Cantidad o costo</th></tr></thead><tbody>${filasResumen.map(([concepto, valor]) =>
    `<tr><th scope="row">${escaparHtml(concepto)}</th><td>${escaparHtml(valor)}</td></tr>`).join("")}</tbody></table>`;

  if (d.sin_tarifas) {
    $("#costosBarras").innerHTML =
      '<p class="mes-vacio" style="padding:8px 18px;">Sin tarifas guardadas: no se puede estimar el costo. Pulsa «Actualizar tarifas».</p>';
    return;
  }
  if (!filas.length) {
    $("#costosBarras").innerHTML =
      `<p class="mes-vacio" style="padding:8px 18px;">${d.periodos?.length ? "Sin mensajes registrados en los períodos seleccionados." : "Sin mensajes enviados todavía."}</p>`;
    return;
  }

  pintarGrafico(
    $("#costosBarras"),
    filas.map((f) => ({
      periodo: f.periodo,
      valor: f.costo,
      sub: `${num(f.mensajes)} mensaje(s)` +
        (f.servicio?.entregados
          ? ` · servicio: ${num(f.servicio.gratuitos)} gratis / ${num(f.servicio.pagados)} pagados`
          : ""),
    })),
    { gran: d.granularidad, fmtValor: (v) => fmtMoneda(v, mon) }
  );

}

if (ES_ADMIN && $("#panelCostos")) {
  // El cooldown de este botón es el de 24h por fecha real (ver
  // actualizarBotonTarifas), no el genérico de 5s: acá alcanza con no
  // dejar clickear un botón ya deshabilitado; el spinner mientras se
  // actualiza de verdad vive adentro de actualizarTarifas().
  $("#btnActualizarTarifas").addEventListener("click", () => {
    if ($("#btnActualizarTarifas").disabled) return;
    actualizarTarifas();
  });
  const selCatCostos = $("#selCatCostos");
  if (selCatCostos) {
    selCatCostos.value = catCostos;
    selCatCostos.addEventListener("change", () => {
      catCostos = selCatCostos.value;
      localStorage.setItem("snw_cat_costos", catCostos);
      cargarCostos(granCostos);
    });
  }
}

if ($("#calendarioCostos")) {
  const abrirCalendario = $("#abrirCalendarioCostos");
  const calendario = $("#calendarioCostos");
  const limpiarFechaCostos = $("#limpiarFechaCostos");
  function centrarCalendarioMovil() {
    const grupo = calendario.parentElement;
    const anchoVisible = window.visualViewport?.width || window.innerWidth;
    const inicioVisible = window.visualViewport?.offsetLeft || 0;
    calendario.style.setProperty("--calendario-centro-x", `${inicioVisible + anchoVisible / 2 - grupo.getBoundingClientRect().left}px`);
  }
  abrirCalendario.addEventListener("click", () => {
    calendario.hidden = !calendario.hidden;
    abrirCalendario.setAttribute("aria-expanded", String(!calendario.hidden));
    if (!calendario.hidden) {
      centrarCalendarioMovil();
      pintarCalendarioCostos();
      calendario.querySelector('[data-cal-modo][aria-selected="true"]').focus();
    }
  });
  window.addEventListener("resize", centrarCalendarioMovil);
  limpiarFechaCostos.addEventListener("click", () => {
    limpiarFiltroCalendarioCostos();
    cerrarCalendarioCostos();
    cargarEnvios("mes");
    if (ES_ADMIN && $("#panelCostos")) cargarCostos("mes");
  });
  document.querySelectorAll("#calendarioCostos [data-cal-modo]").forEach((b) =>
    b.addEventListener("click", () => {
      estadoCalendarioCostos.modo = b.dataset.calModo;
      pintarCalendarioCostos();
    })
  );
  document.querySelectorAll("#calendarioCostos [data-cal-nav]").forEach((b) =>
    b.addEventListener("click", () => {
      const dir = Number(b.dataset.calNav);
      if (estadoCalendarioCostos.modo === "dia") {
        const nueva = new Date(estadoCalendarioCostos.anio, estadoCalendarioCostos.mes + dir, 1);
        estadoCalendarioCostos.anio = nueva.getFullYear();
        estadoCalendarioCostos.mes = nueva.getMonth();
      } else {
        estadoCalendarioCostos.anio += dir * (estadoCalendarioCostos.modo === "mes" ? 1 : 12);
      }
      pintarCalendarioCostos();
    })
  );
  $("#calendarioCostosGrid").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-cal-valor]");
    if (!b || b.disabled) return;
    const grupo = estadoCalendarioCostos.seleccion[estadoCalendarioCostos.modo];
    if (grupo.has(b.dataset.calValor)) grupo.delete(b.dataset.calValor);
    else if (grupo.size < 120) grupo.add(b.dataset.calValor);
    else { toast("Puedes seleccionar hasta 120 períodos.", "error"); return; }
    b.setAttribute("aria-pressed", String(grupo.has(b.dataset.calValor)));
    actualizarResumenCalendarioCostos();
  });
  $("#calendarioCostosLimpiar").addEventListener("click", () => {
    estadoCalendarioCostos.seleccion[estadoCalendarioCostos.modo].clear();
    pintarCalendarioCostos();
  });
  $("#calendarioCostosAplicar").addEventListener("click", () => {
    const modo = estadoCalendarioCostos.modo;
    const valores = [...estadoCalendarioCostos.seleccion[modo]].sort();
    filtroCalendarioCostos = valores.length ? { modo, valores } : null;
    $("#resumenCalendarioCostos").textContent = valores.length
      ? `${valores.length} ${valores.length === 1 ? etiquetaModoCalendarioSingular[modo] : etiquetaModoCalendario[modo]} ` +
        `seleccionado${valores.length === 1 ? "" : "s"}`
      : "Elegir fechas";
    limpiarFechaCostos.hidden = !valores.length;
    cerrarCalendarioCostos(true);
    cargarEnvios(valores.length ? modo : "mes");
    if (ES_ADMIN && $("#panelCostos")) cargarCostos(valores.length ? modo : "mes");
  });
  document.addEventListener("click", (e) => {
    if (!calendario.hidden && !calendario.contains(e.target) && !abrirCalendario.contains(e.target))
      cerrarCalendarioCostos();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !calendario.hidden) cerrarCalendarioCostos(true);
  });
}

window.snwConCooldown($("#btnActualizarEstadisticas"), () =>
  $("#statsTabCampanas")?.classList.contains("activo") ? cargarCampanas(false) : cargar(true));
const barraSeries = $("#seriesTabs");
function posicionarIndicadorSeries() {
  const activa = barraSeries?.querySelector(".stats-series-tab.activo");
  if (!activa) return;
  const rectBarra = barraSeries.getBoundingClientRect();
  const rectTab = activa.getBoundingClientRect();
  barraSeries.style.setProperty("--msg-indicador-x", `${rectTab.left - rectBarra.left - barraSeries.clientLeft}px`);
  barraSeries.style.setProperty("--msg-indicador-ancho", `${rectTab.width}px`);
}
if (barraSeries) {
  barraSeries.querySelectorAll(".stats-series-tab").forEach((b) => b.addEventListener("click", () => {
    if (b.classList.contains("activo")) return;
    const panelDestino = b.dataset.serie === "envios" ? $("#panelEnvios") : $("#panelCostos");
    panelDestino?.style.setProperty(
      "--stats-panel-entrada-x", b.dataset.serie === "costos" ? "20px" : "-20px"
    );
    barraSeries.querySelectorAll(".stats-series-tab").forEach((tab) => {
      const activa = tab === b;
      tab.classList.toggle("activo", activa);
      tab.setAttribute("aria-selected", String(activa));
      tab.tabIndex = activa ? 0 : -1;
    });
    $("#panelEnvios").hidden = b.dataset.serie !== "envios";
    if ($("#panelCostos")) $("#panelCostos").hidden = b.dataset.serie !== "costos";
    posicionarIndicadorSeries();
  }));
  window.addEventListener("resize", posicionarIndicadorSeries);
  requestAnimationFrame(posicionarIndicadorSeries);
}

/* -- Pestañas principales: Envíos / Respuestas ---------------------- */
const statsTabs = $("#statsTabs");
function posicionarIndicadorStatsTabs() {
  const activa = statsTabs?.querySelector(".stats-series-tab.activo");
  if (!activa) return;
  const rectBarra = statsTabs.getBoundingClientRect();
  const rectTab = activa.getBoundingClientRect();
  statsTabs.style.setProperty("--msg-indicador-x", `${rectTab.left - rectBarra.left - statsTabs.clientLeft}px`);
  statsTabs.style.setProperty("--msg-indicador-ancho", `${rectTab.width}px`);
}
function activarTabStats(stab, animar = true) {
  if (!statsTabs) return;
  const boton = statsTabs.querySelector(`[data-stab="${stab}"]`) || statsTabs.querySelector("[data-stab]");
  if (!boton) return;
  const tabs = [...statsTabs.querySelectorAll(".stats-series-tab")];
  const anterior = tabs.findIndex(t => t.classList.contains("activo"));
  const destino = document.getElementById(boton.getAttribute("aria-controls"));
  if (animar) destino?.style.setProperty("--stats-panel-entrada-x", tabs.indexOf(boton) > anterior ? "20px" : "-20px");
  statsTabs.querySelectorAll(".stats-series-tab").forEach((tab) => {
    const activa = tab === boton;
    tab.classList.toggle("activo", activa);
    tab.setAttribute("aria-selected", String(activa));
    tab.tabIndex = activa ? 0 : -1;
  });
  $("#stabEnvios").hidden = boton.dataset.stab !== "envios";
  $("#stabRespuestas").hidden = boton.dataset.stab !== "respuestas";
  $("#stabCampanas").hidden = boton.dataset.stab !== "campanas";
  posicionarIndicadorStatsTabs();
  if (paginaEstadisticasLista && boton.dataset.stab === "respuestas" && !botonesCargados) {
    cargarBotones();
  }
  if (paginaEstadisticasLista && boton.dataset.stab === "campanas" && !campanasCargadas) cargarCampanas(true);
}
if (statsTabs) {
  statsTabs.querySelectorAll(".stats-series-tab").forEach((b) => b.addEventListener("click", () => {
    if (b.classList.contains("activo")) return;
    activarTabStats(b.dataset.stab);
    try { localStorage.setItem("snw_stats_tab", b.dataset.stab); } catch { /* sin almacenamiento */ }
  }));
  window.addEventListener("resize", posicionarIndicadorStatsTabs);
  // Pestaña recordada (por defecto Envíos).
  let tabGuardada = "envios";
  try { tabGuardada = localStorage.getItem("snw_stats_tab") || "envios"; } catch { /* sin almacenamiento */ }
  activarTabStats(["respuestas", "campanas"].includes(tabGuardada) ? tabGuardada : "envios", false);
  requestAnimationFrame(posicionarIndicadorStatsTabs);
}

let campanasCargadas = false;
const enlaceEstadisticas = new URLSearchParams(location.search);
let envioEnlacePendiente = /^\d+$/.test(enlaceEstadisticas.get("envio") || "") ? enlaceEstadisticas.get("envio") : null;
let paginaCampanas = 1;
let paginasCampanas = 1;
let consultaCampanas;
let consultaCampana;
let datosCampana = null;
const selectorCampana = $("#selectorCampanaEst");
const tablaCampanas = $("#tablaCampanasEst");
const cuerpoCampanas = $("#campanasTablaBody");
let tipoCampana = "todos";
let filasCampanas = [];
let campanaSeleccionada = null;
let campanaSolicitada = null;
const estadoCampana = $("#campanaEstado");
const resultadosCampana = $("#campanaResultados");

function pintarSelectorCampana() {
  const envio = campanaSeleccionada;
  $("#campanaSeleccionTitulo").textContent = envio
    ? `#${envio.id} · ${envio.plantilla_nombre || "Envío seleccionado"}` : "Elige un envío";
  $("#campanaSeleccionMeta").textContent = envio
    ? [envio.centro, envio.inicio || envio.fecha].filter(Boolean).join(" · ")
    : "Selecciona el tipo y pulsa una fila de la tabla.";
  $("#campanaSeleccionAccion").textContent = envio
    ? selectorCampana.open ? "Ocultar tabla" : "Cambiar envío" : "Ver envíos";
  selectorCampana.classList.toggle("stats-campanas__explorador--seleccionado", !!envio);
}

function pintarTablaCampanas() {
  cuerpoCampanas.replaceChildren();
  if (!filasCampanas.length) {
    cuerpoCampanas.innerHTML = '<tr><td colspan="5" class="stats-campanas__tabla-estado">No hay envíos para este tipo y alcance.</td></tr>';
    return;
  }
  for (const envio of filasCampanas) {
    const fila = document.createElement("tr");
    fila.dataset.campanaId = String(envio.id);
    const seleccionada = String(campanaSeleccionada?.id) === String(envio.id);
    fila.classList.toggle("seleccionada", seleccionada);
    if (seleccionada) fila.setAttribute("aria-current", "true");
    const tipo = { manual: "Manual", programado: "Programado", reagendamiento: "Reagendamiento" }[envio.tipo] || "Envío";
    fila.innerHTML = `<td><button type="button" class="stats-campanas__elegir" data-elegir-campana="${Number(envio.id)}">${escaparHtml(envio.plantilla_nombre || "Sin nombre")}</button>` +
      `<span class="stats-campanas__fila-meta">#${Number(envio.id)} · ${tipo}${seleccionada ? " · Seleccionado" : ""}</span></td>` +
      `<td>${escaparHtml(envio.centro || "Sin registro")}</td><td class="stats-campanas__fila-fecha">${escaparHtml(envio.fecha)}</td>` +
      `<td class="stats-campanas__fila-numero">${num(envio.total_pacientes)}</td><td><i class="fa-solid ${String(campanaSolicitada) === String(envio.id) ? "fa-spinner fa-spin" : seleccionada ? "fa-circle-check" : "fa-chevron-right"}" aria-hidden="true"></i></td>`;
    cuerpoCampanas.appendChild(fila);
  }
}

async function cargarCampanas(reiniciar = false) {
  consultaCampanas?.abort();
  consultaCampana?.abort();
  consultaCampana = null;
  campanaSolicitada = null;
  const control = new AbortController();
  consultaCampanas = control;
  if (reiniciar) {
    paginaCampanas = 1;
    campanaSeleccionada = null;
    datosCampana = null;
    resultadosCampana.hidden = true;
    $("#filtrosCampana").hidden = true;
    selectorCampana.open = true;
  }
  pintarSelectorCampana();
  tablaCampanas.setAttribute("aria-busy", "true");
  cuerpoCampanas.innerHTML = '<tr><td colspan="5" class="stats-campanas__tabla-estado"><i class="fa-solid fa-spinner fa-spin" aria-hidden="true"></i> Cargando envíos…</td></tr>';
  estadoCampana.textContent = "Cargando envíos…";
  $("#campanasAnterior").disabled = true;
  $("#campanasSiguiente").disabled = true;
  $("#campanasPrimera").disabled = true;
  $("#campanasUltima").disabled = true;
  try {
    const q = encodeURIComponent($("#buscarCampanaEst").value.trim());
    const res = await fetch(`api/estadisticas/campanas?pagina=${paginaCampanas}&q=${q}&tipo=${encodeURIComponent(tipoCampana)}${qsEspEst()}`, {
      headers: authHeaders(), signal: control.signal, cache: "no-store",
    });
    const datos = await res.json();
    if (!res.ok) throw new Error(datos.detail || "No se pudieron cargar los envíos.");
    if (control.signal.aborted) return;
    filasCampanas = datos.items;
    pintarTablaCampanas();
    $("#campanasCantidad").textContent = `${num(datos.total)} envío${datos.total === 1 ? "" : "s"}`;
    paginaCampanas = datos.pagina;
    paginasCampanas = datos.paginas;
    $("#campanasPagina").value = paginaCampanas;
    $("#campanasPagina").max = paginasCampanas;
    $("#campanasTotalPaginas").textContent = `de ${paginasCampanas}`;
    $("#campanasAnterior").disabled = paginaCampanas <= 1;
    $("#campanasSiguiente").disabled = !datos.hay_mas;
    $("#campanasPrimera").disabled = paginaCampanas <= 1;
    $("#campanasUltima").disabled = !datos.hay_mas;
    estadoCampana.textContent = datos.items.length ? "Pulsa un envío de la tabla para ver sus resultados." : "No hay envíos en este alcance.";
    campanasCargadas = true;
    if (envioEnlacePendiente) {
      const id = envioEnlacePendiente;
      envioEnlacePendiente = null;
      await cargarCampana(id);
    } else if (campanaSeleccionada && !selectorCampana.open) {
      await cargarCampana(campanaSeleccionada.id);
    }
  } catch (error) {
    if (error.name !== "AbortError") {
      estadoCampana.textContent = error.message;
      cuerpoCampanas.replaceChildren();
      const fila = cuerpoCampanas.insertRow();
      const celda = fila.insertCell();
      celda.colSpan = 5;
      celda.className = "stats-campanas__tabla-estado";
      celda.textContent = "No se pudo cargar la tabla. Pulsa Buscar para volver a intentarlo.";
      selectorCampana.open = true;
    }
  } finally { if (consultaCampanas === control) tablaCampanas.setAttribute("aria-busy", "false"); }
}

async function cargarCampana(envioId) {
  if (!/^\d+$/.test(String(envioId))) return;
  consultaCampana?.abort();
  campanaSolicitada = String(envioId);
  pintarTablaCampanas();
  datosCampana = null;
  $("#filtrosCampana").hidden = true;
  const control = new AbortController();
  consultaCampana = control;
  resultadosCampana.hidden = true;
  estadoCampana.textContent = "Calculando resultados del envío…";
  try {
    const res = await fetch(`api/estadisticas/campanas/${encodeURIComponent(envioId)}`, {
      headers: authHeaders(), signal: control.signal, cache: "no-store",
    });
    const datos = await res.json();
    if (!res.ok) throw new Error(datos.detail || "No se pudieron calcular las estadísticas.");
    if (control.signal.aborted) return;
    datosCampana = datos;
    campanaSeleccionada = datos.envio;
    selectorCampana.open = false;
    pintarSelectorCampana();
    $("#filtroEntregaCampana").value = "todos";
    $("#filtroResultadoCampana").value = "todos";
    $("#filtrosCampana").hidden = false;
    pintarCampana();
    $("#resumenSelectorCampana").focus({ preventScroll: true });
    if (selectorCampana.getBoundingClientRect().top < 0)
      selectorCampana.scrollIntoView({ block: "start", behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  } catch (error) {
    if (error.name !== "AbortError") {
      estadoCampana.textContent = error.message;
      selectorCampana.open = true;
    }
  } finally {
    if (consultaCampana === control) { campanaSolicitada = null; pintarTablaCampanas(); }
  }
}

function pintarCampana() {
    if (!datosCampana) return;
    const datos = window.SNWEstadisticasCampana.filtrar(datosCampana,
      $("#filtroEntregaCampana").value, $("#filtroResultadoCampana").value);
    const r = datos.resumen;
    document.querySelectorAll("[data-filtro-campana]").forEach(boton => {
      const id = boton.dataset.filtroCampana === "entrega" ? "filtroEntregaCampana" : "filtroResultadoCampana";
      boton.setAttribute("aria-pressed", String(document.getElementById(id).value === boton.dataset.valor));
    });
    const etiquetasFiltro = [...document.querySelectorAll('[data-filtro-campana][aria-pressed="true"]')]
      .filter(b => b.dataset.valor !== "todos").map(b => b.textContent.trim());
    $("#resumenFiltrosCampana").textContent = etiquetasFiltro.join(" · ") || "Todos los enviados sin errores";
    $("#filtrosCampana").classList.toggle("stats-campanas__filtros--activos", !!etiquetasFiltro.length);
    const distribucion = window.SNWEstadisticasCampana.comparar(datos, $("#comparacionCampana").value);
    let acumulado = 0;
    const sectores = distribucion.map(d => {
      const inicio = acumulado;
      acumulado += r.validos ? 100 * d.cantidad / r.validos : 0;
      return `${d.color} ${inicio}% ${acumulado}%`;
    }).join(",");
    const coste = datos.costo;
    const valorCoste = coste.importe == null ? "No disponible" : fmtMoneda(coste.importe, coste.moneda);
    resultadosCampana.innerHTML = `<h3>${escaparHtml(datos.envio.plantilla_nombre || "Envío")} · ${escaparHtml(datos.envio.centro)}</h3>` +
      `<div class="stats-campanas__ficha"><span>Envío #${Number(datos.envio.id)}</span><span>Primer envío: ${escaparHtml(datos.envio.inicio)}</span>` +
      `<span>Remitente: ${escaparHtml(datos.envio.numero_emisor || "Sin registro")}</span><span>Plantilla: ${escaparHtml(datos.envio.plantilla_clave || "—")}</span></div>` +
      `<div class="stats-campanas__numeros stats-campanas__numeros--clave">` +
      `<div class="stats-campanas__numero stats-campanas__numero--principal"><span>Tasa de interés</span><strong>${num(r.tasa_interes)} <small>%</small></strong><span>Sobre enviados sin errores</span></div>` +
      `<div class="stats-campanas__numero"><span>Enviados sin errores</span><strong>${num(r.validos)}</strong><span>${num(r.entregados)} entregados confirmados</span></div>` +
      `<div class="stats-campanas__numero"><span>Leídos sin responder</span><strong>${num(r.vistos_sin_respuesta)}</strong><span>${num(r.leidos)} lecturas en total</span></div></div>` +
      `<div class="stats-campanas__grafico"><h3>Resultado de los botones</h3><div class="stats-campanas__distribucion">` +
      `<div class="stats-campanas__torta" role="img" aria-label="Distribución de interés, desinterés, bajas y sin selección; cifras en la leyenda" style="background:${r.validos ? `conic-gradient(${sectores})` : "var(--panel-2)"}"></div>` +
      `<div class="stats-campanas__leyenda">${distribucion.map((d, i) =>
        `<p><i aria-hidden="true" style="--color-serie:${d.color}"></i><button type="button" data-segmento-campana="${i}"><span>${d.titulo}</span><strong>${num(r.validos ? Math.round(1000 * d.cantidad / r.validos) / 10 : 0)} %</strong><small>${num(d.cantidad)} pacientes</small></button></p>`).join("")}</div></div>` +
      `<p class="field__hint">Base: ${num(r.validos)} enviados sin errores de ${num(datosCampana.resumen.validos)} en total. Pulsa una categoría para filtrar.</p></div>` +
      `<div class="stats-campanas__numeros stats-campanas__numeros--costos">` +
      `<div class="stats-campanas__numero"><span>Costo estimado del segmento</span><strong>${escaparHtml(valorCoste)}</strong></div>` +
      `<div class="stats-campanas__numero"><span>Costo por interesado</span><strong>${coste.por_interesado == null ? "—" : escaparHtml(fmtMoneda(coste.por_interesado, coste.moneda))}</strong></div></div>` +
      `<p class="field__hint">Estimación de ${num(coste.entregas)} entregas confirmadas · ${num(r.sin_confirmacion)} mensajes sin confirmación de entrega.</p>` +
      `<h3>Respuestas a botones</h3>` + (datos.botones.length ?
        `<div class="tabla-wrap"><table><thead><tr><th>Botón u opción</th><th>Clics</th><th>Pacientes</th><th>% de enviados sin error</th></tr></thead><tbody>${datos.botones.map(b =>
          `<tr><td>${escaparHtml(b.boton || "Sin texto")}</td><td>${b.medible === false ? "No medible" : num(b.clics)}</td><td>${b.medible === false ? "—" : num(b.pacientes)}</td><td>${b.medible === false ? "—" : `${num(b.porcentaje)} %`}</td></tr>`).join("")}</tbody></table></div>` :
        `<p class="field__hint">Sin respuestas a botones registradas.</p>`) +
      `<details class="stats-campanas__calculo"><summary>Cómo se calculan estos resultados</summary><p>Se usa la última selección de cada paciente. Una respuesta libre no cuenta como interés; errores e inválidos quedan fuera. Un audio sí cuenta como respuesta al calcular los leídos sin responder. Las respuestas se atribuyen hasta la siguiente campaña de la misma clínica. Los botones de enlace no informan clics. Los costos son estimaciones según tarifas guardadas y entregas confirmadas, no la factura de Meta. Los datos dependen de los webhooks recibidos.</p></details>`;
    resultadosCampana.hidden = false;
    estadoCampana.textContent = `Envío #${datos.envio.id} · ${num(r.validos)} de ${num(datosCampana.resumen.validos)} enviados sin errores en el segmento.`;
    function aplicarSegmento(indice) {
      const filtro = distribucion[indice]?.filtro || {};
      if (filtro.entrega) $("#filtroEntregaCampana").value = filtro.entrega;
      if (filtro.resultado) $("#filtroResultadoCampana").value = filtro.resultado;
      if (filtro.entrega || filtro.resultado) pintarCampana();
    }
    resultadosCampana.querySelectorAll("[data-segmento-campana]").forEach(b =>
      b.addEventListener("click", () => aplicarSegmento(Number(b.dataset.segmentoCampana))));
    const torta = resultadosCampana.querySelector(".stats-campanas__torta");
    torta.addEventListener("click", event => {
      if (!r.validos) return;
      const rect = torta.getBoundingClientRect();
      const x = event.clientX - rect.left - rect.width / 2;
      const y = event.clientY - rect.top - rect.height / 2;
      if (Math.hypot(x, y) > rect.width / 2) return;
      const posicion = ((Math.atan2(y, x) * 180 / Math.PI + 450) % 360) / 3.6;
      let limite = 0;
      for (let i = 0; i < distribucion.length; i++) {
        limite += 100 * distribucion[i].cantidad / r.validos;
        if (posicion < limite) { aplicarSegmento(i); break; }
      }
    });
}
selectorCampana.addEventListener("toggle", pintarSelectorCampana);
cuerpoCampanas.addEventListener("click", e => {
  const fila = e.target.closest("tr[data-campana-id]");
  if (fila) cargarCampana(fila.dataset.campanaId);
});
document.querySelectorAll("[data-tipo-campana]").forEach(boton => boton.addEventListener("click", () => {
  if (tipoCampana === boton.dataset.tipoCampana) return;
  tipoCampana = boton.dataset.tipoCampana;
  document.querySelectorAll("[data-tipo-campana]").forEach(b => b.setAttribute("aria-pressed", String(b === boton)));
  cargarCampanas(true);
}));
document.querySelectorAll("[data-filtro-campana]").forEach(boton => boton.addEventListener("click", () => {
  const id = boton.dataset.filtroCampana === "entrega" ? "filtroEntregaCampana" : "filtroResultadoCampana";
  document.getElementById(id).value = boton.dataset.valor;
  pintarCampana();
}));
$("#btnBuscarCampanaEst").addEventListener("click", () => cargarCampanas(true));
for (const id of ["filtroEntregaCampana", "filtroResultadoCampana", "comparacionCampana"])
  document.getElementById(id).addEventListener("change", pintarCampana);
$("#limpiarFiltrosCampana").addEventListener("click", () => {
  $("#filtroEntregaCampana").value = "todos";
  $("#filtroResultadoCampana").value = "todos";
  $("#comparacionCampana").value = "general";
  pintarCampana();
});
$("#buscarCampanaEst").addEventListener("keydown", e => { if (e.key === "Enter") cargarCampanas(true); });
$("#campanasAnterior").addEventListener("click", () => { paginaCampanas--; cargarCampanas(); });
$("#campanasSiguiente").addEventListener("click", () => { paginaCampanas++; cargarCampanas(); });
$("#campanasPrimera").addEventListener("click", () => { paginaCampanas = 1; cargarCampanas(); });
$("#campanasUltima").addEventListener("click", () => { paginaCampanas = paginasCampanas; cargarCampanas(); });
$("#campanasPagina").addEventListener("change", e => {
  paginaCampanas = Math.min(paginasCampanas, Math.max(1, Number(e.target.value) || 1)); cargarCampanas();
});

let yaCargada = false;
window.snwCargarEstadisticas = function () {
  if (yaCargada) return;
  yaCargada = true;
  (async () => {
    const inicioCarga = iniciarCargaEstadisticas();
    try {
      await Promise.all([cargarAreasEst(), cargarClinicasEst()]);
      if (envioEnlacePendiente) {
        selEntornoEst.value = enlaceEstadisticas.get("base") === "desarrollo" ? "desarrollo" : "produccion";
        selAreaEst.value = enlaceEstadisticas.get("area") || "";
        selCentroEst.value = enlaceEstadisticas.get("centro") || "todos";
        if (!selCentroEst.value) selCentroEst.value = "todos";
        pintarCampoAreaEst();
        activarTabStats("campanas", false);
      }
      paginaEstadisticasLista = true;
      await recargarTodo();
    } finally {
      await terminarCargaEstadisticas(inicioCarga);
    }
  })();
};
})();
