/* Cálculos de segmentación sobre agregados; no contiene datos de pacientes. */
(function () {
  const RESULTADOS = ["interes", "desinteres", "baja", "sin_seleccion", "vistos"];
  function resultadosSeleccionados(valor) {
    if (valor === "todos") return [...RESULTADOS];
    const lista = Array.isArray(valor) ? valor : String(valor).split(",");
    return [...new Set(lista.filter(r => RESULTADOS.includes(r)))];
  }
  function alternarResultado(actual, resultado) {
    if (resultado === "todos") return "todos";
    if (!RESULTADOS.includes(resultado)) return null;
    const elegidos = new Set(resultadosSeleccionados(actual));
    if (elegidos.has(resultado)) elegidos.delete(resultado);
    else elegidos.add(resultado);
    if (elegidos.size < 2) return null;
    return elegidos.size === RESULTADOS.length ? "todos" : [...elegidos].join(",");
  }
  function coincide(celda, entrega, resultado) {
    const entregaOk = entrega === "todos" || celda.entrega === entrega ||
      (entrega === "entregados" && celda.entrega === "leidos");
    return entregaOk && (resultado === "todos" || resultadosSeleccionados(resultado).some(r =>
      r === "vistos" ? celda.visto_sin_respuesta : celda.resultado === r));
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
      leidos_con_respuesta: suma(c => c.entrega === "leidos" && !c.visto_sin_respuesta),
      sin_confirmacion: suma(c => c.entrega === "sin_confirmacion"),
      sin_seleccion: suma(c => c.resultado === "sin_seleccion"),
      tasa_interes: interacciones.interes.porcentaje,
    };
    // El costo representa el envío completo; los filtros solo segmentan respuestas.
    const celdasPrecio = datos.segmentos;
    const interesadosTotales = celdasPrecio.reduce((n, c) => n + (c.resultado === "interes" ? Number(c.cantidad) : 0), 0);
    const desconocido = celdasPrecio.some(c => c.costo == null);
    const importe = desconocido ? null : celdasPrecio.reduce((n, c) => n + Number(c.costo), 0);
    const costo = { ...datos.costo, importe,
      mensajes: celdasPrecio.reduce((n, c) => n + Number(c.cantidad), 0),
      entregas: celdasPrecio.reduce((n, c) => n + (["entregados", "leidos"].includes(c.entrega) ? Number(c.cantidad) : 0), 0),
      por_interesado: importe != null && interesadosTotales ? importe / interesadosTotales : null,
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
  function combinar(envios) {
    const unicos = [...new Map(envios.map(d => [String(d.envio.id), d])).values()];
    if (!unicos.length) throw new Error("Selecciona al menos un envío.");
    if (unicos.length === 1) return unicos[0];
    if (new Set(unicos.map(d => !!d.simulacion)).size > 1)
      throw new Error("Selecciona envíos reales o simulados por separado.");
    const mismaMoneda = new Set(unicos.map(d => d.costo.moneda)).size === 1;
    const resumen = {};
    for (const clave of ["total", "intentados", "errores", "invalidos", "sin_intento"])
      resumen[clave] = unicos.reduce((n, d) => n + Number(d.resumen[clave] || 0), 0);
    return {
      envios: unicos.map(d => d.envio), simulacion: !!unicos[0].simulacion,
      envio: { id: null, plantilla_nombre: `${unicos.length} envíos seleccionados`,
        centro: [...new Set(unicos.map(d => d.envio.centro))].join(" · "),
        inicio: "Varias fechas", numero_emisor: "Varios envíos", plantilla_clave: "Varias plantillas" },
      resumen, costo: { moneda: mismaMoneda ? unicos[0].costo.moneda : null },
      monedas_distintas: !mismaMoneda,
      segmentos: unicos.flatMap(d => d.segmentos.map(c => ({ ...c, costo: mismaMoneda ? c.costo : null }))),
      botones: unicos.flatMap(d => d.botones),
    };
  }
  window.SNWEstadisticasCampana = { filtrar, comparar, combinar, resultadosSeleccionados, alternarResultado };
})();
