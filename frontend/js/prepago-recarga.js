(() => {
  const token = location.hash.slice(1);
  const form = document.getElementById("formRecarga");
  const fecha = document.getElementById("fecha");
  const estado = document.getElementById("estado");
  const boton = form.querySelector("button");
  const api = "api/extras/prepago/recarga-correo";

  async function solicitar(accion, datos) {
    const respuesta = await fetch(`${api}/${accion}`, {
      method: "POST",
      credentials: "omit",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token, ...datos }),
    });
    const resultado = await respuesta.json();
    if (!respuesta.ok) {
      const detalle = typeof resultado.detail === "string" ? resultado.detail : "No se pudo completar la solicitud.";
      const error = new Error(detalle);
      error.status = respuesta.status;
      throw error;
    }
    return resultado;
  }

  form.addEventListener("submit", async (evento) => {
    evento.preventDefault();
    if (boton.disabled) return;
    boton.disabled = true;
    estado.textContent = "Registrando recarga…";
    try {
      await solicitar("confirmar", { fecha: fecha.value });
      form.hidden = true;
      estado.textContent = "Recarga registrada en SNW. El próximo recordatorio se calculará desde esta fecha.";
      history.replaceState(null, "", location.pathname);
    } catch (error) {
      if (error.status === 404) form.hidden = true;
      estado.textContent = error.status ? error.message : "No se pudo confirmar el registro. Vuelve a abrir el enlace para comprobarlo.";
    } finally {
      boton.disabled = false;
    }
  });

  if (!/^[A-Za-z0-9_-]{43}$/.test(token)) {
    estado.textContent = "Abre el enlace del recordatorio de prepago para registrar la recarga.";
    return;
  }
  solicitar("ver").then((datos) => {
    document.getElementById("linea").textContent = `${datos.linea} · Número terminado en ${datos.ultimos_digitos}`;
    fecha.value = fecha.max = datos.hoy;
    form.hidden = false;
    estado.textContent = "";
  }).catch((error) => {
    estado.textContent = error.status ? error.message : "No se pudo verificar el enlace. Inténtalo nuevamente.";
  });
})();
