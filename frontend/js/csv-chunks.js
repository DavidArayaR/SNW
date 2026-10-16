/* Divide un CSV por registros completos: nunca corta un campo entre comillas. */
(function (raiz) {
  "use strict";

  const TAMANO_BLOQUE_CSV = 2 * 1024 * 1024;

  async function* registrosCsv(archivo) {
    const lector = archivo.stream().getReader();
    const decodificador = new TextDecoder("utf-8", { fatal: true });
    let registro = "";
    let entreComillas = false;
    try {
      while (true) {
        const { value, done } = await lector.read();
        const texto = done ? decodificador.decode() :
          decodificador.decode(value, { stream: true });
        for (const caracter of texto) {
          if (caracter === '"') entreComillas = !entreComillas;
          if ((caracter === "\n" || caracter === "\r") && !entreComillas) {
            if (registro.trim()) yield registro;
            registro = "";
          } else {
            registro += caracter;
          }
        }
        if (done) break;
      }
      if (entreComillas) throw new Error("El CSV contiene comillas sin cerrar.");
      if (registro.trim()) yield registro;
    } finally {
      lector.releaseLock();
    }
  }

  async function* fragmentarCsv(archivo, maxBytes = TAMANO_BLOQUE_CSV) {
    const registros = registrosCsv(archivo);
    const primero = await registros.next();
    if (primero.done) throw new Error("El archivo CSV está vacío.");
    const codificador = new TextEncoder();
    const encabezado = `${primero.value}\n`;
    const bytesEncabezado = codificador.encode(encabezado).length;
    if (bytesEncabezado >= maxBytes) throw new Error("El encabezado del CSV es demasiado grande.");

    let contenido = encabezado;
    let tamano = bytesEncabezado;
    let filaInicial = 2;
    let filaSiguiente = 2;
    let cantidad = 0;
    for await (const registro of registros) {
      const linea = `${registro}\n`;
      const bytesLinea = codificador.encode(linea).length;
      if (bytesEncabezado + bytesLinea > maxBytes) {
        throw new Error(`La fila ${filaSiguiente} supera el tamaño máximo de un bloque CSV.`);
      }
      if (tamano + bytesLinea > maxBytes && cantidad) {
        yield { contenido, filaInicial, cantidad };
        contenido = encabezado;
        tamano = bytesEncabezado;
        filaInicial = filaSiguiente;
        cantidad = 0;
      }
      contenido += linea;
      tamano += bytesLinea;
      cantidad++;
      filaSiguiente++;
    }
    if (cantidad || filaSiguiente === 2) yield { contenido, filaInicial, cantidad };
  }

  const api = { fragmentarCsv, TAMANO_BLOQUE_CSV };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (raiz) raiz.snwCsvChunks = api;
})(typeof window !== "undefined" ? window : null);
