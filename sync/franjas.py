"""Los horarios que un lector guarda adentro. Desarmado de bytes, nada más.

Un lector decide dos cosas distintas sobre una persona: si está cargada, y en
qué horario la deja abrir. Lo segundo vive acá:

    FRANJA    los horarios de los siete días: de cuándo a cuándo
    GRUPO     apunta a hasta tres franjas
    USUARIO   pertenece a un grupo, y hereda esas franjas

Está separado del script que las pregunta porque la cuenta es lo único que
puede estar mal sin que se note. La primera versión leía cada día como un
entero de dos bytes, que los da vuelta: una franja abierta de 00:00 a 23:59
aparecía como «?3B17» y parecía que el equipo contestaba basura. No contestaba
basura. Por eso esto se prueba sin equipo.
"""

DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
TODO_EL_DIA = (0, 0, 23, 59)
CERRADA = (0, 0, 0, 0)


def dia(bloque):
    """
    Un día son cuatro bytes sueltos: hora y minuto de inicio, hora y minuto de
    fin. Devuelve None si alguno no puede ser una hora, que es la señal de que
    el formato no es el que creemos.
    """
    if len(bloque) < 4:
        return None
    h1, m1, h2, m2 = bloque[0], bloque[1], bloque[2], bloque[3]
    if h1 > 23 or m1 > 59 or h2 > 23 or m2 > 59:
        return None
    return (h1, m1, h2, m2)


def semana(crudo):
    """Los siete días de una franja, en orden, desde la respuesta del equipo."""
    return tuple(dia(crudo[i * 4:i * 4 + 4]) for i in range(7))


def texto_dia(valores):
    """
    Un día, en palabras.

    Todo en cero no es «de 00:00 a 00:00»: es un día en el que no abre. Son la
    misma cosa para el equipo y dos cosas muy distintas para quien lee.
    """
    if valores is None:
        return "ilegible"
    if valores == CERRADA:
        return "cerrado"
    h1, m1, h2, m2 = valores
    return f"{h1:02d}:{m1:02d} a {h2:02d}:{m2:02d}"


def abierta(semana_leida):
    """Los siete días completos: una franja así no le niega el paso a nadie."""
    return all(d == TODO_EL_DIA for d in semana_leida)


def vacia(semana_leida):
    """Todo en cero: el equipo tiene la franja sin horarios cargados."""
    return all(d == CERRADA for d in semana_leida)


def franjas_del_grupo(crudo):
    """
    Las franjas que usa un grupo, de la respuesta del equipo.

    Vienen enteros de cuatro bytes. Los tres que siguen al primero son las tres
    franjas que el grupo puede usar; el primero es un campo que no
    identificamos, y se devuelve aparte en vez de inventarle un nombre.
    """
    from struct import unpack

    enteros = list(unpack("<" + "I" * (len(crudo) // 4),
                          crudo[:len(crudo) // 4 * 4]))
    if not enteros:
        return [], None
    return [n for n in enteros[1:4] if n], enteros[0]


def rango(numeros):
    """«franjas 1 a 8» en vez de repetir ocho veces lo mismo."""
    if not numeros:
        return "ninguna franja"
    if len(numeros) == 1:
        return f"franja {numeros[0]}"
    if numeros == list(range(numeros[0], numeros[-1] + 1)):
        return f"franjas {numeros[0]} a {numeros[-1]}"
    return "franjas " + ", ".join(str(n) for n in numeros)
