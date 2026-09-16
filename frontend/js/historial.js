const API_HISTORIAL = "api/notificaciones/historial";

function authHeaders(extra = {}) {
  return { Authorization: "Bearer " + (localStorage.getItem("snw_token") || ""), ...extra };
}

if (!localStorage.getItem("snw_token")) location.replace("login.html");

let registros = [];
let filtro = "";
let ambienteDetalle = "produccion";
let pacienteMsgActual = null; // { id, ambiente, interesado }
let plantillasCC = [];        // plantillas de call center
let ccEditId = null;          // id en edición (null = nueva)
const MAX_CC = 1024;

const $ = (sel) => document.querySelector(sel);

const tbodyEl = $("#tablaHistorial tbody");
const vacioEl = $("#tablaVacia");
const buscadorEl = $("#buscador");
const statsEl = $("#stats");
const contadorEl = $("#contador");
const toastEl = $("#toast");

function escaparHtml(texto) {
  const div = document.createElement("div");
  div.textContent = texto ?? "";
  return div.innerHTML;
}

async function cargar() {
  try {
    const [rh, rc] = await Promise.all([
      fetch(`${API_HISTORIAL}?ambiente=todos`, { headers: authHeaders(), cache: "no-store" }),
      fetch(`api/configuracion?ambiente=produccion`, { headers: authHeaders(), cache: "no-store" }),
    ]);
    if (rh.status === 401 || rc.status === 401) { window.snwSesionExpirada(); return; }
    if (!rh.ok || !rc.ok) throw new Error();
    registros = await rh.json();
    $("#badgeEntorno").textContent = "Todas las bases de datos";
    render();
  } catch {
    toast("Error al conectar con el servidor.", "error");
  }
}

function render() {
  const q = filtro.trim().toLowerCase();
  const visibles = registros.filter(
    (r) => !q ||
      [r.base_datos, r.plantilla_clave, r.plantilla_nombre]
        .filter(Boolean)
        .some((v) => v.toLowerCase().includes(q))
  );

  tbodyEl.innerHTML = "";

  for (const r of visibles) {
    const tr = document.createElement("tr");
    tr.dataset.id = String(r.id);
    tr.dataset.amb = (r.base_datos ?? "").includes("prod") ? "produccion" : "desarrollo";
    tr.dataset.base = r.base_datos ?? "";
    const total = r.total_pacientes ?? 0;
    const enviados = r.enviados ?? 0;
    const fallidos = r.fallidos ?? 0;
    const invalidos = r.invalidos ?? 0;
    const estado = r.estado || "completado";
    const estadoLabel =
      { cancelado: "Cancelado", rechazado: "Rechazado" }[estado] || "Completado";
    const comentario = (r.comentario ?? "").trim();
    const notaRechazo = estado === "rechazado"
      ? `<div class="hist-rechazo">Rechazado por el supervisor${comentario ? `: «${escaparHtml(comentario)}»` : " (sin comentario)"}</div>`
      : "";
    tr.innerHTML =
      `<td class="campo-fecha">${escaparHtml(r.fecha)}</td>` +
      `<td><span class="badge badge--db">${escaparHtml(r.base_datos ?? "—")}</span></td>` +
      `<td>${escaparHtml(r.plantilla_nombre ?? r.plantilla_clave ?? "—")}${notaRechazo}</td>` +
      `<td><span class="estado-envio estado-envio--${escaparHtml(estado)}">${escaparHtml(estadoLabel)}</span></td>` +
      `<td class="campo-num">${total}</td>` +
      `<td class="campo-num campo-num--ok">${enviados}</td>` +
      `<td class="campo-num campo-num--error">${fallidos}</td>` +
      `<td class="campo-num campo-num--invalido">${invalidos}</td>` +
      `<td><button class="btn btn--sm btn--ghost" data-detalle="${r.id}">Ver detalle</button></td>`;
    tbodyEl.appendChild(tr);
  }

  vacioEl.hidden = visibles.length > 0;
  contadorEl.textContent = `${visibles.length} envío${visibles.length === 1 ? "" : "s"}`;

  const totalEnvios = registros.length;
  statsEl.innerHTML =
    `<button type="button" class="stat stat--total activo" data-estado="todos">Total envíos <strong>${totalEnvios}</strong></button>`;
}

tbodyEl.addEventListener("click", async (e) => {
  const tr = e.target.closest("tr[data-id]");
  if (!tr) return;
  const envioId = tr.dataset.id;
  const amb = tr.dataset.amb || "produccion";
  const envio = registros.find((r) => String(r.id) === envioId && ((r.base_datos ?? "").includes("prod") ? "produccion" : "desarrollo") === amb)
    || registros.find((r) => String(r.id) === envioId);
  ambienteDetalle = amb;

  try {
    const r = await fetch(`api/notificaciones/historial/${envioId}/detalle?ambiente=${amb}`, {
      headers: authHeaders(), cache: "no-store"
    });
    if (r.status === 401) { window.snwSesionExpirada(); return; }
    if (!r.ok) throw new Error();
    const detalle = await r.json();
    abrirDetalle(envio, detalle);
  } catch {
    toast("Error al cargar el detalle.", "error");
  }
});

function abrirDetalle(envio, detalle) {
  const plantilla = envio?.plantilla_nombre ?? envio?.plantilla_clave ?? "";
  $("#detalleTitulo").textContent = plantilla
    ? `Pacientes del envío · ${plantilla}`
    : "Pacientes del envío";

  const body = $("#detalleBody");
  body.innerHTML = "";

  // Columna extra "Detalle" con el botón «Ver mensajes».
  const thMsgs = $("#thDetalleMensajes");
  if (thMsgs) thMsgs.hidden = false;
  const cols = 4;

  if ((envio?.estado || "") === "rechazado") {
    const c = (envio.comentario ?? "").trim();
    body.innerHTML =
      `<tr><td colspan="${cols}" class="hist-rechazo-detalle">` +
      `Envío rechazado por el supervisor. No se envió ningún mensaje.` +
      (c ? `<br><span>Comentario: «${escaparHtml(c)}»</span>` : "") +
      `</td></tr>`;
    $("#modalDetalle").hidden = false;
    return;
  }

  if (!detalle.length) {
    body.innerHTML = `<tr><td colspan="${cols}" style="text-align:center;color:#66757f;">Sin detalle individual registrado.</td></tr>`;
  } else {
    const pesoRespuesta = { respondio: 0, baja: 1, pendiente: 2 };
    const orden = [...detalle].sort(
      (x, y) => (pesoRespuesta[x.respuesta] ?? 3) - (pesoRespuesta[y.respuesta] ?? 3) || (x.id - y.id)
    );
    for (const d of orden) {
      const r = d.respuesta ?? "pendiente";
      const msg = (d.mensaje_respuesta ?? "").trim();
      const msgHtml = msg
        ? `<div class="hist-msg${d.respuesta_interes ? " hist-msg--interes" : ""}">«${escaparHtml(msg)}»</div>`
        : "";
      const marcaInteres = d.interesado
        ? `<span class="hist-tag-interes" title="Marcado como interesado">interesado</span>`
        : "";
      const celdaMsgs = d.paciente_id
        ? `<td class="hist-col-msgs"><button type="button" class="btn btn--sm btn--ghost" data-mensajes="${d.paciente_id}">Ver mensajes</button></td>`
        : `<td class="hist-col-msgs">—</td>`;
      const tr = document.createElement("tr");
      tr.innerHTML =
        `<td class="campo-nombre">${escaparHtml(d.nombre_paciente ?? "—")}${marcaInteres}</td>` +
        `<td><span class="respuesta-badge respuesta-${escaparHtml(r)}">${escaparHtml(respuestaLabel(r))}</span>${msgHtml}</td>` +
        `<td class="campo-fecha">${escaparHtml(d.fecha ?? "—")}</td>` +
        celdaMsgs;
      body.appendChild(tr);
    }
  }

  $("#modalDetalle").hidden = false;
}

function respuestaLabel(r) {
  return { pendiente: "Sin respuesta", respondio: "Respondió", baja: "Se dio de baja" }[r] ?? r;
}

$("#btnCerrarDetalle").addEventListener("click", () => ($("#modalDetalle").hidden = true));
$("#modalDetalle").addEventListener("click", (e) => { if (e.target === $("#modalDetalle")) $("#modalDetalle").hidden = true; });

/* ---------- Mensajes del paciente / call center ---------- */

$("#detalleBody").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-mensajes]");
  if (btn) abrirMensajes(btn.dataset.mensajes);
});

async function abrirMensajes(pacienteId) {
  try {
    const r = await fetch(`api/pacientes/${pacienteId}/mensajes?ambiente=${ambienteDetalle}`, {
      headers: authHeaders(), cache: "no-store",
    });
    if (r.status === 401) { window.snwSesionExpirada(); return; }
    if (!r.ok) throw new Error();
    const data = await r.json();
    renderMensajes(data);
  } catch {
    toast("No se pudieron cargar los mensajes del paciente.", "error");
  }
}

function renderMensajes(data) {
  const pac = data.paciente || {};
  pacienteMsgActual = { id: pac.id, ambiente: ambienteDetalle, interesado: !!pac.interesado };

  const nombre = [pac.nombre, pac.apellido].filter(Boolean).join(" ") || "Paciente";
  $("#mensajesTitulo").textContent = `Mensajes · ${nombre}`;

  const estado = pac.respuesta === "baja" ? "Se dio de baja"
    : pac.respuesta === "respondio" ? "Respondió" : "Sin respuesta";
  $("#msgPacienteCab").innerHTML =
    `<span>${escaparHtml(pac.telefono ?? "")}</span>` +
    `<span class="msg-cab__estado">${escaparHtml(estado)}</span>` +
    (pac.interesado ? `<span class="hist-tag-interes">interesado</span>` : "");

  const hilo = $("#msgHilo");
  const msgs = data.mensajes || [];
  if (!msgs.length) {
    hilo.innerHTML = `<p class="msg-vacio">El paciente todavía no ha escrito ningún mensaje.</p>`;
  } else {
    hilo.innerHTML = msgs.map((m) => {
      const texto = m.texto || (m.direccion === "saliente" ? `(plantilla: ${m.plantilla ?? "—"})` : "");
      const meta = `${m.direccion === "entrante" ? "Paciente" : "Sistema"} · ${escaparHtml(m.fecha)}` +
        (m.interes ? " · interés" : "") +
        (m.estado === "error" ? " · no entregado" : "");
      return `<div class="msg-burbuja msg-burbuja--${m.direccion}${m.interes ? " msg-burbuja--interes" : ""}">` +
        `<div class="msg-texto">${escaparHtml(texto)}</div>` +
        `<div class="msg-meta">${meta}</div></div>`;
    }).join("");
  }
  const estadoInteres = $("#msgInteresEstado");
  if (estadoInteres) {
    estadoInteres.querySelector("span").textContent =
      pac.interesado ? "Interés: sí (detectado automáticamente)" : "Interés: no";
    estadoInteres.classList.toggle("msg-interes-estado--si", !!pac.interesado);
  }
  actualizarAccionCC(!!pac.interesado);

  $("#modalMensajes").hidden = false;
  // Ya visible: salta al final (mensajes más recientes). Se difiere para que
  // el navegador calcule la altura real del hilo.
  requestAnimationFrame(() => { hilo.scrollTop = hilo.scrollHeight; });
}

// Ajusta el selector + botón "Enviar call center" según el interés del paciente
// y cuántas plantillas de call center hay.
function actualizarAccionCC(interesado) {
  const sel = $("#selCC");
  const btn = $("#btnEnviarCallCenter");
  if (!btn) return;
  if (sel) {
    if (plantillasCC.length > 1) {
      sel.innerHTML = plantillasCC
        .map((p) => `<option value="${p.id}">${escaparHtml(p.nombre)}</option>`)
        .join("");
      sel.hidden = false;
    } else {
      sel.hidden = true;
      sel.innerHTML = "";
    }
  }
  const hayPlantilla = plantillasCC.length > 0;
  btn.disabled = !interesado || !hayPlantilla;
  btn.title = !hayPlantilla
    ? "Primero crea una plantilla de call center (sección al final de la página)"
    : !interesado
      ? "El sistema todavía no detectó interés en los mensajes de este paciente"
      : "";
}

const btnEnviarCC = $("#btnEnviarCallCenter");
if (btnEnviarCC) {
  btnEnviarCC.addEventListener("click", async () => {
    if (!pacienteMsgActual || !pacienteMsgActual.interesado || !plantillasCC.length) return;
    if (!confirm("¿Enviar al paciente el mensaje con el link al chat del call center?")) return;
    const sel = $("#selCC");
    const pid = (sel && !sel.hidden && sel.value) ? sel.value : plantillasCC[0].id;
    btnEnviarCC.disabled = true;
    try {
      const r = await fetch(`api/pacientes/${pacienteMsgActual.id}/call-center?ambiente=${pacienteMsgActual.ambiente}&plantilla_id=${pid}`, {
        method: "POST", headers: authHeaders(),
      });
      const data = await r.json().catch(() => ({}));
      if (r.status === 401) { window.snwSesionExpirada(); return; }
      if (!r.ok) throw new Error(data.detail || "No se pudo enviar");
      toast("Mensaje de call center enviado.", "ok");
      abrirMensajes(pacienteMsgActual.id); // recargar el hilo con el mensaje recién enviado
      cargarLogCC();                        // reflejar el nuevo registro si el panel está visible
    } catch (err) {
      toast(`Error al enviar: ${err.message}`, "error");
      btnEnviarCC.disabled = false;
    }
  });
}

$("#btnCerrarMensajes").addEventListener("click", () => ($("#modalMensajes").hidden = true));
$("#modalMensajes").addEventListener("click", (e) => { if (e.target === $("#modalMensajes")) $("#modalMensajes").hidden = true; });

buscadorEl.addEventListener("input", () => {
  filtro = buscadorEl.value;
  render();
});

$("#btnActualizar").addEventListener("click", cargar);

let toastTimer;
function toast(msg, tipo = "ok") {
  clearTimeout(toastTimer);
  toastEl.textContent = msg;
  toastEl.className = `toast visible toast--${tipo}`;
  toastTimer = setTimeout(() => toastEl.classList.remove("visible"), 3200);
}

/* ---------- Sección "Plantillas de call center" ---------- */

const listaCCEl = $("#listaCC");           // null sin el permiso call_center (data-perm)
const modalCCEl = $("#modalCC");
let ccUrl = "";
let ccNumerosRespaldo = [];
let ccAutoSegundos = 10;

async function cargarCC() {
  if (!listaCCEl) return;
  try {
    const r = await fetch("api/plantillas/call-center", { headers: authHeaders(), cache: "no-store" });
    if (r.status === 401) { window.snwSesionExpirada(); return; }
    if (r.status === 403) { return; }
    if (!r.ok) throw new Error();
    const data = await r.json();
    plantillasCC = data.plantillas || [];
    ccUrl = data.call_center_url || "";
    ccNumerosRespaldo = data.numeros_respaldo || [];
    ccAutoSegundos = data.auto_segundos ?? 10;
    renderCC();
  } catch {
    listaCCEl.innerHTML = `<p class="cc-vacio">No se pudieron cargar las plantillas de call center.</p>`;
  }
}

function ccTieneNumero() {
  return !!ccUrl || ccNumerosRespaldo.length > 0;
}

function renderCC() {
  if (!listaCCEl) return;
  const aviso = $("#ccAviso");
  if (aviso) {
    const cab = ccAutoSegundos > 0
      ? `Envío automático activado: se manda ${ccAutoSegundos} s después de detectar interés.`
      : "Envío automático desactivado (Configuración).";
    if (!ccTieneNumero()) {
      aviso.innerHTML = "⚠ No hay servicio de teléfonos del call center ni números de respaldo: el botón no se añadirá. Configúralo en Configuración.";
      aviso.style.color = "var(--warn-fg)";
    } else {
      const fuente = ccUrl
        ? "El número lo entrega el servicio configurado en Configuración (reparte la carga por su cuenta)."
        : `El botón lleva a uno de los números de respaldo (${ccNumerosRespaldo.map((n) => "+" + n).join(", ")}).`;
      aviso.innerHTML = `${cab} ${fuente}`;
      aviso.style.color = "var(--texto-suave)";
    }
  }
  if (!plantillasCC.length) {
    listaCCEl.innerHTML = `<p class="cc-vacio">Todavía no hay ninguna plantilla de call center.</p>`;
    return;
  }
  listaCCEl.innerHTML = plantillasCC.map((p) => {
    const primera = String(p.texto ?? "").split("\n")[0] || "(sin contenido)";
    const tags =
      (p.es_auto_efectiva ? `<span class="cc-tag cc-tag--auto">automática</span>` : "") +
      (p.cc_boton ? `<span class="cc-tag">con botón</span>` : "");
    return `<div class="cc-item">` +
      `<div class="cc-item__txt">` +
      `<div class="cc-item__cab"><strong>${escaparHtml(p.nombre ?? "(sin nombre)")}</strong>${tags}</div>` +
      `<span>${escaparHtml(primera)}</span>` +
      `</div>` +
      `<div class="cc-item__acc">` +
      `<button type="button" class="btn btn--sm btn--ghost" data-cc-editar="${p.id}">Editar</button>` +
      `<button type="button" class="btn btn--sm btn--danger-ghost" data-cc-borrar="${p.id}">Eliminar</button>` +
      `</div></div>`;
  }).join("");
}

function abrirEditorCC(id) {
  ccEditId = id ?? null;
  const p = id != null ? plantillasCC.find((x) => x.id === id) : null;
  $("#ccTitulo").textContent = p ? `Editar: ${p.nombre}` : "Nueva plantilla de call center";
  $("#ccNombre").value = p ? p.nombre : "";
  $("#ccTexto").value = p ? p.texto : "";
  $("#ccBoton").checked = p ? p.cc_boton !== false : true;
  $("#ccBotonTexto").value = (p && p.cc_boton_texto) || "Ir al call center";
  $("#ccAuto").checked = p ? !!p.cc_auto : (plantillasCC.length === 0);
  sincronizarBotonCC();
  actualizarContadorCC();
  modalCCEl.hidden = false;
  $("#ccNombre").focus();
}

function sincronizarBotonCC() {
  const on = $("#ccBoton").checked;
  $("#ccBotonTextoWrap").hidden = !on;
  const hint = $("#ccBotonHint");
  if (!hint) return;
  if (on && !ccTieneNumero()) {
    hint.textContent = "Falta configurar el servicio de teléfonos del call center en Configuración; hasta entonces el botón no se enviará.";
    hint.style.color = "var(--warn-fg)";
  } else {
    hint.textContent = "El botón lleva al número que entrega el servicio del call center (Configuración). Máx. 20 caracteres.";
    hint.style.color = "var(--texto-suave)";
  }
}

function actualizarContadorCC() {
  const n = $("#ccTexto").value.length;
  const el = $("#ccContador");
  el.textContent = `${n} / ${MAX_CC}`;
  el.classList.toggle("char-count--limite", n > MAX_CC);
}

if (listaCCEl) {
  listaCCEl.addEventListener("click", (e) => {
    const ed = e.target.closest("[data-cc-editar]");
    const bo = e.target.closest("[data-cc-borrar]");
    if (ed) abrirEditorCC(Number(ed.dataset.ccEditar));
    if (bo) borrarCC(Number(bo.dataset.ccBorrar));
  });
  $("#btnNuevaCC").addEventListener("click", () => abrirEditorCC(null));
  $("#ccTexto").addEventListener("input", actualizarContadorCC);
  $("#ccBoton").addEventListener("change", sincronizarBotonCC);
  $("#btnCancelarCC").addEventListener("click", () => (modalCCEl.hidden = true));
  modalCCEl.addEventListener("click", (e) => { if (e.target === modalCCEl) modalCCEl.hidden = true; });

  $("#formCC").addEventListener("submit", async (e) => {
    e.preventDefault();
    const nombre = $("#ccNombre").value.trim();
    const texto = $("#ccTexto").value;
    if (!nombre || !texto.trim()) { toast("Nombre y mensaje son obligatorios.", "error"); return; }
    if (texto.length > MAX_CC) { toast(`El mensaje supera los ${MAX_CC} caracteres.`, "error"); return; }
    const esNueva = ccEditId == null;
    const cuerpo = {
      nombre, texto,
      boton: $("#ccBoton").checked,
      boton_texto: $("#ccBotonTexto").value.trim() || "Ir al call center",
      auto: $("#ccAuto").checked,
    };
    try {
      const r = await fetch(
        esNueva ? "api/plantillas/call-center" : `api/plantillas/call-center/${ccEditId}`,
        {
          method: esNueva ? "POST" : "PUT",
          headers: authHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify(cuerpo),
        },
      );
      const data = await r.json().catch(() => ({}));
      if (r.status === 401) { window.snwSesionExpirada(); return; }
      if (!r.ok) throw new Error(data.detail || `Error ${r.status}`);
      modalCCEl.hidden = true;
      toast(esNueva ? "Plantilla creada." : "Plantilla actualizada.", "ok");
      await cargarCC();
    } catch (err) {
      toast(`No se pudo guardar: ${err.message}`, "error");
    }
  });
}

async function borrarCC(id) {
  const p = plantillasCC.find((x) => x.id === id);
  if (!confirm(`¿Eliminar la plantilla de call center «${p ? p.nombre : id}»?`)) return;
  try {
    const r = await fetch(`api/plantillas/call-center/${id}`, { method: "DELETE", headers: authHeaders() });
    if (r.status === 401) { window.snwSesionExpirada(); return; }
    if (!r.ok) { const d = await r.json().catch(() => ({})); throw new Error(d.detail || `Error ${r.status}`); }
    toast("Plantilla eliminada.", "ok");
    await cargarCC();
  } catch (err) {
    toast(`No se pudo eliminar: ${err.message}`, "error");
  }
}

/* ---------- Panel "Registro de respuestas de call center" ---------- */
/* null si no se tiene el permiso `call_center_registro` (data-perm en el HTML) */
const panelCCLogEl = $("#panelCCRegistro");

async function cargarLogCC() {
  if (!panelCCLogEl) return;
  const body = $("#ccLogBody");
  const cont = $("#ccLogContadores");
  body.innerHTML = `<tr><td colspan="5" style="text-align:center;color:var(--texto-suave);">Cargando…</td></tr>`;
  try {
    const r = await fetch("api/call-center/log", { headers: authHeaders(), cache: "no-store" });
    if (r.status === 401) { window.snwSesionExpirada(); return; }
    if (!r.ok) throw new Error();
    const data = await r.json();
    const c = data.contadores || {};
    cont.innerHTML = Object.keys(c).length
      ? "Usos por número: " + Object.entries(c).map(([n, u]) => `<strong>+${escaparHtml(n)}</strong> ${u}`).join(" · ")
      : "Todavía no se ha usado ningún número.";
    const filas = data.entradas || [];
    body.innerHTML = filas.length
      ? filas.map((f) => {
          const est = f.estado === "error"
            ? `<span class="respuesta-badge respuesta-baja">error</span>`
            : `<span class="respuesta-badge respuesta-respondio">enviado</span>`;
          return `<tr>` +
            `<td class="campo-fecha">${escaparHtml(f.fecha)}</td>` +
            `<td class="campo-nombre">${escaparHtml(f.nombre_paciente ?? "—")}<br><span class="campo-tel">${escaparHtml(f.numero_paciente ?? "")}</span></td>` +
            `<td class="campo-tel">+${escaparHtml(f.numero_call_center)}</td>` +
            `<td>${f.automatico ? "automático" : "manual"}</td>` +
            `<td>${est}${f.descripcion_error ? `<div class="hist-msg">${escaparHtml(f.descripcion_error)}</div>` : ""}</td>` +
            `</tr>`;
        }).join("")
      : `<tr><td colspan="5" style="text-align:center;color:var(--texto-suave);">Todavía no se ha enviado ninguna respuesta de call center.</td></tr>`;
  } catch {
    body.innerHTML = `<tr><td colspan="5" style="text-align:center;color:var(--danger-fg);">No se pudo cargar el registro.</td></tr>`;
  }
}

if (panelCCLogEl) {
  $("#btnActualizarCCLog").addEventListener("click", cargarLogCC);
}

cargar();
cargarCC();
cargarLogCC();
