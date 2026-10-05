const API_URL = "api/plantillas";
const MAX_MENSAJE = 1024; // límite de caracteres del cuerpo del mensaje (límite de Meta)

// Filtro de seguridad extra (el backend ya descarta esto): nunca renderizar
// una entrada rota, por ejemplo si alguien edita plantillas.json a mano y
// deja un objeto a medias.
const esPlantillaValida = (p) =>
  !!p && typeof p === "object" && p.id != null &&
  !!String(p.nombre ?? "").trim() && !!String(p.texto ?? "").trim();

let plantillas = [];
let activaId = null;
let snapshot = null;

// Áreas visibles para la cuenta (Fase 3): todas si es
// privilegiada, solo las asignadas si no. Las plantillas pueden asociarse a
// una (envío solo a su tabla) o quedar globales.
let misAreas = [];

// Usuario actual (para saber si creó cada plantilla: solo el creador la
// edita/elimina; admin/dev, cualquiera; el backend lo exige igual).
let miUsuario = "";
async function cargarMiUsuario() {
  try {
    const res = await fetch("api/auth/me", { headers: authHeaders(), cache: "no-store" });
    if (res.ok) miUsuario = ((await res.json()).usuario || "").toLowerCase();
  } catch { /* sin dato: el backend exige igual */ }
}
const esCreador = (p) => !!p && !!miUsuario && (p.creado_por || "").toLowerCase() === miUsuario;
const puedeEditarEsta = (p) => !p || !!window.snwEsPrivilegiado || esCreador(p);

function nombreArea(id) {
  const e = misAreas.find((x) => x.id === id);
  return e ? e.nombre_visible : null;
}

const $ = (sel) => document.querySelector(sel);

const listaEl = $("#listaPlantillas");
const buscadorEl = $("#buscador");
const selFiltroArea = $("#selFiltroArea");
let filtroArea = localStorage.getItem("snw_filtro_tpl") || "todas";
const formEl = $("#formPlantilla");
const inpNombre = $("#inpNombre");
const inpMensaje = $("#inpMensaje");
const inpTemplate = $("#inpTemplate");
const inpTemplateLang = $("#inpTemplateLang");
const inpTemplateCategoria = $("#inpTemplateCategoria");
const inpArea = $("#inpArea");
const hayTemplateMeta = !!inpTemplate && !!inpTemplateLang && !!inpTemplateCategoria;
const bloqueEstadoMeta = $("#bloqueEstadoMeta");
const badgeEstadoMeta = $("#badgeEstadoMeta");
const badgeAprobadaReciente = $("#badgeAprobadaReciente");
const motivoRechazoMeta = $("#motivoRechazoMeta");
const btnRevisarTodos = $("#btnRevisarTodos");
const btnSincronizarMeta = $("#btnSincronizarMeta");

const valTemplate = () => (hayTemplateMeta ? inpTemplate.value : "");
const valTemplateLang = () => (hayTemplateMeta ? inpTemplateLang.value : "es");
const valTemplateCategoria = () => (hayTemplateMeta ? inpTemplateCategoria.value : "");
const contadorEl = $("#contador");
const avisoComodines = $("#avisoComodines");
const avisoNombrePermanente = $("#avisoNombrePermanente");
const avisoPendiente = $("#avisoPendiente");
const btnCancelar = $("#btnCancelar");
const hintNombre = $("#hintNombre");
const hintTemplate = $("#hintTemplate");
const infoCreacion = $("#infoCreacion");
const previewTexto = $("#previewTexto");
const previewHora = $("#previewHora");
const previewBotones = $("#previewBotones");
const tituloForm = $("#tituloFormulario");
const estadoVacio = $("#estadoVacio");
const btnEliminar = $("#btnEliminar");
const btnGuardar = $("#btnGuardar");
const btnEnviarActual = $("#btnEnviarActual");
const modalEl = $("#modalEliminar");
const toastEl = $("#toast");

const BOTONES_PREDEFINIDOS = ["Me interesa", "No me interesa", "Dar de baja"];

const DATOS_EJEMPLO = {
  nombre: "David",
  apellido: "Araya",
};

// Estado del template en Meta. Solo una plantilla APPROVED se puede usar para
// enviar mensajes. Editar, guardar y eliminar se permiten en APPROVED y
// también en REJECTED (para poder corregirla y volver a mandarla a revisión,
// o borrarla); mientras esté realmente pendiente de revisión (recién creada
// o PENDING) queda de solo lectura. Ver también la validación del servidor
// en /api/plantillas y /api/notificaciones/enviar.
const ETIQUETAS_ESTADO_META = {
  APPROVED: "Aprobada",
  PENDING: "Pendiente",
  REJECTED: "Rechazada",
  DESCONOCIDO: "Sin consultar",
};
const etiquetaEstadoMeta = (status) => ETIQUETAS_ESTADO_META[status] || ETIQUETAS_ESTADO_META.DESCONOCIDO;
// p == null (plantilla nueva, sin guardar todavía) no tiene restricción.
const esPlantillaAprobada = (p) => !p || p.whatsapp_template_status === "APPROVED";
const esPlantillaRechazada = (p) => !!p && p.whatsapp_template_status === "REJECTED";
// Aprobación interna previa a Meta: el usuario normal crea en "pendiente" y
// solo se registra en Meta cuando un admin/dev/supervisor la aprueba. Las
// antiguas (sin campo) cuentan como "aprobada".
const aprobacionPlantilla = (p) => !p || p.aprobacion_estado || "aprobada";
const puedeAprobarPlantillas = () => !!window.snwEsPrivilegiado || window.snwRol === "supervisor";
// Muestra de Meta (hello_world): no se edita ni elimina desde acá.
const esPlantillaProtegida = (p) =>
  !!p && (p.clave === "hello_world" || p.whatsapp_template === "hello_world");
const esPlantillaEditable = (p) => {
  if (!p) return true;
  if (esPlantillaProtegida(p)) return false;
  if (aprobacionPlantilla(p) !== "aprobada") return true; // aún no existe en Meta
  return esPlantillaAprobada(p) || esPlantillaRechazada(p);
};
// Meta solo permite editar un template una vez cada 24h (ver actualizar_plantilla
// en el backend, que es quien de verdad lo exige). No afecta a Eliminar.
const MS_24H = 24 * 3600 * 1000;
const editadaRecientemente = (p) => !!p && !!p.ultima_edicion && (Date.now() - p.ultima_edicion) < MS_24H;
// Formato "HH:MM" con el tiempo que falta para poder editar de nuevo.
function cuentaRegresivaEditable(p) {
  const restanteMs = Math.max(0, MS_24H - (Date.now() - p.ultima_edicion));
  const horas = Math.floor(restanteMs / 3600000);
  const minutos = Math.floor((restanteMs % 3600000) / 60000);
  return `${String(horas).padStart(2, "0")}:${String(minutos).padStart(2, "0")}`;
}

// El servidor revisa solo (cada `plantillas_revision_minutos`) el estado en
// Meta de las plantillas pendientes y guarda cuándo pasaron a aprobadas
// (whatsapp_template_aprobada_en). Durante estos minutos siguientes se
// destaca con un aviso; después se deja de mostrar (no hace falta borrar
// nada, el cálculo es por tiempo). Valor por defecto hasta que se lea el
// real desde /api/configuracion (clave `plantillas_badge_aprobada_minutos`,
// ver actualizarBadgeMensajeria) — configurable en Configuración → WhatsApp.
let MINUTOS_APROBADA_RECIENTE = 10;
const esRecienAprobada = (p) =>
  !!p &&
  p.whatsapp_template_status === "APPROVED" &&
  !!p.whatsapp_template_aprobada_en &&
  MINUTOS_APROBADA_RECIENTE > 0 &&
  Date.now() - p.whatsapp_template_aprobada_en < MINUTOS_APROBADA_RECIENTE * 60 * 1000;

if (!localStorage.getItem("snw_token")) location.replace("login.html");

// Permiso para crear / editar / eliminar plantillas. Sin él, el panel de
// edición queda de solo lectura: se puede elegir una plantilla y enviarla,
// pero no modificarla.
const PUEDE_EDITAR_PLANTILLAS = !window.snwPuede || window.snwPuede("plantillas_editar");

function aplicarModoSoloLecturaPlantillas() {
  if (PUEDE_EDITAR_PLANTILLAS) return;
  [btnGuardar, btnEliminar, $("#btnCancelar"), $("#btnNuevaEmpty"), avisoNombrePermanente,
   document.querySelector(".field__hint.atajos")].forEach((el) => el && (el.hidden = true));
  document.querySelectorAll(".wildcards").forEach((el) => (el.hidden = true));
}

function authHeaders(extra = {}) {
  return { Authorization: "Bearer " + (localStorage.getItem("snw_token") || ""), ...extra };
}

function seleccionarDefault() {
  const def = plantillas.find((p) => p.clave === "default" || String(p.nombre || "").toLowerCase() === "default");
  if (def) abrir(def.id);
}

async function cargarAreas() {
  try {
    const res = await fetch("api/areas/mias", { headers: authHeaders(), cache: "no-store" });
    if (!res.ok) throw new Error();
    misAreas = await res.json();
  } catch {
    console.error("[app.js cargarAreas()]");
    misAreas = [];
  }
  poblarSelectArea();
  poblarFiltroArea();
  poblarFormProg();
}

function poblarFiltroArea() {
  if (!selFiltroArea) return;
  selFiltroArea.innerHTML =
    `<option value="todas">Todas</option>` +
    `<option value="global">Globales (sin área)</option>` +
    misAreas.map((e) => `<option value="${e.id}">${escaparHtml(e.nombre_visible)}</option>`).join("");
  const vals = ["todas", "global", ...misAreas.map((e) => String(e.id))];
  if (!vals.includes(filtroArea)) filtroArea = "todas";
  selFiltroArea.value = filtroArea;
}

if (selFiltroArea) selFiltroArea.addEventListener("change", () => {
  filtroArea = selFiltroArea.value;
  localStorage.setItem("snw_filtro_tpl", filtroArea);
  renderLista(buscadorEl.value);
});

function poblarSelectArea() {
  if (!inpArea) return;
  const actual = inpArea.value;
  // El usuario normal no puede usar plantillas globales: solo sus
  // áreas asignadas (el backend lo exige igual).
  const esUsuario = (window.snwRol || "") === "usuario";
  inpArea.innerHTML =
    (esUsuario ? "" : `<option value="">Global (todas las bases)</option>`) +
    misAreas.map((e) => `<option value="${e.id}">${escaparHtml(e.nombre_visible)}</option>`).join("");
  const vals = [...inpArea.options].map((o) => o.value);
  if (actual && vals.indexOf(actual) === -1) {
    // La plantilla abierta es de un área que no está en la lista (se abrió
    // antes de que cargaran): se conserva su opción para no perder el valor.
    inpArea.add(new Option(nombreArea(Number(actual)) || "Área", actual));
  }
  if (actual && [...inpArea.options].some((o) => o.value === actual)) {
    inpArea.value = actual;
  } else if (!activaId && esUsuario && misAreas.length) {
    // Solo al crear una plantilla nueva: el usuario necesita un área
    // por defecto. Editando una existente no se toca el valor.
    inpArea.value = String(misAreas[0].id);
  }
  refrescarAvisoSinArea();
  // La reconstrucción de la lista no es una edición del usuario: si cambió
  // el valor del select, se re-toma el snapshot para no avisar "cambios sin
  // guardar" falsos al cambiar de plantilla.
  if (snapshot !== null && inpArea.value !== actual) marcarSnapshot();
}

// Sin áreas asignadas el usuario no puede crear plantillas: se le
// indica que pida una a un administrador, en vez de dejar un select vacío.
function refrescarAvisoSinArea() {
  const hint = $("#hintSinArea");
  if (!hint) return;
  const sinAsignadas = (window.snwRol || "") === "usuario" && !misAreas.length;
  hint.hidden = !sinAsignadas;
  const p = activaId ? plantillas.find((x) => x.id === activaId) : null;
  const fueraDeMisAreas = !!p && p.area_id != null &&
    !misAreas.some((area) => Number(area.id) === Number(p.area_id));
  const bloquearPorAlcance = !!p && !window.snwEsPrivilegiado &&
    (!puedeEditarEsta(p) || fueraDeMisAreas);
  if (inpArea) inpArea.disabled = sinAsignadas || bloquearPorAlcance;
}

// Selector único de base de datos de la card de envío: lista solo
// las bases disponibles —desarrollo/producción (según restricción) y una
// opción por área—. La elección de base siempre es explícita.
// Valores: "desarrollo" | "produccion" | "esp:<id>".
function baseSeleccionadaConf() {
  const sel = $("#selBaseConf");
  return sel?.value || "";
}

function modoAreaConf() {
  return baseSeleccionadaConf().startsWith("esp:");
}

function areaEnvioId() {
  const v = baseSeleccionadaConf();
  if (!v.startsWith("esp:")) return null;
  const id = Number(v.slice(4));
  return Number.isFinite(id) ? id : null;
}

function ambienteEnvioConf() {
  // Las áreas son tablas únicas (sin entorno): se envían como
  // producción (con confirmación del supervisor salvo permiso directo).
  return modoAreaConf() ? "produccion" : baseSeleccionadaConf();
}

// Reconstruye las opciones del select. Con `forzarEspId` (plantilla de una
// área) deja solo esa base como opción, pero exige seleccionarla.
function construirOpcionesBaseConf(forzarEspId) {
  const sel = $("#selBaseConf");
  if (!sel) return;
  // En Desarrollo ningún rol puede escoger producción ni áreas para enviar.
  const restringido = usuarioRestringidoADesarrollo() || (window.snwRol || "") === "usuario";
  let html = "";
  if (forzarEspId != null) {
    const espForzada = misAreas.find((e) => e.id === forzarEspId);
    const nombre = espForzada ? espForzada.nombre_visible : (nombreArea(forzarEspId) || "Área");
    const tabla = espForzada ? espForzada.nombre_tabla_base : "";
    html = `<option value="esp:${forzarEspId}"${usuarioRestringidoADesarrollo() ? " disabled" : ""}>${escaparHtml(nombre)}${tabla ? ` (${escaparHtml(tabla)})` : ""}${usuarioRestringidoADesarrollo() ? " · No disponible en Desarrollo" : ""}</option>`;
  } else {
    html = `<optgroup label="Bases">` +
      `<option value="desarrollo">Desarrollo (pacientes_dev)</option>` +
      (restringido ? "" : `<option value="produccion">Producción (pacientes_prod)</option>`) +
      `</optgroup>`;
    if (misAreas.length && !usuarioRestringidoADesarrollo()) {
      html += `<optgroup label="Áreas">` +
        misAreas.map((e) => `<option value="esp:${e.id}">${escaparHtml(e.nombre_visible)} (${escaparHtml(e.nombre_tabla_base)})</option>`).join("") +
        `</optgroup>`;
    }
  }
  sel.innerHTML = `<option value="" selected disabled>— Seleccione base de datos —</option>` + html;
  sel.value = "";
}

async function cargar() {
  for (let intento = 1; intento <= 2; intento++) {
    try {
      const res = await fetch(API_URL, { headers: authHeaders(), cache: "no-store" });
      if (res.status === 401) { window.snwSesionExpirada(); return; }
      if (!res.ok) throw new Error(res.status);
      const datos = await res.json();
      if (!Array.isArray(datos)) throw new Error("formato inválido");
      // Las plantillas de call center se gestionan desde el Historial.
      plantillas = datos.filter((p) => !p.especial && esPlantillaValida(p));
      renderLista(buscadorEl.value);
      // Restaura la selección guardada (si la plantilla sigue existiendo).
      try {
        const guardada = Number(localStorage.getItem("snw_tpl_sel") || 0) || null;
        if (guardada && plantillas.some((p) => p.id === guardada)) {
          tplSelId = guardada;
          renderLista(buscadorEl.value);
          if (tabMsg === "envios") abrirModalConf(guardada);
          else if (tabMsg === "programados") actualizarProgPlantilla();
        }
      } catch { /* sin almacenamiento */ }
      if (activaId === null) {
        const rest = plantillas.find((p) => p.id === tplSelId);
        if (rest) abrir(rest.id);
        else seleccionarDefault();
      }
      return;
    } catch (err) {
      console.error("[app.js cargar()]", err);
      if (intento === 2) {
        toast("Error al conectar con el servidor.", "error");
      } else {
        await new Promise((r) => setTimeout(r, 900));
      }
    }
  }
}

// El servidor ya revisa solo el estado en Meta (cron); esto solo hace que la
// pantalla se entere sin que alguien tenga que recargar. Corre cada 30 s,
// en silencio (sin tocar nada si falla), y avisa con un toast cuando detecta
// que una plantilla pasó a estar aprobada.
async function revisarPlantillasEnSegundoPlano() {
  try {
    const res = await fetch(API_URL, { headers: authHeaders(), cache: "no-store" });
    if (res.status === 401) { window.snwSesionExpirada(); return; }
    if (!res.ok) return;
    const datos = await res.json();
    if (!Array.isArray(datos)) return;
    const nuevas = datos.filter((p) => !p.especial && esPlantillaValida(p));

    for (const p of nuevas) {
      const previa = plantillas.find((x) => x.id === p.id);
      if (!previa) continue;
      if (!esPlantillaAprobada(previa) && esPlantillaAprobada(p)) {
        toast(`✨ «${p.nombre}» fue aprobada por Meta.`, "ok");
      } else if (!esPlantillaRechazada(previa) && esPlantillaRechazada(p)) {
        toast(`⚠️ «${p.nombre}» fue rechazada por Meta.`, "error");
      }
    }

    plantillas = nuevas;
    renderLista(buscadorEl.value);
    if (activaId) {
      const actual = plantillas.find((x) => x.id === activaId);
      if (actual) refrescarVistaPlantillaActiva(actual);
    }
  } catch {
    // Sin conexión momentánea: se reintenta en el próximo ciclo.
  }
}

async function crearPlantilla(nombre, texto, whatsapp_template_lang, whatsapp_template_categoria, area_id) {
  const res = await fetch(API_URL, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ clave: slug(nombre), nombre, texto, whatsapp_template_lang, whatsapp_template_categoria, area_id }),
  });
  if (res.status === 401) { window.snwSesionExpirada(); return Promise.reject(new Error("Sesión expirada")); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail ?? data.error ?? `Error ${res.status}`);
  return data;
}

async function actualizarPlantilla(id, nombre, texto, whatsapp_template_lang, whatsapp_template_categoria, area_id) {
  const res = await fetch(`${API_URL}/${id}`, {
    method: "PUT",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ nombre, texto, whatsapp_template_lang, whatsapp_template_categoria, area_id }),
  });
  if (res.status === 401) { window.snwSesionExpirada(); return Promise.reject(new Error("Sesión expirada")); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail ?? data.error ?? `Error ${res.status}`);
  return data;
}

async function eliminarPlantilla(id) {
  const res = await fetch(`${API_URL}/${id}`, { method: "DELETE", headers: authHeaders() });
  if (res.status === 401) { window.snwSesionExpirada(); return Promise.reject(new Error("Sesión expirada")); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail ?? data.error ?? `Error ${res.status}`);
  return data;
}

function slug(texto) {
  return texto
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 40);
}

function escaparHtml(texto) {
  const div = document.createElement("div");
  div.textContent = texto;
  return div.innerHTML;
}

// Convierte el formato de WhatsApp (*negrita*, _cursiva_, ~tachado~,
// ```monoespaciado```) a HTML para la vista previa. Se escapa primero el
// texto para evitar inyección de HTML.
function formatearWhatsApp(texto) {
  let html = escaparHtml(texto);
  html = html.replace(/```([\s\S]+?)```/g, "<code>$1</code>");
  html = html.replace(/(^|[\s(>])\*(\S(?:[^*\n]*\S)?)\*(?=$|[\s).,!?<])/g, "$1<strong>$2</strong>");
  html = html.replace(/(^|[\s(>])_(\S(?:[^_\n]*\S)?)_(?=$|[\s).,!?<])/g, "$1<em>$2</em>");
  html = html.replace(/(^|[\s(>])~(\S(?:[^~\n]*\S)?)~(?=$|[\s).,!?<])/g, "$1<del>$2</del>");
  return html; // el contenedor .bubble ya usa white-space: pre-wrap para los saltos de línea
}

function crearItemPlantilla(p) {
  const primeraLinea = String(p.texto ?? "").split("\n")[0] || "(sin contenido)";
  const aprobada = esPlantillaAprobada(p);
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "tpl-item" +
    (p.id === tplSelId ? " tpl-item--activa" : "") +
    (aprobada ? "" : " tpl-item--pendiente");
  let estadoTag = "";
  if (esPlantillaRechazada(p)) {
    // Las pendientes de revisión no llevan badge: ya están agrupadas bajo
    // «Plantillas pendientes de aprobación por Meta», el badge era redundante.
    estadoTag = ` <span class="tpl-item__estado tpl-item__estado--rechazada">` +
      `${escaparHtml(etiquetaEstadoMeta(p.whatsapp_template_status))}</span>`;
  } else if (esRecienAprobada(p)) {
    estadoTag = ` <span class="tpl-item__estado tpl-item__estado--nueva">✨ Aprobada</span>`;
  }
  const ap = p.aprobacion_estado || "aprobada";
  if (ap === "pendiente") {
    estadoTag += ` <span class="tpl-item__estado">⏳ Por aprobar</span>`;
  } else if (ap === "rechazada") {
    estadoTag += ` <span class="tpl-item__estado tpl-item__estado--rechazada">Rechazada</span>`;
  }
  if (p.area_id != null) {
    const nombreEsp = nombreArea(p.area_id) || "Área";
    estadoTag += ` <span class="tpl-item__estado">${escaparHtml(nombreEsp)}</span>`;
  }
  btn.innerHTML =
    `<span class="tpl-item__nombre">${escaparHtml(p.nombre ?? "(sin nombre)")}${estadoTag}</span>` +
    `<span class="tpl-item__vista">${escaparHtml(primeraLinea)}</span>`;
  btn.addEventListener("click", () => clicPlantilla(p.id));
  if (p.id !== tplSelId) return btn;
  // Aleta con X en la plantilla seleccionada para deseleccionarla.
  const wrap = document.createElement("div");
  wrap.className = "tpl-item-wrap";
  const x = document.createElement("button");
  x.type = "button";
  x.className = "tpl-quitar";
  x.title = "Quitar selección";
  x.setAttribute("aria-label", "Quitar selección de plantilla");
  x.innerHTML = '<i class="fa-solid fa-xmark"></i>';
  x.addEventListener("click", (ev) => {
    ev.stopPropagation();
    cancelarEdicion();
  });
  wrap.appendChild(btn);
  wrap.appendChild(x);
  return wrap;
}

// Click en la lista según la tab: editor, envío o programar. La selección
// (tplSelId) es única y persiste entre tabs.
function clicPlantilla(id) {
  if (tabMsg === "envios") {
    tplSelId = id;
    renderLista(buscadorEl.value);
    abrirModalConf(id);
    guardarSelTpl();
    return;
  }
  if (tabMsg === "programados") {
    tplSelId = id;
    seleccionarParaProgramar();
    guardarSelTpl();
    return;
  }
  if (intentarAbrir(id)) tplSelId = id;
  guardarSelTpl();
  renderLista(buscadorEl.value);
}

function seleccionarParaProgramar() {
  actualizarProgPlantilla();
  cargarCupoProg();
  actualizarPasosProg();
  renderLista(buscadorEl.value);
}

function actualizarProgPlantilla() {
  const el = document.getElementById("progPlantillaNombre");
  if (!el) return;
  const p = (plantillas || []).find((x) => x.id === tplSelId);
  el.textContent = p ? p.nombre : "—";
  const selBase = document.getElementById("selProgBase");
  if (selBase) {
    const restringido = usuarioRestringidoADesarrollo() || (window.snwRol || "") === "usuario";
    const areas = misAreas || [];
    const esHelloWorld = !!p && esPlantillaProtegida(p);
    const plantillaId = String(p?.id ?? "");
    const seleccionAnterior = selBase.dataset.plantillaId === plantillaId ? selBase.value : "";
    const opcionInicial = `<option value="" selected disabled>— Seleccione base de datos —</option>`;
    if (esHelloWorld) {
      selBase.innerHTML = opcionInicial + `<option value="desarrollo">Desarrollo (pacientes_dev)</option>`;
    } else if (p?.area_id != null) {
      const area = areas.find((item) => Number(item.id) === Number(p.area_id));
      selBase.innerHTML = opcionInicial + (area
        ? `<option value="esp:${area.id}"${usuarioRestringidoADesarrollo() ? " disabled" : ""}>${escaparHtml(area.nombre_visible)} (${escaparHtml(area.nombre_tabla_base)})${usuarioRestringidoADesarrollo() ? " · No disponible en Desarrollo" : ""}</option>`
        : "");
    } else {
      selBase.innerHTML =
        opcionInicial +
        `<optgroup label="Bases de datos">` +
          `<option value="desarrollo">Desarrollo (pacientes_dev)</option>` +
          (restringido ? "" : `<option value="produccion">Producción (pacientes_prod)</option>`) +
        `</optgroup>` +
        (areas.length && !usuarioRestringidoADesarrollo()
          ? `<optgroup label="Bases por área">` +
              areas.map((area) => `<option value="esp:${area.id}">${escaparHtml(area.nombre_visible)} (${escaparHtml(area.nombre_tabla_base)})</option>`).join("") +
            `</optgroup>`
          : "");
    }
    selBase.value = [...selBase.options].some((opcion) => opcion.value === seleccionAnterior)
      ? seleccionAnterior : "";
    selBase.dataset.plantillaId = plantillaId;
  }
  actualizarPasosProg();
}

// Pasos 1-plantilla, 2-área, 3-fecha y 4-límite: cada uno se habilita al
// completar el anterior.
function actualizarPasosProg() {
  const p = (plantillas || []).find((x) => x.id === tplSelId);
  const selA = document.getElementById("selProgBase");
  const inpF = document.getElementById("inpProgFecha");
  const inpL = document.getElementById("inpProgLimite");
  const selModo = document.getElementById("selProgLimiteModo");
  const inpPorcentaje = document.getElementById("inpProgPorcentaje");
  const btn = document.getElementById("btnProgramar");
  const paso1 = !!p;
  const paso2 = paso1 && selA && !!selA.value;
  const paso3Listo = !!(paso2 && inpF && inpF.value);
  const paso4Listo = !!(paso3Listo && limiteProgElegido());
  for (const [id, listo] of [["pasoProg1", paso1], ["pasoProg2", paso2], ["pasoProg3", paso3Listo], ["pasoProg4", paso4Listo]]) {
    const el = document.getElementById(id);
    if (el) el.classList.toggle("prog-paso--listo", !!listo);
  }
  if (selA) selA.disabled = !paso1;
  if (inpF) inpF.disabled = !paso2;
  if (selModo) selModo.disabled = !paso3Listo || progSinCupo;
  const modo = selModo?.value || "";
  if (inpPorcentaje) inpPorcentaje.disabled = !paso3Listo || modo !== "porcentaje" || progSinCupo;
  if (inpL) inpL.disabled = !paso3Listo || progSinCupo || !modo || modo === "porcentaje";
  if (btn) btn.disabled = !paso4Listo || progCreando || progSinCupo;
  actualizarCostoProg();
}

function actualizarCostoProg() {
  const modo = $("#selProgLimiteModo")?.value || "";
  const cantidad = limiteProgElegido() ? Number($("#inpProgLimite").value) : null;
  const base = $("#selProgBase")?.value || "";
  const fecha = $("#inpProgFecha")?.value.slice(0, 10) || "";
  const costo = base && fecha && progCupoKey === `${base}|${fecha}|${tplSelId || ""}`
    ? progCupo?.costo : null;
  pintarCostoEstimado(
    "filaCostoProg", "progCosto", "progCostoDetalle", costo, modo, cantidad
  );
}

function limiteProgElegido() {
  const modo = $("#selProgLimiteModo")?.value || "";
  const inpL = $("#inpProgLimite");
  if (!inpL || !["porcentaje", "cantidad"].includes(modo) || inpL.value.trim() === "") return false;
  const cantidad = Number(inpL.value);
  return Number.isInteger(cantidad) && cantidad >= 1 && cantidad <= techoProgLimite();
}

function actualizarLimiteDesdeModo() {
  const modo = $("#selProgLimiteModo")?.value || "";
  const inpL = $("#inpProgLimite");
  const wrap = $("#progPorcentajeWrap");
  const inpPorcentaje = $("#inpProgPorcentaje");
  const salida = $("#progPorcentajeValor");
  const label = $("#labelProgLimite");
  if (!inpL) return;
  if (wrap) wrap.hidden = modo !== "porcentaje";
  if (label) label.textContent = modo === "porcentaje" ? "Cantidad que se programará" : "Cantidad de pacientes";
  inpL.readOnly = modo === "porcentaje";
  if (modo === "porcentaje") {
    const porcentaje = Number(inpPorcentaje?.value) || 1;
    const cantidad = Math.max(1, Math.floor(techoProgLimite() * porcentaje / 100));
    inpL.value = techoProgLimite() > 0 ? String(cantidad) : "";
    if (salida) salida.textContent = `${porcentaje} % (${inpL.value || 0} pacientes)`;
  } else if (modo !== "cantidad") {
    inpL.value = "";
  }
  revisarLimiteProg();
  actualizarPasosProg();
}

function renderLista(filtro = "") {
  const q = filtro.trim().toLowerCase();
  const fEsp = filtroArea;
  const visibles = [...plantillas]
    .sort((a, b) => (b.actualizada || 0) - (a.actualizada || 0))
    .filter(
      (p) =>
        (fEsp === "todas" ||
          (fEsp === "global" ? p.area_id == null : String(p.area_id) === fEsp)) &&
        (!q ||
          String(p.nombre || "").toLowerCase().includes(q) ||
          String(p.texto || "").toLowerCase().includes(q))
    );

  listaEl.innerHTML = "";

  if (!visibles.length) {
    const li = document.createElement("li");
    li.className = "tpl-list__vacio";
    li.textContent = q ? "Sin resultados." : "No hay plantillas.";
    listaEl.appendChild(li);
    return;
  }

  // Solo una plantilla APROBADA por Meta se puede usar para enviar. Las
  // rechazadas se pueden editar/eliminar (para corregirlas o descartarlas),
  // por eso van en su propio grupo. La aprobación interna ocurre antes de
  // enviar la plantilla a Meta, por lo que ambas esperas deben distinguirse.
  const estaRechazada = (p) => esPlantillaRechazada(p) || aprobacionPlantilla(p) === "rechazada";
  const aprobadas = visibles.filter((p) => esPlantillaAprobada(p) && !estaRechazada(p));
  const rechazadas = visibles.filter(estaRechazada);
  const pendientesInternas = visibles.filter(
    (p) => aprobacionPlantilla(p) === "pendiente" && !estaRechazada(p)
  );
  const pendientesMeta = visibles.filter(
    (p) => aprobacionPlantilla(p) !== "pendiente" && !estaRechazada(p) && !esPlantillaAprobada(p)
  );

  const agregarGrupo = (titulo, lista, tono, icono) => {
    if (!lista.length) return;
    const encabezado = document.createElement("li");
    encabezado.className = `tpl-list__grupo tpl-list__grupo--${tono}`;
    encabezado.innerHTML = `<i class="fa-solid ${icono}"></i> ${escaparHtml(titulo)} (${lista.length})`;
    listaEl.appendChild(encabezado);
    for (const p of lista) listaEl.appendChild(crearItemPlantilla(p));
  };

  agregarGrupo("Plantillas aprobadas por Meta", aprobadas, "ok", "fa-circle-check");
  agregarGrupo("Plantillas rechazadas", rechazadas, "danger", "fa-circle-xmark");
  agregarGrupo("Plantillas pendientes de aprobación", pendientesInternas, "warn", "fa-clock");
  agregarGrupo("Plantillas pendientes de aprobación por Meta", pendientesMeta, "info", "fa-clock");
}

function actualizarPreview() {
  const texto = inpMensaje.value.trim();

  if (!texto) {
    previewTexto.textContent = "Aquí verás cómo llega el mensaje al paciente...";
    previewTexto.parentElement.classList.add("bubble--vacia");
  } else {
    let reemplazado = texto;
    for (const [clave, valor] of Object.entries(DATOS_EJEMPLO)) {
      reemplazado = reemplazado.replaceAll(`{${clave}}`, valor);
    }
    previewTexto.innerHTML = formatearWhatsApp(reemplazado);
    previewTexto.parentElement.classList.remove("bubble--vacia");
  }

  previewHora.textContent = new Date().toLocaleTimeString("es-CL", {
    hour: "2-digit",
    minute: "2-digit",
  });

  if (previewBotones) {
    previewBotones.innerHTML = BOTONES_PREDEFINIDOS
      .map((b) => `<div class="bubble__boton"><i class="fa-solid fa-reply"></i> ${escaparHtml(b)}</div>`)
      .join("");
  }
}

function actualizarContador() {
  const largo = inpMensaje.value.length;
  contadorEl.textContent = `${largo} / ${MAX_MENSAJE}`;
  contadorEl.classList.toggle("char-count--limite", largo > MAX_MENSAJE);
}

function validarComodines() {
  const desconocidos = [...inpMensaje.value.matchAll(/\{([^{}]+)\}/g)]
    .map((m) => m[1].trim())
    .filter((t) => !["nombre", "apellido"].includes(t));

  if (desconocidos.length) {
    const unicos = [...new Set(desconocidos)].map((t) => `{${t}}`).join(", ");
    avisoComodines.textContent =
      `Atención: ${unicos} no ${desconocidos.length > 1 ? "son datos reconocidos" : "es un dato reconocido"}. Revisa los botones disponibles arriba.`;
    avisoComodines.hidden = false;
  } else {
    avisoComodines.hidden = true;
  }
}

function refrescarEditor() {
  actualizarContador();
  validarComodines();
  actualizarPreview();
  actualizarEstadoBotonGuardar();
}

if (inpArea) inpArea.addEventListener("change", refrescarEditor);

function estadoActualEditor() {
  return JSON.stringify([inpNombre.value, inpMensaje.value, valTemplate(), valTemplateLang(), valTemplateCategoria(), inpArea ? inpArea.value : ""]);
}

function marcarSnapshot() {
  snapshot = estadoActualEditor();
  actualizarEstadoBotonGuardar();
}

function hayCambios() {
  return snapshot !== null && snapshot !== estadoActualEditor();
}

// El botón «Guardar» solo se habilita si hubo algún cambio desde que se abrió
// la plantilla (o desde el último guardado) Y están completos nombre, mensaje
// y área (al usuario normal se le exige área: no puede usar
// globales; para el resto, global es válido). Evita guardados vacíos/no-op.
function actualizarEstadoBotonGuardar() {
  if (!btnGuardar) return;
  const incompleto = !inpNombre.value.trim() || !inpMensaje.value.trim() ||
    ((window.snwRol || "") === "usuario" && !(inpArea && inpArea.value));
  btnGuardar.disabled = !hayCambios() || incompleto;
}

function renderEstadoMeta(p) {
  if (!bloqueEstadoMeta) return;

  const tieneTemplate = !!(p && p.whatsapp_template);
  bloqueEstadoMeta.hidden = !tieneTemplate;
  if (!tieneTemplate) return;

  const status = p.whatsapp_template_status || "DESCONOCIDO";
  badgeEstadoMeta.textContent = etiquetaEstadoMeta(status);
  badgeEstadoMeta.className = "estado-badge estado-" + (ETIQUETAS_ESTADO_META[status] ? status : "DESCONOCIDO");
  if (badgeAprobadaReciente) badgeAprobadaReciente.hidden = !esRecienAprobada(p);

  const motivo = p.whatsapp_template_rejected_reason || p.whatsapp_template_error;
  if (motivo && status !== "APPROVED") {
    motivoRechazoMeta.textContent = motivo;
    motivoRechazoMeta.hidden = false;
  } else {
    motivoRechazoMeta.hidden = true;
  }
}

// El nombre del template de Meta NO se elige a mano: se genera a partir del
// nombre de la plantilla (Meta solo admite minúsculas, números y "_"). El
// campo está siempre bloqueado. Si la plantilla ya tiene un template
// registrado en Meta, se muestra ese valor tal cual (es inmutable).
function sincronizarTemplateConNombre() {
  if (!hayTemplateMeta) return;
  const p = activaId ? plantillas.find((x) => x.id === activaId) : null;
  inpTemplate.value = p && p.whatsapp_template ? p.whatsapp_template : slug(inpNombre.value);
}

// Botones de acción (Guardar / Cancelar / Eliminar / Enviar mensaje) según el
// permiso de la cuenta Y el estado en Meta:
// - APPROVED: todo disponible, como siempre.
// - REJECTED: se puede editar/guardar/eliminar (para corregirla y volver a
//   mandarla a revisión, o descartarla), pero NO enviar.
// - pendiente de revisión (recién creada o PENDING): de solo lectura, sin
//   ningún botón, aunque la cuenta tenga permiso de edición.
let _timerEnfriamiento = null;

function pararCuentaRegresiva() {
  if (_timerEnfriamiento) { clearInterval(_timerEnfriamiento); _timerEnfriamiento = null; }
}

function textoEnfriamiento(p) {
  return `Esta plantilla no se puede editar todavía: faltan ${cuentaRegresivaEditable(p)} para que vuelva ` +
    "a estar disponible (Meta solo permite editar un template una vez al día). Sí se puede eliminar.";
}

// Actualiza el aviso cada 30s mientras dure el enfriamiento; al llegar a
// 00:00 refresca los botones/campos solo, sin que haga falta recargar.
function iniciarCuentaRegresiva(p) {
  pararCuentaRegresiva();
  _timerEnfriamiento = setInterval(() => {
    if (!editadaRecientemente(p)) {
      pararCuentaRegresiva();
      actualizarBotonesSegunEstado(p);
      actualizarBloqueoCampos();
      return;
    }
    if (avisoPendiente) avisoPendiente.textContent = textoEnfriamiento(p);
  }, 30000);
}

function actualizarBotonesSegunEstado(p) {
  const aprobada = esPlantillaAprobada(p);
  const editable = esPlantillaEditable(p);
  const enEnfriamiento = editable && editadaRecientemente(p);
  const ap = aprobacionPlantilla(p);
  // Solo se puede enviar lo aprobado por Meta Y por la revisión interna.
  const enviable = p && aprobada && ap === "aprobada";
  // Meta limita la edición a una vez cada 24h, pero no el borrado: Eliminar
  // sigue disponible aunque Guardar esté bloqueado por el enfriamiento.
  // Además solo el creador edita/elimina las suyas (admin/dev, cualquiera).
  const puedeGuardar = PUEDE_EDITAR_PLANTILLAS && puedeEditarEsta(p) && editable && !enEnfriamiento && !esPlantillaProtegida(p);
  const puedeEliminar = PUEDE_EDITAR_PLANTILLAS && puedeEditarEsta(p) && editable && !esPlantillaProtegida(p);
  const puedeRevisar = p && ap === "pendiente" && puedeAprobarPlantillas();
  document.querySelectorAll(".wildcards").forEach((el) => { el.hidden = !puedeGuardar; });
  btnGuardar.hidden = !puedeGuardar;
  const btnAprobar = $("#btnAprobar");
  const btnRechazar = $("#btnRechazar");
  if (btnAprobar) btnAprobar.hidden = !puedeRevisar;
  if (btnRechazar) btnRechazar.hidden = !(p && ap === "pendiente" && puedeAprobarPlantillas());
  // Cancelar no guarda nada, solo deselecciona: debe poder usarse para salir
  // de una plantilla aunque Guardar esté bloqueado (en enfriamiento, pendiente
  // de revisión, etc.) y también al estar creando una plantilla nueva
  // (activaId es null igual que en el estado vacío, así que lo que
  // distingue "creando" de "nada seleccionado" es si el formulario está
  // visible, no si hay un `p`).
  if (btnCancelar) btnCancelar.hidden = formEl.style.display === "none";
  // Eliminar solo aplica si ya existe (tiene id) y se puede gestionar.
  btnEliminar.hidden = !(p && puedeEliminar);
  if (btnEnviarActual) btnEnviarActual.hidden = !(p && enviable);
  if (avisoPendiente) {
    avisoPendiente.classList.toggle(
      "aviso-rechazo",
      !!(p && (ap === "rechazada" || esPlantillaRechazada(p)))
    );
    if (p && esPlantillaProtegida(p)) {
      pararCuentaRegresiva();
      avisoPendiente.textContent =
        "La plantilla Hello World es una muestra de Meta: no se puede editar ni eliminar, solo usar para enviar mensajes.";
      avisoPendiente.hidden = false;
    } else if (p && ap === "pendiente") {
      pararCuentaRegresiva();
      avisoPendiente.textContent =
        "Esta plantilla está pendiente de aprobación interna: todavía no se envió a Meta " +
        "y no se puede usar para enviar mensajes hasta que un administrador o supervisor la apruebe.";
      avisoPendiente.hidden = false;
    } else if (p && ap === "rechazada") {
      pararCuentaRegresiva();
      avisoPendiente.textContent =
        "Esta plantilla fue rechazada en la revisión interna" +
        (p.rechazo_motivo ? `: «${p.rechazo_motivo}».` : ".") +
        " Corrígela y guárdala para pedir revisión de nuevo.";
      avisoPendiente.hidden = false;
    } else if (p && !editable) {
      pararCuentaRegresiva();
      avisoPendiente.textContent = p.whatsapp_template_status === "PENDING"
        ? "Esta plantilla está pendiente de aprobación por Meta: no se puede editar, guardar, eliminar ni usar para enviar mensajes hasta que se apruebe."
        : `Esta plantilla está ${etiquetaEstadoMeta(p.whatsapp_template_status).toLowerCase()} en Meta: ` +
          "no se puede editar, guardar, eliminar ni usar para enviar mensajes hasta que se resuelva.";
      avisoPendiente.hidden = false;
    } else if (p && enEnfriamiento) {
      avisoPendiente.textContent = textoEnfriamiento(p);
      avisoPendiente.hidden = false;
      iniciarCuentaRegresiva(p);
    } else if (p && editable && !aprobada) {
      pararCuentaRegresiva();
      avisoPendiente.textContent =
        "Esta plantilla fue rechazada por Meta: se puede editar y guardar para mandarla de nuevo " +
        "a revisión, o eliminarla. No se puede usar para enviar mensajes hasta que se apruebe.";
      avisoPendiente.hidden = false;
    } else {
      pararCuentaRegresiva();
      avisoPendiente.hidden = true;
    }
  }
}

// El nombre de la plantilla es permanente: una vez creada solo puede
// cambiarse borrando la plantilla y creando otra (la clave interna se
// deriva del nombre al crear y no se recalcula al editar).
//
// El idioma y la categoría del template son inmutables una vez creados
// (Meta no permite cambiarlos). Por eso solo se pueden elegir al crear una
// plantilla nueva; una vez que la plantilla ya tiene un template registrado
// en Meta, quedan bloqueados. Las plantillas antiguas sin template todavía
// pueden completarlos. El nombre del template está SIEMPRE bloqueado.
//
// Si la plantilla está pendiente de revisión, o fue editada hace menos de
// 24h (no es editable, ver esPlantillaEditable / editadaRecientemente), TODO
// el formulario queda deshabilitado (misma rama que la de "sin permiso de
// edición") — ver actualizarBotonesSegunEstado.
function actualizarBloqueoCampos() {
  const p = activaId ? plantillas.find((x) => x.id === activaId) : null;
  // El aviso de "nombre permanente" y su texto de ayuda dependen solo de
  // si se está creando (sin activaId) o editando una plantilla que ya
  // existe — no de si además se puede guardar en este momento. Por eso se
  // calculan antes del return de la rama bloqueada: si no, se quedaban
  // con el valor por defecto del HTML (visible) apenas se abría una
  // plantilla existente que estuviera en enfriamiento o de solo lectura.
  const esExistente = !!activaId;
  if (avisoNombrePermanente) avisoNombrePermanente.hidden = esExistente;
  if (hintNombre) hintNombre.hidden = esExistente;

  if (!PUEDE_EDITAR_PLANTILLAS || (p && (!esPlantillaEditable(p) || editadaRecientemente(p) || !puedeEditarEsta(p)))) {
    formEl.querySelectorAll("input, textarea, select").forEach((el) => { el.disabled = true; });
    return;
  }
  // El mensaje se deja sin disabled explícito acá arriba: solo se pone
  // en true en la rama bloqueada de más arriba, nunca se revierte a
  // false al pasar a una plantilla sí editable — quedaba pegado en
  // disabled para siempre después de abrir la primera no editable.
  inpMensaje.disabled = false;
  inpNombre.disabled = esExistente;
  inpNombre.title = esExistente
    ? "El nombre es permanente y no se puede cambiar. Para usar otro nombre, elimina la plantilla y crea una nueva."
    : "";

  if (!hayTemplateMeta) return;
  // «Ya registrado en Meta»: solo si el template existe realmente allá
  // (whatsapp_template_id). Mientras no se haya podido registrar, el idioma y
  // la categoría se pueden seguir corrigiendo.
  const yaTieneTemplate = !!(p && p.whatsapp_template_id);
  // El nombre del template SIEMPRE está bloqueado (se genera desde el nombre).
  inpTemplate.disabled = true;
  inpTemplateLang.disabled = yaTieneTemplate;
  inpTemplateCategoria.disabled = yaTieneTemplate;
  sincronizarTemplateConNombre();
  // La ayuda del nombre del template solo se muestra al crear una plantilla
  // nueva; en una ya guardada no aporta (el campo es automático e inmutable).
  if (hintTemplate) hintTemplate.hidden = esExistente;
  inpTemplate.title = esExistente
    ? "El nombre del template de Meta es definitivo y no se puede cambiar."
    : "Se genera automáticamente a partir del nombre de la plantilla.";
  inpTemplateLang.title = yaTieneTemplate
    ? "El idioma del template es definitivo y no se puede cambiar una vez creado."
    : "";
  inpTemplateCategoria.title = yaTieneTemplate
    ? "La categoría no se puede cambiar una vez que el template fue aprobado por Meta."
    : "";
}

function abrir(id) {
  const p = plantillas.find((x) => x.id === id);
  if (!p) return;
  activaId = id;
  tplSelId = id;
  guardarSelTpl();
  estadoVacio.style.display = "none";
  formEl.style.display = "";
  tituloForm.textContent = (PUEDE_EDITAR_PLANTILLAS && esPlantillaEditable(p)) ? `Editando: ${p.nombre}` : p.nombre;
  inpNombre.value = p.nombre;
  inpMensaje.value = p.texto;
  if (inpArea) {
    const espVal = p.area_id != null ? String(p.area_id) : "";
    if (espVal && ![...inpArea.options].some((o) => o.value === espVal)) {
      const nombreEsp = nombreArea(p.area_id) || "Área";
      inpArea.add(new Option(nombreEsp, espVal));
    }
    inpArea.value = espVal;
  }
  if (hayTemplateMeta) {
    inpTemplateLang.value = p.whatsapp_template_lang || "es";
    // Todas las plantillas son categoría Marketing; una plantilla antigua con
    // otra categoría conserva la suya (Meta no permite cambiarla ya creada).
    inpTemplateCategoria.value = p.whatsapp_template_categoria || "MARKETING";
  }
  renderEstadoMeta(p);
  actualizarBotonesSegunEstado(p);
  actualizarInfoCreacion(p);
  inpNombre.classList.remove("invalido");
  inpMensaje.classList.remove("invalido");
  if (hayTemplateMeta) { inpTemplate.classList.remove("invalido"); inpTemplateCategoria.classList.remove("invalido"); }
  actualizarBloqueoCampos();
  refrescarAvisoSinArea();
  refrescarEditor();
  marcarSnapshot();
  renderLista(buscadorEl.value);
}

// Quién y cuándo creó la plantilla (trazabilidad); las plantillas sincronizadas
// desde Meta o creadas antes de este campo no tienen `creado_por`.
function actualizarInfoCreacion(p) {
  if (!infoCreacion) return;
  if (!p || !p.creado_por) {
    infoCreacion.hidden = true;
    return;
  }
  const fecha = p.creada_en
    ? new Date(p.creada_en).toLocaleString("es-CL", { dateStyle: "short", timeStyle: "short" })
    : null;
  infoCreacion.textContent = fecha
    ? `Creada por ${p.creado_por} el ${fecha}.`
    : `Creada por ${p.creado_por}.`;
  infoCreacion.hidden = false;
}

function modoNueva() {
  activaId = null;
  estadoVacio.style.display = "none";
  formEl.style.display = "";
  tituloForm.textContent = "Nueva plantilla";
  inpNombre.value = "";
  inpMensaje.value = "";
  if (inpArea) inpArea.value = "";
  if (hayTemplateMeta) {
    inpTemplateLang.value = "es";
    inpTemplateCategoria.value = "MARKETING";
  }
  actualizarInfoCreacion(null);
  if (bloqueEstadoMeta) bloqueEstadoMeta.hidden = true;
  actualizarBotonesSegunEstado(null);
  inpNombre.classList.remove("invalido");
  inpMensaje.classList.remove("invalido");
  if (hayTemplateMeta) { inpTemplate.classList.remove("invalido"); inpTemplateCategoria.classList.remove("invalido"); }
  actualizarBloqueoCampos();
  refrescarAvisoSinArea();
  refrescarEditor();
  marcarSnapshot();
  renderLista(buscadorEl.value);
  inpNombre.focus();
}

function modoVacia() {
  activaId = null;
  tplSelId = null;
  snapshot = null;
  formEl.style.display = "none";
  estadoVacio.style.display = "flex";
  tituloForm.textContent = "Plantillas";
  actualizarBotonesSegunEstado(null);
  renderLista(buscadorEl.value);
}

function intentarAbrir(id) {
  if (id === activaId) return true;
  if (hayCambios() && !confirm("Tienes cambios sin guardar. ¿Deseas descartarlos?")) {
    return false;
  }
  abrir(id);
  return true;
}

function intentarNueva() {
  if (hayCambios() && !confirm("Tienes cambios sin guardar. ¿Deseas descartarlos?")) {
    return;
  }
  modoNueva();
}

function confirmarDescarteEditor() {
  return !hayCambios() || confirm("¿Descartar los cambios?");
}

function cancelarEdicion() {
  if (!confirmarDescarteEditor()) return false;
  // La selección es compartida por las tres pestañas: limpiar todas las vistas.
  resetearCardEnvio();
  return true;
}

formEl.addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!PUEDE_EDITAR_PLANTILLAS) return;
  // Blindaje: aunque el botón esté oculto, el formulario no debe guardarse
  // si la plantilla activa todavía está pendiente de revisión en Meta, o se
  // editó hace menos de 24h (Ctrl+S, Enter...); aprobada o rechazada, y fuera
  // del enfriamiento de 24h, sí se puede guardar.
  if (activaId) {
    const activa = plantillas.find((x) => x.id === activaId);
    if (!esPlantillaEditable(activa) || editadaRecientemente(activa)) return;
    if (!puedeEditarEsta(activa)) return toast("Solo puedes editar las plantillas que tú creaste.", "error");
  }

  const nombre = inpNombre.value.trim();
  const texto = inpMensaje.value.trim();
  const whatsapp_template = valTemplate().trim();
  const whatsapp_template_lang = valTemplateLang() || "es";
  const whatsapp_template_categoria = valTemplateCategoria().trim();

  inpNombre.classList.toggle("invalido", !nombre);
  inpMensaje.classList.toggle("invalido", !texto);
  // whatsapp_template se genera desde el nombre; solo puede quedar vacío si el
  // nombre no tiene ninguna letra ni número.
  if (hayTemplateMeta) {
    inpNombre.classList.toggle("invalido", !nombre || !/^[a-z0-9_]+$/.test(whatsapp_template));
  }

  if (!nombre) return toast("Falta el nombre.", "error");
  if (!texto) return toast("El mensaje está vacío.", "error");
  if (inpMensaje.value.length > MAX_MENSAJE) {
    inpMensaje.classList.add("invalido");
    return toast(
      `El mensaje tiene ${inpMensaje.value.length} caracteres y el máximo es ${MAX_MENSAJE}. Acórtalo para poder guardar.`,
      "error"
    );
  }
  if (hayTemplateMeta && !whatsapp_template_categoria) {
    inpTemplateCategoria.classList.add("invalido");
    return toast("Selecciona la categoría del template (Utility, Marketing o Authentication) para poder guardar la plantilla.", "error");
  }
  if (hayTemplateMeta && !/^[a-z0-9_]+$/.test(whatsapp_template)) {
    return toast("El nombre de la plantilla debe tener al menos una letra o número (se usa para el template de Meta).", "error");
  }

  const duplicada = plantillas.some(
    (p) =>
      p.nombre.toLowerCase() === nombre.toLowerCase() &&
      p.id !== activaId
  );
  if (duplicada) {
    inpNombre.classList.add("invalido");
    return toast("Ya existe una plantilla con ese nombre.", "error");
  }

  const claveNueva = slug(nombre);
  const choqueClave = plantillas.find(
    (p) => p.clave === claveNueva && p.id !== activaId
  );
  if (choqueClave) {
    inpNombre.classList.add("invalido");
    return toast(
      `Ese nombre genera la clave "${claveNueva}", que ya usa "${choqueClave.nombre}". Elige otro nombre.`,
      "error"
    );
  }

  setGuardando(true);

  try {
    const area_id = inpArea && inpArea.value ? Number(inpArea.value) : null;
    if ((window.snwRol || "") === "usuario" && area_id == null) {
      return toast("Debes asociar la plantilla a una de tus áreas asignadas.", "error");
    }
    let fila;
    if (activaId) {
      fila = await actualizarPlantilla(activaId, nombre, texto, whatsapp_template_lang, whatsapp_template_categoria, area_id);
      const i = plantillas.findIndex((x) => x.id === activaId);
      if (i >= 0) plantillas[i] = fila;
    } else {
      fila = await crearPlantilla(nombre, texto, whatsapp_template_lang, whatsapp_template_categoria, area_id);
      plantillas.push(fila);
    }
    setGuardando(false);

    // Mensaje según el estado del template en Meta
    const accion = activaId ? "actualizada" : "creada";
    const tplStatus = fila.whatsapp_template_status;
    const tplError = fila.whatsapp_template_error;
    if (tplError) {
      toast(`Plantilla ${accion}, pero el template en Meta falló: ${tplError}`, "error");
    } else if (tplStatus) {
      toast(`Plantilla ${accion}. Template en Meta: ${tplStatus}.`, "ok");
    } else {
      toast(`Plantilla ${accion}.`, "ok");
    }

    abrir(fila.id);
  } catch (err) {
    console.error("[app.js:991]", err);
    setGuardando(false);
    toast(err.message === "Ya existe una plantilla con esa clave"
      ? "Ya existe una plantilla similar."
      : `Error al guardar: ${err.message}`, "error");
  }
});

btnEliminar.addEventListener("click", () => {
  const p = plantillas.find((x) => x.id === activaId);
  if (!p) return;
  $("#modalNombre").textContent = p.nombre;
  modalEl.hidden = false;
});

$("#btnModalCancelar").addEventListener("click", () => (modalEl.hidden = true));

$("#btnModalConfirmar").addEventListener("click", async () => {
  const btnConfirmar = $("#btnModalConfirmar");
  setConsultandoEstado(true, btnConfirmar, "Eliminando plantilla...");
  try {
    const data = await eliminarPlantilla(activaId);
    plantillas = plantillas.filter((x) => x.id !== activaId);
    modalEl.hidden = true;
    if (data && data.meta_advertencia) {
      toast(`Plantilla eliminada, pero en Meta: ${data.meta_advertencia}`, "error");
    } else if (data && data.meta_borrado) {
      toast("Plantilla eliminada (también en Meta).", "ok");
    } else {
      toast("Plantilla eliminada.", "ok");
    }
    modoVacia();
  } catch (err) {
    console.error("[app.js:1023]", err);
    modalEl.hidden = true;
    toast(`Error al eliminar: ${err.message}`, "error");
  } finally {
    setConsultandoEstado(false, btnConfirmar);
  }
});

$("#btnCancelar").addEventListener("click", cancelarEdicion);

async function aprobarPlantillaActiva() {
  if (!activaId || revisionEnCurso) return;
  revisionEnCurso = true;
  bloquearBotonesRevision(true);
  try {
    const res = await fetch(`${API_URL}/${activaId}/aprobar`, {
      method: "POST", headers: authHeaders(),
    });
    const data = await res.json().catch(() => ({}));
    if (res.status === 401) { window.snwSesionExpirada(); return; }
    if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : `Error ${res.status}`);
    const i = plantillas.findIndex((x) => x.id === activaId);
    if (i >= 0) plantillas[i] = data;
    toast("Plantilla aprobada: ahora va a Meta para su revisión.", "ok");
    abrir(activaId);
  } catch (err) {
    console.error("[app.js aprobarPlantillaActiva()]", err);
    toast(`No se pudo aprobar: ${err.message}`, "error");
  } finally {
    revisionEnCurso = false;
    bloquearBotonesRevision(false);
  }
}

async function rechazarPlantillaActiva(motivo) {
  if (!activaId || revisionEnCurso) return;
  revisionEnCurso = true;
  bloquearBotonesRevision(true);
  try {
    const res = await fetch(`${API_URL}/${activaId}/rechazar`, {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ motivo }),
    });
    const data = await res.json().catch(() => ({}));
    if (res.status === 401) { window.snwSesionExpirada(); return; }
    if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : `Error ${res.status}`);
    const i = plantillas.findIndex((x) => x.id === activaId);
    if (i >= 0) plantillas[i] = data;
    toast("Plantilla rechazada: no irá a Meta.", "ok");
    abrir(activaId);
  } catch (err) {
    console.error("[app.js rechazarPlantillaActiva()]", err);
    toast(`No se pudo rechazar: ${err.message}`, "error");
  } finally {
    revisionEnCurso = false;
    bloquearBotonesRevision(false);
  }
}

// Mientras aprobar/rechazar está en curso se bloquean ambos botones (y sus
// confirmaciones en los modales) para que no se disparen las dos acciones
// a la vez ni se pulse dos veces la misma.
let revisionEnCurso = false;
function bloquearBotonesRevision(bloquear) {
  for (const id of ["btnAprobar", "btnRechazar", "btnConfirmarAprobar", "btnConfirmarRechazar"]) {
    const b = document.getElementById(id);
    if (b) b.disabled = bloquear;
  }
}

const _btnAprobar = $("#btnAprobar");
const _modalAprobar = $("#modalAprobar");
if (_btnAprobar) _btnAprobar.addEventListener("click", () => {
  if (!activaId) return;
  const p = plantillas.find((x) => x.id === activaId);
  $("#aprobarNombre").textContent = p?.nombre ?? "";
  _modalAprobar.hidden = false;
});
const _btnRechazar = $("#btnRechazar");
const _modalRechazar = $("#modalRechazar");
if (_btnRechazar) _btnRechazar.addEventListener("click", () => {
  if (!activaId) return;
  const p = plantillas.find((x) => x.id === activaId);
  $("#rechazarNombre").textContent = p?.nombre ?? "";
  $("#rechazoMotivo").value = "";
  _modalRechazar.hidden = false;
  $("#rechazoMotivo").focus();
});
$("#btnCancelarAprobar").addEventListener("click", () => (_modalAprobar.hidden = true));
_modalAprobar.addEventListener("click", (e) => { if (e.target === _modalAprobar) _modalAprobar.hidden = true; });
$("#btnCancelarRechazar").addEventListener("click", () => (_modalRechazar.hidden = true));
_modalRechazar.addEventListener("click", (e) => { if (e.target === _modalRechazar) _modalRechazar.hidden = true; });
$("#btnConfirmarAprobar").addEventListener("click", () => {
  _modalAprobar.hidden = true;
  aprobarPlantillaActiva();
});
$("#btnConfirmarRechazar").addEventListener("click", () => {
  const motivo = $("#rechazoMotivo").value.trim();
  _modalRechazar.hidden = true;
  rechazarPlantillaActiva(motivo);
});

document.querySelectorAll(".chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    const token = chip.dataset.wc;
    const pos = inpMensaje.selectionStart ?? inpMensaje.value.length;
    const antes = inpMensaje.value.slice(0, pos);
    const despues = inpMensaje.value.slice(inpMensaje.selectionEnd ?? pos);
    inpMensaje.value = antes + token + despues;
    const nuevaPos = pos + token.length;
    inpMensaje.focus();
    inpMensaje.setSelectionRange(nuevaPos, nuevaPos);
    refrescarEditor();
  });
});

inpNombre.addEventListener("input", () => {
  inpNombre.classList.remove("invalido");
  // El nombre del template de Meta refleja el nombre de la plantilla en vivo.
  sincronizarTemplateConNombre();
  if (hayTemplateMeta) inpTemplate.classList.remove("invalido");
  actualizarEstadoBotonGuardar();
});
inpMensaje.addEventListener("input", refrescarEditor);
if (hayTemplateMeta) {
  inpTemplateLang.addEventListener("change", actualizarEstadoBotonGuardar);
  inpTemplateCategoria.addEventListener("change", actualizarEstadoBotonGuardar);
}

buscadorEl.addEventListener("input", () => renderLista(buscadorEl.value));
$("#btnNueva")?.addEventListener("click", intentarNueva);
$("#btnNuevaEmpty")?.addEventListener("click", intentarNueva);

document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
    e.preventDefault();
    if (formEl.style.display !== "none") formEl.requestSubmit();
  }
  if (e.key === "Escape" && modalEl.hidden && formEl.style.display !== "none") {
    cancelarEdicion();
  }
});

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

// Bloquea/desbloquea toda la interfaz (botones, inputs, selects, textareas).
function bloquearInterfaz(bloquear) {
  document.querySelectorAll("button, input, select, textarea").forEach((el) => {
    el.disabled = bloquear;
  });
  // Al desbloquear, el nombre y los campos de Meta de una plantilla ya
  // guardada deben seguir bloqueados (no los reactivamos con el resto).
  if (!bloquear) actualizarBloqueoCampos();
}

// Bloquea toda la interfaz durante el guardado y muestra el estado
// en el botón Guardar (con spinner).
function setGuardando(guardando) {
  bloquearInterfaz(guardando);
  if (btnGuardar) {
    btnGuardar.innerHTML = guardando
      ? '<span class="spinner"></span> Guardando...'
      : "Guardar";
  }
}

// Bloquea toda la interfaz mientras se consulta el estado de un template en
// Meta (individual o "Actualizar estados"), igual que al guardar.
function setConsultandoEstado(consultando, boton, textoConsultando) {
  bloquearInterfaz(consultando);
  if (!boton) return;
  if (consultando) {
    boton.dataset.textoOriginal = boton.dataset.textoOriginal ?? boton.textContent;
    boton.innerHTML = `<span class="spinner"></span> ${textoConsultando}`;
  } else {
    boton.textContent = boton.dataset.textoOriginal ?? boton.textContent;
  }
}

let ambienteConf = localStorage.getItem("snw_ambiente_admin") || localStorage.getItem("snw_ambiente") || "desarrollo";
let entornoGlobal = null;
let timerPollingConf = null;
let envioEnCursoConf = false;
let jobIdActualConf = null;
let totalActualConf = 0;
let hechosActualConf = 0;

let confPlantillaId = null; // plantilla de la card de envío (puede diferir del editor)
let maxManualConf = 0;
let disponiblesBaseManualConf = 0;
let disponiblesMetaManualConf = null;
let tarifaCostoManualConf = null;
let consultaManualConf = 0;

// Solo se bloquean los controles de envío de la card: el resto (tabs, lista,
// programar, otras bases) sigue usable para lanzar otro envío en paralelo.
const CONTROLES_ENVIO_CONF = ["btnLanzarConf", "selBaseConf", "selLimiteModoConf", "limiteRangeConf", "limiteNumConf"];
function setBloqueoEnvioConf(bloquear) {
  envioEnCursoConf = bloquear;
  CONTROLES_ENVIO_CONF.forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.disabled = bloquear;
  });
  // Cancelar y Limpiar de la card de envío siempre quedan disponibles.
  ["btnCancelarConf", "btnCerrarConf"].forEach((id) => {
    const b = document.getElementById(id);
    if (b) b.disabled = false;
  });
  if (!bloquear) actualizarBloqueoCampos();
  actualizarPasosManual();
}

// Sincronizar con el entorno global de la configuración: si cambió a produccion/desarrollo, actualizar la selección
fetch("api/configuracion", { headers: authHeaders(), cache: "no-store" })
  .then((r) => (r.ok ? r.json() : null))
  .then((cfg) => {
    if (cfg && cfg.entorno) {
      entornoGlobal = cfg.entorno;
    }
    if (cfg && cfg.entorno && cfg.entorno !== ambienteConf) {
      ambienteConf = cfg.entorno;
      localStorage.setItem("snw_ambiente_admin", ambienteConf);
      localStorage.setItem("snw_ambiente", ambienteConf);
      actualizarBadgeMensajeria();
    }
  })
  .catch(() => {});

async function actualizarBadgeMensajeria() {
  try {
    const r = await fetch(`api/configuracion?ambiente=${ambienteConf}`, { headers: authHeaders(), cache: "no-store" });
    if (!r.ok) return;
    const cfg = await r.json();
    if (typeof cfg.plantillas_badge_aprobada_minutos === "number") {
      MINUTOS_APROBADA_RECIENTE = cfg.plantillas_badge_aprobada_minutos;
    }
  } catch {}
}

// Si nunca se eligió base de datos, adoptar el entorno global del servidor
if (!localStorage.getItem("snw_ambiente_admin") && !localStorage.getItem("snw_ambiente")) {
  fetch("api/configuracion", { headers: authHeaders(), cache: "no-store" })
    .then((r) => (r.ok ? r.json() : null))
    .then((cfg) => {
      if (cfg && cfg.entorno) {
        entornoGlobal = cfg.entorno;
      }
      if (cfg && cfg.entorno && cfg.entorno !== ambienteConf) {
        ambienteConf = cfg.entorno;
        localStorage.setItem("snw_ambiente_admin", ambienteConf);
        localStorage.setItem("snw_ambiente", ambienteConf);
        actualizarBadgeMensajeria();
      }
    })
    .catch(() => {});
}
actualizarBadgeMensajeria();

// Desarrollo bloquea producción y áreas para todos los roles; el permiso de
// envío directo solo elimina la confirmación en producción cuando esa está activa.
function usuarioRestringidoADesarrollo() {
  return entornoGlobal === "desarrollo";
}

// Aplica la restricción de base de datos en la card de envío.
function aplicarRestriccionAmbiente() {
  // Las opciones se reconstruyen al abrir el modal (construirOpcionesBaseConf
  // ya omite producción si hay restricción); acá solo se corrige la variable
  // si había quedado en un valor no permitido.
  const restringido = usuarioRestringidoADesarrollo();

  if (restringido && ambienteConf === "produccion") {
    ambienteConf = "desarrollo";
    localStorage.setItem("snw_ambiente", ambienteConf);
    localStorage.setItem("snw_ambiente_admin", ambienteConf);
    actualizarBadgeMensajeria();
  }
}

function abrirModalConf(id = tplSelId) {
  const p = plantillas.find((x) => x.id === id);
  if (!p) return;
  tplSelId = id;
  confPlantillaId = id;
  maxManualConf = 0;
  disponiblesBaseManualConf = 0;
  disponiblesMetaManualConf = null;
  tarifaCostoManualConf = null;
  $("#selLimiteModoConf").value = "";
  $("#limiteNumConf").value = "";
  $("#confNombre").textContent = p?.nombre ?? "";

  aplicarRestriccionAmbiente();
  // Si la plantilla es de un área, el select ofrece solo esa base;
  // en todos los casos el usuario debe elegirla explícitamente.
  construirOpcionesBaseConf(p?.area_id ?? null);
  if (esPlantillaProtegida(p)) {
    // Hello World es solo una muestra: únicamente desarrollo.
    const sel = $("#selBaseConf");
    if (sel) {
      sel.innerHTML = `<option value="" selected disabled>— Seleccione base de datos —</option>` +
        `<option value="desarrollo">Desarrollo (pacientes_dev)</option>`;
      sel.value = "";
    }
  }
  refrescarAvisoDevConf();
  refrescarAvisoAdminConf();
  actualizarResumenConf();

  $("#confProgreso").hidden = true;
  $("#listaRechazadosConf").innerHTML = "";
  $("#listaRechazadosConf").hidden = true;
  $("#btnCerrarConf").disabled = false;
  $("#btnLanzarConf").disabled = true;
  $("#btnLanzarConf").hidden = false;
  $("#envioSinPlantilla").hidden = true;
  $("#envioContenido").hidden = false;
  actualizarPasosManual();

  cambiarTabMsg("envios");
  renderLista(buscadorEl.value);
}

// La card de envío es persistente (no un modal): "cerrar" la devuelve a su
// estado inicial sin selección.
function resetearCardEnvio() {
  clearInterval(timerPollingConf);
  consultaManualConf += 1;
  confPlantillaId = null;
  maxManualConf = 0;
  disponiblesBaseManualConf = 0;
  disponiblesMetaManualConf = null;
  tarifaCostoManualConf = null;
  $("#selLimiteModoConf").value = "";
  $("#limiteNumConf").value = "";
  modoVacia();
  guardarSelTpl();
  actualizarProgPlantilla();
  $("#confProgreso").hidden = true;
  $("#listaRechazadosConf").innerHTML = "";
  $("#listaRechazadosConf").hidden = true;
  $("#btnCerrarConf").disabled = false;
  $("#btnLanzarConf").disabled = true;
  $("#btnLanzarConf").hidden = false;
  const sinP = $("#envioSinPlantilla");
  const conP = $("#envioContenido");
  if (sinP) sinP.hidden = false;
  if (conP) conP.hidden = true;
  actualizarPasosManual();
}

$("#btnEnviarActual").addEventListener("click", () => {
  if (!activaId) return toast("Guarda la plantilla antes de enviarla.", "error");
  if (hayCambios()) {
    return toast("Hay cambios sin guardar. Presiona Guardar primero (Ctrl+S).", "error");
  }
  abrirModalConf(activaId);
});

const selBaseConfEl = $("#selBaseConf");
if (selBaseConfEl) selBaseConfEl.addEventListener("change", () => {
  const v = selBaseConfEl.value;
  if (v.startsWith("esp:")) {
    localStorage.setItem("snw_modo_conf", "area");
    localStorage.setItem("snw_esp_mensajeria", v.slice(4));
  } else {
    ambienteConf = v;
    localStorage.setItem("snw_ambiente", ambienteConf);
    localStorage.setItem("snw_ambiente_admin", ambienteConf);
    localStorage.setItem("snw_modo_conf", "base");
    actualizarBadgeMensajeria();
  }
  refrescarAvisoDevConf();
  refrescarAvisoAdminConf();
  actualizarResumenConf();
});

function refrescarAvisoDevConf() {
  const box = $("#confAvisoDev");
  const entornoDesarrollo = entornoGlobal === "desarrollo";
  box.hidden = !entornoDesarrollo;
  if (!entornoDesarrollo) return;
  fetch("api/configuracion?ambiente=desarrollo", { headers: authHeaders() })
    .then((r) => (r.ok ? r.json() : {}))
    .then((cfg) => {
      const nums = (cfg.numeros_autorizados ?? []).join(", ") || "ninguno";
      box.textContent =
        `El sistema está en Desarrollo: los envíos masivos solo se permiten desde pacientes_dev y a números de prueba (${nums}).`;
    })
    .catch(() => {});
}

// Aviso rojo: en producción esta cuenta envía directo, sin confirmación de supervisor.
function refrescarAvisoAdminConf() {
  const box = $("#confAvisoAdmin");
  const puedeProd = window.snwPuede && window.snwPuede("envio_produccion");
  const modoEsp = modoAreaConf();
  const destinoProd = baseSeleccionadaConf() === "produccion" || modoEsp;
  const esAdminProduccion = puedeProd && destinoProd;
  const requiereConf = !puedeProd && destinoProd;
  box.hidden = !(esAdminProduccion || requiereConf);
  if (esAdminProduccion) {
    box.textContent =
      "Logeado como admin: se envía directamente sin confirmación de supervisor.";
  } else if (requiereConf) {
    box.textContent = modoEsp
      ? "Este envío pedirá confirmación por correo al supervisor."
      : "Este envío pedirá confirmación por correo al supervisor.";
  }
}

// El paso 3 permite elegir un porcentaje del máximo disponible o una cantidad
// exacta. En ambos casos se envía al servidor la cantidad final de pacientes.
function erroresExcesoCupo(cantidad, disponiblesBase, disponiblesMeta) {
  const errores = [];
  if (cantidad > disponiblesBase) errores.push("No puede excederse la cantidad de pacientes disponibles");
  if (disponiblesMeta != null && cantidad > disponiblesMeta) {
    errores.push("No debe excederse el límite diario de Meta");
  }
  return errores;
}

function limiteEnvioConf() {
  const modo = $("#selLimiteModoConf")?.value || "";
  const numEl = $("#limiteNumConf");
  if (!modo || !numEl || numEl.value.trim() === "") return null;
  const n = Number(numEl.value);
  return Number.isInteger(n) && n >= 1 && n <= maxManualConf ? n : null;
}

function actualizarPasosManual() {
  const plantilla = plantillas.find((p) => p.id === confPlantillaId);
  const base = $("#selBaseConf")?.value || "";
  const modo = $("#selLimiteModoConf")?.value || "";
  const listo1 = !!plantilla;
  const listo2 = listo1 && !!base;
  const puedeElegirLimite = listo2 && maxManualConf > 0;
  const listo3 = puedeElegirLimite && limiteEnvioConf() != null;
  for (const [id, listo] of [["pasoManual1", listo1], ["pasoManual2", listo2], ["filaLimiteConf", listo3]]) {
    const paso = document.getElementById(id);
    if (paso) paso.classList.toggle("prog-paso--listo", listo);
  }
  const selBase = $("#selBaseConf");
  if (selBase) selBase.disabled = !listo1 || envioEnCursoConf;
  $("#selLimiteModoConf").disabled = !puedeElegirLimite || envioEnCursoConf;
  $("#limiteRangeConf").disabled = !puedeElegirLimite || modo !== "porcentaje" || envioEnCursoConf;
  $("#limiteNumConf").disabled = !puedeElegirLimite || modo !== "cantidad" || envioEnCursoConf;
  $("#btnLanzarConf").disabled = !listo3 || envioEnCursoConf;
}

function actualizarCostoManual() {
  pintarCostoEstimado(
    "filaCostoConf", "confCosto", "confCostoDetalle", tarifaCostoManualConf,
    $("#selLimiteModoConf").value, limiteEnvioConf()
  );
}

function pintarCostoEstimado(filaId, montoId, detalleId, tarifa, modo, cantidad) {
  const fila = document.getElementById(filaId);
  const rate = Number(tarifa?.rate);
  if (!tarifa || !["porcentaje", "cantidad"].includes(modo) ||
      tarifa.rate == null || !Number.isFinite(rate) || rate < 0) {
    fila.hidden = true;
    return;
  }
  fila.hidden = false;
  if (cantidad == null) {
    document.getElementById(montoId).textContent = "—";
    document.getElementById(detalleId).textContent = "Indica una cantidad válida para estimar el costo.";
    return;
  }
  // Igual que el servidor: redondear hacia arriba el costo total.
  document.getElementById(montoId).textContent = fmtMoneda(Math.ceil(rate * cantidad), tarifa.moneda);
  document.getElementById(detalleId).textContent =
    `${new Intl.NumberFormat("es-CL").format(cantidad)} mensajes × ` +
    `${fmtMoneda(rate, tarifa.moneda)} por mensaje · categoría ${tarifa.categoria}`;
}

function actualizarCantidadManual() {
  const modo = $("#selLimiteModoConf").value;
  const num = $("#limiteNumConf");
  const range = $("#limiteRangeConf");
  $("#porcentajeConfWrap").hidden = modo !== "porcentaje";
  $("#labelLimiteConf").textContent = modo === "porcentaje" ? "Cantidad que se enviará" : "Cantidad de pacientes";
  num.readOnly = modo === "porcentaje";
  if (modo === "porcentaje") {
    const porcentaje = Number(range.value);
    num.value = maxManualConf > 0 ? String(Math.max(1, Math.floor(maxManualConf * porcentaje / 100))) : "";
    $("#limitePorcentajeConf").textContent = `${porcentaje} % (${num.value || 0} pacientes)`;
  } else if (modo !== "cantidad") {
    num.value = "";
  }
  const bruto = num.value.trim();
  const cantidad = Number(bruto);
  const errores = modo === "cantidad" && bruto !== ""
    ? (!Number.isInteger(cantidad) || cantidad < 1
      ? ["Ingresa una cantidad válida de pacientes"]
      : erroresExcesoCupo(cantidad, disponiblesBaseManualConf, disponiblesMetaManualConf))
    : [];
  const error = $("#limiteErrorConf");
  error.hidden = errores.length === 0;
  error.textContent = errores.join("\n");
  num.classList.toggle("invalido", errores.length > 0);
  num.setAttribute("aria-invalid", errores.length > 0 ? "true" : "false");
  actualizarCostoManual();
  actualizarPasosManual();
}

// El máximo es el menor entre los elegibles de la base (en Desarrollo incluye
// todos los estados) y el cupo restante de Meta cuando corresponda.
function configurarLimiteConf(pendientes, lim) {
  const nota = $("#limiteNotaConf");
  const disponibles = (lim && lim.disponibles != null) ? lim.disponibles : pendientes;
  disponiblesBaseManualConf = pendientes;
  disponiblesMetaManualConf = lim && lim.disponibles != null ? Number(lim.disponibles) : null;
  maxManualConf = Math.max(0, Math.min(pendientes, disponibles));
  $("#limiteMaxConf").textContent = new Intl.NumberFormat("es-CL").format(maxManualConf);
  $("#limiteNumConf").max = String(Math.max(1, maxManualConf));
  $("#selLimiteModoConf").value = "";
  $("#limiteNumConf").value = "";
  $("#limiteRangeConf").value = "50";
  $("#limitePorcentajeConf").textContent = "50 %";

  if (nota) {
    if (lim && lim.tier) {
      nota.hidden = false;
      const numero = new Intl.NumberFormat("es-CL");
      nota.innerHTML = `<strong>Límite diario de Meta:</strong> ${numero.format(lim.tier)} ` +
        `(${numero.format(Math.max(0, lim.disponibles))} disponibles)`;
      const porcentaje = Math.max(0, Number(lim.disponibles) || 0) / Number(lim.tier) * 100;
      nota.classList.remove("prog-limite-nota--ok", "prog-limite-nota--warn", "prog-limite-nota--danger");
      nota.classList.add(porcentaje > 50 ? "prog-limite-nota--ok" : porcentaje >= 20 ? "prog-limite-nota--warn" : "prog-limite-nota--danger");
    } else {
      nota.hidden = true;
    }
  }
  actualizarCantidadManual();
}

$("#selLimiteModoConf").addEventListener("change", actualizarCantidadManual);
$("#limiteRangeConf").addEventListener("input", actualizarCantidadManual);
$("#limiteNumConf").addEventListener("input", actualizarCantidadManual);

function fmtMoneda(monto, moneda) {
  const entero = Number.isInteger(monto);
  const s = monto.toLocaleString("de-DE", {
    minimumFractionDigits: entero ? 0 : 2,
    maximumFractionDigits: entero ? 0 : 2,
  });
  return `${s} ${moneda}`;
}

// Aviso del límite diario de WhatsApp cuando aplica al envío manual.
function mostrarAvisoLimiteConf(lim) {
  const box = $("#confAvisoLimite");
  if (!box) return;
  if (!lim || lim.disponibles > 0) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  box.textContent =
    `Límite diario de WhatsApp alcanzado: en las últimas 24 h ya se contactó a ` +
    `${lim.usados_24h} usuarios únicos (límite ${lim.tier}).`;
}

async function actualizarResumenConf() {
  const dd = $("#confDestinatarios");
  const filaCosto = $("#filaCostoConf");
  const consulta = ++consultaManualConf;
  dd.textContent = "Contando pacientes...";
  filaCosto.hidden = true;
  maxManualConf = 0;
  disponiblesBaseManualConf = 0;
  disponiblesMetaManualConf = null;
  tarifaCostoManualConf = null;
  $("#limiteNotaConf").hidden = true;
  $("#selLimiteModoConf").value = "";
  $("#limiteNumConf").value = "";
  actualizarCantidadManual();
  if (!baseSeleccionadaConf()) {
    dd.hidden = true;
    configurarLimiteConf(0);
    mostrarAvisoLimiteConf(null);
    return;
  }
  dd.hidden = false;

  try {
    const espId = areaEnvioId();
    const cuerpoEnvio = { plantilla_id: confPlantillaId, ambiente: ambienteEnvioConf() };
    if (espId != null) cuerpoEnvio.area_id = espId;
    const res = await fetch("api/notificaciones/destinatarios", {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(cuerpoEnvio),
    });
    if (res.status === 401) { window.snwSesionExpirada(); return; }
    if (!res.ok) throw new Error("No se pudieron contar los pacientes.");
    const data = await res.json();
    if (consulta !== consultaManualConf) return;
    const lim = data.limite_mensajeria || null;
    const esProd = baseSeleccionadaConf() === "produccion" || espId != null;
    const limAplicable = esProd ? lim : null;
    const pendientes = Number(data.pendientes) || 0;
    dd.textContent = `Disponibles en esta base: ${new Intl.NumberFormat("es-CL").format(pendientes)} pacientes.`;
    configurarLimiteConf(pendientes, limAplicable);
    mostrarAvisoLimiteConf(limAplicable);

    tarifaCostoManualConf = data.costo || null;
    actualizarCostoManual();
  } catch {
    if (consulta !== consultaManualConf) return;
    console.error("[app.js actualizarResumenConf()]");
    dd.textContent = "No se pudieron contar.";
    configurarLimiteConf(0);
    mostrarAvisoLimiteConf(null);
  }
}

$("#btnCancelarConf").addEventListener("click", () => {
  const enProgreso = envioEnCursoConf && jobIdActualConf;
  if (enProgreso) {
    clearInterval(timerPollingConf);
    const restantes = Math.max(0, totalActualConf - hechosActualConf);
    $("#mensajeCancelarConf").textContent =
      `¿Seguro que quieres cancelar el envío de ${restantes} mensaje(s) restante(s)?`;
    $("#btnNoCancelarConf").disabled = false;
    $("#btnConfirmarCancelarConf").disabled = false;
    $("#modalCancelarConf").hidden = false;
    pausarJobConf(jobIdActualConf);
    return;
  }
  // Sin envío en curso: se comporta como limpiar la card.
  cancelarEdicion();
});
$("#btnNoCancelarConf").addEventListener("click", () => {
  $("#modalCancelarConf").hidden = true;
  if (jobIdActualConf) {
    reanudarJobConf(jobIdActualConf);
    seguirProgresoConf(jobIdActualConf, totalActualConf);
  }
});
$("#btnConfirmarCancelarConf").addEventListener("click", async () => {
  if (!confirmarDescarteEditor()) return;
  const accion = await (async () => {
    try {
      const res = await fetch(`api/notificaciones/jobs/${jobIdActualConf}/cancelar`, {
        method: "POST",
        headers: authHeaders(),
      });
      if (res.status === 401) { window.snwSesionExpirada(); return false; }
      return res.ok;
    } catch {
      console.error("[app.js:1592]");
      return false;
    }
  })();
  $("#modalCancelarConf").hidden = true;
  if (accion) {
    finalizarConf("Envío cancelado por el usuario.", true);
    setBloqueoEnvioConf(false);
    jobIdActualConf = null;
    totalActualConf = 0;
    hechosActualConf = 0;
    resetearCardEnvio();
  } else {
    toast("No se pudo cancelar el envío.", "error");
    if (jobIdActualConf) seguirProgresoConf(jobIdActualConf, totalActualConf);
  }
});
$("#btnCerrarConf").addEventListener("click", () => {
  if (!confirmarDescarteEditor()) return;
  clearInterval(timerPollingConf);
  // Si había un envío en curso, el trabajo sigue en el servidor (se ve en Historial).
  if (envioEnCursoConf) setBloqueoEnvioConf(false);
  resetearCardEnvio();
});
$("#btnCerrarRechazoConf").addEventListener("click", () => ($("#modalRechazadoConf").hidden = true));
const modalRechazadoConfEl = $("#modalRechazadoConf");
modalRechazadoConfEl.addEventListener("click", (e) => {
  if (e.target === modalRechazadoConfEl) modalRechazadoConfEl.hidden = true;
});

$("#btnLanzarConf").addEventListener("click", () => {
  const nombre = $("#confNombre").textContent || "plantilla";
  const dest = limiteEnvioConf();
  if (dest == null) return;
  const baseEl = $("#selBaseConf");
  const base = baseEl && baseEl.selectedOptions.length
    ? baseEl.selectedOptions[0].textContent : "";
  $("#mensajeIniciarConf").textContent =
    `¿Iniciar el envío de "${nombre}" para hasta ${dest} paciente(s)${base ? ` de ${base}` : ""}?`;
  $("#modalIniciarConf").hidden = false;
});
$("#btnNoIniciarConf").addEventListener("click", () => {
  $("#modalIniciarConf").hidden = true;
});
$("#modalIniciarConf").addEventListener("click", (e) => {
  if (e.target.id === "modalIniciarConf") $("#modalIniciarConf").hidden = true;
});
$("#btnConfirmarIniciarConf").addEventListener("click", async () => {
  const limiteSeleccionado = limiteEnvioConf();
  if (limiteSeleccionado == null) {
    $("#modalIniciarConf").hidden = true;
    toast("Elige cuántos pacientes quieres incluir en el envío.", "error");
    return;
  }
  $("#modalIniciarConf").hidden = true;
  // Se puede lanzar otro envío aunque se siga uno en la card (el anterior
  // sigue en el servidor y se ve en Historial); la base ocupada la rechaza el backend.
  $("#btnLanzarConf").hidden = true;
  setBloqueoEnvioConf(true);
  const espera = document.getElementById("modalEspera");
  const mensajeEspera = document.getElementById("mensajeEsperaConf");
  const requiereSupervisorPrevisto = ambienteEnvioConf() === "produccion" &&
    !(window.snwPuede && window.snwPuede("envio_produccion"));
  if (requiereSupervisorPrevisto && espera) {
    if (mensajeEspera) mensajeEspera.textContent = "Enviando la solicitud al supervisor...";
    const linkEl = document.getElementById("linkConfirmacionEsperaMsg");
    if (linkEl) linkEl.textContent = "";
    espera.hidden = false;
  }

  try {
    if (!confPlantillaId) throw new Error("Elige una plantilla primero.");
    const cuerpo = { plantilla_id: confPlantillaId, ambiente: ambienteEnvioConf() };
    const espIdLanzar = areaEnvioId();
    if (espIdLanzar != null) cuerpo.area_id = espIdLanzar;
    cuerpo.limite = limiteSeleccionado;
    const res = await fetch("api/notificaciones/enviar", {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(cuerpo),
    });
    if (res.status === 401) {
      if (espera) espera.hidden = true;
      window.snwSesionExpirada();
      setBloqueoEnvioConf(false);
      return;
    }
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail ?? `Error ${res.status}`);

    pintarRechazadosConf(data.rechazados ?? []);

    if (data.aviso_limite_mensajeria) {
      toast(data.aviso_limite_mensajeria, "error");
    }

    if (data.requiere_confirmacion) {
      if (mensajeEspera) mensajeEspera.innerHTML = "Se ha enviado un correo a <strong>supervisor</strong> solicitando confirmación.";
      espera.hidden = false;
      const linkEl = document.getElementById("linkConfirmacionEsperaMsg");
      if (linkEl && data.confirm_url && window.snwEsPrivilegiado) {
        linkEl.innerHTML = `Para pruebas sin correo: <a href="${data.confirm_url}" target="_blank">Confirmar manualmente</a> · <a href="${data.confirm_url.replace('confirmar', 'rechazar')}" target="_blank" style="color:#b23b37;">Rechazar</a>`;
      }
      const beforeUnload = (e) => { e.preventDefault(); e.returnValue = ""; return ""; };
      window.addEventListener("beforeunload", beforeUnload);
      const poll = setInterval(async () => {
        try {
          const r2 = await fetch(`api/notificaciones/solicitud/${data.solicitud_id}`, { headers: authHeaders(), cache: "no-store" });
          if (!r2.ok) return;
          const s = await r2.json();
          if (s.estado === "confirmado" && s.job_id) {
            clearInterval(poll);
            window.removeEventListener("beforeunload", beforeUnload);
            espera.hidden = true;
            alert("Envío confirmado por supervisor");
            await new Promise((res) => setTimeout(res, 1500));
            cambiarTabMsg("envios");
            $("#confProgreso").hidden = false;
            jobIdActualConf = s.job_id;
            totalActualConf = s.total;
            seguirProgresoConf(s.job_id, s.total);
          } else if (s.estado === "rechazado") {
            clearInterval(poll);
            window.removeEventListener("beforeunload", beforeUnload);
            espera.hidden = true;
            const comentario = s.comentario?.trim()
              ? s.comentario
              : "(El supervisor no dejó comentario.)";
            $("#comentarioRechazoConf").textContent = comentario;
            $("#modalRechazadoConf").hidden = false;
            setBloqueoEnvioConf(false);
          }
        } catch {}
      }, 2000);
      window._pollEsperaMsg = poll;
      window._beforeUnloadEsperaMsg = beforeUnload;
      return;
    }

    if (espera) espera.hidden = true;
    if (!data.iniciado) {
      setBloqueoEnvioConf(false);
      finalizarConf(`Ningún destinatario válido en la base ${data.ambiente}.`, true);
      return;
    }

    $("#confProgreso").hidden = false;
    jobIdActualConf = data.job_id;
    totalActualConf = data.total;
    seguirProgresoConf(data.job_id, data.total);
  } catch (err) {
    console.error("[app.js:1708]", err);
    if (espera) espera.hidden = true;
    toast(`Error al iniciar el envío: ${err.message}`, "error");
    setBloqueoEnvioConf(false);
    $("#btnLanzarConf").hidden = false;
  }
});

function pintarRechazadosConf(rechazados) {
  const ul = $("#listaRechazadosConf");
  ul.innerHTML = "";
  const base = baseSeleccionadaConf();
  const esDev = base === "desarrollo" && !modoAreaConf();
  if (esDev) {
    ul.hidden = true;
    return;
  }
  // El filtro de números autorizados solo corresponde a la base desarrollo.
  // No arrastrar un rechazo de una selección anterior a producción/áreas.
  const visibles = rechazados.filter((r) =>
    !/número no autorizado en base desarrollo/i.test(String(r.motivo || "")));
  for (const r of visibles) {
    const li = document.createElement("li");
    li.textContent = `${r.nombre} (${r.telefono || "sin teléfono"}): ${r.motivo}`;
    ul.appendChild(li);
  }
  ul.hidden = visibles.length === 0;
}

async function pausarJobConf(jobId) {
  try {
    await fetch(`api/notificaciones/jobs/${jobId}/pausa`, { method: "POST", headers: authHeaders() });
  } catch {}
}
async function reanudarJobConf(jobId) {
  try {
    await fetch(`api/notificaciones/jobs/${jobId}/reanudar`, { method: "POST", headers: authHeaders() });
  } catch {}
}

function seguirProgresoConf(jobId, total) {
  clearInterval(timerPollingConf);
  timerPollingConf = setInterval(async () => {
    try {
      const res = await fetch(`api/notificaciones/jobs/${jobId}`, {
        headers: authHeaders(),
        cache: "no-store",
      });
      if (res.status === 401) { window.snwSesionExpirada(); return; }
      if (!res.ok) throw new Error();
      const job = await res.json();

      const hechos = job.enviados + job.fallidos;
      hechosActualConf = hechos;
      $("#barraFillConf").style.width = `${total ? Math.round((hechos / total) * 100) : 100}%`;
      $("#progresoNumerosConf").textContent = `${hechos} / ${total}`;
      $("#progresoTextoConf").textContent =
        job.estado === "completado"
          ? "Envío finalizado."
          : `Enviando mensajes... (${hechos}/${total})`;

      if (job.estado === "completado" || job.estado === "error" || job.estado === "cancelado") {
        clearInterval(timerPollingConf);
        const msg = job.estado === "error"
          ? `Error del canal: ${job.detalle}`
          : job.estado === "cancelado"
            ? "Envío cancelado por el usuario."
            : `Envío completado: ${job.enviados} enviado(s), ${job.fallidos} fallido(s).`;
        finalizarConf(msg, job.estado !== "completado");
        setBloqueoEnvioConf(false);
        jobIdActualConf = null;
        totalActualConf = 0;
        hechosActualConf = 0;
        cargar();
      }
    } catch {
      console.error("[app.js seguirProgresoConf()]");
      clearInterval(timerPollingConf);
      finalizarConf("Se perdió la conexión con el servidor.", true);
      setBloqueoEnvioConf(false);
    }
  }, 700);
}

function finalizarConf(mensaje, esError) {
  $("#progresoTextoConf").textContent = mensaje;
  // Terminó el envío: ya no hay nada que iniciar ni que cancelar.
  $("#btnCerrarConf").disabled = false;
  $("#btnLanzarConf").hidden = true;
  setBloqueoEnvioConf(false);
  toast(mensaje, esError ? "error" : "ok");
}

// Tras consultar/actualizar/sincronizar el estado en Meta, la plantilla que se
// está viendo puede haber pasado de pendiente a aprobada (o viceversa): hay
// que refrescar también los botones y el bloqueo de campos, no solo el badge.
function refrescarVistaPlantillaActiva(p) {
  renderEstadoMeta(p);
  actualizarBotonesSegunEstado(p);
  actualizarBloqueoCampos();
}

if (btnRevisarTodos) {
  btnRevisarTodos.addEventListener("click", async () => {
    setConsultandoEstado(true, btnRevisarTodos, "Actualizando...");
    try {
      const res = await fetch(`${API_URL}/estado-meta/actualizar`, {
        method: "POST",
        headers: authHeaders(),
      });
      if (res.status === 401) { window.snwSesionExpirada(); return; }
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail ?? `Error ${res.status}`);
      // Las plantillas de call center se gestionan desde el Historial.
      plantillas = data.filter((p) => !p.especial && esPlantillaValida(p));
      renderLista(buscadorEl.value);
      if (activaId) {
        const actual = plantillas.find((x) => x.id === activaId);
        if (actual) refrescarVistaPlantillaActiva(actual);
      }
      toast("Estados actualizados.", "ok");
    } catch (err) {
      console.error("[app.js refrescarVistaPlantillaActiva()]", err);
      toast(`No se pudieron actualizar los estados: ${err.message}`, "error");
    } finally {
      setConsultandoEstado(false, btnRevisarTodos);
      window.snwCooldownBoton(btnRevisarTodos);
    }
  });
}

// Meta es la fuente de verdad para los templates (esta cuenta no puede
// crearlos/editarlos por API): trae el estado real de los que ya se conocen
// e importa como plantilla nueva los que existan en Meta y falten aquí.
if (btnSincronizarMeta) {
  btnSincronizarMeta.addEventListener("click", async () => {
    setConsultandoEstado(true, btnSincronizarMeta, "Sincronizando...");
    try {
      const res = await fetch(`${API_URL}/sincronizar-meta`, {
        method: "POST",
        headers: authHeaders(),
      });
      if (res.status === 401) { window.snwSesionExpirada(); return; }
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail ?? `Error ${res.status}`);
      // Las plantillas de call center se gestionan desde el Historial.
      plantillas = data.plantillas.filter((p) => !p.especial && esPlantillaValida(p));
      renderLista(buscadorEl.value);
      if (activaId) {
        const actual = plantillas.find((x) => x.id === activaId);
        if (actual) refrescarVistaPlantillaActiva(actual);
      }
      toast(
        `Sincronizado con Meta: ${data.creadas} nueva(s), ${data.actualizadas} actualizada(s)` +
          ` de ${data.total_meta} template(s) en Meta.`,
        "ok"
      );
    } catch (err) {
      console.error("[app.js refrescarVistaPlantillaActiva()]", err);
      toast(`No se pudo sincronizar con Meta: ${err.message}`, "error");
    } finally {
      setConsultandoEstado(false, btnSincronizarMeta);
      window.snwCooldownBoton(btnSincronizarMeta);
    }
  });
}

/* ---------- Tabs de Mensajería ---------- */
let tabMsg = localStorage.getItem("snw_tab_msg") || "plantillas";
if (["plantillas", "envios", "programados"].indexOf(tabMsg) === -1) tabMsg = "plantillas";
let tplSelId = null; // plantilla seleccionada (persiste entre tabs y recargas)

function guardarSelTpl() {
  try {
    if (tplSelId) localStorage.setItem("snw_tpl_sel", String(tplSelId));
    else localStorage.removeItem("snw_tpl_sel");
  } catch { /* sin almacenamiento */ }
}

function pintarTabsMsg() {
  document.querySelectorAll("[data-msgtab]").forEach((b) =>
    b.classList.toggle("activo", b.dataset.msgtab === tabMsg));
  const enPlantillas = tabMsg === "plantillas";
  $("#accionesPlantillas").hidden = !enPlantillas;
  $("#avisoSincronizarMeta").hidden = !enPlantillas;
  // La lista queda fija siempre en el mismo lugar; solo se alterna el panel
  // derecho (editor / envío / columna de programados).
  const pE = $("#panelEditor");
  const pV = $("#panelEnvio");
  const pG = $("#progColumna");
  if (pE) pE.hidden = !enPlantillas;
  if (pV) pV.hidden = tabMsg !== "envios";
  if (pG) pG.hidden = tabMsg !== "programados";
}

function cambiarTabMsg(t) {
  tabMsg = t;
  try { localStorage.setItem("snw_tab_msg", tabMsg); } catch { /* sin almacenamiento */ }
  pintarTabsMsg();
  // Cada tab parte desde arriba: si no, el scroll heredado de la tab
  // anterior (más larga) deja las cards en posiciones distintas, y la
  // lista (sticky) queda pegada donde no corresponde.
  window.scrollTo(0, 0);
  renderLista(buscadorEl.value);
  if (tabMsg === "programados") {
    cargarProgramados();
    actualizarProgPlantilla();
  } else if (tabMsg === "envios") {
    // Si se eligió otra plantilla desde otra tab, actualiza la card manual
    // aunque ya estuviera abierta; si no cambió, conserva su estado actual.
    const vacia = document.getElementById("envioContenido");
    if (tplSelId && vacia && (vacia.hidden || confPlantillaId !== tplSelId)) {
      abrirModalConf(tplSelId);
    }
  } else if (tabMsg === "plantillas") {
    // El editor sigue a la selección: si se eligió en otra tab, se abre acá
    // (con el resguardo de cambios sin guardar; si se cancela, se vuelve a
    // resaltar lo que está abierto).
    if (tplSelId && tplSelId !== activaId) {
      if (!(plantillas || []).some((x) => x.id === tplSelId)) {
        tplSelId = null;
        renderLista(buscadorEl.value);
      } else if (!intentarAbrir(tplSelId)) {
        tplSelId = activaId;
        renderLista(buscadorEl.value);
      }
    }
  }
}

document.querySelectorAll("[data-msgtab]").forEach((b) =>
  b.addEventListener("click", () => cambiarTabMsg(b.dataset.msgtab))
);
let programados = [];
let progCreando = false; // candado anti doble-click/abuso
let progRechazarId = null;
// Cupo de hoy para el envío programado: pendientes de la base y lo que deja el
// límite diario de Meta. El límite del formulario nunca puede pasar ese techo.
let progCupo = null; // {pendientes, disponibles, tier, usados, costo}
let progCupoKey = ""; // base, fecha y plantilla ya consultadas
let progCupoConsulta = 0;
let progSinCupo = false;



function poblarFormProg(forzarCupo = false) {
  const selBase = $("#selProgBase");
  if (!selBase) return Promise.resolve();
  actualizarProgPlantilla();
  // Al refrescar la lista se fuerza la consulta: cancelar o crear un
  // programado cambia libres/reservados en tiempo real.
  const cupo = cargarCupoProg(forzarCupo);
  const inpF = $("#inpProgFecha");
  if (inpF && !inpF.value) {
    const min = new Date(Date.now() + 5 * 60000);
    min.setSeconds(0, 0);
    const tz = new Date(min.getTime() - min.getTimezoneOffset() * 60000);
    inpF.min = tz.toISOString().slice(0, 16);
  }
  return cupo;
}

// Tope real del límite programado: el menor entre los pacientes LIBRES de la
// base (los que ya tienen otro programado activo no cuentan) y los usuarios que
// aún permite contactar el límite diario de Meta. Sin límite de Meta
// configurado, el tope son los libres.
function techoProgLimite() {
  const c = progCupo;
  if (!c) return 0;
  const enBase = (c.libres != null) ? c.libres : (c.pendientes || 0);
  const disponibles = (c.disponibles != null) ? c.disponibles : enBase;
  return Math.max(0, Math.min(enBase, disponibles));
}

// Revisa el campo: lo pinta en rojo mientras no se pueda enviar (pasa el tope
// real, no es entero o es menor a 1) y devuelve si el valor es válido. Sin cupo
// (techo 0) no se compara nada: el campo queda deshabilitado y la nota explica.
function revisarLimiteProg(avisar = false) {
  const inpL = $("#inpProgLimite");
  if (!inpL) return true;
  const bruto = inpL.value.trim();
  const modo = $("#selProgLimiteModo")?.value || "";
  const techo = techoProgLimite();
  const errores = [];
  if (!modo) {
    errores.push("Elige un porcentaje o una cantidad exacta para continuar.");
  } else if (modo === "cantidad" && bruto === "") {
    errores.push("Indica cuántos pacientes quieres programar.");
  } else if (bruto !== "" && techo > 0) {
    const v = Number(bruto);
    if (!Number.isInteger(v) || v < 1) {
      errores.push("Ingresa una cantidad válida de pacientes.");
    } else {
      const c = progCupo || {};
      const disponiblesBase = c.libres != null ? c.libres : (c.pendientes || 0);
      errores.push(...erroresExcesoCupo(v, disponiblesBase, c.disponibles));
    }
  }
  // "invalido" es la clase del tema para pintar el borde en rojo.
  const mostrarError = errores.length > 0 && (avisar || (!!modo && bruto !== ""));
  const error = $("#progLimiteError");
  if (error) {
    error.hidden = !mostrarError;
    error.textContent = mostrarError ? errores.join("\n") : "";
  }
  inpL.classList.toggle("invalido", mostrarError);
  inpL.setAttribute("aria-invalid", mostrarError ? "true" : "false");
  if (errores.length && avisar) {
    toast(errores.join(" "), "error");
    inpL.focus();
  }
  return errores.length === 0;
}

function aplicarCupoProg() {
  const inpL = $("#inpProgLimite");
  const nota = $("#progLimiteNota");
  const disponiblesBase = $("#progDisponiblesBase");
  if (!inpL) return;
  const c = progCupo;
  if (disponiblesBase) {
    disponiblesBase.hidden = !c;
    if (c) {
      const libres = Number(c.libres ?? c.pendientes) || 0;
      disponiblesBase.textContent = `Disponibles en esta base: ${new Intl.NumberFormat("es-CL").format(libres)} pacientes.`;
    }
  }
  if (nota) {
    nota.classList.remove("prog-limite-nota--ok", "prog-limite-nota--warn", "prog-limite-nota--danger");
    if (c?.tier && c.disponibles != null) {
      const porcentaje = (Math.max(0, Number(c.disponibles) || 0) / Number(c.tier)) * 100;
      nota.classList.add(porcentaje > 50 ? "prog-limite-nota--ok" : porcentaje >= 20 ? "prog-limite-nota--warn" : "prog-limite-nota--danger");
    }
  }
  if (!c) {
    inpL.removeAttribute("max");
    progSinCupo = false;
    inpL.classList.remove("invalido");
    inpL.removeAttribute("aria-invalid");
    const error = $("#progLimiteError");
    if (error) { error.hidden = true; error.textContent = ""; }
    if (nota) nota.hidden = true;
    return;
  }
  const techo = techoProgLimite();
  progSinCupo = techo <= 0;
  if ($("#selProgLimiteModo")?.value === "porcentaje") {
    const porcentaje = Number($("#inpProgPorcentaje")?.value) || 1;
    inpL.value = techo > 0 ? String(Math.max(1, Math.floor(techo * porcentaje / 100))) : "";
    const salida = $("#progPorcentajeValor");
    if (salida) salida.textContent = `${porcentaje} % (${inpL.value || 0} pacientes)`;
  }
  if (techo > 0) inpL.max = String(techo);
  else inpL.removeAttribute("max");
  // No se recorta el valor: si quedó mayor al tope se marca en rojo para que
  // quien programa vea el conflicto y lo corrija (el backend lo rechaza igual).
  revisarLimiteProg();
  if (nota) {
    if (!c.tier || c.disponibles == null) {
      nota.hidden = true;
    } else {
      nota.hidden = false;
      const numero = new Intl.NumberFormat("es-CL");
      const limiteMeta = numero.format(c.tier);
      const quedanMeta = numero.format(Math.max(0, c.disponibles));
      nota.innerHTML = `<strong>Límite diario de Meta:</strong> ${limiteMeta} (${quedanMeta} disponibles)`;
    }
  }
}

// Se consulta al elegir área (o al abrir la pestaña con una ya elegida). Una
// sola vez por área: los refrescos de la lista no vuelven a preguntar.
async function cargarCupoProg(forzar = false) {
  const selBase = $("#selProgBase");
  if (!selBase) return;
  const base = selBase.value || "";
  const areaId = base.startsWith("esp:") ? Number(base.slice(4)) : "";
  const ambiente = areaId ? "produccion" : base;
  const fecha = $("#inpProgFecha")?.value.slice(0, 10) || "";
  const key = `${base}|${fecha}|${tplSelId || ""}`;
  if (!forzar && key === progCupoKey) { aplicarCupoProg(); return; }
  const consulta = ++progCupoConsulta;
  progCupoKey = key;
  progCupo = null;
  aplicarCupoProg();
  actualizarPasosProg();
  if (!base) return;
  try {
    const cuerpo = { ambiente, prevision_programado: true };
    if (areaId) cuerpo.area_id = areaId;
    if (fecha) cuerpo.fecha = fecha;
    if (tplSelId) cuerpo.plantilla_id = tplSelId;
    const res = await fetch("api/notificaciones/destinatarios", {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(cuerpo),
    });
    if (res.status === 401) { window.snwSesionExpirada(); return; }
    if (!res.ok) throw new Error();
    const d = await res.json();
    if (consulta !== progCupoConsulta) return;
    const lim = d.limite_mensajeria || null;
    progCupo = {
      pendientes: Number(d.pendientes) || 0,
      // Los que ya tienen otro programado activo no se pueden preelegir otra vez.
      reservados: Number(d.reservados) || 0,
      libres: Number(d.libres ?? d.pendientes) || 0,
      disponibles: lim ? Number(lim.disponibles) : null,
      tier: lim ? lim.tier : 0,
      usados: lim ? lim.usados_24h : 0,
      enviados: lim && lim.enviados_dia != null ? Number(lim.enviados_dia) : null,
      programados: lim && lim.reservados_dia != null ? Number(lim.reservados_dia) : null,
      fecha: lim && lim.fecha ? lim.fecha : "",
      costo: d.costo || null,
    };
  } catch {
    if (consulta !== progCupoConsulta) return;
    console.error("[app.js cargarCupoProg()]");
    progCupo = null;
  }
  aplicarCupoProg();
  actualizarPasosProg();
}

const selProgBaseEl = $("#selProgBase");
if (selProgBaseEl) selProgBaseEl.addEventListener("change", () => {
  cargarCupoProg();
  actualizarPasosProg();
});
const inpProgFechaEl = $("#inpProgFecha");
if (inpProgFechaEl) inpProgFechaEl.addEventListener("input", () => {
  cargarCupoProg(true);
  actualizarPasosProg();
});
const inpProgLimiteEl = $("#inpProgLimite");
if (inpProgLimiteEl) {
  // Mientras se escribe se va marcando en rojo; al salir del campo, el aviso.
  inpProgLimiteEl.addEventListener("input", () => {
    revisarLimiteProg();
    actualizarPasosProg();
  });
  inpProgLimiteEl.addEventListener("change", () => revisarLimiteProg(true));
  inpProgLimiteEl.addEventListener("blur", () => revisarLimiteProg(false));
}
const selProgLimiteModoEl = $("#selProgLimiteModo");
if (selProgLimiteModoEl) selProgLimiteModoEl.addEventListener("change", actualizarLimiteDesdeModo);
const inpProgPorcentajeEl = $("#inpProgPorcentaje");
if (inpProgPorcentajeEl) inpProgPorcentajeEl.addEventListener("input", actualizarLimiteDesdeModo);

async function cargarProgramados(mostrarFeedback = false) {
  const lista = $("#listaProgramados");
  // La lista y el texto de cupo deben reflejar la misma fotografía de reservas.
  await poblarFormProg(true);
  if (!lista) return;
  // Si ya hay datos, se conservan visibles mientras se actualiza (con
  // animación); solo se muestra "Cargando…" la primera vez.
  const primera = !programados.length && !lista.querySelector(".prog-item");
  if (primera) lista.innerHTML = `<p class="field__hint">Cargando…</p>`;
  else marcarProgsActualizando(true);
  try {
    const res = await fetch("api/notificaciones/programados", {
      headers: authHeaders(), cache: "no-store",
    });
    if (res.status === 401) { window.snwSesionExpirada(); return; }
    if (!res.ok) throw new Error();
    programados = await res.json();
    progsPrev = new Map(programados.map((p) => [p.id, p.estado]));
    const listaProg = $("#listaProgramados");
    if (listaProg) listaProg._pagina = 1;
    renderProgramados();
    if (mostrarFeedback) toast("Envíos programados actualizados correctamente.");
  } catch {
    console.error("[app.js cargarProgramados()]");
    marcarProgsActualizando(false);
    if (primera) lista.innerHTML = `<p class="field__hint">No se pudieron cargar.</p>`;
  }
}

/* ---------- Actualización automática de la lista de programados ----------
   Un envío por aprobar, aprobado o rechazado se refleja solo en pantalla:
   el navegador compara una foto liviana (id + estado) y, si algo cambió,
   recarga la lista y avisa. Así también se detectan las decisiones tomadas
   por otro supervisor o desde el enlace del correo, no solo las del propio
   usuario. La cadencia es rápida mientras hay algo por aprobar y se relaja
   cuando no hay nada pendiente. */
const INTERVALO_PROG_VIVO = 3000;
const INTERVALO_PROG_LATIDO = 10000;
let timerProgs = null;
let hayProgsPendientes = false;
let cargandoResumenProgs = false;
let progsPrev = null; // Map id -> estado de la última foto conocida
let progsDecididoPropio = new Map(); // id -> cuándo decidió este usuario

function programarProgs() {
  if (timerProgs) clearInterval(timerProgs);
  timerProgs = setInterval(() => {
    if (!document.hidden) revisarProgs();
  }, hayProgsPendientes ? INTERVALO_PROG_VIVO : INTERVALO_PROG_LATIDO);
}

// Qué cambió respecto de la foto anterior: altas nuevas y cambios de estado.
function diffProgs(items) {
  const nuevos = [], cambiados = [];
  for (const [id, estado] of items) {
    if (!progsPrev.has(id)) nuevos.push([id, estado]);
    else if (progsPrev.get(id) !== estado) cambiados.push([id, estado]);
  }
  return { nuevos, cambiados };
}

// Aviso de lo que cambió por fuera. Los estados técnicos del envío
// (enviando/enviado/error) actualizan la lista sin molestar con un aviso, y
// las decisiones que acaba de tomar este usuario ya tienen su propio aviso
// (no se duplica).
function avisarCambioProgs(nuevos, cambiados) {
  const porId = new Map(programados.map((p) => [p.id, p]));
  const nombre = (id) => (porId.get(id) || {}).plantilla || "Envío programado";
  const quien = (id) => (porId.get(id) || {}).decidido_por || "";
  const conQuien = (id) => (quien(id) ? ` por ${quien(id)}` : "");
  const propia = (id) => (Date.now() - (progsDecididoPropio.get(id) || 0)) < 15000;
  const msgs = [];
  const errores = [];
  const porAprobar = nuevos.filter(([, e]) => e === "pendiente");
  if (porAprobar.length) {
    msgs.push(`Hay ${porAprobar.length} envío${porAprobar.length === 1 ? "" : "s"} ` +
      `programado${porAprobar.length === 1 ? "" : "s"} por aprobar`);
  }
  for (const [id, estado] of cambiados) {
    if (propia(id)) continue;
    const fila = porId.get(id) || {};
    if (estado === "aprobado") msgs.push(`"${nombre(id)}" aprobado${conQuien(id)}`);
    else if (estado === "rechazado") msgs.push(`"${nombre(id)}" rechazado${conQuien(id)}`);
    else if (estado === "cancelado") msgs.push(`"${nombre(id)}" cancelado${conQuien(id)}`);
    else if (estado === "error") {
      // Caso habitual: se agotó el cupo de Meta ese día. Se avisa en pantalla
      // con el motivo (indica los disponibles de ese día).
      errores.push(`"${nombre(id)}" no se envió${fila.motivo ? `: ${fila.motivo}` : ""}`);
    }
  }
  if (msgs.length) toast(msgs.join(" · "), "ok");
  if (errores.length) toast(errores.join(" · "), "error");
}

async function revisarProgs() {
  if (cargandoResumenProgs) return; // no apilar sondeos si uno quedó colgado
  cargandoResumenProgs = true;
  let data;
  try {
    const res = await fetch("api/notificaciones/programados/resumen", {
      headers: authHeaders(), cache: "no-store",
    });
    if (res.status === 401) { window.snwSesionExpirada(); return; }
    if (!res.ok) throw new Error();
    data = await res.json();
  } catch {
    console.error("[app.js revisarProgs()]");
    return;
  } finally {
    cargandoResumenProgs = false;
  }
  const items = data.items || [];
  // La primera foto solo deja registrada la base de comparación: todavía no
  // hay nada que avisar.
  if (progsPrev !== null) {
    const { nuevos, cambiados } = diffProgs(items);
    if (nuevos.length || cambiados.length) {
      await cargarProgramados();
      avisarCambioProgs(nuevos, cambiados);
    } else {
      progsPrev = new Map(items);
    }
  } else {
    progsPrev = new Map(items);
  }
  // La cadencia sigue a la lista: rápida con algo por aprobar, lenta si no.
  const pendientes = Number(data.pendientes) || 0;
  if (hayProgsPendientes !== pendientes > 0) {
    hayProgsPendientes = pendientes > 0;
    programarProgs();
  }
  // El registro de decisiones propias solo sirve unos segundos: se poda.
  if (progsDecididoPropio.size) {
    const ahora = Date.now();
    progsDecididoPropio = new Map(
      [...progsDecididoPropio].filter(([, t]) => ahora - t < 60000));
  }
}

// Feedback mientras se actualiza: las filas visibles pulsan (igual que en
// Base de datos). Al redibujar la lista la clase desaparece sola.
function marcarProgsActualizando(on) {
  document.querySelectorAll("#listaProgramados .prog-item").forEach((el) =>
    el.classList.toggle("actualizando", on));
}

const ESTADO_PROG_LABEL = {
  pendiente: "Pendiente", aprobado: "Aprobado", rechazado: "Rechazado",
  cancelado: "Cancelado", enviando: "Enviando", enviado: "Enviado", error: "Error",
};

function progItemHtml(p) {
  const motivo = (p.estado === "rechazado" || p.estado === "error") && p.motivo
    ? `<div class="prog-item__motivo">${escaparHtml(p.motivo)}</div>` : "";
  const costo = p.costo && p.puede_ver_costo
    ? `<div class="costo-resumen prog-item__costo" title="Cuando se realice el envío se cobrará la tarifa vigente por ${escaparHtml(String(p.costo.elegibles))} mensajes.">` +
        `<span class="costo-resumen__icono" aria-hidden="true"><i class="fa-solid fa-coins"></i></span>` +
        `<div class="costo-resumen__contenido">` +
          `<span class="costo-resumen__etiqueta">Costo aproximado</span>` +
          `<strong class="costo-resumen__monto">${escaparHtml(fmtMoneda(p.costo.costo, p.costo.moneda))}</strong>` +
          `<span class="costo-resumen__detalle">Se cobra al realizarse el envío.</span>` +
        `</div></div>` : "";
  const decided = p.decidido_por && (p.estado === "aprobado" || p.estado === "rechazado" || p.estado === "cancelado")
    ? ` · decidido por ${escaparHtml(p.decidido_por)}` : "";
  const baseDatos = p.tabla || (p.ambiente === "desarrollo" ? "pacientes_dev" : "pacientes_prod");
  const etiquetaBase = p.area
    ? `${escaparHtml(p.area)} (${escaparHtml(baseDatos)})`
    : escaparHtml(baseDatos);
  // Junto al nombre va la fecha para la que quedó programado; en la línea de
  // detalle, cuándo se creó.
  const fechaProg = p.programado_para
    ? `<span class="prog-item__fecha"><i class="fa-regular fa-clock"></i>` +
      `para el día ${escaparHtml(p.programado_para)}</span>` : "";
  const botones =
    (p.puede_decidir
      ? `<button type="button" class="btn btn--sm btn--primary" data-prog-aprobar="${p.id}">Aprobar</button>` +
        `<button type="button" class="btn btn--sm btn--danger" data-prog-rechazar="${p.id}">Rechazar</button>` : "") +
    (p.puede_cancelar
      ? `<button type="button" class="btn btn--sm btn--ghost" data-prog-cancelar="${p.id}">Cancelar</button>` : "");
  // Solo se puede revisar la lista preelegida de la base de desarrollo.
  const listaDest = (p.puede_ver_preelegidos && p.ambiente === "desarrollo" &&
    p.area_id == null && p.preelegidos != null)
    ? `<div class="prog-item__dest">` +
      `<button type="button" class="prog-item__dest-btn" data-prog-dest="${p.id}">` +
      `Ver ${p.preelegidos} preelegido${p.preelegidos === 1 ? "" : "s"}</button>` +
      `<div class="prog-item__dest-lista" data-prog-dest-lista="${p.id}" hidden></div></div>` : "";
  return `<div class="prog-item" data-prog="${p.id}">` +
    `<div class="prog-item__cab"><span class="prog-estado prog-estado--${p.estado}">${ESTADO_PROG_LABEL[p.estado] || p.estado}</span>` +
    `<span>${escaparHtml(p.plantilla || "-")} · Base: ${etiquetaBase}</span>${fechaProg}</div>` +
    `<div class="prog-item__meta">` +
    (p.creado ? `Creado ${escaparHtml(p.creado)}` : "") +
    (p.limite ? ` · límite ${p.limite}` : "") +
    (p.creador_nombre ? ` · por ${escaparHtml(p.creador_nombre)}` : "") + decided + `</div>` +
    motivo + costo + listaDest +
    (botones ? `<div class="prog-item__acciones">${botones}</div>` : "") +
    `</div>`;
}

// Carga y muestra (u oculta) la lista de pacientes que quedaron preelegidos.
async function toggleDestinatariosProg(id, btn) {
  const caja = document.querySelector(`[data-prog-dest-lista="${id}"]`);
  if (!caja) return;
  if (!caja.hidden) { caja.hidden = true; return; }
  caja.hidden = false;
  caja.innerHTML = `<p class="field__hint">Cargando…</p>`;
  try {
    const res = await fetch(`api/notificaciones/programados/${id}/destinatarios`, {
      headers: authHeaders(), cache: "no-store",
    });
    if (res.status === 401) { window.snwSesionExpirada(); return; }
    if (!res.ok) throw new Error();
    const d = await res.json();
    const items = (d.destinatarios || []).map((x) =>
      `<li>${escaparHtml(x.nombre || "Sin nombre")} · ${escaparHtml(x.telefono || "sin teléfono")}</li>`).join("");
    caja.innerHTML = d.total
      ? `<ul class="prog-item__dest-ul">${items}</ul>`
      : `<p class="field__hint">Este envío no tiene una lista preseleccionada porque se creó antes de que existiera.</p>`;
  } catch {
    console.error("[app.js toggleDestinatariosProg()]");
    caja.innerHTML = `<p class="field__hint">No se pudo cargar la lista.</p>`;
  }
}

// Paginador de 3 por página (la lista no crece más que eso) + scroll.
function renderProgramados() {
  const lista = $("#listaProgramados");
  if (!lista) return;
  if (!programados.length) {
    lista.innerHTML =
      `<div class="prog-vacio"><i class="fa-solid fa-calendar-check"></i>` +
      `<p>No hay envíos programados.</p></div>`;
    return;
  }
  const total = programados.length;
  const pageSize = 3;
  const paginas = Math.max(1, Math.ceil(total / pageSize));
  let pagina = lista._pagina || 1;
  if (pagina > paginas) pagina = paginas;
  if (pagina < 1) pagina = 1;
  lista._pagina = pagina;
  const parte = programados.slice((pagina - 1) * pageSize, pagina * pageSize);
  lista.innerHTML =
    `<div class="pag-grupo">` +
      `<span class="pag-info">Página ${pagina} de ${paginas} · ${total} envío${total === 1 ? "" : "s"}</span>` +
      `<span class="pag-fila">` +
        `<button type="button" class="btn btn--ghost" data-pag="ant" title="Página anterior" aria-label="Página anterior"${pagina <= 1 ? " disabled" : ""}><i class="fa-solid fa-chevron-left"></i></button>` +
        `<button type="button" class="btn btn--ghost" data-pag="sig" title="Página siguiente" aria-label="Página siguiente"${pagina >= paginas ? " disabled" : ""}><i class="fa-solid fa-chevron-right"></i></button>` +
      `</span>` +
    `</div>` +
    `<div class="prog-lista-scroll">${parte.map(progItemHtml).join("")}</div>`;
}

function bloquearFilaProg(id, bloquear) {
  const item = document.querySelector(`.prog-item[data-prog="${id}"]`);
  if (!item) return;
  item.querySelectorAll("button").forEach((b) => { b.disabled = bloquear; });
}

async function programarEnvio() {
  if (progCreando) return;
  const selBase = $("#selProgBase");
  const inpF = $("#inpProgFecha");
  const inpL = $("#inpProgLimite");
  const base = selBase && selBase.value ? selBase.value : "";
  const areaId = base.startsWith("esp:") ? Number(base.slice(4)) : null;
  const ambiente = areaId != null ? "produccion" : base;
  const tpl = (plantillas || []).find((x) => x.id === tplSelId);
  if (!base) { toast("Elige la base de datos.", "error"); return; }
  if (!tpl) { toast("Elige una plantilla de la lista de la izquierda.", "error"); return; }
  if (!inpF || !inpF.value) { toast("Elige fecha y hora.", "error"); return; }
  if (progSinCupo) { toast("Hoy no hay cupo disponible para programar este envío.", "error"); return; }
  // Campo en rojo + aviso si el límite pasa los pendientes de la base o el cupo
  // de Meta (o no es un entero válido).
  if (!revisarLimiteProg(true) || !limiteProgElegido()) return;
  const limite = Number(inpL.value);
  const btn = $("#btnProgramar");
  if (btn && btn.disabled && !progCreando) return;
  progCreando = true;
  actualizarPasosProg();
  try {
    const res = await fetch("api/notificaciones/programados", {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ ambiente, ...(areaId != null ? { area_id: areaId } : {}), plantilla_id: tpl.id, programado_para: inpF.value, limite }),
    });
    const data = await res.json().catch(() => ({}));
    if (res.status === 401) { window.snwSesionExpirada(); return; }
    if (!res.ok) throw new Error(data.detail || "No se pudo programar.");
    // El sistema ya dejó reservado el grupo: se informa para que quede claro
    // que el envío va a esos pacientes y no a quien aparezca pendiente después.
    const cuantos = Number(data.preelegidos) || 0;
    toast(data.estado === "pendiente"
      ? `Envío programado: ${cuantos} paciente(s) preelegidos, pendiente de aprobación de un superior.`
      : `Envío programado: ${cuantos} paciente(s) preelegidos.`, "ok");
    inpF.value = "";
    if (inpL) inpL.value = "";
    const selModo = $("#selProgLimiteModo");
    if (selModo) selModo.value = "";
    actualizarLimiteDesdeModo();
    await cargarProgramados();
  } catch (err) {
    console.error("[app.js programarEnvio()]", err);
    toast(err.message || "No se pudo programar.", "error");
  } finally {
    progCreando = false;
    actualizarPasosProg();
  }
}

const btnProgramarEl = $("#btnProgramar");
if (btnProgramarEl) btnProgramarEl.addEventListener("click", programarEnvio);
const btnActualizarProgEl = $("#btnActualizarProg");
if (btnActualizarProgEl) window.snwConCooldown(btnActualizarProgEl, () => cargarProgramados(true));

async function decidirProg(id, accion, motivo) {
  bloquearFilaProg(id, true);
  try {
    const res = await fetch(`api/notificaciones/programados/${id}/${accion}`, {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(accion === "rechazar" ? { motivo: motivo || "" } : {}),
    });
    const data = await res.json().catch(() => ({}));
    if (res.status === 401) { window.snwSesionExpirada(); return; }
    if (!res.ok) throw new Error(data.detail || "No se pudo registrar la decisión.");
    progsDecididoPropio.set(id, Date.now());
    toast(accion === "aprobar" ? "Envío aprobado." : accion === "rechazar" ? "Envío rechazado." : "Envío cancelado.", "ok");
    await cargarProgramados();
  } catch (err) {
    console.error("[app.js decidirProg()]", err);
    toast(err.message || "No se pudo completar.", "error");
    bloquearFilaProg(id, false);
  }
}

const listaProgEl = $("#listaProgramados");
if (listaProgEl) listaProgEl.addEventListener("click", (e) => {
  const bPag = e.target.closest("[data-pag]");
  if (bPag) {
    const lista = $("#listaProgramados");
    lista._pagina = (lista._pagina || 1) + (bPag.dataset.pag === "sig" ? 1 : -1);
    renderProgramados();
    return;
  }
  const bAp = e.target.closest("[data-prog-aprobar]");
  const bRe = e.target.closest("[data-prog-rechazar]");
  const bCa = e.target.closest("[data-prog-cancelar]");
  const bDe = e.target.closest("[data-prog-dest]");
  if (bDe) toggleDestinatariosProg(Number(bDe.dataset.progDest), bDe);
  else if (bAp) decidirProg(Number(bAp.dataset.progAprobar), "aprobar");
  else if (bCa) decidirProg(Number(bCa.dataset.progCancelar), "cancelar");
  else if (bRe) {
    progRechazarId = Number(bRe.dataset.progRechazar);
    const p = programados.find((x) => x.id === progRechazarId);
    $("#progRechazarTexto").textContent =
      `Vas a rechazar "${p ? p.plantilla : ""}" programado para ${p ? p.programado_para : ""}.`;
    $("#progRechazoMotivo").value = "";
    $("#modalProgRechazar").hidden = false;
    $("#progRechazoMotivo").focus();
  }
});

$("#btnCancelarProgRechazar").addEventListener("click", () => {
  progRechazarId = null;
  $("#modalProgRechazar").hidden = true;
});
$("#modalProgRechazar").addEventListener("click", (e) => {
  if (e.target.id === "modalProgRechazar") {
    progRechazarId = null;
    $("#modalProgRechazar").hidden = true;
  }
});
$("#btnConfirmarProgRechazar").addEventListener("click", () => {
  const id = progRechazarId;
  const motivo = $("#progRechazoMotivo").value.trim();
  progRechazarId = null;
  $("#modalProgRechazar").hidden = true;
  if (id) decidirProg(id, "rechazar", motivo);
});

// Al volver a la pestaña se revisa de inmediato: si en otra pantalla se
// aprobó, rechazó o creó un envío, la lista ya está al día al mirarla.
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) revisarProgs();
});
programarProgs();

pintarTabsMsg();

aplicarModoSoloLecturaPlantillas();
modoVacia();
cargarMiUsuario();
// Las áreas primero: la restauración de la plantilla seleccionada necesita
// misAreas (card de envío / formulario de programar).
cargarAreas().then(cargar);
cargarProgramados();
setInterval(revisarPlantillasEnSegundoPlano, 30000);
