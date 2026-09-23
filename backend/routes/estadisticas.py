from ._registry import crear_router

RUTAS = (
    ("/api/estadisticas", "GET", "estadisticas", None), ("/api/estadisticas/envios", "GET", "estadisticas_envios", None),
    ("/api/tarifas", "GET", "obtener_tarifas", None), ("/api/tarifas/actualizar", "POST", "actualizar_tarifas", None),
    ("/api/tarifas/chile.csv", "GET", "descargar_tarifa_csv", None), ("/api/estadisticas/costos", "GET", "estadisticas_costos", None),
)

def registrar(handlers): return crear_router("Estadísticas", handlers, RUTAS)
