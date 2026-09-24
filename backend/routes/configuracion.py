from ._registry import crear_router

RUTAS = (
    ("/api/configuracion", "GET", "obtener_configuracion", None), ("/api/configuracion", "PUT", "actualizar_configuracion", None),
    ("/api/configuracion/todo", "GET", "obtener_configuracion_completa", None),
    ("/api/configuracion/todo", "PUT", "actualizar_configuracion_completa", None),
    ("/api/whatsapp/rate-limit", "GET", "estado_rate_limit", None),
    ("/api/whatsapp/messaging-limit", "GET", "estado_messaging_limit", None),
    ("/api/whatsapp/messaging-limit", "PUT", "actualizar_messaging_limit", None),
)

def registrar(handlers): return crear_router("Configuración", handlers, RUTAS)
