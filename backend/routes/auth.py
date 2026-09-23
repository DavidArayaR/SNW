from ._registry import crear_router

RUTAS = (
    ("/api/auth/login", "POST", "login", None), ("/api/auth/me", "GET", "auth_me", None),
    ("/api/auth/clave", "PUT", "cambiar_clave_propia", None),
    ("/api/auth/correo-recuperacion", "PUT", "cambiar_correo_recuperacion", None),
    ("/api/auth/olvide", "POST", "olvide_clave", None), ("/api/auth/reset/{token}", "GET", "reset_verificar", None),
    ("/api/auth/reset", "POST", "reset_aplicar", None), ("/api/auth/logout", "POST", "logout", None),
    ("/api/auth/invitacion/{token}", "GET", "invitacion_verificar", None),
    ("/api/auth/activar", "POST", "activar_cuenta", 201),
)

def registrar(handlers): return crear_router("Autenticación", handlers, RUTAS)
