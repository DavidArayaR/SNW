const formularioDecision = document.querySelector("#decision");
formularioDecision.addEventListener("submit", async (evento) => {
  evento.preventDefault();
  const resultado = document.querySelector("#resultado");
  const boton = formularioDecision.querySelector("button");
  const token = localStorage.getItem("snw_token");
  if (!token) {
    resultado.textContent = "Inicia sesión con tu cuenta autorizada y vuelve a intentarlo.";
    return;
  }
  boton.disabled = true;
  try {
    const respuesta = await fetch(location.pathname, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
      body: new FormData(formularioDecision),
      cache: "no-store",
    });
    if (!respuesta.ok) {
      const error = await respuesta.json().catch(() => ({}));
      resultado.textContent = typeof error.detail === "string" ? error.detail : "No se pudo guardar la decisión.";
      boton.disabled = false;
      return;
    }
    formularioDecision.hidden = true;
    resultado.textContent = "La decisión quedó registrada.";
  } catch {
    resultado.textContent = "No se pudo contactar a SNW. Vuelve a intentarlo.";
    boton.disabled = false;
  }
});
