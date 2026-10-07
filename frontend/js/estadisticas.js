/* Página «Estadísticas» (estadisticas.html, solo admin/dev). */
(function () {
const $ = (sel) => document.querySelector(sel);

function authHeaders() {
  return { Authorization: "Bearer " + (localStorage.getItem("snw_token") || "") };
}

const toastEl = $("#toast");
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

// Área (solo admin/dev): "" = global producción; con id filtra
// los tres endpoints (resumen, gráfico y costos).
let areasEst = [];
const selAreaEst = $("#selAreaEst");

function areaEstActual() {
  const v = selAreaEst ? selAreaEst.value : "";
  return v ? Number(v) : null;
}

function qsEspEst() {
  const esp = areaEstActual();
  return esp ? `&area_id=${esp}` : "";
}

function nombreAreaEst() {
  const esp = areaEstActual();
  const e = areasEst.find((x) => x.id === esp);
  return e ? e.nombre_visible : "";
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
  const guardada = localStorage.getItem("snw_esp_estadisticas") || "";
  selAreaEst.innerHTML =
    `<option value="">Todas</option>` +
    areasEst.map((e) => `<option value="${e.id}">${e.nombre_visible} (${e.nombre_tabla_base})</option>`).join("");
  if (guardada && areasEst.some((e) => String(e.id) === guardada)) {
    selAreaEst.value = guardada;
  } else {
    localStorage.removeItem("snw_esp_estadisticas");
  }
  selAreaEst.hidden = !areasEst.length;
}

if (selAreaEst) selAreaEst.addEventListener("change", () => {
  const v = selAreaEst.value;
  if (v) localStorage.setItem("snw_esp_estadisticas", v);
  else localStorage.removeItem("snw_esp_estadisticas");
  limpiarFiltroCalendarioCostos();
  cerrarCalendarioCostos();
  recargarTodo();
});

async function recargarTodo() {
  cargar();
  await cargarEnvios(granEnvios);
  if (ES_ADMIN && $("#panelCostos")) {
    cargarTarifas();
    cargarCostos(granCostos);
  }
}

function chip(label, valor, clase, resp) {
  const attr = resp ? ` data-respuesta="${resp}"` : "";
  return `<div class="stat ${clase}"${attr}>${label} <strong>${num(valor)}</strong></div>`;
}

async function cargar(mostrarFeedback = false) {
  // El botón lo deshabilita/rehabilita el cooldown de snwConCooldown, no
  // esta función (ver el addEventListener más abajo).
  try {
    const esp = areaEstActual();
    const res = await fetch(`api/estadisticas${esp ? `?area_id=${esp}` : ""}`, { headers: authHeaders(), cache: "no-store" });
    if (res.status === 401) { window.snwSesionExpirada(); return; }
    if (!res.ok) throw new Error();
    render(await res.json());
    if (mostrarFeedback) toast("Estadísticas actualizadas correctamente.");
  } catch {
    console.error("[estadisticas.js cargar()]");
    toast("No se pudieron cargar las estadísticas.", "error");
  }
}

function render(d) {
  $("#nombreMes").textContent = nombreMes(d.mes);
  $("#enviadosMes").textContent = num(d.enviados_mes);

  const espNombre = (d.area && d.area.nombre_visible) || nombreAreaEst();
  const heroSub = document.querySelector(".hero-sub");
  if (heroSub) {
    heroSub.textContent = espNombre
      ? `mensajes enviados este mes · ${espNombre}`
      : "mensajes enviados este mes · solo producción";
  }
  const hintResp = $("#hintRespuestas");
  if (hintResp) {
    const alcance = espNombre ? `de <strong>${escaparHtml(espNombre)}</strong>` : "de producción";
    hintResp.innerHTML = `Estado actual de los pacientes ${alcance} ` +
      `<strong>a los que ya se les envió un mensaje</strong>. Usa el filtro para comparar ` +
      `respuestas o interés. Sin una señal explícita de interés o rechazo de la oferta quedan «Sin clasificar». ` +
      `En <a href="historial.html">Historial</a> puedes ver quiénes son.`;
  }

  $("#statsMes").innerHTML =
    chip("Enviados", d.enviados_mes, "stat--enviado") +
    chip("Fallidos", d.fallidos_mes, "stat--error") +
    chip("Inválidos", d.invalidos_mes, "stat--invalido") +
    chip("Respondieron", d.respondio_mes, "stat--resp", "respondio") +
    chip("Bajas", d.baja_mes, "stat--resp", "baja");

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

async function cargarEnvios(gran) {
  granEnvios = filtroCalendarioCostos?.modo || gran;
  const solicitud = ++solicitudEnvios;
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
    const anios = [...aniosConEnvios].sort((a, b) => a - b);
    if (anios.length && !aniosConEnvios.has(estadoCalendarioCostos.anio)) {
      estadoCalendarioCostos.anio = anios[anios.length - 1];
      estadoCalendarioCostos.mes = 0;
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
        `${aniosConEnvios.has(anio) ? "" : " disabled"}>${dia}</button>`);
    }
  } else if (modo === "mes") {
    $("#calendarioCostosTitulo").textContent = String(anio);
    MESES.forEach((nombre, i) => {
      const valor = `${anio}-${dosDigitos(i + 1)}`;
      botones.push(`<button type="button" data-cal-valor="${valor}" ` +
        `aria-pressed="${seleccion.mes.has(valor)}"` +
        `${aniosConEnvios.has(anio) ? "" : " disabled"}>${nombre}</button>`);
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
  try {
    const res = await fetch("api/tarifas", { headers: authHeaders(), cache: "no-store" });
    if (res.status === 401) { window.snwSesionExpirada(); return; }
    if (!res.ok) throw new Error();
    renderTarifas(await res.json());
  } catch {
    console.error("[estadisticas.js cargarTarifas()]");
    $("#tarifasVigente").innerHTML =
      '<p class="mes-vacio" style="padding:8px 0;">No se pudieron cargar las tarifas.</p>';
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
  const btn = $("#btnActualizarTarifas");
  const textoOriginal = btn.textContent;
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Actualizando…';
  try {
    const res = await fetch("api/tarifas/actualizar", { method: "POST", headers: authHeaders() });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(d.detail || "");
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
    console.error("[estadisticas.js actualizarTarifas()]", err);
    toast("No se pudieron actualizar las tarifas. " + (err.message || ""), "error");
    btn.disabled = false;
    btn.textContent = textoOriginal;
  }
}

async function cargarCostos(gran) {
  granCostos = filtroCalendarioCostos?.modo || gran;
  const solicitud = ++solicitudCostos;
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
    filasResumen.push(["Servicio gratuito", `${num(servicio.gratuitos)} de 1.000 por número y mes`]);
    filasResumen.push(["Servicio pagado", num(servicio.pagados)]);
  }
  if (servicio.emisor_desconocido) {
    filasResumen.push(["Entregas antiguas sin emisor", `${num(servicio.emisor_desconocido)} · ${servicio.emisor_unico_inferido
      ? "atribuidas al único número de empresa registrado"
      : "agrupadas por separado; cifra estimada"}`]);
  }
  if (t.excluidos) filasResumen.push(["Sin categoría de facturación", `${num(t.excluidos)} · no incluidos en el costo`]);
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
  abrirCalendario.addEventListener("click", () => {
    calendario.hidden = !calendario.hidden;
    abrirCalendario.setAttribute("aria-expanded", String(!calendario.hidden));
    if (!calendario.hidden) {
      pintarCalendarioCostos();
      calendario.querySelector('[data-cal-modo][aria-selected="true"]').focus();
    }
  });
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

window.snwConCooldown($("#btnActualizarEstadisticas"), () => cargar(true));
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

let yaCargada = false;
window.snwCargarEstadisticas = function () {
  if (yaCargada) return;
  yaCargada = true;
  (async () => {
    await cargarAreasEst();
    recargarTodo();
  })();
};
})();
