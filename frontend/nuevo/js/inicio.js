/* Dashboard de Inicio (nuevo frontend). Compone endpoints existentes; no
   agrega contratos al backend. La sidebar/armazón vien de layout.js. */
(function () {
  "use strict";
  const token = localStorage.getItem("snw_token");
  const puede = (p) => (typeof window.snwPuede === "function" ? window.snwPuede(p) : true);
  const ES_PRIV = () => window.snwEsPrivilegiado === true;
  const auth = () => ({ Authorization: "Bearer " + (token || "") });
  const esc = (t) => { const d = document.createElement("div"); d.textContent = t ?? ""; return d.innerHTML; };
  const sesionExpirada = () => window.snwSesionExpirada ? window.snwSesionExpirada() : location.replace("/login.html");
  let toastTimer;
  const toast = (msg, tipo = "ok") => {
    const el = document.getElementById("toast");
    if (!el) return;
    clearTimeout(toastTimer);
    el.textContent = msg;
    el.className = "toast visible" + (tipo === "error" ? " toast--error" : "");
    if (tipo === "ok") toastTimer = setTimeout(() => el.classList.remove("visible"), 3200);
  };
  const nfmt = (n) => Number(n || 0).toLocaleString("es-CL");

  // Saludo
  const nombre = localStorage.getItem("snw_nombre") || "";
  const saludo = document.getElementById("saludo");
  if (saludo) saludo.textContent = nombre ? `Hola, ${nombre}` : "Resumen operativo";

  const cuerpo = (id, html) => { const el = document.getElementById(id); if (el) el.innerHTML = html; };
  const cargando = (id) => cuerpo(id, '<div class="cargando"><span class="rueda"></span>Cargando…</div>');
  const vacio = (id, txt) => cuerpo(id, `<div class="vacio">${esc(txt)}</div>`);
  const fallo = (id) => cuerpo(id, '<div class="vacio">No se pudo cargar.</div>');

  // Estados → badge (texto + color).
  const TONOS = { en_proceso: "info", pausado: "warn", pendiente: "warn", aprobado: "ok", enviando: "info", enviado: "ok", completado: "ok", cancelado: "neutral", rechazado: "danger", error: "danger" };
  const ETIQ = { en_proceso: "En curso", pausado: "Pausado", pendiente: "Pendiente", aprobado: "Aprobado", enviando: "Enviando", enviado: "Enviado", completado: "Completado", cancelado: "Cancelado", rechazado: "Rechazado", error: "Fallido" };
  const badge = (e) => {
    const k = e || "";
    return `<span class="badge badge--${TONOS[k] || "neutral"}">${esc(ETIQ[k] || "—")}</span>`;
  };
  const nombreTpl = (p) => (typeof p === "string" ? p : (p && (p.nombre || p.plantilla)) || "—");
  const fmtFecha = (v) => {
    if (!v) return "";
    const d = typeof v === "number" ? new Date(v) : new Date(String(v).replace(" ", "T"));
    if (isNaN(d)) return "";
    return d.toLocaleString("es-CL", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
  };

  async function getJSON(url) {
    const r = await fetch(url, { headers: auth(), cache: "no-store" });
    if (r.status === 401) { sesionExpirada(); return null; }
    if (!r.ok) throw new Error();
    return r.json();
  }

  async function cargarEnvios() {
    if (!puede("mensajeria")) { vacio("enviosCurso", "Sin permiso para ver envíos."); return; }
    cargando("enviosCurso");
    try {
      const items = await getJSON("/api/notificaciones/envios-en-progreso");
      if (items === null) return;
      if (!items.length) { vacio("enviosCurso", "Sin envíos en curso."); return; }
      cuerpo("enviosCurso", items.slice(0, 6).map((it) =>
        `<div class="tabla-wrap"><table class="tabla"><tbody>` +
        `<tr><td><strong>${esc(nombreTpl(it.plantilla))}</strong><br><span class="muted" style="font-size:.78rem">${esc(it.area || it.base || "")}</span></td>` +
        `<td class="num" style="width:1%">${badge(it.estado)}<br><span class="muted" style="font-size:.78rem">${nfmt(it.enviados)}/${nfmt(it.total)}</span></td></tr>` +
        `</tbody></table></div>`
      ).join(""));
    } catch (e) { fallo("enviosCurso"); }
  }

  async function cargarProgramados() {
    if (!puede("mensajeria")) { vacio("programados", "Sin permiso."); return; }
    cargando("programados");
    try {
      const items = await getJSON("/api/notificaciones/programados");
      if (items === null) return;
      const vivos = items.filter((p) => ["pendiente", "aprobado"].indexOf(p.estado) !== -1);
      if (!vivos.length) { vacio("programados", "Sin programados próximos."); return; }
      cuerpo("programados", vivos.slice(0, 6).map((p) =>
        `<div class="tabla-wrap"><table class="tabla"><tbody>` +
        `<tr><td><strong>${esc(p.plantilla || "—")}</strong><br><span class="muted" style="font-size:.78rem">${esc(fmtFecha(p.programado_para))}</span></td>` +
        `<td class="num" style="width:1%">${badge(p.estado)}</td></tr>` +
        `</tbody></table></div>`
      ).join(""));
    } catch (e) { fallo("programados"); }
  }

  async function cargarHistorial() {
    if (!puede("historial")) { vacio("historial", "Sin permiso."); return; }
    cargando("historial");
    try {
      const q = new URLSearchParams({ ambiente: "todos", origen: "todos", q: "", limite: "5", pagina: "1", incluir_total: "false" });
      const data = await getJSON("/api/notificaciones/historial/pagina?" + q);
      if (data === null) return;
      const regs = data.registros || [];
      if (!regs.length) { vacio("historial", "Aún no hay envíos registrados."); return; }
      cuerpo("historial", regs.slice(0, 6).map((h) =>
        `<div class="tabla-wrap"><table class="tabla"><tbody>` +
        `<tr><td><strong>${esc(h.plantilla_nombre || "—")}</strong><br><span class="muted" style="font-size:.78rem">${esc(fmtFecha(h.fecha))}</span></td>` +
        `<td class="num" style="width:1%">${badge(h.estado)}<br><span class="muted" style="font-size:.78rem">${nfmt(h.enviados)}/${nfmt(h.total_pacientes)}</span></td></tr>` +
        `</tbody></table></div>`
      ).join(""));
    } catch (e) { fallo("historial"); }
  }

  function pintarAcciones() {
    const a = [];
    if (puede("mensajeria")) {
      a.push(["/mensajeria.html?tab=envios", "fa-paper-plane", "Nuevo envío"]);
      a.push(["/mensajeria.html?tab=programados", "fa-calendar-check", "Programar envío"]);
      a.push(["/pacientes.html", "fa-database", "Importar CSV"]);
    }
    if (puede("historial")) a.push(["/historial.html", "fa-clock-rotate-left", "Ver historial"]);
    if (ES_PRIV()) a.push(["/estadisticas.html", "fa-chart-column", "Estadísticas"]);
    cuerpo("acciones", a.map((x) =>
      `<a class="btn btn--ghost" href="${x[0]}"><i class="fa-solid ${x[1]}"></i>${esc(x[2])}</a>`
    ).join(""));
  }

  // Franja de estadísticas (solo admin/dev).
  async function cargarStatsAdmin() {
    if (!ES_PRIV()) return;
    const el = document.getElementById("statsAdmin");
    if (!el) return;
    try {
      const d = await getJSON("/api/estadisticas");
      if (!d) return;
      el.hidden = false;
      const porResp = d.pacientes_por_respuesta || {};
      const porInteres = d.pacientes_por_interes || {};
      el.innerHTML =
        `<div class="stat stat--info"><div class="stat__label">Enviados este mes</div><div class="stat__valor">${nfmt(d.enviados_mes)}</div></div>` +
        `<div class="stat stat--neutral"><div class="stat__label">Aún no responden</div><div class="stat__valor">${nfmt(porResp.pendiente)}</div></div>` +
        `<div class="stat stat--ok"><div class="stat__label">Interesados</div><div class="stat__valor">${nfmt(porInteres.interesado)}</div></div>` +
        `<div class="stat stat--warn"><div class="stat__label">No interesados</div><div class="stat__valor">${nfmt(porInteres.no_interesado)}</div></div>` +
        `<div class="stat stat--danger"><div class="stat__label">Bajas</div><div class="stat__valor">${nfmt(porResp.baja)}</div></div>`;
    } catch (e) { /* franja opcional */ }
  }

  cargarEnvios();
  cargarProgramados();
  cargarHistorial();
  cargarStatsAdmin();
  pintarAcciones();
})();
