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

// Especialidad (Fase 3, solo admin/dev): "" = global producción; con id filtra
// los tres endpoints (resumen, gráfico y costos).
let especialidadesEst = [];
const selEspecialidadEst = $("#selEspecialidadEst");

function especialidadEstActual() {
  const v = selEspecialidadEst ? selEspecialidadEst.value : "";
  return v ? Number(v) : null;
}

function qsEspEst() {
  const esp = especialidadEstActual();
  return esp ? `&especialidad_id=${esp}` : "";
}

function nombreEspecialidadEst() {
  const esp = especialidadEstActual();
  const e = especialidadesEst.find((x) => x.id === esp);
  return e ? e.nombre_visible : "";
}

async function cargarEspecialidadesEst() {
  if (!selEspecialidadEst) return;
  try {
    const res = await fetch("api/especialidades/mias", { headers: authHeaders(), cache: "no-store" });
    if (!res.ok) throw new Error();
    especialidadesEst = await res.json();
  } catch {
    especialidadesEst = [];
  }
  const guardada = localStorage.getItem("snw_esp_estadisticas") || "";
  selEspecialidadEst.innerHTML =
    `<option value="">Todas</option>` +
    especialidadesEst.map((e) => `<option value="${e.id}">${e.nombre_visible} (${e.nombre_tabla_base})</option>`).join("");
  if (guardada && especialidadesEst.some((e) => String(e.id) === guardada)) {
    selEspecialidadEst.value = guardada;
  } else {
    localStorage.removeItem("snw_esp_estadisticas");
  }
  selEspecialidadEst.hidden = !especialidadesEst.length;
}

if (selEspecialidadEst) selEspecialidadEst.addEventListener("change", () => {
  const v = selEspecialidadEst.value;
  if (v) localStorage.setItem("snw_esp_estadisticas", v);
  else localStorage.removeItem("snw_esp_estadisticas");
  recargarTodo();
});

function recargarTodo() {
  cargar();
  cargarEnvios(granEnvios);
  if (ES_ADMIN && $("#panelCostos")) {
    cargarTarifas();
    cargarCostos(granCostos);
  }
}

function chip(label, valor, clase, resp) {
  const attr = resp ? ` data-respuesta="${resp}"` : "";
  return `<div class="stat ${clase}"${attr}>${label} <strong>${num(valor)}</strong></div>`;
}

async function cargar() {
  // El botón lo deshabilita/rehabilita el cooldown de snwConCooldown, no
  // esta función (ver el addEventListener más abajo).
  try {
    const esp = especialidadEstActual();
    const res = await fetch(`api/estadisticas${esp ? `?especialidad_id=${esp}` : ""}`, { headers: authHeaders(), cache: "no-store" });
    if (res.status === 401) { window.snwSesionExpirada(); return; }
    if (!res.ok) throw new Error();
    render(await res.json());
  } catch {
    toast("No se pudieron cargar las estadísticas.", "error");
  }
}

function render(d) {
  $("#nombreMes").textContent = nombreMes(d.mes);
  $("#enviadosMes").textContent = num(d.enviados_mes);

  const espNombre = (d.especialidad && d.especialidad.nombre_visible) || nombreEspecialidadEst();
  const heroSub = document.querySelector(".hero-sub");
  if (heroSub) {
    heroSub.textContent = espNombre
      ? `mensajes enviados este mes · ${espNombre}`
      : "mensajes enviados este mes · solo producción";
  }
  const hintResp = $("#hintRespuestas");
  if (hintResp && espNombre) {
    hintResp.innerHTML = `Estado actual de los pacientes de <strong>${espNombre}</strong> ` +
      `<strong>a los que ya se les envió un mensaje</strong>. Usa los botones para comparar cuántos ` +
      `respondieron, se dieron de baja o no han respondido. En <a href="historial.html">Historial</a> puedes ver quiénes son.`;
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

  renderRespuestasPacientes(d.pacientes_por_respuesta || {});
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
let respCatSel = "todos";

function renderRespuestasPacientes(r) {
  const el = $("#respuestasPacientes");
  const total = r && r.total ? r.total : 0;
  if (!total) {
    el.innerHTML = '<p class="mes-vacio" style="padding:14px 18px;">Sin pacientes registrados.</p>';
    return;
  }
  const maxCat = Math.max(...RESP_CATS.map((c) => r[c.cat] || 0), 1);

  const toggles =
    `<button type="button" class="stat" data-cat="todos">Todos <strong>${num(total)}</strong></button>` +
    RESP_CATS.map(
      (c) =>
        `<button type="button" class="stat stat--resp" data-respuesta="${c.cat}" data-cat="${c.cat}">` +
        `${c.label} <strong>${num(r[c.cat] || 0)}</strong></button>`
    ).join("");

  const filas = RESP_CATS.map((c) => {
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
  document.querySelectorAll("#respToggles .stat").forEach((b) =>
    b.classList.toggle("activo", b.dataset.cat === cat)
  );
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

async function cargarEnvios(gran) {
  granEnvios = gran;
  document.querySelectorAll("#enviosTabs button").forEach((b) =>
    b.classList.toggle("activo", b.dataset.gran === gran)
  );
  try {
    const res = await fetch(`api/estadisticas/envios?granularidad=${gran}${qsEspEst()}`, {
      headers: authHeaders(), cache: "no-store",
    });
    if (res.status === 401) { window.snwSesionExpirada(); return; }
    if (!res.ok) throw new Error();
    const d = await res.json();
    pintarGrafico($("#enviosGrafico"), (d.filas || []).map((f) => ({ periodo: f.periodo, valor: f.enviados })), {
      gran: d.granularidad,
      fmtValor: (v) => num(Math.round(v)),
      vacio: "Sin mensajes enviados en este periodo.",
    });
  } catch {
    $("#enviosGrafico").innerHTML =
      '<p class="mes-vacio" style="padding:8px 18px;">No se pudo cargar el gráfico.</p>';
  }
}

/* -- Costos de mensajes de WhatsApp (solo administrador) ----------- */
let granCostos = "mes";

async function cargarTarifas() {
  try {
    const res = await fetch("api/tarifas", { headers: authHeaders(), cache: "no-store" });
    if (res.status === 401) { window.snwSesionExpirada(); return; }
    if (!res.ok) throw new Error();
    renderTarifas(await res.json());
  } catch {
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
      `Marketing ${fmtMoneda(d.proxima.marketing, d.proxima.moneda)}.`;
  }

  if (!v) {
    $("#tarifasVigente").innerHTML =
      '<p class="mes-vacio" style="padding:8px 0;">Sin tarifas guardadas todavía.</p>';
    $("#tarifasPie").textContent = "";
    return;
  }

  const cats = ["marketing"];
  $("#tarifasVigente").innerHTML = cats
    .map(
      (c) => `
      <div class="tarifa-card">
        <span class="tarifa-card__cat">${CAT_LABEL[c]}</span>
        <span class="tarifa-card__precio">${v[c] != null ? fmtMoneda(v[c], v.moneda) : "—"}</span>
        <span class="tarifa-card__unidad">por mensaje</span>
      </div>`
    )
    .join("");

  $("#tarifasPie").innerHTML =
    `Moneda de facturación: <strong>${d.moneda}</strong> · ` +
    `vigente desde ${fechaDMA(v.efectiva_desde)} · ` +
    `actualizado ${d.ultima_descarga || "—"} · ` +
    '<a href="#" id="btnCsvChile">Descargar CSV de Chile</a>';
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
    toast("No se pudieron actualizar las tarifas. " + (err.message || ""), "error");
    btn.disabled = false;
    btn.textContent = textoOriginal;
  }
}

async function cargarCostos(gran) {
  granCostos = gran;
  document.querySelectorAll("#costosTabs button").forEach((b) =>
    b.classList.toggle("activo", b.dataset.gran === gran)
  );
  try {
    const res = await fetch(`api/estadisticas/costos?granularidad=${gran}${qsEspEst()}`, {
      headers: authHeaders(), cache: "no-store",
    });
    if (res.status === 401) { window.snwSesionExpirada(); return; }
    if (!res.ok) throw new Error();
    renderCostos(await res.json());
  } catch {
    $("#costosBarras").innerHTML =
      '<p class="mes-vacio" style="padding:8px 18px;">No se pudieron cargar los costos.</p>';
    $("#costosTotal").textContent = "";
  }
}

function renderCostos(d) {
  const filas = Array.isArray(d.filas) ? d.filas : [];
  const mon = d.moneda;

  if (d.sin_tarifas) {
    $("#costosBarras").innerHTML =
      '<p class="mes-vacio" style="padding:8px 18px;">Sin tarifas guardadas: no se puede estimar el costo. Pulsa «Actualizar tarifas».</p>';
    $("#costosTotal").textContent = "";
    return;
  }
  if (!filas.length) {
    $("#costosBarras").innerHTML =
      '<p class="mes-vacio" style="padding:8px 18px;">Sin mensajes enviados todavía.</p>';
    $("#costosTotal").textContent = "";
    return;
  }

  pintarGrafico(
    $("#costosBarras"),
    filas.map((f) => ({
      periodo: f.periodo,
      valor: f.costo,
      sub: `${num(f.mensajes)} mensaje(s)`,
    })),
    { gran: d.granularidad, fmtValor: (v) => fmtMoneda(v, mon) }
  );

  const t = d.total || { costo: 0, mensajes: 0, excluidos: 0, por_categoria: {} };
  const desglose = Object.entries(t.por_categoria || {})
    .filter(([, n]) => n)
    .map(([c, n]) => `${CAT_LABEL[c] || c}: ${num(n)}`)
    .join(" · ");
  const excl = Number(t.excluidos || 0);
  $("#costosTotal").innerHTML =
    `<strong>Total:</strong> ${fmtMoneda(t.costo, mon)} · ${num(t.mensajes)} mensajes de plantilla` +
    (desglose ? ` · ${desglose}` : "") +
    (excl
      ? `<br><span class="costos-nota">No se cuentan ${num(excl)} mensaje(s) de texto libre ` +
        `(respuestas dentro de la ventana de 24 h): Meta no los cobra.</span>`
      : "");
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
  document.querySelectorAll("#costosTabs button").forEach((b) =>
    b.addEventListener("click", () => cargarCostos(b.dataset.gran))
  );
}

window.snwConCooldown($("#btnActualizarEstadisticas"), cargar);
document.querySelectorAll("#enviosTabs button").forEach((b) =>
  b.addEventListener("click", () => cargarEnvios(b.dataset.gran))
);

let yaCargada = false;
window.snwCargarEstadisticas = function () {
  if (yaCargada) return;
  yaCargada = true;
  (async () => {
    await cargarEspecialidadesEst();
    recargarTodo();
  })();
};
})();
