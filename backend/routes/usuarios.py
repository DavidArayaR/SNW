from ._registry import crear_router

RUTAS = (
    ("/api/usuarios/invitar", "POST", "invitar_usuario", 201), ("/api/usuarios", "GET", "listar_usuarios", None),
    ("/api/usuarios/{usuario}", "PUT", "actualizar_usuario", None),
    ("/api/usuarios/{usuario}/correo-recuperacion", "PUT", "asignar_correo_recuperacion", None),
    ("/api/usuarios/{usuario}/enviar-cambio-clave", "POST", "enviar_cambio_clave", None),
    ("/api/usuarios/{usuario}/envios", "GET", "envios_de_usuario", None),
    ("/api/usuarios/{usuario}/auditoria", "GET", "auditoria_de_usuario", None),
    ("/api/usuarios/{usuario}", "DELETE", "eliminar_usuario", None),
)

def registrar(handlers): return crear_router("Usuarios", handlers, RUTAS)
