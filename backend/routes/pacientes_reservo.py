from ._registry import crear_router

RUTAS = (
    ("/api/reservo/configuracion", "GET", "listar_configuracion_reservo", None),
    ("/api/reservo/clinicas/{codigo}/probar", "POST", "probar_conexion_reservo", None),
    ("/api/reservo/tratamientos", "POST", "listar_tratamientos_reservo", None),
    ("/api/reservo/sincronizaciones", "GET", "listar_sincronizaciones_reservo", None),
    ("/api/reservo/sincronizaciones", "POST", "iniciar_sincronizacion_reservo", 202),
    ("/api/reservo/sincronizaciones/{job_id}", "GET", "obtener_sincronizacion_reservo", None),
    ("/api/reservo/sincronizaciones/{job_id}/reintentar", "POST", "reintentar_sincronizacion_reservo", 202),
    ("/api/reservo/sincronizaciones/{job_id}/deshacer", "POST", "deshacer_sincronizacion_reservo", None),
    ("/api/reservo/sincronizaciones/{job_id}/pacientes.csv", "GET", "descargar_sincronizacion_reservo_csv", None),
    ("/api/reservo/bases", "GET", "listar_bases_reservo", None),
    ("/api/reservo/sincronizaciones/{job_id}/bases/previsualizar", "POST", "previsualizar_base_reservo", None),
    ("/api/reservo/sincronizaciones/{job_id}/bases", "POST", "guardar_base_reservo", None),
    ("/api/reservo/bases/{base_id}/pacientes", "GET", "listar_pacientes_base_reservo", None),
    ("/api/reservo/pacientes", "GET", "listar_pacientes_reservo", None),
)


def registrar(handlers):
    return crear_router("Pacientes Reservo", handlers, RUTAS)
