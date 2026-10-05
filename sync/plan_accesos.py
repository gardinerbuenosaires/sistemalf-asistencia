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

Cuando se escriba, dos cosas que no se pueden olvidar:

  · La franja horaria. Grabar una huella reenvía el registro del usuario entero
    —el protocolo los manda en el mismo paquete— y pyzk la escribe en cero.
    Mientras estén todas en cero no se pierde nada, pero eso hay que
    verificarlo, no suponerlo: `leer_cargados(con_franja=True)` lo contesta. Si
    alguna no es cero, hay que preservarla antes de escribirle a ese equipo.

  · El grupo. Se lee y se vuelve a escribir tal cual, pero solo si se le pasa a
    `set_user` el usuario leído del equipo. Un alta nace en grupo 0, y qué
    permite el grupo 0 depende de cómo esté configurado cada lector.

  · El nivel de usuario, que es quién puede administrar el lector parado frente
    a él: crear gente y tomarle la huella. `set_user` de pyzk hace

        if privilege not in [USER_DEFAULT, USER_ADMIN]: privilege = USER_DEFAULT

    o sea que a un enrolador (2) o a un administrador (6) los deja en usuario
    común sin avisar, y son justamente los dos niveles que se usan para eso.
    No se puede escribir con `set_user`: hay que armar el paquete sin ese
    recorte, como hace `escribir_usuario` en `scripts/probar_escritura.py`.

El final del módulo: dar de baja tiene que liberar el número.

Hoy la baja es solo `activo = 0`. El número queda tomado para siempre —`user_id`
es UNIQUE en empleados— y la persona sigue cargada en los equipos, porque de eso
se ocupa Enterprise. Cuando Enterprise no esté, la baja tiene que borrar a la
persona de TODOS los equipos (las puertas y el de asistencia) y recién después
liberar el número para que se pueda reutilizar.

Hay una sola condición, y de ella se deduce todo lo demás:

    el sistema borra del .201 recién cuando confirmó que ese número no está
    en ninguna puerta.

Alcanza con eso porque el lector solo ofrece números que él no tiene: mientras
el sistema no lo borre del .201, el lector lo sigue viendo ocupado y no se lo
sugiere a nadie. O sea que el número vuelve a circulación exactamente cuando el
sistema lo devuelve, y no puede reasignarse antes de que las puertas estén
limpias. No hay dos lados que sincronizar.

Esto vale porque el sistema va a ser el ÚNICO que borra. Hoy Enterprise también
borra, y esa convivencia —dos actores repartiendo el mismo recurso— es la que
obliga a liberar el número a mano cuando el lector reusa uno que el sistema
tiene tomado. Ese procedimiento manual ya existe y funciona; deja de hacer falta
cuando Enterprise no esté.

Y la falla parcial es lo normal: siempre hay un equipo apagado. Si la .204 no
contesta, el número no se libera y queda esperando; el trabajo nocturno lo
retoma y lo completa el día que el equipo vuelva. Sin cola de órdenes: vuelve a
comparar y sigue donde estaba.

La parte difícil ya está hecha y no fue a propósito: `estado_deseado` excluye a
los inactivos, así que una baja ya genera el "sacar" en todas las puertas sola.
Falta el equipo de asistencia, que hoy este módulo solo lee, y la compuerta que
libera el número cuando todos confirmaron.

El historial aguanta: los fichajes guardan `empleado_id`, y ninguna consulta
cruza fichajes con empleados por `user_id`. Vaciar el `user_id` del legajo viejo
le deja sus fichajes intactos y bien atribuidos. Lo que sí se pierde es poder
contestar "quién tenía el 42 antes", así que conviene guardarlo en algún lado
antes de vaciarlo.

Y si alguna vez se quieren horarios de verdad —"cocina entra de 6 a 16"— el
lugar donde cuelgan es `perfiles_dispositivos`: su clave es (perfil, puerta), y
como cada puerta es un equipo y el equipo guarda una franja por usuario y por
equipo, el grano ya coincide. Sería una columna nueva que apunte a una tabla de
horarios, más el mismo cambio en `accesos_excepciones`. Nada de lo que hay hoy
estorba: lo que cambia es que los conjuntos de puertas pasan a ser mapas de
puerta a horario.
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

    `lecturas` es {dispositivo_id: resultado de leer_cargados}.
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
    # De quién era cada número que se liberó. Un desconocido en una puerta casi
    # siempre es esto: alguien a quien se le soltó el número y cuya huella quedó
    # cargada. Decir quién era convierte un misterio en una baja sin terminar.
    liberados = {
        str(r["user_id_anterior"]).strip(): dict(r)
        for r in conn.execute(
            """SELECT id, user_id_anterior, user_id_liberado_en, nombre, apellido
                 FROM empleados
                WHERE user_id_anterior IS NOT NULL AND TRIM(user_id_anterior) <> ''"""
        )
    }

    # Dos preguntas distintas sobre el maestro, y antes estaban confundidas en
    # una: `con_huella` miraba si la persona estaba cargada en él, que no es lo
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
            # Si no existe hoy pero el número fue de alguien, se dice de quién:
            # es la diferencia entre "desconocido" y "baja sin terminar".
            antes_de = liberados.get(user_id) if emp is None else None
            fila["sacar"].append({
                "user_id": user_id,
                "nombre": (f"{emp['apellido']}, {emp['nombre']}".strip(", ")
                           if emp else None),
                "era_de": (f"{antes_de['apellido']}, {antes_de['nombre']}".strip(", ")
                           if antes_de else None),
                "liberado_en": antes_de["user_id_liberado_en"] if antes_de else None,
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


def plan_fichaje(conn, equipo: dict, lectura: dict, lecturas: dict,
                 puertas: list) -> dict:
    """
    Lo que hay que corregir en el equipo de fichaje. Es el que faltaba.

    El plan administraba solo puertas, así que una baja dejaba de abrir y
    **seguía pudiendo fichar**. Eso es al revés de lo que importa: que alguien
    que no trabaja más figure marcando asistencia ensucia los datos con los que
    se liquida.

    Acá solo hay bajas, y es a propósito. Agregar a alguien al equipo de fichaje
    no es algo que el sistema pueda hacer: de ahí salen las huellas, no van
    hacia ahí. Una persona nueva se enrola parada frente al lector, con el dedo.
    Por eso los activos que no están figuran aparte, como pendientes de enrolar,
    y no como algo para aplicar.

    La condición que ordena todo lo demás:

        no se borra del equipo de fichaje hasta confirmar que ese número no
        está en ninguna puerta.

    Porque el lector solo ofrece números que él no tiene: mientras el sistema no
    lo borre de ahí, nadie puede reusar ese número. Borrarlo antes de limpiar
    las puertas deja la huella vieja cargada en una puerta bajo un número que
    ya es de otra persona — el dedo del que se fue abriendo con el legajo del
    que entró.

    Y si alguna puerta no contestó, tampoco se borra: no haberla podido leer no
    es lo mismo que haberla leído vacía.
    """
    empleados = {
        str(r["user_id"]).strip(): dict(r)
        for r in conn.execute(
            """SELECT id, user_id, nombre, apellido, activo, fecha_egreso
                 FROM empleados WHERE user_id IS NOT NULL
                   AND TRIM(user_id) <> ''"""
        )
    }
    liberados = {
        str(r["user_id_anterior"]).strip(): dict(r)
        for r in conn.execute(
            """SELECT user_id_anterior, user_id_liberado_en, nombre, apellido
                 FROM empleados
                WHERE user_id_anterior IS NOT NULL AND TRIM(user_id_anterior) <> ''"""
        )
    }

    salida = {"id": equipo["id"], "nombre": equipo["nombre"],
              "ok": bool(lectura and lectura.get("ok")),
              "error": (lectura or {}).get("error"),
              "sacar": [], "sin_enrolar": [], "puertas_sin_leer": []}
    if not salida["ok"]:
        return salida

    # Dónde está cada número en las puertas, y cuáles no se pudieron leer. Las
    # dos cosas hacen falta: una para saber si se puede borrar, la otra para
    # saber si siquiera se puede responder esa pregunta.
    en_puertas: dict[str, list] = {}
    for d in puertas:
        suya = lecturas.get(d["id"], {})
        if not suya.get("ok"):
            salida["puertas_sin_leer"].append(d["nombre"])
            continue
        for u in suya["usuarios"]:
            en_puertas.setdefault(u["user_id"], []).append(d["nombre"])

    actual = {u["user_id"] for u in lectura["usuarios"]}
    activos = {n for n, e in empleados.items() if e["activo"]}

    for user_id in sorted(actual - activos, key=lambda x: (len(x), x)):
        emp = empleados.get(user_id)
        antes_de = liberados.get(user_id) if emp is None else None
        puertas_suyas = en_puertas.get(user_id, [])
        salida["sacar"].append({
            "user_id": user_id,
            "nombre": (f"{emp['apellido']}, {emp['nombre']}".strip(", ")
                       if emp else None),
            "era_de": (f"{antes_de['apellido']}, {antes_de['nombre']}".strip(", ")
                       if antes_de else None),
            "liberado_en": antes_de["user_id_liberado_en"] if antes_de else None,
            "empleado_id": emp["id"] if emp else None,
            "fecha_egreso": emp["fecha_egreso"] if emp else None,
            "motivo": "egresado" if emp else "desconocido",
            "motivo_texto": MOTIVOS_SACAR["egresado" if emp else "desconocido"],
            # Lo que decide si se puede o no, y por qué no.
            "en_puertas": puertas_suyas,
            "se_puede": not puertas_suyas and not salida["puertas_sin_leer"],
        })

    # Los activos que no están. No es un pendiente del sistema sino del local:
    # esa persona tiene que venir a poner el dedo. Pero explica por qué el plan
    # no puede cargarla en ninguna puerta.
    for user_id in sorted(activos - actual, key=lambda x: (len(x), x)):
        emp = empleados[user_id]
        salida["sin_enrolar"].append({
            "user_id": user_id,
            "nombre": f"{emp['apellido']}, {emp['nombre']}".strip(", "),
            "empleado_id": emp["id"],
        })
    return salida
