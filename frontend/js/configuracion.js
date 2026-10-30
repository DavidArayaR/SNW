(function () {
const $ = (sel) => document.querySelector(sel);

function authHeaders(extra = {}) {
  return { Authorization: "Bearer " + (localStorage.getItem("snw_token") || ""), ...extra };
}

const toastEl = $("#toast");
let toastTimer;
function toast(msg, tipo = "ok") {
  clearTimeout(toastTimer);
  toastEl.textContent = msg;
  toastEl.className = `toast visible toast--${tipo}`;
  // Los errores no se desvanecen solos: se cierran con click para leerlos bien.
  if (tipo === "ok") {
    toastTimer = setTimeout(() => toastEl.classList.remove("visible"), 3600);
  }
}

// Los errores no se desvanecen solos: se cierran con click para leerlos bien.
toastEl.addEventListener("click", () => toastEl.classList.remove("visible"));

const cont = $("#secciones");
const idsTabs = ["general", "whatsapp", "clinicas", "correo", "integraciones", "respuestas", "logs"];
let tabConfig = "general";
let logSeleccionado = "";
let peticionLog = 0;
try {
  const guardada = localStorage.getItem("snw_tab_config");
  if (idsTabs.includes(guardada)) tabConfig = guardada;
} catch { /* sin almacenamiento */ }
let centrosConfig = [];
let plantillasReagendar = [];
let tokenGlobalConfigurado = false;
let valores = {};          // valor guardado (servidor)
let estadoSupresion = {};  // solo conteos; nunca teléfonos ni huellas
let campos = {};           // clave -> { def, input }
let derivados = [];        // campos calculados (ej. URL del webhook)

function tabDeSeccion(id) {
  if (id.startsWith("centro_")) return "clinicas";
  if (id.startsWith("respuesta_")) return "respuestas";
  if (id === "correo") return "correo";
  if (id === "whatsapp") return "whatsapp";
  if (id === "ia" || id === "call_center" || id === "reagendar") return "integraciones";
  return "general";
}

function posicionarIndicadorConfig() {
  const barra = $(".config-tabs");
  const activa = barra?.querySelector(".msg-tab.activo");
  if (!barra || !activa) return;
  if (activa.offsetLeft < barra.scrollLeft + 8) {
    barra.scrollLeft = Math.max(0, activa.offsetLeft - 8);
  } else if (activa.offsetLeft + activa.offsetWidth > barra.scrollLeft + barra.clientWidth - 8) {
    barra.scrollLeft = activa.offsetLeft + activa.offsetWidth - barra.clientWidth + 8;
  }
  const rectBarra = barra.getBoundingClientRect();
  const rectActiva = activa.getBoundingClientRect();
  barra.style.setProperty("--msg-indicador-x",
    `${rectActiva.left - rectBarra.left - barra.clientLeft + barra.scrollLeft}px`);
  barra.style.setProperty("--msg-indicador-ancho", `${rectActiva.width}px`);
}

function mostrarTabConfig(anterior = tabConfig) {
  document.querySelectorAll("[data-config-tab]").forEach((boton) => {
    const seleccionada = boton.dataset.configTab === tabConfig;
    boton.classList.toggle("activo", seleccionada);
    boton.setAttribute("aria-selected", String(seleccionada));
    boton.tabIndex = seleccionada ? 0 : -1;
  });
  posicionarIndicadorConfig();
  cont.querySelectorAll(".config-tab-panel").forEach((panel) => {
    if (panel.dataset.configPanel === tabConfig && panel.hidden) {
      panel.style.setProperty("--config-panel-entrada-x",
        idsTabs.indexOf(tabConfig) > idsTabs.indexOf(anterior) ? "20px" : "-20px");
    }
    panel.hidden = panel.dataset.configPanel !== tabConfig;
  });
  $("#configApiDocs").hidden = tabConfig !== "general";
  $("#btnGuardar").hidden = tabConfig === "logs";
  $(".config-intro").hidden = tabConfig === "logs";
}

function cambiarTabConfig(destino, { enfocar = false } = {}) {
  if (!idsTabs.includes(destino) || destino === tabConfig) return;
  const anterior = tabConfig;
  tabConfig = destino;
  try { localStorage.setItem("snw_tab_config", destino); } catch { /* sin almacenamiento */ }
  mostrarTabConfig(anterior);
  if (destino === "logs") cargarLogs();
  if (enfocar) $(`#config-tab-${destino}`)?.focus();
  window.scrollTo(0, 0);
}

document.querySelectorAll("[data-config-tab]").forEach((boton) => {
  boton.addEventListener("click", () => cambiarTabConfig(boton.dataset.configTab));
  boton.addEventListener("keydown", (evento) => {
    const actual = idsTabs.indexOf(boton.dataset.configTab);
    const destino = evento.key === "ArrowRight" ? idsTabs[(actual + 1) % idsTabs.length]
      : evento.key === "ArrowLeft" ? idsTabs[(actual - 1 + idsTabs.length) % idsTabs.length]
      : evento.key === "Home" ? idsTabs[0]
      : evento.key === "End" ? idsTabs[idsTabs.length - 1] : null;
    if (destino) {
      evento.preventDefault();
      cambiarTabConfig(destino, { enfocar: true });
    }
  });
});
window.addEventListener("resize", posicionarIndicadorConfig);

const CATEGORIAS_PATRONES = [
  ["CONSULTA_HORAS", "Horas y citas"],
  ["CONSULTA_MEDICO", "Profesionales de salud"],
  ["CONSULTA_PRECIOS", "Precios"],
  ["SOLICITA_CONTACTO", "Solicitud de contacto"],
  ["SOLICITA_INFORMACION", "Solicitud de información"],
  ["CONSULTA_AMBIGUA", "Consultas generales"],
];

function leerPatrones(valor) {
  try {
    const obj = JSON.parse(valor || "{}");
    if (!obj || typeof obj !== "object" || Array.isArray(obj)) return null;
    const conocidas = new Set(CATEGORIAS_PATRONES.map(([clave]) => clave));
    if (Object.entries(obj).some(([clave, frases]) =>
      !conocidas.has(clave) || !Array.isArray(frases) || frases.some((f) => typeof f !== "string"))) return null;
    return obj;
  } catch { return null; }
}

const CLAVES_REAGENDAR = ["reagendar_plantillas_json", "reagendar_clinicas_ids"];

function leerMapaReagendar(valor) {
  try {
    const mapa = JSON.parse(valor || "{}");
    return mapa && typeof mapa === "object" && !Array.isArray(mapa) &&
      Object.entries(mapa).every(([clave, dato]) => clave && typeof dato === "string" && dato)
      ? mapa : null;
  } catch { return null; }
}

function mapasEquivalentes(a, b) {
  const primero = leerMapaReagendar(a);
  const segundo = leerMapaReagendar(b);
  if (!primero || !segundo) return a.trim() === b.trim();
  const claves = Object.keys(primero).sort();
  return claves.length === Object.keys(segundo).length &&
    claves.every((clave) => primero[clave] === segundo[clave]);
}

function opcionesCentro(valor = "") {
  const centros = centrosConfig.map((c) => ({ codigo: c.codigo, nombre: c.nombre }));
  if (valor && !centros.some((c) => c.codigo === valor)) {
    centros.push({ codigo: valor, nombre: `${valor} · no disponible` });
  }
  return `<option value="">— Seleccione clínica —</option>` + centros.map((c) =>
    `<option value="${escaparAtributo(c.codigo)}"${c.codigo === valor ? " selected" : ""}>${escaparHtml(c.nombre)} (${escaparHtml(c.codigo)})</option>`
  ).join("");
}

function opcionesPlantilla(centro, valor = "") {
  const disponibles = plantillasReagendar.filter((p) => p.centro_codigo === centro &&
    p.whatsapp_template_status === "APPROVED" &&
    (p.aprobacion_estado || "aprobada") === "aprobada" &&
    !p.especial && p.clave !== "hello_world" && p.whatsapp_template !== "hello_world" &&
    (!p.encabezado_media_id || p.encabezado_meta_registrado));
  const opciones = disponibles.map((p) => ({ clave: p.clave, nombre: p.nombre }));
  if (valor && !opciones.some((p) => p.clave === valor)) {
    opciones.unshift({ clave: valor, nombre: `${valor} · pendiente o no disponible` });
  }
  return `<option value="">— Sin plantilla —</option>` + opciones.map((p) =>
    `<option value="${escaparAtributo(p.clave)}"${p.clave === valor ? " selected" : ""}>${escaparHtml(p.nombre)} (${escaparHtml(p.clave)})</option>`
  ).join("");
}

function filaMapaReagendar(clave, llave = "", valor = "") {
  const fila = document.createElement("div");
  fila.className = "config-reagendar__fila";
  if (clave === "reagendar_plantillas_json") {
    fila.innerHTML = `<label>Clínica<select data-reagendar-centro>${opcionesCentro(llave)}</select></label>` +
      `<label>Plantilla aprobada<select data-reagendar-plantilla>${opcionesPlantilla(llave, valor)}</select></label>` +
      `<a class="btn btn--ghost config-reagendar__crear" href="mensajeria.html?tab=plantillas&amp;crear=reagendar&amp;centro=${encodeURIComponent(llave)}"` +
      ` data-reagendar-crear>Crear plantilla</a>`;
    fila.querySelector("[data-reagendar-centro]").addEventListener("change", (evento) => {
      fila.querySelector("[data-reagendar-plantilla]").innerHTML = opcionesPlantilla(evento.target.value);
      fila.querySelector("[data-reagendar-crear]").href =
        `mensajeria.html?tab=plantillas&crear=reagendar&centro=${encodeURIComponent(evento.target.value)}`;
      sincronizarMapaReagendar(fila.closest(".config-campo"));
    });
    fila.querySelector("[data-reagendar-plantilla]").addEventListener("change", () =>
      sincronizarMapaReagendar(fila.closest(".config-campo")));
    fila.querySelector("[data-reagendar-crear]").addEventListener("click", (evento) => {
      if (!fila.querySelector("[data-reagendar-centro]").value) {
        evento.preventDefault();
        toast("Selecciona una clínica antes de crear su plantilla.", "error");
      }
    });
  } else {
    fila.innerHTML = `<label>ID de clínica en Reservo<input type="text" inputmode="numeric" maxlength="30" data-reagendar-id value="${escaparAtributo(llave)}" placeholder="Ej.: 1"></label>` +
      `<label>Clínica en SNW<select data-reagendar-centro>${opcionesCentro(valor)}</select></label>` +
      `<button type="button" class="btn btn--ghost" data-reagendar-quitar aria-label="Quitar asociación">Quitar</button>`;
    fila.querySelectorAll("input, select").forEach((control) =>
      control.addEventListener("input", () => sincronizarMapaReagendar(fila.closest(".config-campo"))));
    fila.querySelector("[data-reagendar-quitar]").addEventListener("click", () => {
      const wrap = fila.closest(".config-campo");
      fila.remove();
      sincronizarMapaReagendar(wrap);
    });
  }
  return fila;
}

function htmlEditorReagendar(clave, valor) {
  const mapa = leerMapaReagendar(valor);
  return `<div class="config-reagendar" data-modo="${mapa ? "facil" : "json"}">` +
    `<label class="config-reagendar__toggle"><input type="checkbox" data-reagendar-json${mapa ? "" : " checked"}> Pegar JSON</label>` +
    `<div data-reagendar-facil${mapa ? "" : " hidden"}>` +
      `<div class="config-reagendar__filas"></div>` +
      (clave === "reagendar_clinicas_ids" ? `<button type="button" class="btn btn--ghost" data-reagendar-agregar>Añadir ID externo</button>` : "") +
      `<p class="field__hint">${clave === "reagendar_plantillas_json" ? "Elige una plantilla aprobada por clínica. Crear plantilla abre un borrador en Mensajería; después debes guardarlo y esperar su aprobación." : "Relaciona los IDs de clínica que envía Reservo con las clínicas de SNW."}</p></div>` +
    `<div data-reagendar-raw${mapa ? " hidden" : ""}><label for="raw_${clave}">JSON completo</label>` +
      `<textarea id="raw_${clave}" rows="6" data-reagendar-valor spellcheck="false">${escaparHtml(mapa ? JSON.stringify(mapa, null, 2) : valor)}</textarea></div>` +
    `<p class="config-reagendar__error" role="alert" hidden></p>` +
    `<textarea id="cf_${clave}" data-clave="${clave}" hidden>${escaparHtml(valor)}</textarea></div>`;
}

function sincronizarMapaReagendar(wrap) {
  const editor = wrap.querySelector(".config-reagendar");
  const clave = editor.querySelector("[data-clave]").dataset.clave;
  const json = editor.querySelector("[data-reagendar-json]").checked;
  const error = editor.querySelector(".config-reagendar__error");
  let mensaje = "";
  if (json) {
    const valor = editor.querySelector("[data-reagendar-valor]").value.trim();
    const mapa = leerMapaReagendar(valor);
    mensaje = !mapa ? "Escribe un objeto JSON con claves y valores de texto." : "";
    editor.querySelector("[data-clave]").value = valor;
  } else {
    const mapa = Object.create(null);
    for (const fila of editor.querySelectorAll(".config-reagendar__fila")) {
      const llave = clave === "reagendar_plantillas_json"
        ? fila.querySelector("[data-reagendar-centro]").value
        : fila.querySelector("[data-reagendar-id]").value.trim();
      const valor = clave === "reagendar_plantillas_json"
        ? fila.querySelector("[data-reagendar-plantilla]").value
        : fila.querySelector("[data-reagendar-centro]").value;
      if (clave === "reagendar_clinicas_ids" && llave && !/^\d+$/.test(llave)) mensaje = "El ID externo debe contener solo números.";
      if (clave === "reagendar_plantillas_json" && llave && !valor) continue;
      if (llave && valor) {
        if (Object.hasOwn(mapa, llave)) mensaje = "Hay una clínica o ID repetido.";
        mapa[llave] = valor;
      } else if (llave || valor) mensaje = "Completa ambos campos o quita la fila.";
    }
    editor.querySelector("[data-clave]").value = JSON.stringify(mapa);
    editor.querySelector("[data-reagendar-valor]").value = JSON.stringify(mapa, null, 2);
  }
  error.textContent = mensaje;
  error.hidden = !mensaje;
  editor.classList.toggle("config-reagendar--invalido", !!mensaje);
  marcarCambios();
}

function restaurarMapaReagendar(wrap, valor) {
  const editor = wrap.querySelector(".config-reagendar");
  const clave = editor.querySelector("[data-clave]").dataset.clave;
  const mapa = leerMapaReagendar(valor);
  const usarJson = !mapa;
  editor.querySelector("[data-reagendar-json]").checked = usarJson;
  editor.querySelector("[data-reagendar-facil]").hidden = usarJson;
  editor.querySelector("[data-reagendar-raw]").hidden = !usarJson;
  editor.querySelector(".config-reagendar__filas").replaceChildren();
  if (mapa) {
    if (clave === "reagendar_plantillas_json") {
      for (const centro of centrosConfig) {
        editor.querySelector(".config-reagendar__filas").appendChild(
          filaMapaReagendar(clave, centro.codigo, mapa[centro.codigo] || ""));
      }
      for (const [codigo, plantilla] of Object.entries(mapa)) {
        if (!centrosConfig.some((c) => c.codigo === codigo)) {
          editor.querySelector(".config-reagendar__filas").appendChild(filaMapaReagendar(clave, codigo, plantilla));
        }
      }
    } else {
      for (const [id, centro] of Object.entries(mapa)) {
        editor.querySelector(".config-reagendar__filas").appendChild(filaMapaReagendar(clave, id, centro));
      }
    }
  }
  editor.querySelector("[data-reagendar-valor]").value = mapa ? JSON.stringify(mapa, null, 2) : valor;
  editor.querySelector("[data-clave]").value = valor;
  sincronizarMapaReagendar(wrap);
}

function prepararEditorReagendar(wrap, valor) {
  const editor = wrap.querySelector(".config-reagendar");
  restaurarMapaReagendar(wrap, valor);
  editor.querySelector("[data-reagendar-valor]").addEventListener("input", () => sincronizarMapaReagendar(wrap));
  editor.querySelector("[data-reagendar-agregar]")?.addEventListener("click", () => {
    editor.querySelector(".config-reagendar__filas").appendChild(
      filaMapaReagendar("reagendar_clinicas_ids"));
  });
  editor.querySelector("[data-reagendar-json]").addEventListener("change", (evento) => {
    const usarJson = evento.target.checked;
    if (!usarJson && !leerMapaReagendar(editor.querySelector("[data-reagendar-valor]").value)) {
      evento.target.checked = true;
      toast("Corrige el JSON antes de abrir el formulario.", "error");
      return;
    }
    if (!usarJson) restaurarMapaReagendar(wrap, editor.querySelector("[data-reagendar-valor]").value);
    else {
      editor.querySelector("[data-reagendar-facil]").hidden = true;
      editor.querySelector("[data-reagendar-raw]").hidden = false;
      sincronizarMapaReagendar(wrap);
    }
  });
}

function patronesEquivalentes(a, b) {
  const primero = leerPatrones(a);
  const segundo = leerPatrones(b);
  if (!primero || !segundo) return a.trim() === b.trim();
  return CATEGORIAS_PATRONES.every(([clave]) =>
    JSON.stringify(primero[clave] || []) === JSON.stringify(segundo[clave] || []));
}

function htmlEditorPatrones(clave, valor) {
  const obj = leerPatrones(valor);
  const grupos = CATEGORIAS_PATRONES.map(([motivo, titulo]) => {
    const id = `patrones_${motivo}`;
    return `<div class="config-patrones__grupo">` +
      `<label for="${id}">${escaparHtml(titulo)} <span class="config-patrones__contador" data-contador-patron="${motivo}">0/30</span></label>` +
      `<textarea id="${id}" data-patron="${motivo}" rows="3" placeholder="Una frase por línea" spellcheck="true">${escaparHtml((obj?.[motivo] || []).join("\n"))}</textarea>` +
      `<p class="config-patrones__error" data-error-patron="${motivo}" hidden></p></div>`;
  }).join("");
  return `<div class="config-patrones" data-modo="${obj ? "facil" : "json"}">` +
    `<label class="config-patrones__toggle"><input type="checkbox" data-pegar-json${obj ? "" : " checked"}> Pegar JSON</label>` +
    `<div class="config-patrones__facil"${obj ? "" : " hidden"}>` +
      `<p class="config-patrones__intro">Escribe una frase por línea en la categoría correspondiente.</p>` +
      `<div class="config-patrones__grupos">${grupos}</div>` +
      `<details class="config-patrones__vista"><summary>Ver JSON que se guardará</summary><pre></pre></details></div>` +
    `<div class="config-patrones__raw"${obj ? " hidden" : ""}>` +
      `<label for="patrones_json_raw">Pega o edita el JSON completo</label>` +
      `<textarea id="patrones_json_raw" data-patrones-raw rows="12" spellcheck="false">${escaparHtml(obj ? JSON.stringify(obj, null, 2) : valor)}</textarea>` +
      `<p class="config-patrones__error" data-error-json hidden></p></div>` +
    `<textarea id="cf_${clave}" data-clave="${clave}" hidden>${escaparHtml(valor)}</textarea></div>`;
}

function actualizarEditorPatrones(wrap) {
  const editor = wrap.querySelector(".config-patrones");
  if (!editor) return;
  const obj = {};
  let valido = true;
  for (const [motivo] of CATEGORIAS_PATRONES) {
    const textarea = editor.querySelector(`[data-patron="${motivo}"]`);
    const frases = textarea.value.split(/\r?\n/).map((f) => f.trim()).filter(Boolean);
    const error = editor.querySelector(`[data-error-patron="${motivo}"]`);
    const mensaje = frases.length > 30 ? "Máximo 30 frases por categoría." :
      frases.some((f) => f.length < 4 || f.length > 80 || /[\d@]/.test(f))
        ? "Cada frase debe tener entre 4 y 80 caracteres, sin números ni @." : "";
    error.textContent = mensaje;
    error.hidden = !mensaje;
    textarea.setAttribute("aria-invalid", mensaje ? "true" : "false");
    editor.querySelector(`[data-contador-patron="${motivo}"]`).textContent = `${frases.length}/30`;
    if (mensaje) valido = false;
    if (frases.length) obj[motivo] = frases;
  }
  editor.classList.toggle("config-patrones--invalido", !valido);
  editor.querySelector("[data-clave]").value = JSON.stringify(obj);
  editor.querySelector("pre").textContent = JSON.stringify(obj, null, 2);
  editor.querySelector("[data-patrones-raw]").value = JSON.stringify(obj, null, 2);
  marcarCambios();
}

function actualizarJsonPatrones(wrap) {
  const editor = wrap.querySelector(".config-patrones");
  const raw = editor.querySelector("[data-patrones-raw]");
  const obj = leerPatrones(raw.value);
  let mensaje = !obj ? "JSON inválido: usa un objeto con las categorías conocidas y listas de frases." : "";
  if (obj && !mensaje) {
    for (const [motivo] of CATEGORIAS_PATRONES) {
      const frases = obj[motivo] || [];
      if (frases.length > 30) mensaje = `${motivo}: máximo 30 frases.`;
      else if (frases.some((f) => f.length < 4 || f.length > 80 || /[\d@]/.test(f))) {
        mensaje = `${motivo}: cada frase debe tener entre 4 y 80 caracteres, sin números ni @.`;
      }
      if (mensaje) break;
    }
  }
  const error = editor.querySelector("[data-error-json]");
  error.textContent = mensaje;
  error.hidden = !mensaje;
  raw.setAttribute("aria-invalid", mensaje ? "true" : "false");
  editor.classList.toggle("config-patrones--invalido", !!mensaje);
  editor.querySelector("[data-clave]").value = raw.value.trim();
  marcarCambios();
}

function cambiarModoPatrones(wrap) {
  const editor = wrap.querySelector(".config-patrones");
  const check = editor.querySelector("[data-pegar-json]");
  if (check.checked) {
    editor.dataset.modo = "json";
    editor.querySelector(".config-patrones__facil").hidden = true;
    editor.querySelector(".config-patrones__raw").hidden = false;
    actualizarJsonPatrones(wrap);
    return;
  }
  const obj = leerPatrones(editor.querySelector("[data-patrones-raw]").value);
  if (!obj || editor.classList.contains("config-patrones--invalido")) {
    check.checked = true;
    toast("Corrige el JSON antes de volver al editor sencillo.", "error");
    return;
  }
  for (const [motivo] of CATEGORIAS_PATRONES) {
    editor.querySelector(`[data-patron="${motivo}"]`).value = (obj[motivo] || []).join("\n");
  }
  editor.dataset.modo = "facil";
  editor.querySelector(".config-patrones__facil").hidden = false;
  editor.querySelector(".config-patrones__raw").hidden = true;
  actualizarEditorPatrones(wrap);
}

function restaurarPatrones(wrap, valor) {
  const editor = wrap.querySelector(".config-patrones");
  const obj = leerPatrones(valor);
  editor.querySelector("[data-patrones-raw]").value = obj ? JSON.stringify(obj, null, 2) : valor;
  if (!obj) editor.querySelector("[data-pegar-json]").checked = true;
  if (obj) {
    editor.classList.remove("config-patrones--invalido");
    for (const [motivo] of CATEGORIAS_PATRONES) {
      editor.querySelector(`[data-patron="${motivo}"]`).value = (obj[motivo] || []).join("\n");
    }
  }
  cambiarModoPatrones(wrap);
}

// Une URL base + ruta evitando "/" duplicados o faltantes.
function unirUrl(base, ruta) {
  base = (base || "").trim().replace(/\/+$/, "");
  ruta = (ruta || "").trim();
  if (!base && !ruta) return "";
  if (ruta && !ruta.startsWith("/")) ruta = "/" + ruta;
  return base + ruta;
}

function escaparHtml(t) {
  const d = document.createElement("div");
  d.textContent = t ?? "";
  return d.innerHTML;
}

function escaparAtributo(t) {
  return escaparHtml(t).replace(/"/g, "&quot;");
}

async function cargar() {
  try {
    const res = await fetch("api/configuracion/todo", { headers: authHeaders(), cache: "no-store" });
    if (res.status === 401) { window.snwSesionExpirada(); return; }
    if (res.status === 403) { location.replace("mensajeria.html"); return; }
    if (!res.ok) throw new Error();
    const d = await res.json();
    valores = d.valores || {};
    centrosConfig = d.centros || [];
    try {
      const respuesta = await fetch("api/plantillas", { headers: authHeaders(), cache: "no-store" });
      plantillasReagendar = respuesta.ok ? await respuesta.json() : [];
      if (!Array.isArray(plantillasReagendar)) plantillasReagendar = [];
    } catch { plantillasReagendar = []; }
    tokenGlobalConfigurado = !!d.wa_token_configurado;
    estadoSupresion = d.supresion || {};
    render(d.secciones || []);
    cargarEstadoDespacho();
  } catch {
    console.error("[configuracion.js cargar()]");
    cont.innerHTML = '<p class="mes-vacio" style="padding:18px;">No se pudo cargar la configuración.</p>';
  }
}

async function cargarEstadoDespacho() {
  try {
    const res = await fetch("api/whatsapp/rate-limit", { headers: authHeaders(), cache: "no-store" });
    if (!res.ok) return;
    const datos = await res.json();
    for (const canal of datos.canales || []) {
      const panel = [...cont.querySelectorAll(".config-seccion[data-centro]")]
        .find((item) => item.dataset.centro === canal.centro);
      if (!panel || !canal.configurado) continue;
      const etiqueta = document.createElement("p");
      etiqueta.className = "config-centro-ritmo";
      etiqueta.textContent = canal.espera_segundos > 0
        ? `En pausa · reintento en ${canal.espera_segundos} s`
        : `Ritmo actual: ${canal.mps_actual} de ${canal.mps_configurado} mensajes/s · ` +
          `${canal.aceptados_meta} aceptados por Meta`;
      panel.querySelector(".panel__head").after(etiqueta);
    }
  } catch (error) {
    console.error("[configuracion.js cargarEstadoDespacho()]", error);
  }
}

function agregarFormularioClinica(contenedor) {
  const acciones = document.createElement("div");
  acciones.className = "config-clinicas-acciones";
  const boton = document.createElement("button");
  boton.type = "button";
  boton.className = "btn btn--ghost";
  boton.innerHTML = '<i class="fa-solid fa-plus" aria-hidden="true"></i> Añadir clínica';
  acciones.appendChild(boton);
  const formulario = document.createElement("form");
  formulario.className = "config-nueva-clinica";
  formulario.hidden = true;
  formulario.innerHTML = '<label for="nombreNuevaClinica">Nombre de la clínica</label>' +
    '<div class="config-nueva-clinica__fila"><input id="nombreNuevaClinica" name="nombre" ' +
    'type="text" minlength="2" maxlength="100" required placeholder="Ej.: Clínica Norte">' +
    '<button class="btn btn--primary" type="submit">Crear clínica</button></div>' +
    '<p class="field__hint">Después podrás configurar su número, Phone Number ID, WABA ID y access token.</p>';
  acciones.appendChild(formulario);
  contenedor.appendChild(acciones);
  boton.addEventListener("click", () => {
    formulario.hidden = !formulario.hidden;
    if (!formulario.hidden) formulario.querySelector("input").focus();
  });
  formulario.addEventListener("submit", async (evento) => {
    evento.preventDefault();
    if (Object.keys(diff()).length) {
      toast("Guarda o descarta los cambios pendientes antes de añadir una clínica.", "error");
      return;
    }
    const enviar = formulario.querySelector("button");
    enviar.disabled = true;
    try {
      const res = await fetch("api/configuracion/clinicas", {
        method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ nombre: formulario.querySelector("input").value.trim() }),
      });
      const datos = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(datos.detail || "No se pudo añadir la clínica.");
      await cargar();
      cont.querySelector(`[data-centro="${datos.codigo}"]`)?.scrollIntoView({ behavior: "smooth", block: "center" });
      toast("Clínica añadida. Completa sus datos y guarda los cambios.");
    } catch (error) {
      toast(error.message || "No se pudo añadir la clínica.", "error");
    } finally {
      enviar.disabled = false;
    }
  });
}

function render(secciones) {
  cont.innerHTML = "";
  campos = {};
  derivados = [];
  const paneles = {};
  for (const id of idsTabs) {
    const grupo = document.createElement("div");
    grupo.className = "config-tab-panel";
    grupo.id = `config-panel-${id}`;
    grupo.dataset.configPanel = id;
    grupo.setAttribute("role", "tabpanel");
    grupo.setAttribute("aria-labelledby", `config-tab-${id}`);
    grupo.tabIndex = 0;
    grupo.hidden = true;
    cont.appendChild(grupo);
    paneles[id] = grupo;
  }
  agregarFormularioClinica(paneles.clinicas);

  for (const s of secciones) {
    const panel = document.createElement("section");
    panel.className = "panel config-seccion";
    if (s.id.startsWith("centro_")) {
      panel.dataset.centro = s.id.slice(7).toUpperCase();
      panel.dataset.activa = String(s.activa !== false);
    }
    if (s.id.startsWith("respuesta_")) {
      panel.dataset.respuestaCentro = s.id.slice(10).toUpperCase();
      panel.classList.add("config-seccion--respuestas");
    }
    panel.innerHTML =
      `<div class="panel__head"><h2><i class="${s.marca || s.icono === "fa-whatsapp" ? "fa-brands" : "fa-solid"} ${s.icono || "fa-gear"}"></i> ${escaparHtml(s.titulo)}</h2></div>` +
      `<div class="config-campos"></div>`;
    if (s.id.startsWith("centro_")) {
      const codigoCentro = s.id.slice(7).toUpperCase();
      const activa = s.activa !== false;
      const centro = centrosConfig.find((c) => c.codigo === codigoCentro);
      const estado = document.createElement("span");
      estado.className = `config-centro-estado ${!activa ? "config-centro-estado--inactiva" : centro?.configured ? "config-centro-estado--ok" : "config-centro-estado--pendiente"}`;
      estado.textContent = !activa ? "Desactivada" : centro?.configured
        ? `Configurado${centro?.avisos?.length ? ` · ${centro.avisos.join(", ")}` : ""}`
        : `Pendiente${centro?.problemas?.length ? ` · ${centro.problemas.join(", ")}` : ""}`;
      panel.querySelector(".panel__head").appendChild(estado);
      if (activa) {
        const verificar = document.createElement("button");
        verificar.type = "button";
        verificar.className = "btn btn--ghost";
        verificar.textContent = "Verificar en Meta";
        panel.querySelector(".panel__head").appendChild(verificar);
        verificar.addEventListener("click", async () => {
          verificar.disabled = true;
          verificar.textContent = "Verificando…";
          try {
            const res = await fetch(`api/configuracion/clinicas/${encodeURIComponent(codigoCentro)}/verificar-meta`, {
              headers: authHeaders(), cache: "no-store",
            });
            const datos = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(datos.detail || "No se pudo verificar la clínica.");
            toast(datos.mensaje || "Verificación completada.", datos.ok ? "ok" : "error");
          } catch (error) {
            toast(error.message || "No se pudo verificar la clínica.", "error");
          } finally {
            verificar.disabled = false;
            verificar.textContent = "Verificar en Meta";
          }
        });
      }
      const alternar = document.createElement("button");
      alternar.type = "button";
      alternar.className = "btn btn--ghost config-alternar-clinica";
      alternar.innerHTML = activa
        ? '<i class="fa-solid fa-pause" aria-hidden="true"></i> Desactivar'
        : '<i class="fa-solid fa-play" aria-hidden="true"></i> Activar';
      panel.querySelector(".panel__head").appendChild(alternar);
      alternar.addEventListener("click", async () => {
        if (Object.keys(diff()).length) {
          toast("Guarda o descarta los cambios pendientes antes de cambiar el estado de la clínica.", "error");
          return;
        }
        alternar.disabled = true;
        try {
          const res = await fetch(`api/configuracion/clinicas/${encodeURIComponent(codigoCentro)}/estado`, {
            method: "PUT", headers: authHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify({ activa: !activa }),
          });
          const datos = await res.json().catch(() => ({}));
          if (!res.ok) throw new Error(datos.detail || "No se pudo cambiar el estado de la clínica.");
          await cargar();
          toast(activa ? "Clínica desactivada. Sus datos se conservaron." : "Clínica activada.");
        } catch (error) {
          toast(error.message || "No se pudo cambiar el estado de la clínica.", "error");
        } finally {
          alternar.disabled = false;
        }
      });
      if (s.eliminable) {
        const eliminar = document.createElement("button");
        eliminar.type = "button";
        eliminar.className = "btn btn--ghost config-eliminar-clinica";
        eliminar.innerHTML = '<i class="fa-solid fa-trash-can" aria-hidden="true"></i> Eliminar clínica';
        panel.querySelector(".panel__head").appendChild(eliminar);
        const confirmar = document.createElement("div");
        confirmar.className = "config-confirmar-eliminacion";
        confirmar.hidden = true;
        confirmar.innerHTML = `<p>¿Eliminar ${escaparHtml(centro?.nombre || s.titulo)}? Dejará de estar disponible para envíos. ` +
          'Su historial se conservará y se quitarán sus datos de conexión en SNW. Esto no elimina el número en Meta.</p>' +
          '<div><button type="button" class="btn btn--ghost" data-cancelar>Volver</button>' +
          '<button type="button" class="btn btn--danger" data-confirmar>Eliminar clínica</button></div>';
        panel.querySelector(".panel__head").after(confirmar);
        eliminar.addEventListener("click", () => {
          if (Object.keys(diff()).length) {
            toast("Guarda o descarta los cambios pendientes antes de eliminar una clínica.", "error");
            return;
          }
          confirmar.hidden = !confirmar.hidden;
          if (!confirmar.hidden) confirmar.querySelector("[data-cancelar]").focus();
        });
        confirmar.querySelector("[data-cancelar]").addEventListener("click", () => {
          confirmar.hidden = true;
          eliminar.focus();
        });
        confirmar.querySelector("[data-confirmar]").addEventListener("click", async () => {
          const aceptar = confirmar.querySelector("[data-confirmar]");
          aceptar.disabled = true;
          try {
            const res = await fetch(`api/configuracion/clinicas/${encodeURIComponent(codigoCentro)}`, {
              method: "DELETE", headers: authHeaders(),
            });
            const datos = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(datos.detail || "No se pudo eliminar la clínica.");
            await cargar();
            toast("Clínica eliminada. Su historial se conserva.");
          } catch (error) {
            toast(error.message || "No se pudo eliminar la clínica.", "error");
          } finally {
            aceptar.disabled = false;
          }
        });
      }
    }
    const grid = panel.querySelector(".config-campos");
    if (s.id === "reagendar") {
      const accion = document.createElement("div");
      accion.className = "config-clinicas-acciones";
      const boton = document.createElement("button");
      boton.type = "button";
      boton.className = "btn btn--ghost";
      boton.textContent = "Generar / rotar token de integración";
      const estado = document.createElement("p");
      estado.className = "field__hint";
      estado.setAttribute("role", "status");
      accion.append(boton, estado);
      grid.before(accion);
      boton.addEventListener("click", async () => {
        if (!confirm("Se invalidará el token anterior del sistema externo. ¿Continuar?")) return;
        boton.disabled = true;
        try {
          const res = await fetch("api/v1/paciente-reagendar/token", {
            method: "POST", headers: authHeaders() });
          const data = await res.json();
          if (!res.ok) throw new Error(data.detail || "No se pudo generar el token.");
          try {
            await navigator.clipboard.writeText(data.token);
            estado.textContent = "Token nuevo copiado al portapapeles. Pégalo ahora en el sistema externo; no volverá a mostrarse.";
          } catch {
            const entrada = document.createElement("input");
            entrada.type = "text";
            entrada.readOnly = true;
            entrada.value = data.token;
            entrada.setAttribute("aria-label", "Nuevo token de integración");
            estado.replaceChildren("Copia este token ahora: ", entrada);
            entrada.select();
          }
        } catch (error) {
          estado.textContent = error.message;
        } finally {
          boton.disabled = false;
        }
      });
    }
    const pendientes = Number(estadoSupresion.pendientes || 0) +
      Number(estadoSupresion.procesando || 0);
    if (s.id === "whatsapp" && pendientes > 0) {
      const aviso = document.createElement("p");
      aviso.className = "aviso-dev config-supresion-aviso";
      aviso.textContent = `${pendientes} solicitud(es) de eliminación pendientes: ` +
        "el número ya está excluido localmente, pero falta confirmar su bloqueo en Meta. " +
        "Revisa las credenciales y los errores del servidor.";
      grid.before(aviso);
    }

    for (const c of s.campos) {
      // Campo calculado de solo lectura (ej. URL completa del webhook).
      if (c.tipo === "derivado") {
        const wrap = document.createElement("div");
        wrap.className = "field config-campo config-campo--derivado";
        const enlace = c.enlace
          ? `<p class="field__hint config-webhook-meta">Si esta URL cambió, actualízala también en Meta: ` +
            `<a href="${c.enlace.url}" target="_blank" rel="noopener">${escaparHtml(c.enlace.texto)} ` +
            `<i class="fa-solid fa-arrow-up-right-from-square"></i></a></p>`
          : "";
        wrap.innerHTML =
          `<label>${escaparHtml(c.etiqueta)}</label>` +
          `<div class="config-secreto">` +
            `<input type="text" class="config-derivado-input" readonly value="">` +
            `<button type="button" class="config-ojo config-copiar" title="Copiar">` +
            `<i class="fa-solid fa-copy"></i></button>` +
          `</div>` +
          (c.ayuda ? `<p class="field__hint">${escaparHtml(c.ayuda)}</p>` : "") +
          enlace;
        grid.appendChild(wrap);

        const salida = wrap.querySelector("input");
        const recomputar = () => {
          const [kBase, kRuta] = c.formula || [];
          salida.value = unirUrl(
            campos[kBase] ? valorActual(kBase) : "",
            c.ruta_fija || (campos[kRuta] ? valorActual(kRuta) : "")
          );
        };
        derivados.push(recomputar);

        wrap.querySelector(".config-copiar").addEventListener("click", async () => {
          const txt = salida.value;
          if (!txt) return toast("Todavía no hay URL (falta la URL base o la ruta).", "error");
          try {
            await navigator.clipboard.writeText(txt);
          } catch {
            console.error("[configuracion.js render()]");
            salida.removeAttribute("readonly");
            salida.select();
            document.execCommand("copy");
            salida.setAttribute("readonly", "");
          }
          toast("URL del webhook copiada.", "ok");
        });
        continue;
      }

      const val = valores[c.clave] ?? "";
      const wrap = document.createElement("div");
      wrap.className = "field config-campo" +
        (c.clave === "call_center_patrones_consulta" ? " config-campo--patrones" : "") +
        (CLAVES_REAGENDAR.includes(c.clave) ? " config-campo--reagendar" : "");

      const id = "cf_" + c.clave;
      let control = "";

      if (c.clave === "call_center_patrones_consulta") {
        control = htmlEditorPatrones(c.clave, val);
      } else if (CLAVES_REAGENDAR.includes(c.clave)) {
        control = htmlEditorReagendar(c.clave, val);
      } else if (c.tipo === "select") {
        control =
          `<select id="${id}" data-clave="${c.clave}">` +
          (c.opciones || []).map((o) => `<option value="${o}"${o === val ? " selected" : ""}>${o}</option>`).join("") +
          `</select>`;
      } else if (c.tipo === "bool") {
        const on = String(val).toLowerCase() === "true";
        control =
          `<label class="config-switch"><input type="checkbox" id="${id}" data-clave="${c.clave}"${on ? " checked" : ""}>` +
          `<span>Activado</span></label>`;
      } else if (c.tipo === "textarea") {
        control = `<textarea id="${id}" data-clave="${c.clave}" rows="4" autocomplete="off" spellcheck="false">${escaparHtml(val)}</textarea>`;
      } else {
        const t = c.tipo === "number" ? "number" : c.tipo === "password" ? "password" : "text";
        const placeholder = c.clave === "wa_token" && tokenGlobalConfigurado
          ? ' placeholder="Token guardado · escribe uno nuevo para reemplazarlo"' : "";
        control = `<input type="${t}" id="${id}" data-clave="${c.clave}" value="${escaparAtributo(val)}"${placeholder} autocomplete="off" spellcheck="false">`;
      }

      wrap.innerHTML =
        `<label${c.clave === "call_center_patrones_consulta" || CLAVES_REAGENDAR.includes(c.clave) ? "" : ` for="${id}"`}>${escaparHtml(c.etiqueta)} <span class="config-clave">${c.clave}</span></label>` +
        control +
        (c.ayuda ? `<p class="field__hint">${escaparHtml(c.ayuda)}</p>` : "");
      grid.appendChild(wrap);

      const input = wrap.querySelector("[data-clave]");
      campos[c.clave] = { def: c, input };
      input.addEventListener("input", marcarCambios);
      input.addEventListener("change", marcarCambios);
      if (c.clave === "call_center_patrones_consulta" && wrap.querySelector(".config-patrones")) {
        wrap.querySelectorAll("[data-patron]").forEach((textarea) =>
          textarea.addEventListener("input", () => actualizarEditorPatrones(wrap)));
        wrap.querySelector("[data-patrones-raw]").addEventListener("input", () => actualizarJsonPatrones(wrap));
        wrap.querySelector("[data-pegar-json]").addEventListener("change", () => cambiarModoPatrones(wrap));
        if (wrap.querySelector("[data-pegar-json]").checked) actualizarJsonPatrones(wrap);
        else actualizarEditorPatrones(wrap);
      }
      if (CLAVES_REAGENDAR.includes(c.clave)) prepararEditorReagendar(wrap, val);

    }
    paneles[tabDeSeccion(s.id)].appendChild(panel);
  }
  const seccionesRespuestas = [...paneles.respuestas.querySelectorAll("[data-respuesta-centro]")];
  if (seccionesRespuestas.length) {
    const selectorWrap = document.createElement("div");
    selectorWrap.className = "field config-respuestas-selector";
    selectorWrap.innerHTML = '<label for="configCentroRespuesta">Clínica</label><select id="configCentroRespuesta"></select>' +
      '<p class="field__hint">Cada clínica tiene sus propios textos. Los cambios se aplican a las próximas respuestas automáticas.</p>';
    const selector = selectorWrap.querySelector("select");
    seccionesRespuestas.forEach((seccion) => selector.add(new Option(
      seccion.querySelector(".panel__head h2")?.textContent.trim() || seccion.dataset.respuestaCentro,
      seccion.dataset.respuestaCentro)));
    const actualizarCentro = () => seccionesRespuestas.forEach((seccion) => {
      seccion.hidden = seccion.dataset.respuestaCentro !== selector.value;
    });
    selector.addEventListener("change", actualizarCentro);
    paneles.respuestas.prepend(selectorWrap);
    actualizarCentro();
  }
  if (!paneles.clinicas.querySelector(".config-seccion")) {
    const vacio = document.createElement("div");
    vacio.className = "config-clinicas-vacio";
    vacio.innerHTML = '<p>No hay clínicas configuradas. Usa «Añadir clínica» para crear una.</p>';
    paneles.clinicas.appendChild(vacio);
  }
  paneles.logs.innerHTML = `
    <section class="panel config-seccion config-logs">
        <div class="panel__head"><h2><i class="fa-solid fa-file-lines" aria-hidden="true"></i> Registros del servidor</h2></div>
        <p class="config-logs__ayuda">Selecciona un registro de errores o de una carga CSV remota para verlo y copiarlo.</p>
      <div class="config-logs__controles">
        <label for="configLogArchivo">Archivo</label>
        <select id="configLogArchivo" disabled><option value="">Cargando registros…</option></select>
        <button class="btn btn--ghost" type="button" id="configLogActualizar"><i class="fa-solid fa-rotate" aria-hidden="true"></i> Actualizar</button>
        <button class="btn btn--ghost" type="button" id="configLogCopiar" disabled><i class="fa-regular fa-copy" aria-hidden="true"></i> Copiar contenido</button>
      </div>
      <p class="config-logs__estado" id="configLogEstado" role="status" aria-live="polite"></p>
      <pre class="config-logs__contenido" id="configLogContenido" tabindex="0">Selecciona un archivo para ver su contenido.</pre>
    </section>`;
  $("#configLogArchivo").addEventListener("change", (evento) => cargarLog(evento.target.value));
  $("#configLogActualizar").addEventListener("click", cargarLogs);
  $("#configLogCopiar").addEventListener("click", copiarLog);
  mostrarTabConfig();
  if (tabConfig === "logs") cargarLogs();
  marcarCambios();
}

async function cargarLogs() {
  const selector = $("#configLogArchivo");
  if (!selector) return;
  selector.disabled = true;
  $("#configLogEstado").textContent = "Cargando registros…";
  try {
    const respuesta = await fetch("api/configuracion/logs", {
      headers: authHeaders(), cache: "no-store",
    });
    if (respuesta.status === 401) { window.snwSesionExpirada(); return; }
    if (!respuesta.ok) throw new Error("No se pudieron cargar los registros.");
    const datos = await respuesta.json();
    const archivos = datos.archivos || [];
    selector.replaceChildren();
    if (!archivos.length) {
        selector.add(new Option("Aún no hay registros", ""));
      logSeleccionado = "";
        $("#configLogContenido").textContent = "Todavía no se han registrado eventos.";
      $("#configLogCopiar").disabled = true;
      $("#configLogEstado").textContent = "";
      return;
    }
    archivos.forEach(({ nombre, bytes }) => selector.add(
      new Option(`${nombre} · ${(bytes / 1024).toFixed(1)} KB`, nombre)));
    selector.disabled = false;
    const elegido = archivos.some(({ nombre }) => nombre === logSeleccionado)
      ? logSeleccionado : archivos[0].nombre;
    selector.value = elegido;
    await cargarLog(elegido);
  } catch (error) {
    $("#configLogEstado").textContent = error.message;
    $("#configLogContenido").textContent = "";
    $("#configLogCopiar").disabled = true;
  }
}

async function cargarLog(nombre) {
  const secuencia = ++peticionLog;
  logSeleccionado = nombre;
  $("#configLogCopiar").disabled = true;
  $("#configLogContenido").textContent = "Cargando contenido…";
  try {
    const respuesta = await fetch(`api/configuracion/logs/${encodeURIComponent(nombre)}`, {
      headers: authHeaders(), cache: "no-store",
    });
    if (respuesta.status === 401) { window.snwSesionExpirada(); return; }
    if (!respuesta.ok) throw new Error("No se pudo leer el archivo seleccionado.");
    const datos = await respuesta.json();
    if (secuencia !== peticionLog) return;
    $("#configLogContenido").textContent = datos.contenido || "El archivo está vacío.";
    $("#configLogEstado").textContent = datos.truncado
      ? "Archivo grande: se muestran y copian solo los últimos 5 MB." : "";
    $("#configLogCopiar").disabled = !datos.contenido;
  } catch (error) {
    if (secuencia !== peticionLog) return;
    $("#configLogEstado").textContent = error.message;
    $("#configLogContenido").textContent = "";
  }
}

async function copiarLog() {
  const contenido = $("#configLogContenido").textContent;
  if (!contenido) return;
  try {
    await navigator.clipboard.writeText(contenido);
    toast("Contenido del registro copiado.");
  } catch {
    toast("No se pudo copiar. Selecciona el texto y cópialo manualmente.", "error");
  }
}

function valorActual(clave) {
  const { input } = campos[clave];
  if (input.type === "checkbox") return input.checked ? "true" : "false";
  return input.value;
}

function diff() {
  const cambios = {};
  for (const clave of Object.keys(campos)) {
    const nuevo = String(valorActual(clave));
    const viejo = String(valores[clave] ?? "");
    if (clave === "call_center_patrones_consulta" ? !patronesEquivalentes(nuevo, viejo)
        : CLAVES_REAGENDAR.includes(clave) ? !mapasEquivalentes(nuevo, viejo)
        : nuevo.trim() !== viejo.trim()) {
      cambios[clave] = nuevo.trim();
    }
  }
  return cambios;
}

function marcarCambios() {
  derivados.forEach((fn) => fn());
  const cambios = diff();
  const n = Object.keys(cambios).length;

  for (const clave of Object.keys(campos)) {
    campos[clave].input.closest(".config-campo").classList.toggle("config-campo--cambiado", clave in cambios);
  }

  const hay = n > 0;
  const invalido = !!cont.querySelector(".config-patrones--invalido, .config-reagendar--invalido");
  $("#btnGuardar").disabled = !hay || invalido;
  $("#btnGuardarFijo").disabled = !hay || invalido;
  $("#barraGuardar").hidden = !hay;
  $("#cambiosResumen").textContent = hay
    ? `${n} cambio${n === 1 ? "" : "s"} sin guardar${invalido ? " · Corrige los campos marcados en Integraciones" : ""}: ${Object.keys(cambios).join(", ")}`
    : "Sin cambios";
}

async function guardar() {
  if (cont.querySelector(".config-patrones--invalido, .config-reagendar--invalido")) {
    toast("Corrige los campos marcados antes de guardar.", "error");
    return;
  }
  const cambios = diff();
  if (!Object.keys(cambios).length) return;

  const botones = [$("#btnGuardar"), $("#btnGuardarFijo")];
  botones.forEach((b) => (b.disabled = true));
  try {
    const res = await fetch("api/configuracion/todo", {
      method: "PUT",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ cambios }),
    });
    const d = await res.json().catch(() => ({}));
    if (res.status === 401) { window.snwSesionExpirada(); return; }
    if (!res.ok) throw new Error(d.detail || "No se pudo guardar");
    valores = d.valores || valores;
    centrosConfig = d.centros || centrosConfig;
    tokenGlobalConfigurado = !!d.wa_token_configurado;
    if (campos.wa_token) campos.wa_token.input.placeholder = tokenGlobalConfigurado
      ? "Token guardado · escribe uno nuevo para reemplazarlo" : "";
    for (const panel of cont.querySelectorAll(".config-seccion")) {
      const codigo = panel.dataset.centro;
      if (!codigo) continue;
      const centro = centrosConfig.find((c) => c.codigo === codigo);
      const estado = panel.querySelector(".config-centro-estado");
      if (estado) {
        const activa = panel.dataset.activa !== "false";
        estado.className = `config-centro-estado ${!activa ? "config-centro-estado--inactiva" : centro?.configured ? "config-centro-estado--ok" : "config-centro-estado--pendiente"}`;
        estado.textContent = !activa ? "Desactivada" : centro?.configured
          ? `Configurado${centro?.avisos?.length ? ` · ${centro.avisos.join(", ")}` : ""}`
          : `Pendiente${centro?.problemas?.length ? ` · ${centro.problemas.join(", ")}` : ""}`;
      }
    }

    // Reflejar los valores normalizados por el servidor.
    for (const clave of Object.keys(campos)) {
      const { input } = campos[clave];
      const v = valores[clave] ?? "";
      if (input.type === "checkbox") input.checked = String(v).toLowerCase() === "true";
      else input.value = v;
      if (clave === "call_center_patrones_consulta" && input.closest(".config-patrones")) {
        restaurarPatrones(input.closest(".config-campo"), v);
      }
      if (CLAVES_REAGENDAR.includes(clave)) restaurarMapaReagendar(input.closest(".config-campo"), v);
    }
    marcarCambios();
    toast(
      "entorno" in cambios
        ? "Configuración guardada. Recarga las demás páginas para aplicar el cambio de entorno."
        : "Configuración guardada.",
      "ok"
    );
  } catch (err) {
    console.error("[configuracion.js guardar()]", err);
    toast(err.message || "No se pudo guardar la configuración.", "error");
  } finally {
    botones.forEach((b) => (b.disabled = false));
    marcarCambios();
  }
}

function descartar() {
  for (const clave of Object.keys(campos)) {
    const { input } = campos[clave];
    const v = valores[clave] ?? "";
    if (input.type === "checkbox") input.checked = String(v).toLowerCase() === "true";
    else input.value = v;
    if (clave === "call_center_patrones_consulta" && input.closest(".config-patrones")) {
      restaurarPatrones(input.closest(".config-campo"), v);
    }
    if (CLAVES_REAGENDAR.includes(clave)) restaurarMapaReagendar(input.closest(".config-campo"), v);
  }
  marcarCambios();
}

$("#btnGuardar").addEventListener("click", guardar);
$("#btnGuardarFijo").addEventListener("click", guardar);
$("#btnDescartar").addEventListener("click", descartar);

window.addEventListener("beforeunload", (e) => {
  if (Object.keys(diff()).length) { e.preventDefault(); e.returnValue = ""; }
});

let yaCargada = false;
window.snwCargarConfiguracion = function () {
  if (yaCargada) return;
  yaCargada = true;
  mostrarTabConfig();
  cargar();
};
})();
