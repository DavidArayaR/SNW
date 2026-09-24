"""Modelos de datos que reciben las rutas de la API.

Mantenerlos fuera de las rutas evita que la capa HTTP y las reglas de negocio
queden mezcladas en ``main.py``.
"""

from pydantic import BaseModel


class PlantillaIn(BaseModel):
    nombre: str
    texto: str
    clave: str | None = None
    whatsapp_template_lang: str | None = None
    whatsapp_template_categoria: str | None = None
    especialidad_id: int | None = None


class EnvioIn(BaseModel):
    pacientes: list[int] | None = None
    plantilla_id: int
    ambiente: str | None = None
    limite: int | None = None
    especialidad_id: int | None = None


class ConfigIn(BaseModel):
    entorno: str | None = None
    metodo_envio: str | None = None
    numeros_prueba_dev: list[str] | None = None
    numeros_prueba_prod: list[str] | None = None
    intervalo_ms: int | None = None
    url_base: str | None = None
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_user: str | None = None
    smtp_pass: str | None = None
    smtp_tls: bool | None = None
    correo_emisor: str | None = None
    correo_destino: str | None = None
    wa_token: str | None = None
    wa_phone_id: str | None = None
    wa_business_account_id: str | None = None
    wa_verify_token: str | None = None
    wa_template_nombre: str | None = None
    wa_template_lang: str | None = None
    wa_webhook_path: str | None = None
    wa_graph_version: str | None = None


class LoginIn(BaseModel):
    usuario: str
    clave: str


class InvitarIn(BaseModel):
    correo: str


class ActivarCuentaIn(BaseModel):
    token: str
    clave: str


class UsuarioUpdIn(BaseModel):
    rol: str | None = None
    permisos: list[str] | None = None
    nombre: str | None = None
    activo: bool | None = None


class ClavePropiaIn(BaseModel):
    clave_actual: str
    clave_nueva: str


class OlvideIn(BaseModel):
    correo: str


class ResetIn(BaseModel):
    token: str
    clave_nueva: str


class CorreoRecuperacionIn(BaseModel):
    correo: str


class ConfigTodoIn(BaseModel):
    cambios: dict[str, str]


class PruebaWAIn(BaseModel):
    telefono: str
    mensaje: str = "Mensaje de prueba del sistema SNW"


class EspecialidadIn(BaseModel):
    nombre: str
    # preguntar: si el nombre visible ya existe, no crea nada y devuelve las
    # coincidencias para que la UI pregunte. reutilizar: usa la existente.
    # nueva: crea una instancia nueva (pacientes_<slug><n>).
    modo: str | None = "preguntar"


class EspecialidadRenombrarIn(BaseModel):
    nombre_visible: str


class RolEspecialidadIn(BaseModel):
    usuario: str
