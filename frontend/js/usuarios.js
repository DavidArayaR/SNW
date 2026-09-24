/* Gestión de usuarios y permisos (administrador / desarrollador).
   Pestaña «Usuarios» de administracion.html*/
(function () {
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
  mensajeria: "Mensajería",
  historial: "Historial",
  plantillas_editar: "Editar plantillas",
  envio_produccion: "Enviar en producción sin confirmación",
  call_center_registro: "Ver registro de respuestas de call center",
};
const _EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]{2,}$/;
const PERM_PAGINAS = ["mensajeria", "historial"];
const PERM_ACCIONES = ["plantillas_editar", "envio_produccion", "call_center_registro"];
const PERMISOS_BASICOS = ["mensajeria", "historial", "plantillas_editar"];
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

function fmtMoneda(monto, moneda) {
  const entero = Number.isInteger(monto);
  const s = monto.toLocaleString("de-DE", {
    minimumFractionDigits: entero ? 0 : 2,
    maximumFractionDigits: entero ? 0 : 2,
  });
  return `${s} ${moneda}`;
}

const ENVIO_ESTADO_LABEL = { completado: "Aprobado", rechazado: "Rechazado", cancelado: "Cancelado" };

function renderEnviosUsuario(lista) {
  if (!lista.length) return `<p class="usr-envios__vacio">Sin envíos registrados.</p>`;
  const filas = lista.map((e) => {
    const costoTxt = e.costo ? fmtMoneda(e.costo.costo, e.costo.moneda) : "—";
    return `<tr>` +
      `<td>${esc(e.fecha)}</td>` +
      `<td>${esc(e.plantilla_nombre || e.plantilla_clave || "—")}</td>` +
      `<td><span class="usr-tag usr-tag--envio-${esc(e.estado)}">${esc(ENVIO_ESTADO_LABEL[e.estado] || e.estado)}</span></td>` +
      `<td>${e.total_pacientes ?? 0}</td>` +
      `<td>${costoTxt}</td>` +
    `</tr>`;
  }).join("");
  return `<table class="usr-envios__tabla">` +
    `<thead><tr><th>Fecha</th><th>Plantilla</th><th>Estado</th><th>Pacientes</th><th>Costo</th></tr></thead>` +
    `<tbody>${filas}</tbody></table>`;
}

async function cargarEnviosUsuario(correo, contenedor) {
  if (!contenedor) return;
  try {
    const r = await fetch("api/usuarios/" + encodeURIComponent(correo) + "/envios", {
      headers: authHeaders(), cache: "no-store",
    });
    if (r.status === 401) { window.snwSesionExpirada(); return; }
    if (!r.ok) throw new Error();
    const lista = await r.json();
    if (seleccion !== correo) return;   // la selección cambió mientras cargaba
    contenedor.innerHTML = renderEnviosUsuario(lista);
  } catch (e) {
    if (seleccion === correo) contenedor.innerHTML = `<p class="usr-envios__vacio">No se pudieron cargar los envíos.</p>`;
  }
}

const AUDITORIA_ACCION_LABEL = {
  invitar: "Invitación enviada",
  cuenta_creada: "Cuenta creada",
  editar: "Cambios en la cuenta",
  correo_recuperacion: "Correo de recuperación asignado",
  reset_clave: "Cambio de contraseña activado",
  eliminar: "Cuenta eliminada",
  plantilla_creada: "Plantilla creada",
};

function renderAuditoriaUsuario(lista) {
  if (!lista.length) return `<p class="usr-envios__vacio">Sin actividad registrada.</p>`;
  const filas = lista.map((a) => {
    return `<tr>` +
      `<td>${esc(a.fecha)}</td>` +
      `<td><span class="usr-tag usr-tag--${esc(a.accion)}">${esc(AUDITORIA_ACCION_LABEL[a.accion] || a.accion)}</span></td>` +
      `<td>${esc(a.objetivo || "—")}</td>` +
      `<td>${esc(a.detalle || "")}</td>` +
    `</tr>`;
  }).join("");
  return `<table class="usr-envios__tabla">` +
    `<thead><tr><th>Fecha</th><th>Acción</th><th>Objetivo</th><th>Detalle</th></tr></thead>` +
    `<tbody>${filas}</tbody></table>`;
}

async function cargarAuditoriaUsuario(correo, contenedor) {
  if (!contenedor) return;
  try {
    const r = await fetch("api/usuarios/" + encodeURIComponent(correo) + "/auditoria", {
      headers: authHeaders(), cache: "no-store",
    });
    if (r.status === 401) { window.snwSesionExpirada(); return; }
    if (!r.ok) throw new Error();
    const lista = await r.json();
    if (seleccion !== correo) return;
    contenedor.innerHTML = renderAuditoriaUsuario(lista);
  } catch (e) {
    if (seleccion === correo) contenedor.innerHTML = `<p class="usr-envios__vacio">No se pudo cargar la actividad.</p>`;
  }
}

async function cargar() {
  try {
    const r = await fetch("api/usuarios", { headers: authHeaders(), cache: "no-store" });
    if (r.status === 401) { window.snwSesionExpirada(); return; }
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
    // Un administrador solo puede ascender a «administrador»; nunca a «desarrollador».
    let disponibles = estado.roles || ["usuario", "administrador", "desarrollador"];
    if (estado.mi_rol === "administrador") {
      disponibles = disponibles.filter((r) => r !== "desarrollador");
    }
    const opts = disponibles
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
    // Card 1: la cuenta en sí (datos + permisos + acciones).
    `<div class="usr-card${bloqueada ? " usr-card--bloqueada" : ""}" data-correo="${esc(u.usuario)}">` +
      `<div class="usr-card__cab">` +
        `<span class="usr-card__correo">${esc(u.usuario)}</span>` +
        (u.es_actual ? `<span class="usr-tag usr-tag--yo">Tú</span>` : "") +
        (estado.puede_cambiar_rol && u.editable ? "" : `<span class="usr-tag usr-tag--${esc(u.rol)}">${esc(ROL_LABEL[u.rol] || u.rol)}</span>`) +
        (u.activo === false ? `<span class="usr-tag usr-tag--inactivo">Desactivada</span>` : "") +
      `</div>` +
      `<div class="usr-grid">` +
        `<label>Nombre</label>` +
        `<div><input type="text" data-nombre value="${esc(u.nombre)}" maxlength="120"${u.editable ? "" : " disabled"}></div>` +
        (estado.puede_cambiar_rol && u.editable ? `<label>Rol</label><div>${rolControl(u)}</div>` : "") +
        `<label>Permisos</label>` +
        `<div id="permWrap">${permisosCheckboxes(u.rol, u.permisos, u.editable)}</div>` +
        (u.editable ?
          `<label>Acceso</label>` +
          `<div><label class="usr-check"><input type="checkbox" data-activo${u.activo === false ? "" : " checked"}> Cuenta activa (puede iniciar sesión)</label></div>`
          : "") +
        (u.editable ?
          `<label>Correo</label>` +
          `<div class="usr-reset">` +
            `<div class="usr-reset__correo">` +
              `<input type="email" data-correo-rec value="${esc(u.correo_recuperacion || "")}" placeholder="correo@ejemplo.cl">` +
              `<button type="button" class="btn btn--ghost" data-guardar-correo>Guardar correo</button>` +
            `</div>` +
            `<button type="button" class="btn btn--primary" data-enviar-reset data-correo-guardado="${esc(u.correo_recuperacion || "")}"${u.correo_recuperacion ? "" : " disabled"}>Activar cambio de contraseña</button>` +
            `<p class="usr-reset__hint" data-reset-hint>` +
              (u.correo_recuperacion
                ? `Se enviará un enlace de cambio de contraseña a <strong>${esc(u.correo_recuperacion)}</strong>.`
                : `Esta cuenta no tiene correo asignado: escribe uno y presiona «Guardar correo» para poder activar el cambio de contraseña.`) +
            `</p>` +
          `</div>`
          : "") +
      `</div>` +
      `<div class="usr-card__pie">` +
        (bloqueada ? `<span class="usr-card__bloqueo">${esc(u.motivo_bloqueo || "No puedes gestionar esta cuenta.")}</span>` : "") +
        `<span class="usr-card__sep"></span>` +
        (u.editable ? `<button type="button" class="btn btn--danger-ghost" data-borrar>Eliminar</button>` : "") +
        (u.editable ? `<button type="button" class="btn btn--primary" data-guardar>Guardar cambios</button>` : "") +
      `</div>` +
    `</div>` +
    // Card 2: Envíos realizados (de solo lectura).
    `<div class="usr-card usr-card--envios">` +
      `<h4>Envíos realizados</h4>` +
      `<div id="usrEnvios" class="usr-envios__cont">Cargando envíos…</div>` +
    `</div>` +
    // Card 3: Actividad (de solo lectura).
    `<div class="usr-card usr-card--envios">` +
      `<h4>Actividad</h4>` +
      `<p style="margin: 0 0 8px; font-size: .84rem; color: var(--texto-suave);">Acciones que hizo esta cuenta (a quién invitó, editó, eliminó, qué plantilla creó...).</p>` +
      `<div id="usrAuditoria" class="usr-envios__cont">Cargando actividad…</div>` +
    `</div>`;

  cargarEnviosUsuario(u.usuario, detalleEl.querySelector("#usrEnvios"));
  cargarAuditoriaUsuario(u.usuario, detalleEl.querySelector("#usrAuditoria"));

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
  const chkActivo = card.querySelector("[data-activo]");
  if (chkActivo) cuerpo.activo = chkActivo.checked;

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
  if (e.target.closest("[data-guardar-correo]")) guardarCorreoRecuperacion(card);
  if (e.target.closest("[data-enviar-reset]")) enviarCambioClave(card);
});

async function guardarCorreoRecuperacion(card) {
  const correo = card.dataset.correo;
  const inp = card.querySelector("[data-correo-rec]");
  const nuevo = inp.value.trim().toLowerCase();
  if (!_EMAIL_RE.test(nuevo)) {
    toast("Escribe un correo electrónico válido.", "error");
    return;
  }
  const btn = card.querySelector("[data-guardar-correo]");
  btn.disabled = true;
  try {
    const r = await fetch("api/usuarios/" + encodeURIComponent(correo) + "/correo-recuperacion", {
      method: "PUT",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ correo: nuevo }),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || "No se pudo guardar el correo.");
    toast("Correo de recuperación guardado.");
    await cargar();
  } catch (ex) {
    toast(ex.message, "error");
  } finally {
    btn.disabled = false;
  }
}

async function enviarCambioClave(card) {
  const correo = card.dataset.correo;
  const btnReset = card.querySelector("[data-enviar-reset]");
  const correoRec = (btnReset && btnReset.dataset.correoGuardado) || "";
  if (!correoRec) return;
  if (!confirm(`Se enviará un enlace para cambiar la contraseña a "${correoRec}". ¿Confirmar el correo y continuar?`)) {
    return;
  }
  btnReset.disabled = true;
  try {
    const r = await fetch("api/usuarios/" + encodeURIComponent(correo) + "/enviar-cambio-clave", {
      method: "POST",
      headers: authHeaders(),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || "No se pudo enviar el enlace.");
    toast("Enlace de cambio de contraseña enviado a " + data.destino + ".");
  } catch (ex) {
    toast(ex.message, "error");
  } finally {
    btnReset.disabled = false;
  }
}

const formInvitar = $("#formInvitar");
const invMsg = $("#invMsg");

formInvitar.addEventListener("submit", async (e) => {
  e.preventDefault();
  const inp = $("#invCorreo");
  const correo = inp.value.trim().toLowerCase();
  invMsg.textContent = "";
  invMsg.className = "usr-intro";
  if (!_EMAIL_RE.test(correo)) {
    invMsg.textContent = "Escribe un correo electrónico válido.";
    return;
  }
  const btn = $("#btnInvitar");
  btn.disabled = true;
  try {
    const r = await fetch("api/usuarios/invitar", {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ correo }),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || "No se pudo enviar la invitación.");
    toast("Invitación enviada a " + correo + ".");
    inp.value = "";
  } catch (ex) {
    invMsg.textContent = ex.message;
  } finally {
    btn.disabled = false;
  }
});

window.snwConCooldown($("#btnRecargar"), cargar);
$("#btnCancelarBorrar").addEventListener("click", cerrarModal);
$("#btnConfirmarBorrar").addEventListener("click", eliminar);
$("#modalBorrar").addEventListener("click", (e) => { if (e.target.id === "modalBorrar") cerrarModal(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") cerrarModal(); });

cargar();
})();
