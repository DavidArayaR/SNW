from ._registry import crear_router

RUTAS = (
    ("/api/pacientes", "GET", "listar_pacientes", None),
    ("/api/pacientes/pagina", "GET", "listar_pacientes_pagina", None),
    ("/api/pacientes/prueba", "POST", "crear_paciente_prueba", None),
    ("/api/pacientes/prueba/{paciente_id}/autorizar", "POST", "autorizar_numero_paciente_prueba", None),
    ("/api/pacientes/prueba/{paciente_id}/autorizar", "DELETE", "quitar_autorizacion_numero_paciente_prueba", None),
    ("/api/pacientes/estado-masivo", "PUT", "actualizar_estado_pacientes", None),
    ("/api/pacientes/respuesta-masiva", "PUT", "actualizar_respuesta_pacientes", None),
    ("/api/pacientes/{paciente_id}", "PUT", "actualizar_paciente", None),
    ("/api/pacientes/{paciente_id}/respuesta", "PUT", "actualizar_respuesta_paciente", None),
    ("/api/pacientes/{paciente_id}/mensajes", "GET", "mensajes_paciente", None),
    ("/api/pacientes/{paciente_id}/mensajes/{mensaje_id}/etiquetas", "PUT", "etiquetar_mensaje_paciente", None),
    ("/api/call-center/log", "GET", "obtener_call_center_log", None),
    ("/api/call-center/derivaciones/{derivacion_id}/reintentar", "POST", "reintentar_derivacion_call_center", None),
)

def registrar(handlers): return crear_router("Pacientes", handlers, RUTAS)
