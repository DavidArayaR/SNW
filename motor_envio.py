import urllib.parse
import webbrowser


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


class MotorNoImplementado:
    nombre = ""

    def __init__(self):
        self._error = f"El método '{self.nombre}' aún no está implementado en este prototipo"

    def disponible(self) -> bool:
        return False

    def enviar(self, telefono: str, mensaje: str):
        return False, self._error


class MotorApiOficial(MotorNoImplementado):
    nombre = "api_oficial"


_MOTORES = {
    "simulado": MotorSimulado,
    "whatsapp_web": MotorWhatsAppWeb,
    "api_oficial": MotorApiOficial,
}


def obtener_canal(config: dict):
    clase = _MOTORES.get(config.get("metodo_envio", "simulado"))
    return clase() if clase else None
