from ._registry import crear_router

RUTAS = (
    ("/api/plantillas", "GET", "listar_plantillas", None), ("/api/plantillas/{plantilla_id}/estado-meta", "GET", "estado_plantilla_meta", None),
    ("/api/plantillas/estado-meta/actualizar", "POST", "actualizar_todos_estados_meta", None),
    ("/api/plantillas/sincronizar-meta", "POST", "sincronizar_plantillas_meta", None),
    ("/api/plantillas", "POST", "crear_plantilla", 201), ("/api/plantillas/{plantilla_id}", "PUT", "actualizar_plantilla", None),
    ("/api/plantillas/{plantilla_id}", "DELETE", "eliminar_plantilla", None),
    ("/api/plantillas/{plantilla_id}/aprobar", "POST", "aprobar_plantilla", None),
    ("/api/plantillas/{plantilla_id}/rechazar", "POST", "rechazar_plantilla", None),
)

def registrar(handlers): return crear_router("Plantillas", handlers, RUTAS)
