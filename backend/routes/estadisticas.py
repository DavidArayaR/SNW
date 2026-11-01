from ._registry import crear_router

RUTAS = (
    ("/api/estadisticas", "GET", "estadisticas", None), ("/api/estadisticas/envios", "GET", "estadisticas_envios", None),
    ("/api/estadisticas/botones", "GET", "estadisticas_botones", None),
    ("/api/estadisticas/campanas", "GET", "listar_envios_estadisticas", None),
    ("/api/estadisticas/campanas/simulacion", "POST", "crear_envio_simulado", 201),
    ("/api/estadisticas/campanas/{envio_id}", "GET", "obtener_estadisticas_envio", None),
    ("/api/tarifas", "GET", "obtener_tarifas", None), ("/api/tarifas/actualizar", "POST", "actualizar_tarifas", None),
    ("/api/tarifas/chile.csv", "GET", "descargar_tarifa_csv", None), ("/api/estadisticas/costos", "GET", "estadisticas_costos", None),
)

def registrar(handlers): return crear_router("Estadísticas", handlers, RUTAS)
