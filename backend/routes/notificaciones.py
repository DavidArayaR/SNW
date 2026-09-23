from ._registry import crear_router

RUTAS = (
    ("/api/notificaciones/prueba-wa", "POST", "probar_api_wa", None), ("/api/notificaciones/enviar", "POST", "iniciar_envio", 202),
    ("/api/notificaciones/solicitud/{token}", "GET", "estado_solicitud", None),
    ("/api/notificaciones/rechazar/{token}", "GET", "formulario_rechazo", None),
    ("/api/notificaciones/rechazar/{token}", "POST", "rechazar_envio", None),
    ("/api/notificaciones/confirmar/{token}", "GET", "confirmar_envio", None),
    ("/api/notificaciones/destinatarios", "POST", "contar_destinatarios", None),
    ("/api/notificaciones/jobs/{job_id}", "GET", "estado_job", None),
    ("/api/notificaciones/jobs/{job_id}/cancelar", "POST", "cancelar_job", None),
    ("/api/notificaciones/jobs/{job_id}/pausa", "POST", "pausar_job", None),
    ("/api/notificaciones/jobs/{job_id}/reanudar", "POST", "reanudar_job", None),
    ("/api/notificaciones/historial", "GET", "listar_historial", None),
    ("/api/notificaciones/historial/{envio_id}/detalle", "GET", "detalle_historial", None),
    ("/api/notificaciones/historial/{registro_id}/respuesta", "PUT", "actualizar_respuesta", None),
)

def registrar(handlers): return crear_router("Notificaciones", handlers, RUTAS)
