import json
import os
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

GRAPH_URL = "https://graph.facebook.com/v21.0/{phone_id}/messages"


class MotorSimulado:
    nombre = "simulado"

    def disponible(self) -> bool:
        return True

    def enviar(self, telefono: str, mensaje: str):
        vista = mensaje.replace("\n", " ")[:60]
        print(f"[MOTOR {self.nombre}] -> {telefono}: {vista}...")
        return True, None


class MotorWhatsAppWeb:
    nombre = "whatsapp_web"

    def disponible(self) -> bool:
        return True

    def enviar(self, telefono: str, mensaje: str):
        numero = telefono.lstrip("+")
        url = f"https://web.whatsapp.com/send?phone={numero}&text={urllib.parse.quote(mensaje)}"
        if webbrowser.open(url):
            return True, None
        return False, "No se pudo abrir WhatsApp Web en el navegador"


class MotorApiOficial:
    nombre = "api_oficial"

    def __init__(self):
        self.token = os.getenv("SNW_WA_TOKEN", "").strip()
        self.phone_id = os.getenv("SNW_WA_PHONE_ID", "").strip()

    def disponible(self) -> bool:
        return bool(self.token) and bool(self.phone_id)

    def enviar(self, telefono: str, mensaje: str):
        url = GRAPH_URL.format(phone_id=self.phone_id)
        payload = json.dumps({
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": telefono.lstrip("+"),
            "type": "text",
            "text": {"preview_url": False, "body": mensaje},
        }).encode("utf-8")

        peticion = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(peticion, timeout=30) as resp:
                datos = json.loads(resp.read().decode("utf-8"))
            if datos.get("messages"):
                return True, None
            return False, f"Respuesta inesperada de la API: {json.dumps(datos)[:200]}"
        except urllib.error.HTTPError as e:
            try:
                detalle = json.loads(e.read().decode("utf-8")).get("error", {}).get("message", "")
            except Exception:
                detalle = ""
            return False, f"Error HTTP {e.code}: {detalle or 'sin detalle'}"
        except Exception as e:
            return False, f"Fallo de conexión con la API de Meta: {e}"


_MOTORES = {
    "simulado": MotorSimulado,
    "whatsapp_web": MotorWhatsAppWeb,
    "api_oficial": MotorApiOficial,
}


def obtener_canal(config: dict):
    clase = _MOTORES.get(config.get("metodo_envio", "simulado"))
    return clase() if clase else None
