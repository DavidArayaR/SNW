/* Dashboard operativo de Inicio (solo con sesión). Compone endpoints
   existentes; no agrega contratos nuevos al backend. Ver DESIGN.md §9. */
(function () {
  "use strict";
  const token = localStorage.getItem("snw_token");
  const hero = document.getElementById("heroPublico");
  const portada = document.getElementById("portadaPublica");
  const dash = document.getElementById("dashboardOperativo");
  if (!token || !dash) return; // sin sesión: la portada pública queda visible

  // Con sesión, la portada comercial se oculta y entra el panel operativo.
  if (hero) hero.hidden = true;
  if (portada) portada.hidden = true;
  dash.hidden = false;
  document.body.classList.add("inicio-sesion");

  const perm = (p) => (typeof window.snwPuede === "function" ? window.snwPuede(p) : true);
  const esPriv = () => window.snwEsPrivilegiado === true;
  const auth = { Authorization: "Bearer " + token };
  const esc = (t) => { const d = document.createElement("div"); d.textContent = t == null ? "" : String(t); return d.innerHTML; };
  const nfmt = (n) => Number(n || 0).toLocaleString("es-CL");

  const saludo = document.getElementById("dashSaludo");
  if (saludo) {
    const nombre = localStorage.getItem("snw_nombre") || "";
    saludo.textContent = nombre ? "Hola, " + nombre : "Resumen operativo";
  }

  const cuerpo = (id, html) => { const el = document.getElementById(id); if (el) el.innerHTML = html; };
  const cargando = (id) => cuerpo(id, '<span class="dash-vacio">Cargando…</span>');
  const vacio = (id, txt) => cuerpo(id, '<span class="dash-vacio">' + esc(txt) + "</span>");
  const fallo = (id) => cuerpo(id, '<span class="dash-vacio">No se pudo cargar la información.</span>');

  // Estados explícitos: etiqueta textual + color semántico (tokens).
  const TONOS = { en_proceso: "info", pausado: "warn", pendiente: "warn", aprobado: "ok", enviando: "info", enviado: "ok", completado: "ok", cancelado: "neutral", rechazado: "danger", error: "danger" };
  const ETIQUETAS = { en_proceso: "En curso", pausado: "Pausado", pendiente: "Pendiente", aprobado: "Aprobado", enviando: "Enviando", enviado: "Enviado", completado: "Completado", cancelado: "Cancelado", rechazado: "Rechazado", error: "Fallido" };
  const badgeEstado = (e) => {
    const key = e || "";
    const tono = TONOS[key] || "neutral";
    const txt = ETIQUETAS[key] || "—";
    return '<span class="estado-badge dash-tono--' + tono + '">' + esc(txt) + "</span>";
  };

  const nombrePlantilla = (p) => (typeof p === "string" ? p : (p && (p.nombre || p.plantilla)) || "—");

  const fmtFecha = (v) => {
    if (!v) return "";
    const d = typeof v === "number" ? new Date(v) : new Date(String(v).replace(" ", "T"));
    if (isNaN(d)) return "";
    return d.toLocaleString("es-CL", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
  };

  async function cargarEnvios() {
    if (!perm("mensajeria")) { vacio("dashEnvios", "Sin permiso para ver envíos."); return; }
    cargando("dashEnvios");
    try {
      const r = await fetch("api/notificaciones/envios-en-progreso", { headers: auth, cache: "no-store" });
      if (r.status === 401) { window.snwSesionExpirada(); return; }
      if (!r.ok) throw new Error();
      const items = await r.json();
      if (!items.length) { vacio("dashEnvios", "Sin envíos en curso."); return; }
      cuerpo("dashEnvios", items.slice(0, 5).map((it) =>
        '<div class="dash-item">' +
          '<span class="dash-item__main">' + esc(nombrePlantilla(it.plantilla)) +
            '<span class="dash-item__meta">' + esc(it.area || it.base || "") + "</span></span>" +
          '<span class="dash-item__der">' + badgeEstado(it.estado) +
            '<span class="dash-item__meta">' + nfmt(it.enviados) + "/" + nfmt(it.total) + "</span></span>" +
        "</div>"
      ).join(""));
    } catch (e) { fallo("dashEnvios"); }
  }

  async function cargarProgramados() {
    if (!perm("mensajeria")) { vacio("dashProgramados", "Sin permiso."); return; }
    cargando("dashProgramados");
    try {
      const r = await fetch("api/notificaciones/programados", { headers: auth, cache: "no-store" });
      if (r.status === 401) { window.snwSesionExpirada(); return; }
      if (!r.ok) throw new Error();
      const items = await r.json();
      const vivos = items.filter((p) => ["pendiente", "aprobado"].indexOf(p.estado) !== -1);
      if (!vivos.length) { vacio("dashProgramados", "Sin programados próximos."); return; }
      cuerpo("dashProgramados", vivos.slice(0, 5).map((p) =>
        '<div class="dash-item">' +
          '<span class="dash-item__main">' + esc(p.plantilla || "—") +
            '<span class="dash-item__meta">' + esc(fmtFecha(p.programado_para)) + "</span></span>" +
          badgeEstado(p.estado) +
        "</div>"
      ).join(""));
    } catch (e) { fallo("dashProgramados"); }
  }

  async function cargarHistorial() {
    if (!perm("historial")) { vacio("dashHistorial", "Sin permiso."); return; }
    cargando("dashHistorial");
    try {
      const params = new URLSearchParams({ ambiente: "todos", origen: "todos", q: "", limite: "5", pagina: "1", incluir_total: "false" });
      const r = await fetch("api/notificaciones/historial/pagina?" + params, { headers: auth, cache: "no-store" });
      if (r.status === 401) { window.snwSesionExpirada(); return; }
      if (!r.ok) throw new Error();
      const data = await r.json();
      const regs = (data && data.registros) || [];
      if (!regs.length) { vacio("dashHistorial", "Aún no hay envíos registrados."); return; }
      cuerpo("dashHistorial", regs.slice(0, 5).map((h) =>
        '<div class="dash-item">' +
          '<span class="dash-item__main">' + esc(h.plantilla_nombre || "—") +
            '<span class="dash-item__meta">' + esc(fmtFecha(h.fecha)) + "</span></span>" +
          '<span class="dash-item__der">' + badgeEstado(h.estado) +
            '<span class="dash-item__meta">' + nfmt(h.enviados) + "/" + nfmt(h.total_pacientes) + "</span></span>" +
        "</div>"
      ).join(""));
    } catch (e) { fallo("dashHistorial"); }
  }

  function pintarAcciones() {
    const acciones = [];
    if (perm("mensajeria")) {
      acciones.push(["mensajeria.html?tab=envios", "fa-paper-plane", "Nuevo envío"]);
      acciones.push(["mensajeria.html?tab=programados", "fa-calendar-check", "Programar envío"]);
      acciones.push(["mensajeria.html?tab=plantillas", "fa-file-lines", "Administrar plantillas"]);
      acciones.push(["pacientes.html", "fa-database", "Importar CSV"]);
    }
    if (perm("historial")) acciones.push(["historial.html", "fa-clock-rotate-left", "Ver historial"]);
    if (esPriv()) acciones.push(["estadisticas.html", "fa-chart-column", "Estadísticas"]);
    cuerpo("dashAcciones", acciones.map((a) =>
      '<a class="btn btn--ghost dash-accion" href="' + a[0] + '"><i class="fa-solid ' + a[1] + '"></i>' + esc(a[2]) + "</a>"
    ).join(""));
  }

  // Franja de estadísticas (solo admin/dev): 5 métricas del estado de pacientes.
  async function cargarStatsAdmin() {
    if (!esPriv()) return;
    const el = document.getElementById("statsAdmin");
    if (!el) return;
    try {
      const r = await fetch("api/estadisticas", { headers: auth, cache: "no-store" });
      if (r.status === 401) { window.snwSesionExpirada(); return; }
      if (!r.ok) throw new Error();
      const d = await r.json();
      const porResp = d.pacientes_por_respuesta || {};
      const porInteres = d.pacientes_por_interes || {};
      el.hidden = false;
      el.innerHTML =
        '<div class="dash-stat dash-stat--info"><span class="dash-stat__label">Enviados este mes</span><span class="dash-stat__valor">' + nfmt(d.enviados_mes) + '</span></div>' +
        '<div class="dash-stat dash-stat--neutral"><span class="dash-stat__label">Aún no responden</span><span class="dash-stat__valor">' + nfmt(porResp.pendiente) + '</span></div>' +
        '<div class="dash-stat dash-stat--ok"><span class="dash-stat__label">Interesados</span><span class="dash-stat__valor">' + nfmt(porInteres.interesado) + '</span></div>' +
        '<div class="dash-stat dash-stat--warn"><span class="dash-stat__label">No interesados</span><span class="dash-stat__valor">' + nfmt(porInteres.no_interesado) + '</span></div>' +
        '<div class="dash-stat dash-stat--danger"><span class="dash-stat__label">Bajas</span><span class="dash-stat__valor">' + nfmt(porResp.baja) + '</span></div>';
    } catch (e) { /* franja opcional */ }
  }

  // Carga inicial en paralelo; cada panel maneja su error de forma aislada.
  cargarEnvios();
  cargarProgramados();
  cargarHistorial();
  cargarStatsAdmin();
  pintarAcciones();
})();
