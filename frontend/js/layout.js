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

  // Deshabilita un botón y muestra una cuenta regresiva (N, N-1, ... "0s")
  // hasta volver a habilitarlo — el cooldown en sí, reusable para llamarlo
  // en cualquier momento (al hacer clic, o al terminar una operación async
  // que ya se bloqueaba sola mientras corría).
  window.snwCooldownBoton = function (btn, segundos = 5) {
    if (!btn) return;
    const textoOriginal = btn.dataset.snwTextoOriginal ?? (btn.dataset.snwTextoOriginal = btn.textContent);
    btn.disabled = true;
    let restante = segundos;
    btn.textContent = `${textoOriginal} (${restante}s)`;
    const timer = setInterval(() => {
      restante -= 1;
      if (restante <= 0) {
        clearInterval(timer);
        btn.disabled = false;
        btn.textContent = textoOriginal;
      } else {
        btn.textContent = `${textoOriginal} (${restante}s)`;
      }
    }, 1000);
  };

  // Envuelve el click de un botón de acción simple (Actualizar...) con el
  // cooldown de arriba: al hacer clic, llama fn() y deshabilita de una vez.
  // Para botones que ya se bloquean solos mientras dura una operación async
  // (spinner propio), no uses esto — llamá snwCooldownBoton directo en el
  // finally, después de que la operación realmente termine (ver
  // btnRevisarTodos / btnSincronizarMeta en app.js).
  window.snwConCooldown = function (btn, fn, segundos = 5) {
    if (!btn) return;
    btn.addEventListener("click", () => {
      if (btn.disabled) return;
      fn();
      window.snwCooldownBoton(btn, segundos);
    });
  };

  function limpiarSesion() {
    ["snw_token", "snw_rol", "snw_nombre", "snw_permisos", "snw_ambiente_admin", "snw_ambiente",
     "snw_esp_pacientes", "snw_esp_mensajeria", "snw_esp_historial", "snw_base_historial",
     "snw_esp_estadisticas", "snw_modo_conf", "snw_page_size_pac",
     "snw_page_size_usr_envios", "snw_page_size_usr_auditoria"]
      .forEach((k) => localStorage.removeItem(k));
  }

  // Cierre de sesión manual (botón «Cerrar sesión»): vuelve directo al login,
  // que le muestra un toast de confirmación (ver login.html).
  window.snwSalir = function () {
    limpiarSesion();
    location.replace("login.html?salida=ok");
  };

  // Sesión vencida/inválida (token rechazado por el servidor con 401): a
  // diferencia de snwSalir, esto vuelve a la portada (no a la página en la
  // que estaba) y le avisa a la persona que tuvo que volver a entrar.
  window.snwSesionExpirada = function () {
    limpiarSesion();
    location.replace("index.html?sesion=expirada");
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

  // Acaba de iniciar sesión (login.html la trae con ?entrada=ok): avisa con
  // un toast en la página a la que aterrizó.
  if (new URLSearchParams(location.search).get("entrada") === "ok") {
    const toastEl = document.getElementById("toast");
    if (toastEl) {
      toastEl.textContent = "Sesión iniciada exitosamente";
      toastEl.className = "toast visible toast--ok";
      setTimeout(() => toastEl.classList.remove("visible"), 3200);
    }
    history.replaceState(null, "", location.pathname);
  }

  // Permiso que exige cada página; si no lo tiene, se le manda a la primera
  // página que sí pueda ver.
  const PERM_PAGINA = {
    mensajeria: "mensajeria",
    historial: "historial",
  };
  const ORDEN_PAGINAS = [
    ["mensajeria", "mensajeria.html"],
    ["historial", "historial.html"],
  ];
  // Páginas de administración: cada una es un HTML propio (usuarios,
  // pacientes, especialidades, estadisticas, configuracion) y la sidebar
  // muestra sus botones de navegación en vez del menú normal.
  const PAGINAS_ADMIN = ["usuarios", "pacientes", "especialidades", "estadisticas", "configuracion"];
  const ES_PAG_ADMIN = PAGINAS_ADMIN.indexOf(PAGINA) !== -1;
  function primeraPaginaPermitida() {
    for (const [perm, href] of ORDEN_PAGINAS) if (puede(perm)) return href;
    if (ES_PRIV) return "usuarios.html";
    return "index.html";
  }
  // Administración (Usuarios + Base de datos + Especialidades + Estadísticas +
  // Configuración) es exclusiva de admin/dev; Configuración, dentro de esas
  // páginas, es exclusiva de desarrollador.
  if (ES_PAG_ADMIN && !ES_PRIV) {
    location.replace(primeraPaginaPermitida());
    return;
  }
  if (PAGINA === "configuracion" && !ES_DEV) {
    location.replace(primeraPaginaPermitida());
    return;
  }
  if (PERM_PAGINA[PAGINA] && !puede(PERM_PAGINA[PAGINA])) {
    location.replace(primeraPaginaPermitida());
    return;
  }

  // Con sesión activa, la portada oculta los botones de acceso/registro.
  ["btnLogin", "heroAcciones"].forEach((id) => {
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
    { pagina: "mensajeria",   href: "mensajeria.html",   icono: "fa-paper-plane",       texto: "Mensajería y plantillas", perm: "mensajeria" },
    { pagina: "historial",    href: "historial.html",    icono: "fa-clock-rotate-left", texto: "Historial", perm: "historial" },
    { pagina: "usuarios", href: "usuarios.html", icono: "fa-user-shield", texto: "Administración", priv: true },
  ];

  // En las páginas de administración la sidebar cambia a los botones de
  // navegación de administración (con su separador), en vez del menú normal.
  const LINKS_ADMIN = [
    { pagina: "inicio",       href: "index.html",        icono: "fa-house",             texto: "Inicio" },
    { pagina: "mensajeria",   href: "mensajeria.html",   icono: "fa-paper-plane",       texto: "Mensajería y plantillas", perm: "mensajeria" },
    { pagina: "historial",    href: "historial.html",    icono: "fa-clock-rotate-left", texto: "Historial", perm: "historial" },
    { separador: "Administración" },
    { pagina: "usuarios",        href: "usuarios.html",        icono: "fa-users",        texto: "Usuarios" },
    { pagina: "pacientes",       href: "pacientes.html",       icono: "fa-database",     texto: "Base de datos" },
    { pagina: "especialidades",  href: "especialidades.html",  icono: "fa-stethoscope",  texto: "Especialidades" },
    { pagina: "estadisticas",    href: "estadisticas.html",    icono: "fa-chart-column", texto: "Estadísticas" },
    { pagina: "configuracion",   href: "configuracion.html",   icono: "fa-gear",         texto: "Configuración", dev: true },
  ];

  const FUENTE = ES_PAG_ADMIN ? LINKS_ADMIN : LINKS;
  const items = FUENTE
    .filter((l) => (!l.perm || puede(l.perm)) && (!l.priv || ES_PRIV) && (!l.dev || ES_DEV))
    .map((l) => {
      if (l.separador) return `<li class="sidebar__seccion">${l.separador}</li>`;
      return `<li class="nav-item">` +
        `<a class="nav-link${l.pagina === PAGINA ? " active" : ""}" href="${l.href}" title="${l.texto}">` +
        `<i class="fa-solid ${l.icono}"></i><span>${l.texto}</span></a></li>`;
    })
    .join("");

  const sidebar = document.getElementById("sidebar");
  if (sidebar) {
    const nombreUsuario = localStorage.getItem("snw_nombre") || "";
    const escaparHtml = (t) => {
      const d = document.createElement("div");
      d.textContent = t;
      return d.innerHTML;
    };
    sidebar.innerHTML =
      `<a class="sidebar__brand" href="index.html" title="Notificaciones WhatsApp">` +
      `<i class="fa-brands fa-whatsapp"></i>` +
      `<span><strong>Notificaciones</strong><small>Sistema Notificaciones WhatsApp</small></span></a>` +
      `<button type="button" class="sidebar__plegar" id="btnPlegar" aria-label="Plegar o expandir el menú">` +
      `<i class="fa-solid fa-angles-left"></i></button>` +
      `<ul class="nav flex-column sidebar__nav">${items}</ul>` +
      `<button type="button" class="sidebar__tema" id="btnTema" title="Cambiar entre modo claro y oscuro">` +
      `<i class="fa-solid fa-moon"></i><span>Modo oscuro</span></button>` +
      `<button type="button" class="sidebar__salir" id="btnClavePropia" title="Mi cuenta: contraseña y correo de recuperación">` +
      `<i class="fa-solid fa-circle-user"></i><span>${nombreUsuario ? `Mi cuenta - ${escaparHtml(nombreUsuario)}` : "Mi cuenta"}</span></button>` +
      `<button type="button" class="sidebar__salir" id="btnSalir" title="Cerrar sesión">` +
      `<i class="fa-solid fa-right-from-bracket"></i><span>Cerrar sesión</span></button>`;

    // Feedback instantáneo al hacer clic: marca el ítem como activo de
    // una vez (sin esperar a que la página nueva termine de cargar)
    const navLinks = sidebar.querySelectorAll(".sidebar__nav .nav-link");
    navLinks.forEach((link) => {
      link.addEventListener("click", () => {
        navLinks.forEach((l) => l.classList.remove("active"));
        link.classList.add("active");
      });
    });

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

    document.getElementById("btnClavePropia").addEventListener("click", () => {
      // En móvil la sidebar queda encima del modal; se cierra primero.
      cerrarMovil();
      abrirModalClave();
    });
  }

  // ----- Modal "Mi cuenta": cambiar la propia contraseña -----
  let modalClave = null;
  function abrirModalClave() {
    if (!modalClave) {
      modalClave = document.createElement("div");
      modalClave.className = "modal";
      modalClave.id = "modalMiCuenta";
      modalClave.hidden = true;
      modalClave.innerHTML =
        `<div class="modal__card" role="dialog" aria-modal="true">` +
        `<h3>Mi cuenta</h3>` +
        `<div class="field"><label>Correo</label><p class="field__valor" id="miCuentaCorreo">&hellip;</p></div>` +

        `<form id="formClavePropia" novalidate>` +
        `<div class="field"><label for="clAct">Contraseña actual</label>` +
        `<input type="password" id="clAct" autocomplete="current-password"></div>` +
        `<div class="field"><label for="clNue">Contraseña nueva</label>` +
        `<input type="password" id="clNue" autocomplete="new-password">` +
        `<p class="field__hint">Mínimo 8 caracteres, con una minúscula, una mayúscula y un número.</p></div>` +
        `<div class="field"><label for="clRep">Repite la contraseña nueva</label>` +
        `<input type="password" id="clRep" autocomplete="new-password"></div>` +
        `<p class="field__hint">Al cambiarla te avisamos por correo, si tu cuenta tiene uno registrado.</p>` +
        `<p class="warn" id="clMsg" hidden></p>` +
        `<div class="modal__actions">` +
        `<button type="button" class="btn btn--ghost" id="clCancelar">Cerrar</button>` +
        `<button type="submit" class="btn btn--primary" id="clGuardar">Cambiar contraseña</button>` +
        `</div></form></div>`;
      document.body.appendChild(modalClave);
      if (window.snwOjitoPass) window.snwOjitoPass(modalClave);

      const q = (s) => modalClave.querySelector(s);
      const cerrar = () => {
        modalClave.hidden = true;
        q("#formClavePropia").reset();
        q("#clMsg").hidden = true;
      };
      q("#clCancelar").addEventListener("click", cerrar);
      modalClave.addEventListener("click", (e) => { if (e.target === modalClave) cerrar(); });
      document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !modalClave.hidden) cerrar(); });

      q("#formClavePropia").addEventListener("submit", async (e) => {
        e.preventDefault();
        const msg = q("#clMsg");
        const act = q("#clAct").value;
        const nue = q("#clNue").value;
        const rep = q("#clRep").value;
        const mostrar = (t) => { msg.textContent = t; msg.hidden = false; };
        if (nue !== rep) return mostrar("Las contraseñas nuevas no coinciden.");
        const btn = q("#clGuardar");
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
          alert("Contraseña actualizada. Si tu cuenta tiene un correo registrado, te llegará un aviso.");
        } catch (ex) {
          mostrar(ex.message);
        } finally {
          btn.disabled = false;
        }
      });
    }
    modalClave.hidden = false;
    modalClave.querySelector("#clAct").focus();

    const correoEl = modalClave.querySelector("#miCuentaCorreo");
    if (correoEl) {
      correoEl.textContent = "…";
      fetch("api/auth/me", { headers: { Authorization: "Bearer " + token } })
        .then((r) => (r.ok ? r.json() : null))
        .then((d) => { correoEl.textContent = (d && d.usuario) || "—"; })
        .catch(() => { correoEl.textContent = "—"; });
    }
  }

  // Elementos que dependen de un permiso concreto (data-perm="call_center", ...):
  // se quitan del DOM para quien no lo tenga.
  document.querySelectorAll("[data-perm]").forEach((el) => {
    if (!puede(el.dataset.perm)) el.remove();
  });

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
