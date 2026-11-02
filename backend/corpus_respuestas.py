"""Guarda respuestas completas y exporta corpus para revisión manual.

El corpus desidentificado legado conserva solo un vocabulario cerrado;
sus sustituciones no permiten recuperar el mensaje original.
"""

import re
import unicodedata
import csv
import io

from db import conectar

_CORPUS_EXPORTABLES = {
    "completo": ("corpus_respuestas_completas", "mensaje"),
    "desidentificado": ("corpus_respuestas", "texto_desidentificado"),
}
_FILAS_EXPORTACION = 500


def preparar_csv(origen: str):
    """CSV por bloques, sin teléfonos, huellas ni IDs de pacientes.

    Captura un límite superior para que nuevas respuestas no prolonguen una
    descarga. Cada bloque usa su propia conexión y respeta borrados posteriores.
    """
    if origen not in (*_CORPUS_EXPORTABLES, "ambos"):
        raise ValueError("Corpus inválido")
    fuentes = list(_CORPUS_EXPORTABLES) if origen == "ambos" else [origen]
    limites = {}
    with conectar() as conn, conn.cursor() as cur:
        for fuente in fuentes:
            tabla, _ = _CORPUS_EXPORTABLES[fuente]
            cur.execute(f"SELECT COALESCE(MAX(id),0) AS ultimo FROM {tabla}")
            limites[fuente] = int(cur.fetchone()["ultimo"])

    def fila_csv(valores):
        salida = io.StringIO(newline="")
        # Evitar que Excel interprete mensajes como fórmulas al abrir el CSV.
        seguros = []
        for valor in valores:
            valor = str(valor)
            if valor.lstrip().startswith(("=", "+", "-", "@")) or valor.startswith(("\t", "\r", "\n")):
                valor = "'" + valor
            seguros.append(valor)
        csv.writer(salida).writerow(seguros)
        return salida.getvalue()

    def generar():
        yield "\ufeff" + fila_csv(("corpus", "mensaje", "tipo_respuesta"))
        for fuente in fuentes:
            tabla, columna = _CORPUS_EXPORTABLES[fuente]
            ultimo = 0
            while ultimo < limites[fuente]:
                with conectar() as conn, conn.cursor() as cur:
                    cur.execute(
                        f"SELECT id, {columna} AS mensaje FROM {tabla}"
                        " WHERE id>%s AND id<=%s ORDER BY id LIMIT %s",
                        (ultimo, limites[fuente], _FILAS_EXPORTACION))
                    filas = cur.fetchall()
                if not filas:
                    break
                for fila in filas:
                    yield fila_csv((fuente, fila["mensaje"] or "", ""))
                ultimo = filas[-1]["id"]
    return generar()


def guardar_mensaje_completo(cur, texto: str, telefono: str) -> None:
    """Inserta texto íntegro y vínculo seudónimo en la misma transacción.

    La llamada falla sin huella válida; jamás deja un texto completo huérfano.
    """
    from supresion import huella_telefono

    huella = huella_telefono(telefono)
    cur.execute("SELECT estado FROM supresion_telefonos WHERE huella = %s FOR UPDATE",
                (huella,))
    estado = cur.fetchone()
    if estado and estado["estado"] != "exento":
        return
    cur.execute("INSERT INTO corpus_respuestas_completas (mensaje) VALUES (%s)",
                (texto or "",))
    cur.execute(
        "INSERT INTO corpus_respuestas_origen (corpus_id, huella) VALUES (%s, %s)",
        (cur.lastrowid, huella),
    )

_PALABRAS = frozenset("""
hola holi buenas buenos dias tardes noches gracias por favor porfavor
si no nop sip ok dale listo claro me mi mis te tu tus su sus nos nuestro
nuestra nuestros nuestras yo usted ustedes el la los las un una unos unas
de del a al en con sin para por que como cual cuando donde cuanto cuantos
hay tienen tiene tengo quiero quisiera queriamos necesito necesitamos
puedo pueden podemos podrian gustaria interesa interesan interesado
interesada interesaria saber mas informacion info detalles consulta
consultar pregunta duda ayuda explicar responder respondan contacto
contactar llamen llamar hablo hablar persona ejecutivo asesores
hora horas horita horitas cita citas agendar agendo agenda reagendar
reservar reserva cupo cupos disponible disponibles disponibilidad
hoy manana pasado semana proxima lunes martes miercoles jueves viernes
sabado domingo atienden atiende atendiendo atender atencion medico
medicos doctor doctora doctores profesional especialista especialidad
precio precios valor valores cuesta costo costos arancel convenio
fonasa horario horarios ubicacion estan estamos servicio servicios
confirmo confirmacion confirmar confirmado asisto asistire ire voy
cancelar cancelo cancelacion asistir reagendar cambiar otra oportunidad
ahora momento despues luego ya fui atendieron atendi necesito buscar
busco encontre consegui tengo gracias grax bien mal nunca tampoco
baja dar den dejar recibir recibirlos escriban escribirme envien
manden mensajes promociones publicidad eliminar borrar borrenme
numero datos stop salir suscripcion lista contactar contactarme
bienvenido bienvenida comunicarte contactarnos escribirnos recibido
recibimos pronto responderemos mensaje automatico automatica bot
asistente virtual presiona escribe selecciona elige opcion opciones
menu espera esperando fuera nuestro equipo atencion cliente asesores
uno dos tres cuatro cinco primero segundo tercero siguiente continuar
atenderemos comunicar pronto posible hablar humano recepcion
""".split())

_ENLACE_O_CORREO = re.compile(r"https?://\S+|www\.\S+|\b\S+@\S+\.\S+\b", re.I)
_NUMERO_LARGO = re.compile(r"(?<!\w)\+?\d[\d\s()./-]{5,}\d(?!\w)")


def desidentificar_mensaje(texto: str) -> str:
    """Devuelve solo palabras útiles aprobadas y marcadores no reversibles."""
    original = (texto or "")[:4000]
    original = _ENLACE_O_CORREO.sub(" DATORESERVADO ", original)
    original = _NUMERO_LARGO.sub(" DATORESERVADO ", original)
    plano = unicodedata.normalize("NFKD", original.casefold())
    plano = "".join(c for c in plano if not unicodedata.combining(c))
    tokens = re.findall(r"[a-z]+|\d+|[?!.]", plano)
    salida = []
    for token in tokens:
        if token == "datoreservado" or token.isdigit():
            limpio = "[dato]"
        elif token in _PALABRAS:
            limpio = token
        elif token in ("?", "!", "."):
            limpio = token
        else:
            limpio = "[otro]"
        if limpio == "[otro]" and salida and salida[-1] == limpio:
            continue
        salida.append(limpio)
    return " ".join(salida)[:2000] or "[sin_texto]"
