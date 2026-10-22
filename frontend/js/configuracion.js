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
const idsTabs = ["general", "whatsapp", "clinicas", "correo", "integraciones"];
let tabConfig = "general";
try {
  const guardada = localStorage.getItem("snw_tab_config");
  if (idsTabs.includes(guardada)) tabConfig = guardada;
} catch { /* sin almacenamiento */ }
let centrosConfig = [];
let tokenGlobalConfigurado = false;
let valores = {};          // valor guardado (servidor)
let estadoSupresion = {};  // solo conteos; nunca teléfonos ni huellas
let campos = {};           // clave -> { def, input }
let derivados = [];        // campos calculados (ej. URL del webhook)

function tabDeSeccion(id) {
  if (id.startsWith("centro_")) return "clinicas";
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
}

function cambiarTabConfig(destino, { enfocar = false } = {}) {
  if (!idsTabs.includes(destino) || destino === tabConfig) return;
  const anterior = tabConfig;
  tabConfig = destino;
  try { localStorage.setItem("snw_tab_config", destino); } catch { /* sin almacenamiento */ }
  mostrarTabConfig(anterior);
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
        (c.clave === "call_center_patrones_consulta" ? " config-campo--patrones" : "");

      const id = "cf_" + c.clave;
      let control = "";

      if (c.clave === "call_center_patrones_consulta") {
        control = htmlEditorPatrones(c.clave, val);
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
        `<label${c.clave === "call_center_patrones_consulta" ? "" : ` for="${id}"`}>${escaparHtml(c.etiqueta)} <span class="config-clave">${c.clave}</span></label>` +
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

    }
    paneles[tabDeSeccion(s.id)].appendChild(panel);
  }
  if (!paneles.clinicas.querySelector(".config-seccion")) {
    const vacio = document.createElement("div");
    vacio.className = "config-clinicas-vacio";
    vacio.innerHTML = '<p>No hay clínicas configuradas. Usa «Añadir clínica» para crear una.</p>';
    paneles.clinicas.appendChild(vacio);
  }
  mostrarTabConfig();
  marcarCambios();
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
    if (clave === "call_center_patrones_consulta" ?
        !patronesEquivalentes(nuevo, viejo) : nuevo.trim() !== viejo.trim()) {
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
  const invalido = !!cont.querySelector(".config-patrones--invalido");
  $("#btnGuardar").disabled = !hay || invalido;
  $("#btnGuardarFijo").disabled = !hay || invalido;
  $("#barraGuardar").hidden = !hay;
  $("#cambiosResumen").textContent = hay
    ? `${n} cambio${n === 1 ? "" : "s"} sin guardar${invalido ? " · Corrige las frases marcadas en Integraciones" : ""}: ${Object.keys(cambios).join(", ")}`
    : "Sin cambios";
}

async function guardar() {
  if (cont.querySelector(".config-patrones--invalido")) {
    toast("Corrige las frases marcadas antes de guardar.", "error");
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
