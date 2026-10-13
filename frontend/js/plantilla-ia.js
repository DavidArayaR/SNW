/* El borrador sustituye el mensaje en el editor sin guardarlo automáticamente. */
(function (root) {
  function elegirTexto(borrador) {
    const nuevo = String(borrador || "").trim();
    if (!nuevo) return null;
    return nuevo;
  }
  root.snwElegirBorradorIA = elegirTexto;
  if (typeof module !== "undefined" && module.exports) module.exports = elegirTexto;
})(typeof window !== "undefined" ? window : globalThis);
