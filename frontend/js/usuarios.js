/* Gestión de usuarios y permisos (administrador / desarrollador). */

const $ = (s) => document.querySelector(s);

function authHeaders(extra = {}) {
  return { Authorization: "Bearer " + (localStorage.getItem("snw_token") || ""), ...extra };
}

if (!localStorage.getItem("snw_token")) location.replace("login.html");

const selEl = $("#selUsuario");
const detalleEl = $("#detalle");
const vacioEl = $("#vacio");
const toastEl = $("#toast");

const PERM_LABEL = {
  pacientes: "Base de datos",
  mensajeria: "Mensajería",
  historial: "Historial",
  estadisticas: "Estadísticas",
  plantillas_editar: "Editar plantillas",
  envio_produccion: "Enviar en producción sin confirmación",
  tarifas_editar: "Administrar tarifas y costos",
  call_center: "Plantillas de call center",
};
const PERM_PAGINAS = ["pacientes", "mensajeria", "historial", "estadisticas"];
const PERM_ACCIONES = ["plantillas_editar", "envio_produccion", "tarifas_editar", "call_center"];
const PERMISOS_BASICOS = ["mensajeria", "historial", "estadisticas", "plantillas_editar"];
const ROL_LABEL = { usuario: "Usuario", administrador: "Administrador", desarrollador: "Desarrollador" };
const ROLES_TOTALES = ["administrador", "desarrollador"];

let estado = { usuarios: [], roles: [], mi_rol: "", puede_cambiar_rol: false };
let seleccion = null;   // correo de la cuenta mostrada en el panel
let borrarCorreo = null;

let toastTimer;
function toast(msg, tipo = "ok") {
  clearTimeout(toastTimer);
  toastEl.textContent = msg;
  toastEl.className = `toast visible toast--${tipo}`;
  toastTimer = setTimeout(() => toastEl.classList.remove("visible"), 3200);
}

function esc(t) {
  const d = document.createElement("div");
  d.textContent = t ?? "";
  return d.innerHTML;
}

async function cargar() {
  try {
    const r = await fetch("api/usuarios", { headers: authHeaders(), cache: "no-store" });
    if (r.status === 401) { window.snwSalir(); return; }
    if (r.status === 403) { location.replace("mensajeria.html"); return; }
    if (!r.ok) throw new Error();
    estado = await r.json();
    $("#badgeRol").textContent = ROL_LABEL[estado.mi_rol] || estado.mi_rol || "";
    const info = $("#devInfo");
    if (info && estado.max_desarrolladores) {
      info.textContent = `Desarrolladores: ${estado.desarrolladores} de ${estado.max_desarrolladores}.`;
    }
    render();
  } catch (e) {
    toast("No se pudieron cargar las cuentas.", "error");
  }
}

function permisosCheckboxes(rol, permisos, editable) {
  if (ROLES_TOTALES.indexOf(rol) !== -1) {
    return `<p class="usr-total-nota">El rol <strong>${esc(ROL_LABEL[rol] || rol)}</strong> tiene acceso total a todo el sistema; sus permisos no se editan por separado.</p>`;
  }
  const set = new Set(permisos || []);
  const bloque = (titulo, claves) =>
    `<div class="usr-perms__grupo">${titulo}</div>` +
    claves.map((k) => {
      const on = set.has(k);
      return `<label class="usr-perm${on ? "" : " usr-perm--off"}">` +
        `<input type="checkbox" data-perm="${k}"${on ? " checked" : ""}${editable ? "" : " disabled"}>` +
        `<span>${esc(PERM_LABEL[k] || k)}</span></label>`;
    }).join("");
  return `<div class="usr-perms">${bloque("Páginas", PERM_PAGINAS)}${bloque("Acciones", PERM_ACCIONES)}</div>`;
}

function rolControl(u) {
  if (estado.puede_cambiar_rol && u.editable) {
    const opts = (estado.roles || ["usuario", "administrador", "desarrollador"])
      .map((r) => `<option value="${r}"${r === u.rol ? " selected" : ""}>${esc(ROL_LABEL[r] || r)}</option>`)
      .join("");
    return `<select data-rol>${opts}</select>`;
  }
  return `<span class="usr-tag usr-tag--${esc(u.rol)}">${esc(ROL_LABEL[u.rol] || u.rol)}</span>`;
}

function render() {
  const us = estado.usuarios || [];
  vacioEl.hidden = us.length > 0;
  selEl.parentElement.hidden = us.length === 0;

  // Mantener la selección si sigue existiendo; si no, abrir en la primera
  // cuenta gestionable (o la primera de la lista si no hay ninguna).
  if (!us.some((u) => u.usuario === seleccion)) {
    const primeraEditable = us.find((u) => u.editable);
    seleccion = (primeraEditable || us[0] || {}).usuario || null;
  }

  selEl.innerHTML = us.map((u) => {
    const etq = `${u.usuario} — ${ROL_LABEL[u.rol] || u.rol}` + (u.es_actual ? " (tú)" : "");
    return `<option value="${esc(u.usuario)}"${u.usuario === seleccion ? " selected" : ""}>${esc(etq)}</option>`;
  }).join("");
  if (seleccion) selEl.value = seleccion;

  renderDetalle();
}

function renderDetalle() {
  const u = (estado.usuarios || []).find((x) => x.usuario === seleccion);
  if (!u) { detalleEl.innerHTML = ""; return; }
  const bloqueada = !u.editable;
  detalleEl.innerHTML =
    `<div class="usr-card${bloqueada ? " usr-card--bloqueada" : ""}" data-correo="${esc(u.usuario)}">` +
      `<div class="usr-card__cab">` +
        `<span class="usr-card__correo">${esc(u.usuario)}</span>` +
        (u.es_actual ? `<span class="usr-tag usr-tag--yo">Tú</span>` : "") +
        (estado.puede_cambiar_rol && u.editable ? "" : `<span class="usr-tag usr-tag--${esc(u.rol)}">${esc(ROL_LABEL[u.rol] || u.rol)}</span>`) +
      `</div>` +
      `<div class="usr-grid">` +
        `<label>Nombre</label>` +
        `<div><input type="text" data-nombre value="${esc(u.nombre)}" maxlength="120"${u.editable ? "" : " disabled"}></div>` +
        (estado.puede_cambiar_rol && u.editable ? `<label>Rol</label><div>${rolControl(u)}</div>` : "") +
        `<label>Permisos</label>` +
        `<div id="permWrap">${permisosCheckboxes(u.rol, u.permisos, u.editable)}</div>` +
      `</div>` +
      `<div class="usr-card__pie">` +
        (bloqueada ? `<span class="usr-card__bloqueo">${esc(u.motivo_bloqueo || "No puedes gestionar esta cuenta.")}</span>` : "") +
        `<span class="usr-card__sep"></span>` +
        (u.editable ? `<button type="button" class="btn btn--danger-ghost" data-borrar>Eliminar</button>` : "") +
        (u.editable ? `<button type="button" class="btn btn--primary" data-guardar>Guardar cambios</button>` : "") +
      `</div>` +
    `</div>`;

  // Al cambiar el rol: si pasa a «usuario», se muestran los permisos con los
  // básicos ya marcados; si pasa a un rol total, se muestra la nota.
  const selRol = detalleEl.querySelector("[data-rol]");
  if (selRol) {
    selRol.addEventListener("change", () => {
      const nuevoRol = selRol.value;
      let permisos = u.permisos || [];
      if (nuevoRol === "usuario" && u.rol !== "usuario") permisos = PERMISOS_BASICOS;
      detalleEl.querySelector("#permWrap").innerHTML =
        permisosCheckboxes(nuevoRol, permisos, u.editable);
    });
  }
}

async function guardar(card) {
  const correo = card.dataset.correo;
  const nombre = card.querySelector("[data-nombre]").value.trim();
  const selRol = card.querySelector("[data-rol]");
  const cuerpo = { nombre };
  if (selRol) cuerpo.rol = selRol.value;
  // Solo se envían los permisos cuando hay casillas (rol «usuario»); para un
  // rol total no aplican y no se tocan.
  const casillas = card.querySelectorAll("[data-perm]");
  if (casillas.length) {
    cuerpo.permisos = [...casillas].filter((c) => c.checked).map((c) => c.dataset.perm);
  }

  const btn = card.querySelector("[data-guardar]");
  btn.disabled = true;
  try {
    const r = await fetch("api/usuarios/" + encodeURIComponent(correo), {
      method: "PUT",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(cuerpo),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || "No se pudo guardar.");
    toast("Cuenta actualizada.");
    await cargar();
  } catch (e) {
    toast(e.message, "error");
    btn.disabled = false;
  }
}

async function eliminar() {
  if (!borrarCorreo) return;
  try {
    const r = await fetch("api/usuarios/" + encodeURIComponent(borrarCorreo), {
      method: "DELETE", headers: authHeaders(),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || "No se pudo eliminar.");
    toast("Cuenta eliminada.");
    cerrarModal();
    await cargar();
  } catch (e) {
    toast(e.message, "error");
  }
}

function cerrarModal() {
  $("#modalBorrar").hidden = true;
  borrarCorreo = null;
}

selEl.addEventListener("change", () => {
  seleccion = selEl.value;
  renderDetalle();
});

detalleEl.addEventListener("click", (e) => {
  const card = e.target.closest(".usr-card");
  if (!card) return;
  if (e.target.closest("[data-guardar]")) guardar(card);
  if (e.target.closest("[data-borrar]")) {
    borrarCorreo = card.dataset.correo;
    $("#borrarTexto").textContent =
      `Se eliminará la cuenta "${borrarCorreo}" y se cerrarán sus sesiones. Esta acción no se puede deshacer.`;
    $("#modalBorrar").hidden = false;
  }
});

$("#btnRecargar").addEventListener("click", cargar);
$("#btnCancelarBorrar").addEventListener("click", cerrarModal);
$("#btnConfirmarBorrar").addEventListener("click", eliminar);
$("#modalBorrar").addEventListener("click", (e) => { if (e.target.id === "modalBorrar") cerrarModal(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") cerrarModal(); });

cargar();
