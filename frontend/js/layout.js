/* ============================================================
   Barra lateral común a todas las páginas de la app.
   Construye el <nav>, marca el enlace activo, controla el acceso
   por rol/permiso y el cierre de sesión. Se carga ANTES del JS de
   cada página (app.js, pacientes.js, ...) porque define window.snwSalir
   y window.snwPuede.
   ============================================================ */
(function () {
  const token = localStorage.getItem("snw_token");
  const rol = localStorage.getItem("snw_rol") || "";
  const ES_DEV = rol === "desarrollador";
  const ES_PRIV = rol === "administrador" || ES_DEV;
  const PAGINA = document.body.dataset.pagina || "";

  let PERMISOS = [];
  try { PERMISOS = JSON.parse(localStorage.getItem("snw_permisos") || "[]"); } catch (e) { PERMISOS = []; }
  if (!Array.isArray(PERMISOS)) PERMISOS = [];

  // Un rol privilegiado (administrador / desarrollador) tiene todos los permisos.
  function puede(perm) { return ES_PRIV || PERMISOS.indexOf(perm) !== -1; }
  window.snwPuede = puede;
  window.snwRol = rol;
  window.snwEsPrivilegiado = ES_PRIV;
  window.snwEsDev = ES_DEV;

  window.snwSalir = function () {
    ["snw_token", "snw_rol", "snw_nombre", "snw_permisos", "snw_ambiente_admin", "snw_ambiente"]
      .forEach((k) => localStorage.removeItem(k));
    location.replace("login.html");
  };

  // La portada (index) es pública: sin sesión se muestra sin sidebar.
  if (!token) {
    if (PAGINA === "inicio") {
      const sh = document.querySelector(".app-shell");
      if (sh) sh.classList.add("sin-sidebar");
      if (window.snwMontarFabTema) window.snwMontarFabTema();
      return;
    }
    location.replace("login.html");
    return;
  }

  // Permiso que exige cada página; si no lo tiene, se le manda a la primera
  // página que sí pueda ver.
  const PERM_PAGINA = {
    pacientes: "pacientes",
    mensajeria: "mensajeria",
    historial: "historial",
    estadisticas: "estadisticas",
  };
  const ORDEN_PAGINAS = [
    ["mensajeria", "mensajeria.html"],
    ["historial", "historial.html"],
    ["estadisticas", "estadisticas.html"],
    ["pacientes", "pacientes.html"],
  ];
  function primeraPaginaPermitida() {
    for (const [perm, href] of ORDEN_PAGINAS) if (puede(perm)) return href;
    if (ES_DEV) return "configuracion.html";
    return "index.html";
  }
  // Páginas exclusivas del rol desarrollador / privilegiado.
  if (PAGINA === "configuracion" && !ES_DEV) {
    location.replace(primeraPaginaPermitida());
    return;
  }
  if (PAGINA === "usuarios" && !ES_PRIV) {
    location.replace(primeraPaginaPermitida());
    return;
  }
  if (PERM_PAGINA[PAGINA] && !puede(PERM_PAGINA[PAGINA])) {
    location.replace(primeraPaginaPermitida());
    return;
  }

  // Con sesión activa, la portada oculta los botones de acceso/registro.
  ["btnLogin", "btnRegistro", "heroAcciones"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.hidden = true;
  });

  // Estado plegado de la sidebar en escritorio (se recuerda entre sesiones).
  const shellEl = document.querySelector(".app-shell");
  if (shellEl && localStorage.getItem("snw_sidebar_colapsada") === "1") {
    shellEl.classList.add("sidebar-colapsada");
  }

  const LINKS = [
    { pagina: "inicio",       href: "index.html",        icono: "fa-house",             texto: "Inicio" },
    { pagina: "pacientes",    href: "pacientes.html",    icono: "fa-database",          texto: "Base de datos", perm: "pacientes" },
    { pagina: "mensajeria",   href: "mensajeria.html",   icono: "fa-paper-plane",       texto: "Mensajería", perm: "mensajeria" },
    { pagina: "historial",    href: "historial.html",    icono: "fa-clock-rotate-left", texto: "Historial", perm: "historial" },
    { pagina: "estadisticas", href: "estadisticas.html", icono: "fa-chart-column",      texto: "Estadísticas", perm: "estadisticas" },
    { pagina: "configuracion", href: "configuracion.html", icono: "fa-gear",            texto: "Configuración", dev: true },
    { pagina: "usuarios",     href: "usuarios.html",     icono: "fa-users-gear",        texto: "Usuarios", priv: true },
  ];

  const items = LINKS
    .filter((l) => (!l.perm || puede(l.perm)) && (!l.priv || ES_PRIV) && (!l.dev || ES_DEV))
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
      `<button type="button" class="sidebar__tema" id="btnTema" title="Cambiar entre modo claro y oscuro">` +
      `<i class="fa-solid fa-moon"></i><span>Modo oscuro</span></button>` +
      `<button type="button" class="sidebar__salir" id="btnClavePropia" title="Cambiar mi contraseña">` +
      `<i class="fa-solid fa-key"></i><span>Cambiar contraseña</span></button>` +
      `<button type="button" class="sidebar__salir" id="btnSalir" title="Cerrar sesión">` +
      `<i class="fa-solid fa-right-from-bracket"></i><span>Cerrar sesión</span></button>`;

    const btnTema = document.getElementById("btnTema");
    const pintarTema = () => {
      const oscuro = window.snwTemaOscuro && window.snwTemaOscuro();
      btnTema.querySelector("i").className = oscuro ? "fa-solid fa-sun" : "fa-solid fa-moon";
      btnTema.querySelector("span").textContent = oscuro ? "Modo claro" : "Modo oscuro";
    };
    pintarTema();
    btnTema.addEventListener("click", () => {
      if (window.snwToggleTema) window.snwToggleTema();
      pintarTema();
    });

    document.getElementById("btnSalir").addEventListener("click", async () => {
      try {
        await fetch("api/auth/logout", {
          method: "POST", headers: { Authorization: "Bearer " + token },
        });
      } catch (e) { /* la sesión se cierra igual en el cliente */ }
      window.snwSalir();
    });

    document.getElementById("btnClavePropia").addEventListener("click", abrirModalClave);
  }

  // ----- Modal "Cambiar mi contraseña" (disponible en toda la app) -----
  let modalClave = null;
  function abrirModalClave() {
    if (!modalClave) {
      modalClave = document.createElement("div");
      modalClave.className = "modal";
      modalClave.id = "modalClavePropia";
      modalClave.hidden = true;
      modalClave.innerHTML =
        `<div class="modal__card" role="dialog" aria-modal="true">` +
        `<h3>Cambiar mi contraseña</h3>` +
        `<form id="formClavePropia" novalidate>` +
        `<div class="field"><label for="clAct">Contraseña actual</label>` +
        `<input type="password" id="clAct" autocomplete="current-password"></div>` +
        `<div class="field"><label for="clNue">Contraseña nueva</label>` +
        `<input type="password" id="clNue" autocomplete="new-password">` +
        `<p class="field__hint">Mínimo 8 caracteres, con una minúscula, una mayúscula y un número.</p></div>` +
        `<div class="field"><label for="clRep">Repite la contraseña nueva</label>` +
        `<input type="password" id="clRep" autocomplete="new-password"></div>` +
        `<p class="warn" id="clMsg" hidden></p>` +
        `<div class="modal__actions">` +
        `<button type="button" class="btn btn--ghost" id="clCancelar">Cancelar</button>` +
        `<button type="submit" class="btn btn--primary" id="clGuardar">Guardar</button>` +
        `</div></form></div>`;
      document.body.appendChild(modalClave);

      const cerrar = () => {
        modalClave.hidden = true;
        modalClave.querySelector("#formClavePropia").reset();
        modalClave.querySelector("#clMsg").hidden = true;
      };
      modalClave.querySelector("#clCancelar").addEventListener("click", cerrar);
      modalClave.addEventListener("click", (e) => { if (e.target === modalClave) cerrar(); });
      document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !modalClave.hidden) cerrar(); });

      modalClave.querySelector("#formClavePropia").addEventListener("submit", async (e) => {
        e.preventDefault();
        const msg = modalClave.querySelector("#clMsg");
        const act = modalClave.querySelector("#clAct").value;
        const nue = modalClave.querySelector("#clNue").value;
        const rep = modalClave.querySelector("#clRep").value;
        const mostrar = (t) => { msg.textContent = t; msg.hidden = false; };
        if (nue !== rep) return mostrar("Las contraseñas nuevas no coinciden.");
        const btn = modalClave.querySelector("#clGuardar");
        btn.disabled = true;
        try {
          const r = await fetch("api/auth/clave", {
            method: "PUT",
            headers: { Authorization: "Bearer " + token, "Content-Type": "application/json" },
            body: JSON.stringify({ clave_actual: act, clave_nueva: nue }),
          });
          const data = await r.json().catch(() => ({}));
          if (!r.ok) throw new Error(data.detail || "No se pudo cambiar la contraseña.");
          cerrar();
          alert("Contraseña actualizada.");
        } catch (ex) {
          mostrar(ex.message);
        } finally {
          btn.disabled = false;
        }
      });
    }
    modalClave.hidden = false;
    modalClave.querySelector("#clAct").focus();
  }

  // Elementos que dependen de un permiso concreto (data-perm="call_center", ...).
  document.querySelectorAll("[data-perm]").forEach((el) => {
    if (!puede(el.dataset.perm)) el.remove();
  });
  // Elementos reservados a roles privilegiados (administrador / desarrollador).
  if (!ES_PRIV) {
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

  // Refresco silencioso: si el administrador cambió el rol o los permisos de
  // esta cuenta, se actualiza el almacenamiento local y se recarga la página
  // para que el menú y los elementos ocultos reflejen el cambio.
  fetch("api/auth/me", { headers: { Authorization: "Bearer " + token } })
    .then((r) => (r.ok ? r.json() : null))
    .then((me) => {
      if (!me) return;
      const permsSrv = Array.isArray(me.permisos) ? me.permisos.slice().sort().join(",") : "";
      const permsLoc = PERMISOS.slice().sort().join(",");
      if ((me.rol && me.rol !== rol) || permsSrv !== permsLoc) {
        localStorage.setItem("snw_rol", me.rol || rol);
        localStorage.setItem("snw_permisos", JSON.stringify(me.permisos || []));
        if (me.nombre) localStorage.setItem("snw_nombre", me.nombre);
        location.reload();
      }
    })
    .catch(() => { /* sin conexión: se queda con lo que hay */ });
})();
