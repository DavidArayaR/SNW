(function () {
const API_PACIENTES = "api/pacientes";

function authHeaders(extra = {}) {
  return { Authorization: "Bearer " + (localStorage.getItem("snw_token") || ""), ...extra };
}

let ambienteAdmin = localStorage.getItem("snw_ambiente_admin");
let _ambienteInicializado = false;

let pacientes = [];
let totalPacientesFiltrados = 0;
let resumenPacientes = null;
let baseResumenPacientes = null;
let consultaPacientesAnterior = null;
let areas = [];
let filtro = "";
let filtroEstado = "todos";
let filtroRespuesta = "todas";
let filtroInteres = "todos";
let seleccionados = new Set();
let seleccionadosDatos = new Map();
let todoMarcado = false;

// Paginación de la tabla: 10 a 100 pacientes por página.
let paginaPac = 1;
let pageSizePac = Math.min(100, Math.max(10, Number(localStorage.getItem("snw_page_size_pac")) || 10));

function filtradosPac() {
  // El servidor ya aplicó la búsqueda y los filtros a toda la base.
  return pacientes;
}

function totalPaginasPac(n) {
  return Math.max(1, Math.ceil(n / pageSizePac));
}

function itemsPaginaPac(visibles) {
  const total = totalPaginasPac(totalPacientesFiltrados);
  if (paginaPac > total) paginaPac = total;
  if (paginaPac < 1) paginaPac = 1;
  return visibles;
}

function pintarPaginadorPac(total) {
  const info = $("#pagInfoPac");
  const btnPrim = $("#pagPrimPac");
  const btnAnt = $("#pagAntPac");
  const inpIr = $("#pagIrPac");
  const btnSig = $("#pagSigPac");
  const btnUlt = $("#pagUltPac");
  const selTam = $("#selPageSizePac");
  if (!info && !btnAnt && !btnSig && !selTam) return; // HTML antiguo en caché
  const paginas = totalPaginasPac(total);
  if (info) info.textContent = `Página ${paginaPac} de ${paginas} · ${total} paciente${total === 1 ? "" : "s"}`;
  if (btnPrim) btnPrim.disabled = paginaPac <= 1;
  if (btnAnt) btnAnt.disabled = paginaPac <= 1;
  if (inpIr) {
    inpIr.max = String(paginas);
    inpIr.value = String(paginaPac);
    inpIr.disabled = paginas <= 1;
    inpIr.setAttribute("aria-label", `Ir a la página (1 a ${paginas})`);
  }
  if (btnSig) btnSig.disabled = paginaPac >= paginas;
  if (btnUlt) btnUlt.disabled = paginaPac >= paginas;
  if (selTam && selTam.value !== String(pageSizePac)) selTam.value = String(pageSizePac);
}

const $ = (sel) => document.querySelector(sel);

const tbodyEl = $("#tablaPacientes tbody");
const tablaEl = $("#tablaPacientes");
const vacioEl = $("#tablaVacia");
const tablaWrap = $("#tablaPacientesWrap");
const tablaCargando = $("#tablaCargando");
const tablaCargandoTexto = $("#tablaCargandoTexto");
let solicitudPacientes = 0;
let controlCargaPacientes = null;

function mostrarCargaPacientes(activa) {
  if (tablaCargando) tablaCargando.hidden = !activa;
  if (activa && tablaCargandoTexto) tablaCargandoTexto.textContent = "Cargando pacientes…";
  if (tablaWrap) {
    tablaWrap.classList.toggle("is-loading", activa);
  }
  if (tablaEl) tablaEl.setAttribute("aria-busy", String(activa));
  if (activa && vacioEl) vacioEl.hidden = true;
}
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
if (window.snwEsDev && selRespuestaMasivo) {
  const opcion = document.createElement("option");
  opcion.value = "respondio";
  opcion.textContent = "Respondió (prueba)";
  selRespuestaMasivo.add(opcion, selRespuestaMasivo.querySelector('option[value="baja"]'));
}
if (window.snwEsDev && selEstadoMasivo) {
  const opcion = document.createElement("option");
  opcion.value = "error";
  opcion.textContent = "Error (prueba)";
  selEstadoMasivo.add(opcion);
}

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
    (window.snwEsPrivilegiado
      ? `<option value="dev">Base de datos desarrollo (pacientes_dev)</option>` : "") +
    (PUEDE_GESTIONAR_PAC
      ? `<option value="prod">Base de datos producción (pacientes_prod)</option>` : "") +
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

function puedeEditarPaciente(p) {
  return PUEDE_GESTIONAR_PAC && !!p.editable;
}

function puedeSeleccionarPaciente(p) {
  return puedeEditarPaciente(p) || (espPacId() != null && PUEDE_ELIMINAR_TABLA);
}

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
  const esDesarrollo = basePacActual() === "dev" && !!window.snwEsPrivilegiado;
  const cardPacientePrueba = $("#cardPacientePrueba");
  if (cardPacientePrueba) cardPacientePrueba.hidden = !esDesarrollo;
  const colAutorizacion = $("#colAutorizacionPrueba");
  if (colAutorizacion) colAutorizacion.hidden = !esDesarrollo;

  const avisoSinEsp = $("#gestionSinEsp");
  if (avisoSinEsp) avisoSinEsp.hidden = areas.length > 0 || PUEDE_GESTIONAR_PAC;
  const avisoLegacy = $("#gestionLegacy");
  if (avisoLegacy) avisoLegacy.hidden = esArea || !PUEDE_GESTIONAR_PAC;

  const puedeCargarArea = !!window.snwEsPrivilegiado ||
    (!!window.snwPuede && window.snwPuede("csv_subir"));
  const btnSubir = $("#btnSubirCsv");
  const btnVaciar = $("#btnVaciarBase");
  const btnEliminarTabla = $("#btnEliminarTablaPac");
  const inpCsv = $("#csvArchivo");
  const lblCsv = $("#lblArchivoGestion");
  // Sin áreas no hay nada que gestionar; con tabla general tampoco.
  const puedeOperar = esArea && puedeCargarArea;
  if (btnSubir) btnSubir.disabled = !puedeOperar || !inpCsv?.files?.length ||
    !!$("#csvMapeo")?.hidden || !!$("#csvMapError")?.textContent;
  if (btnVaciar) {
    btnVaciar.hidden = !window.snwPuede?.("pacientes_vaciar");
    btnVaciar.disabled = !esArea;
  }
  if (inpCsv) inpCsv.disabled = !puedeOperar;
  if (lblCsv) lblCsv.classList.toggle("is-disabled", !puedeOperar);
  if (btnEliminarTabla) btnEliminarTabla.hidden = !PUEDE_ELIMINAR_TABLA;
  if (btnEliminarTabla) btnEliminarTabla.disabled = !puedeOperar;

  const msg = $("#csvMsg");
  if (msg) {
    msg.textContent = esArea
      ? `Destino: ${area.nombre_visible} (${area.nombre_tabla_base}) · asigna las columnas tras elegir el CSV UTF-8. La carga se divide en partes de hasta 2.000 filas o 2 MB.`
      : "Destino: —";
  }

  // El borrado de registros seleccionados solo existe para tablas de área.
  const btnDelMasivo = $("#btnEliminarMasivo");
  if (btnDelMasivo) btnDelMasivo.hidden = esp == null || !PUEDE_ELIMINAR_TABLA;
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
  seleccionadosDatos.clear();
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

async function cargar(mostrarFeedback = false) {
  const solicitud = ++solicitudPacientes;
  if (controlCargaPacientes) controlCargaPacientes.abort();
  const control = new AbortController();
  controlCargaPacientes = control;
  mostrarCargaPacientes(true);
  try {
    // Si no hay preferencia guardada, usar el entorno global de la configuración como default
    if (!_ambienteInicializado && !localStorage.getItem("snw_ambiente_admin")) {
      try {
        const r0 = await fetch("api/configuracion", { headers: authHeaders(), cache: "no-store", signal: control.signal });
        if (r0.ok) {
          const c0 = await r0.json();
          if (solicitud !== solicitudPacientes) return;
          ambienteAdmin = c0.entorno;
          if (selBasePac && espPacId() == null) {
            const preferida = ambienteAdmin === "produccion" ? "prod" : "dev";
            if ([...selBasePac.options].some((opcion) => opcion.value === preferida)) {
              selBasePac.value = preferida;
              localStorage.setItem("snw_ambiente_admin", ambienteAdmin);
              aplicarModoBase();
            }
          }
        }
      } catch {}
      _ambienteInicializado = true;
    }
    if (solicitud !== solicitudPacientes) return;
    if (!selBasePac?.value) {
      pacientes = [];
      totalPacientesFiltrados = 0;
      resumenPacientes = null;
      consultaPacientesAnterior = null;
      render();
      return;
    }
    const baseQs = qsBase();
    if (baseResumenPacientes !== baseQs) {
      resumenPacientes = null;
      baseResumenPacientes = baseQs;
      totalPacientesFiltrados = 0;
      consultaPacientesAnterior = null;
    }
    pacientes = [];
    render();
    const esp = espPacId();
    const parametros = new URLSearchParams(baseQs);
    parametros.set("limite", String(pageSizePac));
    parametros.set("q", filtro.trim().slice(0, 100));
    parametros.set("estado", filtroEstado);
    parametros.set("respuesta", filtroRespuesta);
    parametros.set("interes", filtroInteres);
    const claveConsulta = `${baseQs}|${filtro.trim().slice(0, 100)}|${filtroEstado}|${filtroRespuesta}|${filtroInteres}`;
    parametros.set("incluir_total", String(claveConsulta !== consultaPacientesAnterior || mostrarFeedback));
    parametros.set("incluir_resumen", String(resumenPacientes === null || mostrarFeedback));
    let bloque;
    do {
      parametros.set("pagina", String(paginaPac));
      // Solo se descarga la página visible: "1 de N" aquí parecía un
      // progreso de carga de todas las páginas, aunque no ocurre eso.
      if (tablaCargandoTexto) tablaCargandoTexto.textContent =
        `Cargando pacientes de la página ${paginaPac}…`;
      const rp = await fetch(`${API_PACIENTES}/pagina?${parametros}`, {
        headers: authHeaders(), cache: "no-store", signal: control.signal,
      });
      if (solicitud !== solicitudPacientes) return;
      if (rp.status === 401) { window.snwSesionExpirada(); return; }
      if (rp.status === 403) throw new Error(esp ? "No tienes acceso a esta área." : "Sin acceso a esta base.");
      if (!rp.ok) throw new Error("No se pudieron obtener los pacientes de esta base.");
      bloque = await rp.json();
      if (solicitud !== solicitudPacientes) return;
      if (bloque.total !== null) totalPacientesFiltrados = Number(bloque.total) || 0;
      if (bloque.resumen) resumenPacientes = bloque.resumen;
      if (paginaPac > totalPaginasPac(totalPacientesFiltrados)) {
        paginaPac = totalPaginasPac(totalPacientesFiltrados);
        parametros.set("incluir_total", "false");
        parametros.set("incluir_resumen", "false");
      } else break;
    } while (true);
    pacientes = (bloque.pacientes || []).map((p) => ({ ...p, estado: p.estado || "pendiente" }));
    consultaPacientesAnterior = claveConsulta;
    for (const p of pacientes) if (seleccionados.has(p.id)) seleccionadosDatos.set(p.id, p);

    aplicarModoBase();

    render();
    if (mostrarFeedback) toast("Pacientes actualizados correctamente.");
  } catch (err) {
    if (solicitud !== solicitudPacientes) return;
    if (err?.name === "AbortError") return;
    console.error("[pacientes] cargar:", err);
    toast(err?.message || "Error al cargar pacientes.", "error");
  } finally {
    if (solicitud === solicitudPacientes) {
      controlCargaPacientes = null;
      mostrarCargaPacientes(false);
      render();
    }
  }
}

function render() {
  const visibles = filtradosPac();
  const enPagina = itemsPaginaPac(visibles);

  tbodyEl.innerHTML = "";

  for (const p of enPagina) {
    const editable = puedeEditarPaciente(p);
    const seleccionable = puedeSeleccionarPaciente(p);
    const nombreCompleto = [p.nombre, p.apellido].filter(Boolean).join(" ");
    const tr = document.createElement("tr");
    const respuesta = p.respuesta || "pendiente";
    const esBaja = respuesta === "baja" || !!p.whatsapp_opt_out;
    // Baja pedida con sus propias palabras por WhatsApp: no se puede editar a
    // mano, solo se libera si el paciente se retracta (webhook).
    const bajaBloqueada = respuesta === "baja" && !!p.whatsapp_opt_out && !!p.opt_out_explicito;
    const respuestaLabels = { pendiente: "Sin respuesta", respondio: "Respondió", no_interesado: "No interesado", baja: "Se dio de baja" };
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
      // Sin permiso de gestión la casilla no existe: tampoco su celda, o las
      // filas quedarían con una columna más que la cabecera.
      (PUEDE_GESTIONAR_PAC
        ? `<td class="col-check">${seleccionable ? `<input type="checkbox" data-id="${p.id}" ${seleccionados.has(p.id) ? "checked" : ""}>` : ""}</td>`
        : "") +
      `<td class="campo-id">${p.id}</td>` +
      `<td class="campo-nombre">${escaparHtml(nombreCompleto)}</td>` +
      `<td class="campo-tel">${escaparHtml(p.telefono)}</td>` +
      `<td class="campo-tratamiento" title="${escaparHtml(p.tratamiento_ultima_atencion || "")}">${escaparHtml(p.tratamiento_ultima_atencion || "—")}</td>` +
      `<td class="campo-fecha-atencion">${escaparHtml(p.fecha_ultima_atencion || "—")}</td>` +
      `<td class="campo-estado">` +
        `<span class="estado-badge estado-${escaparHtml(p.estado)}"${editable ? ` data-editable data-id="${p.id}" title="Click para cambiar estado"` : ` title="${PUEDE_GESTIONAR_PAC ? "Solo se edita en números de prueba" : "Sin permiso para editar"}"`}>${escaparHtml(p.estado)}</span>` +
        `<select class="estado-select" data-id="${p.id}" hidden>` +
          `<option value="pendiente"${p.estado === "pendiente" ? " selected" : ""}>pendiente</option>` +
          `<option value="enviado"${p.estado === "enviado" ? " selected" : ""}>enviado</option>` +
          `${window.snwEsDev || p.estado === "error" ? `<option value="error"${p.estado === "error" ? " selected" : ""}>error${window.snwEsDev ? " (prueba)" : ""}</option>` : ""}` +
        `</select>` +
      `</td>` +
      celdaError +
      `<td class="campo-respuesta">` +
        `<span class="respuesta-badge respuesta-${escaparHtml(respuesta)}${bajaBloqueada ? " respuesta-badge--bloqueada" : ""}"` +
          `${!editable || bajaBloqueada ? "" : " data-editable"} data-id="${p.id}"` +
          ` title="${escaparHtml(!editable || bajaBloqueada ? tituloResp + (PUEDE_GESTIONAR_PAC && !p.editable ? " · Solo se edita en números de prueba" : "") : tituloResp + " · click para cambiar")}">` +
          `${bajaBloqueada ? '<i class="fa-solid fa-lock"></i> ' : ""}${escaparHtml(respuestaLabels[respuesta] ?? respuesta)}</span>` +
        `<select class="respuesta-select" data-id="${p.id}" hidden>` +
          // Un desarrollador puede simular una respuesta solo en números de
          // prueba; el backend valida rol y número en cada actualización.
          ["pendiente", "baja"].map((v) =>
            `<option value="${v}"${v === respuesta ? " selected" : ""}>${respuestaLabels[v]}</option>`).join("") +
          // Si ya respondió, se conserva el valor en el selector aun para
          // quienes no pueden marcarlo manualmente.
          (window.snwEsDev || respuesta === "respondio"
            ? `<option value="respondio"${respuesta === "respondio" ? " selected" : ""}>${respuestaLabels.respondio}${window.snwEsDev ? " (prueba)" : ""}</option>`
            : "") +
          (respuesta === "no_interesado"
            ? `<option value="no_interesado" selected disabled>${respuestaLabels.no_interesado} (WhatsApp)</option>`
            : "") +
        `</select>` +
        subResp +
      `</td>` +
      `<td class="campo-fecha">${escaparHtml(p.actualizado)}</td>`;
    if (basePacActual() === "dev" && window.snwEsPrivilegiado) {
      const autorizadoAmbos = p.autorizado_prueba && p.autorizado_prueba_prod;
      const etiquetaAutorizar = p.autorizado_prueba ? "Agregar a producción"
        : p.autorizado_prueba_prod ? "Agregar a desarrollo" : "Agregar a lista de pruebas";
      tr.innerHTML += `<td class="campo-prueba"><div class="paciente-prueba__acciones">${autorizadoAmbos
        ? `<span class="paciente-prueba__estado">Autorizado</span>`
        : `<button type="button" class="btn btn--ghost paciente-prueba__autorizar" data-autorizar-id="${p.id}">${etiquetaAutorizar}</button>`}
        ${p.autorizado_prueba || p.autorizado_prueba_prod
          ? `<button type="button" class="btn btn--ghost paciente-prueba__quitar" data-quitar-prueba-id="${p.id}" title="Quitar el número de las listas de prueba de desarrollo y producción, sin borrar al paciente">Quitar de números de prueba</button>`
          : ""}</div></td>`;
    }
    const chk = tr.querySelector('input[type="checkbox"]');
    if (chk) chk.addEventListener("change", (e) => {
      if (e.target.checked) { seleccionados.add(p.id); seleccionadosDatos.set(p.id, p); }
      else { seleccionados.delete(p.id); seleccionadosDatos.delete(p.id); }
      if (!e.target.checked) todoMarcado = false;
      refrescarSeleccion();
    });
    tbodyEl.appendChild(tr);
  }

  vacioEl.hidden = visibles.length > 0 || !!(tablaCargando && !tablaCargando.hidden);
  pintarPaginadorPac(totalPacientesFiltrados);

  const conteo = resumenPacientes || {};
  const conteoResp = resumenPacientes || {};
  const conteoInteresados = conteo.interesado || 0;
  const conteoNoInteresados = conteo.no_interesado || 0;

  const esActivoEstado = (estado) => filtroRespuesta === "todas" && filtroInteres === "todos" && filtroEstado === estado;
  const esActivoResp = (resp) => filtroEstado === "todos" && filtroInteres === "todos" && filtroRespuesta === resp;
  const esActivoInteres = (interes) =>
    filtroEstado === "todos" && filtroRespuesta === "todas" && filtroInteres === interes;
  // Tarjeta con ícono en chip de color (mismo tono que usaban los puntitos
  // de antes), en vez de la píldora chica: mismo botón/atributos de
  // filtro, solo cambia cómo se ve.
  const statCard = (tono, icono, activo, attrs, etiqueta, numero) =>
    `<button type="button" class="stat stat-card stat-card--${tono}${activo ? " activo" : ""}" ${attrs}>` +
      `<span class="stat-card__icono"><i class="fa-solid ${icono}"></i></span>` +
      `<span class="stat-card__texto"><strong>${numero}</strong><span>${etiqueta}</span></span>` +
    `</button>`;
  statsEl.innerHTML =
    statCard("neutral", "fa-users", esActivoEstado("todos"), 'data-estado="todos"', "Total", conteo.total || 0) +
    statCard("warn", "fa-clock", esActivoEstado("pendiente"), 'data-estado="pendiente"', "Pendientes", conteo.pendiente || 0) +
    statCard("ok", "fa-paper-plane", esActivoEstado("enviado"), 'data-estado="enviado"', "Enviados", conteo.enviado || 0) +
    statCard("danger", "fa-triangle-exclamation", esActivoEstado("error"), 'data-estado="error"', "Errores", conteo.error || 0) +
    statCard("neutral", "fa-comment-slash", esActivoResp("pendiente"), 'data-respuesta="pendiente"', "Sin resp.", conteoResp.sin_respuesta || 0) +
    statCard("ok", "fa-thumbs-up", esActivoInteres("interesado"), 'data-interes="interesado"', "Interesados", conteoInteresados) +
    statCard("danger", "fa-user-slash", esActivoResp("baja"), 'data-respuesta="baja"', "Baja", conteoResp.baja || 0) +
    statCard("info", "fa-thumbs-down", esActivoInteres("no_interesado"), 'data-interes="no_interesado"', "No le interesa", conteoNoInteresados);

  refrescarSeleccion(enPagina);
}

function refrescarSeleccion(visibles = null) {
  // Sin argumento: la página actual. El "seleccionar todos" opera sobre la
  // página visible, no sobre todo el filtro.
  visibles = (visibles ?? itemsPaginaPac(filtradosPac())).filter(puedeSeleccionarPaciente);

  contadorSel.textContent =
    seleccionados.size > 0 ? `${seleccionados.size} seleccionado${seleccionados.size === 1 ? "" : "s"}` : "";

  if (toolbarMasivo) toolbarMasivo.hidden = seleccionados.size === 0;

  const soloPruebas = [...seleccionados].every((id) =>
    puedeEditarPaciente(seleccionadosDatos.get(id) || {}));
  if (selEstadoMasivo) selEstadoMasivo.disabled = !soloPruebas;
  if (selRespuestaMasivo) selRespuestaMasivo.disabled = !soloPruebas;
  const avisoMasivo = $("#avisoMasivoPrueba");
  if (avisoMasivo) avisoMasivo.hidden = seleccionados.size === 0 || soloPruebas;

  chkTodos.checked =
    visibles.length > 0 && visibles.every((p) => seleccionados.has(p.id));
  chkTodos.disabled = visibles.length === 0;
}

// Devuelve los ids de la página actual (el "seleccionar todos" opera sobre
// la página visible, no sobre todo el filtro).
function idsSeleccionables() {
  return new Set(itemsPaginaPac(filtradosPac()).filter(puedeSeleccionarPaciente).map((p) => p.id));
}

// Mantiene la selección coherente con el filtro actual:
// - Si el check "seleccionar todos" está marcado (todoMarcado), la selección
//   se re-sincroniza para contener exactamente los pacientes del filtro actual.
function sincronizarSeleccionConFiltro() {
  const seleccionables = idsSeleccionables();

  if (todoMarcado) {
    // Re-seleccionar exactamente los del filtro actual.
    seleccionados = new Set([...seleccionables]);
    seleccionadosDatos = new Map(pacientes.filter((p) => seleccionados.has(p.id)).map((p) => [p.id, p]));
  } else {
    // Conservar solo los seleccionados que aún cumplen el filtro.
    for (const id of [...seleccionados]) {
      if (!seleccionables.has(id)) { seleccionados.delete(id); seleccionadosDatos.delete(id); }
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
    for (const id of idsSeleccionables()) { seleccionados.delete(id); seleccionadosDatos.delete(id); }
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
  const id = Number(chk.dataset.id);
  if (chk.checked) {
    seleccionados.add(id);
    const p = pacientes.find((item) => item.id === id);
    if (p) seleccionadosDatos.set(id, p);
  } else { seleccionados.delete(id); seleccionadosDatos.delete(id); }
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
    toast(nueva === "baja"
      ? mensajeBaja(paciente, "Paciente marcado como dado de baja.")
      : `Respuesta actualizada a "${nueva}".`, "ok");
    await cargarTrasCambio();
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
    await cargarTrasCambio();
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

let timerBusquedaPac = null;
buscadorEl.addEventListener("input", () => {
  filtro = buscadorEl.value;
  paginaPac = 1;
  seleccionados.clear();
  seleccionadosDatos.clear();
  todoMarcado = false;
  clearTimeout(timerBusquedaPac);
  timerBusquedaPac = setTimeout(() => cargar(), 250);
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
  seleccionados.clear();
  seleccionadosDatos.clear();
  todoMarcado = false;
  cargar();
});

window.snwConCooldown($("#btnActualizar"), () => cargar(true));

// --- Paginación: extremos, pasos, salto directo y tamaño (10–100) -----------
const pagPrimPac = $("#pagPrimPac");
const pagAntPac = $("#pagAntPac");
const pagSigPac = $("#pagSigPac");
const pagUltPac = $("#pagUltPac");
const pagIrPac = $("#pagIrPac");
const selPageSizePac = $("#selPageSizePac");
if (selPageSizePac) selPageSizePac.value = String(pageSizePac);
function irPaginaPac(valor) {
  const numero = Number(valor);
  if (!Number.isSafeInteger(numero) || numero < 1) {
    if (pagIrPac) pagIrPac.value = String(paginaPac);
    return;
  }
  const destino = Math.min(numero, totalPaginasPac(totalPacientesFiltrados));
  if (destino === paginaPac) {
    if (pagIrPac) pagIrPac.value = String(paginaPac);
    return;
  }
  paginaPac = destino;
  cargar();
}
if (pagPrimPac) pagPrimPac.addEventListener("click", () => irPaginaPac(1));
if (pagAntPac) pagAntPac.addEventListener("click", () => irPaginaPac(paginaPac - 1));
if (pagSigPac) pagSigPac.addEventListener("click", () => irPaginaPac(paginaPac + 1));
if (pagUltPac) pagUltPac.addEventListener("click", () => irPaginaPac(totalPaginasPac(totalPacientesFiltrados)));
if (pagIrPac) {
  pagIrPac.addEventListener("focus", () => pagIrPac.select());
  pagIrPac.addEventListener("change", () => irPaginaPac(pagIrPac.value));
  pagIrPac.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); irPaginaPac(pagIrPac.value); pagIrPac.blur(); }
  });
}
if (selPageSizePac) selPageSizePac.addEventListener("change", () => {
  pageSizePac = Math.min(100, Math.max(10, Number(selPageSizePac.value) || 10));
  localStorage.setItem("snw_page_size_pac", String(pageSizePac));
  paginaPac = 1;
  cargar();
});

function cargarTrasCambio() {
  resumenPacientes = null;
  consultaPacientesAnterior = null;
  return cargar();
}

// --- Edición masiva: cambiar estado o respuesta de todos los seleccionados --
const ESTADO_LABEL = { pendiente: "pendiente", enviado: "enviado", error: "error" };
const RESPUESTA_LABEL = { pendiente: "sin respuesta", respondio: "respondió", no_interesado: "no interesado", baja: "dado de baja" };

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

// --- Baja en cascada ---------------------------------------------------------
// Una baja manual se replica en otras bases solo cuando allí el número también
// figura entre los de prueba. Se informa qué bases se actualizaron.
function mensajeBaja(paciente, base) {
  const otras = (paciente && paciente.otras_bases) || [];
  if (!otras.length) return base;
  const detalle = otras
    .map((b) => `${b.nombre || b.tabla} (${b.actualizados})`)
    .join(", ");
  return `${base} También se aplicó en: ${detalle}.`;
}

function pedirConfirmacionMasiva(sel, url, campo) {
  const valor = sel.value;
  if (!valor) return;
  const ids = [...seleccionados];
  if (!ids.length) {
    sel.value = "";
    toast("Selecciona al menos un paciente primero.", "error");
    return;
  }
  if (ids.some((id) => !puedeEditarPaciente(seleccionadosDatos.get(id) || {}))) {
    sel.value = "";
    toast("Solo los números de prueba permiten cambiar estado o respuesta.", "error");
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
    // La baja manual se propaga a otras copias de prueba del mismo número.
    if (campo === "respuesta" && valor === "baja" && (data.otras_bases || []).length) {
      const detalle = data.otras_bases
        .map((b) => `${b.nombre || b.tabla} (${b.actualizados})`).join(", ");
      msg += ` También se aplicó en otras bases: ${detalle}.`;
    }
    toast(msg, data.bloqueados ? "error" : "ok");
    await cargarTrasCambio();
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
      if (res.ok) { ok += 1; seleccionados.delete(id); seleccionadosDatos.delete(id); }
      else mal += 1;
    } catch { mal += 1; }
  }
  toast(mal ? `${ok} eliminados, ${mal} fallaron.` : `${ok} registro${ok === 1 ? "" : "s"} eliminado${ok === 1 ? "" : "s"}.`, mal ? "error" : "ok");
  todoMarcado = false;
  btnEliminarMasivo.disabled = false;
  cargarTrasCambio();
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

async function errorApiPrueba(res) {
  const dato = await res.json().catch(() => ({}));
  return typeof dato.detail === "string" ? dato.detail : "No se pudo completar la acción.";
}

const formPacientePrueba = $("#formPacientePrueba");
if (formPacientePrueba) formPacientePrueba.addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!window.snwEsPrivilegiado || basePacActual() !== "dev") return;
  const boton = $("#btnCrearPacientePrueba");
  boton.disabled = true;
  try {
    const res = await fetch("api/pacientes/prueba", {
      method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({
        nombre: $("#pruebaNombre").value.trim(),
        apellido: $("#pruebaApellido").value.trim(),
        telefono: $("#pruebaTelefono").value.trim(),
      }),
    });
    if (res.status === 401) { window.snwSesionExpirada(); return; }
    if (!res.ok) throw new Error(await errorApiPrueba(res));
    formPacientePrueba.reset();
    toast("Paciente de prueba agregado. Puedes autorizar su número en la tabla.");
    await cargarTrasCambio();
  } catch (err) {
    toast(err.message || "No se pudo agregar el paciente.", "error");
  } finally {
    boton.disabled = false;
  }
});

tbodyEl.addEventListener("click", async (e) => {
  const boton = e.target.closest("[data-autorizar-id]");
  if (!boton || !tbodyEl.contains(boton) || !window.snwEsPrivilegiado || basePacActual() !== "dev") return;
  boton.disabled = true;
  try {
    const res = await fetch(`api/pacientes/prueba/${boton.dataset.autorizarId}/autorizar`, {
      method: "POST", headers: authHeaders(),
    });
    if (res.status === 401) { window.snwSesionExpirada(); return; }
    if (!res.ok) throw new Error(await errorApiPrueba(res));
    toast("Número autorizado para pruebas en desarrollo y producción.");
    await cargarTrasCambio();
  } catch (err) {
    toast(err.message || "No se pudo autorizar el número.", "error");
    boton.disabled = false;
  }
});

tbodyEl.addEventListener("click", async (e) => {
  const boton = e.target.closest("[data-quitar-prueba-id]");
  if (!boton || !tbodyEl.contains(boton) || !window.snwEsPrivilegiado || basePacActual() !== "dev") return;
  if (!confirm("¿Quitar este número de las listas de prueba de desarrollo y producción? El paciente permanecerá en la base de desarrollo.")) return;
  boton.disabled = true;
  try {
    const res = await fetch(`api/pacientes/prueba/${boton.dataset.quitarPruebaId}/autorizar`, {
      method: "DELETE", headers: authHeaders(),
    });
    if (res.status === 401) { window.snwSesionExpirada(); return; }
    if (!res.ok) throw new Error(await errorApiPrueba(res));
    toast("Número quitado de las listas de prueba. El paciente permanece en desarrollo.");
    await cargarTrasCambio();
  } catch (err) {
    toast(err.message || "No se pudo quitar el número de prueba.", "error");
    boton.disabled = false;
  }
});

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
    seleccionadosDatos.clear();
    todoMarcado = false;
    localStorage.removeItem("snw_esp_pacientes");
    await cargarBasesPac();
    cargarTrasCambio();
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
    cargarTrasCambio();
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
const csvMapeo = $("#csvMapeo");
const csvMapError = $("#csvMapError");
const csvSelectores = [...document.querySelectorAll("[data-csv-campo]")];
let archivoElegido = null;
let csvEncabezados = [];

function mapeoCsvSeleccionado() {
  return Object.fromEntries(csvSelectores.filter((select) => select.value)
    .map((select) => [select.dataset.csvCampo, select.value]));
}

function actualizarMapeoCsv() {
  if (!csvMapeo || csvMapeo.hidden || !csvEncabezados.length) return;
  csvMapError.textContent = window.snwCsvChunks.validarMapeoCsv(
    mapeoCsvSeleccionado(), csvEncabezados);
  aplicarModoBase();
}

for (const select of csvSelectores) select.addEventListener("change", actualizarMapeoCsv);

if (csvInput) {
  csvInput.addEventListener("change", async () => {
    const f = csvInput.files && csvInput.files[0];
    archivoElegido = f || null;
    csvEncabezados = [];
    csvMapeo.hidden = !f;
    csvMapError.textContent = f ? "Leyendo columnas del archivo…" : "";
    if (csvResultado) csvResultado.textContent = "";
    if (nombreArchivo) {
      nombreArchivo.textContent = f
        ? `${f.name} (${(f.size / 1024).toFixed(1)} KB)`
        : "No se ha seleccionado ningún archivo";
    }
    aplicarModoBase();
    if (!f) return;
    try {
      const encabezados = await window.snwCsvChunks.leerEncabezadosCsv(f);
      if (archivoElegido !== f) return;
      csvEncabezados = encabezados;
      const sugeridos = window.snwCsvChunks.sugerirMapeoCsv(encabezados);
      for (const select of csvSelectores) {
        select.replaceChildren(new Option(
          ["nombre", "apellido", "telefono"].includes(select.dataset.csvCampo)
            ? "— Seleccione columna —" : "— No importar —", ""));
        for (const encabezado of encabezados) {
          select.add(new Option(encabezado, encabezado));
        }
        select.value = sugeridos[select.dataset.csvCampo] || "";
      }
      actualizarMapeoCsv();
    } catch (err) {
      if (archivoElegido !== f) return;
      csvMapError.textContent = err.message || "No se pudieron leer las columnas del CSV.";
      aplicarModoBase();
    }
  });
}

function acumularInformeCsv(total, parcial) {
  for (const campo of ["procesados", "insertados", "duplicados", "rechazados", "actualizados",
    "propagados", "omitidos_candado", "omitidos_eliminados"]) {
    total[campo] = (total[campo] || 0) + (parcial[campo] || 0);
  }
  for (const base of parcial.bases || []) {
    const previa = total.bases.find((b) => b.tabla === base.tabla);
    if (previa) previa.actualizados += base.actualizados || 0;
    else total.bases.push({ ...base });
  }
  total.errores.push(...(parcial.errores || []).slice(0, Math.max(0, 20 - total.errores.length)));
}

if (btnSubirCsv) btnSubirCsv.addEventListener("click", async () => {
  if (!window.snwPuede?.("csv_subir")) return toast("No tienes permiso para subir CSV.", "error");
  const esp = espPacId();
  if (esp == null) return toast("Elige un área como base de datos.", "error");
  if (!archivoElegido) return toast("Elige un archivo CSV.", "error");
  const mapeo = mapeoCsvSeleccionado();
  const errorMapeo = window.snwCsvChunks.validarMapeoCsv(mapeo, csvEncabezados);
  if (csvMapeo.hidden || errorMapeo) return toast(errorMapeo || "Revisa las columnas del CSV.", "error");
  const archivo = archivoElegido;
  const informeTotal = { bases: [], errores: [] };
  let bloquesCompletados = 0;
  const baseEstabaDeshabilitada = !!selBasePac?.disabled;
  btnSubirCsv.disabled = true;
  csvInput.disabled = true;
  if (selBasePac) selBasePac.disabled = true;
  if (csvResultado) csvResultado.textContent = "Preparando el CSV para subirlo por partes…";
  try {
    for await (const bloque of window.snwCsvChunks.fragmentarCsv(archivo)) {
      if (csvResultado) csvResultado.textContent =
        `Subiendo parte ${bloquesCompletados + 1}… ${informeTotal.procesados || 0} filas procesadas.`;
      const datos = new FormData();
      datos.append("archivo", new Blob([bloque.contenido], { type: "text/csv;charset=utf-8" }), archivo.name);
      datos.append("mapeo_csv", JSON.stringify(mapeo));
      const res = await fetch(`api/areas/${esp}/pacientes/csv?fila_inicial=${bloque.filaInicial}`, {
        method: "POST", headers: authHeaders(), body: datos,
      });
      const cuerpo = await res.json().catch(() => ({}));
      if (res.status === 401) { window.snwSesionExpirada(); return; }
      if (res.status === 403) throw new Error("No tienes acceso a esta área.");
      if (!res.ok) {
        const det = cuerpo.detail;
        throw new Error((det && det.columnas) ||
          (typeof det === "string" ? det : `Error ${res.status}`));
      }
      acumularInformeCsv(informeTotal, cuerpo.informe || {});
      bloquesCompletados++;
    }
    const inf = informeTotal;
    const errores = inf.errores || [];
    const mostrados = errores.slice(0, 20);
    if (csvResultado) {
      csvResultado.innerHTML =
        `<p>CSV cargado en ${bloquesCompletados} parte(s). <strong>${inf.insertados ?? 0}</strong> insertados de ` +
        `${inf.procesados ?? 0} procesados ` +
        `(${inf.duplicados ?? 0} ya estaban, ${inf.rechazados ?? 0} rechazados` +
        `${inf.actualizados ? `, ${inf.actualizados} actualizados` : ""}` +
        `${inf.omitidos_eliminados ? `, ${inf.omitidos_eliminados} excluidos por eliminación` : ""}).</p>` +
        ((inf.bases || []).length
          ? `<p>También se aplicó la respuesta/estado del CSV al mismo número en otras bases: ` +
            `${inf.bases.map((b) => `${escaparHtml(b.nombre || b.tabla)} (${b.actualizados})`).join(", ")}.` +
            (inf.omitidos_candado
              ? ` <span class="field__hint">${inf.omitidos_candado} bajas con candado ` +
                `se respetaron y no se reactivaron.</span>` : "") +
            `</p>`
          : (inf.omitidos_candado
            ? `<p class="field__hint">${inf.omitidos_candado} bajas con candado se respetaron ` +
              `y no se reactivaron.</p>` : "")) +
        (mostrados.length
          ? `<ul>` + mostrados.map((e) => `<li>fila ${e.fila}: ${escaparHtml(e.motivo)}</li>`).join("") + `</ul>` +
            ((inf.rechazados || 0) > mostrados.length
              ? `<p class="field__hint">…y ${inf.rechazados - mostrados.length} más.</p>` : "")
          : "");
    }
    toast("Base de datos cargada.", "ok");
    csvInput.value = "";
    archivoElegido = null;
    csvEncabezados = [];
    csvMapeo.hidden = true;
    csvMapError.textContent = "";
    if (nombreArchivo) nombreArchivo.textContent = "No se ha seleccionado ningún archivo";
    cargarTrasCambio();
  } catch (err) {
    console.error("[pacientes.js btnSubirCsv]", err);
    if (csvResultado) csvResultado.textContent = bloquesCompletados
      ? `Carga interrumpida tras ${bloquesCompletados} parte(s): ${informeTotal.insertados || 0} pacientes insertados. Puedes reintentar el archivo; los pacientes ya importados no se duplicarán.`
      : "No se cargó ninguna parte del CSV.";
    if (bloquesCompletados) cargarTrasCambio();
    toast(err.message || "No se pudo cargar el archivo.", "error");
  } finally {
    if (selBasePac) selBasePac.disabled = baseEstabaDeshabilitada;
    aplicarModoBase();
  }
});
})();
