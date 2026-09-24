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

// Especialidades visibles para la cuenta (Fase 3): todas si es
// privilegiada, solo las asignadas si no. Las plantillas pueden asociarse a
// una (envío solo a su tabla) o quedar globales.
let misEspecialidades = [];

function nombreEspecialidad(id) {
  const e = misEspecialidades.find((x) => x.id === id);
  return e ? e.nombre_visible : null;
}

const $ = (sel) => document.querySelector(sel);

const listaEl = $("#listaPlantillas");
const buscadorEl = $("#buscador");
const formEl = $("#formPlantilla");
const inpNombre = $("#inpNombre");
const inpMensaje = $("#inpMensaje");
const inpTemplate = $("#inpTemplate");
const inpTemplateLang = $("#inpTemplateLang");
const inpTemplateCategoria = $("#inpTemplateCategoria");
const inpEspecialidad = $("#inpEspecialidad");
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
const esPlantillaEditable = (p) => !p || esPlantillaAprobada(p) || esPlantillaRechazada(p);
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

async function cargarEspecialidades() {
  try {
    const res = await fetch("api/especialidades/mias", { headers: authHeaders(), cache: "no-store" });
    if (!res.ok) throw new Error();
    misEspecialidades = await res.json();
  } catch {
    misEspecialidades = [];
  }
  poblarSelectEspecialidad();
}

function poblarSelectEspecialidad() {
  if (!inpEspecialidad) return;
  const actual = inpEspecialidad.value;
  inpEspecialidad.innerHTML =
    `<option value="">Global (todas las bases)</option>` +
    misEspecialidades.map((e) => `<option value="${e.id}">${escaparHtml(e.nombre_visible)}</option>`).join("");
  if (actual && misEspecialidades.some((e) => String(e.id) === actual)) {
    inpEspecialidad.value = actual;
  }
}

// Selector único de base de datos del modal de envío (Fase 3): lista solo
// las bases disponibles —desarrollo/producción (según restricción) y una
// opción por especialidad—. Si hay una sola disponible, queda seleccionada.
// Valores: "desarrollo" | "produccion" | "esp:<id>".
function baseSeleccionadaConf() {
  const sel = $("#selBaseConf");
  return sel && sel.value ? sel.value : ambienteConf;
}

function modoEspecialidadConf() {
  return baseSeleccionadaConf().startsWith("esp:");
}

function especialidadEnvioId() {
  const v = baseSeleccionadaConf();
  if (!v.startsWith("esp:")) return null;
  const id = Number(v.slice(4));
  return Number.isFinite(id) ? id : null;
}

function ambienteEnvioConf() {
  // Las especialidades son tablas únicas (sin entorno): se envían como
  // producción (con confirmación del supervisor salvo permiso directo).
  return modoEspecialidadConf() ? "produccion" : baseSeleccionadaConf();
}

// Reconstruye las opciones del select. Con `forzarEspId` (plantilla de una
// especialidad) deja solo esa opción, ya seleccionada.
function construirOpcionesBaseConf(forzarEspId) {
  const sel = $("#selBaseConf");
  if (!sel) return;
  const restringido = usuarioRestringidoADesarrollo();
  let html = "";
  if (forzarEspId != null) {
    const nombre = nombreEspecialidad(forzarEspId) || "Especialidad";
    html = `<option value="esp:${forzarEspId}">${escaparHtml(nombre)}</option>`;
  } else {
    html = `<optgroup label="Bases">` +
      `<option value="desarrollo">Base de datos desarrollo</option>` +
      (restringido ? "" : `<option value="produccion">Base de datos producción</option>`) +
      `</optgroup>`;
    if (misEspecialidades.length) {
      html += `<optgroup label="Especialidades">` +
        misEspecialidades.map((e) => `<option value="esp:${e.id}">${escaparHtml(e.nombre_visible)}</option>`).join("") +
        `</optgroup>`;
    }
  }
  sel.innerHTML = html;

  // Restaura la última elección si sigue disponible; si no, la primera
  // opción (cuando hay una sola disponible, queda esa seleccionada).
  const valores = [...sel.options].map((o) => o.value);
  let elegido = null;
  if (forzarEspId != null) {
    elegido = `esp:${forzarEspId}`;
  } else {
    const guardadoEsp = localStorage.getItem("snw_esp_mensajeria");
    if (localStorage.getItem("snw_modo_conf") === "especialidad" && guardadoEsp &&
        valores.includes(`esp:${guardadoEsp}`)) {
      elegido = `esp:${guardadoEsp}`;
    } else if (valores.includes(ambienteConf)) {
      elegido = ambienteConf;
    } else if (valores.length) {
      elegido = valores[0];
    }
  }
  if (elegido != null) {
    sel.value = elegido;
    if (elegido.startsWith("esp:")) {
      localStorage.setItem("snw_modo_conf", "especialidad");
      localStorage.setItem("snw_esp_mensajeria", elegido.slice(4));
    } else {
      ambienteConf = elegido;
      localStorage.setItem("snw_ambiente", ambienteConf);
      localStorage.setItem("snw_ambiente_admin", ambienteConf);
      localStorage.setItem("snw_modo_conf", "base");
      actualizarBadgeMensajeria();
    }
  }
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
      if (activaId === null) seleccionarDefault();
      return;
    } catch (err) {
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

async function crearPlantilla(nombre, texto, whatsapp_template_lang, whatsapp_template_categoria, especialidad_id) {
  const res = await fetch(API_URL, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ clave: slug(nombre), nombre, texto, whatsapp_template_lang, whatsapp_template_categoria, especialidad_id }),
  });
  if (res.status === 401) { window.snwSesionExpirada(); return Promise.reject(new Error("Sesión expirada")); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail ?? data.error ?? `Error ${res.status}`);
  return data;
}

async function actualizarPlantilla(id, nombre, texto, whatsapp_template_lang, whatsapp_template_categoria, especialidad_id) {
  const res = await fetch(`${API_URL}/${id}`, {
    method: "PUT",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ nombre, texto, whatsapp_template_lang, whatsapp_template_categoria, especialidad_id }),
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
    (p.id === activaId ? " tpl-item--activa" : "") +
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
  if (p.especialidad_id != null) {
    const nombreEsp = nombreEspecialidad(p.especialidad_id) || "Especialidad";
    estadoTag += ` <span class="tpl-item__estado">${escaparHtml(nombreEsp)}</span>`;
  }
  btn.innerHTML =
    `<span class="tpl-item__nombre">${escaparHtml(p.nombre ?? "(sin nombre)")}${estadoTag}</span>` +
    `<span class="tpl-item__vista">${escaparHtml(primeraLinea)}</span>`;
  btn.addEventListener("click", () => intentarAbrir(p.id));
  return btn;
}

function renderLista(filtro = "") {
  const q = filtro.trim().toLowerCase();
  const visibles = [...plantillas]
    .sort((a, b) => (b.actualizada || 0) - (a.actualizada || 0))
    .filter(
      (p) =>
        !q ||
        String(p.nombre || "").toLowerCase().includes(q) ||
        String(p.texto || "").toLowerCase().includes(q)
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
  // por eso van en su propio grupo; las realmente pendientes de revisión
  // (recién creadas o PENDING) quedan de solo lectura (ver abrir()).
  const aprobadas = visibles.filter((p) => esPlantillaAprobada(p));
  const rechazadas = visibles.filter((p) => esPlantillaRechazada(p));
  const pendientes = visibles.filter((p) => !esPlantillaAprobada(p) && !esPlantillaRechazada(p));

  const agregarGrupo = (titulo, lista, tono, icono) => {
    if (!lista.length) return;
    const encabezado = document.createElement("li");
    encabezado.className = `tpl-list__grupo tpl-list__grupo--${tono}`;
    encabezado.innerHTML = `<i class="fa-solid ${icono}"></i> ${escaparHtml(titulo)} (${lista.length})`;
    listaEl.appendChild(encabezado);
    for (const p of lista) listaEl.appendChild(crearItemPlantilla(p));
  };

  agregarGrupo("Plantillas aprobadas por Meta", aprobadas, "ok", "fa-circle-check");
  agregarGrupo("Plantillas rechazadas por Meta", rechazadas, "danger", "fa-circle-xmark");
  agregarGrupo("Plantillas pendientes de aprobación por Meta", pendientes, "warn", "fa-clock");
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

function estadoActualEditor() {
  return JSON.stringify([inpNombre.value, inpMensaje.value, valTemplate(), valTemplateLang(), valTemplateCategoria(), inpEspecialidad ? inpEspecialidad.value : ""]);
}

function marcarSnapshot() {
  snapshot = estadoActualEditor();
  actualizarEstadoBotonGuardar();
}

function hayCambios() {
  return snapshot !== null && snapshot !== estadoActualEditor();
}

// El botón «Guardar» solo se habilita si hubo algún cambio desde que se abrió
// la plantilla (o desde el último guardado); evita guardados vacíos/no-op.
function actualizarEstadoBotonGuardar() {
  if (btnGuardar) btnGuardar.disabled = !hayCambios();
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
  // Meta limita la edición a una vez cada 24h, pero no el borrado: Eliminar
  // sigue disponible aunque Guardar esté bloqueado por el enfriamiento.
  const puedeGuardar = PUEDE_EDITAR_PLANTILLAS && editable && !enEnfriamiento;
  const puedeEliminar = PUEDE_EDITAR_PLANTILLAS && editable;
  btnGuardar.hidden = !puedeGuardar;
  // Cancelar no guarda nada, solo deselecciona: debe poder usarse para salir
  // de una plantilla aunque Guardar esté bloqueado (en enfriamiento, pendiente
  // de revisión, etc.) y también al estar creando una plantilla nueva
  // (activaId es null igual que en el estado vacío, así que lo que
  // distingue "creando" de "nada seleccionado" es si el formulario está
  // visible, no si hay un `p`).
  if (btnCancelar) btnCancelar.hidden = formEl.style.display === "none";
  // Eliminar solo aplica si ya existe (tiene id) y se puede gestionar.
  btnEliminar.hidden = !(p && puedeEliminar);
  if (btnEnviarActual) btnEnviarActual.hidden = !(p && aprobada);
  if (avisoPendiente) {
    if (p && !editable) {
      pararCuentaRegresiva();
      avisoPendiente.textContent =
        `Esta plantilla está ${etiquetaEstadoMeta(p.whatsapp_template_status).toLowerCase()} en Meta: ` +
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

  if (!PUEDE_EDITAR_PLANTILLAS || (p && (!esPlantillaEditable(p) || editadaRecientemente(p)))) {
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
  estadoVacio.style.display = "none";
  formEl.style.display = "";
  tituloForm.textContent = (PUEDE_EDITAR_PLANTILLAS && esPlantillaEditable(p)) ? `Editando: ${p.nombre}` : p.nombre;
  inpNombre.value = p.nombre;
  inpMensaje.value = p.texto;
  if (inpEspecialidad) {
    const espVal = p.especialidad_id != null ? String(p.especialidad_id) : "";
    if (espVal && ![...inpEspecialidad.options].some((o) => o.value === espVal)) {
      const nombreEsp = nombreEspecialidad(p.especialidad_id) || "Especialidad";
      inpEspecialidad.add(new Option(nombreEsp, espVal));
    }
    inpEspecialidad.value = espVal;
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
  if (inpEspecialidad) inpEspecialidad.value = "";
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
  refrescarEditor();
  marcarSnapshot();
  renderLista(buscadorEl.value);
  inpNombre.focus();
}

function modoVacia() {
  activaId = null;
  snapshot = null;
  formEl.style.display = "none";
  estadoVacio.style.display = "flex";
  tituloForm.textContent = "Plantillas";
  actualizarBotonesSegunEstado(null);
  renderLista(buscadorEl.value);
}

function intentarAbrir(id) {
  if (id === activaId) return;
  if (hayCambios() && !confirm("Tienes cambios sin guardar. ¿Deseas descartarlos?")) {
    return;
  }
  abrir(id);
}

function intentarNueva() {
  if (hayCambios() && !confirm("Tienes cambios sin guardar. ¿Deseas descartarlos?")) {
    return;
  }
  modoNueva();
}

function cancelarEdicion() {
  if (hayCambios() && !confirm("¿Descartar los cambios?")) return;
  // Cancelar siempre deselecciona la plantilla (no la vuelve a abrir).
  modoVacia();
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
    const especialidad_id = inpEspecialidad && inpEspecialidad.value ? Number(inpEspecialidad.value) : null;
    let fila;
    if (activaId) {
      fila = await actualizarPlantilla(activaId, nombre, texto, whatsapp_template_lang, whatsapp_template_categoria, especialidad_id);
      const i = plantillas.findIndex((x) => x.id === activaId);
      if (i >= 0) plantillas[i] = fila;
    } else {
      fila = await crearPlantilla(nombre, texto, whatsapp_template_lang, whatsapp_template_categoria, especialidad_id);
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
    modalEl.hidden = true;
    toast(`Error al eliminar: ${err.message}`, "error");
  } finally {
    setConsultandoEstado(false, btnConfirmar);
  }
});

$("#btnCancelar").addEventListener("click", cancelarEdicion);

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
  toastTimer = setTimeout(() => toastEl.classList.remove("visible"), 3200);
}

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

const badgeEntornoMensajeria = document.getElementById("badgeEntorno");
const modalConf = $("#modalConfirmar");
let ambienteConf = localStorage.getItem("snw_ambiente_admin") || localStorage.getItem("snw_ambiente") || "desarrollo";
let entornoGlobal = null;
let timerPollingConf = null;
let envioEnCursoConf = false;
let jobIdActualConf = null;
let totalActualConf = 0;
let hechosActualConf = 0;

function setBloqueoEnvioConf(bloquear) {
  envioEnCursoConf = bloquear;
  bloquearInterfaz(bloquear);
  // Cancelar y Cerrar del modal de envío siempre quedan disponibles.
  ["btnCancelarConf", "btnCerrarConf"].forEach((id) => {
    const b = document.getElementById(id);
    if (b) b.disabled = false;
  });
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

// Devuelve true si la cuenta debe quedar restringida a la base de desarrollo
// (cuando el entorno global del sistema es desarrollo y no tiene permiso de
// envío directo en producción).
function usuarioRestringidoADesarrollo() {
  const puedeProd = window.snwPuede && window.snwPuede("envio_produccion");
  return !puedeProd && entornoGlobal === "desarrollo";
}

// Aplica la restricción de base de datos en el modal de envío.
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

function abrirModalConf() {
  const p = plantillas.find((x) => x.id === activaId);
  $("#confNombre").textContent = p?.nombre ?? "";

  aplicarRestriccionAmbiente();
  // Si la plantilla es de una especialidad, el select trae solo esa base;
  // si no, trae las disponibles y restaura la última usada.
  construirOpcionesBaseConf(p?.especialidad_id ?? null);
  refrescarAvisoDevConf();
  refrescarAvisoAdminConf();
  cargarLimiteAdminConf();
  actualizarResumenConf();

  $("#confProgreso").hidden = true;
  $("#listaRechazadosConf").innerHTML = "";
  $("#listaRechazadosConf").hidden = true;
  $("#btnCerrarConf").disabled = false;
  $("#btnLanzarConf").disabled = true;
  $("#btnLanzarConf").hidden = false;

  modalConf.hidden = false;
}

$("#btnEnviarActual").addEventListener("click", () => {
  if (!activaId) return toast("Guarda la plantilla antes de enviarla.", "error");
  if (hayCambios()) {
    return toast("Hay cambios sin guardar. Presiona Guardar primero (Ctrl+S).", "error");
  }
  abrirModalConf();
});

const selBaseConfEl = $("#selBaseConf");
if (selBaseConfEl) selBaseConfEl.addEventListener("change", () => {
  const v = selBaseConfEl.value;
  if (v.startsWith("esp:")) {
    localStorage.setItem("snw_modo_conf", "especialidad");
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
  const base = baseSeleccionadaConf();
  const esDev = base === "desarrollo" && !modoEspecialidadConf();
  box.hidden = !esDev;
  if (!esDev) return;
  fetch(`api/configuracion?ambiente=${base}`, { headers: authHeaders() })
    .then((r) => (r.ok ? r.json() : {}))
    .then((cfg) => {
      const nums = (cfg.numeros_autorizados ?? []).join(", ") || "ninguno";
      box.textContent =
        `Base de datos desarrollo: solo se enviará a los números autorizados (${nums}). El resto será descartado.`;
    })
    .catch(() => {});
}

// Aviso rojo: en producción esta cuenta envía directo, sin confirmación de supervisor.
function refrescarAvisoAdminConf() {
  const box = $("#confAvisoAdmin");
  const puedeProd = window.snwPuede && window.snwPuede("envio_produccion");
  const modoEsp = modoEspecialidadConf();
  const destinoProd = baseSeleccionadaConf() === "produccion" || modoEsp;
  const esAdminProduccion = puedeProd && destinoProd;
  const requiereConf = !puedeProd && destinoProd;
  box.hidden = !(esAdminProduccion || requiereConf);
  if (esAdminProduccion) {
    box.textContent =
      "Logeado como admin: se envía directamente sin confirmación de supervisor.";
  } else if (requiereConf) {
    box.textContent = modoEsp
      ? "Este envío pedirá confirmación por correo al supervisor (llega también a los supervisores de la especialidad)."
      : "Este envío pedirá confirmación por correo al supervisor.";
  }
}

// Selector de cuántos mensajes enviar (solo producción). Mantiene el slider
// y el número sincronizados y devuelve el valor elegido.
function limiteEnvioConf() {
  const fila = $("#filaLimiteConf");
  if (!fila || fila.hidden) return null;
  const numEl = $("#limiteNumConf");
  if (numEl.disabled) return null;
  const n = parseInt(numEl.value, 10);
  return Number.isFinite(n) ? n : null;
}

// Configura el slider de cuántos enviar. `max` es el menor entre los pendientes
// y los usuarios que aún permite contactar el límite diario de WhatsApp (si
// aplica). Muestra cuántos quedan disponibles hoy y nunca deja superar el cupo.
function configurarLimiteConf(pendientes, lim) {
  const fila = $("#filaLimiteConf");
  const range = $("#limiteRangeConf");
  const num = $("#limiteNumConf");
  const nota = $("#limiteNotaConf");
  const esProd = baseSeleccionadaConf() === "produccion" || modoEspecialidadConf();

  if (!esProd || pendientes <= 0) {
    fila.hidden = true;
    return;
  }
  const disponibles = (lim && lim.disponibles != null) ? lim.disponibles : pendientes;
  const max = Math.max(0, Math.min(pendientes, disponibles));

  fila.hidden = false;
  const techo = Math.max(1, max);
  range.max = num.max = String(techo);
  range.min = num.min = "1";
  range.disabled = num.disabled = max <= 0;
  // Por defecto se envían todos los que permite el cupo de hoy.
  range.value = num.value = String(techo);
  $("#limiteMaxConf").textContent = max;

  if (nota) {
    if (lim && lim.tier) {
      nota.hidden = false;
      const extra = pendientes > max
        ? ` · ${pendientes - max} quedan para más adelante`
        : "";
      nota.textContent =
        `${lim.disponibles} de ${lim.tier} disponibles hoy y disponibles en base de datos: ` +
        `${pendientes}${extra}.`;
    } else {
      nota.hidden = false;
      nota.textContent = `Disponibles en base de datos: ${pendientes}.`;
    }
  }
}

(function sincronizarLimiteConf() {
  const range = $("#limiteRangeConf");
  const num = $("#limiteNumConf");
  if (!range || !num) return;
  const clamp = (v) => Math.min(Math.max(1, parseInt(v, 10) || 1), parseInt(num.max, 10) || 1);
  range.addEventListener("input", () => { num.value = range.value; });
  num.addEventListener("input", () => { range.value = clamp(num.value); });
  num.addEventListener("change", () => { num.value = range.value = clamp(num.value); });
})();

function fmtMoneda(monto, moneda) {
  const entero = Number.isInteger(monto);
  const s = monto.toLocaleString("de-DE", {
    minimumFractionDigits: entero ? 0 : 2,
    maximumFractionDigits: entero ? 0 : 2,
  });
  return `${s} ${moneda}`;
}

// Editor del límite diario (wa_messaging_limit_24h) para admin/dev. Se muestra
// dentro del modal de envío para ajustarlo a lo que indique el dashboard de Meta.
function cargarLimiteAdminConf() {
  const fila = $("#filaLimiteAdminConf");
  if (!fila) return;
  if (!window.snwEsPrivilegiado) {
    fila.hidden = true;
    return;
  }
  fila.hidden = false;
  fetch("api/whatsapp/messaging-limit", { headers: authHeaders(), cache: "no-store" })
    .then((r) => (r.ok ? r.json() : null))
    .then((data) => {
      if (!data || data.tier == null) return;
      $("#limiteAdminNumConf").value = String(data.tier);
    })
    .catch(() => {});
}

const btnGuardarLimiteConf = $("#btnGuardarLimiteConf");
if (btnGuardarLimiteConf) {
  btnGuardarLimiteConf.addEventListener("click", async () => {
    const input = $("#limiteAdminNumConf");
    const valor = parseInt(input.value, 10);
    if (!Number.isFinite(valor) || valor < 0) {
      return toast("Escribe un número válido (0 = ilimitado).", "error");
    }
    btnGuardarLimiteConf.disabled = true;
    try {
      const res = await fetch("api/whatsapp/messaging-limit", {
        method: "PUT",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ limite: valor }),
      });
      if (res.status === 401) { window.snwSesionExpirada(); return; }
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail ?? `Error ${res.status}`);
      input.value = String(data.tier);
      toast("Límite diario actualizado.");
      actualizarResumenConf();
    } catch (e) {
      toast(e.message || "No se pudo guardar el límite.", "error");
    } finally {
      btnGuardarLimiteConf.disabled = false;
    }
  });
}

// Aviso del límite diario de WhatsApp alcanzado. En producción bloquea el
// envío; en desarrollo solo se indica (el envío no se bloquea).
function mostrarAvisoLimiteConf(lim, esProd) {
  const box = $("#confAvisoLimite");
  if (!box) return;
  if (!lim || lim.disponibles > 0) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  box.textContent =
    `Límite diario de WhatsApp alcanzado: en las últimas 24 h ya se contactó a ` +
    `${lim.usados_24h} usuarios únicos (límite ${lim.tier}).` +
    (esProd ? "" : " En desarrollo el envío no se bloquea, pero en producción se rechazaría.");
}

async function actualizarResumenConf() {
  const dd = $("#confDestinatarios");
  const filaCosto = $("#filaCostoConf");
  const ddCosto = $("#confCosto");
  dd.textContent = "Contando...";
  filaCosto.hidden = true;
  $("#btnLanzarConf").disabled = true;

  try {
    const espId = especialidadEnvioId();
    const cuerpoEnvio = { plantilla_id: activaId, ambiente: ambienteEnvioConf() };
    if (espId != null) cuerpoEnvio.especialidad_id = espId;
    const res = await fetch("api/notificaciones/destinatarios", {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(cuerpoEnvio),
    });
    if (res.status === 401) { window.snwSesionExpirada(); return; }
    const data = await res.json();
    const lim = data.limite_mensajeria || null;
    const esProd = baseSeleccionadaConf() === "produccion" || espId != null;
    const sinCupo = !!(lim && lim.disponibles <= 0);
    dd.textContent = `${data.pendientes} pendiente(s) · base: ${data.base_datos}`;
    configurarLimiteConf(data.pendientes || 0, lim);
    // En producción el cupo diario bloquea el envío; en desarrollo solo se avisa.
    $("#btnLanzarConf").disabled = data.pendientes === 0 || (sinCupo && esProd);
    mostrarAvisoLimiteConf(lim, esProd);
    if (sinCupo && esProd) {
      dd.textContent =
        `Sin cupo hoy: ${data.pendientes} pendiente(s) · límite de WhatsApp alcanzado ` +
        `(${lim.usados_24h}/${lim.tier} usuarios en 24 h).`;
    }

    if (data.costo) {
      ddCosto.textContent =
        `${fmtMoneda(data.costo.costo, data.costo.moneda)} ` +
        `(${data.costo.total} × ${fmtMoneda(data.costo.rate, data.costo.moneda)}, categoría ${data.costo.categoria})`;
      filaCosto.hidden = false;
    } else {
      filaCosto.hidden = true;
    }
  } catch {
    dd.textContent = "No se pudieron contar.";
    configurarLimiteConf(0);
    mostrarAvisoLimiteConf(null, false);
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
  // Sin envío en curso: se comporta como cerrar.
  clearInterval(timerPollingConf);
  modalConf.hidden = true;
});
$("#btnNoCancelarConf").addEventListener("click", () => {
  $("#modalCancelarConf").hidden = true;
  if (jobIdActualConf) {
    reanudarJobConf(jobIdActualConf);
    seguirProgresoConf(jobIdActualConf, totalActualConf);
  }
});
$("#btnConfirmarCancelarConf").addEventListener("click", async () => {
  const accion = await (async () => {
    try {
      const res = await fetch(`api/notificaciones/jobs/${jobIdActualConf}/cancelar`, {
        method: "POST",
        headers: authHeaders(),
      });
      if (res.status === 401) { window.snwSesionExpirada(); return false; }
      return res.ok;
    } catch {
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
    modalConf.hidden = true;
  } else {
    toast("No se pudo cancelar el envío.", "error");
    if (jobIdActualConf) seguirProgresoConf(jobIdActualConf, totalActualConf);
  }
});
modalConf.addEventListener("click", (e) => {
  if (envioEnCursoConf) return;
  if (e.target === modalConf) {
    clearInterval(timerPollingConf);
    modalConf.hidden = true;
  }
});
$("#btnCerrarConf").addEventListener("click", () => {
  clearInterval(timerPollingConf);
  // Si había un envío en curso, el trabajo sigue en el servidor (se ve en Historial).
  if (envioEnCursoConf) setBloqueoEnvioConf(false);
  modalConf.hidden = true;
});
$("#btnCerrarRechazoConf").addEventListener("click", () => ($("#modalRechazadoConf").hidden = true));
const modalRechazadoConfEl = $("#modalRechazadoConf");
modalRechazadoConfEl.addEventListener("click", (e) => {
  if (e.target === modalRechazadoConfEl) modalRechazadoConfEl.hidden = true;
});

$("#btnLanzarConf").addEventListener("click", async () => {
  if (envioEnCursoConf) return;
  $("#btnLanzarConf").hidden = true;
  setBloqueoEnvioConf(true);

  try {
    const cuerpo = { plantilla_id: activaId, ambiente: ambienteEnvioConf() };
    const espIdLanzar = especialidadEnvioId();
    if (espIdLanzar != null) cuerpo.especialidad_id = espIdLanzar;
    const lim = limiteEnvioConf();
    if (lim != null) cuerpo.limite = lim;
    const res = await fetch("api/notificaciones/enviar", {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(cuerpo),
    });
    if (res.status === 401) { window.snwSesionExpirada(); setBloqueoEnvioConf(false); return; }
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail ?? `Error ${res.status}`);

    pintarRechazadosConf(data.rechazados ?? []);

    if (data.aviso_limite_mensajeria) {
      toast(data.aviso_limite_mensajeria, "error");
    }

    if (data.requiere_confirmacion) {
      modalConf.hidden = true;
      const espera = document.getElementById("modalEspera");
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
            modalConf.hidden = false;
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
    toast(`Error al iniciar el envío: ${err.message}`, "error");
    setBloqueoEnvioConf(false);
    $("#btnLanzarConf").hidden = false;
  }
});

function pintarRechazadosConf(rechazados) {
  const ul = $("#listaRechazadosConf");
  ul.innerHTML = "";
  const esDev = ambienteConf === "desarrollo";
  if (esDev) {
    ul.hidden = true;
    return;
  }
  for (const r of rechazados) {
    const li = document.createElement("li");
    li.textContent = `${r.nombre} (${r.telefono || "sin teléfono"}): ${r.motivo}`;
    ul.appendChild(li);
  }
  ul.hidden = rechazados.length === 0;
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
      toast(`No se pudo sincronizar con Meta: ${err.message}`, "error");
    } finally {
      setConsultandoEstado(false, btnSincronizarMeta);
      window.snwCooldownBoton(btnSincronizarMeta);
    }
  });
}

aplicarModoSoloLecturaPlantillas();
modoVacia();
cargarEspecialidades();
cargar();
setInterval(revisarPlantillasEnSegundoPlano, 30000);
