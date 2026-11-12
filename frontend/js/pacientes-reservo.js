(function () {
  const $ = (selector) => document.querySelector(selector);
  const token = () => localStorage.getItem("snw_token") || "";
  const headers = (extra = {}) => ({ Authorization: `Bearer ${token()}`, ...extra });
  const escapar = (valor) => {
    const nodo = document.createElement("span");
    nodo.textContent = valor == null ? "" : String(valor);
    return nodo.innerHTML;
  };

  const ui = {
    clinic: $("#reservoClinica"), from: $("#reservoDesde"), to: $("#reservoHasta"),
    form: $("#formSincronizarReservo"), searchTreatments: $("#btnBuscarTratamientos"),
    treatments: $("#reservoTratamientos"), start: $("#btnIniciarReservo"), notice: $("#reservoAviso"),
    tools: $("#reservoHerramientas"), treatmentSearch: $("#reservoBuscarTratamiento"),
    selectedCount: $("#reservoSeleccionados"), selectAll: $("#reservoSeleccionarTodos"),
    clearSelection: $("#reservoLimpiarSeleccion"),
    syncs: $("#reservoSincronizaciones"), patients: $("#reservoPacientes"), total: $("#reservoTotal"),
    modal: $("#reservoModalBase"), modalName: $("#reservoNombrePersonalizado"),
    modalPreview: $("#reservoPreviewBase"), modalSave: $("#reservoModalGuardar"),
    currentQuery: $("#reservoFiltroConsulta"), showAll: $("#reservoMostrarTodos"),
    clinicFilter: $("#filtroReservoClinica"), treatmentFilter: $("#filtroReservoTratamiento"),
    fromFilter: $("#filtroReservoDesde"), toFilter: $("#filtroReservoHasta"), search: $("#filtroReservoBuscar"),
    pageSize: $("#reservoPorPagina"), page: $("#reservoPaginaIr"), pageInfo: $("#reservoPaginaInfo"),
    syncPageSize: $("#reservoHistPorPagina"), syncPage: $("#reservoHistPaginaIr"),
    syncPageInfo: $("#reservoHistPaginaInfo"),
  };
  let clinics = [];
  let page = 1;
  let pages = 1;
  let syncPage = 1;
  let syncPages = 1;
  let syncRequest = 0;
  let selectedSyncId = null;
  let pollTimer = null;
  let searchTimer = null;
  let confirmacionActiva = null;
  let treatmentsByUuid = new Map();
  let loadingTreatments = false;
  let guardarJobId = null;
  let previewTimer = null;
  let previewVersion = 0;

  async function request(url, options = {}) {
    const response = await fetch(url, { ...options, headers: headers(options.headers || {}), cache: "no-store" });
    const cuerpo = await response.text().catch(() => "");
    let data = {};
    try { data = cuerpo ? JSON.parse(cuerpo) : {}; } catch { /* Respuesta no JSON, como una página de proxy. */ }
    if (response.status === 401) { window.snwSesionExpirada(); throw new Error("La sesión expiró."); }
    if (!response.ok) {
      const detalle = typeof data.detail === "string" ? data.detail
        : Array.isArray(data.detail) ? data.detail.map((item) => item.msg || "").filter(Boolean).join("; ")
        : typeof data.message === "string" ? data.message : "";
      const estado = `HTTP ${response.status}${response.statusText ? ` ${response.statusText}` : ""}`;
      const respaldo = [502, 503, 504].includes(response.status)
        ? `El servidor o el proxy interrumpió la solicitud (${estado}). Revisa los logs del backend.`
        : `SNW respondió con un error (${estado}).`;
      throw new Error(detalle || respaldo);
    }
    return data;
  }

  function mostrarAviso(texto, tipo = "info") {
    ui.notice.textContent = texto;
    ui.notice.className = `reservo-aviso reservo-aviso--${tipo}`;
  }

  function actualizarClinica() {
    const clinic = clinics.find((item) => item.codigo === ui.clinic.value);
    const seleccionados = ui.treatments.querySelectorAll("[data-tratamiento]:checked").length;
    const estadoListo = clinic?.estado_atendido_codigo && clinic?.estado_atendido_verificado;
    ui.selectedCount.textContent = `${seleccionados} seleccionado${seleccionados === 1 ? "" : "s"}`;
    ui.searchTreatments.disabled = loadingTreatments || !clinic?.token_disponible || !ui.from.value || !ui.to.value;
    ui.start.disabled = loadingTreatments || !clinic?.token_disponible || !estadoListo || !seleccionados;
    if (!clinic?.token_disponible) {
      mostrarAviso("Configura el token API de esta clínica en Configuración → Integraciones.", "warning");
    } else if (!estadoListo) {
      mostrarAviso("Sincronización bloqueada: primero confirma el código «Atendido» a partir de una cita real en Configuración → Integraciones.", "warning");
    } else if (!seleccionados) {
      mostrarAviso("Consulta los tratamientos del rango y selecciona uno o varios por nombre.", "info");
    } else {
      mostrarAviso(`${seleccionados} tratamiento(s) seleccionado(s). Se consultarán las citas con el estado verificado.`, "info");
    }
  }

  function limpiarTratamientos(mensaje = "Selecciona la clínica y el rango, luego consulta los tratamientos.") {
    treatmentsByUuid = new Map();
    ui.tools.hidden = true;
    ui.treatmentSearch.value = "";
    ui.treatments.classList.remove("reservo-tratamientos--cargando");
    ui.treatments.textContent = mensaje;
    actualizarClinica();
  }

  async function consultarTratamientos() {
    const criterio = `${ui.clinic.value}|${ui.from.value}|${ui.to.value}`;
    loadingTreatments = true;
    actualizarClinica();
    ui.tools.hidden = true;
    ui.treatments.classList.add("reservo-tratamientos--cargando");
    ui.treatments.textContent = "Consultando tratamientos en Reservo…";
    try {
      const data = await request("api/reservo/tratamientos", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ clinica_codigo: ui.clinic.value,
          fecha_desde: ui.from.value, fecha_hasta: ui.to.value }),
      });
      if (criterio !== `${ui.clinic.value}|${ui.from.value}|${ui.to.value}`) return;
      const tratamientos = data.items || [];
      if (!tratamientos.length) {
        limpiarTratamientos("Reservo no devolvió tratamientos para ese rango.");
        return;
      }
      treatmentsByUuid = new Map(tratamientos.map((item) => [String(item.uuid), String(item.nombre)]));
      ui.treatments.classList.remove("reservo-tratamientos--cargando");
      ui.tools.hidden = false;
      ui.treatments.innerHTML = tratamientos.map((item) =>
        `<label class="reservo-tratamiento"><input type="checkbox" data-tratamiento ` +
        `value="${escapar(item.uuid)}">` +
        `<span>${escapar(item.nombre)}</span></label>`).join("");
      actualizarClinica();
    } catch (error) {
      limpiarTratamientos(error.message || "No se pudieron consultar los tratamientos.");
    } finally {
      loadingTreatments = false;
      actualizarClinica();
    }
  }

  function tratamientosSeleccionados() {
    return [...ui.treatments.querySelectorAll("[data-tratamiento]:checked")]
      .map((entrada) => ({ uuid: entrada.value, nombre: treatmentsByUuid.get(entrada.value) || "" }));
  }

  async function cargarConfiguracion() {
    const data = await request("api/reservo/configuracion");
    clinics = data.clinicas || [];
    const anterior = ui.clinic.value;
    ui.clinic.replaceChildren(...clinics.map((item) => new Option(`${item.nombre} (${item.codigo})`, item.codigo)));
    ui.clinicFilter.replaceChildren(new Option("Todas", ""), ...clinics.map((item) => new Option(item.nombre, item.codigo)));
    if (clinics.some((item) => item.codigo === anterior)) ui.clinic.value = anterior;
    actualizarClinica();
  }

  function formatoFecha(valor, soloFecha = false) {
    if (!valor) return "—";
    const fecha = new Date(String(valor).replace(" ", "T") + (String(valor).includes("Z") ? "" : "Z"));
    return Number.isNaN(fecha.getTime()) ? String(valor) : fecha.toLocaleString("es-CL", {
      dateStyle: "short", ...(soloFecha ? {} : { timeStyle: "short" }), timeZone: "America/Santiago",
    });
  }

  function estadoClase(estado) {
    if (estado === "completado") return "reservo-estado--ok";
    if (estado === "completado_con_advertencias") return "reservo-estado--warning";
    if (estado === "error") return "reservo-estado--error";
    if (estado === "deshecho") return "reservo-estado--deshecho";
    return "reservo-estado--proceso";
  }

  function estadoTexto(estado) {
    return ({ pendiente: "Pendiente", ejecutando: "En curso", completado: "Completado",
      completado_con_advertencias: "Con advertencias", error: "Error",
      deshecho: "Deshecho" })[estado] || estado || "—";
  }

  function ubicarConfirmacion() {
    if (!confirmacionActiva) return;
    const { panel, boton } = confirmacionActiva;
    if (!boton.isConnected) { cerrarConfirmacion(false); return; }
    const origen = boton.getBoundingClientRect();
    const ancho = panel.getBoundingClientRect().width;
    const alto = panel.getBoundingClientRect().height;
    const margen = 12;
    const arriba = origen.bottom + alto + margen > window.innerHeight && origen.top > alto + margen;
    const posicionVertical = arriba ? origen.top - alto - 8 : origen.bottom + 8;
    panel.style.left = `${Math.max(margen, Math.min(origen.left, window.innerWidth - ancho - margen))}px`;
    panel.style.top = `${Math.max(margen, Math.min(posicionVertical, window.innerHeight - alto - margen))}px`;
  }

  function cerrarConfirmacion(restaurarFoco = true) {
    if (!confirmacionActiva) return;
    const { panel, boton } = confirmacionActiva;
    document.removeEventListener("pointerdown", cerrarConfirmacionExterior);
    document.removeEventListener("keydown", cerrarConfirmacionEscape);
    window.removeEventListener("scroll", ubicarConfirmacion, true);
    window.removeEventListener("resize", ubicarConfirmacion);
    panel.remove();
    confirmacionActiva = null;
    if (restaurarFoco && boton.isConnected) boton.focus();
  }

  function cerrarConfirmacionExterior(evento) {
    if (confirmacionActiva && !confirmacionActiva.panel.contains(evento.target) &&
        !confirmacionActiva.boton.contains(evento.target)) cerrarConfirmacion(false);
  }

  function cerrarConfirmacionEscape(evento) {
    if (evento.key === "Escape") cerrarConfirmacion();
  }

  function mostrarConfirmacion(boton) {
    if (confirmacionActiva?.boton === boton) { cerrarConfirmacion(); return; }
    cerrarConfirmacion(false);
    const panel = document.createElement("div");
    panel.className = "reservo-confirmacion";
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-labelledby", "reservoConfirmarTitulo");
    panel.innerHTML = '<strong id="reservoConfirmarTitulo">¿Deshacer esta búsqueda?</strong>' +
      '<p>Se quitarán los pacientes exclusivos de las búsquedas deshechas. Los de búsquedas vigentes se conservarán.</p>' +
      '<div class="reservo-confirmacion__acciones">' +
      '<button type="button" class="btn btn--ghost" data-volver>Volver</button>' +
      '<button type="button" class="btn btn--danger" data-confirmar>Confirmar deshacer</button></div>';
    document.body.append(panel);
    confirmacionActiva = { panel, boton };
    ubicarConfirmacion();
    document.addEventListener("pointerdown", cerrarConfirmacionExterior);
    document.addEventListener("keydown", cerrarConfirmacionEscape);
    window.addEventListener("scroll", ubicarConfirmacion, true);
    window.addEventListener("resize", ubicarConfirmacion);
    panel.querySelector("[data-volver]").addEventListener("click", () => cerrarConfirmacion());
    const confirmar = panel.querySelector("[data-confirmar]");
    confirmar.addEventListener("click", () => deshacer(boton.dataset.deshacer, confirmar));
    confirmar.focus();
  }

  async function deshacer(id, boton) {
    boton.disabled = true;
    try {
      const resultado = await request(`api/reservo/sincronizaciones/${id}/deshacer`, { method: "POST" });
      mostrarAviso(`Búsqueda deshecha: ${Number(resultado.pacientes_eliminados || 0)} pacientes exclusivos quitados. Los compartidos con otras búsquedas se conservaron.`, "success");
      cerrarConfirmacion(false);
      await Promise.all([cargarSincronizaciones(), cargarPacientes()]);
    } catch (error) {
      mostrarAviso(error.message || "No se pudo deshacer la búsqueda.", "error");
      boton.disabled = false;
    }
  }

  async function reintentar(id, boton) {
    boton.disabled = true;
    try {
      await request(`api/reservo/sincronizaciones/${id}/reintentar`, { method: "POST" });
      mostrarAviso("Se creó una nueva sincronización. Los pacientes se deduplicarán por clínica y UUID de Reservo.", "success");
      await cargarSincronizaciones();
      iniciarSondeo();
    } catch (error) {
      mostrarAviso(error.message, "error");
    } finally {
      boton.disabled = false;
    }
  }

  async function prepararVistaPreviaBase() {
    const version = ++previewVersion;
    const nombre = ui.modalName.value.trim();
    ui.modalSave.disabled = true;
    if (!guardarJobId || !nombre) {
      ui.modalPreview.textContent = "Escribe un nombre para preparar la vista previa.";
      return;
    }
    ui.modalPreview.innerHTML = '<span class="reservo-cargando-punto" aria-hidden="true"></span> Preparando vista previa…';
    try {
      const data = await request(`api/reservo/sincronizaciones/${guardarJobId}/bases/previsualizar`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ nombre_personalizado: nombre }),
      });
      if (version !== previewVersion) return;
      ui.modalPreview.innerHTML =
        `<strong class="reservo-preview__nombre">${escapar(data.nombre)}</strong>` +
        `<dl><div><dt>Clínica</dt><dd>${escapar(data.clinica_nombre)} (${escapar(data.clinica_codigo)})</dd></div>` +
        `<div><dt>Tratamiento</dt><dd>${escapar(data.tratamiento_nombre)}</dd></div>` +
        `<div><dt>Período consultado</dt><dd>${escapar(data.fecha_desde)} – ${escapar(data.fecha_hasta)}</dd></div>` +
        `<div><dt>Pacientes en la consulta</dt><dd>${Number(data.total).toLocaleString("es-CL")}</dd></div>` +
        `<div><dt>Pacientes que se guardarán (teléfono chileno válido)</dt><dd>${Number(data.telefonos_validos).toLocaleString("es-CL")}</dd></div>` +
        `<div><dt>Sin teléfono</dt><dd>${Number(data.sin_telefono).toLocaleString("es-CL")}</dd></div>` +
        `<div><dt>Teléfono no válido para Chile</dt><dd>${Number(data.telefonos_invalidos).toLocaleString("es-CL")}</dd></div></dl>`;
      ui.modalSave.disabled = !Number(data.telefonos_validos);
    } catch (error) {
      if (version !== previewVersion) return;
      ui.modalPreview.innerHTML = `<span class="reservo-vacio--error">${escapar(error.message)}</span>`;
    }
  }

  function abrirModalGuardar(jobId) {
    guardarJobId = Number(jobId);
    ui.modal.hidden = false;
    ui.modalName.value = "pacientes_tratamiento";
    ui.modalPreview.textContent = "Escribe un nombre para preparar la vista previa.";
    ui.modalSave.disabled = true;
    prepararVistaPreviaBase();
    ui.modalName.focus();
  }

  function cerrarModalGuardar() {
    ui.modal.hidden = true;
    guardarJobId = null;
    previewVersion++;
  }

  async function guardarBaseReservo() {
    if (!guardarJobId || !ui.modalName.value.trim()) return;
    ui.modalSave.disabled = true;
    try {
      const creada = await request(`api/reservo/sincronizaciones/${guardarJobId}/bases`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ nombre_personalizado: ui.modalName.value.trim() }),
      });
      cerrarModalGuardar();
      const omitidos = Number(creada.telefonos_excluidos || 0);
      mostrarAviso(`Base «${creada.nombre}»: ${Number(creada.total).toLocaleString("es-CL")} pacientes guardados; ${omitidos.toLocaleString("es-CL")} omitidos por teléfono inválido o ausente. Ya puedes seleccionarla en Base de datos → Pacientes.`, "success");
    } catch (error) {
      ui.modalPreview.innerHTML = `<span class="reservo-vacio--error">${escapar(error.message)}</span>`;
      ui.modalSave.disabled = false;
    }
  }

  async function cargarSincronizaciones() {
    const secuencia = ++syncRequest;
    const params = new URLSearchParams({ pagina: String(syncPage), limite: ui.syncPageSize.value });
    const data = await request(`api/reservo/sincronizaciones?${params}`);
    if (secuencia !== syncRequest) return null;
    syncPage = data.pagina;
    syncPages = data.paginas;
    if (document.activeElement !== ui.syncPage) ui.syncPage.value = String(syncPage);
    ui.syncPage.max = String(syncPages);
    const primero = (syncPage - 1) * data.por_pagina + 1;
    const ultimo = Math.min(syncPage * data.por_pagina, data.total);
    const info = data.total ? `${primero}–${ultimo} de ${data.total.toLocaleString("es-CL")}` : "Sin sincronizaciones";
    if (ui.syncPageInfo.textContent !== info) ui.syncPageInfo.textContent = info;
    $("#reservoHistPrimera").disabled = $("#reservoHistAnterior").disabled = syncPage <= 1;
    $("#reservoHistSiguiente").disabled = $("#reservoHistUltima").disabled = syncPage >= syncPages;
    if (data.activo) iniciarSondeo();
    cerrarConfirmacion(false);
    const filas = data.items || [];
    if (!filas.length) {
      ui.syncs.innerHTML = '<tr><td colspan="6" class="reservo-vacio">Todavía no hay sincronizaciones.</td></tr>';
      return data.activo;
    }
    ui.syncs.innerHTML = filas.map((item) => {
      const periodo = `${escapar(item.fecha_desde)} – ${escapar(item.fecha_hasta)}`;
      const conteos = `${Number(item.periodos_completados || 0)}/${Number(item.periodos_total || 0)} tramos · ${Number(item.paginas_consultadas || 0)} páginas · ${Number(item.citas_recibidas || 0)} citas`;
      const progreso = item.estado === "completado" || item.estado === "completado_con_advertencias"
        ? 100 : Math.min(100, Math.round(100 * Number(item.periodos_completados || 0) / Math.max(1, Number(item.periodos_total || 0))));
      const pacientes = `${Number(item.pacientes_unicos || 0)} únicos · ${Number(item.pacientes_nuevos || 0)} nuevos · ${Number(item.pacientes_actualizados || 0)} actualizados`;
      const reintentarBoton = ["error", "completado_con_advertencias"].includes(item.estado)
        ? `<button type="button" class="btn btn--ghost reservo-reintentar" data-reintentar="${Number(item.id)}"><i class="fa-solid fa-rotate-right" aria-hidden="true"></i> Reintentar</button>` : "";
      const deshacerBoton = ["completado", "completado_con_advertencias", "error"].includes(item.estado)
        ? `<button type="button" class="btn btn--danger-ghost" data-deshacer="${Number(item.id)}"><i class="fa-solid fa-rotate-left" aria-hidden="true"></i> Deshacer búsqueda</button>` : "";
      const accion = item.estado === "deshecho"
        ? `<small>${Number(item.pacientes_revertidos || 0)} pacientes quitados</small>`
        : `<div class="reservo-acciones">${["completado", "completado_con_advertencias"].includes(item.estado)
          ? `<button type="button" class="btn btn--primary" data-guardar-base="${Number(item.id)}"><i class="fa-solid fa-database" aria-hidden="true"></i> Guardar como base</button>` +
            `<button type="button" class="btn btn--ghost" data-descargar-csv="${Number(item.id)}"><i class="fa-solid fa-download" aria-hidden="true"></i> Descargar CSV</button>` : ""}${reintentarBoton}${deshacerBoton}</div>`;
      return `<tr><td><strong>${escapar(item.clinica_nombre)}</strong><br><small>${periodo}</small></td>` +
        `<td>${escapar(item.tratamiento_nombre || "Tratamiento histórico")}</td>` +
        `<td><span class="reservo-estado ${estadoClase(item.estado)}">${estadoTexto(item.estado)}</span></td>` +
        `<td><div class="reservo-progreso" role="progressbar" aria-label="Tramos completados" aria-valuenow="${progreso}" aria-valuemin="0" aria-valuemax="100"><span style="width:${progreso}%"></span></div><small>${conteos}</small>${item.ultimo_error ? `<p class="reservo-error-detalle">${escapar(item.ultimo_error)}</p>` : ""}</td>` +
        `<td><strong>${pacientes}</strong><br><small>${Number(item.citas_atendidas || 0)} atendidas · ${Number(item.citas_tratamiento || 0)} con tratamiento · ${Number(item.pacientes_duplicados || 0)} duplicados</small></td>` +
        `<td>${accion}</td></tr>`;
    }).join("");
    ui.syncs.querySelectorAll("[data-reintentar]").forEach((boton) =>
      boton.addEventListener("click", () => reintentar(boton.dataset.reintentar, boton)));
    ui.syncs.querySelectorAll("[data-deshacer]").forEach((boton) =>
      boton.addEventListener("click", () => mostrarConfirmacion(boton)));
    ui.syncs.querySelectorAll("[data-guardar-base]").forEach((boton) =>
      boton.addEventListener("click", () => abrirModalGuardar(boton.dataset.guardarBase)));
    ui.syncs.querySelectorAll("[data-descargar-csv]").forEach((boton) =>
      boton.addEventListener("click", async () => {
        const id = Number(boton.dataset.descargarCsv);
        boton.disabled = true;
        try {
          const respuesta = await fetch(`api/reservo/sincronizaciones/${id}/pacientes.csv`, {
            headers: headers(), cache: "no-store",
          });
          if (respuesta.status === 401) { window.snwSesionExpirada(); return; }
          if (!respuesta.ok) {
            const detalle = await respuesta.json().catch(() => ({}));
            throw new Error(detalle.detail || "No se pudo descargar el CSV.");
          }
          const url = URL.createObjectURL(await respuesta.blob());
          const enlace = document.createElement("a");
          enlace.href = url;
          enlace.download = `reservo-consulta-${id}.csv`;
          document.body.appendChild(enlace);
          enlace.click();
          enlace.remove();
          setTimeout(() => URL.revokeObjectURL(url), 60000);
          mostrarAviso("CSV descargado con los pacientes que tienen teléfono chileno válido.", "success");
        } catch (error) {
          mostrarAviso(error.message || "No se pudo descargar el CSV.", "error");
        } finally {
          boton.disabled = false;
        }
      }));
    return data.activo;
  }

  function irPaginaHistorial(destino) {
    syncPage = destino;
    ui.syncPage.value = String(destino);
    cargarSincronizaciones().catch((error) => mostrarAviso(error.message, "error"));
  }

  function iniciarSondeo() {
    if (pollTimer) return;
    pollTimer = setInterval(async () => {
      try {
        const activo = await cargarSincronizaciones();
        if (activo === false) {
          clearInterval(pollTimer);
          pollTimer = null;
          await cargarPacientes();
        }
      } catch (error) {
        console.error("[pacientes-reservo.js sondeo]", error);
      }
    }, 5000);
  }

  function parametrosPacientes() {
    const params = new URLSearchParams({ pagina: String(page), por_pagina: ui.pageSize.value });
    if (selectedSyncId) params.set("sincronizacion_id", String(selectedSyncId));
    if (ui.clinicFilter.value) params.set("clinica", ui.clinicFilter.value);
    if (ui.treatmentFilter.value.trim()) params.set("tratamiento", ui.treatmentFilter.value.trim());
    if (ui.fromFilter.value) params.set("desde", ui.fromFilter.value);
    if (ui.toFilter.value) params.set("hasta", ui.toFilter.value);
    if (ui.search.value.trim()) params.set("q", ui.search.value.trim());
    return params;
  }

  async function cargarPacientes() {
    ui.patients.innerHTML = '<tr><td colspan="8" class="reservo-vacio">Cargando pacientes…</td></tr>';
    try {
      const data = await request(`api/reservo/pacientes?${parametrosPacientes()}`);
      pages = Math.max(1, Number(data.paginas || 1));
      page = Math.min(page, pages);
      ui.total.textContent = `${Number(data.total || 0).toLocaleString("es-CL")} paciente(s)`;
      ui.pageInfo.textContent = data.total
        ? `${(page - 1) * Number(data.por_pagina) + 1}–${Math.min(page * Number(data.por_pagina), data.total)} de ${Number(data.total).toLocaleString("es-CL")}`
        : "Sin resultados";
      ui.page.value = String(page);
      ["reservoPrimera", "reservoAnterior"].forEach((id) => { $(`#${id}`).disabled = page <= 1; });
      ["reservoSiguiente", "reservoUltima"].forEach((id) => { $(`#${id}`).disabled = page >= pages; });
      if (!(data.items || []).length) {
        const mensaje = selectedSyncId && ui.syncs.querySelector(".reservo-estado--proceso")
          ? "La consulta está en curso. Los pacientes aparecerán aquí conforme se procesen los tramos."
          : "No hay pacientes para estos filtros.";
        ui.patients.innerHTML = `<tr><td colspan="8" class="reservo-vacio">${mensaje}</td></tr>`;
        return;
      }
      ui.patients.innerHTML = data.items.map((item) =>
        `<tr><td><strong>${escapar(item.paciente_nombre || "—")}</strong></td>` +
        `<td>${escapar(item.paciente_apellido || "—")}</td>` +
        `<td>${escapar(item.paciente_telefono || "—")}</td><td>${escapar(item.clinica_nombre)}</td>` +
        `<td><code>${escapar(item.cliente_uuid_parcial)}</code></td>` +
        `<td>${escapar(item.tratamientos_nombre || "—")}</td>` +
        `<td>${escapar(formatoFecha(item.fecha_ultima_atencion, true))}</td>` +
        `<td>${escapar(formatoFecha(item.ultima_sincronizacion_en))}</td></tr>`).join("");
    } catch (error) {
      ui.patients.innerHTML = `<tr><td colspan="8" class="reservo-vacio reservo-vacio--error">${escapar(error.message)}</td></tr>`;
    }
  }

  ui.clinic.addEventListener("change", () => limpiarTratamientos());
  ui.from.addEventListener("change", () => limpiarTratamientos());
  ui.to.addEventListener("change", () => limpiarTratamientos());
  ui.treatments.addEventListener("change", (evento) => {
    if (evento.target.matches("[data-tratamiento]") &&
        ui.treatments.querySelectorAll("[data-tratamiento]:checked").length > 50) {
      evento.target.checked = false;
      actualizarClinica();
      mostrarAviso("Puedes seleccionar hasta 50 tratamientos por consulta.", "warning");
      return;
    }
    actualizarClinica();
  });
  ui.treatmentSearch.addEventListener("input", () => {
    const query = ui.treatmentSearch.value.trim().toLocaleLowerCase("es");
    ui.treatments.querySelectorAll(".reservo-tratamiento").forEach((row) => {
      row.hidden = !row.textContent.toLocaleLowerCase("es").includes(query);
    });
  });
  ui.selectAll.addEventListener("click", () => {
    const entries = [...ui.treatments.querySelectorAll("[data-tratamiento]")];
    entries.forEach((entry, index) => { entry.checked = index < 50; });
    actualizarClinica();
    if (entries.length > 50) mostrarAviso("Puedes seleccionar hasta 50 tratamientos por consulta.", "warning");
  });
  ui.clearSelection.addEventListener("click", () => {
    ui.treatments.querySelectorAll("[data-tratamiento]").forEach((entry) => { entry.checked = false; });
    actualizarClinica();
  });
  ui.searchTreatments.addEventListener("click", consultarTratamientos);
  ui.form.addEventListener("submit", async (evento) => {
    evento.preventDefault();
    ui.start.disabled = true;
    try {
      const trabajo = await request("api/reservo/sincronizaciones", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ clinica_codigo: ui.clinic.value, fecha_desde: ui.from.value,
          fecha_hasta: ui.to.value, tratamientos: tratamientosSeleccionados() }),
      });
      selectedSyncId = Number(trabajo.id) || null;
      ui.currentQuery.hidden = !selectedSyncId;
      ui.clinicFilter.value = "";
      ui.treatmentFilter.value = "";
      ui.fromFilter.value = "";
      ui.toFilter.value = "";
      ui.search.value = "";
      page = 1;
      syncPage = 1;
      await cargarSincronizaciones();
      await cargarPacientes();
      mostrarAviso("Consulta iniciada. El progreso se actualizará aquí; no se enviará ningún WhatsApp.", "success");
      iniciarSondeo();
    } catch (error) {
      mostrarAviso(error.message, "error");
    } finally {
      actualizarClinica();
    }
  });
  ui.showAll.addEventListener("click", () => {
    selectedSyncId = null;
    ui.currentQuery.hidden = true;
    page = 1;
    cargarPacientes();
  });
  $("#btnActualizarReservo").addEventListener("click", async () => {
    try { await Promise.all([cargarConfiguracion(), cargarSincronizaciones(), cargarPacientes()]); }
    catch (error) { mostrarAviso(error.message, "error"); }
  });
  $("#btnFiltrarReservo").addEventListener("click", () => { page = 1; cargarPacientes(); });
  ui.search.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { page = 1; cargarPacientes(); }, 350);
  });
  ui.pageSize.addEventListener("change", () => { page = 1; cargarPacientes(); });
  ui.syncPageSize.addEventListener("change", () => irPaginaHistorial(1));
  $("#reservoHistPrimera").addEventListener("click", () => irPaginaHistorial(1));
  $("#reservoHistAnterior").addEventListener("click", () => irPaginaHistorial(Math.max(1, syncPage - 1)));
  $("#reservoHistSiguiente").addEventListener("click", () => irPaginaHistorial(Math.min(syncPages, syncPage + 1)));
  $("#reservoHistUltima").addEventListener("click", () => irPaginaHistorial(syncPages));
  ui.syncPage.addEventListener("change", () => {
    const destino = Number.parseInt(ui.syncPage.value, 10);
    irPaginaHistorial(Number.isFinite(destino) ? Math.min(syncPages, Math.max(1, destino)) : syncPage);
  });
  $("#reservoPrimera").addEventListener("click", () => { page = 1; cargarPacientes(); });
  $("#reservoAnterior").addEventListener("click", () => { page = Math.max(1, page - 1); cargarPacientes(); });
  $("#reservoSiguiente").addEventListener("click", () => { page = Math.min(pages, page + 1); cargarPacientes(); });
  $("#reservoUltima").addEventListener("click", () => { page = pages; cargarPacientes(); });
  ui.page.addEventListener("change", () => {
    const destino = Number.parseInt(ui.page.value, 10);
    if (Number.isFinite(destino)) { page = Math.min(pages, Math.max(1, destino)); cargarPacientes(); }
  });
  ui.modalName.addEventListener("input", () => {
    clearTimeout(previewTimer);
    previewTimer = setTimeout(prepararVistaPreviaBase, 300);
  });
  ui.modalSave.addEventListener("click", guardarBaseReservo);
  $("#reservoModalCerrar").addEventListener("click", cerrarModalGuardar);
  $("#reservoModalCancelar").addEventListener("click", cerrarModalGuardar);
  ui.modal.addEventListener("click", (evento) => {
    if (evento.target === ui.modal) cerrarModalGuardar();
  });
  document.addEventListener("keydown", (evento) => {
    if (evento.key === "Escape" && !ui.modal.hidden) cerrarModalGuardar();
  });
  function iniciarRecuperacion() {
    const ruta = "api/v1/recuperacion-pacientes";
    const form = $("#formRecuperacion");
    const clinica = $("#recuperacionClinica");
    const anio = $("#recuperacionAnio");
    const mes = $("#recuperacionMes");
    const mesesInactividad = $("#recuperacionMeses");
    const consultarTratamientosRec = $("#recuperacionConsultarTratamientos");
    const tratamientosRec = $("#recuperacionTratamientos");
    const herramientasRec = $("#recuperacionTratamientosHerramientas");
    const buscarTratamientoRec = $("#recuperacionBuscarTratamiento");
    const procesar = $("#recuperacionProcesar");
    const aviso = $("#recuperacionAviso");
    const historial = $("#recuperacionSolicitudes");
    const anterior = $("#recuperacionAnterior");
    const siguiente = $("#recuperacionSiguiente");
    const detalle = $("#recuperacionDetalle");
    const titulo = $("#recuperacionDetalleTitulo");
    const estado = $("#recuperacionEstado");
    const revisar = $("#recuperacionRevisar");
    const publicar = $("#recuperacionPublicar");
    const abrirBase = $("#recuperacionAbrirBase");
    const contactoAviso = $("#recuperacionContactosAviso");
    const nombresMes = new Intl.DateTimeFormat("es-CL", { month: "long", timeZone: "UTC" });
    const meses = Array.from({ length: 12 }, (_, i) => nombresMes.format(new Date(Date.UTC(2000, i, 1))));
    const etiquetas = { PENDIENTE: "Pendiente", PROCESANDO: "Procesando", ANALIZADA: "Analizada",
      COMPLETADA: "Completada", SIN_RESULTADOS: "Sin resultados", ERROR: "Error", CANCELADA: "Cancelada" };
    let centros = [];
    let cursores = [null];
    let cursorSiguiente = null;
    let consultaHistorial = 0;
    let enviando = false;
    let consultandoTratamientos = false;
    let tratamientosConsultados = false;
    let versionTratamientos = 0;
    let seleccion = null;
    let secuencia = 0;
    let temporizador;
    const intentos = new Map();

    function periodoActual() {
      const partes = new Intl.DateTimeFormat("en-CA", {
        year: "numeric", month: "numeric", timeZone: "America/Santiago",
      }).formatToParts(new Date());
      return Number(partes.find((p) => p.type === "year").value) * 12
        + Number(partes.find((p) => p.type === "month").value);
    }

    const ultimo = periodoActual() - 1;
    anio.value = String(Math.floor((ultimo - 1) / 12));
    anio.max = String(Math.floor((periodoActual() - 1) / 12));
    mes.replaceChildren(...meses.map((nombre, i) => new Option(nombre, String(i + 1))));
    mes.value = String((ultimo - 1) % 12 + 1);
    const nombreClinica = (codigo) => centros.find((c) => c.clinica_codigo === codigo)?.nombre || codigo;
    const periodo = (fila) => `${meses[fila.mes - 1]} de ${fila.anio}`;
    const activa = (valor) => ["PENDIENTE", "PROCESANDO"].includes(valor);
    const avisar = (texto, tipo = "info") => {
      aviso.textContent = texto;
      aviso.className = `reservo-aviso reservo-aviso--${tipo}`;
    };

    function validarFormulario() {
      const centro = centros.find((c) => c.clinica_codigo === clinica.value);
      const historico = anio.checkValidity() && Number(anio.value) * 12 + Number(mes.value) < periodoActual();
      $("#recuperacionUmbral").textContent = !historico ? "Selecciona un mes histórico completo."
        : "Ingresa entre 1 y 120 meses de inactividad.";
      const criteriosValidos = historico && centro && mesesInactividad.checkValidity();
      const cantidad = tratamientosElegidos().length;
      consultarTratamientosRec.disabled = enviando || consultandoTratamientos || !criteriosValidos;
      procesar.disabled = enviando || consultandoTratamientos || !tratamientosConsultados || !criteriosValidos
        || cantidad > 50;
      $("#recuperacionTratamientosSeleccion").textContent = tratamientosConsultados
        ? (cantidad ? `${cantidad} seleccionados` : "Todos los tratamientos") : "";
      $("#recuperacionSiguientePaso").textContent = !criteriosValidos ? "Completa los datos del paso 1."
        : consultandoTratamientos ? "Esperando la consulta de Reservo."
        : !tratamientosConsultados ? "Consulta los tratamientos para continuar."
        : cantidad > 50 ? "Selecciona como máximo 50 tratamientos."
        : "Cuando termine el análisis podrás revisar cuántos pacientes se pueden publicar.";
    }

    function tratamientosElegidos() {
      return [...tratamientosRec.querySelectorAll("input:checked")].map((opcion) => opcion.value);
    }

    for (const campo of [clinica, anio, mes, mesesInactividad]) {
      campo.addEventListener("input", () => {
        versionTratamientos++;
        consultandoTratamientos = false;
        tratamientosConsultados = false;
        herramientasRec.hidden = true;
        buscarTratamientoRec.value = "";
        tratamientosRec.textContent = "Consulta los tratamientos nuevamente.";
      });
    }

    consultarTratamientosRec.addEventListener("click", async () => {
      const version = ++versionTratamientos;
      const elegidos = new Set(tratamientosElegidos());
      consultandoTratamientos = true;
      tratamientosConsultados = false;
      herramientasRec.hidden = true;
      buscarTratamientoRec.value = "";
      validarFormulario();
      tratamientosRec.textContent = "Consultando tratamientos en Reservo…";
      try {
        const consulta = await request(`${ruta}/tratamientos`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ clinica_codigo: clinica.value, anio: Number(anio.value), mes: Number(mes.value) }),
        });
        let data;
        while (version === versionTratamientos) {
          data = await request(`${ruta}/tratamientos/${encodeURIComponent(consulta.id)}`);
          if (version !== versionTratamientos) return;
          if (data.estado === "ERROR") throw new Error(data.error || "La consulta de tratamientos falló.");
          if (data.estado === "COMPLETADA") break;
          tratamientosRec.textContent = data.estado === "PENDIENTE" ? "Consulta en cola…"
            : `Consultando Reservo · ${data.paginas} páginas revisadas`;
          await new Promise((resolver) => setTimeout(resolver, 3000));
        }
        if (version !== versionTratamientos) return;
        const opciones = (data.items || []).map((item) => {
          const etiqueta = document.createElement("label");
          etiqueta.className = "reservo-tratamiento";
          const casilla = document.createElement("input");
          casilla.type = "checkbox";
          casilla.value = item.uuid;
          casilla.checked = elegidos.has(item.uuid);
          etiqueta.append(casilla, document.createTextNode(item.nombre));
          return etiqueta;
        });
        tratamientosRec.replaceChildren(...opciones);
        tratamientosConsultados = true;
        herramientasRec.hidden = !opciones.length;
        if (!opciones.length) tratamientosRec.textContent = "No hay tratamientos para este período.";
      } catch (error) {
        if (version === versionTratamientos) tratamientosRec.textContent = error.message;
      } finally {
        if (version === versionTratamientos) {
          consultandoTratamientos = false;
          validarFormulario();
        }
      }
    });

    buscarTratamientoRec.addEventListener("input", () => {
      const termino = buscarTratamientoRec.value.trim().toLocaleLowerCase("es");
      for (const opcion of tratamientosRec.querySelectorAll("label")) {
        opcion.hidden = !opcion.textContent.toLocaleLowerCase("es").includes(termino);
      }
    });
    $("#recuperacionLimpiarTratamientos").addEventListener("click", () => {
      for (const casilla of tratamientosRec.querySelectorAll("input:checked")) casilla.checked = false;
      validarFormulario();
    });

    function conteos(id, datos, campos) {
      $(id).replaceChildren(...Object.entries(campos).filter(([clave]) => datos?.[clave] != null)
        .map(([clave, nombre]) => {
          const li = document.createElement("li");
          li.textContent = `${nombre}: ${Number(datos[clave]).toLocaleString("es-CL")}`;
          return li;
        }));
    }

    async function cargarHistorial(navegacion = cursores) {
      const consulta = ++consultaHistorial;
      anterior.disabled = siguiente.disabled = true;
      try {
        const params = new URLSearchParams({ limite: "10" });
        if (navegacion.at(-1)) params.set("cursor", navegacion.at(-1));
        const data = await request(`${ruta}/solicitudes?${params}`);
        if (consulta !== consultaHistorial) return;
        cursores = navegacion;
        cursorSiguiente = data.siguiente_cursor;
        $("#recuperacionPagina").textContent = `Página ${cursores.length}`;
        historial.innerHTML = (data.items || []).map((fila) =>
          `<tr><td>${escapar(nombreClinica(fila.clinica_codigo))}</td><td>${escapar(periodo(fila))}</td>` +
          `<td>${Number(fila.meses_inactividad)} meses</td><td data-rec-estado>${escapar(etiquetas[fila.estado] || fila.estado)}</td>` +
          `<td><button class="btn btn--ghost" type="button" data-rec-id="${escapar(fila.solicitud_id)}">Ver detalle</button></td></tr>`
        ).join("") || '<tr><td colspan="5" class="reservo-vacio">Todavía no hay solicitudes.</td></tr>';
      } catch (error) {
        if (consulta === consultaHistorial) avisar(error.message, "error");
      } finally {
        if (consulta === consultaHistorial) {
          anterior.disabled = cursores.length < 2;
          siguiente.disabled = !cursorSiguiente;
        }
      }
    }

    async function consultarDetalle(id, enfocar = false, reintentos = 0, comprobar = false) {
      const version = secuencia;
      const url = `${ruta}/solicitudes/${encodeURIComponent(id)}`;
      try {
        const fila = await request(url);
        if (version !== secuencia) return;
        titulo.textContent = `${nombreClinica(fila.clinica_codigo)} · ${periodo(fila)}`;
        if (enfocar) titulo.focus();
        const avance = fila.progreso;
        const resumen = `${etiquetas[fila.estado] || fila.estado} · Umbral de ${fila.meses_inactividad} meses`
          + (avance ? ` · ${avance.tramos} tramos y ${avance.paginas} páginas revisadas` : "");
        if (estado.textContent !== resumen) estado.textContent = resumen;
        for (const boton of historial.querySelectorAll("[data-rec-id]")) {
          if (boton.dataset.recId === id) boton.closest("tr").querySelector("[data-rec-estado]").textContent =
            etiquetas[fila.estado] || fila.estado;
        }
        if (activa(fila.estado)) {
          temporizador = setTimeout(() => consultarDetalle(id, false, 0, comprobar), 5000);
          return;
        }
        const resultado = await request(`${url}/resultado`);
        if (version !== secuencia) return;
        if (resultado.error?.detalle) estado.textContent = resultado.error.detalle;
        conteos("#recuperacionConteos", resultado.conteos, { pacientes_analizados: "Pacientes analizados",
          pacientes_inactivos: "Pacientes inactivos", citas_sin_uuid: "Citas sin identificador" });
        revisar.hidden = fila.estado !== "ANALIZADA";
        if (comprobar && fila.estado === "ANALIZADA") revisarContactos();
        if (resultado.publicacion_pendiente) {
          abrirBase.hidden = true;
          publicar.hidden = false;
          publicar.textContent = "Reintentar publicación";
          contactoAviso.textContent = "La lista se guardó y falta habilitarla en Base de datos.";
        } else if (resultado.lista_generada_id) {
          publicar.hidden = true;
          abrirBase.href = `pacientes.html?base_reservo=${encodeURIComponent(resultado.lista_generada_id)}`;
          abrirBase.hidden = false;
          contactoAviso.textContent = `Base publicada con ${Number(resultado.contactos_publicados).toLocaleString("es-CL")} pacientes.`;
        } else if (fila.estado === "COMPLETADA") {
          contactoAviso.textContent = "La base publicada ya no está disponible.";
        }
      } catch (error) {
        if (version !== secuencia) return;
        estado.textContent = error.message;
        if (!enfocar && reintentos < 3 && error.message !== "La sesión expiró.") {
          temporizador = setTimeout(() => consultarDetalle(id, false, reintentos + 1, comprobar),
            5000 * 2 ** (reintentos + 1));
        }
      }
    }

    function abrirSolicitud(id, comprobar = false) {
      clearTimeout(temporizador);
      secuencia++;
      seleccion = id;
      detalle.hidden = false;
      titulo.textContent = "Detalle de recuperación";
      estado.textContent = "Consultando solicitud…";
      contactoAviso.textContent = "";
      $("#recuperacionConteos").replaceChildren();
      $("#recuperacionContactos").replaceChildren();
      revisar.hidden = true;
      revisar.disabled = false;
      publicar.hidden = true;
      publicar.textContent = "Publicar base de pacientes";
      publicar.disabled = false;
      abrirBase.hidden = true;
      abrirBase.removeAttribute("href");
      consultarDetalle(id, true, 0, comprobar);
    }

    async function revisarContactos() {
      const version = secuencia;
      revisar.disabled = true;
      publicar.hidden = true;
      contactoAviso.textContent = "Revisando contactos…";
      $("#recuperacionContactos").replaceChildren();
      try {
        const datos = await request(`${ruta}/solicitudes/${encodeURIComponent(seleccion)}/revision-contactos`);
        if (version !== secuencia) return;
        publicar.hidden = !datos.publicacion_disponible;
        contactoAviso.textContent = datos.publicacion_disponible
          ? `Puedes publicar ${Number(datos.conteos.contactos_sin_bloqueo).toLocaleString("es-CL")} pacientes en ${datos.nombre_propuesto}.`
          : "No hay contactos disponibles para publicar.";
        conteos("#recuperacionContactos", datos.conteos, { pacientes_inactivos: "Pacientes inactivos",
          sin_ficha_local: "Sin ficha local", telefonos_invalidos: "Teléfonos inválidos",
          atenciones_recientes: "Con atención posterior al umbral",
          telefonos_compartidos: "Contactos con teléfono compartido", bloqueados: "Bloqueados",
          contactos_sin_bloqueo: "Contactos sin bloqueo" });
      } catch (error) {
        if (version === secuencia) contactoAviso.textContent = error.message;
      } finally {
        if (version === secuencia) revisar.disabled = false;
      }
    }
    revisar.addEventListener("click", revisarContactos);

    publicar.addEventListener("click", async () => {
      if (!seleccion || publicar.disabled) return;
      const id = seleccion;
      const version = secuencia;
      publicar.disabled = revisar.disabled = true;
      contactoAviso.textContent = "Publicando base…";
      try {
        await request(`${ruta}/solicitudes/${encodeURIComponent(id)}/publicar`, { method: "POST" });
        if (version !== secuencia) return;
        abrirSolicitud(id);
        cargarHistorial();
      } catch (error) {
        if (version !== secuencia) return;
        contactoAviso.textContent = error.message;
        publicar.disabled = revisar.disabled = false;
      }
    });

    form.addEventListener("input", validarFormulario);
    form.addEventListener("submit", async (evento) => {
      evento.preventDefault();
      validarFormulario();
      if (procesar.disabled) return;
      const body = JSON.stringify({ clinica_codigo: clinica.value, anio: Number(anio.value),
        mes: Number(mes.value), meses_inactividad: Number(mesesInactividad.value),
        tratamientos: tratamientosElegidos() });
      if (!intentos.has(body)) {
        const bytes = crypto.getRandomValues(new Uint8Array(16));
        intentos.set(body, Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join(""));
      }
      enviando = true;
      validarFormulario();
      avisar("Registrando solicitud…");
      try {
        const fila = await request(`${ruta}/solicitudes`, { method: "POST", body,
          headers: { "Content-Type": "application/json", "Idempotency-Key": intentos.get(body) } });
        intentos.delete(body);
        avisar("Solicitud registrada.", "success");
        abrirSolicitud(fila.solicitud_id, true);
        await cargarHistorial([null]);
      } catch (error) {
        avisar(error.message, "error");
      } finally {
        enviando = false;
        validarFormulario();
      }
    });
    historial.addEventListener("click", (evento) => {
      const boton = evento.target.closest("[data-rec-id]");
      if (boton) abrirSolicitud(boton.dataset.recId);
    });
    anterior.addEventListener("click", () => cargarHistorial(cursores.slice(0, -1)));
    siguiente.addEventListener("click", () => cargarHistorial([...cursores, cursorSiguiente]));
    $("#recuperacionActualizar").addEventListener("click", () => {
      cargarHistorial(cursores);
      if (seleccion) abrirSolicitud(seleccion);
    });
    request(`${ruta}/clinicas`).then((data) => {
      centros = data.items || [];
      clinica.replaceChildren(new Option("Selecciona una clínica", ""),
        ...centros.map((c) => new Option(c.nombre, c.clinica_codigo)));
      validarFormulario();
      return cargarHistorial();
    }).catch((error) => {
      avisar(error.message, "error");
      historial.innerHTML = '<tr><td colspan="5" class="reservo-vacio">No se pudo cargar el historial.</td></tr>';
    });
  }

  iniciarRecuperacion();
  (async function iniciar() {
    try {
      await cargarConfiguracion();
      await Promise.all([cargarSincronizaciones(), cargarPacientes()]);
    } catch (error) {
      mostrarAviso(error.message || "No se pudo cargar Pacientes Reservo.", "error");
    }
  })();
})();
