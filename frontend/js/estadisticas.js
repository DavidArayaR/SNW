const $ = (sel) => document.querySelector(sel);

function authHeaders() {
  return { Authorization: "Bearer " + (localStorage.getItem("snw_token") || "") };
}

if (!localStorage.getItem("snw_token")) location.replace("login.html");

const toastEl = $("#toast");
let toastTimer;
function toast(msg, tipo = "ok") {
  clearTimeout(toastTimer);
  toastEl.textContent = msg;
  toastEl.className = `toast visible toast--${tipo}`;
  toastTimer = setTimeout(() => toastEl.classList.remove("visible"), 3200);
}

const MESES = [
  "enero", "febrero", "marzo", "abril", "mayo", "junio",
  "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
];

function nombreMes(ym) {
  const [a, m] = String(ym).split("-").map(Number);
  return `${MESES[(m || 1) - 1]} ${a}`;
}

const num = (n) => Number(n || 0).toLocaleString("es-CL");

function chip(label, valor, clase, resp) {
  const attr = resp ? ` data-respuesta="${resp}"` : "";
  return `<div class="stat ${clase}"${attr}>${label} <strong>${num(valor)}</strong></div>`;
}

async function cargar() {
  const btn = $("#btnActualizar");
  btn.disabled = true;
  try {
    const res = await fetch("api/estadisticas", { headers: authHeaders(), cache: "no-store" });
    if (res.status === 401) { window.snwSalir(); return; }
    if (!res.ok) throw new Error();
    render(await res.json());
  } catch {
    toast("No se pudieron cargar las estadísticas.", "error");
  } finally {
    btn.disabled = false;
  }
}

function render(d) {
  $("#nombreMes").textContent = nombreMes(d.mes);
  $("#enviadosMes").textContent = num(d.enviados_mes);

  $("#statsMes").innerHTML =
    chip("Enviados", d.enviados_mes, "stat--enviado") +
    chip("Fallidos", d.fallidos_mes, "stat--error") +
    chip("Inválidos", d.invalidos_mes, "stat--invalido") +
    chip("Respondieron", d.respondio_mes, "stat--resp", "respondio") +
    chip("Clicks", d.click_mes, "stat--resp", "click") +
    chip("Bajas", d.baja_mes, "stat--resp", "baja");

  const porMes = Array.isArray(d.por_mes) ? d.por_mes : [];
  const maxMes = Math.max(1, ...porMes.map((m) => m.enviados));
  $("#meses").innerHTML = porMes.length
    ? porMes
        .map(
          (m) => `
        <li class="mes-fila">
          <span class="mes-nombre">${nombreMes(m.mes)}</span>
          <span class="mes-barra"><span style="width:${Math.round((m.enviados / maxMes) * 100)}%"></span></span>
          <span class="mes-num">${num(m.enviados)}</span>
        </li>`
        )
        .join("")
    : '<li class="mes-vacio">Sin envíos registrados todavía.</li>';

  $("#statsTotal").innerHTML =
    chip("Mensajes enviados", d.total_enviados_historico, "stat--total") +
    chip("Envíos realizados", d.total_batches, "stat--total");
}

$("#btnActualizar").addEventListener("click", cargar);
cargar();
