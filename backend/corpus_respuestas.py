"""Representación conservadora para estudiar intención sin copiar datos libres.

Solo sobreviven palabras de un vocabulario cerrado. Nombres, teléfonos,
direcciones, diagnósticos y cualquier palabra no aprobada se sustituyen.
No es una copia recuperable del mensaje original.
"""

import re
import unicodedata

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
