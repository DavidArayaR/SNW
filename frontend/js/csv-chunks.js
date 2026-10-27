/* Divide un CSV por registros completos: nunca corta un campo entre comillas. */
(function (raiz) {
  "use strict";

  const TAMANO_BLOQUE_CSV = 2 * 1024 * 1024;
  const MAX_FILAS_BLOQUE_CSV = 2000;

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

  function separarEncabezadoCsv(registro) {
    const columnas = [];
    let valor = "";
    let entreComillas = false;
    for (let i = 0; i < registro.length; i++) {
      const caracter = registro[i];
      if (caracter === '"') {
        if (entreComillas && registro[i + 1] === '"') {
          valor += '"';
          i++;
        } else if (entreComillas || !valor) {
          entreComillas = !entreComillas;
        } else {
          throw new Error("El encabezado del CSV tiene comillas mal ubicadas.");
        }
      } else if (caracter === "," && !entreComillas) {
        columnas.push(valor);
        valor = "";
      } else {
        valor += caracter;
      }
    }
    if (entreComillas) throw new Error("El encabezado del CSV tiene comillas sin cerrar.");
    columnas.push(valor);
    if (columnas.some((c) => !c.trim()) || new Set(columnas).size !== columnas.length) {
      throw new Error("El CSV tiene columnas vacías o encabezados repetidos.");
    }
    return columnas;
  }

  async function leerEncabezadosCsv(archivo) {
    const registros = registrosCsv(archivo);
    try {
      const primero = await registros.next();
      if (primero.done) throw new Error("El archivo CSV está vacío.");
      return separarEncabezadoCsv(primero.value);
    } finally {
      await registros.return();
    }
  }

  function sugerirMapeoCsv(encabezados) {
    const normalizar = (valor) => valor.normalize("NFD").replace(/[\u0300-\u036f]/g, "")
      .toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
    const aliases = {
      nombre: ["nombre", "nombres", "primer nombre"],
      apellido: ["apellido", "apellidos", "primer apellido"],
      telefono: ["telefono", "numero de telefono", "numero telefono", "celular", "movil", "whatsapp"],
      respuesta: ["respuesta", "respuesta paciente", "estado respuesta"],
      estado: ["estado", "estado paciente", "estado envio"],
      tratamiento_ultima_atencion: ["tratamiento ultima atencion", "ultimo tratamiento", "tratamiento"],
      fecha_ultima_atencion: ["fecha ultima atencion", "ultima atencion", "fecha atencion"],
    };
    const resultado = {};
    const usados = new Set();
    for (const [campo, opciones] of Object.entries(aliases)) {
      const encontrado = encabezados.find((columna) =>
        !usados.has(columna) && opciones.includes(normalizar(columna)));
      if (encontrado) {
        resultado[campo] = encontrado;
        usados.add(encontrado);
      }
    }
    return resultado;
  }

  function validarMapeoCsv(mapeo, encabezados) {
    for (const campo of ["nombre", "apellido", "telefono"]) {
      if (!mapeo[campo]) return `Selecciona la columna para ${campo}.`;
    }
    const usadas = Object.values(mapeo).filter(Boolean);
    if (usadas.some((columna) => !encabezados.includes(columna))) {
      return "Una columna asignada no existe en el archivo.";
    }
    if (new Set(usadas).size !== usadas.length) {
      return "Cada columna del archivo debe asignarse a un solo campo.";
    }
    return "";
  }

  async function* fragmentarCsv(archivo, maxBytes = TAMANO_BLOQUE_CSV,
                                maxFilas = MAX_FILAS_BLOQUE_CSV) {
    if (!Number.isInteger(maxFilas) || maxFilas < 1) {
      throw new Error("El máximo de filas por bloque debe ser positivo.");
    }
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
      if ((tamano + bytesLinea > maxBytes || cantidad >= maxFilas) && cantidad) {
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

  const api = { fragmentarCsv, leerEncabezadosCsv, sugerirMapeoCsv, validarMapeoCsv,
    TAMANO_BLOQUE_CSV, MAX_FILAS_BLOQUE_CSV };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (raiz) raiz.snwCsvChunks = api;
})(typeof window !== "undefined" ? window : null);
