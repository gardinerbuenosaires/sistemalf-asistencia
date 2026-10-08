"""Una persona contra los lectores: ¿realmente puede abrir esa puerta?

Es otra pregunta que la del plan. El plan mira una puerta y se pregunta a quién
le falta o a quién le sobra; esto mira a una persona y recorre las puertas. Es la
pregunta que se hace todos los días —"¿a Fulano le quedó el depósito?"— y la que
había que abrir Enterprise para contestar.

La ficha del legajo sola no alcanza: ahí se ve lo que la persona *debería* abrir
según su perfil. Que el equipo lo tenga cargado es otra cosa, y es la que
importa cuando alguien se queda afuera a las siete de la mañana.

Y estar cargado tampoco alcanza: sin la huella en ese equipo, la persona figura
en la lista y no abre igual. Ese caso es el peor de todos, porque mirando el
lista parece resuelto. Se distingue aparte.

Solo lectura.
"""
import logging

logger = logging.getLogger(__name__)

# Qué se concluye de cada combinación de: le corresponde · está cargado · tiene
# huella. Los equipos que no son puerta tienen sus propios estados y no comparten
# ninguno con las puertas: el maestro no abre nada, y decir de él que "abre"
# —aunque sea por reusar una etiqueta— es afirmar algo falso sobre un equipo.
DIAGNOSTICOS = {
    # Puertas.
    "abre":         "Abre esta puerta",
    "sin_huella":   "Cargado pero SIN huella: no abre",
    "falta":        "Debería abrir y no está cargado",
    "sobra":        "Está cargado y no debería abrir",
    "no_abre":      "No abre, y no está cargado",
    # Equipo de asistencia. No abre puertas: registra la huella y de ahí se copia
    # a las puertas, así que lo único que se informa es si está enrolado.
    "enrolado":             "Enrolado para asistencia (este equipo no abre puertas)",
    "maestro_sin_huella":   "Enrolado SIN huella: no puede fichar ni hay huella para copiar",
    "no_enrolado":          "No está enrolado en el equipo de asistencia",
    # Cualquiera de los dos.
    "sin_leer":     "No se pudo leer el equipo",
}


def _nombre_coincide(en_el_equipo: str, pedido: str) -> bool:
    """
    ¿El equipo tiene el nombre que se le pidió?

    No alcanza con compararlos: cada equipo corta el nombre a un largo distinto
    —24 el de fichaje, 8 las puertas— así que el que se pidió casi nunca entra
    entero. Que el del equipo sea el COMIENZO del pedido es lo que significa que
    está bien y solo quedó cortado.

    Así no hace falta saber de antemano cuánto corta cada modelo, que es el tipo
    de constante que se descubre estando mal.
    """
    a = (en_el_equipo or "").strip().upper()
    b = (pedido or "").strip().upper()
    if not b:
        return True          # no se pidió nada: no hay nada que incumplir
    if not a:
        return False         # se pidió un nombre y el equipo no tiene ninguno
    return b.startswith(a)


def verificar(user_id, equipos: list, lecturas: dict, deseadas: set,
              nombre_pedido: str = None) -> dict:
    """
    Cruza a una persona contra cada equipo leído.

    `equipos` son filas de dispositivos (con `nombre`, `es_acceso`,
    `cuenta_asistencia`), `lecturas` es {id: resultado de leer_cargados} y
    `deseadas` el conjunto de puertas que le tocan según perfil y excepciones.

    De los equipos que no son puerta —el de asistencia— no se opina si debería
    estar o no: ahí la persona está por fichar, no por abrir. Pero se informa,
    porque es de donde sale la huella que habría que copiar, y si no la tiene ahí
    no hay nada que copiar a ninguna puerta.
    """
    user_id = str(user_id).strip()
    filas = []
    resumen = {"abre": 0, "falta": 0, "sobra": 0, "sin_huella": 0, "sin_leer": 0,
               "sin_huella_maestro": False, "no_en_maestro": False,
               "nombre_distinto": 0, "huellas_de_menos": 0}

    # Cuantas huellas tiene en el equipo de asistencia. Es la referencia para
    # las puertas: a alguien se le gasta un dedo, enrola otro ahi, y las puertas
    # donde ya esta cargado se quedan con el juego viejo. Nadie lo nota hasta
    # que esa persona apoya el dedo que ya no le lee.
    #
    # En None si no se pudo leer. No saber no es lo mismo que no tener, y con
    # None no se compara nada.
    huellas_maestro = None
    for d in equipos:
        if d.get("es_acceso"):
            continue
        lec = lecturas.get(d["id"]) or {}
        if not lec.get("ok"):
            continue
        suyo = next((u for u in lec["usuarios"] if u["user_id"] == user_id), None)
        if suyo and suyo.get("huellas") is not None:
            huellas_maestro = suyo["huellas"]
            break

    for d in equipos:
        lectura = lecturas.get(d["id"]) or {
            "ok": False, "error": "sin resultado", "usuarios": []}
        es_puerta = bool(d.get("es_acceso"))
        debe = d["id"] in deseadas

        fila = {
            "id": d["id"], "nombre": d["nombre"], "ubicacion": d.get("ubicacion"),
            "es_puerta": es_puerta,
            "es_asistencia": bool(d.get("cuenta_asistencia")),
            "deberia": debe if es_puerta else None,
            "ok": lectura["ok"], "error": lectura.get("error"),
            "cargado": None, "huellas": None, "huellas_maestro": None,
            "huellas_de_menos": False, "nombre_en_equipo": None,
            "grupo": None, "uid": None, "nombre_ok": None,
        }

        if not lectura["ok"]:
            fila["estado"] = "sin_leer"
            resumen["sin_leer"] += 1
            filas.append(dict(fila, diagnostico=DIAGNOSTICOS["sin_leer"]))
            continue

        encontrado = next(
            (u for u in lectura["usuarios"] if u["user_id"] == user_id), None)
        fila["cargado"] = encontrado is not None
        if encontrado:
            fila["uid"] = encontrado.get("uid")
            fila["nombre_en_equipo"] = encontrado.get("nombre")
            fila["grupo"] = encontrado.get("grupo")
            fila["huellas"] = encontrado.get("huellas")
            fila["huellas_maestro"] = huellas_maestro
            # Menos huellas que en el fichaje: le falta al menos la ultima que
            # enrolo. Se compara por cantidad porque es lo que la lectura trae;
            # al sincronizar se compara el contenido, que ademas detecta el dedo
            # cambiado por otro.
            fila["huellas_de_menos"] = bool(
                es_puerta and huellas_maestro
                and encontrado.get("huellas") is not None
                and encontrado["huellas"] < huellas_maestro)
            if fila["huellas_de_menos"]:
                resumen["huellas_de_menos"] += 1
            # El nombre solo se juzga si se pidió uno. Sin pedido, lo que tenga
            # el equipo está bien por definición: eso es lo que significa dejar
            # el campo vacío.
            fila["nombre_ok"] = _nombre_coincide(
                encontrado.get("nombre"), nombre_pedido) if nombre_pedido else None
            if fila["nombre_ok"] is False:
                resumen["nombre_distinto"] += 1

        # Sin huella no abre, así que pesa más que estar cargado. Pero "huellas"
        # puede venir en None porque no se pudieron leer los templates, y eso no
        # es lo mismo que no tener: con None no se concluye nada de la huella.
        sin_huella = encontrado is not None and encontrado.get("huellas") == 0

        if not es_puerta:
            # El equipo de asistencia. No se juzga si debería estar o no —ahí
            # está por fichar— y sobre todo no se dice que abre: no abre nada.
            fila["estado"] = ("maestro_sin_huella" if sin_huella
                              else "enrolado" if encontrado else "no_enrolado")
            # Dos situaciones distintas, y antes compartían una sola bandera:
            # el cartel decía «no tiene huella» de alguien que ni siquiera
            # estaba cargado. Las dos impiden copiar a una puerta, pero se
            # arreglan de forma opuesta.
            if sin_huella:
                # Está en el equipo y sin ninguna huella: hay que enrolarlo.
                resumen["sin_huella_maestro"] = True
            elif not encontrado:
                # No está en el equipo de asistencia. Puede ser normal —un
                # legajo que existe solo para abrir puertas y nunca ficha— o
                # puede ser que lo hayan borrado de ahí y haya quedado en las
                # puertas. Decir cuál de las dos no le toca al sistema.
                resumen["no_en_maestro"] = True
        elif debe and encontrado and sin_huella:
            fila["estado"] = "sin_huella"
            resumen["sin_huella"] += 1
        elif debe and encontrado:
            fila["estado"] = "abre"
            resumen["abre"] += 1
        elif debe:
            fila["estado"] = "falta"
            resumen["falta"] += 1
        elif encontrado:
            fila["estado"] = "sobra"
            resumen["sobra"] += 1
        else:
            fila["estado"] = "no_abre"

        fila["diagnostico"] = DIAGNOSTICOS[fila["estado"]]
        filas.append(fila)

    # Primero lo que está mal, y dentro de eso el orden del listado de equipos.
    # Primero lo que hay que hacer algo al respecto, y el maestro sin huella
    # arriba de todo: si falta ahí, falta en todas las puertas por ese motivo.
    orden = {"maestro_sin_huella": 0, "no_enrolado": 1, "falta": 2,
             "sin_huella": 3, "sobra": 4, "sin_leer": 5,
             "abre": 6, "enrolado": 7, "no_abre": 8}
    filas.sort(key=lambda f: (orden[f["estado"]], not f["es_puerta"]))
    return {"user_id": user_id, "resumen": resumen, "equipos": filas}
