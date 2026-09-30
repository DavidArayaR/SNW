(function () {
const API_PACIENTES = "api/pacientes";

function authHeaders(extra = {}) {
  return { Authorization: "Bearer " + (localStorage.getItem("snw_token") || ""), ...extra };
}

let ambienteAdmin = localStorage.getItem("snw_ambiente_admin");
let _ambienteInicializado = false;

let pacientes = [];
let config = null;
let areas = [];
let filtro = "";
let filtroEstado = "todos";
let filtroRespuesta = "todas";
let filtroInteres = "todos";
let seleccionados = new Set();
let todoMarcado = false;

// Paginación de la tabla: 10 a 100 pacientes por página.
let paginaPac = 1;
let pageSizePac = Math.min(100, Math.max(10, Number(localStorage.getItem("snw_page_size_pac")) || 10));

function filtradosPac() {
  const q = filtro.trim().toLowerCase();
  return pacientes.filter(
    (p) =>
      (filtroEstado === "todos" || p.estado === filtroEstado) &&
      (filtroRespuesta === "todas" || (p.respuesta || "pendiente") === filtroRespuesta) &&
      (filtroInteres === "todos" || !!p.no_interesado) &&
      (!q ||
        [p.nombre, p.apellido, p.telefono]
          .filter(Boolean)
          .some((v) => v.toLowerCase().includes(q)))
  );
}

function totalPaginasPac(n) {
  return Math.max(1, Math.ceil(n / pageSizePac));
}

function itemsPaginaPac(visibles) {
  const total = totalPaginasPac(visibles.length);
  if (paginaPac > total) paginaPac = total;
  if (paginaPac < 1) paginaPac = 1;
  return visibles.slice((paginaPac - 1) * pageSizePac, paginaPac * pageSizePac);
}

function pintarPaginadorPac(total) {
  const info = $("#pagInfoPac");
  const btnAnt = $("#pagAntPac");
  const btnSig = $("#pagSigPac");
  const selTam = $("#selPageSizePac");
  if (!info && !btnAnt && !btnSig && !selTam) return; // HTML antiguo en caché
  const paginas = totalPaginasPac(total);
  if (info) info.textContent = `Página ${paginaPac} de ${paginas} · ${total} paciente${total === 1 ? "" : "s"}`;
  if (btnAnt) btnAnt.disabled = paginaPac <= 1;
  if (btnSig) btnSig.disabled = paginaPac >= paginas;
  if (selTam && selTam.value !== String(pageSizePac)) selTam.value = String(pageSizePac);
}

const $ = (sel) => document.querySelector(sel);

const tbodyEl = $("#tablaPacientes tbody");
const vacioEl = $("#tablaVacia");
const buscadorEl = $("#buscador");
const statsEl = $("#stats");
const chkTodos = $("#chkTodos");
const contadorSel = $("#contadorSel");
const toastEl = $("#toast");
const selBasePac = $("#selBasePac");

// Solo lectura para cuentas sin permiso de gestión (el usuario normal ve su
// base pero no la edita: sin checks, sin edición inline, sin CSV ni borrado;
// el backend exige el permiso en cada mutación de todas formas).
const PUEDE_GESTIONAR_PAC = !!window.snwEsPrivilegiado ||
  (!!window.snwPuede && window.snwPuede("pacientes"));
if (!PUEDE_GESTIONAR_PAC && chkTodos) {
  const thCheck = chkTodos.closest("th");
  if (thCheck) thCheck.hidden = true;
}
const toolbarMasivo = $("#toolbarMasivo");
const selEstadoMasivo = $("#selEstadoMasivo");
const selRespuestaMasivo = $("#selRespuestaMasivo");

// Selector único de base de datos: legacy (dev/prod) o área, siempre
// mostrando el nombre físico de la tabla. Valores: "dev" | "prod" | "esp:<id>".
function basePacActual() {
  return (selBasePac && selBasePac.value) || "dev";
}

function espPacId() {
  const v = basePacActual();
  if (!v.startsWith("esp:")) return null;
  const id = Number(v.slice(4));
  return Number.isFinite(id) ? id : null;
}

function tablaPacActual() {
  const esp = espPacId();
  if (esp != null) {
    const e = areas.find((x) => x.id === esp);
    return e ? e.nombre_tabla_base : null;
  }
  return basePacActual() === "prod" ? "pacientes_prod" : "pacientes_dev";
}

function qsBase() {
  const esp = espPacId();
  if (esp != null) return `ambiente=produccion&area_id=${esp}`;
  return `ambiente=${basePacActual() === "prod" ? "produccion" : "desarrollo"}`;
}

async function cargarBasesPac() {
  if (!selBasePac) return;
  try {
    const r = await fetch("api/areas/mias", { headers: authHeaders(), cache: "no-store" });
    if (!r.ok) throw new Error();
    areas = await r.json();
  } catch {
    console.error("[pacientes.js cargarBasesPac()]");
    areas = [];
  }
  selBasePac.innerHTML =
    (PUEDE_GESTIONAR_PAC
      ? `<option value="dev">Base de datos desarrollo (pacientes_dev)</option>` +
        `<option value="prod">Base de datos producción (pacientes_prod)</option>`
      : "") +
    areas.map((e) => `<option value="esp:${e.id}">${escaparHtml(e.nombre_visible)} (${escaparHtml(e.nombre_tabla_base)})</option>`).join("") +
    (!PUEDE_GESTIONAR_PAC && !areas.length
      ? `<option value="" disabled>Sin bases asignadas: pide un área a un administrador</option>`
      : "");
  // Restaura la última usada si sigue disponible; si no, la primera.
  const valores = [...selBasePac.options].map((o) => o.value).filter(Boolean);
  const guardadaEsp = localStorage.getItem("snw_esp_pacientes") || "";
  let elegido = null;
  if (guardadaEsp && valores.includes(`esp:${guardadaEsp}`)) {
    elegido = `esp:${guardadaEsp}`;
  } else if (PUEDE_GESTIONAR_PAC) {
    const amb = ambienteAdmin === "produccion" ? "prod" : "dev";
    elegido = valores.includes(amb) ? amb : valores[0];
    localStorage.removeItem("snw_esp_pacientes");
  } else {
    elegido = valores[0] || null;
    localStorage.removeItem("snw_esp_pacientes");
  }
  if (elegido == null) return;
  selBasePac.value = elegido;
  if (elegido.startsWith("esp:")) {
    localStorage.setItem("snw_esp_pacientes", elegido.slice(4));
  } else if (localStorage.getItem("snw_ambiente_admin")) {
    ambienteAdmin = elegido === "prod" ? "produccion" : "desarrollo";
    localStorage.setItem("snw_ambiente_admin", ambienteAdmin);
  } else {
    ambienteAdmin = null; // lo define cargar() desde el entorno global
  }
  aplicarModoBase();
}

// Borrar la tabla completa (DROP) es irreversible: solo administrador y
// desarrollador. El backend lo exige igual (Depends(solo_admin)).
const PUEDE_ELIMINAR_TABLA = !!window.snwEsPrivilegiado;

function areaActual() {
  const esp = espPacId();
  return esp == null ? null : areas.find((x) => x.id === esp) || null;
}

// La card «Gestionar base de datos» trabaja sobre la base elegida arriba: con
// una tabla general (dev/prod) no hay carga ni borrado, y con un área se
// habilita lo que corresponda según el rol.
function aplicarModoBase() {
  const esp = espPacId();
  const tabla = tablaPacActual();
  const area = areaActual();
  const esArea = !!area;

  const avisoSinEsp = $("#gestionSinEsp");
  if (avisoSinEsp) avisoSinEsp.hidden = areas.length > 0;
  const avisoLegacy = $("#gestionLegacy");
  if (avisoLegacy) avisoLegacy.hidden = esArea || areas.length === 0;

  const btnSubir = $("#btnSubirCsv");
  const btnVaciar = $("#btnVaciarBase");
  const btnEliminarTabla = $("#btnEliminarTablaPac");
  const inpCsv = $("#csvArchivo");
  const lblCsv = $("#lblArchivoGestion");
  // Sin áreas no hay nada que gestionar; con tabla general tampoco.
  const puedeOperar = esArea && areas.length > 0;
  if (btnSubir) btnSubir.disabled = !puedeOperar;
  if (btnVaciar) btnVaciar.disabled = !puedeOperar;
  if (inpCsv) inpCsv.disabled = !puedeOperar;
  if (lblCsv) lblCsv.classList.toggle("is-disabled", !puedeOperar);
  if (btnEliminarTabla) btnEliminarTabla.hidden = !PUEDE_ELIMINAR_TABLA;
  if (btnEliminarTabla) btnEliminarTabla.disabled = !puedeOperar;

  const msg = $("#csvMsg");
  if (msg) {
    msg.textContent = esArea
      ? `Destino: ${area.nombre_visible} (${area.nombre_tabla_base}) · columnas: nombre, apellido, telefono (UTF-8).`
      : "Destino: —";
  }

  // El borrado de registros seleccionados solo existe para tablas de área.
  const btnDelMasivo = $("#btnEliminarMasivo");
  if (btnDelMasivo) btnDelMasivo.hidden = esp == null;
  const tituloEl = $("#tituloPacientes");
  if (tituloEl && tabla) tituloEl.textContent = `Pacientes · ${tabla}`;
}

if (selBasePac) selBasePac.addEventListener("change", async () => {
  const v = selBasePac.value;
  if (v.startsWith("esp:")) {
    localStorage.setItem("snw_esp_pacientes", v.slice(4));
  } else {
    localStorage.removeItem("snw_esp_pacientes");
    ambienteAdmin = v === "prod" ? "produccion" : "desarrollo";
    localStorage.setItem("snw_ambiente_admin", ambienteAdmin);
  }
  seleccionados = new Set();
  todoMarcado = false;
  paginaPac = 1;
  aplicarModoBase();
  cargar();
});

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
          if (selBasePac && espPacId() == null) {
            selBasePac.value = ambienteAdmin === "produccion" ? "prod" : "dev";
            localStorage.setItem("snw_ambiente_admin", ambienteAdmin);
            aplicarModoBase();
          }
        }
      } catch {}
      _ambienteInicializado = true;
    }
    const esp = espPacId();
    const amb = esp != null ? "produccion" : (basePacActual() === "prod" ? "produccion" : "desarrollo");
    const [rp, rc] = await Promise.all([
      fetch(`${API_PACIENTES}?${qsBase()}`, { headers: authHeaders(), cache: "no-store" }),
      fetch(`api/configuracion?ambiente=${amb}`, { headers: authHeaders(), cache: "no-store" }),
    ]);
    if (rp.status === 401 || rc.status === 401) { window.snwSesionExpirada(); return; }
    if (rp.status === 403) {
      toast(esp ? "No tienes acceso a esta área." : "Sin acceso a esta base.", "error");
      pacientes = [];
      render();
      return;
    }

    pacientes = (await rp.json()).map((p) => ({
      ...p,
      estado: p.estado || "pendiente",
    }));
    config = await rc.json();

    aplicarModoBase();

    render();
  } catch (err) {
    console.error("[pacientes] cargar:", err);
    toast("Error al conectar con el servidor" + (err && err.message ? `: ${err.message}` : "."), "error");
  }
}

function render() {
  const visibles = filtradosPac();
  const enPagina = itemsPaginaPac(visibles);

  tbodyEl.innerHTML = "";

  for (const p of enPagina) {
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
      (PUEDE_GESTIONAR_PAC
        ? `<td class="col-check"><input type="checkbox" data-id="${p.id}" ${seleccionados.has(p.id) ? "checked" : ""}></td>`
        : `<td class="col-check"></td>`) +
      `<td class="campo-id">${p.id}</td>` +
      `<td class="campo-nombre">${escaparHtml(nombreCompleto)}</td>` +
      `<td class="campo-tel">${escaparHtml(p.telefono)}</td>` +
      `<td class="campo-estado">` +
        `<span class="estado-badge estado-${escaparHtml(p.estado)}"${PUEDE_GESTIONAR_PAC ? ` data-editable data-id="${p.id}" title="Click para cambiar estado"` : ""}>${escaparHtml(p.estado)}</span>` +
        `<select class="estado-select" data-id="${p.id}" hidden>` +
          `<option value="pendiente"${p.estado === "pendiente" ? " selected" : ""}>pendiente</option>` +
          `<option value="enviado"${p.estado === "enviado" ? " selected" : ""}>enviado</option>` +
        `</select>` +
      `</td>` +
      celdaError +
      `<td class="campo-respuesta">` +
        `<span class="respuesta-badge respuesta-${escaparHtml(respuesta)}${bajaBloqueada ? " respuesta-badge--bloqueada" : ""}"` +
          `${!PUEDE_GESTIONAR_PAC || bajaBloqueada ? "" : " data-editable"} data-id="${p.id}"` +
          ` title="${escaparHtml(!PUEDE_GESTIONAR_PAC || bajaBloqueada ? tituloResp : tituloResp + " · click para cambiar")}">` +
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
    if (chk) chk.addEventListener("change", (e) => {
      e.target.checked ? seleccionados.add(p.id) : seleccionados.delete(p.id);
      if (!e.target.checked) todoMarcado = false;
      refrescarSeleccion();
    });
    tbodyEl.appendChild(tr);
  }

  vacioEl.hidden = visibles.length > 0;
  pintarPaginadorPac(visibles.length);

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
    statCard("neutral", "fa-users", esActivoEstado("todos"), 'data-estado="todos"', "Total", conteo.total) +
    statCard("warn", "fa-clock", esActivoEstado("pendiente"), 'data-estado="pendiente"', "Pendientes", conteo.pendiente) +
    statCard("ok", "fa-paper-plane", esActivoEstado("enviado"), 'data-estado="enviado"', "Enviados", conteo.enviado) +
    statCard("danger", "fa-triangle-exclamation", esActivoEstado("error"), 'data-estado="error"', "Errores", conteo.error) +
    statCard("neutral", "fa-comment-slash", esActivoResp("pendiente"), 'data-respuesta="pendiente"', "Sin resp.", conteoResp.pendiente) +
    statCard("ok", "fa-comments", esActivoResp("respondio"), 'data-respuesta="respondio"', "Respondió", conteoResp.respondio) +
    statCard("danger", "fa-user-slash", esActivoResp("baja"), 'data-respuesta="baja"', "Baja", conteoResp.baja) +
    statCard("info", "fa-thumbs-down", esActivoInteres, 'data-interes="no_interesado"', "No le interesa", conteoNoInteresados);

  refrescarSeleccion(enPagina);
}

function refrescarSeleccion(visibles = null) {
  // Sin argumento: la página actual. El "seleccionar todos" opera sobre la
  // página visible, no sobre todo el filtro.
  visibles = visibles ?? itemsPaginaPac(filtradosPac());

  contadorSel.textContent =
    seleccionados.size > 0 ? `${seleccionados.size} seleccionado${seleccionados.size === 1 ? "" : "s"}` : "";

  if (toolbarMasivo) toolbarMasivo.hidden = seleccionados.size === 0;

  chkTodos.checked =
    visibles.length > 0 && visibles.every((p) => seleccionados.has(p.id));
}

// Devuelve los ids de la página actual (el "seleccionar todos" opera sobre
// la página visible, no sobre todo el filtro).
function idsSeleccionables() {
  return new Set(itemsPaginaPac(filtradosPac()).map((p) => p.id));
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

// Edición inline del estado y de la respuesta (badge -> select). Solo con
// permiso de gestión; el resto ve los badges fijos.
tbodyEl.addEventListener("click", (e) => {
  if (!PUEDE_GESTIONAR_PAC) return;
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
  if (!PUEDE_GESTIONAR_PAC) return;
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
  marcarFilasActualizando([id], true);
  try {
    const res = await fetch(`api/pacientes/${id}/respuesta?${qsBase()}`, {
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
    console.error("[pacientes.js:477]", err);
    toast(err.message || "No se pudo actualizar la respuesta", "error");
    marcarFilasActualizando([id], false);
    if (paciente) sel.value = anterior || "pendiente";
    const badge = tbodyEl.querySelector(`.respuesta-badge[data-id="${id}"]`);
    if (badge) badge.hidden = false;
    sel.hidden = true;
    sel.disabled = false;
  }
});

tbodyEl.addEventListener("change", async (e) => {
  if (!PUEDE_GESTIONAR_PAC) return;
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
  marcarFilasActualizando([id], true);
  try {
    const res = await fetch(`api/pacientes/${id}?${qsBase()}`, {
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
    console.error("[pacientes.js:521]", err);
    toast(err.message || "No se pudo actualizar el estado", "error");
    marcarFilasActualizando([id], false);
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
  paginaPac = 1;
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
  paginaPac = 1;
  sincronizarSeleccionConFiltro();
  render();
});

window.snwConCooldown($("#btnActualizar"), cargar);

// --- Paginación: Anterior/Siguiente y tamaño de página (10–100) ------------
const pagAntPac = $("#pagAntPac");
const pagSigPac = $("#pagSigPac");
const selPageSizePac = $("#selPageSizePac");
if (selPageSizePac) selPageSizePac.value = String(pageSizePac);
if (pagAntPac) pagAntPac.addEventListener("click", () => {
  if (paginaPac > 1) { paginaPac -= 1; render(); }
});
if (pagSigPac) pagSigPac.addEventListener("click", () => {
  paginaPac += 1;
  render();
});
if (selPageSizePac) selPageSizePac.addEventListener("change", () => {
  pageSizePac = Math.min(100, Math.max(10, Number(selPageSizePac.value) || 10));
  localStorage.setItem("snw_page_size_pac", String(pageSizePac));
  paginaPac = 1;
  render();
});

// --- Edición masiva: cambiar estado o respuesta de todos los seleccionados --
const ESTADO_LABEL = { pendiente: "pendiente", enviado: "enviado", error: "error" };
const RESPUESTA_LABEL = { pendiente: "sin respuesta", respondio: "respondió", baja: "dado de baja" };

// Feedback visual mientras se aplica un cambio: las filas afectadas pulsan
// hasta que la tabla se vuelve a dibujar (ver .fila-actualizando en CSS).
function marcarFilasActualizando(ids, on) {
  for (const id of ids) {
    const chk = tbodyEl.querySelector(`input[type="checkbox"][data-id="${id}"]`);
    const tr = chk ? chk.closest("tr") : null;
    if (tr) tr.classList.toggle("fila-actualizando", on);
  }
}

let masivoPendiente = null;

function pedirConfirmacionMasiva(sel, url, campo) {
  const valor = sel.value;
  if (!valor) return;
  const ids = [...seleccionados];
  if (!ids.length) {
    sel.value = "";
    toast("Selecciona al menos un paciente primero.", "error");
    return;
  }
  const etiquetas = campo === "estado" ? ESTADO_LABEL : RESPUESTA_LABEL;
  const cantidad = ids.length;
  masivoPendiente = { sel, url, campo, valor, ids };
  $("#masivoTitulo").textContent = campo === "estado" ? "Cambiar estado" : "Cambiar respuesta";
  $("#masivoTexto").textContent = campo === "estado"
    ? `¿Cambiar el estado de ${cantidad} paciente${cantidad === 1 ? "" : "s"} a "${etiquetas[valor]}"?`
    : `¿Cambiar la respuesta de ${cantidad} paciente${cantidad === 1 ? "" : "s"} a "${etiquetas[valor]}"?`;
  $("#modalConfirmarMasivo").hidden = false;
}

function cerrarModalMasivo(revertirSelect) {
  const op = masivoPendiente;
  masivoPendiente = null;
  $("#modalConfirmarMasivo").hidden = true;
  if (revertirSelect && op) op.sel.value = "";
}

async function ejecutarMasivo() {
  const op = masivoPendiente;
  masivoPendiente = null;
  if (!op) return;
  $("#modalConfirmarMasivo").hidden = true;
  const { sel, url, campo, valor, ids } = op;
  sel.disabled = true;
  marcarFilasActualizando(ids, true);
  try {
    const res = await fetch(`${url}?${qsBase()}`, {
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
    console.error("[pacientes.js ejecutarMasivo()]", err);
    toast(err.message || "No se pudo aplicar el cambio", "error");
    marcarFilasActualizando(ids, false);
  } finally {
    sel.value = "";
    sel.disabled = false;
  }
}

if (selEstadoMasivo) {
  selEstadoMasivo.addEventListener("change", () =>
    pedirConfirmacionMasiva(selEstadoMasivo, "api/pacientes/estado-masivo", "estado"));
}
if (selRespuestaMasivo) {
  selRespuestaMasivo.addEventListener("change", () =>
    pedirConfirmacionMasiva(selRespuestaMasivo, "api/pacientes/respuesta-masiva", "respuesta"));
}
$("#btnConfirmarMasivo").addEventListener("click", () => {
  if (eliminarPendiente) ejecutarEliminacion();
  else ejecutarMasivo();
});
function cerrarModalesMasivo() {
  cerrarModalMasivo(true);
  cerrarModalEliminar();
}
$("#btnCancelarMasivo").addEventListener("click", cerrarModalesMasivo);
$("#modalConfirmarMasivo").addEventListener("click", (e) => {
  if (e.target.id === "modalConfirmarMasivo") cerrarModalesMasivo();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("#modalConfirmarMasivo").hidden) cerrarModalesMasivo();
});

// --- Eliminar registros en bloque (solo tablas de área;
// esta página es exclusiva admin/dev): mismo modal de confirmación y
// animación de filas que el cambio masivo. ------------------------------------
let eliminarPendiente = null;

const btnEliminarMasivo = $("#btnEliminarMasivo");
if (btnEliminarMasivo) btnEliminarMasivo.addEventListener("click", () => {
  const esp = espPacId();
  const ids = [...seleccionados];
  if (esp == null || !ids.length) return;
  eliminarPendiente = { esp, ids };
  $("#masivoTitulo").textContent = "Eliminar registros";
  $("#masivoTexto").textContent =
    `¿Eliminar ${ids.length} registro${ids.length === 1 ? "" : "s"} de esta área? Esta acción no se puede deshacer.`;
  $("#btnConfirmarMasivo").classList.remove("btn--primary");
  $("#btnConfirmarMasivo").classList.add("btn--danger");
  $("#modalConfirmarMasivo").hidden = false;
});

function cerrarModalEliminar() {
  eliminarPendiente = null;
  $("#modalConfirmarMasivo").hidden = true;
  $("#btnConfirmarMasivo").classList.add("btn--primary");
  $("#btnConfirmarMasivo").classList.remove("btn--danger");
}

async function ejecutarEliminacion() {
  const op = eliminarPendiente;
  eliminarPendiente = null;
  if (!op) return;
  $("#modalConfirmarMasivo").hidden = true;
  $("#btnConfirmarMasivo").classList.add("btn--primary");
  $("#btnConfirmarMasivo").classList.remove("btn--danger");
  const { esp, ids } = op;
  btnEliminarMasivo.disabled = true;
  marcarFilasActualizando(ids, true);
  let ok = 0, mal = 0;
  for (const id of ids) {
    try {
      const res = await fetch(`api/areas/${esp}/pacientes/${id}`, {
        method: "DELETE", headers: authHeaders(),
      });
      if (res.status === 401) { window.snwSesionExpirada(); return; }
      if (res.ok) { ok += 1; seleccionados.delete(id); }
      else mal += 1;
    } catch { mal += 1; }
  }
  toast(mal ? `${ok} eliminados, ${mal} fallaron.` : `${ok} registro${ok === 1 ? "" : "s"} eliminado${ok === 1 ? "" : "s"}.`, mal ? "error" : "ok");
  todoMarcado = false;
  btnEliminarMasivo.disabled = false;
  cargar();
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

let yaCargada = false;
window.snwCargarPacientes = function () {
  if (yaCargada) return;
  yaCargada = true;
  (async () => { await cargarBasesPac(); cargar(); })();
};

// --- Eliminar tabla completa (solo área; esta página es exclusiva
// admin/dev): modal con 10 s de espera antes de activar Confirmar; Cancelar
// siempre activo. --------------------------------------------------------------
const modalDelTabla = $("#modalEliminarTabla");
let timerDelTabla = null;

function cerrarModalDelTabla() {
  if (timerDelTabla) { clearInterval(timerDelTabla); timerDelTabla = null; }
  if (modalDelTabla) modalDelTabla.hidden = true;
}

const btnEliminarTablaPac = $("#btnEliminarTablaPac");
if (btnEliminarTablaPac) btnEliminarTablaPac.addEventListener("click", () => {
  const area = areaActual();
  if (!PUEDE_ELIMINAR_TABLA || !area || !modalDelTabla) return;
  $("#delTablaNombre").textContent = `${area.nombre_visible} (${area.nombre_tabla_base})`;
  $("#delTablaTotal").textContent = Number.isFinite(area.total_pacientes)
    ? String(area.total_pacientes) : String(pacientes.length);
  const btnConf = $("#btnConfirmarDelTabla");
  const btnCanc = $("#btnCancelarDelTabla");
  btnConf.disabled = true;
  let restantes = 10;
  btnConf.textContent = `Confirmar (${restantes})`;
  if (timerDelTabla) clearInterval(timerDelTabla);
  timerDelTabla = setInterval(() => {
    restantes -= 1;
    if (restantes <= 0) {
      clearInterval(timerDelTabla);
      timerDelTabla = null;
      btnConf.disabled = false;
      btnConf.textContent = "Confirmar";
    } else {
      btnConf.textContent = `Confirmar (${restantes})`;
    }
  }, 1000);
  // Cancelar nunca se deshabilita.
  btnCanc.disabled = false;
  modalDelTabla.hidden = false;
});

const btnCancelarDelTabla = $("#btnCancelarDelTabla");
if (btnCancelarDelTabla) btnCancelarDelTabla.addEventListener("click", cerrarModalDelTabla);
if (modalDelTabla) modalDelTabla.addEventListener("click", (e) => {
  if (e.target === modalDelTabla) cerrarModalDelTabla();
});

const btnConfirmarDelTabla = $("#btnConfirmarDelTabla");
if (btnConfirmarDelTabla) btnConfirmarDelTabla.addEventListener("click", async () => {
  const esp = espPacId();
  if (esp == null) { cerrarModalDelTabla(); return; }
  btnConfirmarDelTabla.disabled = true;
  try {
    const res = await fetch(`api/areas/${esp}/tabla`, {
      method: "DELETE", headers: authHeaders(),
    });
    if (res.status === 401) { window.snwSesionExpirada(); return; }
    if (!res.ok) throw new Error(`Error ${res.status}`);
    const data = await res.json().catch(() => ({}));
    toast(`Tabla ${data.nombre_tabla_base || ""} eliminada.`.trim(), "ok");
    cerrarModalDelTabla();
    seleccionados = new Set();
    todoMarcado = false;
    localStorage.removeItem("snw_esp_pacientes");
    await cargarBasesPac();
    cargar();
  } catch (err) {
    console.error("[pacientes.js cerrarModalDelTabla()]", err);
    cerrarModalDelTabla();
    toast(err.message || "No se pudo eliminar.", "error");
  }
});

// --- Eliminar SOLO los pacientes del área elegida en la card de gestión -----
const btnVaciarBase = $("#btnVaciarBase");
const modalVaciarTabla = $("#modalVaciarTabla");
let timerVaciarTabla = null;

function cerrarModalVaciarTabla() {
  if (timerVaciarTabla) { clearInterval(timerVaciarTabla); timerVaciarTabla = null; }
  if (modalVaciarTabla) modalVaciarTabla.hidden = true;
}

if (btnVaciarBase) btnVaciarBase.addEventListener("click", () => {
  const area = areaActual();
  if (!area || !modalVaciarTabla) return;
  $("#vaciarTablaNombre").textContent = `${area.nombre_visible} (${area.nombre_tabla_base})`;
  $("#vaciarTablaTotal").textContent =
    Number.isFinite(area.total_pacientes) ? String(area.total_pacientes) : "…";
  const btnConf = $("#btnConfirmarVaciarTabla");
  const btnCanc = $("#btnCancelarVaciarTabla");
  btnConf.disabled = true;
  let restantes = 10;
  btnConf.textContent = `Confirmar (${restantes})`;
  if (timerVaciarTabla) clearInterval(timerVaciarTabla);
  timerVaciarTabla = setInterval(() => {
    restantes -= 1;
    if (restantes <= 0) {
      clearInterval(timerVaciarTabla);
      timerVaciarTabla = null;
      btnConf.disabled = false;
      btnConf.textContent = "Confirmar";
    } else {
      btnConf.textContent = `Confirmar (${restantes})`;
    }
  }, 1000);
  btnCanc.disabled = false;
  modalVaciarTabla.hidden = false;
});

const btnCancelarVaciarTabla = $("#btnCancelarVaciarTabla");
if (btnCancelarVaciarTabla) btnCancelarVaciarTabla.addEventListener("click", cerrarModalVaciarTabla);
if (modalVaciarTabla) modalVaciarTabla.addEventListener("click", (e) => {
  if (e.target === modalVaciarTabla) cerrarModalVaciarTabla();
});

const btnConfirmarVaciarTabla = $("#btnConfirmarVaciarTabla");
if (btnConfirmarVaciarTabla) btnConfirmarVaciarTabla.addEventListener("click", async () => {
  const espId = espPacId();
  if (espId == null) { cerrarModalVaciarTabla(); return; }
  btnConfirmarVaciarTabla.disabled = true;
  try {
    const res = await fetch(`api/areas/${espId}/tabla/datos`, {
      method: "DELETE", headers: authHeaders(),
    });
    if (res.status === 401) { window.snwSesionExpirada(); return; }
    if (!res.ok) throw new Error(`Error ${res.status}`);
    const data = await res.json().catch(() => ({}));
    toast(`Base de datos vaciada (${data.pacientes_eliminados ?? 0} pacientes eliminados).`, "ok");
    cerrarModalVaciarTabla();
    await cargarBasesPac();
    cargar();
  } catch (err) {
    console.error("[pacientes.js btnConfirmarVaciarTabla()]", err);
    cerrarModalVaciarTabla();
    toast(err.message || "No se pudo vaciar.", "error");
  }
});

// --- Cargar CSV en el área elegida en la card de gestión --------------------
const csvInput = $("#csvArchivo");
const btnSubirCsv = $("#btnSubirCsv");
const nombreArchivo = $("#nombreArchivoGestion");
const csvResultado = $("#csvResultado");
let archivoElegido = null;

if (csvInput) {
  csvInput.addEventListener("change", () => {
    const f = csvInput.files && csvInput.files[0];
    archivoElegido = f || null;
    if (nombreArchivo) {
      nombreArchivo.textContent = f
        ? `${f.name} (${(f.size / 1024).toFixed(1)} KB)`
        : "No se ha seleccionado ningún archivo";
    }
  });
}

if (btnSubirCsv) btnSubirCsv.addEventListener("click", async () => {
  const esp = espPacId();
  if (esp == null) return toast("Elige un área como base de datos.", "error");
  if (!archivoElegido) return toast("Elige un archivo CSV.", "error");
  btnSubirCsv.disabled = true;
  if (csvResultado) csvResultado.innerHTML = `<p class="field__hint">Subiendo…</p>`;
  try {
    const datos = new FormData();
    datos.append("archivo", archivoElegido);
    const res = await fetch(`api/areas/${esp}/pacientes/csv`, {
      method: "POST", headers: authHeaders(), body: datos,
    });
    const cuerpo = await res.json().catch(() => ({}));
    if (res.status === 401) { window.snwSesionExpirada(); return; }
    if (res.status === 403) throw new Error("No tienes acceso a esta área.");
    if (!res.ok) {
      const det = cuerpo.detail;
      throw new Error(
        (det && det.columnas) || (typeof det === "string" ? det : `Error ${res.status}`)
      );
    }
    const inf = cuerpo.informe || {};
    const errores = inf.errores || [];
    const mostrados = errores.slice(0, 20);
    if (csvResultado) {
      csvResultado.innerHTML =
        `<p><strong>${inf.insertados ?? 0}</strong> insertados de ` +
        `${inf.procesados ?? 0} procesados ` +
        `(${inf.duplicados ?? 0} duplicados, ${inf.rechazados ?? 0} rechazados).</p>` +
        (mostrados.length
          ? `<ul>` + mostrados.map((e) => `<li>fila ${e.fila}: ${escaparHtml(e.motivo)}</li>`).join("") + `</ul>` +
            (errores.length > mostrados.length
              ? `<p class="field__hint">…y ${errores.length - mostrados.length} más.</p>` : "")
          : "");
    }
    toast("Base de datos cargada.", "ok");
    csvInput.value = "";
    archivoElegido = null;
    if (nombreArchivo) nombreArchivo.textContent = "No se ha seleccionado ningún archivo";
    cargar();
  } catch (err) {
    console.error("[pacientes.js btnSubirCsv]", err);
    if (csvResultado) csvResultado.innerHTML = "";
    toast(err.message || "No se pudo cargar el archivo.", "error");
  } finally {
    aplicarModoBase();
  }
});
})();
