const API_URL = "api/plantillas";

let plantillas = [];
let activaId = null;
let snapshot = null;

const $ = (sel) => document.querySelector(sel);

const listaEl = $("#listaPlantillas");
const buscadorEl = $("#buscador");
const formEl = $("#formPlantilla");
const inpNombre = $("#inpNombre");
const inpMensaje = $("#inpMensaje");
const tituloForm = $("#tituloFormulario");
const estadoVacio = $("#estadoVacio");
const btnEliminar = $("#btnEliminar");
const modalEl = $("#modalEliminar");
const toastEl = $("#toast");

async function cargar() {
  try {
    const res = await fetch(API_URL, { cache: "no-store" });
    if (!res.ok) throw new Error(res.status);
    plantillas = await res.json();
    renderLista(buscadorEl.value);
  } catch {
    toast("No se pudo conectar con la base de datos.", "error");
  }
}

async function crearPlantilla(nombre, texto) {
  const res = await fetch(API_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ clave: slug(nombre), nombre, texto }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail ?? data.error ?? `Error ${res.status}`);
  return data;
}

async function actualizarPlantilla(id, nombre, texto) {
  const res = await fetch(`${API_URL}/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ nombre, texto }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail ?? data.error ?? `Error ${res.status}`);
  return data;
}

async function eliminarPlantilla(id) {
  const res = await fetch(`${API_URL}/${id}`, { method: "DELETE" });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail ?? data.error ?? `Error ${res.status}`);
}

function slug(texto) {
  return texto
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 40);
}

function escaparHtml(texto) {
  const div = document.createElement("div");
  div.textContent = texto;
  return div.innerHTML;
}

function renderLista(filtro = "") {
  const q = filtro.trim().toLowerCase();
  const visibles = [...plantillas]
    .sort((a, b) => b.actualizada - a.actualizada)
    .filter(
      (p) =>
        !q ||
        p.nombre.toLowerCase().includes(q) ||
        p.texto.toLowerCase().includes(q)
    );

  listaEl.innerHTML = "";

  if (!visibles.length) {
    const li = document.createElement("li");
    li.className = "tpl-list__vacio";
    li.textContent = q ? "Sin resultados." : "No hay plantillas.";
    listaEl.appendChild(li);
    return;
  }

  for (const p of visibles) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "tpl-item" + (p.id === activaId ? " tpl-item--activa" : "");
    btn.innerHTML =
      `<span class="tpl-item__nombre">${escaparHtml(p.nombre)}</span>` +
      `<span class="tpl-item__vista">${escaparHtml(p.texto.split("\n")[0])}</span>`;
    btn.addEventListener("click", () => intentarAbrir(p.id));
    listaEl.appendChild(btn);
  }
}

function marcarSnapshot() {
  snapshot = JSON.stringify([inpNombre.value, inpMensaje.value]);
}

function hayCambios() {
  return snapshot !== null && snapshot !== JSON.stringify([inpNombre.value, inpMensaje.value]);
}

function abrir(id) {
  const p = plantillas.find((x) => x.id === id);
  if (!p) return;
  activaId = id;
  estadoVacio.style.display = "none";
  formEl.style.display = "";
  tituloForm.textContent = `Editando: ${p.nombre}`;
  inpNombre.value = p.nombre;
  inpMensaje.value = p.texto;
  btnEliminar.hidden = false;
  inpNombre.classList.remove("invalido");
  inpMensaje.classList.remove("invalido");
  marcarSnapshot();
  renderLista(buscadorEl.value);
}

function modoNueva() {
  activaId = null;
  estadoVacio.style.display = "none";
  formEl.style.display = "";
  tituloForm.textContent = "Nueva plantilla";
  inpNombre.value = "";
  inpMensaje.value = "";
  btnEliminar.hidden = true;
  inpNombre.classList.remove("invalido");
  inpMensaje.classList.remove("invalido");
  marcarSnapshot();
  renderLista(buscadorEl.value);
  inpNombre.focus();
}

function modoVacia() {
  activaId = null;
  snapshot = null;
  formEl.style.display = "none";
  estadoVacio.style.display = "flex";
  tituloForm.textContent = "Plantillas";
  btnEliminar.hidden = true;
  renderLista(buscadorEl.value);
}

function intentarAbrir(id) {
  if (id === activaId) return;
  if (hayCambios() && !confirm("Tienes cambios sin guardar. ¿Deseas descartarlos?")) {
    return;
  }
  abrir(id);
}

function intentarNueva() {
  if (hayCambios() && !confirm("Tienes cambios sin guardar. ¿Deseas descartarlos?")) {
    return;
  }
  modoNueva();
}

formEl.addEventListener("submit", async (e) => {
  e.preventDefault();

  const nombre = inpNombre.value.trim();
  const texto = inpMensaje.value.trim();

  inpNombre.classList.toggle("invalido", !nombre);
  inpMensaje.classList.toggle("invalido", !texto);

  if (!nombre) return toast("Falta el nombre.", "error");
  if (!texto) return toast("El mensaje está vacío.", "error");

  const duplicada = plantillas.some(
    (p) =>
      p.nombre.toLowerCase() === nombre.toLowerCase() &&
      p.id !== activaId
  );
  if (duplicada) {
    inpNombre.classList.add("invalido");
    return toast("Ya existe una plantilla con ese nombre.", "error");
  }

  try {
    let fila;
    if (activaId) {
      fila = await actualizarPlantilla(activaId, nombre, texto);
      const i = plantillas.findIndex((x) => x.id === activaId);
      if (i >= 0) plantillas[i] = fila;
      toast("Plantilla actualizada.", "ok");
    } else {
      fila = await crearPlantilla(nombre, texto);
      plantillas.push(fila);
      toast("Plantilla creada.", "ok");
    }
    abrir(fila.id);
  } catch (err) {
    toast(err.message === "Ya existe una plantilla con esa clave"
      ? "Ya existe una plantilla similar."
      : `Error al guardar: ${err.message}`, "error");
  }
});

btnEliminar.addEventListener("click", () => {
  const p = plantillas.find((x) => x.id === activaId);
  if (!p) return;
  $("#modalNombre").textContent = p.nombre;
  modalEl.hidden = false;
});

$("#btnModalCancelar").addEventListener("click", () => (modalEl.hidden = true));

$("#btnModalConfirmar").addEventListener("click", async () => {
  try {
    await eliminarPlantilla(activaId);
    plantillas = plantillas.filter((x) => x.id !== activaId);
    modalEl.hidden = true;
    toast("Plantilla eliminada.", "ok");
    modoVacia();
  } catch (err) {
    modalEl.hidden = true;
    toast(`Error al eliminar: ${err.message}`, "error");
  }
});

$("#btnCancelar").addEventListener("click", () => {
  if (hayCambios() && !confirm("¿Descartar los cambios?")) return;
  activaId ? abrir(activaId) : modoVacia();
});

document.querySelectorAll(".chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    const token = chip.dataset.wc;
    const pos = inpMensaje.selectionStart ?? inpMensaje.value.length;
    const antes = inpMensaje.value.slice(0, pos);
    const despues = inpMensaje.value.slice(inpMensaje.selectionEnd ?? pos);
    inpMensaje.value = antes + token + despues;
    const nuevaPos = pos + token.length;
    inpMensaje.focus();
    inpMensaje.setSelectionRange(nuevaPos, nuevaPos);
  });
});

inpNombre.addEventListener("input", () => inpNombre.classList.remove("invalido"));
buscadorEl.addEventListener("input", () => renderLista(buscadorEl.value));
$("#btnNueva").addEventListener("click", intentarNueva);
$("#btnNuevaEmpty").addEventListener("click", intentarNueva);

let toastTimer;
function toast(msg, tipo = "ok") {
  clearTimeout(toastTimer);
  toastEl.textContent = msg;
  toastEl.className = `toast visible toast--${tipo}`;
  toastTimer = setTimeout(() => toastEl.classList.remove("visible"), 2800);
}

modoVacia();
cargar();
