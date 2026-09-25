/* Gestión de especialidades y sus roles (administrador / desarrollador).
   Página «Especialidades» (especialidades.html) */
(function () {
const $ = (s) => document.querySelector(s);

function authHeaders(extra = {}) {
  return { Authorization: "Bearer " + (localStorage.getItem("snw_token") || ""), ...extra };
}

if (!localStorage.getItem("snw_token")) location.replace("login.html");

const selEl = $("#selEspecialidad");
const detalleEl = $("#detalleEspecialidad");
const vacioEl = $("#espVacio");
const msgEl = $("#espMsg");
const toastEl = $("#toast");

let estado = { lista: [], usuarios: [] };
let seleccion = null;   // id de la especialidad mostrada
let yaCargada = false;

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

function msg(texto, tipo = "") {
  msgEl.textContent = texto;
  msgEl.style.color = tipo === "error" ? "var(--danger-fg)" : "";
}

async function cargar() {
  yaCargada = true;
  try {
    const [re, ru] = await Promise.all([
      fetch("api/especialidades", { headers: authHeaders(), cache: "no-store" }),
      fetch("api/usuarios", { headers: authHeaders(), cache: "no-store" }),
    ]);
    if (re.status === 401 || ru.status === 401) { window.snwSesionExpirada(); return; }
    if (!re.ok) throw new Error();
    estado.lista = await re.json();
    estado.usuarios = ru.ok ? ((await ru.json()).usuarios || []) : [];
    msg("");
    render();
  } catch (e) {
    toast("No se pudieron cargar las especialidades.", "error");
  }
}

window.snwCargarEspecialidades = function () {
  if (yaCargada) return;
  cargar();
};

const btnActualizar = $("#btnActualizarEspecialidades");
if (btnActualizar) btnActualizar.addEventListener("click", () => { yaCargada = false; cargar(); });

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
    (u.especialidades || []).some((e) => e.id === espId));
}

function usuariosAgregables(espId) {
  const conRol = new Set(usuariosConRol(espId).map((u) => u.usuario));
  return (estado.usuarios || []).filter((u) => u.editable && !conRol.has(u.usuario));
}

function renderDetalle() {
  const esp = (estado.lista || []).find((x) => x.id === seleccion);
  if (!esp) { detalleEl.innerHTML = ""; return; }
  const asignados = usuariosConRol(esp.id);
  const agregables = usuariosAgregables(esp.id);

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
          (asignados.length
            ? `<ul class="usr-envios__cont" style="list-style:none; margin:0; padding:0;">` + asignados.map((u) =>
              `<li style="display:flex; align-items:center; gap:8px; padding:4px 0;">` +
                `<span>${esc(u.nombre || u.usuario)} <span class="field__hint">(${esc(u.usuario)} · ${esc(u.rol)})</span></span>` +
                (u.editable
                  ? `<button type="button" class="btn btn--danger-ghost" data-quitar="${esc(u.usuario)}">Quitar</button>`
                  : "") +
              `</li>`).join("") + `</ul>`
            : `<p class="usr-envios__vacio">Ninguna cuenta tiene este rol todavía.</p>`) +
        `</div>` +
        `<label>Asignar rol</label>` +
        `<div class="usr-reset__correo">` +
          `<select data-nuevo>${agregables.map((u) =>
            `<option value="${esc(u.usuario)}">${esc(u.nombre || u.usuario)} (${esc(u.usuario)})</option>`).join("")}</select>` +
          `<button type="button" class="btn btn--primary" data-asignar${agregables.length ? "" : " disabled"}>Asignar</button>` +
        `</div>` +
      `</div>` +
    `</div>`;

  const card = detalleEl.querySelector(".usr-card");
  card.querySelector("[data-renombrar]").addEventListener("click", () => renombrar(card));
  card.querySelector("[data-asignar]").addEventListener("click", () => asignar(card));
  card.querySelectorAll("[data-quitar]").forEach((b) =>
    b.addEventListener("click", () => retirar(card, b.dataset.quitar)));
}

async function renombrar(card) {
  const id = Number(card.dataset.id);
  const nombre = card.querySelector("[data-nombre]").value.trim();
  if (!nombre) return toast("Escribe el nombre visible.", "error");
  try {
    const r = await fetch("api/especialidades/" + id, {
      method: "PUT",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ nombre_visible: nombre }),
    });
    const d = await r.json().catch(() => ({}));
    if (r.status === 401) { window.snwSesionExpirada(); return; }
    if (!r.ok) throw new Error(typeof d.detail === "string" ? d.detail : `Error ${r.status}`);
    toast("Especialidad renombrada.");
    yaCargada = false;
    await cargar();
  } catch (e) {
    toast(e.message || "No se pudo renombrar.", "error");
  }
}

async function asignar(card) {
  const id = Number(card.dataset.id);
  const sel = card.querySelector("[data-nuevo]");
  if (!sel || !sel.value) return;
  try {
    const r = await fetch(`api/especialidades/${id}/roles`, {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ usuario: sel.value }),
    });
    const d = await r.json().catch(() => ({}));
    if (r.status === 401) { window.snwSesionExpirada(); return; }
    if (!r.ok) throw new Error(typeof d.detail === "string" ? d.detail : `Error ${r.status}`);
    toast(`Rol asignado a ${sel.value}.`);
    yaCargada = false;
    await cargar();
  } catch (e) {
    toast(e.message || "No se pudo asignar el rol.", "error");
  }
}

async function retirar(card, correo) {
  const id = Number(card.dataset.id);
  if (!confirm(`¿Quitar a ${correo} el acceso a esta especialidad?`)) return;
  try {
    const r = await fetch(`api/especialidades/${id}/roles/` + encodeURIComponent(correo), {
      method: "DELETE", headers: authHeaders(),
    });
    if (r.status === 401) { window.snwSesionExpirada(); return; }
    if (!r.ok) throw new Error(`Error ${r.status}`);
    toast("Acceso retirado.");
    yaCargada = false;
    await cargar();
  } catch (e) {
    toast(e.message || "No se pudo retirar el acceso.", "error");
  }
}

async function crear(nombre, modo) {
  const r = await fetch("api/especialidades", {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ nombre, modo }),
  });
  const d = await r.json().catch(() => ({}));
  return { estado: r.status, ok: r.ok, datos: d };
}

const formCrear = $("#formCrearEspecialidad");
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
        "Ya existe una especialidad con este nombre. ¿Qué deseas hacer?\n\n" +
        "Aceptar = Crear una nueva instancia (ej: «Kinesiología 2», tabla propia).\n" +
        "Cancelar = Utilizar la especialidad existente."
      );
      res = await crear(nombre, nueva ? "nueva" : "reutilizar");
      if (res.estado === 401) { window.snwSesionExpirada(); return; }
    }
    if (!res.ok) {
      const det = res.datos.detail;
      throw new Error(typeof det === "string" ? det : `Error ${res.estado}`);
    }
    const esp = res.datos.especialidad || {};
    msg(res.datos.creada
      ? `Creada «${esp.nombre_visible}» (tabla ${esp.nombre_tabla_base}).`
      : `Se utiliza la especialidad existente «${esp.nombre_visible}».`);
    inp.value = "";
    toast("Especialidad lista.");
    seleccion = esp.id ?? seleccion;
    yaCargada = false;
    await cargar();
  } catch (err) {
    msg(err.message || "No se pudo crear.", "error");
  }
});
})();
