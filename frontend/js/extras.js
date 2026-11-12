(function () {
  if (!window.snwEsPrivilegiado) return;

  const $ = (id) => document.getElementById(id);
  const api = "/api/extras/prepago";
  const modal = $("modal");
  const botonesAjustes = ["agregar", "destino", "operadores", "plazo"];
  let datos;
  let versionModal = 0;

  function escapar(valor) {
    return String(valor ?? "").replace(/[&<>"']/g, (c) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));
  }

  function mensaje(id, texto, error = false) {
    $(id).textContent = texto;
    $(id).dataset.error = error;
  }

  async function pedir(ruta = "", metodo = "GET", cuerpo) {
    const respuesta = await fetch(api + ruta, {
      method: metodo,
      headers: { Authorization: `Bearer ${localStorage.getItem("snw_token")}`, "Content-Type": "application/json" },
      body: cuerpo === undefined ? undefined : JSON.stringify(cuerpo),
      cache: "no-store",
    });
    if (respuesta.status === 401) window.snwSesionExpirada();
    const json = await respuesta.json().catch(() => ({}));
    if (!respuesta.ok) {
      throw new Error(typeof json.detail === "string" ? json.detail : `No se pudo completar la solicitud (HTTP ${respuesta.status}).`);
    }
    return json;
  }

  function fecha(iso) {
    return iso ? iso.slice(0, 10).split("-").reverse().join("-") : "—";
  }

  function pintar() {
    const diasAviso = datos.recordatorio_dias === 1 ? "1 día" : `${datos.recordatorio_dias} días`;
    $("ayudaPlazo").textContent = `El aviso vence en ${diasAviso} desde la última recarga. Si sigue pendiente, SNW envía un aviso urgente cuatro días después del primer correo.`;
    const activas = datos.lineas.filter((l) => l.activo);
    $("totalActivas").textContent = activas.length;
    $("totalPendientes").textContent = activas.filter((l) => l.estado === "Pendiente").length;
    $("totalConfigurar").textContent = activas.filter((l) => l.estado === "Sin configurar").length;
    $("resumen").hidden = false;
    $("correoEstado").hidden = datos.correo_listo;
    const busqueda = $("buscar").value.trim().toLocaleLowerCase("es");
    const filtro = $("filtroActivo").value;
    $("resumen").querySelectorAll("[data-filtro]").forEach((boton) => {
      boton.setAttribute("aria-pressed", String(boton.dataset.filtro === filtro));
    });
    const orden = { Pendiente: 0, "Sin configurar": 1, Próximo: 2, "Al día": 3 };
    const lineas = datos.lineas.filter((l) => {
      const clinica = datos.clinicas[l.clinica] || "";
      const texto = `${l.nombre} ${l.telefono || ""} ${l.compania} ${clinica}`.toLocaleLowerCase("es");
      if (!texto.includes(busqueda)) return false;
      if (filtro === "todas") return true;
      if (filtro === "0") return !l.activo;
      if (!l.activo) return false;
      return filtro === "1" || l.estado === (filtro === "pendientes" ? "Pendiente" : "Sin configurar");
    }).sort((a, b) => Number(b.activo) - Number(a.activo) || orden[a.estado] - orden[b.estado]
      || a.dias_restantes - b.dias_restantes);
    $("lineas").innerHTML = lineas.map(pintarLinea).join("")
      || '<tr class="extras-vacio" role="row"><td colspan="4" role="cell">No hay líneas que coincidan con el filtro.</td></tr>';
  }

  function pintarLinea(l) {
    const estado = l.activo ? l.estado : "Desactivada";
    const clinica = datos.clinicas[l.clinica];
    const enlace = l.url_recarga?.startsWith("https://") ? l.url_recarga : "";
    const dias = l.dias_restantes;
    let detalle = "";
    if (l.activo && estado === "Pendiente" && dias == null) {
      detalle = "Registra la primera recarga para iniciar el plazo.";
    } else if (l.activo && estado !== "Sin configurar" && dias != null) {
      detalle = dias < 0 ? `Venció el ${fecha(l.proximo_recordatorio)}`
        : dias === 0 ? "Vence hoy" : `${fecha(l.proximo_recordatorio)} · ${dias === 1 ? "Falta 1 día" : `Faltan ${dias} días`}`;
    }
    const recargar = enlace
      ? `<a class="btn ${l.ultima_recarga && l.telefono ? "btn--primary" : "btn--ghost"}" href="${escapar(enlace)}"
          target="_blank" rel="noopener noreferrer">Recargar <i class="fa-solid fa-arrow-up-right-from-square" aria-hidden="true"></i></a>`
      : "";
    const registrar = l.telefono
      ? `<button class="btn ${l.ultima_recarga ? "btn--ghost" : "btn--primary"}" type="button" data-accion="recarga">${l.ultima_recarga ? "Registrar recarga" : "Registrar primera recarga"}</button>`
      : '<button class="btn btn--primary" type="button" data-accion="editar">Asignar número</button>';
    return `<tr data-id="${l.id}" role="row">
      <th scope="row" role="rowheader">
        <strong>${escapar(l.nombre)}</strong>
        <small>${escapar([l.compania, l.sistema, clinica].filter(Boolean).join(" · "))}</small>
        <div class="extras-telefono">
          <span>${escapar(l.telefono || "Sin número asignado")}</span>
          ${l.telefono ? '<button class="btn btn--ghost extras-copiar" type="button" data-accion="copiar" title="Copiar número" aria-label="Copiar número"><i class="fa-regular fa-copy" aria-hidden="true"></i></button>' : ""}
        </div>
      </th>
      <td class="extras-fecha" role="cell">
        <span class="extras-etiqueta-movil">Última recarga</span>
        <strong>${l.ultima_recarga ? fecha(l.ultima_recarga) : "Sin registrar"}</strong>
        ${l.dias_transcurridos != null ? `<small>${l.dias_transcurridos === 0 ? "Hoy" : l.dias_transcurridos === 1 ? "Hace 1 día" : `Hace ${l.dias_transcurridos} días`}</small>` : ""}
      </td>
      <td class="extras-ciclo" role="cell">
        <span class="extras-etiqueta-movil">Estado del aviso</span>
        <span class="extras-estado" data-estado="${escapar(estado)}">${estado === "Pendiente" ? "Vencida" : escapar(estado)}</span>
        ${detalle ? `<small>${detalle}</small>` : ""}
        ${!l.avisar && l.activo ? "<small>Correo desactivado para esta línea</small>" : ""}
      </td>
      <td class="extras-fila-acciones" role="cell">
        <div class="extras-actions">${l.ultima_recarga && l.telefono ? recargar + registrar : registrar + recargar}</div>
        <div class="extras-actions extras-secundarias">
          ${l.telefono ? '<button class="btn btn--ghost" type="button" data-accion="editar">Editar</button>' : ""}
          <button class="btn btn--ghost" type="button" data-accion="historial">Ver historial</button>
        </div>
        ${enlace ? "" : "<small>El operador no tiene un portal verificado.</small>"}
      </td>
    </tr>`;
  }

  async function cargar() {
    $("panelPrepago").setAttribute("aria-busy", "true");
    try {
      datos = await pedir();
      for (const id of botonesAjustes) $(id).disabled = false;
      pintar();
    } finally {
      $("panelPrepago").removeAttribute("aria-busy");
    }
  }

  function abrir(titulo, contenido) {
    versionModal += 1;
    $("ajustes").open = false;
    $("modalTitulo").textContent = titulo;
    $("modalCuerpo").innerHTML = contenido;
    mensaje("modalEstado", "");
    if (!modal.open) modal.showModal();
  }

  function opciones(lista, elegido) {
    return lista.map(([valor, texto]) =>
      `<option value="${escapar(valor)}" ${valor === elegido ? "selected" : ""}>${escapar(texto)}</option>`
    ).join("");
  }

  const guardar = '<button class="btn btn--primary" type="submit">Guardar</button>';

  function guardarFormulario(accion, texto) {
    const form = $("modalCuerpo").querySelector("form");
    form.addEventListener("submit", async (evento) => {
      evento.preventDefault();
      const boton = form.querySelector('[type="submit"]');
      if (boton.disabled) return;
      const apertura = versionModal;
      boton.disabled = true;
      mensaje("modalEstado", "Guardando…");
      try {
        await accion(form, Object.fromEntries(new FormData(form)));
      } catch (error) {
        if (apertura === versionModal && modal.open) mensaje("modalEstado", error.message, true);
        return;
      } finally {
        boton.disabled = false;
      }
      if (apertura === versionModal && modal.open) modal.close();
      mensaje("estado", texto);
      cargar().catch((error) => mensaje("estado", `${texto} ${error.message}`, true));
    });
  }

  function editarLinea(l) {
    const ops = datos.operadores.map((o) => [o.codigo, o.nombre]);
    abrir(l ? (l.telefono ? "Editar línea" : "Asignar número") : "Agregar número", `<form>
      <div class="extras-form-grid">
        <label class="field"><span>Nombre</span><input name="nombre" type="text" required maxlength="80" value="${escapar(l?.nombre)}"></label>
        <label class="field"><span>Número móvil</span><input name="telefono" type="tel" maxlength="24" ${l?.semilla ? "" : "required"}
            placeholder="+56 9" value="${escapar(l?.telefono)}"></label>
        <label class="field"><span>Compañía</span><select name="operador" required>${opciones(ops, l?.operador || "wom")}</select></label>
        <label class="field"><span>Sistema o uso</span><select name="sistema">${opciones(["SNW", "CRM", "Personal", "Otro"].map((s) => [s, s]), l?.sistema || "Otro")}</select></label>
        <label class="field"><span>Clínica</span><select name="clinica">${opciones([["", "Sin clínica"], ...Object.entries(datos.clinicas)], l?.clinica || "")}</select></label>
        ${l?.ultima_recarga ? `<label class="field"><span>Última recarga</span><input name="ultima_recarga" type="date"
            min="1900-01-01" max="${datos.hoy}" value="${escapar(l.ultima_recarga)}" required></label>` : ""}
      </div>
      <label class="extras-check"><input type="checkbox" name="avisar" ${!l || l.avisar ? "checked" : ""}> <span>Enviar recordatorios por correo</span></label>
      <label class="extras-check"><input type="checkbox" name="activo" ${!l || l.activo ? "checked" : ""}> <span>Línea activa</span></label>
      <div class="extras-actions">${guardar}</div>
    </form>`);
    guardarFormulario((form, body) => pedir(l ? `/lineas/${l.id}` : "/lineas", l ? "PUT" : "POST", {
      ...body, ultima_recarga: body.ultima_recarga || null,
      activo: form.elements.activo.checked, avisar: form.elements.avisar.checked, version: l?.version,
    }), l ? "Línea actualizada." : "Número agregado.");
  }

  function registrarRecarga(l) {
    abrir("Registrar recarga", `<form>
      <p>${escapar(l.nombre)} · ${escapar(l.telefono)}</p>
      <label class="field"><span>Fecha real de recarga</span><input name="fecha" type="date" required min="1900-01-01"
          max="${datos.hoy}" value="${datos.hoy}"></label>
      <p class="extras-status">El próximo aviso se calcula desde esta fecha.</p>
      <label class="field"><span>Observación (opcional)</span><textarea name="observacion" maxlength="500" rows="2"></textarea></label>
      <div class="extras-actions"><button class="btn btn--primary" type="submit">Confirmar recarga</button></div>
    </form>`);
    guardarFormulario((_, body) => pedir(`/lineas/${l.id}/recargas`, "POST", {
      ...body, version: l.version,
    }), "Recarga registrada. Se actualizó la fecha del próximo aviso.");
  }

  async function historial(l, pagina = 1) {
    abrir(`Historial de ${l.nombre}`, '<p class="extras-status">Cargando historial…</p>');
    const apertura = versionModal;
    const h = await pedir(`/lineas/${l.id}/historial?pagina=${pagina}`);
    if (!modal.open || versionModal !== apertura) return;
    const tipos = { inicial: "Fecha inicial", correccion: "Corrección", recarga: "Recarga" };
    const estados = { enviado: "Enviado", error: "Error de envío", incierto: "Entrega incierta", cancelado: "Cancelado" };
    const recargas = h.recargas.map((r) => `<li>${fecha(r.fecha)} · ${tipos[r.tipo]} · ${escapar(r.registrado_por)}
      ${r.fecha_anterior ? `<small>(anterior: ${fecha(r.fecha_anterior)})</small>` : ""}
      ${r.observacion ? `<br>${escapar(r.observacion)}` : ""}</li>`).join("");
    const avisos = h.avisos.map((a) => `<li>${fecha(a.programado)} · ${a.etapa === "urgente" ? "Aviso urgente" : "Primer aviso"} · ${estados[a.estado]} · ${escapar(a.destinatario)}
      <br><small>Intento: ${fecha(a.intento_en)}${a.enviado_en ? ` · Enviado: ${fecha(a.enviado_en)}` : ""}
      ${a.error ? ` · ${escapar(a.error)}` : ""}</small></li>`).join("");
    $("modalCuerpo").innerHTML = `<h3>Recargas</h3><ul class="extras-historial">${recargas || "<li>Sin recargas en esta página.</li>"}</ul>
      <h3>Recordatorios</h3><ul class="extras-historial">${avisos || "<li>Sin recordatorios en esta página.</li>"}</ul>
      <div class="extras-actions">
        <button class="btn btn--ghost" type="button" id="histAnterior" ${pagina === 1 ? "disabled" : ""}>Anterior</button>
        <span>Página ${pagina}</span>
        <button class="btn btn--ghost" type="button" id="histSiguiente" ${h.mas ? "" : "disabled"}>Siguiente</button>
      </div>`;
    const ir = (destino) => historial(l, destino).catch((error) => mensaje("modalEstado", error.message, true));
    $("histAnterior").onclick = () => ir(pagina - 1);
    $("histSiguiente").onclick = () => ir(pagina + 1);
  }

  function operadores(codigo = "") {
    const o = datos.operadores.find((op) => op.codigo === codigo);
    abrir("Operadores de prepago", `<form>
      <label class="field"><span>Operador</span><select id="operadorElegido">${opciones([
        ["", "Agregar operador"], ...datos.operadores.map((op) => [op.codigo, op.nombre]),
      ], codigo)}</select></label>
      <div class="extras-form-grid">
        <label class="field"><span>Código</span><input name="codigo" type="text" required maxlength="32" pattern="[A-Za-z0-9_\\-]+" value="${escapar(o?.codigo)}" ${o ? "readonly" : ""}></label>
        <label class="field"><span>Nombre</span><input name="nombre" type="text" required maxlength="80" value="${escapar(o?.nombre)}"></label>
        <label class="field"><span>Dominio oficial</span><input name="dominio" type="text" maxlength="253" value="${escapar(o?.dominio)}" ${o?.dominio ? "readonly" : ""}></label>
        <label class="field"><span>URL de recarga</span><input name="url_recarga" type="url" maxlength="500" value="${escapar(o?.url_recarga)}"></label>
      </div>
      <label class="extras-check"><input type="checkbox" name="verificado"> <span>Verifiqué la página de recargas en el sitio oficial</span></label>
      <p class="extras-status">Puedes guardar un operador sin enlace y añadirlo después.</p>
      <div class="extras-actions">${guardar}</div>
    </form>`);
    $("operadorElegido").onchange = (evento) => operadores(evento.target.value);
    guardarFormulario((form, body) => pedir("/operadores", "PUT", {
      ...body, verificado: form.elements.verificado.checked,
    }), "Operador guardado.");
  }

  $("cerrar").onclick = () => modal.close();
  $("agregar").onclick = () => editarLinea();
  $("operadores").hidden = !window.snwEsDev;
  $("operadores").onclick = () => operadores();
  $("plazo").onclick = () => {
    abrir("Plazo del recordatorio", `<form>
      <label class="field"><span>Días desde la última recarga</span><input type="number" name="dias" required
          min="1" max="${datos.recordatorio_dias_max}" step="1" value="${datos.recordatorio_dias}"></label>
      <p class="extras-status">El plazo se aplica a todas las líneas desde su última recarga registrada.</p>
      <div class="extras-actions">${guardar}</div>
    </form>`);
    guardarFormulario((form) => pedir("/plazo", "PUT", {
      dias: form.elements.dias.valueAsNumber,
    }), "Plazo del recordatorio actualizado.");
  };
  $("destino").onclick = () => {
    abrir("Correo de aviso", `<form>
      <label class="field"><span>Correo para recordatorios</span><input type="email" name="correo" maxlength="254"
          value="${escapar(datos.destino)}"></label>
      <p class="extras-status">${datos.destino_general
        ? `Si lo dejas vacío, se usará el correo administrativo (${escapar(datos.destino_general)}).`
        : "Configura un destinatario para activar los avisos."}</p>
      <div class="extras-actions">${guardar}</div>
    </form>`);
    guardarFormulario((_, body) => pedir("/destino", "PUT", body), "Correo de aviso actualizado.");
  };
  $("revisarCorreo").onclick = () => $("destino").click();

  $("lineas").onclick = async (evento) => {
    const boton = evento.target.closest("[data-accion]");
    if (!boton) return;
    const l = datos.lineas.find((linea) => String(linea.id) === boton.closest("tr").dataset.id);
    try {
      if (boton.dataset.accion === "editar") editarLinea(l);
      if (boton.dataset.accion === "recarga") registrarRecarga(l);
      if (boton.dataset.accion === "historial") await historial(l);
      if (boton.dataset.accion === "copiar") {
        await navigator.clipboard.writeText(l.telefono);
        mensaje("estado", "Número copiado.");
      }
    } catch (error) {
      if (modal.open) mensaje("modalEstado", error.message, true);
      else mensaje("estado", "No se pudo copiar el número. Puedes seleccionarlo en la tabla.", true);
    }
  };
  $("resumen").onclick = (evento) => {
    const filtro = evento.target.closest("[data-filtro]");
    if (!filtro || !datos) return;
    $("filtroActivo").value = filtro.dataset.filtro;
    pintar();
  };
  $("buscar").oninput = () => { if (datos) pintar(); };
  $("filtroActivo").onchange = () => { if (datos) pintar(); };
  for (const id of botonesAjustes) $(id).disabled = true;
  $("extras").hidden = false;
  mensaje("estado", "Cargando líneas…");
  cargar().then(() => mensaje("estado", ""), (error) => mensaje("estado", error.message, true));
}());
