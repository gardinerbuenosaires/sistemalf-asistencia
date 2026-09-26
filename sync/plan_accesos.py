"""El plan: qué habría que cambiar en cada lector para que refleje la política.

No encola cambios. Cuando alguien toca un perfil o una excepción, lo único que
cambia es la base — el **estado deseado**. Después esto lee cada equipo, lo
compara contra lo deseado y arma la diferencia.

Se eligió así y no una cola de órdenes porque una orden envejece mal: si en el
medio alguien tocó el equipo a mano, o restauraron un backup, "agregar a Juan en
Personal" puede haber dejado de corresponder. Comparar estados es idempotente,
se autocorrige venga el desvío de donde venga, y un equipo apagado no pierde
nada: la próxima vez que se lo alcance, se corrige.

Acá no se escribe nada. Esto arma el plan; aplicarlo es otra cosa, y no se
habilita hasta que escribir esté probado contra los equipos reales.
"""
import logging

logger = logging.getLogger(__name__)

# Por qué alguien sobra en un lector. El orden importa: es el que se usa para
# mostrar primero lo que más urge.
MOTIVOS_SACAR = {
    "egresado":     "Dado de baja en el sistema",
    "desconocido":  "No existe en el sistema",
    "sin_derecho":  "Su perfil ya no incluye esta puerta",
    "sin_politica": "Ningún perfil incluye esta puerta todavía",
}


def estado_deseado(conn) -> dict:
    """
    Quién debería estar cargado en cada puerta, según perfiles y excepciones.

    Devuelve {dispositivo_id: {user_id: datos_del_empleado}}.

    Solo entran los activos con número de dispositivo: sin número no hay forma
    de cargarlos en un lector, y un egresado no debería abrir nada.
    """
    puertas_de_perfil: dict[int, set] = {}
    for r in conn.execute("SELECT perfil_id, dispositivo_id FROM perfiles_dispositivos"):
        puertas_de_perfil.setdefault(r["perfil_id"], set()).add(r["dispositivo_id"])

    excepciones: dict[int, list] = {}
    for r in conn.execute(
        "SELECT empleado_id, dispositivo_id, modo FROM accesos_excepciones"
    ):
        excepciones.setdefault(r["empleado_id"], []).append(dict(r))

    # El perfil propio y nada más. El cargo propone, no da acceso: si diera,
    # cambiarle el cargo a alguien le cambiaría las puertas, y eso lo puede
    # hacer quien edita empleados aunque no tenga permiso de accesos.
    deseado: dict[int, dict] = {}
    for e in conn.execute(
        """SELECT e.id, e.user_id, e.nombre, e.apellido,
                  e.perfil_acceso_id AS perfil_id
             FROM empleados e
            WHERE e.activo = 1 AND e.user_id IS NOT NULL AND TRIM(e.user_id) <> ''"""
    ):
        puertas = set(puertas_de_perfil.get(e["perfil_id"], ())) if e["perfil_id"] else set()
        for x in excepciones.get(e["id"], ()):
            if x["modo"] == "quitar":
                puertas.discard(x["dispositivo_id"])
            else:
                puertas.add(x["dispositivo_id"])

        if not puertas:
            continue
        datos = {"empleado_id": e["id"], "user_id": str(e["user_id"]).strip(),
                 "nombre": f"{e['apellido']}, {e['nombre']}".strip(", ")}
        for did in puertas:
            deseado.setdefault(did, {})[datos["user_id"]] = datos
    return deseado


def _falta_huella(user_id, en_maestro, con_huella):
    """
    ¿Hay huella para copiarle a esta persona? Son tres situaciones, no dos.

    Devuelve (falta, motivo). Y la certeza importa tanto como la respuesta:
    afirmar que a alguien le falta la huella sin haberlo verificado manda a
    enrolar de nuevo a gente que ya estaba bien.

      · No está en el equipo de asistencia. Certeza total y sin leer ninguna
        huella: si no está cargado ahí, no hay ningún template suyo que copiar.
      · Está cargado ahí pero sin huella enrolada. Hace falta haber leído los
        templates para saberlo.
      · Está cargado y no se leyeron los templates. No se sabe, y se dice.
    """
    if en_maestro is None:
        return False, None                      # no se pudo leer el maestro
    if user_id not in en_maestro:
        return True, "no_en_maestro"
    if con_huella is None:
        return False, None                      # está, pero sin verificar huella
    if user_id not in con_huella:
        return True, "sin_template"
    return False, None


def armar_plan(conn, puertas: list, lecturas: dict, maestro: dict | None) -> dict:
    """
    Compara lo leído de cada puerta contra lo que debería tener.

    `lecturas` es {dispositivo_id: resultado de leer_padron}.
    `maestro` es el resultado de leer el equipo de asistencia, o None si no se
    pudo: de ahí sale la huella que habría que copiar, así que sin él no se
    puede saber a quién falta enrolar.
    """
    deseado = estado_deseado(conn)
    # Puertas que ningun perfil incluye. Importa distinguirlas: si una puerta
    # estuvo caida cuando se armaron los perfiles, quedo afuera de todos, y
    # entonces toda su gente figuraria como "su perfil ya no la incluye" —un
    # motivo falso que invita a sacar a gente que si tiene que entrar. No es lo
    # mismo "a esta persona le sacaron el acceso" que "esta puerta todavia no
    # esta en la politica".
    con_perfil = {
        r["dispositivo_id"] for r in conn.execute(
            "SELECT DISTINCT dispositivo_id FROM perfiles_dispositivos")
    }
    empleados = {
        str(r["user_id"]).strip(): dict(r)
        for r in conn.execute(
            """SELECT id, user_id, nombre, apellido, activo, fecha_egreso
                 FROM empleados WHERE user_id IS NOT NULL"""
        )
    }

    # Dos preguntas distintas sobre el maestro, y antes estaban confundidas en
    # una: `con_huella` miraba si la persona figuraba en su padrón, que no es lo
    # mismo que tener la huella. Alguien cargado ahí sin huella quedaba informado
    # como que la tenía, y el plan proponía copiar algo que no existe.
    #
    #   en_maestro   figura en el equipo de asistencia
    #   con_huella   figura Y tiene al menos una huella enrolada
    #
    # Las dos en None si el maestro no contestó: sin leerlo no se sabe, y el plan
    # lo dice en vez de suponer.
    en_maestro = con_huella = None
    if maestro and maestro.get("ok"):
        en_maestro = {u["user_id"] for u in maestro["usuarios"]}
        if maestro.get("huellas_leidas"):
            con_huella = {u["user_id"] for u in maestro["usuarios"]
                          if (u.get("huellas") or 0) > 0}

    salida, total = [], {"agregar": 0, "sacar": 0, "sin_huella": 0,
                         "sin_leer": 0, "puertas": len(puertas),
                         "no_en_maestro": 0}

    for d in puertas:
        lectura = lecturas.get(d["id"], {"ok": False, "error": "sin resultado", "usuarios": []})
        fila = {"id": d["id"], "nombre": d["nombre"], "ubicacion": d["ubicacion"],
                "ok": lectura["ok"], "error": lectura.get("error"),
                "en_algun_perfil": d["id"] in con_perfil,
                "agregar": [], "sacar": []}

        if not lectura["ok"]:
            total["sin_leer"] += 1
            salida.append(fila)
            continue

        actual = {u["user_id"] for u in lectura["usuarios"]}
        debe = deseado.get(d["id"], {})

        for user_id in sorted(debe.keys() - actual, key=lambda x: (len(x), x)):
            persona = dict(debe[user_id])
            # Sin huella en el maestro no hay nada que copiar: cargar al usuario
            # sin su huella lo deja sin poder abrir igual, y encima parece hecho.
            persona["sin_huella"], persona["motivo_huella"] = _falta_huella(
                user_id, en_maestro, con_huella)
            if persona["sin_huella"]:
                total["sin_huella"] += 1
            fila["agregar"].append(persona)

        for user_id in sorted(actual - debe.keys(), key=lambda x: (len(x), x)):
            emp = empleados.get(user_id)
            if emp is None:
                motivo = "desconocido"
            elif not emp["activo"]:
                motivo = "egresado"
            elif d["id"] not in con_perfil:
                motivo = "sin_politica"
            else:
                motivo = "sin_derecho"
            # Estar en una puerta y NO estar en el equipo de asistencia es una
            # baja que no se propagó: la dieron de baja, se sincronizó con el
            # maestro, y en las puertas quedó. Es evidencia del propio equipo, y
            # sirve justamente cuando el legajo ya no dice nada de esa persona.
            esta_en_maestro = None if en_maestro is None else (user_id in en_maestro)
            if esta_en_maestro is False:
                total["no_en_maestro"] += 1
            fila["sacar"].append({
                "user_id": user_id,
                "nombre": (f"{emp['apellido']}, {emp['nombre']}".strip(", ")
                           if emp else None),
                "empleado_id": emp["id"] if emp else None,
                "fecha_egreso": emp["fecha_egreso"] if emp else None,
                "motivo": motivo,
                "motivo_texto": MOTIVOS_SACAR[motivo],
                "en_maestro": esta_en_maestro,
            })

        orden = {"egresado": 0, "desconocido": 1, "sin_politica": 2, "sin_derecho": 3}
        fila["sacar"].sort(key=lambda s: (orden[s["motivo"]], len(s["user_id"]), s["user_id"]))
        total["agregar"] += len(fila["agregar"])
        total["sacar"] += len(fila["sacar"])
        salida.append(fila)

    return {
        "total": total,
        "puertas": salida,
        "huellas_verificadas": con_huella is not None,
        "maestro_leido": en_maestro is not None,
    }
