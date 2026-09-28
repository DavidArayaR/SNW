from ._registry import crear_router

RUTAS = (
    ("/api/areas", "GET", "listar_areas", None),
    ("/api/areas/mias", "GET", "mis_areas", None),
    ("/api/areas", "POST", "crear_area", 201),
    ("/api/areas/{area_id}", "PUT", "renombrar_area", None),
    ("/api/areas/{area_id}/roles", "POST", "asignar_rol_area", None),
    ("/api/areas/{area_id}/roles/{usuario}", "DELETE", "retirar_rol_area", None),
    ("/api/areas/{area_id}/pacientes/{paciente_id}", "DELETE", "eliminar_paciente_area", None),
    ("/api/areas/{area_id}/tabla", "DELETE", "eliminar_tabla_area", None),
    ("/api/areas/{area_id}/pacientes/csv", "POST", "importar_pacientes_csv", 201),
)

def registrar(handlers): return crear_router("Areas", handlers, RUTAS)
