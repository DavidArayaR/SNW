/* Mensajería (frontend nuevo). Reutiliza el armazón de layout.js y app.css.
   Núcleo: lista de plantillas con búsqueda/selección, editor, pestañas,
   base para envío y listado de programados. Rutas absolutas /api/… */
(function () {
  "use strict";
  const token = localStorage.getItem("snw_token");
  const puede = (p) => (typeof window.snwPuede === "function" ? window.snwPuede(p) : true);
  const auth = () => ({ Authorization: "Bearer " + (token || "") });
  const esc = (t) => { const d = document.createElement("div"); d.textContent = t ?? ""; return d.innerHTML; };
  const nfmt = (n) => Number(n || 0).toLocaleString("es-CL");
  const $ = (s) => document.querySelector(s);

  let toastTimer;
  const toast = (msg, tipo = "ok") => {
    const el = $("#toast");
    if (!el) return;
    clearTimeout(toastTimer);
    el.textContent = msg;
    el.className = "toast visible" + (tipo === "error" ? " toast--error" : "");
    if (tipo === "ok") toastTimer = setTimeout(() => el.classList.remove("visible"), 3200);
  };
  $("#toast")?.addEventListener("click", (e) => e.currentTarget.classList.remove("visible"));

  async function getJSON(url) {
    const r = await fetch(url, { headers: auth(), cache: "no-store" });
    if (r.status === 401) { window.snwSesionExpirada(); return null; }
    if (!r.status || r.status >= 500) throw new Error();
    if (!r.ok) return { __error: r.status };
    return r.json();
  }

  /* ---------- Estados ---------- */
  const ESTADOS = {
    APPROVED: ["Aprobada", "ok"], PENDING: ["Pendiente", "warn"],
    REJECTED: ["Rechazada", "danger"], PAUSED: ["Pausada", "warn"],
    DISABLED: ["Deshabilitada", "danger"],
  };
  const badgeEstado = (s) => {
    const [txt, tono] = ESTADOS[s] || ["Sin consultar", "neutral"];
    return `<span class="badge badge--${tono}">${esc(txt)}</span>`;
  };

  /* ---------- Pestañas ---------- */
  const mtabs = $("#mtabs");
  function posicionarTabs() {
    const activa = mtabs?.querySelector("button.activo");
    if (!activa) return;
    const rb = mtabs.getBoundingClientRect(), ra = activa.getBoundingClientRect();
    mtabs.style.setProperty("--tab-x", `${ra.left - rb.left - mtabs.clientLeft}px`);
    mtabs.style.setProperty("--tab-w", `${ra.width}px`);
  }
  function activarTab(id) {
    if (!mtabs) return;
    mtabs.querySelectorAll("button").forEach((b) => {
      const on = b.dataset.mtab === id;
      b.classList.toggle("activo", on);
      b.setAttribute("aria-selected", String(on));
      b.tabIndex = on ? 0 : -1;
    });
    ["plantillas", "envios", "programados"].forEach((k) => {
      const v = $("#vista" + k.charAt(0).toUpperCase() + k.slice(1));
      if (v) v.hidden = k !== id;
    });
    posicionarTabs();
    if (id === "programados") cargarProgramados();
  }
  mtabs?.querySelectorAll("button").forEach((b) =>
    b.addEventListener("click", () => activarTab(b.dataset.mtab)));
  window.addEventListener("resize", posicionarTabs);

  /* ---------- Lista de plantillas ---------- */
  let plantillas = [];
  let selId = null;
  let filtro = "";

  function pintarLista() {
    const cont = $("#listaPlantillas");
    if (!cont) return;
    const visibles = plantillas
      .filter((p) => !filtro || (p.nombre || "").toLowerCase().includes(filtro))
      .sort((a, b) => (a.nombre || "").localeCompare(b.nombre || ""));
    if (!visibles.length) {
      cont.innerHTML = `<div class="vacio">${filtro ? "Sin resultados." : "No hay plantillas."}</div>`;
      return;
    }
    cont.innerHTML = visibles.map((p) =>
      `<button type="button" class="lista-item${p.id === selId ? " active" : ""}" data-id="${p.id}">` +
        `<span class="lista-item__main">` +
          `<span class="lista-item__nombre">${esc(p.nombre || "(sin nombre)")}</span>` +
          `<span class="lista-item__meta">${esc(areaNombre(p.area_id))}${p.whatsapp_template ? " · " + esc(p.whatsapp_template) : ""}</span>` +
        `</span>` +
        `<span class="lista-item__der">${badgeEstado(p.whatsapp_template_status)}</span>` +
      `</button>`
    ).join("");
    cont.querySelectorAll(".lista-item").forEach((b) =>
      b.addEventListener("click", () => seleccionar(Number(b.dataset.id))));
  }

  let areas = [];
  const areaNombre = (id) => {
    if (id == null) return "Global";
    const a = areas.find((x) => x.id === id);
    return a ? a.nombre_visible : "—";
  };

  function seleccionar(id) {
    selId = id;
    const p = plantillas.find((x) => x.id === id);
    pintarLista();
    pintarEditor(p);
    pintarEnvio(p);
  }

  function pintarEditor(p) {
    const vacio = $("#editorVacio"), cuerpo = $("#editorCuerpo");
    if (!p) {
      $("#editorTitulo").textContent = "Selecciona una plantilla";
      $("#editorEstados").innerHTML = "";
      if (vacio) vacio.hidden = false;
      if (cuerpo) cuerpo.hidden = true;
      return;
    }
    $("#editorTitulo").textContent = p.nombre || "Plantilla";
    $("#editorEstados").innerHTML = badgeEstado(p.whatsapp_template_status);
    if (vacio) vacio.hidden = true;
    if (cuerpo) cuerpo.hidden = false;
    $("#edNombre").value = p.nombre || "";
    $("#edArea").value = areaNombre(p.area_id);
    $("#edMensaje").textContent = p.texto || "(sin contenido)";
    const meta = [];
    if (p.whatsapp_template) meta.push(["Template Meta", p.whatsapp_template]);
    if (p.whatsapp_template_lang) meta.push(["Idioma", p.whatsapp_template_lang]);
    if (p.whatsapp_template_categoria) meta.push(["Categoría", p.whatsapp_template_categoria]);
    meta.push(["Última edición", p.actualizada ? new Date(p.actualizada).toLocaleString("es-CL") : "—"]);
    $("#edMeta").innerHTML = meta.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("");
    const acc = $("#editorAcciones");
    const editable = puede("plantillas_editar");
    acc.innerHTML =
      `<a class="btn btn--ghost" href="/mensajeria.html?tab=plantillas"><i class="fa-solid fa-pen"></i> Editar</a>` +
      (editable ? `<button type="button" class="btn btn--ghost" id="edEliminar"><i class="fa-solid fa-trash"></i> Eliminar</button>` : "");
    $("#edEliminar")?.addEventListener("click", () =>
      toast("La eliminación se realiza en la vista completa por ahora.", "error"));
  }

  function pintarEnvio(p) {
    const vacio = $("#envioVacio"), cuerpo = $("#envioCuerpo");
    if (!p) {
      if (vacio) vacio.hidden = false;
      if (cuerpo) cuerpo.hidden = true;
      return;
    }
    if (vacio) vacio.hidden = true;
    if (cuerpo) cuerpo.hidden = false;
    $("#envioPlantilla").textContent = p.nombre || "—";
  }

  async function cargarPlantillas() {
    const cont = $("#listaPlantillas");
    if (cont) cont.innerHTML = '<div class="cargando"><span class="rueda"></span>Cargando…</div>';
    try {
      const d = await getJSON("/api/plantillas");
      if (d === null) return;
      if (d && d.__error) throw new Error();
      plantillas = (Array.isArray(d) ? d : []).filter((p) => !p.especial);
      if (selId == null && plantillas.length) selId = plantillas[0].id;
      pintarLista();
      pintarEditor(plantillas.find((x) => x.id === selId) || null);
      pintarEnvio(plantillas.find((x) => x.id === selId) || null);
    } catch (e) {
      if (cont) cont.innerHTML = '<div class="vacio">No se pudieron cargar las plantillas.</div>';
    }
  }

  async function cargarAreas() {
    try {
      const d = await getJSON("/api/areas/mias");
      areas = (Array.isArray(d) ? d : []);
    } catch (e) { areas = []; }
    const sel = $("#envioBase");
    if (!sel) return;
    sel.innerHTML =
      `<option value="desarrollo">Desarrollo</option>` +
      `<option value="produccion">Producción · todas las áreas</option>` +
      areas.map((a) => `<option value="${a.id}">${esc(a.nombre_visible)}</option>`).join("");
  }

  /* ---------- Programados ---------- */
  const TONOS_PROG = { pendiente: "warn", aprobado: "info", enviando: "info", enviado: "ok", rechazado: "danger", cancelado: "neutral", error: "danger" };
  const ETIQ_PROG = { pendiente: "Pendiente", aprobado: "Aprobado", enviando: "Enviando", enviado: "Enviado", rechazado: "Rechazado", cancelado: "Cancelado", error: "Error" };
  async function cargarProgramados() {
    const cont = $("#listaProgramados");
    if (!cont) return;
    cont.innerHTML = '<div class="cargando"><span class="rueda"></span>Cargando…</div>';
    try {
      const d = await getJSON("/api/notificaciones/programados");
      if (d === null) return;
      if (d && d.__error) throw new Error();
      const items = Array.isArray(d) ? d : [];
      if (!items.length) {
        cont.innerHTML = '<div class="vacio">No hay envíos programados.</div>';
        return;
      }
      cont.innerHTML = items.map((p) =>
        `<div class="lista-item" style="cursor:default">` +
          `<span class="lista-item__main">` +
            `<span class="lista-item__nombre">${esc(p.plantilla || "—")}</span>` +
            `<span class="lista-item__meta">${esc((p.programado_para || "").replace(" ", " · "))} · ${esc(p.area || p.tabla || "")}</span>` +
          `</span>` +
          `<span class="lista-item__der"><span class="badge badge--${TONOS_PROG[p.estado] || "neutral"}">${esc(ETIQ_PROG[p.estado] || p.estado || "—")}</span></span>` +
        `</div>`
      ).join("");
    } catch (e) {
      cont.innerHTML = '<div class="vacio">No se pudieron cargar los programados.</div>';
    }
  }

  /* ---------- Acciones varias ---------- */
  $("#buscarTpl")?.addEventListener("input", (e) => { filtro = e.target.value.trim().toLowerCase(); pintarLista(); });
  $("#btnNueva")?.addEventListener("click", () => toast("La creación de plantillas se realiza en la vista completa por ahora.", "error"));
  $("#progActualizar")?.addEventListener("click", () => { cargarProgramados(); toast("Programados actualizados."); });
  $("#envioLimpiar")?.addEventListener("click", () => { const l = $("#envioLimite"); if (l) l.value = ""; });
  $("#envioIniciar")?.addEventListener("click", () =>
    toast("El flujo completo de envío se conecta en la versión final de esta vista.", "error"));

  cargarPlantillas();
  cargarAreas();
  requestAnimationFrame(posicionarTabs);
})();
