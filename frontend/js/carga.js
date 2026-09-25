/* Carga de base de datos vía CSV (todas las cuentas con permiso de
   mensajería): el archivo se sube a una especialidad asignada. */
(function () {
const $ = (s) => document.querySelector(s);

function authHeaders(extra = {}) {
  return { Authorization: "Bearer " + (localStorage.getItem("snw_token") || ""), ...extra };
}

if (!localStorage.getItem("snw_token")) location.replace("login.html");

const selEsp = $("#selEspCarga");
const inpArchivo = $("#csvArchivoCarga");
const nombreArchivo = $("#nombreArchivoCarga");
const btnSubir = $("#btnSubirCsv");
const aviso = $("#csvSinEsp");
const resultado = $("#csvResultado");
const toastEl = $("#toast");

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

async function init() {
  let lista = [];
  try {
    const r = await fetch("api/especialidades/mias", { headers: authHeaders(), cache: "no-store" });
    if (r.status === 401) { window.snwSesionExpirada(); return; }
    if (!r.ok) throw new Error();
    lista = await r.json();
  } catch {
    toast("No se pudieron cargar tus especialidades.", "error");
  }
  selEsp.innerHTML = lista.map((e) =>
    `<option value="${e.id}">${esc(e.nombre_visible)} (${esc(e.nombre_tabla_base)})</option>`).join("");
  const sinAsignadas = !lista.length;
  aviso.hidden = !sinAsignadas;
  selEsp.disabled = sinAsignadas;
  inpArchivo.disabled = sinAsignadas;
  btnSubir.disabled = sinAsignadas;
}

inpArchivo.addEventListener("change", () => {
  const f = inpArchivo.files && inpArchivo.files[0];
  nombreArchivo.textContent = f
    ? `${f.name} (${(f.size / 1024).toFixed(1)} KB)`
    : "No se ha seleccionado ningún archivo";
});

btnSubir.addEventListener("click", async () => {
  const espId = selEsp.value;
  const archivo = inpArchivo.files && inpArchivo.files[0];
  if (!espId) return toast("Elige la especialidad destino.", "error");
  if (!archivo) return toast("Elige un archivo CSV.", "error");
  btnSubir.disabled = true;
  resultado.innerHTML = "<p class=\"field__hint\">Subiendo…</p>";
  try {
    const datos = new FormData();
    datos.append("archivo", archivo);
    const res = await fetch(`api/especialidades/${encodeURIComponent(espId)}/pacientes/csv`, {
      method: "POST", headers: authHeaders(), body: datos,
    });
    const cuerpo = await res.json().catch(() => ({}));
    if (res.status === 401) { window.snwSesionExpirada(); return; }
    if (res.status === 403) throw new Error("No tienes acceso a esta especialidad.");
    if (!res.ok) throw new Error(typeof cuerpo.detail === "string" ? cuerpo.detail : `Error ${res.status}`);
    const inf = cuerpo.informe || {};
    const errores = inf.errores || [];
    const mostrados = errores.slice(0, 20);
    resultado.innerHTML =
      `<p><strong>${inf.insertados ?? 0}</strong> insertados de ` +
      `${inf.procesados ?? 0} procesados ` +
      `(${inf.duplicados ?? 0} duplicados, ${inf.rechazados ?? 0} rechazados).</p>` +
      (mostrados.length
        ? `<ul>` + mostrados.map((e) => `<li>fila ${e.fila}: ${esc(e.motivo)}</li>`).join("") + `</ul>` +
          (errores.length > mostrados.length ? `<p class="field__hint">…y ${errores.length - mostrados.length} más.</p>` : "")
        : "");
    toast("Base de datos cargada.", "ok");
    inpArchivo.value = "";
    nombreArchivo.textContent = "No se ha seleccionado ningún archivo";
  } catch (err) {
    resultado.innerHTML = "";
    toast(err.message || "No se pudo cargar el archivo.", "error");
  } finally {
    btnSubir.disabled = selEsp.disabled;
  }
});

init();
})();
