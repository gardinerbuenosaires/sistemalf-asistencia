"""Lo que el sistema le escribe a un lector.

Aparte de `lectores.py` a propósito: ese módulo promete ser solo lectura y la
promesa tiene que seguir valiendo. Acá está todo lo que puede cambiar un equipo,
en un solo archivo, para que se pueda leer entero antes de confiar en él.

Las tres reglas que valen para todo lo de acá:

  · **Se verifica releyendo.** Que el equipo conteste que sí no alcanza: un
    lector viejo puede aceptar el comando y no hacer nada. Lo que se informa es
    lo que se comprobó.

  · **Los demás tienen que quedar enteros.** Que la operación pedida haya
    salido es la parte fácil. Lo que hay que probar es que no se llevó puesto a
    nadie más, porque eso no se nota hasta que alguien se queda afuera.

  · **No se escribe sobre una lectura que no cierra.** Si lo leído no coincide
    con lo que el equipo declara tener, se aborta: el índice libre se calcula a
    partir de esa lista, y con una lista corta se sobrescribe a alguien.

Sobre pyzk. Dos cosas suyas no se pueden usar tal cual:

  · `set_user` deja en «usuario común» a cualquiera que no sea 0 o 14, así que
    un enrolador o un administrador pierden su nivel sin aviso. Por eso el
    paquete se arma acá.

  · Al grabar, la franja horaria se escribe en cero. Hoy en este local están
    todas en cero y no se pierde nada, pero eso se comprueba antes de escribir
    en un equipo, no se supone.
"""
import logging

logger = logging.getLogger(__name__)

# Los cuatro niveles del equipo. Los dos del medio son los que permiten
# administrar el lector parado frente a él.
NIVELES = {0: "usuario común", 2: "enrolador", 6: "administrador", 14: "super admin"}


def _conectar(dispositivo):
    from sync.lectores import _conectar as abrir
    return abrir(dispositivo["ip"], dispositivo.get("puerto", 4370),
                 dispositivo.get("password", 0), dispositivo.get("timeout", 10))


def _foto(conexion):
    """Cómo está el equipo: cada usuario con su índice, nombre, grupo y huellas."""
    from collections import Counter

    usuarios = conexion.get_users()
    huellas, crudas = Counter(), {}
    for h in conexion.get_templates():
        if getattr(h, "valid", 1):
            huellas[h.uid] += 1
            crudas.setdefault(h.uid, []).append(h)
    return {
        "declarados": getattr(conexion, "users", None),
        "crudos": {str(u.user_id).strip(): u for u in usuarios},
        "huellas_crudas": crudas,
        "usuarios": {
            str(u.user_id).strip(): {
                "uid": u.uid, "nombre": (u.name or "").strip(),
                "grupo": str(u.group_id).strip(), "privilegio": u.privilege,
                "huellas": huellas.get(u.uid, 0),
            } for u in usuarios},
    }


def _lectura_cierra(f):
    """El equipo dice cuántos tiene; si no coincide, la lectura vino cortada."""
    leidos, dice = len(f["usuarios"]), f["declarados"]
    if dice is None:
        return False, "el equipo no dijo cuántos usuarios tiene"
    if leidos != dice:
        return False, f"dice tener {dice} usuarios y se leyeron {leidos}"
    return True, f"{leidos} usuarios"


def _intactos(antes, despues, excepto):
    """
    Qué le pasó a los demás. `excepto` son los números que sí tenían que cambiar:
    uno solo, o todos los que tocó una pasada entera.

    Compara índice, nombre, grupo, privilegio y cantidad de huellas: un borrado
    que corre índices o una escritura que pisa a alguien se ven acá y en ningún
    otro lado.
    """
    tocados = {excepto} if isinstance(excepto, str) else set(excepto or ())
    problemas = []
    for numero, a in antes["usuarios"].items():
        if numero in tocados:
            continue
        d = despues["usuarios"].get(numero)
        if d is None:
            problemas.append(f"desapareció el {numero} ({a['nombre']})")
            continue
        for campo in ("uid", "nombre", "grupo", "privilegio", "huellas"):
            if a[campo] != d[campo]:
                problemas.append(f"al {numero} ({a['nombre']}) le cambió "
                                 f"{campo}: {a[campo]} → {d[campo]}")
    for numero in set(despues["usuarios"]) - set(antes["usuarios"]) - tocados:
        problemas.append(f"apareció un {numero} que nadie creó")
    return problemas


def _nombre_para_el_lector(configurado, del_maestro, numero):
    """
    Qué nombre se le escribe a una persona en una puerta, y si hubo que inventarlo.

    El orden es el configurado en el legajo, después el que el equipo de fichaje
    ya le muestra, y recién al final el número. El número no es una buena opción
    y por eso se avisa: pasa solo cuando no hay nombre en el legajo Y el equipo
    de fichaje no contestó, y conviene que se vea para arreglarlo.

    No se usa el apellido del legajo como último recurso, aunque exista. Quedaría
    escrito en el equipo pareciendo una decisión que alguien tomó, y nadie
    volvería a mirarlo.
    """
    elegido = (configurado or "").strip()
    if elegido:
        return elegido, False
    elegido = (getattr(del_maestro, "name", "") or "").strip()
    if elegido:
        return elegido, False
    return str(numero), True


def _escribir_usuario(conexion, uid, nombre, privilegio, grupo, numero, franja=0):
    """
    Crea o reescribe un usuario SIN degradarle el nivel.

    `set_user` de pyzk hace, antes de empaquetar:

        if privilege not in [USER_DEFAULT, USER_ADMIN]: privilege = USER_DEFAULT

    o sea que a un enrolador (2) o a un administrador (6) los deja como usuario
    común, sin avisar. Son los dos niveles que sirven para administrar el
    lector, así que perderlos no es un detalle: esa persona deja de poder
    enrolar a nadie y nadie se entera hasta que lo necesita.

    Acá se arma el mismo paquete y se manda con el mismo comando; lo único que
    no se hace es recortar el nivel. Y la franja viaja como parámetro en vez de
    ir clavada en cero.
    """
    from struct import pack
    from zk import const

    if conexion.user_packet_size != 28:
        raise RuntimeError("Este equipo usa el formato de 72 bytes; esta función "
                           "es para las puertas, que usan el de 28")
    codificacion = getattr(conexion, "encoding", "latin-1")
    paquete = pack("HB5s8sIxBHI", int(uid), int(privilegio), b"",
                   (nombre or "").encode(codificacion, errors="ignore"),
                   0, int(grupo or 0), int(franja), int(numero))
    respuesta = getattr(conexion, "_ZK__send_command")(const.CMD_USER_WRQ, paquete, 1024)
    if not respuesta.get("status"):
        raise RuntimeError("El equipo rechazó la escritura del usuario")
    conexion.refresh_data()


def _respaldar(conexion, puerta, usuario, huellas):
    """
    Guarda el registro completo antes de borrarlo, con sus huellas.

    Al lado de la base, como los respaldos del relevamiento. **Tiene huellas
    adentro: no sale de esta máquina.**

    Sin esto, sacar a alguien de un lector es irreversible. Y el caso que más
    necesita la herramienta —alguien que quedó cargado por un error— es
    justamente el que no se sabe de antemano si está bien decidido.
    """
    import json
    import os
    from datetime import datetime
    from pathlib import Path

    base = os.environ.get("DB_PATH")
    destino = (Path(base).resolve().parent if base else Path(".")) / "borrados"
    destino.mkdir(parents=True, exist_ok=True)
    ruta = destino / (f"{puerta['ip'].replace('.', '-')}"
                      f"-{usuario['user_id']}-{datetime.now():%Y%m%d-%H%M%S}.json")
    ruta.write_text(json.dumps({
        "equipo": puerta["nombre"], "ip": puerta["ip"],
        "cuando": datetime.now().strftime("%d-%m-%Y %H:%M:%S"),
        "uid": usuario["uid"], "user_id": usuario["user_id"],
        "nombre": usuario["nombre"], "grupo": usuario["grupo"],
        "privilegio": usuario["privilegio"],
        "huellas": [h.json_pack() for h in huellas],
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    return str(ruta)


def resultado_de_sacar(r: dict) -> str:
    """
    Cómo se registra un borrado: `sacado`, `pendiente` o `falló`.

    Son tres estados y no dos. Que la persona siga cargada no es una falla: el
    borrado quedó pendiendo y la próxima pasada lo retoma. Un problema con los
    demás usuarios sí es una falla, y es de las que hay que mirar hoy.
    """
    if r.get("ok"):
        return "sacado"
    return "pendiente" if r.get("sigue") else "falló"


def sacar_de_puerta(puerta: dict, numero: str) -> dict:
    """
    Saca a una persona de una puerta, con respaldo y verificación.

    Lo que hace que esto no sea un `delete_user` suelto son las dos lecturas.
    Antes, para guardar el respaldo y para comprobar que la lista vino completa:
    si vino corta, no se borra, porque borrar por índice sobre una lista
    incompleta es sacar a quien no era. Después, para verificar que la persona
    ya no está **y que los demás quedaron enteros** — un borrado puede correr
    los índices del resto, y eso no se nota hasta que alguien se queda afuera.

    No decide. Decidir si corresponde es de quien llama: desde el plan, porque
    la política dice que esa persona no va ahí; a mano, porque alguien lo pidió
    con un motivo. Acá se ejecuta y se verifica.
    """
    numero = str(numero).strip()
    conexion = None
    try:
        conexion, transporte = _conectar(puerta)
        antes = _foto(conexion)
        cierra, detalle = _lectura_cierra(antes)
        if not cierra:
            return {"ok": False, "error": f"Lectura no confiable: {detalle}. "
                                          f"No se borró nada."}
        objetivo = antes["usuarios"].get(numero)
        if objetivo is None:
            return {"ok": False, "no_estaba": True,
                    "error": f"El {numero} no está cargado en {puerta['nombre']}."}

        huellas = antes["huellas_crudas"].get(objetivo["uid"], [])
        respaldo = _respaldar(conexion, puerta, {**objetivo, "user_id": numero}, huellas)
        conexion.delete_user(uid=objetivo["uid"])

        # Se relee: que el equipo conteste que sí no alcanza, un lector viejo
        # puede aceptar el comando y no hacer nada.
        despues = _foto(conexion)
        sigue = numero in despues["usuarios"]
        problemas = _intactos(antes, despues, numero)
        bien = not sigue and not problemas
        return {"ok": bien, "transporte": transporte, "respaldo": respaldo,
                "nombre_equipo": objetivo["nombre"], "uid": objetivo["uid"],
                "huellas": len(huellas), "otros": len(antes["usuarios"]) - 1,
                "problemas": problemas,
                # Que siga cargado no es lo mismo que haber fallado: el borrado
                # sigue pendiendo y se va a reintentar. Un problema con los
                # demás, en cambio, es algo que hay que mirar ahora.
                "sigue": sigue,
                "error": None if bien else (
                    "sigue cargado después de borrarlo" if sigue
                    else "; ".join(problemas))}
    except Exception as exc:
        logger.warning("No se pudo sacar %s de %s: %s", numero, puerta.get("ip"), exc)
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        if conexion:
            try:
                conexion.disconnect()
            except Exception:
                pass


def _elegir_grupo(conexion, reparto):
    """
    En qué grupo del equipo va la persona que se está cargando.

    El criterio era «el más usado de esta puerta»: copiar lo que ya funciona en
    vez de probar algo nuevo. Sigue valiendo, con una corrección que apareció al
    leer el .209 — ahí hay dos franjas horarias cargadas de verdad, 08:00 a
    19:30 y 17:00 a 03:00, en grupos donde hoy no hay nadie.

    O sea que el grupo puede imponer horario. Y como el único equipo donde
    alguien asignó horarios a propósito es el .209 —en los demás las franjas
    abiertas son el estado de fábrica— poner a alguien en un grupo con horario
    nunca es lo que se quiso. Entonces se prefiere **el más usado que no
    restrinja**, y si hubo que desviarse del más usado se dice por qué.

    No se bloquea nunca. Si todos los grupos de esa puerta tienen horario, se
    copia el más usado igual: la alternativa sería dejar esa puerta inutilizable
    hasta que alguien camine hasta el equipo, y ahora el horario elegido se
    informa en rojo, así que la falla se ve antes de que alguien quede afuera.

    Devuelve (grupo, ventana, aviso).
    """
    from sync.lectores import ventanas_de_grupos

    if not reparto:
        # Puerta vacía: no hay de dónde copiar. El 1 es el que usan todas las
        # puertas de este local para la gente común.
        return "1", None, None

    orden = [g for g, _ in reparto.most_common()]
    ventanas = ventanas_de_grupos(conexion, orden)
    sin_horario = [g for g in orden if ventanas.get(g, {}).get("restringe") is False]

    if sin_horario:
        elegido = sin_horario[0]
        aviso = None
        if elegido != orden[0]:
            otro = ventanas[orden[0]]
            aviso = (f"el grupo más usado de esta puerta es el {orden[0]}, pero "
                     f"tiene horario ({otro['texto']}), así que se usó el "
                     f"{elegido}, que no le pone horario a nadie")
        return elegido, ventanas[elegido], aviso

    elegido = orden[0]
    ventana = ventanas.get(elegido)
    if ventana and ventana["restringe"] is None:
        aviso = ("no se pudo leer el horario de los grupos de esta puerta, así "
                 "que se copió el más usado sin poder comprobar si impone horario")
    else:
        aviso = ("todos los grupos de esta puerta tienen horario, así que no hubo "
                 "ninguno sin restricción para elegir")
    return elegido, ventana, aviso


def huellas_de_varios(dispositivo, numeros, guardadas=None):
    """
    Las huellas de varias personas en un equipo, de una sola conexión. SOLO LECTURA.

    Una por una sería una conexión por persona contra el equipo de fichaje, que
    es el de producción y el que está tomando asistencia mientras tanto. Leer el
    lista y las huellas una vez y repartirlas acá es la diferencia entre
    molestarlo una vez y molestarlo treinta.

    `guardadas` son las que hay respaldadas en la base, y se usan **solo para lo
    que el equipo no pudo dar**: porque no contestó, o porque esa persona no
    está cargada ahí. El equipo sigue siendo la fuente, y no por desconfianza
    del respaldo sino porque de ahí sale también el nombre corto que el lector
    muestra en pantalla, que el respaldo no tiene. Reemplazarlo haría que cargar
    a alguien le cambiara el nombre sin que nadie lo pidiera.

    Devuelve {numero: (usuario, [huellas])} con los que tienen, y
    {numero: motivo} con los que no, para poder decir por qué en cada caso.
    """
    pedidos = {str(n).strip() for n in numeros}
    if not pedidos:
        return {}, {}
    guardadas = {str(k).strip(): v for k, v in (guardadas or {}).items() if v}

    def _con_respaldo(traidas, faltan):
        """Lo que el equipo no dio, si está respaldado en la base."""
        for numero in list(faltan):
            if numero in guardadas:
                traidas[numero] = (None, guardadas[numero])
                faltan.pop(numero)
        return traidas, faltan

    conexion = None
    try:
        conexion, _t = _conectar(dispositivo)
        gente = {str(u.user_id).strip(): u for u in conexion.get_users()}
        por_uid = {}
        for h in conexion.get_templates():
            if getattr(h, "valid", 1):
                por_uid.setdefault(h.uid, []).append(h)

        traidas, faltan = {}, {}
        for numero in pedidos:
            quien = gente.get(numero)
            if quien is None:
                faltan[numero] = "no está cargado en el equipo de asistencia"
                continue
            suyas = por_uid.get(quien.uid, [])
            if not suyas:
                faltan[numero] = "está en el equipo de asistencia pero sin ninguna huella"
                continue
            traidas[numero] = (quien, suyas)
        return _con_respaldo(traidas, faltan)
    except Exception as exc:
        # El equipo no contesto. Es justo el caso para el que se guardan: sin el
        # respaldo, una puerta no se podria tocar hasta que el equipo de fichaje
        # vuelva.
        logger.warning("No se pudieron leer las huellas de %s: %s",
                       dispositivo.get("ip"), exc)
        return _con_respaldo(
            {}, {n: f"{type(exc).__name__}: {exc}" for n in pedidos})
    finally:
        if conexion:
            try:
                conexion.disconnect()
            except Exception:
                pass


def cargar_en_puerta(puerta: dict, maestro: dict, numero: str,
                     nombre: str = None, grupo: str = None,
                     guardadas: dict = None) -> dict:
    """
    Carga a una persona en una puerta, con su huella copiada del maestro.

    Sin huella no se carga. Un usuario cargado sin huella figura en la lista del
    equipo y no abre igual: parece hecho y no lo está, que es peor que no
    haberlo hecho — nadie vuelve a mirar algo que ya figura resuelto.

    El grupo, si no se indica, es el más frecuente de ese equipo. Si todos los
    que abren están en el grupo 1, crear a este en el 1 reproduce lo que ya
    funciona; elegir otro sería probar algo distinto sin querer.

    El grupo, si no se indica, lo elige `_elegir_grupo`: el más usado de esa
    puerta que no le imponga horario a nadie. Y en cualquier caso se lee en qué
    horario lo deja y se devuelve en `horario`, con `horario_restringe` en True
    si no es todo el día. Un número de grupo nadie lo revisa; "abre de 08:00 a
    19:30" sí. Y es la única forma de enterarse, porque meter a alguien en el
    grupo equivocado no da ningún error: simplemente un día a cierta hora no
    abre, y nadie relaciona una cosa con la otra.
    """
    from collections import Counter

    numero = str(numero).strip()
    traidas, faltan = huellas_de_varios(maestro, [numero], guardadas)
    if numero not in traidas:
        return {"ok": False, "error": f"No hay huella para copiar: "
                                      f"{faltan.get(numero, 'no se pudo leer')}"}
    quien, suyas = traidas[numero]

    conexion = None
    try:
        conexion, transporte = _conectar(puerta)
        antes = _foto(conexion)
        cierra, detalle = _lectura_cierra(antes)
        if not cierra:
            return {"ok": False, "error": f"Lectura no confiable: {detalle}. "
                                          f"No se escribió nada."}
        if numero in antes["usuarios"]:
            return {"ok": False, "error": f"El {numero} ya está cargado en "
                                          f"{puerta['nombre']}."}

        usados = {u["uid"] for u in antes["usuarios"].values()}
        uid = (max(usados) + 1) if usados else 1

        # El grupo del equipo, que es de donde el lector saca el horario en que
        # abre quien está en él: cada grupo apunta a franjas, y cada franja trae
        # los horarios de los siete días. Puede decidir que alguien no abra aun
        # estando cargado, por eso no es un dato de adorno.
        reparto = Counter(u["grupo"] for u in antes["usuarios"].values() if u["grupo"])
        ambiguo = len(reparto) > 1
        if grupo is None:
            grupo, ventana, aviso_grupo = _elegir_grupo(conexion, reparto)
        else:
            # Lo pidió quien llama: se respeta, pero igual se lee el horario.
            # Un número de grupo nadie lo revisa; «abre de 08:00 a 19:30» sí.
            from sync.lectores import ventana_del_grupo
            ventana, aviso_grupo = ventana_del_grupo(conexion, grupo), None
        # El nombre que se eligió para los lectores; si no hay, el del maestro,
        # que es lo que esa persona ya muestra en el otro equipo.
        # El nombre corto que el lector muestra en pantalla. Sale del legajo
        # —pestaña Accesos— y si ahi no hay nada se copia el que el equipo de
        # fichaje ya muestra, que es lo que esa persona viene viendo.
        #
        # Cuando no hay ninguno de los dos queda el numero. Es feo a proposito:
        # no se pone el apellido porque quedaria pareciendo configurado, y esto
        # se tiene que ver para que alguien lo arregle. Pasa en un solo caso
        # —sin nombre en el legajo Y con el equipo de fichaje caido— y se avisa.
        texto, por_defecto = _nombre_para_el_lector(nombre, quien, numero)

        _escribir_usuario(conexion, uid, texto, 0, grupo, numero)
        recien = next((u for u in conexion.get_users()
                       if str(u.user_id).strip() == numero), None)
        if recien is None:
            return {"ok": False, "error": "Se escribió el usuario pero no aparece "
                                          "al releer el equipo."}
        conexion.save_user_template(recien, suyas)

        despues = _foto(conexion)
        quedo = despues["usuarios"].get(numero)
        problemas = _intactos(antes, despues, numero)
        bien = quedo and quedo["huellas"] == len(suyas) and not problemas
        return {"ok": bool(bien), "transporte": transporte,
                "uid": uid, "grupo": grupo, "nombre_escrito": texto,
                "nombre_por_defecto": por_defecto,
                "grupo_ambiguo": ambiguo,
                "reparto_grupos": dict(reparto),
                "horario": ventana["texto"] if ventana else None,
                "horario_restringe": ventana["restringe"] if ventana else None,
                "aviso_grupo": aviso_grupo,
                "huellas": quedo["huellas"] if quedo else 0,
                "huellas_esperadas": len(suyas),
                "otros": len(antes["usuarios"]), "problemas": problemas,
                "error": None if bien else (
                    "; ".join(problemas) if problemas else
                    f"quedó con {quedo['huellas'] if quedo else 0} de "
                    f"{len(suyas)} huella(s)")}
    except Exception as exc:
        logger.warning("No se pudo cargar %s en %s: %s", numero, puerta.get("ip"), exc)
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        if conexion:
            try:
                conexion.disconnect()
            except Exception:
                pass


def aplicar_en_puerta(puerta: dict, maestro: dict, altas: list, bajas: list,
                      guardadas: dict = None) -> dict:
    """
    Aplica de una vez todo lo que el plan pide para una puerta.

    Por qué junto y no fila por fila. Cada fila suelta es conectarse, leer los
    usuarios y las huellas del equipo entero, escribir, y volver a leer todo
    para verificar. Con ochenta personas cargadas eso tarda, y treinta filas son
    treinta lecturas completas del mismo equipo: el proceso se hace tan largo
    que nadie lo usa. Acá se lee una vez al principio, se hacen todos los
    cambios, y se lee una vez al final.

    La verificación no se afloja por eso, al contrario: al final se comprueba
    cada alta y cada baja, y además que **nadie más haya cambiado**, con la foto
    completa de antes contra la de después. Eso es más fuerte que verificar de a
    uno, porque ve el efecto acumulado de toda la pasada.

    Un fallo no detiene al resto. Si una persona no se puede cargar, las demás
    siguen: lo contrario deja la puerta a medio aplicar en un punto que depende
    de en qué orden estaban las filas.

    `altas` son {"user_id", "nombre"} y `bajas` {"user_id", "motivo"}. Quién
    tiene que estar y quién no lo decidió el que llama, mirando la política;
    acá se ejecuta y se verifica.
    """
    numeros_altas = [str(a["user_id"]).strip() for a in altas]
    traidas, faltan = (huellas_de_varios(maestro, numeros_altas, guardadas)
                       if numeros_altas else ({}, {}))

    conexion, resultados = None, []
    try:
        conexion, transporte = _conectar(puerta)
        antes = _foto(conexion)
        cierra, detalle = _lectura_cierra(antes)
        if not cierra:
            return {"ok": False, "resultados": [],
                    "error": f"Lectura no confiable: {detalle}. No se tocó nada."}

        # El grupo se decide una sola vez para toda la pasada: es el mismo para
        # todos los que entran a esta puerta, y preguntarlo por persona serían
        # dos comandos más contra el equipo por cada uno.
        from collections import Counter
        reparto = Counter(u["grupo"] for u in antes["usuarios"].values() if u["grupo"])
        grupo, ventana, aviso_grupo = _elegir_grupo(conexion, reparto)

        tocados, usados = set(), {u["uid"] for u in antes["usuarios"].values()}

        # Las bajas primero: son las urgentes —un egresado que sigue abriendo— y
        # así una alta que falle no las deja para otro día.
        for baja in bajas:
            numero = str(baja["user_id"]).strip()
            objetivo = antes["usuarios"].get(numero)
            if objetivo is None:
                resultados.append({"user_id": numero, "accion": "sacar", "ok": False,
                                   "no_estaba": True,
                                   "error": "ya no estaba cargado"})
                continue
            try:
                huellas = antes["huellas_crudas"].get(objetivo["uid"], [])
                respaldo = _respaldar(conexion, puerta,
                                      {**objetivo, "user_id": numero}, huellas)
                conexion.delete_user(uid=objetivo["uid"])
                tocados.add(numero)
                resultados.append({"user_id": numero, "accion": "sacar", "ok": True,
                                   "nombre_equipo": objetivo["nombre"],
                                   "motivo": baja.get("motivo"), "respaldo": respaldo,
                                   "error": None})
            except Exception as exc:
                resultados.append({"user_id": numero, "accion": "sacar", "ok": False,
                                   "error": f"{type(exc).__name__}: {exc}"})

        for alta in altas:
            numero = str(alta["user_id"]).strip()
            if numero in antes["usuarios"]:
                resultados.append({"user_id": numero, "accion": "cargar", "ok": False,
                                   "ya_estaba": True, "error": "ya estaba cargado"})
                continue
            if numero not in traidas:
                resultados.append({"user_id": numero, "accion": "cargar", "ok": False,
                                   "error": f"No hay huella para copiar: "
                                            f"{faltan.get(numero, 'no se pudo leer')}"})
                continue
            quien, suyas = traidas[numero]
            texto, por_defecto = _nombre_para_el_lector(
                alta.get("nombre"), quien, numero)
            try:
                uid = max(usados) + 1 if usados else 1
                _escribir_usuario(conexion, uid, texto, 0, grupo, numero)
                recien = next((u for u in conexion.get_users()
                               if str(u.user_id).strip() == numero), None)
                if recien is None:
                    resultados.append({"user_id": numero, "accion": "cargar",
                                       "ok": False,
                                       "error": "se escribió pero no aparece al releer"})
                    continue
                conexion.save_user_template(recien, suyas)
                usados.add(uid)
                tocados.add(numero)
                resultados.append({"user_id": numero, "accion": "cargar", "ok": True,
                                   "nombre_escrito": texto, "grupo": grupo,
                                   "nombre_por_defecto": por_defecto,
                                   "huellas_esperadas": len(suyas), "error": None})
            except Exception as exc:
                resultados.append({"user_id": numero, "accion": "cargar", "ok": False,
                                   "error": f"{type(exc).__name__}: {exc}"})

        # Una sola relectura para verificar toda la pasada. Lo que se informa es
        # lo que se comprobó, no lo que el equipo contestó mientras se escribía.
        despues = _foto(conexion)
        for r in resultados:
            if not r["ok"]:
                continue
            quedo = despues["usuarios"].get(r["user_id"])
            if r["accion"] == "sacar" and quedo is not None:
                r["ok"], r["sigue"] = False, True
                r["error"] = "sigue cargado después de borrarlo"
            elif r["accion"] == "cargar":
                if quedo is None:
                    r["ok"] = False
                    r["error"] = "no quedó cargado"
                else:
                    r["huellas"] = quedo["huellas"]
                    if quedo["huellas"] != r["huellas_esperadas"]:
                        r["ok"] = False
                        r["error"] = (f"quedó con {quedo['huellas']} de "
                                      f"{r['huellas_esperadas']} huella(s)")

        problemas = _intactos(antes, despues, tocados)
        return {"ok": not problemas and all(r["ok"] for r in resultados),
                "transporte": transporte, "resultados": resultados,
                "problemas": problemas, "grupo": grupo,
                "horario": ventana["texto"] if ventana else None,
                "horario_restringe": ventana["restringe"] if ventana else None,
                "aviso_grupo": aviso_grupo,
                # Los que ya estaban y nadie tocó. Restar los tocados a secas
                # contaría de menos: los que se cargaron no estaban antes.
                "otros": len(set(antes["usuarios"]) - tocados),
                "error": "; ".join(problemas) if problemas else None}
    except Exception as exc:
        logger.warning("No se pudo aplicar el plan en %s: %s", puerta.get("ip"), exc)
        return {"ok": False, "resultados": resultados,
                "error": f"{type(exc).__name__}: {exc}"}
    finally:
        if conexion:
            try:
                conexion.disconnect()
            except Exception:
                pass
