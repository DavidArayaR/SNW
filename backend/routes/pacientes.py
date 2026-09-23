from ._registry import crear_router

RUTAS = (
    ("/api/pacientes", "GET", "listar_pacientes", None), ("/api/pacientes/estado-masivo", "PUT", "actualizar_estado_pacientes", None),
    ("/api/pacientes/respuesta-masiva", "PUT", "actualizar_respuesta_pacientes", None),
    ("/api/pacientes/{paciente_id}", "PUT", "actualizar_paciente", None),
    ("/api/pacientes/{paciente_id}/respuesta", "PUT", "actualizar_respuesta_paciente", None),
    ("/api/pacientes/{paciente_id}/mensajes", "GET", "mensajes_paciente", None),
    ("/api/call-center/log", "GET", "obtener_call_center_log", None),
)

def registrar(handlers): return crear_router("Pacientes", handlers, RUTAS)
