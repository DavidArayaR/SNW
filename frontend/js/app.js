const API_URL = "api/plantillas";

let plantillas = [];
let activaId = null;
let snapshot = null;

const $ = (sel) => document.querySelector(sel);

const listaEl = $("#listaPlantillas");
const buscadorEl = $("#buscador");
const formEl = $("#formPlantilla");
const inpNombre = $("#inpNombre");
const inpMensaje = $("#inpMensaje");
const inpTemplate = $("#inpTemplate");
const inpTemplateLang = $("#inpTemplateLang");
const inpTemplateCategoria = $("#inpTemplateCategoria");
const hayTemplateMeta = !!inpTemplate && !!inpTemplateLang && !!inpTemplateCategoria;

const valTemplate = () => (hayTemplateMeta ? inpTemplate.value : "");
const valTemplateLang = () => (hayTemplateMeta ? inpTemplateLang.value : "es");
const valTemplateCategoria = () => (hayTemplateMeta ? inpTemplateCategoria.value : "UTILITY");
const contadorEl = $("#contador");
const avisoComodines = $("#avisoComodines");
const previewTexto = $("#previewTexto");
const previewHora = $("#previewHora");
const tituloForm = $("#tituloFormulario");
const estadoVacio = $("#estadoVacio");
const btnEliminar = $("#btnEliminar");
const btnGuardar = $("#btnGuardar");
const modalEl = $("#modalEliminar");
const toastEl = $("#toast");

const DATOS_EJEMPLO = {
  nombre: "David",
  apellido: "Araya",
  info_extra: "su cita es el lunes 31-08-2026 a las 10:30 en Consulta Nº 4",
};

if (!localStorage.getItem("snw_token")) location.replace("login.html");

function authHeaders(extra = {}) {
  return { Authorization: "Bearer " + (localStorage.getItem("snw_token") || ""), ...extra };
}

function seleccionarDefault() {
  const def = plantillas.find((p) => p.clave === "default" || String(p.nombre || "").toLowerCase() === "default");
  if (def) abrir(def.id);
}

async function cargar() {
  for (let intento = 1; intento <= 2; intento++) {
    try {
      const res = await fetch(API_URL, { headers: authHeaders(), cache: "no-store" });
      if (res.status === 401) { window.snwSalir(); return; }
      if (!res.ok) throw new Error(res.status);
      plantillas = await res.json();
      if (!Array.isArray(plantillas)) throw new Error("formato inválido");
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

async function crearPlantilla(nombre, texto, whatsapp_template, whatsapp_template_lang, whatsapp_template_categoria) {
  const res = await fetch(API_URL, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ clave: slug(nombre), nombre, texto, whatsapp_template, whatsapp_template_lang, whatsapp_template_categoria }),
  });
  if (res.status === 401) { window.snwSalir(); return Promise.reject(new Error("Sesión expirada")); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail ?? data.error ?? `Error ${res.status}`);
  return data;
}

async function actualizarPlantilla(id, nombre, texto, whatsapp_template, whatsapp_template_lang, whatsapp_template_categoria) {
  const res = await fetch(`${API_URL}/${id}`, {
    method: "PUT",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ nombre, texto, whatsapp_template, whatsapp_template_lang, whatsapp_template_categoria }),
  });
  if (res.status === 401) { window.snwSalir(); return Promise.reject(new Error("Sesión expirada")); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail ?? data.error ?? `Error ${res.status}`);
  return data;
}

async function eliminarPlantilla(id) {
  const res = await fetch(`${API_URL}/${id}`, { method: "DELETE", headers: authHeaders() });
  if (res.status === 401) { window.snwSalir(); return Promise.reject(new Error("Sesión expirada")); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail ?? data.error ?? `Error ${res.status}`);
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

  for (const p of visibles) {
    const primeraLinea = String(p.texto ?? "").split("\n")[0] || "(sin contenido)";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "tpl-item" + (p.id === activaId ? " tpl-item--activa" : "");
    btn.innerHTML =
      `<span class="tpl-item__nombre">${escaparHtml(p.nombre ?? "(sin nombre)")}</span>` +
      `<span class="tpl-item__vista">${escaparHtml(primeraLinea)}</span>`;
    btn.addEventListener("click", () => intentarAbrir(p.id));
    listaEl.appendChild(btn);
  }
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
    previewTexto.textContent = reemplazado;
    previewTexto.parentElement.classList.remove("bubble--vacia");
  }

  previewHora.textContent = new Date().toLocaleTimeString("es-CL", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function actualizarContador() {
  const largo = inpMensaje.value.length;
  contadorEl.textContent = `${largo} / 4096`;
  contadorEl.classList.toggle("char-count--limite", largo > 4096);
}

function validarComodines() {
  const desconocidos = [...inpMensaje.value.matchAll(/\{([^{}]+)\}/g)]
    .map((m) => m[1].trim())
    .filter((t) => !["nombre", "apellido", "info_extra"].includes(t));

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
}

function marcarSnapshot() {
  snapshot = JSON.stringify([inpNombre.value, inpMensaje.value, valTemplate(), valTemplateLang(), valTemplateCategoria()]);
}

function hayCambios() {
  return snapshot !== null && snapshot !== JSON.stringify([inpNombre.value, inpMensaje.value, valTemplate(), valTemplateLang(), valTemplateCategoria()]);
}

function abrir(id) {
  const p = plantillas.find((x) => x.id === id);
  if (!p) return;
  activaId = id;
  estadoVacio.style.display = "none";
  formEl.style.display = "";
  tituloForm.textContent = `Editando: ${p.nombre}`;
  inpNombre.value = p.nombre;
  inpMensaje.value = p.texto;
  if (hayTemplateMeta) {
    inpTemplate.value = p.whatsapp_template || "";
    inpTemplateLang.value = p.whatsapp_template_lang || "es";
    inpTemplateCategoria.value = p.whatsapp_template_categoria || "UTILITY";
  }
  btnEliminar.hidden = false;
  inpNombre.classList.remove("invalido");
  inpMensaje.classList.remove("invalido");
  refrescarEditor();
  marcarSnapshot();
  renderLista(buscadorEl.value);
}

function modoNueva() {
  activaId = null;
  estadoVacio.style.display = "none";
  formEl.style.display = "";
  tituloForm.textContent = "Nueva plantilla";
  inpNombre.value = "";
  inpMensaje.value = "";
  if (hayTemplateMeta) {
    inpTemplate.value = "";
    inpTemplateLang.value = "es";
    inpTemplateCategoria.value = "UTILITY";
  }
  btnEliminar.hidden = true;
  inpNombre.classList.remove("invalido");
  inpMensaje.classList.remove("invalido");
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
  btnEliminar.hidden = true;
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
  activaId ? abrir(activaId) : modoVacia();
}

formEl.addEventListener("submit", async (e) => {
  e.preventDefault();

  const nombre = inpNombre.value.trim();
  const texto = inpMensaje.value.trim();
  const whatsapp_template = valTemplate().trim() || null;
  const whatsapp_template_lang = valTemplateLang() || "es";
  const whatsapp_template_categoria = valTemplateCategoria() || "UTILITY";

  inpNombre.classList.toggle("invalido", !nombre);
  inpMensaje.classList.toggle("invalido", !texto);

  if (!nombre) return toast("Falta el nombre.", "error");
  if (!texto) return toast("El mensaje está vacío.", "error");

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
    let fila;
    if (activaId) {
      fila = await actualizarPlantilla(activaId, nombre, texto, whatsapp_template, whatsapp_template_lang, whatsapp_template_categoria);
      const i = plantillas.findIndex((x) => x.id === activaId);
      if (i >= 0) plantillas[i] = fila;
    } else {
      fila = await crearPlantilla(nombre, texto, whatsapp_template, whatsapp_template_lang, whatsapp_template_categoria);
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
  try {
    await eliminarPlantilla(activaId);
    plantillas = plantillas.filter((x) => x.id !== activaId);
    modalEl.hidden = true;
    toast("Plantilla eliminada.", "ok");
    modoVacia();
  } catch (err) {
    modalEl.hidden = true;
    toast(`Error al eliminar: ${err.message}`, "error");
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

inpNombre.addEventListener("input", () => inpNombre.classList.remove("invalido"));
inpMensaje.addEventListener("input", refrescarEditor);
buscadorEl.addEventListener("input", () => renderLista(buscadorEl.value));
$("#btnNueva").addEventListener("click", intentarNueva);
$("#btnNuevaEmpty").addEventListener("click", intentarNueva);

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

// Bloquea/desbloquea toda la interfaz durante el guardado y muestra el estado
// en el botón Guardar (con spinner).
function setGuardando(guardando) {
  document.querySelectorAll("button, input, select, textarea").forEach((el) => {
    el.disabled = guardando;
  });
  if (btnGuardar) {
    btnGuardar.innerHTML = guardando
      ? '<span class="spinner"></span> Guardando...'
      : "Guardar";
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
  document.querySelectorAll("button, input, select, textarea").forEach((el) => {
    // El botón Cancelar siempre queda habilitado durante el envío
    if (el.id === "btnCancelarConf") return;
    el.disabled = bloquear;
  });
}

// Sincronizar con el .env global: si el archivo cambió a produccion/desarrollo, actualizar la selección
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
    if (badgeEntornoMensajeria) {
      badgeEntornoMensajeria.textContent = `Base de datos ${cfg.entorno === "produccion" ? "producción" : "desarrollo"} · ${cfg.base_datos ?? ""}`;
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

// Devuelve true si el usuario normal debe quedar restringido a la base de
// desarrollo (cuando el entorno global del sistema es desarrollo).
function usuarioRestringidoADesarrollo() {
  const rol = localStorage.getItem("snw_rol");
  return rol !== "administrador" && entornoGlobal === "desarrollo";
}

// Aplica la restricción de base de datos en el modal de envío.
function aplicarRestriccionAmbiente() {
  const radios = document.querySelectorAll('input[name="ambienteConf"]');
  const restringido = usuarioRestringidoADesarrollo();

  radios.forEach((r) => {
    if (r.value === "produccion") {
      r.disabled = restringido;
    }
  });

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
  document.querySelectorAll('input[name="ambienteConf"]').forEach((r) => {
    r.checked = r.value === ambienteConf;
  });
  refrescarAvisoDevConf();
  refrescarAvisoAdminConf();
  actualizarResumenConf();

  $("#confProgreso").hidden = true;
  $("#listaRechazadosConf").innerHTML = "";
  $("#listaRechazadosConf").hidden = true;
  $("#btnCerrarConf").hidden = true;
  $("#btnLanzarConf").disabled = true;
  $("#btnLanzarConf").hidden = false;
  $("#btnCancelarConf").hidden = false;

  modalConf.hidden = false;
}

$("#btnEnviarActual").addEventListener("click", () => {
  if (!activaId) return toast("Guarda la plantilla antes de enviarla.", "error");
  if (hayCambios()) {
    return toast("Hay cambios sin guardar. Presiona Guardar primero (Ctrl+S).", "error");
  }
  abrirModalConf();
});

document.querySelectorAll('input[name="ambienteConf"]').forEach((r) => {
  r.addEventListener("change", () => {
    ambienteConf = document.querySelector('input[name="ambienteConf"]:checked').value;
    localStorage.setItem("snw_ambiente", ambienteConf);
    localStorage.setItem("snw_ambiente_admin", ambienteConf);
    actualizarBadgeMensajeria();
    refrescarAvisoDevConf();
    refrescarAvisoAdminConf();
    actualizarResumenConf();
  });
});

function refrescarAvisoDevConf() {
  const box = $("#confAvisoDev");
  const esDev = ambienteConf === "desarrollo";
  box.hidden = !esDev;
  if (!esDev) return;
  fetch(`api/configuracion?ambiente=${ambienteConf}`, { headers: authHeaders() })
    .then((r) => (r.ok ? r.json() : {}))
    .then((cfg) => {
      const nums = (cfg.numeros_autorizados ?? []).join(", ") || "ninguno";
      box.textContent =
        `Base de datos desarrollo: solo se enviará a los números autorizados (${nums}). El resto será descartado.`;
    })
    .catch(() => {});
}

// Aviso rojo para el administrador: en producción envía directo sin confirmación.
function refrescarAvisoAdminConf() {
  const box = $("#confAvisoAdmin");
  const rol = localStorage.getItem("snw_rol");
  const esAdminProduccion = rol === "administrador" && ambienteConf === "produccion";
  box.hidden = !esAdminProduccion;
  if (esAdminProduccion) {
    box.textContent =
      "Logeado como admin: se envía directamente sin confirmación de supervisor.";
  }
}

async function actualizarResumenConf() {
  const dd = $("#confDestinatarios");
  dd.textContent = "Contando...";
  $("#btnLanzarConf").disabled = true;

  try {
    const res = await fetch("api/notificaciones/destinatarios", {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ ambiente: ambienteConf }),
    });
    if (res.status === 401) { window.snwSalir(); return; }
    const data = await res.json();
    dd.textContent = `${data.pendientes} pendiente(s) · base: ${data.base_datos}`;
    $("#btnLanzarConf").disabled = data.pendientes === 0;
  } catch {
    dd.textContent = "No se pudieron contar.";
  }
}

$("#btnCancelarConf").addEventListener("click", () => {
  const enProgreso = envioEnCursoConf && jobIdActualConf;
  if (envioEnCursoConf && !enProgreso) {
    clearInterval(timerPollingConf);
    modalConf.hidden = true;
    return;
  }
  if (enProgreso) {
    clearInterval(timerPollingConf);
    const restantes = Math.max(0, totalActualConf - hechosActualConf);
    $("#mensajeCancelarConf").textContent =
      `¿Seguro que quieres cancelar el envío de ${restantes} mensaje(s) restante(s)?`;
    $("#btnNoCancelarConf").disabled = false;
    $("#btnConfirmarCancelarConf").disabled = false;
    $("#modalCancelarConf").hidden = false;
    pausarJobConf(jobIdActualConf);
  }
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
      if (res.status === 401) { window.snwSalir(); return false; }
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
  if (envioEnCursoConf) return;
  clearInterval(timerPollingConf);
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
    const res = await fetch("api/notificaciones/enviar", {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ plantilla_id: activaId, ambiente: ambienteConf }),
    });
    if (res.status === 401) { window.snwSalir(); setBloqueoEnvioConf(false); return; }
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail ?? `Error ${res.status}`);

    pintarRechazadosConf(data.rechazados ?? []);

    if (data.requiere_confirmacion) {
      modalConf.hidden = true;
      const espera = document.getElementById("modalEspera");
      espera.hidden = false;
      const linkEl = document.getElementById("linkConfirmacionEsperaMsg");
      if (linkEl && data.confirm_url && localStorage.getItem("snw_rol") === "administrador") {
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
    $("#btnCancelarConf").hidden = false;
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
      if (res.status === 401) { window.snwSalir(); return; }
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
  $("#btnCerrarConf").hidden = false;
  $("#btnCerrarConf").disabled = false;
  $("#btnCancelarConf").hidden = true;
  setBloqueoEnvioConf(false);
  toast(mensaje, esError ? "error" : "ok");
}

modoVacia();
cargar();
