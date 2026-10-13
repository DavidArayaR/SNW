/* Decisión de reemplazo separada para conservar el texto si se cancela. */
(function (root) {
  function elegirTexto(actual, borrador, confirmar) {
    const previo = String(actual || "");
    const nuevo = String(borrador || "").trim();
    if (!nuevo) return null;
    if (previo.trim() && previo.trim() !== nuevo &&
        !confirmar("El mensaje actual será reemplazado. ¿Quieres usar este borrador?")) return null;
    return nuevo;
  }
  root.snwElegirBorradorIA = elegirTexto;
  if (typeof module !== "undefined" && module.exports) module.exports = elegirTexto;
})(typeof window !== "undefined" ? window : globalThis);
