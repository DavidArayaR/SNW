/* Gestión de áreas y sus roles (administrador / desarrollador).
   Página «Areas» (areas.html) */
(function () {
const $ = (s) => document.querySelector(s);

function authHeaders(extra = {}) {
  return { Authorization: "Bearer " + (localStorage.getItem("snw_token") || ""), ...extra };
}

if (!localStorage.getItem("snw_token")) location.replace("login.html");

const selEl = $("#selArea");
const detalleEl = $("#detalleArea");
const vacioEl = $("#espVacio");
const msgEl = $("#espMsg");
const toastEl = $("#toast");

let estado = { lista: [], usuarios: [] };
let seleccion = null;   // id del área mostrada
let yaCargada = false;
let asignacionEnCurso = false;
let tipoAsignando = null;
let asignarBloqueadoHasta = 0;
let temporizadorAsignar = null;

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

function esc(t) {
  const d = document.createElement("div");
  d.textContent = t ?? "";
  return d.innerHTML;
}

function msg(texto, tipo = "") {
  msgEl.textContent = texto;
  msgEl.style.color = tipo === "error" ? "var(--danger-fg)" : "";
}

async function cargar(mostrarFeedback = false) {
  yaCargada = true;
  try {
    const [re, ru] = await Promise.all([
      fetch("api/areas", { headers: authHeaders(), cache: "no-store" }),
      fetch("api/usuarios", { headers: authHeaders(), cache: "no-store" }),
    ]);
    if (re.status === 401 || ru.status === 401) { window.snwSesionExpirada(); return; }
    if (!re.ok) throw new Error();
    estado.lista = await re.json();
    const du = ru.ok ? await ru.json() : {};
    estado.usuarios = du.usuarios || [];
    msg("");
    render();
    if (mostrarFeedback) toast("Áreas actualizadas correctamente.");
  } catch (e) {
    console.error("[areas.js cargar()]", e);
    toast("No se pudieron cargar las áreas.", "error");
  }
}

window.snwCargarAreas = function () {
  if (yaCargada) return;
  cargar();
};

const btnActualizar = $("#btnActualizarAreas");
  if (btnActualizar) window.snwConCooldown(btnActualizar, () => cargar(true));

function fmtFecha(f) {
  if (!f) return "—";
  const d = new Date(String(f).replace(" ", "T"));
  return isNaN(d) ? String(f) : d.toLocaleString("es-CL", { dateStyle: "short", timeStyle: "short" });
}

function render() {
  const lista = estado.lista || [];
  vacioEl.hidden = lista.length > 0;
  selEl.parentElement.hidden = lista.length === 0;

  if (!lista.some((e) => e.id === seleccion)) {
    seleccion = (lista[0] || {}).id ?? null;
  }
  selEl.innerHTML = lista.map((e) =>
    `<option value="${e.id}"${e.id === seleccion ? " selected" : ""}>${esc(e.nombre_visible)}</option>`
  ).join("");
  if (seleccion != null) selEl.value = String(seleccion);

  renderDetalle();
}

if (selEl) selEl.addEventListener("change", () => {
  seleccion = Number(selEl.value);
  renderDetalle();
});

function usuariosConRol(espId) {
  return (estado.usuarios || []).filter((u) =>
    (u.areas || []).some((e) => e.id === espId));
}

function usuariosAgregables(espId) {
  const conRol = new Set(usuariosConRol(espId).map((u) => u.usuario));
  return (estado.usuarios || []).filter((u) => u.editable && !conRol.has(u.usuario));
}

function actualizarControlesAsignar() {
  const card = detalleEl.querySelector(".usr-card");
  if (!card) return;
  const segundos = Math.max(0, Math.ceil((asignarBloqueadoHasta - Date.now()) / 1000));
  for (const tipo of ["nuevo-usuario", "nuevo-supervisor"]) {
    const sel = card.querySelector(`[data-${tipo}]`);
    const boton = card.querySelector(`[data-asignar-${tipo}]`);
    if (!sel || !boton) continue;
    sel.disabled = asignacionEnCurso || segundos > 0 || !sel.options.length;
    boton.disabled = sel.disabled;
    boton.textContent = asignacionEnCurso && tipoAsignando === tipo ? "Asignando…"
      : segundos > 0 ? `Espera ${segundos} s` : "Asignar";
  }
}

function iniciarPausaAsignar() {
  asignarBloqueadoHasta = Date.now() + 5000;
  clearInterval(temporizadorAsignar);
  temporizadorAsignar = setInterval(() => {
    actualizarControlesAsignar();
    if (Date.now() >= asignarBloqueadoHasta) {
      clearInterval(temporizadorAsignar);
      temporizadorAsignar = null;
    }
  }, 1000);
}

function renderDetalle() {
  const esp = (estado.lista || []).find((x) => x.id === seleccion);
  if (!esp) { detalleEl.innerHTML = ""; return; }
  const asignados = usuariosConRol(esp.id);
  const agregables = usuariosAgregables(esp.id);
  const tablaUsuarios = asignados.filter((u) => u.rol === "usuario");
  const tablaSupervisores = asignados.filter((u) => u.rol === "supervisor");
  const tablaOtros = asignados.filter((u) => u.rol !== "usuario" && u.rol !== "supervisor");
  const filasAcceso = (lista) => lista.map((u) =>
    `<tr>` +
      `<td>${esc(u.nombre || u.usuario)}</td>` +
      `<td>${esc(u.usuario)}</td>` +
      `<td style="text-align:right;">` +
        (u.editable
          ? `<button type="button" class="btn btn--danger-ghost" data-quitar="${esc(u.usuario)}">Quitar</button>`
          : "") +
      `</td>` +
    `</tr>`).join("");
  const tablaAcceso = (titulo, lista, vacioTxt) =>
    `<h4 class="usr-subtitulo">${titulo} (${lista.length})</h4>` +
    (lista.length
      ? `<div class="usr-tabla-scroll"><table class="usr-envios__tabla usr-envios__tabla--compacta">` +
        `<thead><tr><th>Nombre</th><th>Correo</th><th></th></tr></thead>` +
        `<tbody>${filasAcceso(lista)}</tbody></table></div>`
      : `<p class="usr-envios__vacio">${vacioTxt}</p>`);
  const soloUsuarios = agregables.filter((u) => u.rol === "usuario");
  const soloSupervisores = agregables.filter((u) => u.rol === "supervisor");
  const opcion = (u) => `<option value="${esc(u.usuario)}">${esc(u.nombre || u.usuario)} (${esc(u.usuario)})</option>`;
  const filaAsignar = (titulo, attrSel, lista) =>
    `<label>${titulo}</label>` +
    `<div class="usr-reset__correo">` +
      `<select data-${attrSel}>${lista.map(opcion).join("")}</select>` +
      `<button type="button" class="btn btn--primary" data-asignar-${attrSel}${lista.length ? "" : " disabled"}>Asignar</button>` +
    `</div>`;

  detalleEl.innerHTML =
    `<div class="usr-card" data-id="${esp.id}">` +
      `<div class="usr-card__cab">` +
        `<span class="usr-card__correo">${esc(esp.nombre_visible)}</span>` +
        `<span class="usr-tag">${esc(esp.nombre_tabla_base)}</span>` +
      `</div>` +
      `<div class="usr-grid">` +
        `<label>Nombre visible</label>` +
        `<div class="usr-reset__correo">` +
          `<input type="text" data-nombre value="${esc(esp.nombre_visible)}" maxlength="150">` +
          `<button type="button" class="btn btn--ghost" data-renombrar>Renombrar</button>` +
        `</div>` +
        `<label>Tabla</label><div><code>${esc(esp.nombre_tabla_base)}</code> <span class="field__hint">No cambia al renombrar.</span></div>` +
        `<label>Rol</label><div><span class="usr-tag">${esc(esp.rol_nombre || esp.nombre_visible)}</span></div>` +
        `<label>Creada</label><div>${esc(fmtFecha(esp.fecha_creacion))}</div>` +
        `<label>Cuentas con acceso</label>` +
        `<div>` +
          tablaAcceso("Usuarios", tablaUsuarios, "Ninguno todavía.") +
          tablaAcceso("Supervisores", tablaSupervisores, "Ninguno todavía.") +
          (tablaOtros.length ? tablaAcceso("Otras cuentas", tablaOtros, "Ninguna.") : "") +
        `</div>` +
        filaAsignar("Asignar usuario", "nuevo-usuario", soloUsuarios) +
        filaAsignar("Asignar supervisor", "nuevo-supervisor", soloSupervisores) +
        `<label>Zona de peligro</label>` +
        `<div><button type="button" class="btn btn--danger" data-eliminar-tabla>Eliminar tabla completa</button>` +
        `<p class="field__hint" style="margin:6px 0 0;">Borra la tabla <code>${esc(esp.nombre_tabla_base)}</code> ` +
        `(${(esp.total_pacientes ?? 0)} pacientes), su rol y sus asignaciones. El historial de envíos se conserva. No se puede deshacer.</p></div>` +
      `</div>` +
    `</div>`;

  const card = detalleEl.querySelector(".usr-card");
  card.querySelector("[data-renombrar]").addEventListener("click", () => renombrar(card));
  card.querySelector("[data-asignar-nuevo-usuario]").addEventListener("click", () => asignar(card, "nuevo-usuario"));
  card.querySelector("[data-asignar-nuevo-supervisor]").addEventListener("click", () => asignar(card, "nuevo-supervisor"));
  card.querySelector("[data-eliminar-tabla]").addEventListener("click", () => eliminarTabla(card));
  card.querySelectorAll("[data-quitar]").forEach((b) =>
    b.addEventListener("click", () => retirar(card, b.dataset.quitar)));
  actualizarControlesAsignar();
}

async function renombrar(card) {
  const id = Number(card.dataset.id);
  const nombre = card.querySelector("[data-nombre]").value.trim();
  if (!nombre) return toast("Escribe el nombre visible.", "error");
  try {
    const r = await fetch("api/areas/" + id, {
      method: "PUT",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ nombre_visible: nombre }),
    });
    const d = await r.json().catch(() => ({}));
    if (r.status === 401) { window.snwSesionExpirada(); return; }
    if (!r.ok) throw new Error(typeof d.detail === "string" ? d.detail : `Error ${r.status}`);
    toast("Área renombrada.");
    yaCargada = false;
    await cargar();
  } catch (e) {
    console.error("[areas.js renombrar()]", e);
    toast(e.message || "No se pudo renombrar.", "error");
  }
}

async function asignar(card, attrSel) {
  if (asignacionEnCurso || Date.now() < asignarBloqueadoHasta) return;
  const id = Number(card.dataset.id);
  const sel = card.querySelector(`[data-${attrSel}]`);
  const boton = card.querySelector(`[data-asignar-${attrSel}]`);
  if (!sel || !sel.value || !boton || boton.disabled) return;
  const correo = sel.value;
  asignacionEnCurso = true;
  tipoAsignando = attrSel;
  actualizarControlesAsignar();
  try {
    const r = await fetch(`api/areas/${id}/roles`, {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ usuario: correo }),
    });
    const d = await r.json().catch(() => ({}));
    if (r.status === 401) { window.snwSesionExpirada(); return; }
    if (!r.ok) throw new Error(typeof d.detail === "string" ? d.detail : `Error ${r.status}`);
    iniciarPausaAsignar();
    toast(d.asignado === false ? `${correo} ya tenía acceso a esta área.` : `Rol asignado a ${correo}.`);
    yaCargada = false;
    await cargar();
  } catch (e) {
    console.error("[areas.js asignar()]", e);
    toast(e.message || "No se pudo asignar el rol.", "error");
  } finally {
    asignacionEnCurso = false;
    tipoAsignando = null;
    actualizarControlesAsignar();
  }
}

async function eliminarTabla(card) {
  const id = Number(card.dataset.id);
  const esp = (estado.lista || []).find((x) => x.id === id);
  if (!esp) return;
  const total = esp.total_pacientes ?? "?";
  if (!confirm(
    `¿Eliminar DEFINITIVAMENTE el área «${esp.nombre_visible}»?\n\n` +
    `Se borra la tabla ${esp.nombre_tabla_base} (${total} pacientes), su rol y sus ` +
    `asignaciones. El historial de envíos se conserva.\n\nEsta acción no se puede deshacer.`
  )) return;
  try {
    const r = await fetch(`api/areas/${id}/tabla`, {
      method: "DELETE", headers: authHeaders(),
    });
    if (r.status === 401) { window.snwSesionExpirada(); return; }
    if (!r.ok) throw new Error(`Error ${r.status}`);
    toast(`Tabla ${esp.nombre_tabla_base} eliminada.`);
    seleccion = null;
    yaCargada = false;
    await cargar();
  } catch (e) {
    console.error("[areas.js eliminarTabla()]", e);
    toast(e.message || "No se pudo eliminar.", "error");
  }
}
let quitarPendiente = null; // {id, correo} a la espera de confirmación

function pedirQuitar(id, correo) {
  quitarPendiente = { id, correo };
  $("#quitarTexto").textContent = `¿Quitar a ${correo} el acceso a esta área?`;
  $("#modalQuitarRol").hidden = false;
}

function cerrarModalQuitar() {
  quitarPendiente = null;
  $("#modalQuitarRol").hidden = true;
}

$("#btnCancelarQuitar").addEventListener("click", cerrarModalQuitar);
$("#modalQuitarRol").addEventListener("click", (e) => {
  if (e.target.id === "modalQuitarRol") cerrarModalQuitar();
});
$("#btnConfirmarQuitar").addEventListener("click", async () => {
  if (!quitarPendiente) return;
  const { id, correo } = quitarPendiente;
  try {
    const r = await fetch(`api/areas/${id}/roles/` + encodeURIComponent(correo), {
      method: "DELETE", headers: authHeaders(),
    });
    if (r.status === 401) { window.snwSesionExpirada(); return; }
    if (!r.ok) throw new Error(`Error ${r.status}`);
    toast("Acceso retirado.");
    cerrarModalQuitar();
    yaCargada = false;
    await cargar();
  } catch (e) {
    console.error("[areas.js cerrarModalQuitar()]", e);
    toast(e.message || "No se pudo retirar el acceso.", "error");
  }
});

async function retirar(card, correo) {
  pedirQuitar(Number(card.dataset.id), correo);
}

async function crear(nombre, modo) {
  const r = await fetch("api/areas", {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ nombre, modo }),
  });
  const d = await r.json().catch(() => ({}));
  return { estado: r.status, ok: r.ok, datos: d };
}

const formCrear = $("#formCrearArea");
if (formCrear) formCrear.addEventListener("submit", async (e) => {
  e.preventDefault();
  const inp = $("#espNombre");
  const nombre = inp.value.trim();
  if (!nombre) return;
  msg("Creando…");
  try {
    let res = await crear(nombre, "preguntar");
    if (res.estado === 401) { window.snwSesionExpirada(); return; }
    if (res.estado === 409 && res.datos.detail && res.datos.detail.existentes) {
      // Nombre visible duplicado: la UI pregunta qué hacer.
      const nueva = confirm(
        "Ya existe un área con este nombre. ¿Qué deseas hacer?\n\n" +
        "Aceptar = Crear una nueva instancia (ej: «Kinesiología 2», tabla propia).\n" +
        "Cancelar = Utilizar el área existente."
      );
      res = await crear(nombre, nueva ? "nueva" : "reutilizar");
      if (res.estado === 401) { window.snwSesionExpirada(); return; }
    }
    if (!res.ok) {
      const det = res.datos.detail;
      throw new Error(typeof det === "string" ? det : `Error ${res.estado}`);
    }
    const esp = res.datos.area || {};
    msg(res.datos.creada
      ? `Creada «${esp.nombre_visible}» (tabla ${esp.nombre_tabla_base}).`
      : `Se utiliza el área existente «${esp.nombre_visible}».`);
    inp.value = "";
    toast("Área lista.");
    seleccion = esp.id ?? seleccion;
    yaCargada = false;
    await cargar();
  } catch (err) {
    console.error("[areas.js crear()]", err);
    msg(err.message || "No se pudo crear.", "error");
  }
});
})();
