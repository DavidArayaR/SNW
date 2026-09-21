(function () {
const API_PACIENTES = "api/pacientes";

function authHeaders(extra = {}) {
  return { Authorization: "Bearer " + (localStorage.getItem("snw_token") || ""), ...extra };
}

let ambienteAdmin = localStorage.getItem("snw_ambiente_admin");
let _ambienteInicializado = false;

let pacientes = [];
let config = null;
let filtro = "";
let filtroEstado = "todos";
let filtroRespuesta = "todas";
let filtroInteres = "todos";
let seleccionados = new Set();
let todoMarcado = false;

const $ = (sel) => document.querySelector(sel);

const tbodyEl = $("#tablaPacientes tbody");
const vacioEl = $("#tablaVacia");
const buscadorEl = $("#buscador");
const statsEl = $("#stats");
const chkTodos = $("#chkTodos");
const contadorSel = $("#contadorSel");
const toastEl = $("#toast");
const selAmbiente = $("#selAmbiente");
const toolbarMasivo = $("#toolbarMasivo");
const selEstadoMasivo = $("#selEstadoMasivo");
const selRespuestaMasivo = $("#selRespuestaMasivo");

if (ambienteAdmin) selAmbiente.value = ambienteAdmin;
selAmbiente.addEventListener("change", async () => {
  ambienteAdmin = selAmbiente.value;
  localStorage.setItem("snw_ambiente_admin", ambienteAdmin);
  cargar();
});

function ambienteActual() {
  return selAmbiente.value;
}

function escaparHtml(texto) {
  const div = document.createElement("div");
  div.textContent = texto ?? "";
  return div.innerHTML;
}

async function cargar() {
  try {
    // Si no hay preferencia guardada, usar el entorno global de la configuración como default
    if (!_ambienteInicializado && !localStorage.getItem("snw_ambiente_admin")) {
      try {
        const r0 = await fetch("api/configuracion", { headers: authHeaders(), cache: "no-store" });
        if (r0.ok) {
          const c0 = await r0.json();
          ambienteAdmin = c0.entorno;
          selAmbiente.value = ambienteAdmin;
          localStorage.setItem("snw_ambiente_admin", ambienteAdmin);
        }
      } catch {}
      _ambienteInicializado = true;
    }
    const amb = ambienteActual() || ambienteAdmin || "desarrollo";
    const [rp, rc] = await Promise.all([
      fetch(`${API_PACIENTES}?ambiente=${amb}`, { headers: authHeaders(), cache: "no-store" }),
      fetch(`api/configuracion?ambiente=${amb}`, { headers: authHeaders(), cache: "no-store" }),
    ]);
    if (rp.status === 401 || rc.status === 401) { window.snwSesionExpirada(); return; }
    if (rp.status === 403) { location.href = "mensajeria.html"; return; }

    pacientes = (await rp.json()).map((p) => ({
      ...p,
      estado: p.estado || "pendiente",
    }));
    config = await rc.json();

    const tituloEl = $("#tituloPacientes");
    if (tituloEl) {
      const entornoLabel = config.entorno === "produccion" ? "producción" : "desarrollo";
      tituloEl.textContent = `Pacientes ${entornoLabel}`;
    }

    render();
  } catch {
    toast("Error al conectar con el servidor.", "error");
  }
}

function render() {
  const q = filtro.trim().toLowerCase();
  const resp = (p) => p.respuesta || "pendiente";
  const visibles = pacientes.filter(
    (p) =>
      (filtroEstado === "todos" || p.estado === filtroEstado) &&
      (filtroRespuesta === "todas" || resp(p) === filtroRespuesta) &&
      (filtroInteres === "todos" || !!p.no_interesado) &&
      (!q ||
        [p.nombre, p.apellido, p.telefono]
          .filter(Boolean)
          .some((v) => v.toLowerCase().includes(q)))
  );

  tbodyEl.innerHTML = "";

  for (const p of visibles) {
    const nombreCompleto = [p.nombre, p.apellido].filter(Boolean).join(" ");
    const tr = document.createElement("tr");
    const respuesta = p.respuesta || "pendiente";
    const esBaja = respuesta === "baja" || !!p.whatsapp_opt_out;
    // Baja pedida con sus propias palabras por WhatsApp: no se puede editar a
    // mano, solo se libera si el paciente se retracta (webhook).
    const bajaBloqueada = respuesta === "baja" && !!p.whatsapp_opt_out && !!p.opt_out_explicito;
    const respuestaLabels = { pendiente: "Sin respuesta", respondio: "Respondió", baja: "Se dio de baja" };
    tr.classList.toggle("es-baja", esBaja);
    const hayError = p.estado === "error" || p.ultimo_estado_envio === "error";
    const textoError = hayError && p.ultimo_error ? String(p.ultimo_error) : "";
    const celdaError = textoError
      ? `<td class="campo-error"><span title="${escaparHtml(textoError)}">${escaparHtml(textoError)}</span></td>`
      : `<td class="campo-error campo-error--vacio">—</td>`;

    // Tooltip de la respuesta: cuándo escribió y qué dijo (lo trae el webhook).
    let tituloResp = respuestaLabels[respuesta] ?? respuesta;
    if (p.ultima_respuesta_fecha) {
      tituloResp += ` · ${p.ultima_respuesta_fecha}`;
      if (p.ultimo_mensaje_recibido) tituloResp += `\n«${p.ultimo_mensaje_recibido}»`;
    }
    if (bajaBloqueada) {
      tituloResp = "El paciente pidió la baja por WhatsApp: no se puede editar a mano. " +
        "Se libera solo si el paciente escribe de nuevo mostrando interés.";
    }
    const subResp = p.ultima_respuesta_fecha
      ? `<span class="respuesta-fecha">${escaparHtml(p.ultima_respuesta_fecha)}</span>`
      : "";
    tr.innerHTML =
      `<td class="col-check"><input type="checkbox" data-id="${p.id}" ${seleccionados.has(p.id) ? "checked" : ""}></td>` +
      `<td class="campo-id">${p.id}</td>` +
      `<td class="campo-nombre">${escaparHtml(nombreCompleto)}</td>` +
      `<td class="campo-tel">${escaparHtml(p.telefono)}</td>` +
      `<td class="campo-estado">` +
        `<span class="estado-badge estado-${escaparHtml(p.estado)}" data-editable data-id="${p.id}" title="Click para cambiar estado">${escaparHtml(p.estado)}</span>` +
        `<select class="estado-select" data-id="${p.id}" hidden>` +
          `<option value="pendiente"${p.estado === "pendiente" ? " selected" : ""}>pendiente</option>` +
          `<option value="enviado"${p.estado === "enviado" ? " selected" : ""}>enviado</option>` +
        `</select>` +
      `</td>` +
      celdaError +
      `<td class="campo-respuesta">` +
        `<span class="respuesta-badge respuesta-${escaparHtml(respuesta)}${bajaBloqueada ? " respuesta-badge--bloqueada" : ""}"` +
          `${bajaBloqueada ? "" : " data-editable"} data-id="${p.id}"` +
          ` title="${escaparHtml(bajaBloqueada ? tituloResp : tituloResp + " · click para cambiar")}">` +
          `${bajaBloqueada ? '<i class="fa-solid fa-lock"></i> ' : ""}${escaparHtml(respuestaLabels[respuesta] ?? respuesta)}</span>` +
        `<select class="respuesta-select" data-id="${p.id}" hidden>` +
          // "Respondió" no es una opción manual: solo la pone el propio
          // paciente al contestar por WhatsApp, nunca un ajuste manual acá.
          ["pendiente", "baja"].map((v) =>
            `<option value="${v}"${v === respuesta ? " selected" : ""}>${respuestaLabels[v]}</option>`).join("") +
          // Si el valor actual ES "respondio" (vino de una respuesta real),
          // se agrega como opción extra ya seleccionada, así el <select> no
          // pierde silenciosamente el valor real al abrirlo.
          (respuesta === "respondio"
            ? `<option value="respondio" selected>${respuestaLabels.respondio}</option>`
            : "") +
        `</select>` +
        subResp +
      `</td>` +
      `<td class="campo-fecha">${escaparHtml(p.actualizado)}</td>`;
    const chk = tr.querySelector('input[type="checkbox"]');
    chk.addEventListener("change", (e) => {
      e.target.checked ? seleccionados.add(p.id) : seleccionados.delete(p.id);
      if (!e.target.checked) todoMarcado = false;
      refrescarSeleccion();
    });
    tbodyEl.appendChild(tr);
  }

  vacioEl.hidden = visibles.length > 0;

  const conteo = { total: pacientes.length, pendiente: 0, enviado: 0, error: 0 };
  for (const p of pacientes) {
    if (conteo[p.estado] !== undefined) conteo[p.estado]++;
  }

  const conteoResp = { todas: pacientes.length, pendiente: 0, respondio: 0, baja: 0 };
  for (const p of pacientes) {
    const r = p.respuesta || "pendiente";
    if (conteoResp[r] !== undefined) conteoResp[r]++;
  }
  const conteoNoInteresados = pacientes.filter((p) => !!p.no_interesado).length;

  const esActivoEstado = (estado) => filtroRespuesta === "todas" && filtroInteres === "todos" && filtroEstado === estado;
  const esActivoResp = (resp) => filtroEstado === "todos" && filtroInteres === "todos" && filtroRespuesta === resp;
  const esActivoInteres = filtroEstado === "todos" && filtroRespuesta === "todas" && filtroInteres === "no_interesado";
  // Tarjeta con ícono en chip de color (mismo tono que usaban los puntitos
  // de antes), en vez de la píldora chica: mismo botón/atributos de
  // filtro, solo cambia cómo se ve.
  const statCard = (tono, icono, activo, attrs, etiqueta, numero) =>
    `<button type="button" class="stat stat-card stat-card--${tono}${activo ? " activo" : ""}" ${attrs}>` +
      `<span class="stat-card__icono"><i class="fa-solid ${icono}"></i></span>` +
      `<span class="stat-card__texto"><strong>${numero}</strong><span>${etiqueta}</span></span>` +
    `</button>`;
  statsEl.innerHTML =
    statCard("neutral", "fa-users", esActivoEstado("todos") || (filtroEstado === "todos" && filtroRespuesta === "todas"), 'data-estado="todos"', "Total", conteo.total) +
    statCard("warn", "fa-clock", esActivoEstado("pendiente"), 'data-estado="pendiente"', "Pendientes", conteo.pendiente) +
    statCard("ok", "fa-paper-plane", esActivoEstado("enviado"), 'data-estado="enviado"', "Enviados", conteo.enviado) +
    statCard("danger", "fa-triangle-exclamation", esActivoEstado("error"), 'data-estado="error"', "Errores", conteo.error) +
    statCard("neutral", "fa-comment-slash", esActivoResp("pendiente"), 'data-respuesta="pendiente"', "Sin resp.", conteoResp.pendiente) +
    statCard("ok", "fa-comments", esActivoResp("respondio"), 'data-respuesta="respondio"', "Respondió", conteoResp.respondio) +
    statCard("danger", "fa-user-slash", esActivoResp("baja"), 'data-respuesta="baja"', "Baja", conteoResp.baja) +
    statCard("info", "fa-thumbs-down", esActivoInteres, 'data-interes="no_interesado"', "No le interesa", conteoNoInteresados);

  refrescarSeleccion(visibles);
}

function refrescarSeleccion(visibles = null) {
  visibles = visibles ?? pacientes.filter(
    (p) =>
      (filtroEstado === "todos" || p.estado === filtroEstado) &&
      (filtroRespuesta === "todas" || (p.respuesta || "pendiente") === filtroRespuesta) &&
      (filtroInteres === "todos" || !!p.no_interesado)
  );

  contadorSel.textContent =
    seleccionados.size > 0 ? `${seleccionados.size} seleccionado${seleccionados.size === 1 ? "" : "s"}` : "";

  if (toolbarMasivo) toolbarMasivo.hidden = seleccionados.size === 0;

  chkTodos.checked =
    visibles.length > 0 && visibles.every((p) => seleccionados.has(p.id));
}

// Devuelve el Set de ids de los pacientes visibles con el filtro actual.
function idsSeleccionables() {
  const q = filtro.trim().toLowerCase();
  return new Set(
    pacientes
      .filter(
        (p) =>
          (filtroEstado === "todos" || p.estado === filtroEstado) &&
          (filtroRespuesta === "todas" || (p.respuesta || "pendiente") === filtroRespuesta) &&
          (filtroInteres === "todos" || !!p.no_interesado) &&
          (!q ||
            [p.nombre, p.apellido, p.telefono]
              .filter(Boolean)
              .some((v) => v.toLowerCase().includes(q)))
      )
      .map((p) => p.id)
  );
}

// Mantiene la selección coherente con el filtro actual:
// - Si el check "seleccionar todos" está marcado (todoMarcado), la selección
//   se re-sincroniza para contener exactamente los pacientes del filtro actual.
function sincronizarSeleccionConFiltro() {
  const seleccionables = idsSeleccionables();

  if (todoMarcado) {
    // Re-seleccionar exactamente los del filtro actual.
    seleccionados = new Set([...seleccionables]);
  } else {
    // Conservar solo los seleccionados que aún cumplen el filtro.
    for (const id of [...seleccionados]) {
      if (!seleccionables.has(id)) seleccionados.delete(id);
    }
  }
}

chkTodos.addEventListener("change", (e) => {
  todoMarcado = e.target.checked;
  if (todoMarcado) {
    // Marcar todos los del filtro actual.
    sincronizarSeleccionConFiltro();
  } else {
    // Desmarcar todos los del filtro actual.
    for (const id of idsSeleccionables()) seleccionados.delete(id);
  }
  render();
});

tbodyEl.addEventListener("click", (e) => {
  if (e.target.closest(".estado-badge, .estado-select")) return;
  if (e.target.closest(".respuesta-badge, .respuesta-select")) return;
  if (e.target.tagName === "INPUT") return;
  const tr = e.target.closest("tr");
  if (!tr || !tbodyEl.contains(tr)) return;
  const chk = tr.querySelector("input[type=checkbox]");
  if (!chk) return;
  chk.checked = !chk.checked;
  chk.checked ? seleccionados.add(Number(chk.dataset.id)) : seleccionados.delete(Number(chk.dataset.id));
  if (!chk.checked) todoMarcado = false;
  refrescarSeleccion();
});

// Edición inline del estado y de la respuesta (badge -> select)
tbodyEl.addEventListener("click", (e) => {
  const badge = e.target.closest(".estado-badge[data-editable], .respuesta-badge[data-editable]");
  if (!badge) return;
  e.stopPropagation();
  const clase = badge.classList.contains("respuesta-badge") ? "respuesta-select" : "estado-select";
  const sel = badge.parentElement.querySelector(`.${clase}[data-id="${badge.dataset.id}"]`);
  if (!sel) return;
  badge.hidden = true;
  sel.hidden = false;
  sel.focus();
});

tbodyEl.addEventListener("change", async (e) => {
  const sel = e.target;
  if (!sel.classList.contains("respuesta-select")) return;
  e.stopPropagation();
  const id = Number(sel.dataset.id);
  const nueva = sel.value;
  const paciente = pacientes.find((p) => p.id === id);
  const anterior = paciente ? paciente.respuesta : null;
  if (paciente && paciente.respuesta === nueva) {
    const badge = tbodyEl.querySelector(`.respuesta-badge[data-id="${id}"]`);
    if (badge) badge.hidden = false;
    sel.hidden = true;
    return;
  }
  sel.disabled = true;
  try {
    const amb = ambienteActual();
    const res = await fetch(`api/pacientes/${id}/respuesta?ambiente=${encodeURIComponent(amb)}`, {
      method: "PUT",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ respuesta: nueva }),
    });
    if (res.status === 401) { window.snwSesionExpirada(); return; }
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || "No se pudo actualizar la respuesta");
    }
    const actualizado = await res.json();
    if (paciente) Object.assign(paciente, actualizado);
    toast(nueva === "baja" ? "Paciente marcado como dado de baja." : `Respuesta actualizada a "${nueva}".`, "ok");
    render();
  } catch (err) {
    toast(err.message || "No se pudo actualizar la respuesta", "error");
    if (paciente) sel.value = anterior || "pendiente";
    const badge = tbodyEl.querySelector(`.respuesta-badge[data-id="${id}"]`);
    if (badge) badge.hidden = false;
    sel.hidden = true;
    sel.disabled = false;
  }
});

tbodyEl.addEventListener("change", async (e) => {
  const sel = e.target;
  if (!sel.classList.contains("estado-select")) return;
  e.stopPropagation();
  const id = Number(sel.dataset.id);
  const nuevoEstado = sel.value;
  const paciente = pacientes.find((p) => p.id === id);
  const estadoAnterior = paciente ? paciente.estado : null;
  if (paciente && paciente.estado === nuevoEstado) {
    const badge = tbodyEl.querySelector(`.estado-badge[data-id="${id}"]`);
    if (badge) badge.hidden = false;
    sel.hidden = true;
    return;
  }
  sel.disabled = true;
  try {
    const amb = ambienteActual();
    const res = await fetch(`api/pacientes/${id}?ambiente=${encodeURIComponent(amb)}`, {
      method: "PUT",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ estado: nuevoEstado }),
    });
    if (res.status === 401) { window.snwSesionExpirada(); return; }
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || "No se pudo actualizar el estado");
    }
    const actualizado = await res.json();
    if (paciente) {
      paciente.estado = actualizado.estado;
      paciente.actualizado = actualizado.actualizado;
    }
    toast(`Estado actualizado a "${nuevoEstado}"`, "ok");
    render();
  } catch (err) {
    toast(err.message || "No se pudo actualizar el estado", "error");
    if (paciente) sel.value = estadoAnterior || "pendiente";
    const badge = tbodyEl.querySelector(`.estado-badge[data-id="${id}"]`);
    if (badge) badge.hidden = false;
    sel.hidden = true;
    sel.disabled = false;
  }
});

function cerrarSelectInline(sel) {
  const claseBadge = sel.classList.contains("respuesta-select") ? "respuesta-badge" : "estado-badge";
  const badge = sel.parentElement.querySelector(`.${claseBadge}[data-id="${sel.dataset.id}"]`);
  if (badge) badge.hidden = false;
  sel.hidden = true;
}

tbodyEl.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && (e.target.classList.contains("estado-select") || e.target.classList.contains("respuesta-select"))) {
    cerrarSelectInline(e.target);
    e.target.blur();
  }
});

document.addEventListener("click", (e) => {
  if (e.target.closest(".estado-badge, .estado-select, .respuesta-badge, .respuesta-select")) return;
  tbodyEl.querySelectorAll(".estado-select:not([hidden]), .respuesta-select:not([hidden])").forEach(cerrarSelectInline);
});

buscadorEl.addEventListener("input", () => {
  filtro = buscadorEl.value;
  sincronizarSeleccionConFiltro();
  render();
});

statsEl.addEventListener("click", (e) => {
  const btn = e.target.closest(".stat");
  if (!btn) return;
  if (btn.dataset.estado) {
    filtroEstado = btn.dataset.estado;
    filtroRespuesta = "todas";
    filtroInteres = "todos";
  } else if (btn.dataset.respuesta) {
    filtroRespuesta = btn.dataset.respuesta;
    filtroEstado = "todos";
    filtroInteres = "todos";
  } else if (btn.dataset.interes) {
    filtroInteres = btn.dataset.interes;
    filtroEstado = "todos";
    filtroRespuesta = "todas";
  }
  sincronizarSeleccionConFiltro();
  render();
});

$("#btnActualizar").addEventListener("click", cargar);

// --- Edición masiva: cambiar estado o respuesta de todos los seleccionados --
const ESTADO_LABEL = { pendiente: "pendiente", enviado: "enviado", error: "error" };
const RESPUESTA_LABEL = { pendiente: "sin respuesta", respondio: "respondió", baja: "dado de baja" };

async function aplicarMasivo(sel, url, campo, etiquetas) {
  const valor = sel.value;
  if (!valor) return;
  const ids = [...seleccionados];
  const cantidad = ids.length;
  const pregunta = campo === "estado"
    ? `¿Cambiar el estado de ${cantidad} paciente${cantidad === 1 ? "" : "s"} a "${etiquetas[valor]}"?`
    : `¿Cambiar la respuesta de ${cantidad} paciente${cantidad === 1 ? "" : "s"} a "${etiquetas[valor]}"?`;
  if (!confirm(pregunta)) {
    sel.value = "";
    return;
  }
  sel.disabled = true;
  try {
    const amb = ambienteActual();
    const res = await fetch(`${url}?ambiente=${encodeURIComponent(amb)}`, {
      method: "PUT",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ pacientes: ids, [campo]: valor }),
    });
    if (res.status === 401) { window.snwSesionExpirada(); return; }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "No se pudo aplicar el cambio");
    let msg = `${data.actualizados} paciente${data.actualizados === 1 ? "" : "s"} actualizado${data.actualizados === 1 ? "" : "s"}.`;
    if (data.bloqueados) {
      msg += ` ${data.bloqueados} no se pudo${data.bloqueados === 1 ? "" : "n"} cambiar (pidieron la baja por WhatsApp).`;
    }
    toast(msg, data.bloqueados ? "error" : "ok");
    await cargar();
  } catch (err) {
    toast(err.message || "No se pudo aplicar el cambio", "error");
  } finally {
    sel.value = "";
    sel.disabled = false;
  }
}

if (selEstadoMasivo) {
  selEstadoMasivo.addEventListener("change", () =>
    aplicarMasivo(selEstadoMasivo, "api/pacientes/estado-masivo", "estado", ESTADO_LABEL));
}
if (selRespuestaMasivo) {
  selRespuestaMasivo.addEventListener("change", () =>
    aplicarMasivo(selRespuestaMasivo, "api/pacientes/respuesta-masiva", "respuesta", RESPUESTA_LABEL));
}

let toastTimer;
function toast(msg, tipo = "ok") {
  clearTimeout(toastTimer);
  toastEl.textContent = msg;
  toastEl.className = `toast visible toast--${tipo}`;
  toastTimer = setTimeout(() => toastEl.classList.remove("visible"), 3200);
}

let yaCargada = false;
window.snwCargarPacientes = function () {
  if (yaCargada) return;
  yaCargada = true;
  cargar();
};
})();
