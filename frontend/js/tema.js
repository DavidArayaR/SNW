/* Modo claro / oscuro. Se carga en el <head> de todas las páginas para
   aplicar el tema guardado antes del primer render (sin parpadeo). */
(function () {
  var KEY = "snw_tema";
  try {
    if (localStorage.getItem(KEY) === "oscuro") {
      document.documentElement.dataset.tema = "oscuro";
    }
  } catch (e) { /* almacenamiento no disponible */ }

  // Devuelve true si quedó en oscuro.
  window.snwToggleTema = function () {
    var root = document.documentElement;
    var oscuro = root.dataset.tema !== "oscuro";
    root.classList.add("tema-anim");
    root.dataset.tema = oscuro ? "oscuro" : "";
    try { localStorage.setItem(KEY, oscuro ? "oscuro" : "claro"); } catch (e) {}
    window.setTimeout(function () { root.classList.remove("tema-anim"); }, 300);
    document.dispatchEvent(new CustomEvent("snw:tema", { detail: oscuro ? "oscuro" : "claro" }));
    return oscuro;
  };

  window.snwTemaOscuro = function () {
    return document.documentElement.dataset.tema === "oscuro";
  };

  // Botón flotante para las páginas que no tienen sidebar (portada / login).
  window.snwMontarFabTema = function () {
    if (document.querySelector(".tema-fab")) return;
    var b = document.createElement("button");
    b.type = "button";
    b.className = "tema-fab";
    b.setAttribute("aria-label", "Cambiar entre modo claro y oscuro");
    var icono = function () { return window.snwTemaOscuro() ? "☀" : "☾"; };
    b.textContent = icono();
    b.addEventListener("click", function () {
      window.snwToggleTema();
      b.textContent = icono();
    });
    document.body.appendChild(b);
  };
})();
