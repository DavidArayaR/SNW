"""Consulta y sincronización acotada de pacientes desde la API pública de Reservo.

El módulo no crea campañas ni envía mensajes. La selección de citas atendidas
requiere el código de estado verificado manualmente para cada clínica.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import re
import time
import unicodedata
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import httpx
import pymysql
from telefono import normalizar_telefono

from reservo_catalogo import (
    RESERVO_CLINICAS, clave_estado_atendido, clave_estado_verificado,
    clave_token,
)

API_BASE = "https://reservo.cl/APIpublica/v2/"
MAX_PAGINAS = 10000
TIMEOUT_SEGUNDOS = 20.0
MAX_REINTENTOS = 2
DIAS_TRAMO_TRATAMIENTOS = 7
VERSION_CACHE_CITAS = "v2"
TABLA_LISTA_RESERVO = "reservo_lista_pacientes"
_MESES_ES = ("enero", "febrero", "marzo", "abril", "mayo", "junio",
             "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre")


def generar_nombre_base(codigo: str, nombre_personalizado: str,
                        ahora: dt.datetime | None = None) -> dict[str, str]:
    """Normaliza el nombre lógico de una lista y calcula su sufijo chileno."""
    codigo = str(codigo or "").strip().upper()
    if codigo not in RESERVO_CLINICAS:
        raise ValueError("La clínica Reservo no es válida.")
    original = " ".join(str(nombre_personalizado or "").split()).strip()
    if not original:
        raise ValueError("Escribe un nombre para la base de datos.")
    nombre = unicodedata.normalize("NFKD", original.casefold())
    nombre = "".join(c for c in nombre if not unicodedata.combining(c))
    nombre = re.sub(r"[^a-z0-9]+", "_", nombre).strip("_")
    nombre = re.sub(r"_{2,}", "_", nombre)
    nombre = re.sub(rf"^{re.escape(codigo.casefold())}_+", "", nombre)
    nombre = re.sub(
        r"_(?:enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre)_\d{4}$",
        "", nombre,
    ).rstrip("_")
    if not nombre:
        raise ValueError("El nombre personalizado debe contener letras o números.")
    ahora = ahora or dt.datetime.now(ZoneInfo("America/Santiago"))
    if ahora.tzinfo is None:
        ahora = ahora.replace(tzinfo=ZoneInfo("America/Santiago"))
    ahora = ahora.astimezone(ZoneInfo("America/Santiago"))
    mes = _MESES_ES[ahora.month - 1]
    sufijo = f"_{mes}_{ahora.year}"
    prefijo = f"{codigo}_"
    # La columna del nombre editable admite 100 caracteres. Limitar también
    # por el nombre completo evita que una lista válida falle al persistirse.
    max_personalizado = min(100, 150 - len(prefijo) - len(sufijo))
    nombre = nombre[:max_personalizado].strip("_")
    if not nombre:
        raise ValueError("El nombre personalizado es demasiado largo.")
    return {"nombre_personalizado": nombre, "mes": mes, "anio": str(ahora.year),
            "nombre": f"{prefijo}{nombre}{sufijo}"}


def _valores_pacientes_lista(lista_id: int, pacientes: list[dict]) -> list[tuple]:
    """Copia solo pacientes con móvil chileno aceptado por telefono.py."""
    from telefono import normalizar_telefono

    valores = []
    for paciente in pacientes:
        telefono = normalizar_telefono(str(paciente.get("paciente_telefono") or ""))
        if not telefono:
            continue
        valores.append((int(lista_id), paciente["id"], paciente["cliente_uuid"],
            paciente.get("paciente_nombre"), paciente.get("paciente_apellido"), telefono, 1,
            paciente.get("tratamiento_nombre"), paciente.get("fecha_ultima_atencion")))
    return valores


def _detalle_tratamientos(job: dict) -> tuple[list[dict], str]:
    try:
        items = json.loads(job.get("tratamientos_json") or "[]")
    except (TypeError, ValueError):
        items = []
    if not isinstance(items, list):
        items = []
    items = [{"uuid": str(item.get("uuid") or ""),
              "nombre": str(item.get("nombre") or "").strip()}
             for item in items if isinstance(item, dict) and item.get("uuid")]
    if not items and job.get("tratamiento_uuid"):
        items = [{"uuid": str(job["tratamiento_uuid"]), "nombre": ""}]
    texto = ", ".join(dict.fromkeys(x["nombre"] for x in items if x["nombre"]))
    return items, texto


class ReservoError(Exception):
    """Error seguro para mostrar: nunca contiene headers ni cuerpo de respuesta."""

    def __init__(self, categoria: str, *, status_code: int | None = None):
        self.categoria = categoria
        self.status_code = status_code
        super().__init__(categoria)


def _huella_token(token: str) -> str:
    """Separa la caché al rotar credenciales sin almacenar el token."""
    return hashlib.sha256(f"{VERSION_CACHE_CITAS}:{token}".encode("utf-8")).hexdigest()


def obtener_tratamientos_cache(codigo: str, desde: dt.date, hasta: dt.date,
                               token: str) -> list[dict] | None:
    from db import conectar
    with conectar() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT tratamientos_json FROM reservo_tratamientos_cache"
            " WHERE clinica_codigo=%s AND fecha_desde=%s AND fecha_hasta=%s"
            " AND token_hash=%s AND expira_en>UTC_TIMESTAMP()",
            (codigo, desde, hasta, _huella_token(token)),
        )
        fila = cur.fetchone()
    if fila is None:
        return None
    try:
        items = json.loads(fila["tratamientos_json"])
        if isinstance(items, list) and all(
                isinstance(item, dict) and isinstance(item.get("uuid"), str)
                and isinstance(item.get("nombre"), str) for item in items):
            return items
    except (TypeError, ValueError):
        pass
    return None


def guardar_tratamientos_cache(codigo: str, desde: dt.date, hasta: dt.date,
                               token: str, items: list[dict], minutos: int,
                               paginas: int = 0, estado_atendido: str = "") -> None:
    from db import conectar
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM reservo_tratamientos_cache WHERE expira_en<=UTC_TIMESTAMP()")
        cur.execute(
            "INSERT INTO reservo_tratamientos_cache"
            " (clinica_codigo,fecha_desde,fecha_hasta,token_hash,tratamientos_json,expira_en,paginas,estado_atendido)"
            " VALUES (%s,%s,%s,%s,%s,DATE_ADD(UTC_TIMESTAMP(), INTERVAL %s MINUTE),%s,%s)"
            " ON DUPLICATE KEY UPDATE tratamientos_json=VALUES(tratamientos_json),"
            " expira_en=VALUES(expira_en),paginas=VALUES(paginas),estado_atendido=VALUES(estado_atendido)",
            (codigo, desde, hasta, _huella_token(token),
             json.dumps(items, ensure_ascii=False), minutos, paginas, estado_atendido),
        )
        if paginas:
            cur.execute(
                "UPDATE reservo_citas_cache SET expira_en=DATE_ADD(UTC_TIMESTAMP(), INTERVAL %s MINUTE)"
                " WHERE clinica_codigo=%s AND fecha_desde=%s AND fecha_hasta=%s AND token_hash=%s",
                (minutos, codigo, desde, hasta, _huella_token(token)),
            )
        conn.commit()


def _cita_para_cache(cita: object, estado_atendido: str) -> dict:
    if not isinstance(cita, dict):
        return {}
    estado = codigo_estado(cita)
    resultado = {"estado": {"codigo": estado}, "tratamientos": tratamientos_cita(cita)}
    if estado_atendido and estado == estado_atendido:
        fecha = fecha_atencion_utc(cita)
        if fecha:
            resultado["inicio"] = fecha.isoformat() + "Z"
        cliente = cliente_valido(cita)
        if cliente:
            resultado["cliente"] = {
                "uuid": cliente["uuid"],
                "nombre": texto_cliente(cliente, "nombre", 200),
                "apellido_paterno": (texto_cliente(cliente, "apellido_paterno", 200)
                                     or texto_cliente(cliente, "apellidos", 200)
                                     or texto_cliente(cliente, "apellido", 200)),
                "telefono_1": telefono_cliente(cliente),
            }
    return resultado


def preparar_cache_citas(codigo: str, desde: dt.date, hasta: dt.date, token: str) -> bool:
    from db import conectar
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM reservo_sincronizaciones WHERE clinica_codigo=%s"
                    " AND fecha_desde=%s AND fecha_hasta=%s"
                    " AND estado IN ('pendiente','ejecutando') LIMIT 1",
                    (codigo, desde, hasta))
        if cur.fetchone():
            return False
        cur.execute("DELETE FROM reservo_citas_cache WHERE expira_en<=UTC_TIMESTAMP()")
        cur.execute("DELETE FROM reservo_citas_cache WHERE clinica_codigo=%s"
                    " AND fecha_desde=%s AND fecha_hasta=%s AND token_hash=%s",
                    (codigo, desde, hasta, _huella_token(token)))
        conn.commit()
    return True


def guardar_pagina_citas_cache(codigo: str, desde: dt.date, hasta: dt.date,
                               token: str, numero: int, tramo_desde: dt.date,
                               tramo_hasta: dt.date, pagina: int, citas: list,
                               estado_atendido: str) -> None:
    from db import conectar
    minimo = [_cita_para_cache(cita, estado_atendido) for cita in citas]
    from supresion import (bloquear_huella_baja, limpiar_cerrojos_baja_inactivos)
    huellas_temporales: list[str] = []
    with conectar() as conn, conn.cursor() as cur:
        for cita in minimo:
            cliente = cita.get("cliente") if isinstance(cita, dict) else None
            telefono = (cliente or {}).get("telefono_1") if isinstance(cliente, dict) else None
            if not telefono:
                cita["cliente"] = None
                continue
            telefono = normalizar_telefono(str(telefono))
            if not telefono:
                # Conserva datos de la cita/tratamiento, pero no PII de un
                # paciente que no se puede importar ni comparar con supresión.
                cita["cliente"] = None
                continue
            cliente["telefono_1"] = telefono
            huella, baja = bloquear_huella_baja(cur, telefono)
            if not baja:
                huellas_temporales.append(huella)
            cur.execute("SELECT estado FROM supresion_telefonos WHERE huella=%s FOR UPDATE",
                        (huella,))
            supresion = cur.fetchone()
            cur.execute("SELECT 1 FROM whatsapp_avisos_unicos"
                        " WHERE telefono=%s AND tipo='eliminacion_simulada' LIMIT 1",
                        (telefono,))
            eliminacion_simulada = cur.fetchone() is not None
            if (baja or eliminacion_simulada
                    or (supresion and supresion.get("estado") != "exento")):
                # Conserva el dato de cita/tratamiento, pero no cachea PII del
                # paciente que está de baja o tiene una eliminación pendiente.
                cita["cliente"] = None
        cur.execute(
            "INSERT INTO reservo_citas_cache"
            " (clinica_codigo,fecha_desde,fecha_hasta,token_hash,numero,tramo_desde,tramo_hasta,pagina,citas_json,expira_en)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,DATE_ADD(UTC_TIMESTAMP(), INTERVAL 1 DAY))",
            (codigo, desde, hasta, _huella_token(token), numero, tramo_desde,
             tramo_hasta, pagina, json.dumps(minimo, ensure_ascii=False)),
        )
        conn.commit()
        # La página de caché ya quedó persistida. La limpieza de cerrojos es
        # secundaria y se ejecuta en otra transacción para que un deadlock al
        # borrar filas inactivas no revierta la página recién guardada.
        if huellas_temporales:
            try:
                limpiar_cerrojos_baja_inactivos(cur, huellas_temporales)
                conn.commit()
            except pymysql.err.OperationalError as exc:
                conn.rollback()
                if not exc.args or exc.args[0] != 1213:
                    raise
                print(
                    "[WARN] Reservo: la página de caché se guardó; se aplazó la "
                    "limpieza de cerrojos de baja por un deadlock de MySQL (1213).",
                    flush=True,
                )


def paginas_citas_cache(codigo: str, desde: dt.date, hasta: dt.date,
                        token: str, estado_atendido: str, *, reservar: bool = False) -> int:
    from db import conectar
    with conectar() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT c.paginas,COUNT(p.numero) AS disponibles"
            " FROM reservo_tratamientos_cache c LEFT JOIN reservo_citas_cache p"
            " ON p.clinica_codigo=c.clinica_codigo AND p.fecha_desde=c.fecha_desde"
            " AND p.fecha_hasta=c.fecha_hasta AND p.token_hash=c.token_hash"
            " AND p.expira_en>UTC_TIMESTAMP()"
            " WHERE c.clinica_codigo=%s AND c.fecha_desde=%s AND c.fecha_hasta=%s"
            " AND c.token_hash=%s AND c.estado_atendido=%s"
            " AND c.expira_en>UTC_TIMESTAMP() GROUP BY c.paginas",
            (codigo, desde, hasta, _huella_token(token), estado_atendido),
        )
        fila = cur.fetchone()
        if fila and int(fila["paginas"] or 0) > 0 and int(fila["disponibles"]) == int(fila["paginas"]):
            if reservar:
                cur.execute("UPDATE reservo_citas_cache SET expira_en=DATE_ADD(UTC_TIMESTAMP(), INTERVAL 1 DAY)"
                            " WHERE clinica_codigo=%s AND fecha_desde=%s AND fecha_hasta=%s"
                            " AND token_hash=%s AND expira_en>UTC_TIMESTAMP()",
                            (codigo, desde, hasta, _huella_token(token)))
                conn.commit()
            return int(fila["paginas"])
    return 0


def iterar_citas_cache(codigo: str, desde: dt.date, hasta: dt.date, token: str,
                      tramo_desde: dt.date, tramo_hasta: dt.date):
    from db import conectar
    with conectar() as conn, conn.cursor(pymysql.cursors.SSDictCursor) as cur:
        cur.execute(
            "SELECT tramo_desde,tramo_hasta,pagina,citas_json FROM reservo_citas_cache"
            " WHERE clinica_codigo=%s AND fecha_desde=%s AND fecha_hasta=%s"
            " AND token_hash=%s AND tramo_desde=%s AND tramo_hasta=%s"
            " AND expira_en>UTC_TIMESTAMP() ORDER BY numero",
            (codigo, desde, hasta, _huella_token(token), tramo_desde, tramo_hasta),
        )
        for fila in cur:
            yield (fila["tramo_desde"], fila["tramo_hasta"], fila["pagina"],
                   json.loads(fila["citas_json"]))


def validar_uuid(valor: str) -> str:
    valor = str(valor or "").strip()
    # Reservo expone UUID como texto. Se aceptan las representaciones estándar
    # con guiones y la forma hexadecimal de 32 caracteres.
    if not (re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", valor)
            or re.fullmatch(r"[0-9a-fA-F]{32}", valor)):
        raise ValueError("El UUID debe tener 32 caracteres hexadecimales, con o sin guiones.")
    return valor.lower()


def dividir_periodo(desde: dt.date, hasta: dt.date, *, dias_por_tramo: int = 31) -> list[tuple[dt.date, dt.date]]:
    """Divide un período inclusivo en bloques de hasta 31 días."""
    if not isinstance(desde, dt.date) or not isinstance(hasta, dt.date):
        raise ValueError("Las fechas no son válidas.")
    if desde > hasta:
        raise ValueError("La fecha inicial no puede ser posterior a la fecha final.")
    if not 1 <= dias_por_tramo <= 31:
        raise ValueError("El tramo debe tener entre 1 y 31 días.")
    bloques = []
    inicio = desde
    while inicio <= hasta:
        fin = min(inicio + dt.timedelta(days=dias_por_tramo - 1), hasta)
        bloques.append((inicio, fin))
        inicio = fin + dt.timedelta(days=1)
    return bloques


def codigo_estado(cita: dict) -> str:
    estado = cita.get("estado")
    if not isinstance(estado, dict) or estado.get("codigo") is None:
        return ""
    return str(estado["codigo"]).strip()


def fecha_atencion_utc(cita: dict) -> dt.datetime | None:
    """Convierte `inicio` de Reservo a UTC; omite fechas sin zona horaria."""
    valor = cita.get("inicio")
    if not isinstance(valor, str) or len(valor) > 40:
        return None
    try:
        fecha = dt.datetime.fromisoformat(valor.strip().replace("Z", "+00:00"))
        if fecha.tzinfo is None:
            return None
        return fecha.astimezone(dt.timezone.utc).replace(tzinfo=None)
    except (ValueError, OverflowError):
        return None


def uuid_tratamientos(cita: dict) -> set[str]:
    tratamientos = cita.get("tratamientos")
    if not isinstance(tratamientos, list):
        return set()
    return {str(item.get("uuid") or "").strip().lower()
            for item in tratamientos if isinstance(item, dict) and item.get("uuid")}


def tratamientos_cita(cita: dict) -> list[dict]:
    """Extrae pares internos UUID/nombre de todos los tratamientos de una cita."""
    elementos = cita.get("tratamientos")
    if not isinstance(elementos, list):
        return []
    resultado, vistos = [], set()
    for elemento in elementos:
        if not isinstance(elemento, dict):
            continue
        try:
            uuid = validar_uuid(elemento.get("uuid") or "")
        except ValueError:
            continue
        nombre = texto_cliente(elemento, "nombre", 200)
        if not nombre or uuid in vistos:
            continue
        vistos.add(uuid)
        resultado.append({"uuid": uuid, "nombre": nombre})
    return resultado


def normalizar_tratamientos_seleccionados(elementos) -> list[dict]:
    if not isinstance(elementos, list) or not elementos or len(elementos) > 50:
        raise ValueError("Selecciona al menos un tratamiento (máximo 50).")
    normalizados, vistos = [], set()
    for elemento in elementos:
        if not isinstance(elemento, dict):
            raise ValueError("La selección de tratamientos no es válida.")
        try:
            uuid = validar_uuid(elemento.get("uuid") or "")
        except ValueError as exc:
            raise ValueError("Uno de los tratamientos seleccionados no es válido.") from exc
        nombre = texto_cliente(elemento, "nombre", 200)
        if not nombre:
            raise ValueError("Uno de los tratamientos seleccionados no tiene nombre.")
        if uuid not in vistos:
            vistos.add(uuid)
            normalizados.append({"uuid": uuid, "nombre": nombre})
    if not normalizados:
        raise ValueError("Selecciona al menos un tratamiento.")
    return normalizados


def tratamientos_del_trabajo(job: dict) -> list[dict]:
    valor = job.get("tratamientos_json")
    if valor:
        try:
            return normalizar_tratamientos_seleccionados(json.loads(valor))
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
    # Compatibilidad con trabajos antiguos creados antes de la selección múltiple.
    uuid = str(job.get("tratamiento_uuid") or "").strip()
    try:
        uuid = validar_uuid(uuid)
    except ValueError:
        return []
    return [{"uuid": uuid, "nombre": ""}]


def cliente_valido(cita: dict) -> dict | None:
    cliente = cita.get("cliente")
    if not isinstance(cliente, dict):
        return None
    uuid = str(cliente.get("uuid") or "").strip()
    if not uuid or len(uuid) > 80 or any(ord(c) < 32 for c in uuid):
        return None
    telefono = normalizar_telefono(str(telefono_cliente(cliente) or ""))
    if not telefono:
        return None
    resultado = dict(cliente)
    resultado["telefono_1"] = telefono
    return resultado


def normalizar_cliente_uuid(valor: str) -> str:
    """Normaliza UUID hexadecimales sin alterar otros identificadores válidos."""
    texto = str(valor or "").strip()
    try:
        return validar_uuid(texto)
    except ValueError:
        return texto


def texto_cliente(cliente: dict, clave: str, maximo: int) -> str | None:
    valor = cliente.get(clave)
    if valor is None:
        return None
    texto = " ".join(str(valor).split()).strip()
    if not texto or len(texto) > maximo or any(ord(c) < 32 for c in texto):
        return None
    return texto


def _clave_campo_normalizada(clave: object) -> str:
    texto = unicodedata.normalize("NFKD", str(clave or ""))
    texto = "".join(caracter for caracter in texto if not unicodedata.combining(caracter))
    return re.sub(r"[^a-z0-9]", "", texto.casefold())


def _valores_telefono(valor):
    if isinstance(valor, dict):
        claves_numero = {"telefono", "telefono1", "numero", "numerotelefono", "valor",
                         "celular", "phone", "number"}
        for clave, contenido in valor.items():
            if _clave_campo_normalizada(clave) in claves_numero and contenido is not None:
                yield contenido
    elif isinstance(valor, (list, tuple)):
        for elemento in valor:
            yield from _valores_telefono(elemento)
    elif valor is not None:
        yield valor


def telefono_cliente(cliente: dict) -> str | None:
    """Acepta el teléfono simple y campos como «telefono 1»/«teléfono 2»."""
    campos = {_clave_campo_normalizada(clave): valor for clave, valor in cliente.items()}
    preferidos = (
        "telefono1", "telefono", "telefono2", "telefono3", "telefono4",
        "telefonoprincipal", "telefonocontacto", "celular", "celular1", "celular2",
        "movil", "movil1", "movil2", "fono", "fono1", "phone", "phone1", "phone2",
        "telefonos", "phones",
    )
    for clave in preferidos:
        if clave not in campos:
            continue
        for valor in _valores_telefono(campos[clave]):
            texto = " ".join(str(valor).split()).strip()
            if texto and len(texto) <= 40 and not any(ord(caracter) < 32 for caracter in texto):
                return texto
    return None


class ClienteReservo:
    """Cliente HTTP de solo lectura con autenticación propia de cada clínica."""

    def __init__(self, token: str, *, transport=None, sleeper=time.sleep):
        if not token:
            raise ReservoError("Falta el token API de Reservo para esta clínica.")
        self._cliente = httpx.Client(
            base_url=API_BASE,
            headers={"Authorization": f"Token {token}", "Accept": "application/json"},
            timeout=TIMEOUT_SEGUNDOS,
            follow_redirects=False,
            transport=transport,
        )
        self._sleeper = sleeper

    def close(self):
        self._cliente.close()

    def _pagina_siguiente(self, valor, pagina_actual: int) -> int | None:
        if not valor:
            return None
        if isinstance(valor, int) or str(valor).isdigit():
            siguiente = int(valor)
        else:
            url = urlparse(str(valor))
            if (url.scheme != "https" or url.hostname != "reservo.cl"
                    or not url.path.rstrip("/").endswith("/citas")):
                raise ReservoError("Reservo devolvió una referencia de paginación inesperada.")
            try:
                siguiente = int((parse_qs(url.query).get("pagina") or [""])[0])
            except ValueError as exc:
                raise ReservoError("Reservo devolvió una página inválida.") from exc
        if siguiente <= pagina_actual:
            raise ReservoError("La paginación de Reservo no avanzó; se detuvo para evitar un ciclo.")
        return siguiente

    def obtener_pagina(self, desde: dt.date, hasta: dt.date, pagina: int = 1) -> dict:
        parametros = {"fecha_inicial": desde.isoformat(), "fecha_final": hasta.isoformat(), "pagina": pagina}
        for intento in range(MAX_REINTENTOS + 1):
            try:
                respuesta = self._cliente.get("citas/", params=parametros)
            except httpx.TimeoutException as exc:
                if intento < MAX_REINTENTOS:
                    self._sleeper(min(2 ** intento, 4))
                    continue
                raise ReservoError("Reservo no respondió antes del tiempo límite.") from exc
            except httpx.RequestError as exc:
                if intento < MAX_REINTENTOS:
                    self._sleeper(min(2 ** intento, 4))
                    continue
                raise ReservoError("No se pudo conectar con Reservo.") from exc

            if respuesta.status_code == 429 or respuesta.status_code >= 500:
                if intento < MAX_REINTENTOS:
                    espera = respuesta.headers.get("Retry-After", "")
                    try:
                        segundos = max(1, min(30, int(espera))) if espera else 2 ** intento
                    except ValueError:
                        segundos = 2 ** intento
                    self._sleeper(segundos)
                    continue
            if respuesta.status_code in (401, 403):
                raise ReservoError("Reservo rechazó el token o sus permisos.", status_code=respuesta.status_code)
            if respuesta.status_code == 429:
                raise ReservoError("Reservo mantiene temporalmente el límite de solicitudes.", status_code=429)
            if respuesta.status_code >= 500:
                raise ReservoError("Reservo presenta un error temporal.", status_code=respuesta.status_code)
            if not respuesta.is_success:
                raise ReservoError("Reservo rechazó la consulta.", status_code=respuesta.status_code)
            try:
                datos = respuesta.json()
            except ValueError as exc:
                raise ReservoError("Reservo devolvió una respuesta que no es JSON.") from exc
            if not isinstance(datos, dict) or not isinstance(datos.get("resultados"), list):
                raise ReservoError("La respuesta de Reservo no tiene el formato esperado.")
            return datos
        raise ReservoError("No se pudo completar la consulta a Reservo.")

    def iterar_citas(self, desde: dt.date, hasta: dt.date, *, dias_por_tramo: int = 31):
        for inicio, fin in dividir_periodo(desde, hasta, dias_por_tramo=dias_por_tramo):
            pagina = 1
            visitadas = set()
            while pagina:
                if pagina in visitadas or len(visitadas) >= MAX_PAGINAS:
                    raise ReservoError("La paginación de Reservo no terminó correctamente.")
                visitadas.add(pagina)
                datos = self.obtener_pagina(inicio, fin, pagina)
                yield inicio, fin, pagina, datos["resultados"]
                pagina = self._pagina_siguiente(datos.get("pagina_siguiente"), pagina)

    def listar_tratamientos(self, desde: dt.date, hasta: dt.date, *, al_recibir_pagina=None) -> list[dict]:
        """Consulta citas y devuelve solo el catálogo de tratamientos observado."""
        encontrados = {}
        for _inicio, _fin, _pagina, citas in self.iterar_citas(
                desde, hasta, dias_por_tramo=DIAS_TRAMO_TRATAMIENTOS):
            if al_recibir_pagina:
                al_recibir_pagina(_inicio, _fin, _pagina, citas)
            for cita in citas:
                if not isinstance(cita, dict):
                    continue
                for tratamiento in tratamientos_cita(cita):
                    encontrados.setdefault(tratamiento["uuid"], tratamiento)
        return sorted(encontrados.values(), key=lambda item: item["nombre"].casefold())

    def probar_conexion(self, fecha: dt.date) -> dict:
        datos = self.obtener_pagina(fecha, fecha, 1)
        estados: dict[tuple[str, str], int] = {}
        tratamientos = {}
        for cita in datos["resultados"]:
            if isinstance(cita, dict):
                for tratamiento in tratamientos_cita(cita):
                    tratamientos.setdefault(tratamiento["uuid"], tratamiento["nombre"])
            estado = cita.get("estado") if isinstance(cita, dict) else None
            if not isinstance(estado, dict):
                continue
            codigo = str(estado.get("codigo") or "").strip()
            descripcion = " ".join(str(estado.get("descripcion") or "").split())[:80]
            if codigo:
                estados[(codigo, descripcion)] = estados.get((codigo, descripcion), 0) + 1
        return {"ok": True, "citas_en_muestra": len(datos["resultados"]),
                "estados_observados": [{"codigo": codigo, "descripcion": descripcion, "cantidad": cantidad}
                                       for (codigo, descripcion), cantidad in sorted(estados.items())],
                "tratamientos_observados": sorted(tratamientos.values(), key=str.casefold)}


def preparar_sincronizacion(codigo: str, desde: dt.date, hasta: dt.date,
                            config: dict) -> dict:
    if codigo not in RESERVO_CLINICAS:
        raise ValueError("Selecciona una clínica Reservo válida.")
    token = str(config.get(clave_token(codigo)) or "").strip()
    if not token:
        raise ValueError(f"Falta el token API de Reservo para {RESERVO_CLINICAS[codigo]}.")
    estado_code = str(config.get(clave_estado_atendido(codigo)) or "").strip()
    if not estado_code or str(config.get(clave_estado_verificado(codigo)) or "").lower() not in ("true", "1", "si", "sí"):
        raise ValueError("Antes de sincronizar, verifica el código «Atendido» en una cita real y confírmalo en Configuración → Integraciones.")
    bloques = dividir_periodo(desde, hasta, dias_por_tramo=DIAS_TRAMO_TRATAMIENTOS)
    return {"clinica_codigo": codigo, "clinica_nombre": RESERVO_CLINICAS[codigo],
            "token": token, "estado_atendido_codigo": estado_code, "bloques": bloques}


def coincide_cita(cita: dict, estado_atendido: str, tratamiento_uuid: str) -> bool:
    return (codigo_estado(cita) == str(estado_atendido).strip()
            and tratamiento_uuid.lower() in uuid_tratamientos(cita))


def resumen_error_seguro(exc: BaseException) -> str:
    """Expone solo errores de integración conocidos; nunca texto arbitrario de SQL/API."""
    if isinstance(exc, ReservoError):
        return exc.categoria[:240]
    if isinstance(exc, ValueError):
        # Los ValueError del flujo son mensajes locales de validación, no incluyen
        # cuerpos externos ni valores de pacientes.
        return str(exc)[:240]
    return f"Error interno de sincronización ({type(exc).__name__})."


def _db():
    from db import conectar
    return conectar()


def _obtener_job_base(cur, job_id: int, *, bloquear: bool = False) -> dict:
    cur.execute(
        "SELECT id,clinica_codigo,tratamiento_uuid,tratamientos_json,fecha_desde,fecha_hasta,"
        "estado,creado_por FROM reservo_sincronizaciones WHERE id=%s"
        + (" FOR UPDATE" if bloquear else ""), (int(job_id),),
    )
    job = cur.fetchone()
    if not job:
        raise LookupError("No se encontró la sincronización de Reservo.")
    if job["estado"] not in ("completado", "completado_con_advertencias"):
        raise ValueError("Solo puedes guardar una sincronización terminada correctamente.")
    if job["clinica_codigo"] not in RESERVO_CLINICAS:
        raise ValueError("La sincronización no tiene una clínica válida.")
    return job


def previsualizar_base_contactos(job_id: int, nombre_personalizado: str,
                                 ahora: dt.datetime | None = None) -> dict:
    """Resumen de la cohorte ya sincronizada; no vuelve a consultar Reservo."""
    from telefono import normalizar_telefono

    with _db() as conn, conn.cursor() as cur:
        job = _obtener_job_base(cur, job_id)
        nombre = generar_nombre_base(job["clinica_codigo"], nombre_personalizado, ahora)
        cur.execute(
            "SELECT p.paciente_telefono FROM reservo_sincronizacion_pacientes sp"
            " JOIN reservo_pacientes p ON p.id=sp.paciente_id"
            " WHERE sp.job_id=%s", (int(job_id),),
        )
        telefonos = [str(row.get("paciente_telefono") or "").strip() for row in cur.fetchall()]
    validos = sum(bool(normalizar_telefono(tel)) for tel in telefonos)
    tratamientos, tratamiento_texto = _detalle_tratamientos(job)
    return {
        **nombre,
        "clinica_codigo": job["clinica_codigo"],
        "clinica_nombre": RESERVO_CLINICAS[job["clinica_codigo"]],
        "tratamientos": tratamientos,
        "tratamiento_nombre": tratamiento_texto or "Tratamiento histórico",
        "fecha_desde": str(job["fecha_desde"]),
        "fecha_hasta": str(job["fecha_hasta"]),
        "total": len(telefonos),
        "telefonos_validos": validos,
        "sin_telefono": sum(not tel for tel in telefonos),
        "telefonos_invalidos": sum(bool(tel) and not normalizar_telefono(tel) for tel in telefonos),
    }


def guardar_base_contactos(job_id: int, nombre_personalizado: str, creado_por: str,
                           ahora: dt.datetime | None = None) -> dict:
    """Guarda una lista lógica y su cohorte en tablas fijas, sin tabla dinámica."""
    lock_name = "snw-reservo-listas"
    with _db() as conn, conn.cursor() as cur:
        cur.execute("SELECT GET_LOCK(%s, 0) AS adquirido", (lock_name,))
        if not (cur.fetchone() or {}).get("adquirido"):
            raise RuntimeError("Hay otra lista de Reservo guardándose. Inténtalo nuevamente.")
        try:
            job = _obtener_job_base(cur, job_id, bloquear=True)
            nombre = generar_nombre_base(job["clinica_codigo"], nombre_personalizado, ahora)
            tratamientos, _ = _detalle_tratamientos(job)
            if not tratamientos:
                raise ValueError("La sincronización no conserva los tratamientos seleccionados.")
            uids = [item["uuid"] for item in tratamientos]
            placeholders = ",".join("%s" for _ in uids)
            cur.execute(
                "SELECT p.id,p.cliente_uuid,p.paciente_nombre,p.paciente_apellido,"
                "p.paciente_telefono,p.fecha_ultima_atencion,"
                "GROUP_CONCAT(DISTINCT t.tratamiento_nombre ORDER BY t.tratamiento_nombre SEPARATOR ', ')"
                " AS tratamiento_nombre FROM reservo_sincronizacion_pacientes sp"
                " JOIN reservo_pacientes p ON p.id=sp.paciente_id"
                " LEFT JOIN reservo_paciente_tratamientos t ON t.paciente_id=p.id"
                f" AND t.tratamiento_uuid IN ({placeholders})"
                " WHERE sp.job_id=%s GROUP BY p.id,p.cliente_uuid,p.paciente_nombre,"
                "p.paciente_apellido,p.paciente_telefono,p.fecha_ultima_atencion ORDER BY p.id",
                (*uids, int(job_id)),
            )
            pacientes = cur.fetchall()
            if not pacientes:
                raise ValueError("La sincronización no contiene pacientes para guardar.")
            base = nombre["nombre"]
            secuencia = 1
            while True:
                cur.execute("SELECT id FROM reservo_bases_contacto WHERE nombre=%s", (base,))
                if not cur.fetchone():
                    break
                secuencia += 1
                sufijo = f"_{secuencia}"
                base = nombre["nombre"][:150-len(sufijo)].rstrip("_") + sufijo
            cur.execute(
                "INSERT INTO reservo_bases_contacto"
                " (nombre_personalizado,nombre,clinica_codigo,origen,sync_job_id,tratamientos_json,"
                "fecha_desde,fecha_hasta,mes,anio,creado_por)"
                " VALUES (%s,%s,%s,'RESERVO',%s,%s,%s,%s,%s,%s,%s)",
                (nombre["nombre_personalizado"], base, job["clinica_codigo"], int(job_id),
                 json.dumps(tratamientos, ensure_ascii=False), job["fecha_desde"], job["fecha_hasta"],
                 nombre["mes"], int(nombre["anio"]), (creado_por or "")[:150]),
            )
            lista_id = int(cur.lastrowid)
            valores = _valores_pacientes_lista(lista_id, pacientes)
            if not valores:
                raise ValueError("La consulta no contiene pacientes con teléfono chileno válido.")
            for inicio in range(0, len(valores), 1000):
                cur.executemany(
                    "INSERT INTO reservo_lista_pacientes"
                    " (lista_id,paciente_id,cliente_uuid,nombre,apellido,telefono,telefono_valido,"
                    "tratamiento_ultima_atencion,fecha_ultima_atencion,estado,consentimiento_verificado)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'pendiente',1)",
                    valores[inicio:inicio+1000],
                )
            conn.commit()
            return {"id": lista_id, "nombre": base, "nombre_personalizado": nombre["nombre_personalizado"],
                    "clinica_codigo": job["clinica_codigo"],
                    "clinica_nombre": RESERVO_CLINICAS[job["clinica_codigo"]],
                    "total": len(valores),
                    "telefonos_validos": len(valores),
                    "telefonos_excluidos": len(pacientes) - len(valores)}
        finally:
            cur.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))


def listar_bases_contacto() -> list[dict]:
    with _db() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT b.id,b.nombre,b.area_id,b.nombre_personalizado,b.clinica_codigo,b.origen,"
            "b.sync_job_id,b.tratamientos_json,b.fecha_desde,b.fecha_hasta,b.creado_por,b.creado_en,"
            "COUNT(p.id) AS total,"
            "SUM(CASE WHEN p.telefono_valido=1 THEN 1 ELSE 0 END) AS telefonos_validos,"
            "SUM(CASE WHEN p.telefono_valido=0 THEN 1 ELSE 0 END) AS telefonos_invalidos "
            "FROM reservo_bases_contacto b LEFT JOIN reservo_lista_pacientes p ON p.lista_id=b.id "
            "WHERE b.area_id IS NULL OR EXISTS (SELECT 1 FROM areas a WHERE a.id=b.area_id) "
            "GROUP BY b.id ORDER BY b.creado_en DESC,b.id DESC"
        )
        items = cur.fetchall()
    for item in items:
        if item.get('area_id'):
            import servicio_areas
            area = servicio_areas.obtener_area(item['area_id'])
            item['nombre_tabla_base'] = (area or {}).get('nombre_tabla_base')
            item['total'] = (area or {}).get('total_pacientes', 0)
        tratamientos, nombres = _detalle_tratamientos({"tratamientos_json": item.pop("tratamientos_json")})
        item["tratamientos"] = tratamientos
        item["tratamiento_nombre"] = nombres or "Tratamiento histórico"
        item["clinica_nombre"] = RESERVO_CLINICAS.get(item["clinica_codigo"], item["clinica_codigo"])
    return items


def listar_pacientes_base_contacto(lista_id: int, pagina: int = 1,
                                   por_pagina: int = 25, q: str = "",
                                   estado: str = "todos", respuesta: str = "todas",
                                   interes: str = "todos") -> dict:
    pagina, por_pagina = max(1, int(pagina)), min(100, max(1, int(por_pagina)))
    if estado not in ("todos", "pendiente", "enviado", "error") or \
       respuesta not in ("todas", "pendiente", "respondio", "no_interesado", "baja") or \
       interes not in ("todos", "interesado", "no_interesado"):
        raise ValueError("Filtro de pacientes inválido.")
    termino = " ".join(str(q or "").split()).strip()[:100]
    condiciones = ["lista_id=%s"]
    parametros: list = [int(lista_id)]
    if termino:
        condiciones.append(
            "(LOWER(CONCAT_WS(' ',COALESCE(nombre,''),COALESCE(apellido,''),"
            "COALESCE(telefono,''),CONVERT(cliente_uuid USING utf8mb4)))"
            " COLLATE utf8mb4_unicode_ci LIKE %s)"
        )
        parametros.append(f"%{termino.casefold()}%")
    if estado != "todos":
        condiciones.append("COALESCE(NULLIF(estado,''),'pendiente')=%s")
        parametros.append(estado)
    respuesta_sql = (
        "CASE WHEN whatsapp_opt_out=1 THEN 'baja' "
        "WHEN no_interesado=1 THEN 'no_interesado' "
        "WHEN interesado=1 THEN 'interesado' "
        "WHEN respuesta_manual IS NOT NULL AND respuesta_manual<>'' THEN respuesta_manual "
        "ELSE 'pendiente' END"
    )
    if respuesta != "todas":
        condiciones.append(f"({respuesta_sql})=%s")
        parametros.append(respuesta)
    if interes != "todos":
        condiciones.append("COALESCE(interesado,0)=1" if interes == "interesado"
                           else "COALESCE(no_interesado,0)=1")
    filtro = " AND ".join(condiciones)
    with _db() as conn, conn.cursor() as cur:
        cur.execute("SELECT id,nombre FROM reservo_bases_contacto WHERE id=%s", (int(lista_id),))
        base = cur.fetchone()
        if not base:
            raise LookupError("No se encontró la base de Reservo.")
        cur.execute(f"SELECT COUNT(*) AS total FROM reservo_lista_pacientes WHERE {filtro}", parametros)
        total = int((cur.fetchone() or {}).get("total") or 0)
        cur.execute(
            "SELECT COUNT(*) AS total,"
            "SUM(CASE WHEN COALESCE(NULLIF(estado,''),'pendiente')='pendiente' THEN 1 ELSE 0 END) AS pendientes,"
            "SUM(CASE WHEN estado='enviado' THEN 1 ELSE 0 END) AS enviados,"
            "SUM(CASE WHEN estado='error' THEN 1 ELSE 0 END) AS errores,"
            f"SUM(CASE WHEN ({respuesta_sql})='pendiente' THEN 1 ELSE 0 END) AS sin_respuesta,"
            f"SUM(CASE WHEN ({respuesta_sql})='baja' THEN 1 ELSE 0 END) AS bajas,"
            "SUM(CASE WHEN interesado=1 THEN 1 ELSE 0 END) AS interesados,"
            "SUM(CASE WHEN no_interesado=1 THEN 1 ELSE 0 END) AS no_interesados "
            "FROM reservo_lista_pacientes WHERE lista_id=%s", (int(lista_id),)
        )
        resumen = cur.fetchone() or {}
        cur.execute(
            "SELECT id,cliente_uuid,nombre,apellido,telefono,telefono_valido,"
            "tratamiento_ultima_atencion,fecha_ultima_atencion,estado,whatsapp_opt_out,"
            "respuesta_manual,interesado,no_interesado,fecha_actualizacion "
            f"FROM reservo_lista_pacientes WHERE {filtro} ORDER BY id LIMIT %s OFFSET %s",
            (*parametros, por_pagina, (pagina-1)*por_pagina),
        )
        filas = cur.fetchall()
    for fila in filas:
        if fila.get("whatsapp_opt_out"):
            fila["respuesta"] = "baja"
        elif fila.get("no_interesado"):
            fila["respuesta"] = "no_interesado"
        elif fila.get("interesado"):
            fila["respuesta"] = "interesado"
        else:
            fila["respuesta"] = fila.get("respuesta_manual") or "pendiente"
    return {"base": base, "items": filas, "total": total, "pagina": pagina,
            "por_pagina": por_pagina, "paginas": max(1, (total + por_pagina - 1)//por_pagina),
            "resumen": {clave: int(valor or 0) for clave, valor in resumen.items()}}


def crear_sincronizacion(codigo: str, desde: dt.date, hasta: dt.date,
                          usuario: str, config: dict, tratamientos: list[dict]) -> dict:
    preparada = preparar_sincronizacion(codigo, desde, hasta, config)
    seleccionados = normalizar_tratamientos_seleccionados(tratamientos)
    # MySQL advisory lock evita que dos instancias encolen trabajos solapados
    # para la misma clínica entre la comprobación y la inserción.
    lock_name = f"snw-reservo-{codigo}"
    with _db() as conn, conn.cursor() as cur:
        cur.execute("SELECT GET_LOCK(%s, 0) AS adquirido", (lock_name,))
        if not (cur.fetchone() or {}).get("adquirido"):
            raise RuntimeError("Ya se está iniciando otra sincronización de esta clínica.")
        try:
            cur.execute("SELECT id FROM reservo_sincronizaciones WHERE clinica_codigo=%s"
                        " AND estado IN ('pendiente','ejecutando') LIMIT 1 FOR UPDATE", (codigo,))
            if cur.fetchone():
                raise RuntimeError("Ya hay una sincronización de esta clínica pendiente o en curso.")
            cur.execute(
                "INSERT INTO reservo_sincronizaciones"
                " (clinica_codigo, tratamiento_uuid, tratamientos_json, estado_atendido_codigo,"
                " fecha_desde, fecha_hasta, estado, periodos_total, creado_por)"
                " VALUES (%s,%s,%s,%s,%s,%s,'pendiente',%s,%s)",
                (codigo, seleccionados[0]["uuid"], json.dumps(seleccionados, ensure_ascii=False),
                 preparada["estado_atendido_codigo"], desde, hasta, len(preparada["bloques"]),
                 (usuario or "")[:150]),
            )
            job_id = cur.lastrowid
            conn.commit()
        finally:
            cur.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
    return obtener_sincronizacion(job_id)


_SELECT_SINCRONIZACIONES = (
    "SELECT id,clinica_codigo,tratamiento_uuid,estado_atendido_codigo,fecha_desde,fecha_hasta,estado,"
    "periodos_total,periodos_completados,paginas_consultadas,citas_recibidas,"
    "citas_atendidas,citas_tratamiento,pacientes_unicos,pacientes_nuevos,"
    "pacientes_actualizados,pacientes_descartados,pacientes_duplicados,"
    "pacientes_revertidos,errores,"
    "ultimo_error,creado_por,creado_en,iniciado_en,terminado_en,heartbeat_en,tratamientos_json "
    "FROM reservo_sincronizaciones"
)


def obtener_sincronizacion(job_id: int) -> dict | None:
    with _db() as conn, conn.cursor() as cur:
        cur.execute(_SELECT_SINCRONIZACIONES + " WHERE id=%s", (job_id,))
        fila = cur.fetchone()
    return _presentar_sincronizacion(fila) if fila else None


def _presentar_sincronizacion(fila: dict) -> dict:
    fila["clinica_nombre"] = RESERVO_CLINICAS.get(fila["clinica_codigo"], fila["clinica_codigo"])
    fila["tratamientos"] = tratamientos_del_trabajo(fila)
    fila["tratamiento_nombre"] = ", ".join(
        item["nombre"] for item in fila["tratamientos"] if item.get("nombre")
    ) or "Tratamiento histórico"
    return fila


def listar_sincronizaciones(limite: int = 50) -> list[dict]:
    with _db() as conn, conn.cursor() as cur:
        cur.execute(_SELECT_SINCRONIZACIONES + " ORDER BY id DESC LIMIT %s", (limite,))
        return [_presentar_sincronizacion(fila) for fila in cur.fetchall()]


def paginar_sincronizaciones(pagina: int, limite: int) -> dict:
    with _db() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS total, COALESCE(MAX(estado IN ('pendiente','ejecutando')),0) AS activo"
            " FROM reservo_sincronizaciones"
        )
        resumen = cur.fetchone()
        total = int(resumen["total"])
        paginas = max(1, (total + limite - 1) // limite)
        pagina = min(pagina, paginas)
        cur.execute(_SELECT_SINCRONIZACIONES + " ORDER BY id DESC LIMIT %s OFFSET %s",
                    (limite, (pagina - 1) * limite))
        items = [_presentar_sincronizacion(fila) for fila in cur.fetchall()]
    return {"items": items, "total": total, "pagina": pagina, "paginas": paginas,
            "por_pagina": limite, "activo": bool(resumen["activo"])}


def reintentar_sincronizacion(job_id: int, usuario: str, config: dict) -> dict:
    original = obtener_sincronizacion(job_id)
    if not original:
        raise LookupError("No se encontró la sincronización.")
    if original["estado"] not in ("error", "completado_con_advertencias"):
        raise ValueError("Solo se pueden reintentar sincronizaciones con error o advertencias.")
    tratamientos = original.get("tratamientos") or []
    if not tratamientos or any(not item.get("nombre") for item in tratamientos):
        raise ValueError("Este registro antiguo no conserva los nombres de tratamiento; inicia una consulta nueva.")
    if str(config.get(clave_estado_atendido(original["clinica_codigo"])) or "").strip() != original["estado_atendido_codigo"]:
        raise ValueError("El código de estado atendido cambió. Verifica la clínica antes de reintentar.")
    return crear_sincronizacion(original["clinica_codigo"], original["fecha_desde"],
                                original["fecha_hasta"], usuario, config, tratamientos)


def _actualizar_progreso(job_id: int, **valores):
    from db import conectar
    permitidas = {"periodos_completados", "paginas_consultadas", "citas_recibidas",
                  "citas_atendidas", "citas_tratamiento", "pacientes_unicos",
                  "pacientes_nuevos", "pacientes_actualizados", "pacientes_descartados",
                  "pacientes_duplicados", "errores", "ultimo_error", "estado",
                  "iniciado_en", "terminado_en"}
    cambios = {k: v for k, v in valores.items() if k in permitidas}
    if not cambios:
        return
    asignacion = ",".join(f"{k}=%s" for k in cambios) + ",heartbeat_en=UTC_TIMESTAMP()"
    with conectar() as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE reservo_sincronizaciones SET {asignacion} WHERE id=%s",
                    (*cambios.values(), job_id))
        conn.commit()


def _auditar_sincronizacion(job: dict, estado: str, detalle: str = "") -> None:
    """Audita el cierre sin incluir datos de pacientes ni mensajes de la API."""
    try:
        from db import auditoria_registrar
        auditoria_registrar(
            job.get("creado_por") or "sistema", f"reservo_sincronizacion_{estado}",
            str(job["id"]),
            f"Clínica {job['clinica_codigo']}; rango {job['fecha_desde']} a {job['fecha_hasta']}"
            + (f"; {detalle[:160]}" if detalle else ""),
        )
    except Exception as exc:
        from db import log_error
        log_error("auditoría de sincronización Reservo", exc, solo_tipo=True)


def _claim_job() -> dict | None:
    from db import conectar
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM reservo_sincronizaciones WHERE estado='pendiente'"
                    " ORDER BY id LIMIT 1 FOR UPDATE")
        fila = cur.fetchone()
        if not fila:
            return None
        job_id = int(fila["id"])
        cur.execute("UPDATE reservo_sincronizaciones SET estado='ejecutando',"
                    " iniciado_en=UTC_TIMESTAMP(),heartbeat_en=UTC_TIMESTAMP(),ultimo_error=NULL"
                    " WHERE id=%s AND estado='pendiente'", (job_id,))
        if cur.rowcount != 1:
            return None
        conn.commit()
    return obtener_sincronizacion(job_id)


def _guardar_paciente_cursor(cur, codigo: str, cliente: dict,
                             tratamientos: list[dict],
                             fecha_atencion: dt.datetime | None = None,
                             huellas_baja_inactivas: list[str] | None = None) -> tuple[str, int]:
    uuid = normalizar_cliente_uuid(cliente["uuid"])
    nombre = texto_cliente(cliente, "nombre", 200)
    apellido = (texto_cliente(cliente, "apellido_paterno", 200)
                or texto_cliente(cliente, "apellidos", 200)
                or texto_cliente(cliente, "apellido", 200))
    telefono = normalizar_telefono(str(telefono_cliente(cliente) or ""))
    if not telefono:
        return "telefono_invalido", 0
    from supresion import bloquear_huella_baja

    huella, baja = bloquear_huella_baja(cur, telefono)
    if not baja and huellas_baja_inactivas is not None:
        huellas_baja_inactivas.append(huella)
    cur.execute("SELECT estado FROM supresion_telefonos WHERE huella=%s FOR UPDATE",
                (huella,))
    supresion = cur.fetchone()
    if baja:
        return "omitido_baja", 0
    cur.execute("SELECT 1 FROM whatsapp_avisos_unicos"
                " WHERE telefono=%s AND tipo='eliminacion_simulada' LIMIT 1",
                (telefono,))
    if cur.fetchone():
        return "omitido_baja", 0
    if supresion and supresion.get("estado") != "exento":
        return "omitido_eliminado", 0
    cur.execute("SELECT id FROM reservo_pacientes WHERE clinica_codigo=%s AND cliente_uuid=%s FOR UPDATE",
                (codigo, uuid))
    existente = cur.fetchone()
    if existente:
        cur.execute(
            "UPDATE reservo_pacientes SET paciente_nombre=COALESCE(%s,paciente_nombre),"
            " paciente_apellido=COALESCE(%s,paciente_apellido),"
            " paciente_telefono=COALESCE(%s,paciente_telefono),"
            " fecha_ultima_atencion=CASE WHEN %s IS NOT NULL"
            " AND (fecha_ultima_atencion IS NULL OR fecha_ultima_atencion<%s)"
            " THEN %s ELSE fecha_ultima_atencion END,"
            " ultima_sincronizacion_en=UTC_TIMESTAMP(), actualizado_en=UTC_TIMESTAMP() WHERE id=%s",
            (nombre, apellido, telefono, fecha_atencion, fecha_atencion,
             fecha_atencion, existente["id"]),
        )
        resultado = "actualizado"
        paciente_id = existente["id"]
    else:
        cur.execute(
            "INSERT INTO reservo_pacientes (clinica_codigo,cliente_uuid,paciente_nombre,"
            "paciente_apellido,paciente_telefono,fecha_ultima_atencion,"
            "primera_sincronizacion_en,ultima_sincronizacion_en)"
            " VALUES (%s,%s,%s,%s,%s,%s,UTC_TIMESTAMP(),UTC_TIMESTAMP())",
            (codigo, uuid, nombre, apellido, telefono, fecha_atencion),
        )
        paciente_id = cur.lastrowid
        resultado = "nuevo"
    for tratamiento in tratamientos:
        cur.execute(
            "INSERT INTO reservo_paciente_tratamientos"
            " (paciente_id,clinica_codigo,cliente_uuid,tratamiento_uuid,tratamiento_nombre,ultima_sincronizacion_en)"
            " VALUES (%s,%s,%s,%s,%s,UTC_TIMESTAMP())"
            " ON DUPLICATE KEY UPDATE tratamiento_nombre=COALESCE(VALUES(tratamiento_nombre),tratamiento_nombre),"
            " ultima_sincronizacion_en=UTC_TIMESTAMP()",
            (paciente_id, codigo, uuid, tratamiento["uuid"], tratamiento["nombre"]),
        )
    return resultado, int(paciente_id)


def _guardar_pagina(job_id: int, codigo: str,
                    filas: list[tuple], progreso: dict) -> tuple[int, int]:
    """Guarda pacientes/relaciones y contadores de la página en una transacción."""
    from db import conectar

    nuevos = actualizados = omitidos = 0
    huellas_baja_inactivas: list[str] = []
    campos = ("paginas_consultadas", "citas_recibidas", "citas_atendidas",
              "citas_tratamiento", "pacientes_unicos", "pacientes_descartados",
              "pacientes_duplicados")
    cambios = {campo: int(progreso[campo]) for campo in campos if campo in progreso}
    with conectar() as conn, conn.cursor() as cur:
        for fila in filas:
            cliente, tratamientos = fila[:2]
            fecha_atencion = fila[2] if len(fila) > 2 else None
            resultado, paciente_id = _guardar_paciente_cursor(
                cur, codigo, cliente, tratamientos, fecha_atencion,
                huellas_baja_inactivas)
            if not paciente_id:
                omitidos += 1
                continue
            cur.execute(
                "INSERT INTO reservo_sincronizacion_pacientes (job_id,paciente_id,nuevo)"
                " VALUES (%s,%s,%s) ON DUPLICATE KEY UPDATE"
                " nuevo=GREATEST(nuevo,VALUES(nuevo))",
                (job_id, paciente_id, resultado == "nuevo"),
            )
            if resultado == "nuevo":
                nuevos += 1
            else:
                actualizados += 1
        if omitidos:
            cambios["pacientes_descartados"] = (
                int(cambios.get("pacientes_descartados", 0)) + omitidos)
        progreso["pacientes_baja_omitidos"] = omitidos
        asignaciones = [f"{campo}=%s" for campo in cambios]
        parametros = list(cambios.values())
        asignaciones.extend(("pacientes_nuevos=pacientes_nuevos+%s",
                             "pacientes_actualizados=pacientes_actualizados+%s",
                             "heartbeat_en=UTC_TIMESTAMP()"))
        parametros.extend((nuevos, actualizados, job_id))
        cur.execute("UPDATE reservo_sincronizaciones SET " + ",".join(asignaciones) +
                    " WHERE id=%s", parametros)
        from supresion import limpiar_cerrojos_baja_inactivos
        limpiar_cerrojos_baja_inactivos(cur, huellas_baja_inactivas)
        conn.commit()
    return nuevos, actualizados


def _procesar(job: dict) -> None:
    from db import config_all, log_error
    codigo = job["clinica_codigo"]
    try:
        cfg = config_all(refresh=True)
        preparada = preparar_sincronizacion(codigo, job["fecha_desde"], job["fecha_hasta"], cfg)
        if preparada["estado_atendido_codigo"] != job["estado_atendido_codigo"]:
            raise ReservoError("El código de estado atendido cambió durante la espera; verifica la clínica e inicia una nueva consulta.")
        estado_atendido = job["estado_atendido_codigo"]
        tratamientos_seleccionados = tratamientos_del_trabajo(job)
        por_uuid = {item["uuid"]: item for item in tratamientos_seleccionados}
        if not por_uuid:
            raise ReservoError("La sincronización no conserva tratamientos válidos.")
        paginas = recibidas = atendidas = coincidencias = nuevos = actualizados = descartados = duplicados = 0
        vistos = set()
        try:
            usar_cache = bool(paginas_citas_cache(
                codigo, job["fecha_desde"], job["fecha_hasta"], preparada["token"],
                estado_atendido, reservar=True))
        except Exception as exc:
            log_error("leer páginas de citas Reservo", exc, solo_tipo=True)
            usar_cache = False
        client = None if usar_cache else ClienteReservo(preparada["token"])
        try:
            bloques = preparada["bloques"]
            _actualizar_progreso(job["id"], iniciado_en=dt.datetime.utcnow())
            for indice_bloque, (desde, hasta) in enumerate(bloques, start=1):
                paginas_bloque = set()
                citas_del_bloque = (
                    iterar_citas_cache(codigo, job["fecha_desde"], job["fecha_hasta"],
                                      preparada["token"], desde, hasta)
                    if usar_cache else client.iterar_citas(desde, hasta))
                for _inicio, _fin, pagina, citas in citas_del_bloque:
                    if pagina in paginas_bloque:
                        raise ReservoError("Reservo repitió una página durante la sincronización.")
                    paginas_bloque.add(pagina)
                    paginas += 1
                    recibidas += len(citas)
                    pacientes_pagina = {}
                    for cita in citas:
                        if not isinstance(cita, dict) or codigo_estado(cita) != estado_atendido:
                            continue
                        atendidas += 1
                        coincidentes = [item for item in tratamientos_cita(cita)
                                        if item["uuid"] in por_uuid]
                        if not coincidentes:
                            continue
                        coincidencias += 1
                        cliente_obj = cliente_valido(cita)
                        if cliente_obj is None:
                            descartados += 1
                            continue
                        uuid_cliente = normalizar_cliente_uuid(cliente_obj["uuid"])
                        if uuid_cliente in vistos:
                            duplicados += 1
                        else:
                            vistos.add(uuid_cliente)
                        fila = pacientes_pagina.setdefault(uuid_cliente, {"cliente": cliente_obj,
                                                                          "tratamientos": {},
                                                                          "fecha_atencion": None})
                        fecha = fecha_atencion_utc(cita)
                        if fecha and (fila["fecha_atencion"] is None or fecha > fila["fecha_atencion"]):
                            fila["fecha_atencion"] = fecha
                        for tratamiento in coincidentes:
                            fila["tratamientos"][tratamiento["uuid"]] = tratamiento
                    filas_pagina = [(fila["cliente"], list(fila["tratamientos"].values()),
                                    fila["fecha_atencion"])
                                    for fila in pacientes_pagina.values()]
                    progreso_pagina = {
                            "paginas_consultadas": paginas,
                            "citas_recibidas": recibidas,
                            "citas_atendidas": atendidas,
                            "citas_tratamiento": coincidencias,
                            "pacientes_unicos": len(vistos),
                            "pacientes_descartados": descartados,
                            "pacientes_duplicados": duplicados,
                        }
                    nuevos_pagina, actualizados_pagina = _guardar_pagina(
                        job["id"], codigo, filas_pagina, progreso_pagina)
                    nuevos += nuevos_pagina
                    actualizados += actualizados_pagina
                    descartados += progreso_pagina.get("pacientes_baja_omitidos", 0)
                if usar_cache and not paginas_bloque:
                    raise ReservoError("La caché de citas quedó incompleta; vuelve a consultar los tratamientos.")
                _actualizar_progreso(job["id"], periodos_completados=indice_bloque)
        finally:
            if client:
                client.close()
        _actualizar_progreso(job["id"], estado="completado_con_advertencias" if descartados else "completado",
                             terminado_en=dt.datetime.utcnow(), errores=descartados,
                             pacientes_descartados=descartados)
        estado_final = "completado_con_advertencias" if descartados else "completado"
        _auditar_sincronizacion(job, estado_final,
                                f"{len(vistos)} pacientes únicos; {descartados} descartados")
    except Exception as exc:
        log_error(f"sincronización Reservo #{job['id']} ({codigo})", exc, solo_tipo=True)
        resumen = resumen_error_seguro(exc)
        _actualizar_progreso(job["id"], estado="error", terminado_en=dt.datetime.utcnow(),
                             errores=1, ultimo_error=resumen)
        _auditar_sincronizacion(job, "error", resumen)


def procesar_siguiente() -> bool:
    job = _claim_job()
    if not job:
        return False
    _procesar(job)
    return True


async def loop_trabajos():
    """Worker persistente en BD; cada proceso reclama trabajos con bloqueo SQL."""
    ultima_recuperacion = 0.0
    while True:
        try:
            if time.monotonic() - ultima_recuperacion >= 30:
                recuperar_sincronizaciones_interrumpidas()
                ultima_recuperacion = time.monotonic()
            hubo = await asyncio.to_thread(procesar_siguiente)
            if not hubo:
                await asyncio.sleep(2)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            from db import log_error
            log_error("worker de pacientes Reservo", exc, solo_tipo=True)
            await asyncio.sleep(5)


def recuperar_sincronizaciones_interrumpidas() -> int:
    """Marca como reintentables trabajos sin heartbeat durante diez minutos."""
    from db import conectar
    with conectar() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE reservo_sincronizaciones SET estado='error',errores=GREATEST(errores,1),"
            " ultimo_error='Proceso interrumpido; resultados parciales conservados y reintentables.',"
            " terminado_en=UTC_TIMESTAMP(),heartbeat_en=UTC_TIMESTAMP()"
            " WHERE estado='ejecutando' AND heartbeat_en IS NOT NULL"
            " AND heartbeat_en < UTC_TIMESTAMP() - INTERVAL 10 MINUTE"
        )
        cantidad = cur.rowcount
        conn.commit()
    return cantidad


def deshacer_sincronizacion(job_id: int) -> dict:
    """Quita solo pacientes creados por este trabajo y no usados por otro."""
    from db import conectar

    with conectar() as conn, conn.cursor() as cur:
        cur.execute("SELECT clinica_codigo FROM reservo_sincronizaciones WHERE id=%s", (job_id,))
        fila = cur.fetchone()
        if not fila:
            raise LookupError("No se encontró la búsqueda.")
        lock_name = f"snw-reservo-{fila['clinica_codigo']}"
        cur.execute("SELECT GET_LOCK(%s, 0) AS adquirido", (lock_name,))
        if not (cur.fetchone() or {}).get("adquirido"):
            raise RuntimeError("Hay otra operación de esta clínica en curso. Inténtalo de nuevo.")
        try:
            return _deshacer_sincronizacion_bloqueada(job_id)
        finally:
            cur.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))


def _limpiar_pacientes_de_busquedas_deshachas(cur, codigo: str) -> int:
    """Retira pacientes cuya creación y usos conocidos ya fueron deshechos."""
    cur.execute(
        "DELETE p FROM reservo_pacientes p"
        " WHERE p.clinica_codigo=%s"
        " AND EXISTS (SELECT 1 FROM reservo_sincronizaciones origen"
        " WHERE origen.clinica_codigo=p.clinica_codigo AND origen.estado='deshecho'"
        " AND p.primera_sincronizacion_en BETWEEN origen.iniciado_en AND origen.terminado_en)"
        " AND NOT EXISTS (SELECT 1 FROM reservo_sincronizaciones vigente"
        " WHERE vigente.clinica_codigo=p.clinica_codigo AND vigente.estado<>'deshecho'"
        " AND vigente.iniciado_en<=p.ultima_sincronizacion_en"
        " AND (vigente.terminado_en IS NULL"
        " OR vigente.terminado_en>=p.primera_sincronizacion_en))"
        " AND NOT EXISTS (SELECT 1 FROM reservo_sincronizacion_pacientes referencia"
        " JOIN reservo_sincronizaciones vigente ON vigente.id=referencia.job_id"
        " WHERE referencia.paciente_id=p.id AND vigente.estado<>'deshecho')"
        " AND NOT EXISTS (SELECT 1 FROM reservo_lista_pacientes lp WHERE lp.paciente_id=p.id)",
        (codigo,),
    )
    return cur.rowcount


def _deshacer_sincronizacion_bloqueada(job_id: int) -> dict:
    from db import conectar

    with conectar() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id,clinica_codigo,estado,iniciado_en,terminado_en"
            " FROM reservo_sincronizaciones WHERE id=%s FOR UPDATE", (job_id,),
        )
        job = cur.fetchone()
        if not job:
            raise LookupError("No se encontró la búsqueda.")
        if job["estado"] not in ("completado", "completado_con_advertencias", "error", "deshecho"):
            raise ValueError("Solo se puede deshacer una búsqueda terminada.")
        cur.execute("SELECT id FROM reservo_sincronizaciones WHERE clinica_codigo=%s"
                    " AND id<>%s AND estado IN ('pendiente','ejecutando') LIMIT 1 FOR UPDATE",
                    (job["clinica_codigo"], job_id))
        if cur.fetchone():
            raise ValueError("Espera a que termine la búsqueda activa de esta clínica para deshacer.")
        if job["estado"] == "deshecho":
            eliminados = _limpiar_pacientes_de_busquedas_deshachas(cur, job["clinica_codigo"])
            if eliminados:
                cur.execute("UPDATE reservo_sincronizaciones SET"
                            " pacientes_revertidos=pacientes_revertidos+%s WHERE id=%s",
                            (eliminados, job_id))
            conn.commit()
            return {"id": job_id, "estado": "deshecho", "pacientes_eliminados": eliminados}
        cur.execute("SELECT COUNT(*) AS total FROM reservo_sincronizacion_pacientes"
                    " WHERE job_id=%s", (job_id,))
        con_referencias = int((cur.fetchone() or {}).get("total") or 0) > 0
        if con_referencias:
            cur.execute(
                "DELETE p FROM reservo_pacientes p"
                " JOIN reservo_sincronizacion_pacientes actual"
                " ON actual.paciente_id=p.id AND actual.job_id=%s AND actual.nuevo=1"
                " LEFT JOIN reservo_sincronizacion_pacientes otra"
                " ON otra.paciente_id=p.id AND otra.job_id<>%s"
                " WHERE otra.paciente_id IS NULL"
                " AND NOT EXISTS (SELECT 1 FROM reservo_lista_pacientes lp"
                " WHERE lp.paciente_id=p.id)", (job_id, job_id),
            )
        elif job["iniciado_en"] and job["terminado_en"]:
            # Trabajos anteriores al registro de procedencia: las fechas solo
            # permiten identificar pacientes creados y no sincronizados después.
            cur.execute(
                "SELECT COUNT(*) AS total FROM reservo_sincronizaciones"
                " WHERE clinica_codigo=%s AND id<>%s"
                " AND iniciado_en<=%s AND terminado_en>=%s",
                (job["clinica_codigo"], job_id, job["terminado_en"], job["iniciado_en"]),
            )
            if int((cur.fetchone() or {}).get("total") or 0):
                raise ValueError("Esta búsqueda antigua coincide en horario con otra y no puede deshacerse con seguridad.")
            cur.execute(
                "DELETE p FROM reservo_pacientes p"
                " LEFT JOIN reservo_sincronizacion_pacientes otra ON otra.paciente_id=p.id"
                " WHERE p.clinica_codigo=%s"
                " AND p.primera_sincronizacion_en BETWEEN %s AND %s"
                " AND p.ultima_sincronizacion_en BETWEEN %s AND %s"
                " AND otra.paciente_id IS NULL"
                " AND NOT EXISTS (SELECT 1 FROM reservo_lista_pacientes lp"
                " WHERE lp.paciente_id=p.id)",
                (job["clinica_codigo"], job["iniciado_en"], job["terminado_en"],
                 job["iniciado_en"], job["terminado_en"]),
            )
        else:
            raise ValueError("Esta búsqueda antigua no tiene fechas suficientes para deshacerla con seguridad.")
        eliminados = cur.rowcount
        cur.execute("DELETE FROM reservo_sincronizacion_pacientes WHERE job_id=%s", (job_id,))
        cur.execute("UPDATE reservo_sincronizaciones SET estado='deshecho',"
                    " heartbeat_en=UTC_TIMESTAMP() WHERE id=%s", (job_id,))
        eliminados += _limpiar_pacientes_de_busquedas_deshachas(cur, job["clinica_codigo"])
        cur.execute("UPDATE reservo_sincronizaciones SET pacientes_revertidos=%s WHERE id=%s",
                    (eliminados, job_id))
        conn.commit()
    return {"id": job_id, "estado": "deshecho", "pacientes_eliminados": eliminados}


def configurar_tablas(cur):
    """Migración idempotente llamada por `asegurar_tabla_config`."""
    cur.execute(
        "CREATE TABLE IF NOT EXISTS reservo_tratamientos_cache ("
        " clinica_codigo VARCHAR(8) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,"
        " fecha_desde DATE NOT NULL,fecha_hasta DATE NOT NULL,"
        " token_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,"
        " tratamientos_json LONGTEXT NOT NULL,expira_en DATETIME NOT NULL,"
        " paginas INT UNSIGNED NOT NULL DEFAULT 0,estado_atendido VARCHAR(80) NOT NULL DEFAULT '',"
        " PRIMARY KEY (clinica_codigo,fecha_desde,fecha_hasta,token_hash),"
        " KEY idx_reservo_tratamientos_expira (expira_en)"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
    )
    cur.execute("SELECT COLUMN_NAME FROM information_schema.COLUMNS"
                " WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='reservo_tratamientos_cache'")
    columnas_cache = {fila["COLUMN_NAME"] for fila in cur.fetchall()}
    if "paginas" not in columnas_cache:
        cur.execute("ALTER TABLE reservo_tratamientos_cache ADD COLUMN paginas"
                    " INT UNSIGNED NOT NULL DEFAULT 0")
    if "estado_atendido" not in columnas_cache:
        cur.execute("ALTER TABLE reservo_tratamientos_cache ADD COLUMN estado_atendido"
                    " VARCHAR(80) NOT NULL DEFAULT ''")
    cur.execute(
        "CREATE TABLE IF NOT EXISTS reservo_citas_cache ("
        " clinica_codigo VARCHAR(8) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,"
        " fecha_desde DATE NOT NULL,fecha_hasta DATE NOT NULL,"
        " token_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,"
        " numero INT UNSIGNED NOT NULL,tramo_desde DATE NOT NULL,tramo_hasta DATE NOT NULL,"
        " pagina INT UNSIGNED NOT NULL,citas_json LONGTEXT NOT NULL,expira_en DATETIME NOT NULL,"
        " KEY idx_reservo_citas_cache_expira (expira_en),"
        " PRIMARY KEY (clinica_codigo,fecha_desde,fecha_hasta,token_hash,numero)"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
    )
    cur.execute(
        "CREATE TABLE IF NOT EXISTS reservo_pacientes ("
        " id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,"
        " clinica_codigo VARCHAR(8) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,"
        " cliente_uuid VARCHAR(80) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,"
        " paciente_nombre VARCHAR(200) NULL,paciente_apellido VARCHAR(200) NULL,"
        " paciente_telefono VARCHAR(40) NULL,"
        " fecha_ultima_atencion DATETIME NULL,"
        " primera_sincronizacion_en DATETIME NOT NULL,ultima_sincronizacion_en DATETIME NOT NULL,"
        " creado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,"
        " actualizado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,"
        " UNIQUE KEY uq_reservo_paciente_clinica_uuid (clinica_codigo,cliente_uuid),"
        " KEY idx_reservo_paciente_clinica_fecha (clinica_codigo,ultima_sincronizacion_en),"
        " KEY idx_reservo_paciente_nombre (paciente_nombre)"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
    )
    cur.execute("SELECT COLUMN_NAME FROM information_schema.COLUMNS"
                " WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='reservo_pacientes'")
    columnas_pacientes = {fila["COLUMN_NAME"] for fila in cur.fetchall()}
    if "fecha_ultima_atencion" not in columnas_pacientes:
        cur.execute("ALTER TABLE reservo_pacientes ADD COLUMN fecha_ultima_atencion DATETIME NULL")
    cur.execute(
        "CREATE TABLE IF NOT EXISTS reservo_paciente_tratamientos ("
        " id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,paciente_id BIGINT UNSIGNED NOT NULL,"
        " clinica_codigo VARCHAR(8) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,"
        " cliente_uuid VARCHAR(80) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,"
        " tratamiento_uuid VARCHAR(80) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,"
        " tratamiento_nombre VARCHAR(200) NULL,ultima_sincronizacion_en DATETIME NOT NULL,"
        " UNIQUE KEY uq_reservo_paciente_tratamiento (clinica_codigo,cliente_uuid,tratamiento_uuid),"
        " KEY idx_reservo_tratamiento (clinica_codigo,tratamiento_uuid),"
        " CONSTRAINT fk_reservo_paciente_tratamiento FOREIGN KEY (paciente_id)"
        " REFERENCES reservo_pacientes(id) ON DELETE CASCADE"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
    )
    cur.execute(
        "CREATE TABLE IF NOT EXISTS reservo_sincronizaciones ("
        " id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,"
        " clinica_codigo VARCHAR(8) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,"
        " tratamiento_uuid VARCHAR(80) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,"
        " tratamientos_json LONGTEXT NULL,"
        " estado_atendido_codigo VARCHAR(80) NOT NULL,"
        " fecha_desde DATE NOT NULL,fecha_hasta DATE NOT NULL,"
        " estado VARCHAR(32) NOT NULL DEFAULT 'pendiente',periodos_total INT UNSIGNED NOT NULL DEFAULT 0,"
        " periodos_completados INT UNSIGNED NOT NULL DEFAULT 0,paginas_consultadas INT UNSIGNED NOT NULL DEFAULT 0,"
        " citas_recibidas INT UNSIGNED NOT NULL DEFAULT 0,citas_atendidas INT UNSIGNED NOT NULL DEFAULT 0,"
        " citas_tratamiento INT UNSIGNED NOT NULL DEFAULT 0,pacientes_unicos INT UNSIGNED NOT NULL DEFAULT 0,"
        " pacientes_nuevos INT UNSIGNED NOT NULL DEFAULT 0,pacientes_actualizados INT UNSIGNED NOT NULL DEFAULT 0,"
        " pacientes_descartados INT UNSIGNED NOT NULL DEFAULT 0,pacientes_duplicados INT UNSIGNED NOT NULL DEFAULT 0,"
        " pacientes_revertidos INT UNSIGNED NOT NULL DEFAULT 0,"
        " errores INT UNSIGNED NOT NULL DEFAULT 0,ultimo_error VARCHAR(240) NULL,"
        " creado_por VARCHAR(150) NOT NULL,creado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,"
        " iniciado_en DATETIME NULL,terminado_en DATETIME NULL,heartbeat_en DATETIME NULL,"
        " KEY idx_reservo_sync_estado (estado,creado_en),"
        " KEY idx_reservo_sync_clinica (clinica_codigo,estado,creado_en)"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
    )
    cur.execute("SELECT COLUMN_NAME FROM information_schema.COLUMNS"
                " WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='reservo_sincronizaciones'")
    columnas = {fila["COLUMN_NAME"] for fila in cur.fetchall()}
    if "estado_atendido_codigo" not in columnas:
        cur.execute("ALTER TABLE reservo_sincronizaciones ADD COLUMN estado_atendido_codigo"
                    " VARCHAR(80) NOT NULL DEFAULT '' AFTER tratamiento_uuid")
    if "tratamientos_json" not in columnas:
        cur.execute("ALTER TABLE reservo_sincronizaciones ADD COLUMN tratamientos_json LONGTEXT NULL"
                    " AFTER tratamiento_uuid")
    if "pacientes_revertidos" not in columnas:
        cur.execute("ALTER TABLE reservo_sincronizaciones ADD COLUMN pacientes_revertidos"
                    " INT UNSIGNED NOT NULL DEFAULT 0 AFTER pacientes_duplicados")
    cur.execute(
        "CREATE TABLE IF NOT EXISTS reservo_sincronizacion_pacientes ("
        " job_id BIGINT UNSIGNED NOT NULL,paciente_id BIGINT UNSIGNED NOT NULL,"
        " nuevo TINYINT(1) NOT NULL DEFAULT 0,"
        " PRIMARY KEY (job_id,paciente_id),KEY idx_reservo_referencia_paciente (paciente_id),"
        " CONSTRAINT fk_reservo_referencia_job FOREIGN KEY (job_id)"
        " REFERENCES reservo_sincronizaciones(id) ON DELETE CASCADE,"
        " CONSTRAINT fk_reservo_referencia_paciente FOREIGN KEY (paciente_id)"
        " REFERENCES reservo_pacientes(id) ON DELETE CASCADE"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
    )
    cur.execute(
        "CREATE TABLE IF NOT EXISTS reservo_bases_contacto ("
        " id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,"
        " nombre_personalizado VARCHAR(100) NOT NULL,nombre VARCHAR(150) NOT NULL,"
        " clinica_codigo VARCHAR(8) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,"
        " origen VARCHAR(20) NOT NULL DEFAULT 'RESERVO',sync_job_id BIGINT UNSIGNED NOT NULL,"
        " tratamientos_json LONGTEXT NOT NULL,fecha_desde DATE NOT NULL,fecha_hasta DATE NOT NULL,"
        " mes VARCHAR(12) NOT NULL,anio SMALLINT UNSIGNED NOT NULL,"
        " creado_por VARCHAR(150) NOT NULL,creado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,"
        " UNIQUE KEY uq_reservo_base_nombre (nombre),"
        " UNIQUE KEY uq_reservo_base_origen_nombre (sync_job_id,nombre),"
        " KEY idx_reservo_base_clinica (clinica_codigo,creado_en),"
        " CONSTRAINT fk_reservo_base_sync FOREIGN KEY (sync_job_id)"
        " REFERENCES reservo_sincronizaciones(id) ON DELETE RESTRICT"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
    )
    cur.execute(
        "CREATE TABLE IF NOT EXISTS reservo_lista_pacientes ("
        " id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,lista_id INT NOT NULL,"
        " paciente_id BIGINT UNSIGNED NOT NULL,cliente_uuid VARCHAR(80)"
        " CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,"
        " nombre VARCHAR(200) NULL,apellido VARCHAR(200) NULL,telefono VARCHAR(40) NULL,"
        " telefono_valido TINYINT(1) NOT NULL DEFAULT 0,"
        " tratamiento_ultima_atencion TEXT NULL,fecha_ultima_atencion DATETIME NULL,"
        " estado VARCHAR(32) NOT NULL DEFAULT 'pendiente',"
        " whatsapp_opt_out TINYINT(1) NOT NULL DEFAULT 0,opt_out_explicito TINYINT(1) NOT NULL DEFAULT 0,"
        " respuesta_manual VARCHAR(32) NULL,interesado TINYINT(1) NOT NULL DEFAULT 0,"
        " no_interesado TINYINT(1) NOT NULL DEFAULT 0,interes_plantilla_clave VARCHAR(50) NULL,"
        " interes_fecha DATETIME NULL,consentimiento_verificado TINYINT(1) NOT NULL DEFAULT 1,"
        " consentimiento_referencia VARCHAR(200) NULL,fecha_actualizacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
        " ON UPDATE CURRENT_TIMESTAMP,"
        " UNIQUE KEY uq_reservo_lista_paciente (lista_id,paciente_id),"
        " KEY idx_reservo_lista_telefono (telefono),"
        " KEY idx_reservo_lista_elegibles (lista_id,consentimiento_verificado,estado),"
        " CONSTRAINT fk_reservo_lista_base FOREIGN KEY (lista_id)"
        " REFERENCES reservo_bases_contacto(id) ON DELETE CASCADE,"
        " CONSTRAINT fk_reservo_lista_paciente FOREIGN KEY (paciente_id)"
        " REFERENCES reservo_pacientes(id) ON DELETE CASCADE"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
    )
    # El proceso Reservo que alimenta estas listas ya filtra y registra la
    # autorización. Las filas previas a ese acuerdo se habilitan al migrar.
    cur.execute("UPDATE reservo_lista_pacientes SET consentimiento_verificado=1,"
                " consentimiento_referencia=NULL WHERE consentimiento_verificado<>1 "
                "OR consentimiento_referencia IS NOT NULL")


def tablas_disponibles() -> bool:
    """Evita arrancar el worker si el esquema no quedó instalado."""
    from db import conectar
    requeridas = ("reservo_pacientes", "reservo_paciente_tratamientos",
                 "reservo_sincronizaciones", "reservo_sincronizacion_pacientes",
                 "reservo_bases_contacto", "reservo_lista_pacientes")
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS cantidad FROM information_schema.TABLES"
                    " WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME IN (%s,%s,%s,%s,%s,%s)", requeridas)
        return int((cur.fetchone() or {}).get("cantidad") or 0) == len(requeridas)

