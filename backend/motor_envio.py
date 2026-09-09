from db import log_error
from whatsapp_service import WhatsAppService


class MotorSimulado:
    nombre = "simulado"

    def disponible(self) -> bool:
        return True

    def enviar(self, telefono: str, mensaje: str, plantilla: dict | None = None, variables: dict | None = None):
        vista = mensaje.replace("\n", " ")[:60]
        cta = (plantilla or {}).get("cta") if isinstance(plantilla, dict) else None
        extra = f"  [botón: {cta.get('texto')} -> {cta.get('url')}]" if cta and cta.get("url") else ""
        try:
            print(f"[MOTOR {self.nombre}] -> {telefono}: {vista}...{extra}")
        except UnicodeEncodeError:
            # La consola de Windows (cp1252) no puede imprimir emojis del mensaje.
            print(f"[MOTOR {self.nombre}] -> {telefono}: ({len(mensaje)} caracteres){extra}")
        return True, None, None


class MotorApiOficial:
    nombre = "api_oficial"

    def __init__(self):
        self.servicio = WhatsAppService()

    def disponible(self) -> bool:
        return self.servicio.configurada()

    def enviar(self, telefono: str, mensaje: str, plantilla: dict | None = None, variables: dict | None = None):
        """Envía vía API oficial. Devuelve (ok, message_id, error).

        Si la plantilla lleva 'whatsapp_template' configurado se envía como
        template aprobado; si no, texto libre dentro de la ventana de 24h.
        """
        import asyncio

        try:
            ok, message_id, error, _ = asyncio.run(
                self.servicio.enviar(telefono, mensaje, plantilla=plantilla, variables=variables)
            )
        except Exception as e:
            log_error(f"MotorApiOficial.enviar a {telefono}", e)
            return False, None, f"Fallo de conexión con la API de Meta: {e}"
        if not ok:
            log_error(f"MotorApiOficial.enviar a {telefono}: {error}")
        return ok, message_id, error


_MOTORES = {
    "simulado": MotorSimulado,
    "api_oficial": MotorApiOficial,
}


def obtener_canal(config: dict):
    clase = _MOTORES.get(config.get("metodo_envio", "simulado"))
    return clase() if clase else None
