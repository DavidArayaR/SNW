(function () {
  const card = document.getElementById("cardReagendar");
  if (!card || !window.snwEsPrivilegiado) return;
  card.hidden = false;
  const $ = id => document.getElementById(id);
  let pagina = 1;
  let paginas = 1;
  let loteActual = null;
  const tbody = $("tablaReagendar").querySelector("tbody");
  const mensaje = $("reagendarMensaje");
  const celda = (tr, valor) => {
    const td = document.createElement("td");
    td.textContent = valor == null ? "—" : String(valor);
    tr.appendChild(td);
  };
  const fecha = valor => valor ? String(valor).slice(0, 10) : "—";
  const fechaHoraChile = valor => {
    if (!valor) return "—";
    const utc = /(?:Z|[+-]\d\d:\d\d)$/.test(String(valor)) ? String(valor) : `${valor}Z`;
    const d = new Date(utc);
    return Number.isNaN(d.getTime()) ? "—" : new Intl.DateTimeFormat("es-CL", {
      timeZone: "America/Santiago", dateStyle: "short", timeStyle: "short" }).format(d);
  };
  const auth = () => ({ Authorization: "Bearer " + (localStorage.getItem("snw_token") || "") });
  async function cargarLotes() {
    const tabla = $("tablaReagendarLotes");
    const cuerpo = tabla.querySelector("tbody");
    tabla.setAttribute("aria-busy", "true");
    $("reagendarLotesMensaje").textContent = "Cargando programaciones…";
    try {
      const res = await fetch("api/v1/paciente-reagendar/lotes", { headers: auth(), cache: "no-store" });
      if (!res.ok) throw new Error("No se pudieron cargar las programaciones.");
      const lotes = await res.json();
      cuerpo.replaceChildren();
      for (const lote of lotes) {
        const tr = document.createElement("tr");
        for (const valor of [fecha(lote.fecha), lote.ventana, fechaHoraChile(lote.recibido_en),
          fechaHoraChile(lote.programado_para), lote.estado,
          `${lote.creados} importadas · ${lote.omitidos} excluidas · ${lote.rechazados} rechazadas`, lote.enviados]) celda(tr, valor);
        const acciones = document.createElement("td");
        const revisar = document.createElement("button");
        revisar.type = "button"; revisar.className = "btn btn--ghost"; revisar.textContent = "Revisar";
        revisar.addEventListener("click", () => { loteActual = lote.id; pagina = 1; cargar(); cargarVista(lote.id); });
        acciones.appendChild(revisar);
        if (["PROGRAMADO", "APLAZADO", "REVISION"].includes(lote.estado)) {
          const aplazar = document.createElement("button");
          aplazar.type = "button"; aplazar.className = "btn btn--ghost"; aplazar.textContent = "Aplazar";
          aplazar.addEventListener("click", () => {
            loteActual = lote.id;
            $("reagendarAplazarTitulo").textContent = `Aplazar carga #${lote.id}`;
            $("reagendarAplazarForm").hidden = false;
            $("reagendarAplazarMensaje").textContent = "";
            $("reagendarNuevaFecha").focus();
          });
          acciones.appendChild(aplazar);
        }
        tr.appendChild(acciones); cuerpo.appendChild(tr);
      }
      $("reagendarLotesMensaje").textContent = lotes.length ? "Selecciona Revisar para ver las citas y la plantilla vigente." : "Aún no hay cargas.";
    } catch (e) { $("reagendarLotesMensaje").textContent = e.message; }
    finally { tabla.setAttribute("aria-busy", "false"); }
  }
  async function cargarVista(id) {
    const caja = $("reagendarVistaPrevia");
    caja.hidden = false; caja.replaceChildren();
    const titulo = document.createElement("h3"); titulo.textContent = `Plantillas de la carga #${id}`; caja.appendChild(titulo);
    try {
      const res = await fetch(`api/v1/paciente-reagendar/lotes/${id}/vista-previa`, { headers: auth(), cache: "no-store" });
      if (!res.ok) throw new Error("No se pudo cargar la vista previa.");
      const data = await res.json();
      for (const item of data.items || []) {
        const bloque = document.createElement("div"); bloque.className = "reagendar__ejemplo";
        const nombre = document.createElement("strong");
        nombre.textContent = `${item.clinica} · ${item.emisor || "Sin emisor"} · ${item.plantilla || "Sin plantilla"} · ${item.idioma || "Sin idioma"} · ${item.estado}`;
        const contenido = document.createElement("p"); contenido.textContent = item.ejemplo || "Sin vista previa.";
        bloque.append(nombre, contenido); caja.appendChild(bloque);
      }
    } catch (e) { const p = document.createElement("p"); p.textContent = e.message; caja.appendChild(p); }
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
    mensaje.textContent = "Cargando citas…";
    $("tablaReagendar").setAttribute("aria-busy", "true");
    try {
      const res = await fetch(`api/v1/paciente-reagendar?${params}`, {
        headers: { Authorization: "Bearer " + (localStorage.getItem("snw_token") || "") }, cache: "no-store" });
      if (!res.ok) throw new Error("No se pudieron cargar las citas.");
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
      mensaje.textContent = datos.total ? `Página ${pagina} de ${paginas} · ${datos.total} citas` : "No hay citas para estos filtros.";
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
  $("reagendarActualizar").addEventListener("click", () => { cargarLotes(); cargar(); });
  $("reagendarCerrarAplazar").addEventListener("click", () => { $("reagendarAplazarForm").hidden = true; });
  $("reagendarAplazarForm").addEventListener("submit", async e => {
    e.preventDefault();
    const input = $("reagendarNuevaFecha").value.trim();
    const msg = $("reagendarAplazarMensaje");
    if (!/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/.test(input)) {
      msg.textContent = "Escribe la fecha y hora como AAAA-MM-DD HH:MM."; return;
    }
    const boton = e.submitter || $("reagendarAplazarForm").querySelector('[type="submit"]');
    boton.disabled = true; msg.textContent = "Guardando…";
    try {
      const res = await fetch(`api/v1/paciente-reagendar/lotes/${loteActual}/aplazar`, {
        method: "POST", headers: { ...auth(), "Content-Type": "application/json" },
        body: JSON.stringify({ fecha_local: input, motivo: $("reagendarMotivo").value.trim() }) });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "No se pudo aplazar la carga.");
      $("reagendarAplazarForm").hidden = true; await cargarLotes();
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
  centros();
  cargarLotes();
  cargar();
})();
