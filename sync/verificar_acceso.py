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
padrón parece resuelto. Se distingue aparte.

Solo lectura.
"""
import logging

logger = logging.getLogger(__name__)

# Qué se concluye de cada combinación, para una puerta.
#
#   debería abrir · está cargado · tiene huella
DIAGNOSTICOS = {
    "abre":          "Abre esta puerta",
    "sin_huella":    "Cargado pero SIN huella: no abre",
    "falta":         "Debería abrir y no está cargado",
    "sobra":         "Está cargado y no debería abrir",
    "no_abre":       "No abre, y no está cargado",
    "sin_leer":      "No se pudo leer el equipo",
}


def verificar(user_id, equipos: list, lecturas: dict, deseadas: set) -> dict:
    """
    Cruza a una persona contra cada equipo leído.

    `equipos` son filas de dispositivos (con `nombre`, `es_acceso`,
    `cuenta_asistencia`), `lecturas` es {id: resultado de leer_padron} y
    `deseadas` el conjunto de puertas que le tocan según perfil y excepciones.

    De los equipos que no son puerta —el de asistencia— no se opina si debería
    estar o no: ahí la persona está por fichar, no por abrir. Pero se informa,
    porque es de donde sale la huella que habría que copiar, y si no la tiene ahí
    no hay nada que copiar a ninguna puerta.
    """
    user_id = str(user_id).strip()
    filas = []
    resumen = {"abre": 0, "falta": 0, "sobra": 0, "sin_huella": 0, "sin_leer": 0}

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
            "cargado": None, "huellas": None, "nombre_en_equipo": None,
            "grupo": None, "uid": None,
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

        # Sin huella no abre, así que pesa más que estar cargado. Pero "huellas"
        # puede venir en None porque no se pudieron leer los templates, y eso no
        # es lo mismo que no tener: con None no se concluye nada de la huella.
        sin_huella = encontrado is not None and encontrado.get("huellas") == 0

        if not es_puerta:
            # El equipo de asistencia. No se juzga, se informa.
            fila["estado"] = ("sin_huella" if sin_huella
                              else "abre" if encontrado else "no_abre")
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
    orden = {"falta": 0, "sin_huella": 1, "sobra": 2, "sin_leer": 3,
             "abre": 4, "no_abre": 5}
    filas.sort(key=lambda f: (orden[f["estado"]], not f["es_puerta"]))
    return {"user_id": user_id, "resumen": resumen, "equipos": filas}
