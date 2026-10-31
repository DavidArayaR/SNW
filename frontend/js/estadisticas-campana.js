/* Cálculos de segmentación sobre agregados; no contiene datos de pacientes. */
(function () {
  function coincide(celda, entrega, resultado) {
    const entregaOk = entrega === "todos" || celda.entrega === entrega ||
      (entrega === "entregados" && celda.entrega === "leidos");
    return entregaOk && (resultado === "todos" ||
      (resultado === "vistos" ? celda.visto_sin_respuesta : celda.resultado === resultado));
  }

  function filtrar(datos, entrega = "todos", resultado = "todos") {
    const celdas = datos.segmentos.filter(c => coincide(c, entrega, resultado));
    const suma = predicado => celdas.reduce((n, c) => n + (predicado(c) ? Number(c.cantidad) : 0), 0);
    const validos = suma(() => true);
    const interacciones = Object.fromEntries(["interes", "desinteres", "baja"].map(clave => {
      const pacientes = suma(c => c.resultado === clave);
      return [clave, { pacientes, porcentaje: validos ? Math.round(1000 * pacientes / validos) / 10 : 0 }];
    }));
    const resumen = { ...datos.resumen, validos,
      entregados: suma(c => ["entregados", "leidos"].includes(c.entrega)),
      leidos: suma(c => c.entrega === "leidos"), vistos_sin_respuesta: suma(c => c.visto_sin_respuesta),
      sin_confirmacion: suma(c => c.entrega === "sin_confirmacion"),
      sin_seleccion: suma(c => c.resultado === "sin_seleccion"),
      tasa_interes: interacciones.interes.porcentaje,
    };
    const desconocido = celdas.some(c => c.costo == null);
    const importe = desconocido ? null : celdas.reduce((n, c) => n + Number(c.costo), 0);
    const costo = { ...datos.costo, importe,
      entregas: celdas.reduce((n, c) => n + Number(c.facturables), 0),
      por_interesado: importe != null && interacciones.interes.pacientes ? importe / interacciones.interes.pacientes : null,
    };
    const agrupados = new Map();
    for (const b of datos.botones) {
      // Un clic recibido implica que no pertenece al grupo «visto sin responder».
      const celda = { ...b, entrega: b.entrega === "read" ? "leidos" : b.entrega === "delivered" ? "entregados" : "sin_confirmacion", visto_sin_respuesta: false };
      if (b.medible !== false && !coincide(celda, entrega, resultado)) continue;
      const clave = `${b.boton || ""}:${b.accion || ""}`;
      const previo = agrupados.get(clave) || { ...b, clics: 0, pacientes: 0 };
      previo.clics += Number(b.clics || 0);
      previo.pacientes += Number(b.pacientes || 0);
      agrupados.set(clave, previo);
    }
    const botones = [...agrupados.values()].map(b => ({ ...b,
      porcentaje: validos ? Math.round(1000 * b.pacientes / validos) / 10 : 0,
    }));
    return { ...datos, resumen, interacciones, costo, botones };
  }

  function comparar(datos, modo) {
    const r = datos.resumen;
    const opciones = {
      interes: ["Interesados", "#58ad96", datos.interacciones.interes.pacientes, { resultado: "interes" }],
      desinteres: ["No interesados", "#c89348", datos.interacciones.desinteres.pacientes, { resultado: "desinteres" }],
      baja: ["Se dieron de baja", "#d17c7c", datos.interacciones.baja.pacientes, { resultado: "baja" }],
      sin_seleccion: ["Sin selección", "#87978f", r.sin_seleccion, { resultado: "sin_seleccion" }],
      vistos: ["Leídos sin responder", "#7599bb", r.vistos_sin_respuesta, { resultado: "vistos" }],
      leidos: ["Leídos", "#58ad96", r.leidos, { entrega: "leidos" }],
      entregados: ["Entregados sin lectura", "#7599bb", r.entregados - r.leidos, {}],
      sin_confirmacion: ["Sin confirmación", "#87978f", r.sin_confirmacion, { entrega: "sin_confirmacion" }],
    };
    const claves = modo === "general" ? ["interes", "desinteres", "baja", "sin_seleccion"] :
      modo === "entregas" ? ["leidos", "entregados", "sin_confirmacion"] : modo.split("-");
    const series = claves.filter(k => opciones[k]).map(clave => {
      const [titulo, color, cantidad, filtro] = opciones[clave];
      return { titulo, color, cantidad, filtro };
    });
    const resto = Math.max(0, r.validos - series.reduce((n, s) => n + s.cantidad, 0));
    if (resto) series.push({ titulo: "Fuera de esta comparación", color: "#87978f", cantidad: resto, filtro: {} });
    return series;
  }
  window.SNWEstadisticasCampana = { filtrar, comparar };
})();
