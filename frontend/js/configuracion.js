const $ = (sel) => document.querySelector(sel);

function authHeaders(extra = {}) {
  return { Authorization: "Bearer " + (localStorage.getItem("snw_token") || ""), ...extra };
}

if (!localStorage.getItem("snw_token")) location.replace("login.html");
else if (localStorage.getItem("snw_rol") !== "desarrollador") location.replace("mensajeria.html");

const toastEl = $("#toast");
let toastTimer;
function toast(msg, tipo = "ok") {
  clearTimeout(toastTimer);
  toastEl.textContent = msg;
  toastEl.className = `toast visible toast--${tipo}`;
  toastTimer = setTimeout(() => toastEl.classList.remove("visible"), 3600);
}

const cont = $("#secciones");
let valores = {};          // valor guardado (servidor)
let campos = {};           // clave -> { def, input }
let derivados = [];        // campos calculados (ej. URL del webhook)

// Une URL base + ruta evitando "/" duplicados o faltantes.
function unirUrl(base, ruta) {
  base = (base || "").trim().replace(/\/+$/, "");
  ruta = (ruta || "").trim();
  if (!base && !ruta) return "";
  if (ruta && !ruta.startsWith("/")) ruta = "/" + ruta;
  return base + ruta;
}

function escaparHtml(t) {
  const d = document.createElement("div");
  d.textContent = t ?? "";
  return d.innerHTML;
}

async function cargar() {
  try {
    const res = await fetch("api/configuracion/todo", { headers: authHeaders(), cache: "no-store" });
    if (res.status === 401) { window.snwSalir(); return; }
    if (res.status === 403) { location.replace("mensajeria.html"); return; }
    if (!res.ok) throw new Error();
    const d = await res.json();
    valores = d.valores || {};
    render(d.secciones || []);
  } catch {
    cont.innerHTML = '<p class="mes-vacio" style="padding:18px;">No se pudo cargar la configuración.</p>';
  }
}

function render(secciones) {
  cont.innerHTML = "";
  campos = {};
  derivados = [];

  for (const s of secciones) {
    const panel = document.createElement("section");
    panel.className = "panel config-seccion";
    panel.innerHTML =
      `<div class="panel__head"><h2><i class="${s.marca ? "fa-brands" : "fa-solid"} ${s.icono || "fa-gear"}"></i> ${escaparHtml(s.titulo)}</h2></div>` +
      `<div class="config-campos"></div>`;
    const grid = panel.querySelector(".config-campos");

    for (const c of s.campos) {
      // Campo calculado de solo lectura (ej. URL completa del webhook).
      if (c.tipo === "derivado") {
        const wrap = document.createElement("div");
        wrap.className = "field config-campo config-campo--derivado";
        const enlace = c.enlace
          ? `<p class="field__hint config-webhook-meta">Si esta URL cambió, actualízala también en Meta: ` +
            `<a href="${c.enlace.url}" target="_blank" rel="noopener">${escaparHtml(c.enlace.texto)} ` +
            `<i class="fa-solid fa-arrow-up-right-from-square"></i></a></p>`
          : "";
        wrap.innerHTML =
          `<label>${escaparHtml(c.etiqueta)}</label>` +
          `<div class="config-secreto">` +
            `<input type="text" class="config-derivado-input" readonly value="">` +
            `<button type="button" class="config-ojo config-copiar" title="Copiar">` +
            `<i class="fa-solid fa-copy"></i></button>` +
          `</div>` +
          (c.ayuda ? `<p class="field__hint">${escaparHtml(c.ayuda)}</p>` : "") +
          enlace;
        grid.appendChild(wrap);

        const salida = wrap.querySelector("input");
        const recomputar = () => {
          const [kBase, kRuta] = c.formula || [];
          salida.value = unirUrl(
            campos[kBase] ? valorActual(kBase) : "",
            campos[kRuta] ? valorActual(kRuta) : ""
          );
        };
        derivados.push(recomputar);

        wrap.querySelector(".config-copiar").addEventListener("click", async () => {
          const txt = salida.value;
          if (!txt) return toast("Todavía no hay URL (falta la URL base o la ruta).", "error");
          try {
            await navigator.clipboard.writeText(txt);
          } catch {
            salida.removeAttribute("readonly");
            salida.select();
            document.execCommand("copy");
            salida.setAttribute("readonly", "");
          }
          toast("URL del webhook copiada.", "ok");
        });
        continue;
      }

      const val = valores[c.clave] ?? "";
      const wrap = document.createElement("div");
      wrap.className = "field config-campo";

      const id = "cf_" + c.clave;
      let control = "";

      if (c.tipo === "select") {
        control =
          `<select id="${id}" data-clave="${c.clave}">` +
          (c.opciones || []).map((o) => `<option value="${o}"${o === val ? " selected" : ""}>${o}</option>`).join("") +
          `</select>`;
      } else if (c.tipo === "bool") {
        const on = String(val).toLowerCase() === "true";
        control =
          `<label class="config-switch"><input type="checkbox" id="${id}" data-clave="${c.clave}"${on ? " checked" : ""}>` +
          `<span>Activado</span></label>`;
      } else if (c.tipo === "password") {
        control =
          `<div class="config-secreto">` +
          `<input type="password" id="${id}" data-clave="${c.clave}" value="${escaparHtml(val)}" autocomplete="off" spellcheck="false">` +
          `<button type="button" class="config-ojo" aria-label="Mostrar u ocultar"><i class="fa-solid fa-eye"></i></button>` +
          `</div>`;
      } else {
        const t = c.tipo === "number" ? "number" : "text";
        control = `<input type="${t}" id="${id}" data-clave="${c.clave}" value="${escaparHtml(val)}" autocomplete="off" spellcheck="false">`;
      }

      wrap.innerHTML =
        `<label for="${id}">${escaparHtml(c.etiqueta)} <span class="config-clave">${c.clave}</span></label>` +
        control +
        (c.ayuda ? `<p class="field__hint">${escaparHtml(c.ayuda)}</p>` : "");
      grid.appendChild(wrap);

      const input = wrap.querySelector("[data-clave]");
      campos[c.clave] = { def: c, input };
      input.addEventListener("input", marcarCambios);
      input.addEventListener("change", marcarCambios);

      const ojo = wrap.querySelector(".config-ojo");
      if (ojo) {
        ojo.addEventListener("click", () => {
          const esPwd = input.type === "password";
          input.type = esPwd ? "text" : "password";
          ojo.querySelector("i").className = esPwd ? "fa-solid fa-eye-slash" : "fa-solid fa-eye";
        });
      }
    }
    cont.appendChild(panel);
  }
  marcarCambios();
}

function valorActual(clave) {
  const { input } = campos[clave];
  if (input.type === "checkbox") return input.checked ? "true" : "false";
  return input.value;
}

function diff() {
  const cambios = {};
  for (const clave of Object.keys(campos)) {
    const nuevo = String(valorActual(clave));
    const viejo = String(valores[clave] ?? "");
    if (nuevo.trim() !== viejo.trim()) cambios[clave] = nuevo.trim();
  }
  return cambios;
}

function marcarCambios() {
  derivados.forEach((fn) => fn());
  const cambios = diff();
  const n = Object.keys(cambios).length;

  for (const clave of Object.keys(campos)) {
    campos[clave].input.closest(".config-campo").classList.toggle("config-campo--cambiado", clave in cambios);
  }

  const hay = n > 0;
  $("#btnGuardar").disabled = !hay;
  $("#barraGuardar").hidden = !hay;
  $("#cambiosResumen").textContent = hay
    ? `${n} cambio${n === 1 ? "" : "s"} sin guardar: ${Object.keys(cambios).join(", ")}`
    : "Sin cambios";
}

async function guardar() {
  const cambios = diff();
  if (!Object.keys(cambios).length) return;

  const botones = [$("#btnGuardar"), $("#btnGuardarFijo")];
  botones.forEach((b) => (b.disabled = true));
  try {
    const res = await fetch("api/configuracion/todo", {
      method: "PUT",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ cambios }),
    });
    const d = await res.json().catch(() => ({}));
    if (res.status === 401) { window.snwSalir(); return; }
    if (!res.ok) throw new Error(d.detail || "No se pudo guardar");
    valores = d.valores || valores;

    // Reflejar los valores normalizados por el servidor.
    for (const clave of Object.keys(campos)) {
      const { input } = campos[clave];
      const v = valores[clave] ?? "";
      if (input.type === "checkbox") input.checked = String(v).toLowerCase() === "true";
      else input.value = v;
    }
    marcarCambios();
    toast(
      "entorno" in cambios
        ? "Configuración guardada. Recarga las demás páginas para aplicar el cambio de entorno."
        : "Configuración guardada.",
      "ok"
    );
  } catch (err) {
    toast(err.message || "No se pudo guardar la configuración.", "error");
  } finally {
    botones.forEach((b) => (b.disabled = false));
    marcarCambios();
  }
}

function descartar() {
  for (const clave of Object.keys(campos)) {
    const { input } = campos[clave];
    const v = valores[clave] ?? "";
    if (input.type === "checkbox") input.checked = String(v).toLowerCase() === "true";
    else input.value = v;
  }
  marcarCambios();
}

$("#btnGuardar").addEventListener("click", guardar);
$("#btnGuardarFijo").addEventListener("click", guardar);
$("#btnDescartar").addEventListener("click", descartar);

window.addEventListener("beforeunload", (e) => {
  if (Object.keys(diff()).length) { e.preventDefault(); e.returnValue = ""; }
});

cargar();
