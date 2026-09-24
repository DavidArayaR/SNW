from ._registry import crear_router

RUTAS = (
    ("/api/especialidades", "GET", "listar_especialidades", None),
    ("/api/especialidades/mias", "GET", "mis_especialidades", None),
    ("/api/especialidades", "POST", "crear_especialidad", 201),
    ("/api/especialidades/{especialidad_id}", "PUT", "renombrar_especialidad", None),
    ("/api/especialidades/{especialidad_id}/roles", "POST", "asignar_rol_especialidad", None),
    ("/api/especialidades/{especialidad_id}/roles/{usuario}", "DELETE", "retirar_rol_especialidad", None),
    ("/api/especialidades/{especialidad_id}/pacientes/csv", "POST", "importar_pacientes_csv", 201),
)

def registrar(handlers): return crear_router("Especialidades", handlers, RUTAS)
