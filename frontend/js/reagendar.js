(function () {
  const card = document.getElementById("cardReagendar");
  if (!card || !window.snwEsPrivilegiado) return;
  const $ = id => document.getElementById(id);
  let pagina = 1;
  let paginas = 1;
  let paginaLotes = 1;
  let loteActual = null;
  let loteAplazarId = null;
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
        celdaResumen(tr, String(lote.enviados || 0), "Meta aceptó; no implica entrega");
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
      tbody.replaceChildren();
      for (const r of datos.items || []) {
        const tr = document.createElement("tr");
        for (const valor of [fecha(r.fecha), r.ventana, r.clinica_nombre,
                             r.paciente_nombre, r.paciente_telefono_normalizado,
                             r.doctor_nombre, r.modo_prueba ? "Prueba" : "Operativo",
                             r.estado_envio, r.intentos_envio,
                             fecha(r.enviado_en), r.ultima_respuesta, r.ultimo_error_codigo]) celda(tr, valor);
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
