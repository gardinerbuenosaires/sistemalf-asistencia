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
ABREV = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")
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


def describir(semana_leida):
    """
    Una franja en una línea, para mostrarle a alguien.

    Los días iguales se agrupan: «lun a vie 08:00 a 18:00, sáb 09:00 a 13:00,
    dom cerrado» se entiende, y siete renglones repetidos no.

    Un horario que termina antes de empezar —17:00 a 03:00— no es un error: es
    un turno que cruza la medianoche, y el equipo los guarda así. No se corrige
    ni se avisa, se muestra como está.
    """
    if any(d is None for d in semana_leida):
        return "ilegible"
    if abierta(semana_leida):
        return "todo el día, los siete días"
    if vacia(semana_leida):
        return "sin horarios cargados"
    if len(set(semana_leida)) == 1:
        return f"todos los días de {texto_dia(semana_leida[0])}"

    tramos = []
    for i, d in enumerate(semana_leida):
        if tramos and tramos[-1][1] == d:
            tramos[-1][0].append(i)
        else:
            tramos.append(([i], d))
    partes = []
    for dias, d in tramos:
        etiqueta = (ABREV[dias[0]] if len(dias) == 1
                    else f"{ABREV[dias[0]]} a {ABREV[dias[-1]]}")
        partes.append(f"{etiqueta} {texto_dia(d)}")
    return ", ".join(partes)


def describir_grupo(suyas, definiciones):
    """
    Qué horario le da un grupo a quien está en él.

    `suyas` son los números de franja que el grupo tiene asignados; cada grupo
    tiene tres lugares y suele repetir la misma. `definiciones` es lo que el
    equipo contestó por cada franja.

    Devuelve (texto, restringe). `restringe` es lo que importa al cargar gente:
    si es True, el grupo elegido le está poniendo un horario a alguien, y eso
    tiene que decirse en voz alta.

    Y queda en None cuando no se pudo leer —`suyas` en None—, que no es lo mismo
    que no tener horario: afirmar «abre a cualquier hora» sobre algo que nadie
    leyó es el error que deja a alguien afuera sin que nada lo haya avisado.
    """
    if suyas is None:
        return "no se pudo leer el horario de este grupo", None

    numeros = sorted({n for n in suyas if n})
    if not numeros:
        return "sin franja asignada: abre a cualquier hora", False

    textos, restringe = [], False
    for n in numeros:
        semana_leida = definiciones.get(n)
        if semana_leida is None:
            textos.append(f"franja {n} (no se pudo leer)")
            continue
        textos.append(f"franja {n}: {describir(semana_leida)}")
        if not abierta(semana_leida) and not vacia(semana_leida):
            restringe = True
    return "; ".join(textos), restringe


def rango(numeros):
    """«franjas 1 a 8» en vez de repetir ocho veces lo mismo."""
    if not numeros:
        return "ninguna franja"
    if len(numeros) == 1:
        return f"franja {numeros[0]}"
    if numeros == list(range(numeros[0], numeros[-1] + 1)):
        return f"franjas {numeros[0]} a {numeros[-1]}"
    return "franjas " + ", ".join(str(n) for n in numeros)
