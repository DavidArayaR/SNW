class MotorSimulado:
    nombre = "simulado"

    def disponible(self) -> bool:
        return True

    def enviar(self, telefono: str, mensaje: str):
        vista = mensaje.replace("\n", " ")[:60]
        print(f"[MOTOR {self.nombre}] -> {telefono}: {vista}...")
        return True, None


class MotorNoImplementado:
    nombre = ""

    def __init__(self):
        self._error = f"El método '{self.nombre}' aún no está implementado en este prototipo"

    def disponible(self) -> bool:
        return False

    def enviar(self, telefono: str, mensaje: str):
        return False, self._error


class MotorWhatsAppWeb(MotorNoImplementado):
    nombre = "whatsapp_web"


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
