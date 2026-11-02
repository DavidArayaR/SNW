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
    clinicFilter: $("#filtroReservoClinica"), treatmentFilter: $("#filtroReservoTratamiento"),
    fromFilter: $("#filtroReservoDesde"), toFilter: $("#filtroReservoHasta"), search: $("#filtroReservoBuscar"),
    pageSize: $("#reservoPorPagina"), page: $("#reservoPaginaIr"), pageInfo: $("#reservoPaginaInfo"),
  };
  let clinics = [];
  let page = 1;
  let pages = 1;
  let pollTimer = null;
  let searchTimer = null;
  let treatmentsByUuid = new Map();
  let loadingTreatments = false;

  async function request(url, options = {}) {
    const response = await fetch(url, { ...options, headers: headers(options.headers || {}), cache: "no-store" });
    const data = await response.json().catch(() => ({}));
    if (response.status === 401) { window.snwSesionExpirada(); throw new Error("La sesión expiró."); }
    if (!response.ok) throw new Error(data.detail || "No se pudo completar la solicitud.");
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

  function formatoFecha(valor) {
    if (!valor) return "—";
    const fecha = new Date(String(valor).replace(" ", "T") + (String(valor).includes("Z") ? "" : "Z"));
    return Number.isNaN(fecha.getTime()) ? String(valor) : fecha.toLocaleString("es-CL", { dateStyle: "short", timeStyle: "short" });
  }

  function estadoClase(estado) {
    if (estado === "completado") return "reservo-estado--ok";
    if (estado === "completado_con_advertencias") return "reservo-estado--warning";
    if (estado === "error") return "reservo-estado--error";
    return "reservo-estado--proceso";
  }

  function estadoTexto(estado) {
    return ({ pendiente: "Pendiente", ejecutando: "En curso", completado: "Completado",
      completado_con_advertencias: "Con advertencias", error: "Error" })[estado] || estado || "—";
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

  async function cargarSincronizaciones() {
    const data = await request("api/reservo/sincronizaciones?limite=50");
    const filas = data.items || [];
    if (!filas.length) {
      ui.syncs.innerHTML = '<tr><td colspan="6" class="reservo-vacio">Todavía no hay sincronizaciones.</td></tr>';
      return false;
    }
    ui.syncs.innerHTML = filas.map((item) => {
      const periodo = `${escapar(item.fecha_desde)} – ${escapar(item.fecha_hasta)}`;
      const conteos = `${Number(item.periodos_completados || 0)}/${Number(item.periodos_total || 0)} tramos · ${Number(item.paginas_consultadas || 0)} páginas · ${Number(item.citas_recibidas || 0)} citas`;
      const progreso = item.estado === "completado" || item.estado === "completado_con_advertencias"
        ? 100 : Math.min(100, Math.round(100 * Number(item.periodos_completados || 0) / Math.max(1, Number(item.periodos_total || 0))));
      const pacientes = `${Number(item.pacientes_unicos || 0)} únicos · ${Number(item.pacientes_nuevos || 0)} nuevos · ${Number(item.pacientes_actualizados || 0)} actualizados`;
      const accion = ["error", "completado_con_advertencias"].includes(item.estado)
        ? `<button type="button" class="btn btn--ghost reservo-reintentar" data-reintentar="${Number(item.id)}"><i class="fa-solid fa-rotate-right" aria-hidden="true"></i> Reintentar</button>` : "";
      return `<tr><td><strong>${escapar(item.clinica_nombre)}</strong><br><small>${periodo}</small></td>` +
        `<td>${escapar(item.tratamiento_nombre || "Tratamiento histórico")}</td>` +
        `<td><span class="reservo-estado ${estadoClase(item.estado)}">${estadoTexto(item.estado)}</span></td>` +
        `<td><div class="reservo-progreso" role="progressbar" aria-label="Tramos completados" aria-valuenow="${progreso}" aria-valuemin="0" aria-valuemax="100"><span style="width:${progreso}%"></span></div><small>${conteos}</small>${item.ultimo_error ? `<p class="reservo-error-detalle">${escapar(item.ultimo_error)}</p>` : ""}</td>` +
        `<td><strong>${pacientes}</strong><br><small>${Number(item.citas_atendidas || 0)} atendidas · ${Number(item.citas_tratamiento || 0)} con tratamiento · ${Number(item.pacientes_duplicados || 0)} duplicados</small></td>` +
        `<td>${accion}</td></tr>`;
    }).join("");
    ui.syncs.querySelectorAll("[data-reintentar]").forEach((boton) =>
      boton.addEventListener("click", () => reintentar(boton.dataset.reintentar, boton)));
    return filas.some((item) => ["pendiente", "ejecutando"].includes(item.estado));
  }

  function iniciarSondeo() {
    if (pollTimer) return;
    pollTimer = setInterval(async () => {
      try {
        const activo = await cargarSincronizaciones();
        if (!activo) {
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
    if (ui.clinicFilter.value) params.set("clinica", ui.clinicFilter.value);
    if (ui.treatmentFilter.value.trim()) params.set("tratamiento", ui.treatmentFilter.value.trim());
    if (ui.fromFilter.value) params.set("desde", ui.fromFilter.value);
    if (ui.toFilter.value) params.set("hasta", ui.toFilter.value);
    if (ui.search.value.trim()) params.set("q", ui.search.value.trim());
    return params;
  }

  async function cargarPacientes() {
    ui.patients.innerHTML = '<tr><td colspan="7" class="reservo-vacio">Cargando pacientes…</td></tr>';
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
        ui.patients.innerHTML = '<tr><td colspan="7" class="reservo-vacio">No hay pacientes para estos filtros.</td></tr>';
        return;
      }
      ui.patients.innerHTML = data.items.map((item) =>
        `<tr><td><strong>${escapar(item.paciente_nombre || "—")}</strong></td>` +
        `<td>${escapar(item.paciente_apellido || "—")}</td>` +
        `<td>${escapar(item.paciente_telefono || "—")}</td><td>${escapar(item.clinica_nombre)}</td>` +
        `<td><code>${escapar(item.cliente_uuid_parcial)}</code></td>` +
        `<td>${escapar(item.tratamientos_nombre || "—")}</td>` +
        `<td>${escapar(formatoFecha(item.ultima_sincronizacion_en))}</td></tr>`).join("");
    } catch (error) {
      ui.patients.innerHTML = `<tr><td colspan="7" class="reservo-vacio reservo-vacio--error">${escapar(error.message)}</td></tr>`;
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
      await request("api/reservo/sincronizaciones", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ clinica_codigo: ui.clinic.value, fecha_desde: ui.from.value,
          fecha_hasta: ui.to.value, tratamientos: tratamientosSeleccionados() }),
      });
      mostrarAviso("Consulta iniciada. El progreso se actualizará aquí; no se enviará ningún WhatsApp.", "success");
      await cargarSincronizaciones();
      iniciarSondeo();
    } catch (error) {
      mostrarAviso(error.message, "error");
    } finally {
      actualizarClinica();
    }
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
  $("#reservoPrimera").addEventListener("click", () => { page = 1; cargarPacientes(); });
  $("#reservoAnterior").addEventListener("click", () => { page = Math.max(1, page - 1); cargarPacientes(); });
  $("#reservoSiguiente").addEventListener("click", () => { page = Math.min(pages, page + 1); cargarPacientes(); });
  $("#reservoUltima").addEventListener("click", () => { page = pages; cargarPacientes(); });
  ui.page.addEventListener("change", () => {
    const destino = Number.parseInt(ui.page.value, 10);
    if (Number.isFinite(destino)) { page = Math.min(pages, Math.max(1, destino)); cargarPacientes(); }
  });

  (async function iniciar() {
    try {
      await cargarConfiguracion();
      await Promise.all([cargarSincronizaciones(), cargarPacientes()]);
      if (ui.syncs.querySelector(".reservo-estado--proceso")) iniciarSondeo();
    } catch (error) {
      mostrarAviso(error.message || "No se pudo cargar Pacientes Reservo.", "error");
    }
  })();
})();
