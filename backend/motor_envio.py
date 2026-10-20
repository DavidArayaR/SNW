from db import log_error
from whatsapp_service import WhatsAppService
from supresion import esta_suprimido


class MotorSimulado:
    nombre = "simulado"

    def disponible(self) -> bool:
        return True

    def enviar(self, telefono: str, mensaje: str, plantilla: dict | None = None, variables: dict | None = None):
        if esta_suprimido(telefono):
            return False, None, "Número suprimido por solicitud del paciente"
        cta = (plantilla or {}).get("cta") if isinstance(plantilla, dict) else None
        # El URL puede llevar una consulta del paciente prellenada; jamás
        # imprimir mensaje, teléfono ni URL en la consola de simulación.
        print(f"[MOTOR {self.nombre}] mensaje simulado ({len(mensaje)} caracteres, "
              f"botón={'sí' if (cta and cta.get('url')) or (plantilla or {}).get('opciones_insulto') else 'no'})")
        return True, None, None


class MotorApiOficial:
    nombre = "api_oficial"

    def __init__(self, phone_number_id: str | None = None):
        self.servicio = WhatsAppService(phone_number_id=phone_number_id)

    def disponible(self) -> bool:
        return self.servicio.configurada()

    def enviar(self, telefono: str, mensaje: str, plantilla: dict | None = None, variables: dict | None = None):
        """Envía vía API oficial. Devuelve (ok, message_id, error).

        Si la plantilla lleva 'whatsapp_template' configurado se envía como
        template aprobado; si no, texto libre dentro de la ventana de 24h.
        """
        import asyncio

        try:
            if esta_suprimido(telefono):
                return False, None, "Número suprimido por solicitud del paciente"
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


def obtener_canal(config: dict, phone_number_id: str | None = None):
    clase = _MOTORES.get(config.get("metodo_envio", "simulado"))
    if clase is MotorApiOficial:
        return clase(phone_number_id)
    return clase() if clase else None
