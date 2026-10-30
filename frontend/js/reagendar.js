(function () {
  const card = document.getElementById("cardReagendar");
  if (!card || !window.snwEsPrivilegiado) return;
  const $ = id => document.getElementById(id);
  let pagina = 1;
  let paginas = 1;
  let paginaLotes = 1;
  let loteActual = null;
  let loteAplazarId = null;
  let loteEliminarId = null;
  let pacienteEliminarId = null;
  const modalEliminarPruebas = $("modalEliminarPruebasReagendar");
  const confirmarEliminarPruebas = $("confirmarEliminarPruebasReagendar");
  const botonEliminarPruebas = $("reagendarEliminarPruebas");
  function cerrarEliminarPruebas() {
    modalEliminarPruebas.hidden = true;
    $("errorEliminarPruebasReagendar").textContent = "";
  }
  botonEliminarPruebas.addEventListener("click", async () => {
    if (botonEliminarPruebas.disabled) return;
    botonEliminarPruebas.disabled = true;
    try {
      const res = await fetch("api/v1/paciente-reagendar/pruebas/borrado/resumen",
        { headers: auth(), cache: "no-store" });
      const datos = await res.json().catch(() => ({}));
      if (res.status === 401) { window.snwSesionExpirada(); return; }
      if (!res.ok) throw new Error(datos.detail || "No se pudieron revisar los números de prueba.");
      if (!datos.eliminables) {
        mensaje.textContent = "Aún no hay números de prueba finalizados para quitar. Espera la respuesta o 24 horas desde el envío.";
        return;
      }
      $("textoEliminarPruebasReagendar").textContent =
        `Se quitarán ${datos.eliminables} registros de números de prueba de reagendamientos. ` +
        "El historial de mensajes se conservará y los números seguirán autorizados en Configuración. " +
        "Podrán volver a importarse en otro CSV. Esta acción no se puede deshacer.";
      modalEliminarPruebas.hidden = false;
      confirmarEliminarPruebas.focus();
    } catch (error) {
      mensaje.textContent = error.message;
    } finally {
      botonEliminarPruebas.disabled = false;
    }
  });
  $("cancelarEliminarPruebasReagendar").addEventListener("click", cerrarEliminarPruebas);
  modalEliminarPruebas.addEventListener("click", e => {
    if (e.target === modalEliminarPruebas && !confirmarEliminarPruebas.disabled) cerrarEliminarPruebas();
  });
  document.addEventListener("keydown", e => {
    if (e.key === "Escape" && !modalEliminarPruebas.hidden && !confirmarEliminarPruebas.disabled) {
      cerrarEliminarPruebas();
    }
  });
  confirmarEliminarPruebas.addEventListener("click", async () => {
    if (confirmarEliminarPruebas.disabled) return;
    confirmarEliminarPruebas.disabled = true;
    $("errorEliminarPruebasReagendar").textContent = "Quitando registros de prueba…";
    try {
      const res = await fetch("api/v1/paciente-reagendar/pruebas/borrado",
        { method: "DELETE", headers: auth() });
      const datos = await res.json().catch(() => ({}));
      if (res.status === 401) { window.snwSesionExpirada(); return; }
      if (!res.ok) throw new Error(datos.detail || "No se pudieron quitar los registros de prueba.");
      cerrarEliminarPruebas();
      await cargarLotes();
      await cargar();
      mensaje.textContent = `${datos.pacientes_eliminados || 0} registros de prueba retirados de reagendamientos.`;
    } catch (error) {
      $("errorEliminarPruebasReagendar").textContent = error.message;
    } finally {
      confirmarEliminarPruebas.disabled = false;
    }
  });
  const modalEliminarTodos = $("modalEliminarTodosReagendar");
  const confirmarEliminarTodos = $("confirmarEliminarTodosReagendar");
  const botonEliminarTodos = $("reagendarEliminarTodos");
  function cerrarEliminarTodos() {
    modalEliminarTodos.hidden = true;
    $("errorEliminarTodosReagendar").textContent = "";
  }
  botonEliminarTodos.addEventListener("click", async () => {
    if (botonEliminarTodos.disabled) return;
    botonEliminarTodos.disabled = true;
    try {
      const res = await fetch("api/v1/paciente-reagendar/borrado/resumen",
        { headers: auth(), cache: "no-store" });
      const datos = await res.json().catch(() => ({}));
      if (res.status === 401) { window.snwSesionExpirada(); return; }
      if (!res.ok) throw new Error(datos.detail || "No se pudo consultar el total.");
      if (!datos.eliminables) {
        mensaje.textContent = "No hay pacientes sin envío que se puedan quitar.";
        return;
      }
      $("textoEliminarTodosReagendar").textContent =
        `Se quitarán ${datos.eliminables} pacientes que todavía no tienen intentos de envío. ` +
        `${datos.conservados} registros con envíos, intentos o bajas se conservarán. ` +
        "Si vuelven a llegar en otro CSV, podrán importarse de nuevo. Esta acción no se puede deshacer.";
      modalEliminarTodos.hidden = false;
      confirmarEliminarTodos.focus();
    } catch (error) {
      mensaje.textContent = error.message;
    } finally {
      botonEliminarTodos.disabled = false;
    }
  });
  $("cancelarEliminarTodosReagendar").addEventListener("click", cerrarEliminarTodos);
  modalEliminarTodos.addEventListener("click", e => {
    if (e.target === modalEliminarTodos && !confirmarEliminarTodos.disabled) cerrarEliminarTodos();
  });
  document.addEventListener("keydown", e => {
    if (e.key === "Escape" && !modalEliminarTodos.hidden && !confirmarEliminarTodos.disabled) {
      cerrarEliminarTodos();
    }
  });
  confirmarEliminarTodos.addEventListener("click", async () => {
    if (confirmarEliminarTodos.disabled) return;
    confirmarEliminarTodos.disabled = true;
    $("errorEliminarTodosReagendar").textContent = "Quitando pacientes…";
    try {
      const res = await fetch("api/v1/paciente-reagendar/borrado/todos",
        { method: "DELETE", headers: auth() });
      const datos = await res.json().catch(() => ({}));
      if (res.status === 401) { window.snwSesionExpirada(); return; }
      if (!res.ok) throw new Error(datos.detail || "No se pudieron quitar los pacientes.");
      cerrarEliminarTodos();
      await cargarLotes();
      await cargar();
      mensaje.textContent = `${datos.pacientes_eliminados || 0} pacientes retirados antes del envío.`;
    } catch (error) {
      $("errorEliminarTodosReagendar").textContent = error.message;
    } finally {
      confirmarEliminarTodos.disabled = false;
    }
  });
  const modalEliminarPaciente = $("modalEliminarPacienteReagendar");
  const confirmarEliminarPaciente = $("confirmarEliminarPacienteReagendar");
  function cerrarEliminarPaciente() {
    modalEliminarPaciente.hidden = true;
    pacienteEliminarId = null;
    $("errorEliminarPacienteReagendar").textContent = "";
  }
  function abrirEliminarPaciente(paciente) {
    pacienteEliminarId = paciente.id;
    $("textoEliminarPacienteReagendar").textContent =
      `Se quitará a ${paciente.paciente_nombre} de esta carga. No recibirá la plantilla de ` +
      "este reagendamiento. Si vuelve a llegar en otro CSV, podrá importarse de nuevo.";
    $("errorEliminarPacienteReagendar").textContent = "";
    modalEliminarPaciente.hidden = false;
    confirmarEliminarPaciente.focus();
  }
  $("cancelarEliminarPacienteReagendar").addEventListener("click", cerrarEliminarPaciente);
  modalEliminarPaciente.addEventListener("click", e => {
    if (e.target === modalEliminarPaciente && !confirmarEliminarPaciente.disabled) cerrarEliminarPaciente();
  });
  document.addEventListener("keydown", e => {
    if (e.key === "Escape" && !modalEliminarPaciente.hidden && !confirmarEliminarPaciente.disabled) {
      cerrarEliminarPaciente();
    }
  });
  confirmarEliminarPaciente.addEventListener("click", async () => {
    const id = pacienteEliminarId;
    if (!id || confirmarEliminarPaciente.disabled) return;
    confirmarEliminarPaciente.disabled = true;
    $("errorEliminarPacienteReagendar").textContent = "Quitando paciente…";
    try {
      const res = await fetch(`api/v1/paciente-reagendar/${id}`,
        { method: "DELETE", headers: auth() });
      const datos = await res.json().catch(() => ({}));
      if (res.status === 401) { window.snwSesionExpirada(); return; }
      if (!res.ok) throw new Error(datos.detail || "No se pudo quitar al paciente.");
      cerrarEliminarPaciente();
      await cargar();
      await cargarLotes();
      mensaje.textContent = "Paciente retirado del reagendamiento antes del envío.";
    } catch (error) {
      $("errorEliminarPacienteReagendar").textContent = error.message;
    } finally {
      confirmarEliminarPaciente.disabled = false;
    }
  });
  const modalEliminarCarga = $("modalEliminarCargaReagendar");
  const confirmarEliminarCarga = $("confirmarEliminarCargaReagendar");
  function cerrarEliminarCarga() {
    modalEliminarCarga.hidden = true;
    loteEliminarId = null;
    $("errorEliminarCargaReagendar").textContent = "";
  }
  function abrirEliminarCarga(lote) {
    loteEliminarId = lote.id;
    $("textoEliminarCargaReagendar").textContent =
      `Se borrarán los pacientes nuevos de la carga #${lote.id} (${lote.creados || 0} registrados) ` +
      "y se cancelará su programación. No se borrarán pacientes de otras cargas. " +
      "Si vuelven a llegar en otro CSV, podrán importarse de nuevo. Esta acción no se puede deshacer.";
    $("errorEliminarCargaReagendar").textContent = "";
    modalEliminarCarga.hidden = false;
    confirmarEliminarCarga.focus();
  }
  $("cancelarEliminarCargaReagendar").addEventListener("click", cerrarEliminarCarga);
  modalEliminarCarga.addEventListener("click", e => {
    if (e.target === modalEliminarCarga && !confirmarEliminarCarga.disabled) cerrarEliminarCarga();
  });
  document.addEventListener("keydown", e => {
    if (e.key === "Escape" && !modalEliminarCarga.hidden && !confirmarEliminarCarga.disabled) {
      cerrarEliminarCarga();
    }
  });
  confirmarEliminarCarga.addEventListener("click", async () => {
    const id = loteEliminarId;
    if (!id || confirmarEliminarCarga.disabled) return;
    confirmarEliminarCarga.disabled = true;
    $("errorEliminarCargaReagendar").textContent = "Eliminando carga…";
    try {
      const res = await fetch(`api/v1/paciente-reagendar/lotes/${id}`,
        { method: "DELETE", headers: auth() });
      const datos = await res.json().catch(() => ({}));
      if (res.status === 401) { window.snwSesionExpirada(); return; }
      if (!res.ok) throw new Error(datos.detail || "No se pudo eliminar la carga.");
      cerrarEliminarCarga();
      if (loteActual === id) {
        loteActual = null;
        pagina = 1;
        $("reagendarVistaPrevia").hidden = true;
      }
      await cargarLotes();
      await cargar();
      $("reagendarLotesMensaje").textContent =
        `Carga #${id} eliminada: ${datos.pacientes_eliminados || 0} pacientes borrados.`;
    } catch (error) {
      $("errorEliminarCargaReagendar").textContent = error.message;
    } finally {
      confirmarEliminarCarga.disabled = false;
    }
  });
  const formularioAplazar = $("reagendarAplazarForm");
  // Fuera del panel animado para que position:fixed se ancle al viewport.
  document.body.appendChild(formularioAplazar);
  let botonAplazarActual = null;
  function cerrarAplazamiento(devolverFoco = false) {
    if (formularioAplazar.hidden) return;
    formularioAplazar.hidden = true;
    const boton = botonAplazarActual;
    boton?.setAttribute("aria-expanded", "false");
    botonAplazarActual = null;
    loteAplazarId = null;
    if (devolverFoco && boton?.isConnected) boton.focus();
  }
  function posicionarAplazamiento() {
    if (formularioAplazar.hidden || !botonAplazarActual) return;
    if (!botonAplazarActual.isConnected) { cerrarAplazamiento(); return; }
    const boton = botonAplazarActual.getBoundingClientRect();
    if (boton.bottom < 0 || boton.top > window.innerHeight) { cerrarAplazamiento(); return; }
    const ancho = formularioAplazar.offsetWidth;
    const alto = formularioAplazar.offsetHeight;
    const izquierda = Math.min(Math.max(12, boton.left + boton.width / 2 - ancho / 2),
      Math.max(12, window.innerWidth - ancho - 12));
    const arriba = boton.top - alto - 10;
    const abajo = boton.bottom + 10;
    const top = arriba >= 12 ? arriba : Math.min(abajo, Math.max(12, window.innerHeight - alto - 12));
    formularioAplazar.style.left = `${Math.round(izquierda)}px`;
    formularioAplazar.style.top = `${Math.round(Math.max(12, top))}px`;
  }
  window.snwCerrarAplazamiento = cerrarAplazamiento;
  window.addEventListener("resize", posicionarAplazamiento);
  window.addEventListener("scroll", posicionarAplazamiento, true);
  document.addEventListener("keydown", (evento) => {
    if (evento.key === "Escape" && !formularioAplazar.hidden) cerrarAplazamiento(true);
  });
  document.addEventListener("pointerdown", (evento) => {
    if (!formularioAplazar.hidden && !formularioAplazar.contains(evento.target)
        && !botonAplazarActual?.contains(evento.target)) cerrarAplazamiento();
  });
  const tbody = $("tablaReagendar").querySelector("tbody");
  const mensaje = $("reagendarMensaje");
  const celda = (tr, valor) => {
    const td = document.createElement("td");
    td.textContent = valor == null ? "—" : String(valor);
    tr.appendChild(td);
  };
  const fecha = valor => valor ? String(valor).slice(0, 10) : "—";
  const fechaCarga = valor => {
    const partes = fecha(valor).split("-");
    return partes.length === 3 ? `${partes[2]}/${partes[1]}/${partes[0]}` : "—";
  };
  const fechaHoraChile = valor => {
    if (!valor) return "—";
    const utc = /(?:Z|[+-]\d\d:\d\d)$/.test(String(valor)) ? String(valor) : `${valor}Z`;
    const d = new Date(utc);
    return Number.isNaN(d.getTime()) ? "—" : new Intl.DateTimeFormat("es-CL", {
      timeZone: "America/Santiago", dateStyle: "short", timeStyle: "short" }).format(d);
  };
  const auth = () => ({ Authorization: "Bearer " + (localStorage.getItem("snw_token") || "") });
  const estadosLote = {
    PROGRAMADO: ["Programado", "Espera la hora indicada y las condiciones de envío.", "info"],
    APLAZADO: ["Fecha cambiada", "Se intentará enviar en la nueva fecha.", "info"],
    REVISION: ["Requiere revisión", "No se programó automáticamente; revisa antes de elegir una fecha.", "warn"],
    COMPLETADO: ["Procesado", "No hay pacientes pendientes en esta carga.", "ok"],
    EN_CURSO: ["En proceso", "SNW está procesando los mensajes.", "info"],
  };
  function celdaResumen(tr, principal, detalle = "") {
    const td = document.createElement("td");
    const titulo = document.createElement("strong");
    titulo.textContent = principal;
    td.appendChild(titulo);
    if (detalle) {
      const ayuda = document.createElement("small");
      ayuda.textContent = detalle;
      td.appendChild(ayuda);
    }
    tr.appendChild(td);
    return td;
  }
  function actualizarSeleccionLote() {
    $("reagendarSeleccionLote").textContent = loteActual
      ? `Mostrando pacientes nuevos de la carga #${loteActual}. Los repetidos figuran en el resumen del CSV.`
      : "Todos los pacientes, sin duplicados. Se aplican los filtros de abajo.";
    $("reagendarVerTodas").hidden = !loteActual;
    $("tablaReagendarLotes").querySelectorAll("tbody tr[data-lote-id]").forEach((fila) => {
      fila.classList.toggle("reagendar__lote--seleccionado", fila.dataset.loteId === String(loteActual));
    });
  }
  async function cargarLotes(agregar = false) {
    const tabla = $("tablaReagendarLotes");
    const cuerpo = tabla.querySelector("tbody");
    const botonMas = $("reagendarMasLotes");
    tabla.setAttribute("aria-busy", "true");
    botonMas.disabled = true;
    if (!agregar) paginaLotes = 1;
    $("reagendarLotesMensaje").textContent = "Cargando archivos recibidos…";
    try {
      const destino = `api/v1/paciente-reagendar/lotes?pagina=${paginaLotes}&limite=50`;
      const res = await fetch(destino, { headers: auth(), cache: "no-store" });
      if (!res.ok) throw new Error("No se pudieron cargar las programaciones.");
      const lotes = await res.json();
      cerrarAplazamiento();
      if (!agregar) cuerpo.replaceChildren();
      for (const lote of lotes) {
        const tr = document.createElement("tr");
        tr.dataset.loteId = String(lote.id);
        const tramo = { "08-14": "08:00 a 14:00", "14-20": "14:00 a 20:00" }[lote.ventana] || lote.ventana;
        celdaResumen(tr, fechaCarga(lote.fecha), `Búsqueda: ${tramo}`);
        celdaResumen(tr, fechaHoraChile(lote.recibido_en));
        celdaResumen(tr, lote.programado_para ? fechaHoraChile(lote.programado_para) : "Sin fecha",
          lote.programado_para ? "Hora de Chile" : "Necesita revisión");
        const estado = estadosLote[lote.estado] || [lote.estado || "Sin estado", "", "info"];
        const celdaEstado = celdaResumen(tr, estado[0], estado[1]);
        celdaEstado.classList.add(`reagendar__estado--${estado[2]}`);
        celdaResumen(tr, `${lote.creados || 0} nuevos`,
          `${lote.repetidos || 0} ya registrados · ${lote.rechazados || 0} rechazados`);
        celdaResumen(tr, String(lote.recibidos_pacientes || 0),
          "Entrega o lectura confirmada por WhatsApp");
        const acciones = document.createElement("td");
        acciones.className = "reagendar__acciones-lote";
        const revisar = document.createElement("button");
        revisar.type = "button"; revisar.className = "btn btn--ghost"; revisar.textContent = "Ver detalle";
        revisar.title = "Ver pacientes nuevos y plantilla de esta carga";
        revisar.addEventListener("click", () => {
          loteActual = lote.id; pagina = 1; actualizarSeleccionLote(); cargar(); cargarVista(lote.id);
        });
        acciones.appendChild(revisar);
        if (["PROGRAMADO", "APLAZADO", "REVISION"].includes(lote.estado)) {
          const aplazar = document.createElement("button");
          aplazar.type = "button"; aplazar.className = "btn btn--ghost"; aplazar.textContent = "Aplazar";
          aplazar.title = "Cambiar la fecha del envío; no modifica las citas";
          aplazar.setAttribute("aria-haspopup", "dialog");
          aplazar.setAttribute("aria-controls", "reagendarAplazarForm");
          aplazar.setAttribute("aria-expanded", "false");
          aplazar.addEventListener("click", () => {
            if (botonAplazarActual === aplazar && !formularioAplazar.hidden) {
              cerrarAplazamiento(true); return;
            }
            cerrarAplazamiento();
            loteAplazarId = lote.id;
            $("reagendarAplazarTitulo").textContent = `Aplazar envío de la carga #${lote.id}`;
            botonAplazarActual = aplazar;
            aplazar.setAttribute("aria-expanded", "true");
            formularioAplazar.style.visibility = "hidden";
            formularioAplazar.hidden = false;
            posicionarAplazamiento();
            formularioAplazar.style.visibility = "";
            $("reagendarAplazarMensaje").textContent = "";
            $("reagendarNuevaFecha").focus();
          });
          acciones.appendChild(aplazar);
        }
        const eliminar = document.createElement("button");
        eliminar.type = "button";
        eliminar.className = "btn btn--danger-ghost";
        eliminar.textContent = "Eliminar carga";
        eliminar.disabled = lote.estado === "EN_CURSO" || Number(lote.no_eliminables) > 0;
        eliminar.title = eliminar.disabled
          ? "No se puede borrar: contiene envíos, intentos o bajas que deben conservarse."
          : "Borrar los pacientes nuevos de esta carga antes de que se envíen";
        eliminar.addEventListener("click", () => abrirEliminarCarga(lote));
        acciones.appendChild(eliminar);
        tr.appendChild(acciones); cuerpo.appendChild(tr);
      }
      actualizarSeleccionLote();
      botonMas.hidden = lotes.length < 50;
      $("reagendarLotesMensaje").textContent = cuerpo.rows.length
        ? "«Ver detalle» muestra los pacientes nuevos de esa carga; los ya registrados no se duplican."
        : "Aún no hay cargas. Aparecerán cuando Reservo envíe un CSV.";
    } catch (e) {
      if (agregar) paginaLotes -= 1;
      $("reagendarLotesMensaje").textContent = e.message;
    } finally {
      botonMas.disabled = false;
      tabla.setAttribute("aria-busy", "false");
    }
  }
  async function cargarVista(id) {
    const caja = $("reagendarVistaPrevia");
    caja.hidden = false; caja.replaceChildren();
    const titulo = document.createElement("h3"); titulo.textContent = `Vista previa de la carga #${id}`; caja.appendChild(titulo);
    const ayuda = document.createElement("p");
    ayuda.className = "field__hint";
    ayuda.textContent = "Muestra la plantilla vigente de cada clínica. Se asigna en Configuración → Integraciones, no en esta carga.";
    caja.appendChild(ayuda);
    try {
      const res = await fetch(`api/v1/paciente-reagendar/lotes/${id}/vista-previa`, { headers: auth(), cache: "no-store" });
      if (!res.ok) throw new Error("No se pudo cargar la vista previa.");
      const data = await res.json();
      if (loteActual !== id) return;
      for (const item of data.items || []) {
        const bloque = document.createElement("div"); bloque.className = "reagendar__ejemplo";
        const nombre = document.createElement("strong");
        nombre.textContent = `${item.clinica} · ${item.plantilla || "Sin plantilla aprobada"}`;
        const detalle = document.createElement("small");
        detalle.textContent = `Emisor: ${item.emisor || "sin configurar"} · ${item.estado}`;
        const contenido = document.createElement("p"); contenido.textContent = item.ejemplo || "Sin vista previa.";
        bloque.append(nombre, detalle, contenido); caja.appendChild(bloque);
      }
      if (!(data.items || []).length) {
        const vacio = document.createElement("p"); vacio.textContent = "Esta carga no tiene clínicas con plantilla para mostrar.";
        caja.appendChild(vacio);
      }
    } catch (e) {
      if (loteActual !== id) return;
      const p = document.createElement("p"); p.textContent = e.message; caja.appendChild(p);
    }
    if (!card.hidden && loteActual === id) {
      caja.focus({ preventScroll: true });
      caja.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "nearest" });
    }
  }
  async function cargar() {
    const f = $("reagendarFecha").value.trim();
    if (f && !/^\d{4}-\d{2}-\d{2}$/.test(f)) {
      mensaje.textContent = "Usa el formato AAAA-MM-DD para la fecha.";
      return;
    }
    const params = new URLSearchParams({ pagina: String(pagina), limite: "25" });
    if (loteActual) params.set("lote_id", String(loteActual));
    for (const [id, clave] of [["reagendarCentro", "centro"], ["reagendarFecha", "fecha"],
                               ["reagendarVentana", "ventana"], ["reagendarEstado", "estado"],
                               ["reagendarModo", "modo_prueba"]]) {
      const valor = $(id).value.trim();
      if (valor) params.set(clave, valor);
    }
    mensaje.textContent = "Cargando pacientes…";
    $("tablaReagendar").setAttribute("aria-busy", "true");
    try {
      const res = await fetch(`api/v1/paciente-reagendar?${params}`, {
        headers: { Authorization: "Bearer " + (localStorage.getItem("snw_token") || "") }, cache: "no-store" });
      if (!res.ok) throw new Error("No se pudieron cargar los pacientes.");
      const datos = await res.json();
      if (pagina > 1 && (datos.total || 0) <= (pagina - 1) * 25) {
        pagina -= 1;
        return cargar();
      }
      tbody.replaceChildren();
      for (const r of datos.items || []) {
        const tr = document.createElement("tr");
        for (const valor of [fecha(r.fecha), r.ventana, r.clinica_nombre,
                             r.paciente_nombre, r.paciente_telefono_normalizado,
                             r.doctor_nombre, r.modo_prueba ? "Prueba" : "Operativo",
                             r.estado_envio, r.intentos_envio,
                             fechaHoraChile(r.recibido_en), r.ultima_respuesta,
                             r.ultimo_error_codigo]) celda(tr, valor);
        const acciones = document.createElement("td");
        const eliminar = document.createElement("button");
        eliminar.type = "button";
        eliminar.className = "btn btn--danger-ghost";
        eliminar.textContent = "Quitar";
        eliminar.disabled = ["PROCESANDO", "ENVIADO"].includes(r.estado_envio)
          || Number(r.intentos_envio) > 0 || Boolean(r.whatsapp_opt_out)
          || Boolean(r.envio_id || r.log_envio_id || r.whatsapp_message_id)
          || ["PROCESANDO", "ENVIADO"].includes(r.destinatario_estado);
        eliminar.title = eliminar.disabled
          ? "Ya tiene un envío, un intento o una baja que debe conservarse."
          : "Quitar a este paciente antes de enviar la plantilla";
        eliminar.addEventListener("click", () => abrirEliminarPaciente(r));
        acciones.appendChild(eliminar);
        tr.appendChild(acciones);
        tbody.appendChild(tr);
      }
      paginas = Math.max(1, Math.ceil((datos.total || 0) / 25));
      $("reagendarIr").value = String(pagina);
      $("reagendarIr").max = String(paginas);
      $("reagendarPrimera").disabled = $("reagendarAnterior").disabled = pagina <= 1;
      $("reagendarUltima").disabled = $("reagendarSiguiente").disabled = pagina >= paginas;
      mensaje.textContent = datos.total ? `Página ${pagina} de ${paginas} · ${datos.total} pacientes` : "No hay pacientes para estos filtros.";
    } catch (e) {
      mensaje.textContent = e.message;
    } finally {
      $("tablaReagendar").setAttribute("aria-busy", "false");
    }
  }
  async function centros() {
    try {
      const res = await fetch("api/whatsapp/centros", {
        headers: { Authorization: "Bearer " + (localStorage.getItem("snw_token") || "") } });
      if (!res.ok) return;
      const data = await res.json();
      for (const item of (Array.isArray(data) ? data : [])) {
        const option = document.createElement("option");
        option.value = item.codigo;
        option.textContent = item.nombre;
        $("reagendarCentro").appendChild(option);
      }
    } catch (_) { /* La tabla puede cargarse sin el selector. */ }
  }
  $("reagendarFiltrar").addEventListener("click", () => { pagina = 1; cargar(); });
  $("reagendarMasLotes").addEventListener("click", () => {
    paginaLotes += 1; cargarLotes(true);
  });
  $("reagendarActualizar").addEventListener("click", () => { cargarLotes(); cargar(); if (loteActual) cargarVista(loteActual); });
  $("reagendarVerTodas").addEventListener("click", () => {
    loteActual = null; pagina = 1;
    $("reagendarVistaPrevia").hidden = true;
    actualizarSeleccionLote(); cargar();
  });
  $("reagendarCerrarAplazar").addEventListener("click", () => cerrarAplazamiento(true));
  formularioAplazar.addEventListener("submit", async e => {
    e.preventDefault();
    const input = $("reagendarNuevaFecha").value.trim();
    const msg = $("reagendarAplazarMensaje");
    if (!/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/.test(input)) {
      msg.textContent = "Escribe la fecha y hora como AAAA-MM-DD HH:MM."; return;
    }
    const boton = e.submitter || formularioAplazar.querySelector('[type="submit"]');
    boton.disabled = true; msg.textContent = "Guardando…";
    try {
      const res = await fetch(`api/v1/paciente-reagendar/lotes/${loteAplazarId}/aplazar`, {
        method: "POST", headers: { ...auth(), "Content-Type": "application/json" },
        body: JSON.stringify({ fecha_local: input, motivo: $("reagendarMotivo").value.trim() }) });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "No se pudo aplazar la carga.");
      cerrarAplazamiento(); await cargarLotes();
      $("reagendarLotesMensaje").textContent = "Aplazamiento guardado.";
    } catch (error) { msg.textContent = error.message; }
    finally { boton.disabled = false; }
  });
  for (const [id, destino] of [["reagendarPrimera", () => 1], ["reagendarAnterior", () => pagina - 1],
                               ["reagendarSiguiente", () => pagina + 1], ["reagendarUltima", () => paginas]]) {
    $(id).addEventListener("click", () => { pagina = destino(); cargar(); });
  }
  $("reagendarIr").addEventListener("change", e => {
    pagina = Math.min(paginas, Math.max(1, Number(e.target.value) || 1)); cargar();
  });
  let iniciada = false;
  window.snwCargarReagendar = function () {
    if (iniciada) return;
    iniciada = true;
    centros();
    cargarLotes();
    cargar();
  };
})();
