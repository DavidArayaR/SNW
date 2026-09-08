/* ============================================================
   Barra lateral común a todas las páginas de la app.
   Construye el <nav>, marca el enlace activo, controla el acceso
   por rol y el cierre de sesión. Se carga ANTES del JS de cada
   página (app.js, pacientes.js, ...) porque define window.snwSalir.
   ============================================================ */
(function () {
  const token = localStorage.getItem("snw_token");
  const rol = localStorage.getItem("snw_rol") || "";
  const ES_ADMIN = rol === "administrador";
  const PAGINA = document.body.dataset.pagina || "";

  window.snwSalir = function () {
    ["snw_token", "snw_rol", "snw_nombre", "snw_ambiente_admin", "snw_ambiente"]
      .forEach((k) => localStorage.removeItem(k));
    location.replace("login.html");
  };

  // La portada (index) es pública: sin sesión se muestra sin sidebar.
  if (!token) {
    if (PAGINA === "inicio") {
      const sh = document.querySelector(".app-shell");
      if (sh) sh.classList.add("sin-sidebar");
      return;
    }
    location.replace("login.html");
    return;
  }
  if (PAGINA === "pacientes" && !ES_ADMIN) { location.replace("mensajeria.html"); return; }

  const btnLogin = document.getElementById("btnLogin");
  if (btnLogin) btnLogin.hidden = true;

  // Estado plegado de la sidebar en escritorio (se recuerda entre sesiones).
  const shellEl = document.querySelector(".app-shell");
  if (shellEl && localStorage.getItem("snw_sidebar_colapsada") === "1") {
    shellEl.classList.add("sidebar-colapsada");
  }

  const LINKS = [
    { pagina: "inicio",       href: "index.html",        icono: "fa-house",             texto: "Inicio" },
    { pagina: "pacientes",    href: "pacientes.html",    icono: "fa-database",          texto: "Base de datos", admin: true },
    { pagina: "mensajeria",   href: "mensajeria.html",   icono: "fa-paper-plane",       texto: "Mensajería" },
    { pagina: "historial",    href: "historial.html",    icono: "fa-clock-rotate-left", texto: "Historial" },
    { pagina: "estadisticas", href: "estadisticas.html", icono: "fa-chart-column",      texto: "Estadísticas" },
  ];

  const items = LINKS
    .filter((l) => !l.admin || ES_ADMIN)
    .map((l) =>
      `<li class="nav-item">` +
      `<a class="nav-link${l.pagina === PAGINA ? " active" : ""}" href="${l.href}" title="${l.texto}">` +
      `<i class="fa-solid ${l.icono}"></i><span>${l.texto}</span></a></li>`
    )
    .join("");

  const sidebar = document.getElementById("sidebar");
  if (sidebar) {
    sidebar.innerHTML =
      `<a class="sidebar__brand" href="index.html" title="Notificaciones WhatsApp">` +
      `<i class="fa-brands fa-whatsapp"></i>` +
      `<span><strong>Notificaciones</strong><small>Sistema Notificaciones WhatsApp</small></span></a>` +
      `<button type="button" class="sidebar__plegar" id="btnPlegar" aria-label="Plegar o expandir el menú">` +
      `<i class="fa-solid fa-angles-left"></i></button>` +
      `<ul class="nav flex-column sidebar__nav">${items}</ul>` +
      `<button type="button" class="sidebar__salir" id="btnSalir" title="Cerrar sesión">` +
      `<i class="fa-solid fa-right-from-bracket"></i><span>Cerrar sesión</span></button>`;

    document.getElementById("btnSalir").addEventListener("click", async () => {
      try {
        await fetch("api/auth/logout", {
          method: "POST", headers: { Authorization: "Bearer " + token },
        });
      } catch (e) { /* la sesión se cierra igual en el cliente */ }
      window.snwSalir();
    });
  }

  // Elementos reservados a administradores
  if (!ES_ADMIN) {
    document.querySelectorAll("[data-solo-admin]").forEach((el) => el.remove());
  }

  // ----- Control de la sidebar -----
  const shell = document.querySelector(".app-shell");
  const backdrop = document.querySelector(".sidebar-backdrop");
  const cerrarMovil = () => shell && shell.classList.remove("sidebar-abierta");

  // Escritorio: plegar/expandir a modo icono (se recuerda).
  const btnPlegar = document.getElementById("btnPlegar");
  if (btnPlegar && shell) {
    btnPlegar.addEventListener("click", () => {
      const col = shell.classList.toggle("sidebar-colapsada");
      try { localStorage.setItem("snw_sidebar_colapsada", col ? "1" : "0"); } catch (e) { /* modo privado */ }
    });
  }

  // Móvil: hamburguesa de la barra superior abre/cierra el cajón.
  const toggle = document.getElementById("sidebarToggle");
  if (toggle && shell) {
    toggle.addEventListener("click", (e) => {
      e.stopPropagation();
      shell.classList.toggle("sidebar-abierta");
    });
  }

  if (backdrop) backdrop.addEventListener("click", cerrarMovil);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") cerrarMovil(); });
})();
