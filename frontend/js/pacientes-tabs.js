(function () {
  const barra = document.querySelector(".pacientes-tabs");
  const panelPacientes = document.getElementById("panelBaseDatos");
  const panelReagendar = document.getElementById("cardReagendar");
  if (!barra || !panelPacientes || !panelReagendar) return;

  const botones = [...barra.querySelectorAll("[data-pacientes-tab]")];
  const botonReagendar = document.getElementById("pacientes-tab-reagendar");
  const puedeReagendar = !!window.snwEsPrivilegiado &&
    typeof window.snwCargarReagendar === "function";
  botonReagendar.hidden = !puedeReagendar;
  botonReagendar.tabIndex = -1;
  let actual = "pacientes";

  function posicionarIndicador() {
    const activo = barra.querySelector(".msg-tab.activo");
    if (!activo) return;
    const caja = barra.getBoundingClientRect();
    const boton = activo.getBoundingClientRect();
    barra.style.setProperty("--msg-indicador-x",
      `${boton.left - caja.left - barra.clientLeft + barra.scrollLeft}px`);
    barra.style.setProperty("--msg-indicador-ancho", `${boton.width}px`);
  }

  function seleccionar(destino, enfocar = false) {
    if (destino === actual) {
      if (enfocar) barra.querySelector(`[data-pacientes-tab="${destino}"]`)?.focus();
      return;
    }
    if (destino === "reagendar" && !puedeReagendar) return;
    const anterior = actual;
    actual = destino;
    for (const boton of botones) {
      const activo = boton.dataset.pacientesTab === actual;
      boton.classList.toggle("activo", activo);
      boton.setAttribute("aria-selected", String(activo));
      boton.tabIndex = activo ? 0 : -1;
    }
    panelPacientes.hidden = actual !== "pacientes";
    panelReagendar.hidden = actual !== "reagendar";
    if (actual !== "reagendar") window.snwCerrarAplazamiento?.();
    const panel = actual === "pacientes" ? panelPacientes : panelReagendar;
    panel.style.setProperty("--pacientes-entrada-x", actual === "reagendar" ? "20px" : "-20px");
    posicionarIndicador();
    if (actual === "reagendar") window.snwCargarReagendar?.();
    if (enfocar) barra.querySelector(`[data-pacientes-tab="${destino}"]`)?.focus();
    if (anterior !== actual) window.scrollTo(0, 0);
  }

  for (const boton of botones) {
    boton.addEventListener("click", () => seleccionar(boton.dataset.pacientesTab));
    boton.addEventListener("keydown", (evento) => {
      const disponibles = botones.filter((item) => !item.hidden);
      const indice = disponibles.indexOf(boton);
      const destino = evento.key === "ArrowRight" ? disponibles[(indice + 1) % disponibles.length]
        : evento.key === "ArrowLeft" ? disponibles[(indice - 1 + disponibles.length) % disponibles.length]
        : evento.key === "Home" ? disponibles[0]
        : evento.key === "End" ? disponibles[disponibles.length - 1] : null;
      if (destino) {
        evento.preventDefault();
        seleccionar(destino.dataset.pacientesTab, true);
      }
    });
  }
  window.addEventListener("resize", posicionarIndicador);
  document.fonts?.ready.then(posicionarIndicador);
  posicionarIndicador();
  window.snwCargarPacientes?.();
})();
