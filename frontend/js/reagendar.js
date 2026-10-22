(function () {
  const card = document.getElementById("cardReagendar");
  if (!card || !window.snwEsPrivilegiado) return;
  card.hidden = false;
  const $ = id => document.getElementById(id);
  let pagina = 1;
  let paginas = 1;
  const tbody = $("tablaReagendar").querySelector("tbody");
  const mensaje = $("reagendarMensaje");
  const celda = (tr, valor) => {
    const td = document.createElement("td");
    td.textContent = valor == null ? "—" : String(valor);
    tr.appendChild(td);
  };
  const fecha = valor => valor ? String(valor).slice(0, 10) : "—";
  async function cargar() {
    const f = $("reagendarFecha").value.trim();
    if (f && !/^\d{4}-\d{2}-\d{2}$/.test(f)) {
      mensaje.textContent = "Usa el formato AAAA-MM-DD para la fecha.";
      return;
    }
    const params = new URLSearchParams({ pagina: String(pagina), limite: "25" });
    for (const [id, clave] of [["reagendarCentro", "centro"], ["reagendarFecha", "fecha"],
                               ["reagendarVentana", "ventana"], ["reagendarEstado", "estado"]]) {
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
                             r.doctor_nombre, r.estado_envio, r.intentos_envio,
                             fecha(r.enviado_en), r.ultimo_error_codigo]) celda(tr, valor);
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
  $("reagendarActualizar").addEventListener("click", cargar);
  for (const [id, destino] of [["reagendarPrimera", () => 1], ["reagendarAnterior", () => pagina - 1],
                               ["reagendarSiguiente", () => pagina + 1], ["reagendarUltima", () => paginas]]) {
    $(id).addEventListener("click", () => { pagina = destino(); cargar(); });
  }
  $("reagendarIr").addEventListener("change", e => {
    pagina = Math.min(paginas, Math.max(1, Number(e.target.value) || 1)); cargar();
  });
  centros();
  cargar();
})();
