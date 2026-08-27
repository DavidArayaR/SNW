const API_PACIENTES = "api/pacientes";
const API_PLANTILLAS = "api/plantillas";
const API_ENVIAR = "api/notificaciones/enviar";

function authHeaders(extra = {}) {
  return { Authorization: "Bearer " + (localStorage.getItem("snw_token") || ""), ...extra };
}

if (!localStorage.getItem("snw_token")) location.replace("login.html");
else if (localStorage.getItem("snw_rol") !== "administrador") location.replace("mensajeria.html");

let ambienteAdmin = localStorage.getItem("snw_ambiente_admin");
let _ambienteInicializado = false;

let pacientes = [];
let plantillas = [];
let config = null;
let filtro = "";
let filtroEstado = "todos";
let seleccionados = new Set();
let plantillaId = null;
let timerPolling = null;
let envioEnCurso = false;

function setBloqueoEnvio(bloquear) {
  envioEnCurso = bloquear;
  document.querySelectorAll("button, input, select, textarea").forEach((el) => {
    // No bloquear el toast ni la barra de progreso (no son inputs)
    el.disabled = bloquear;
  });
  // Si se desbloquea, restaurar estados correctos vía render
  if (!bloquear) render();
}

const $ = (sel) => document.querySelector(sel);

const tbodyEl = $("#tablaPacientes tbody");
const vacioEl = $("#tablaVacia");
const buscadorEl = $("#buscador");
const statsEl = $("#stats");
const chkTodos = $("#chkTodos");
const contadorSel = $("#contadorSel");
const btnIniciar = $("#btnIniciar");
const modalEl = $("#modalEnvio");
const toastEl = $("#toast");
const selAmbiente = $("#selAmbiente");

if (ambienteAdmin) selAmbiente.value = ambienteAdmin;
selAmbiente.addEventListener("change", async () => {
  ambienteAdmin = selAmbiente.value;
  localStorage.setItem("snw_ambiente_admin", ambienteAdmin);
  // Sincroniza también el .env global (solo admin)
  try {
    await fetch("api/configuracion", {
      method: "PUT",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ entorno: ambienteAdmin }),
    });
  } catch {}
  cargar();
});

function ambienteActual() {
  return selAmbiente.value;
}

function escaparHtml(texto) {
  const div = document.createElement("div");
  div.textContent = texto ?? "";
  return div.innerHTML;
}

async function cargar() {
  try {
    // Si no hay preferencia guardada, usar el entorno global del .env como default
    if (!_ambienteInicializado && !localStorage.getItem("snw_ambiente_admin")) {
      try {
        const r0 = await fetch("api/configuracion", { headers: authHeaders(), cache: "no-store" });
        if (r0.ok) {
          const c0 = await r0.json();
          ambienteAdmin = c0.entorno;
          selAmbiente.value = ambienteAdmin;
          localStorage.setItem("snw_ambiente_admin", ambienteAdmin);
        }
      } catch {}
      _ambienteInicializado = true;
    }
    const amb = ambienteActual() || ambienteAdmin || "desarrollo";
    const [rp, rt, rc] = await Promise.all([
      fetch(`${API_PACIENTES}?ambiente=${amb}`, { headers: authHeaders(), cache: "no-store" }),
      fetch(API_PLANTILLAS, { headers: authHeaders(), cache: "no-store" }),
      fetch(`api/configuracion?ambiente=${amb}`, { headers: authHeaders(), cache: "no-store" }),
    ]);
    if (rp.status === 401 || rt.status === 401 || rc.status === 401) { window.snwSalir(); return; }
    if (rp.status === 403) { location.href = "mensajeria.html"; return; }

    pacientes = (await rp.json()).map((p) => ({
      ...p,
      estado: p.estado || "pendiente",
      info_extra: p.info_extra || "",
    }));
    plantillas = await rt.json();
    config = await rc.json();

    $("#nombreBd").textContent = config.base_datos ?? "";

    render();
  } catch {
    toast("Error al conectar con el servidor.", "error");
  }
}

function render() {
  const q = filtro.trim().toLowerCase();
  const visibles = pacientes.filter(
    (p) =>
      (filtroEstado === "todos" || p.estado === filtroEstado) &&
      (!q ||
        [p.nombre, p.apellido, p.telefono, p.info_extra]
          .filter(Boolean)
          .some((v) => v.toLowerCase().includes(q)))
  );

  tbodyEl.innerHTML = "";

  for (const p of visibles) {
    const nombreCompleto = [p.nombre, p.apellido].filter(Boolean).join(" ");
    const tr = document.createElement("tr");
    const respuesta = p.respuesta || "pendiente";
    const respuestaLabels = { pendiente: "Sin respuesta", click: "Hizo click", respondio: "Respondió", baja: "Se dio de baja" };
    tr.innerHTML =
      `<td class="col-check"><input type="checkbox" data-id="${p.id}" ${seleccionados.has(p.id) ? "checked" : ""}></td>` +
      `<td class="campo-id">${p.id}</td>` +
      `<td class="campo-nombre">${escaparHtml(nombreCompleto)}</td>` +
      `<td class="campo-tel">${escaparHtml(p.telefono)}</td>` +
      `<td class="campo-estado">` +
        `<span class="estado-badge estado-${escaparHtml(p.estado)}" data-editable data-id="${p.id}" title="Click para cambiar estado">${escaparHtml(p.estado)}</span>` +
        `<select class="estado-select" data-id="${p.id}" hidden>` +
          `<option value="pendiente"${p.estado === "pendiente" ? " selected" : ""}>pendiente</option>` +
          `<option value="enviado"${p.estado === "enviado" ? " selected" : ""}>enviado</option>` +
        `</select>` +
      `</td>` +
      `<td class="campo-respuesta">` +
        `<span class="respuesta-badge respuesta-${escaparHtml(respuesta)}" title="${escaparHtml(respuestaLabels[respuesta] ?? respuesta)}">${escaparHtml(respuestaLabels[respuesta] ?? respuesta)}</span>` +
      `</td>` +
      `<td class="campo-info">${escaparHtml(p.info_extra ?? "—")}</td>` +
      `<td class="campo-fecha">${escaparHtml(p.actualizado)}</td>`;
    tr.querySelector('input[type="checkbox"]').addEventListener("change", (e) => {
      e.target.checked ? seleccionados.add(p.id) : seleccionados.delete(p.id);
      refrescarSeleccion();
    });
    tbodyEl.appendChild(tr);
  }

  vacioEl.hidden = visibles.length > 0;

  const conteo = { total: pacientes.length, pendiente: 0, enviado: 0, error: 0 };
  for (const p of pacientes) {
    if (conteo[p.estado] !== undefined) conteo[p.estado]++;
  }

  const activo = (estado) => (filtroEstado === estado ? " activo" : "");
  statsEl.innerHTML =
    `<button type="button" class="stat stat--total${activo("todos")}" data-estado="todos">Total <strong>${conteo.total}</strong></button>` +
    `<button type="button" class="stat stat--pendiente${activo("pendiente")}" data-estado="pendiente">Pendientes <strong>${conteo.pendiente}</strong></button>` +
    `<button type="button" class="stat stat--enviado${activo("enviado")}" data-estado="enviado">Enviados <strong>${conteo.enviado}</strong></button>` +
    `<button type="button" class="stat stat--error${activo("error")}" data-estado="error">Errores <strong>${conteo.error}</strong></button>`;

  document.querySelectorAll(".filtro-btn").forEach((b) => {
    b.classList.toggle("activo", b.dataset.estado === filtroEstado);
  });

  refrescarSeleccion(visibles);
}

function refrescarSeleccion(visibles = null) {
  visibles = visibles ?? pacientes.filter(
    (p) => filtroEstado === "todos" || p.estado === filtroEstado
  );

  contadorSel.textContent =
    seleccionados.size > 0 ? `${seleccionados.size} seleccionado${seleccionados.size === 1 ? "" : "s"}` : "";

  btnIniciar.disabled = seleccionados.size === 0;
  btnIniciar.textContent =
    seleccionados.size > 0 ? `Iniciar envío (${seleccionados.size})` : "Iniciar envío";

  const idsVisibles = visibles.map((p) => p.id);
  chkTodos.checked =
    idsVisibles.length > 0 && idsVisibles.every((id) => seleccionados.has(id));
}

chkTodos.addEventListener("change", () => {
  const q = filtro.trim().toLowerCase();
  const visibles = pacientes.filter(
    (p) =>
      (filtroEstado === "todos" || p.estado === filtroEstado) &&
      (!q ||
        [p.nombre, p.apellido, p.telefono, p.info_extra]
          .filter(Boolean)
          .some((v) => v.toLowerCase().includes(q)))
  );
  for (const p of visibles) {
    chkTodos.checked ? seleccionados.add(p.id) : seleccionados.delete(p.id);
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
  refrescarSeleccion();
});

// Edición inline del estado
tbodyEl.addEventListener("click", (e) => {
  const badge = e.target.closest(".estado-badge[data-editable]");
  if (!badge) return;
  e.stopPropagation();
  const id = badge.dataset.id;
  const sel = tbodyEl.querySelector(`.estado-select[data-id="${id}"]`);
  if (!sel) return;
  badge.hidden = true;
  sel.hidden = false;
  sel.focus();
});

tbodyEl.addEventListener("change", async (e) => {
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
  try {
    const amb = ambienteActual();
    const res = await fetch(`api/pacientes/${id}?ambiente=${encodeURIComponent(amb)}`, {
      method: "PUT",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ estado: nuevoEstado }),
    });
    if (res.status === 401) { window.snwSalir(); return; }
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
    toast(err.message || "No se pudo actualizar el estado", "error");
    if (paciente) sel.value = estadoAnterior || "pendiente";
    const badge = tbodyEl.querySelector(`.estado-badge[data-id="${id}"]`);
    if (badge) badge.hidden = false;
    sel.hidden = true;
    sel.disabled = false;
  }
});

tbodyEl.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && e.target.classList.contains("estado-select")) {
    const sel = e.target;
    const badge = tbodyEl.querySelector(`.estado-badge[data-id="${sel.dataset.id}"]`);
    if (badge) badge.hidden = false;
    sel.hidden = true;
    sel.blur();
  }
});

document.addEventListener("click", (e) => {
  if (e.target.closest(".estado-badge, .estado-select")) return;
  tbodyEl.querySelectorAll(".estado-select:not([hidden])").forEach((sel) => {
    const badge = tbodyEl.querySelector(`.estado-badge[data-id="${sel.dataset.id}"]`);
    if (badge) badge.hidden = false;
    sel.hidden = true;
  });
});

buscadorEl.addEventListener("input", () => {
  filtro = buscadorEl.value;
  render();
});

document.querySelectorAll(".filtro-btn").forEach((btn) => {
  btn.addEventListener("click", () => setFiltro(btn.dataset.estado));
});

statsEl.addEventListener("click", (e) => {
  const btn = e.target.closest(".stat");
  if (!btn) return;
  setFiltro(btn.dataset.estado);
});

$("#btnActualizar").addEventListener("click", cargar);

function setFiltro(estado) {
  filtroEstado = estado;
  render();
}

btnIniciar.addEventListener("click", () => {
  if (!seleccionados.size) return;
  plantillaId = null;
  abrirModal();
});

function abrirModal() {
  renderPlantillasModal();

  $("#resumenLinea").textContent =
    `Se enviará un mensaje a ${seleccionados.size} ${seleccionados.size === 1 ? "persona" : "personas"}`;

  const esDev = config?.entorno === "desarrollo";
  if (!esDev) {
    avisoDevEl.hidden = true;
  } else {
    avisoDevEl.hidden = false;
    const nums = (config?.numeros_autorizados ?? []).join(", ") || "ninguno";
    avisoDevEl.textContent = `Base de datos desarrollo: solo se enviará a los números autorizados (${nums}). El resto será descartado.`;
  }

  $("#faseConfig").hidden = false;
  $("#faseProgreso").hidden = true;
  $("#btnLanzarEnvio").hidden = true;
  $("#btnCerrarModal").hidden = true;
  $("#listaRechazados").innerHTML = "";
  $("#listaRechazados").hidden = true;
  $("#barraFill").style.width = "0%";

  modalEl.hidden = false;
}

function renderPlantillasModal() {
  const cont = $("#listaPlantillasModal");
  cont.innerHTML = "";

  for (const t of plantillas) {
    const label = document.createElement("label");
    label.className = "tpl-card" + (t.id === plantillaId ? " seleccionada" : "");
    label.innerHTML =
      `<input type="radio" name="plantillaModal" value="${t.id}" ${t.id === plantillaId ? "checked" : ""}>` +
      `<span class="tpl-card__nombre">${escaparHtml(t.nombre)}</span>` +
      `<span class="tpl-card__texto">${escaparHtml(t.texto.split("\n")[0])}</span>`;
    label.querySelector("input").addEventListener("change", () => {
      plantillaId = t.id;
      cont.querySelectorAll(".tpl-card").forEach((c) => c.classList.remove("seleccionada"));
      label.classList.add("seleccionada");
      $("#btnLanzarEnvio").hidden = false;
    });
    cont.appendChild(label);
  }
}

const avisoDevEl = $("#avisoDev");

$("#btnCancelarEnvio").addEventListener("click", () => {
  if (envioEnCurso) return;
  clearInterval(timerPolling);
  modalEl.hidden = true;
});
modalEl.addEventListener("click", (e) => {
  if (envioEnCurso) return;
  if (e.target === modalEl) {
    clearInterval(timerPolling);
    modalEl.hidden = true;
  }
});
$("#btnCerrarModal").addEventListener("click", () => {
  if (envioEnCurso) return;
  clearInterval(timerPolling);
  seleccionados.clear();
  plantillaId = null;
  modalEl.hidden = true;
  render();
});

$("#btnLanzarEnvio").addEventListener("click", async () => {
  if (envioEnCurso) return;

  $("#btnLanzarEnvio").hidden = true;
  setBloqueoEnvio(true);

  try {
    const res = await fetch(API_ENVIAR, {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ pacientes: [...seleccionados], plantilla_id: plantillaId, ambiente: ambienteActual() }),
    });
    const data = await res.json();
    if (res.status === 401) { window.snwSalir(); setBloqueoEnvio(false); return; }
    if (!res.ok) throw new Error(data.detail ?? `Error ${res.status}`);

    pintarRechazados(data.rechazados ?? []);

    if (data.requiere_confirmacion) {
      modalEl.hidden = true;
      const espera = document.getElementById("modalEspera");
      espera.hidden = false;
      const linkEl = document.getElementById("linkConfirmacionEspera");
      if (linkEl && data.confirm_url && localStorage.getItem("snw_rol") === "administrador") {
        linkEl.innerHTML = `Para pruebas sin correo: <a href="${data.confirm_url}" target="_blank">Confirmar manualmente</a> · <a href="${data.confirm_url.replace('confirmar', 'rechazar')}" target="_blank" style="color:#b23b37;">Rechazar</a>`;
      }
      const beforeUnload = (e) => { e.preventDefault(); e.returnValue = ""; return ""; };
      window.addEventListener("beforeunload", beforeUnload);
      const poll = setInterval(async () => {
        try {
          const r2 = await fetch(`api/notificaciones/solicitud/${data.solicitud_id}`, { headers: authHeaders(), cache: "no-store" });
          if (!r2.ok) return;
          const s = await r2.json();
          if (s.estado === "confirmado" && s.job_id) {
            clearInterval(poll);
            window.removeEventListener("beforeunload", beforeUnload);
            espera.hidden = true;
            alert("Envío confirmado por supervisor");
            await new Promise((res) => setTimeout(res, 3000));
            modalEl.hidden = false;
            $("#faseConfig").hidden = true;
            $("#faseProgreso").hidden = false;
            seguirProgreso(s.job_id, s.total);
          } else if (s.estado === "rechazado") {
            clearInterval(poll);
            window.removeEventListener("beforeunload", beforeUnload);
            espera.hidden = true;
            toast("Envío rechazado por supervisor.", "error");
            setBloqueoEnvio(false);
            $("#btnLanzarEnvio").hidden = false;
            render();
          }
        } catch {}
      }, 2000);
      window._pollEspera = poll;
      window._beforeUnloadEspera = beforeUnload;
      return;
    }

    if (!data.iniciado) {
      toast("Ningún destinatario válido. Envío no iniciado.", "error");
      setBloqueoEnvio(false);
      $("#btnLanzarEnvio").hidden = false;
      return;
    }

    $("#faseConfig").hidden = true;
    $("#faseProgreso").hidden = false;
    seguirProgreso(data.job_id, data.total);
  } catch (err) {
    toast(`Error al iniciar el envío: ${err.message}`, "error");
    setBloqueoEnvio(false);
    $("#btnLanzarEnvio").hidden = false;
  }
});

function normalizarTelefonoJs(crudo) {
  const limpio = String(crudo ?? "").trim().replace(/[^\d+]/g, "");
  const d = limpio.replace(/\D/g, "");
  if (limpio.startsWith("+")) {
    if (d.length === 11 && d.startsWith("569")) return "+" + d;
    if (d.length === 10 && d.startsWith("56")) return "+569" + d.slice(2);
    if (d.length === 9 && d.startsWith("9")) return "+56" + d;
    return null;
  }
  if (d.length === 11 && d.startsWith("569")) return "+" + d;
  if (d.length === 10 && d.startsWith("56")) return "+569" + d.slice(2);
  if (d.length === 9 && d.startsWith("9")) return "+56" + d;
  if (d.length === 8 && d.startsWith("9")) return "+569" + d;
  return null;
}

function pintarRechazados(rechazados) {
  const ul = $("#listaRechazados");
  ul.innerHTML = "";
  const esDev = (config?.entorno === "desarrollo") || ambienteActual() === "desarrollo";
  if (esDev) {
    ul.hidden = true;
    return;
  }
  for (const r of rechazados) {
    const li = document.createElement("li");
    li.textContent = `${r.nombre} (${r.telefono || "sin teléfono"}): ${r.motivo}`;
    ul.appendChild(li);
  }
  ul.hidden = rechazados.length === 0;
}

function seguirProgreso(jobId, total) {
  clearInterval(timerPolling);
  timerPolling = setInterval(async () => {
    try {
      const res = await fetch(`api/notificaciones/jobs/${jobId}`, {
        headers: authHeaders(),
        cache: "no-store",
      });
      if (res.status === 401) { window.snwSalir(); return; }
      if (!res.ok) throw new Error();
      const job = await res.json();

      const hechos = job.enviados + job.fallidos;
      const porcentaje = total ? Math.round((hechos / total) * 100) : 100;
      $("#barraFill").style.width = `${porcentaje}%`;
      $("#progresoNumeros").textContent = `${hechos} / ${total}`;
      $("#progresoTexto").textContent =
        job.estado === "completado"
          ? "Envío finalizado."
          : `Enviando mensajes... (${hechos}/${total})`;

      if (job.estado === "completado" || job.estado === "error") {
        clearInterval(timerPolling);
        const msg = job.estado === "error"
          ? `Error del canal: ${job.detalle}`
          : `Envío completado: ${job.enviados} enviado(s), ${job.fallidos} fallido(s).`;
        finalizar(msg, job.estado === "error");
        setBloqueoEnvio(false);
        cargar();
      }
    } catch {
      clearInterval(timerPolling);
      finalizar("Se perdió la conexión con el servidor.", true);
      setBloqueoEnvio(false);
    }
  }, 700);
}

function finalizar(mensaje, esError) {
  $("#progresoTexto").textContent = mensaje;
  toast(mensaje, esError ? "error" : "ok");
  // Habilitar solo el botón Cerrar para poder salir del modal
  const btnCerrar = document.getElementById("btnCerrarModal");
  if (btnCerrar) btnCerrar.disabled = false;
}

let toastTimer;
function toast(msg, tipo = "ok") {
  clearTimeout(toastTimer);
  toastEl.textContent = msg;
  toastEl.className = `toast visible toast--${tipo}`;
  toastTimer = setTimeout(() => toastEl.classList.remove("visible"), 3200);
}

cargar();
