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
    Qué le pasó a los demás. `excepto` es el número que sí tenía que cambiar.

    Compara índice, nombre, grupo, privilegio y cantidad de huellas: un borrado
    que corre índices o una escritura que pisa a alguien se ven acá y en ningún
    otro lado.
    """
    problemas = []
    for numero, a in antes["usuarios"].items():
        if numero == excepto:
            continue
        d = despues["usuarios"].get(numero)
        if d is None:
            problemas.append(f"desapareció el {numero} ({a['nombre']})")
            continue
        for campo in ("uid", "nombre", "grupo", "privilegio", "huellas"):
            if a[campo] != d[campo]:
                problemas.append(f"al {numero} ({a['nombre']}) le cambió "
                                 f"{campo}: {a[campo]} → {d[campo]}")
    for numero in set(despues["usuarios"]) - set(antes["usuarios"]) - {excepto}:
        problemas.append(f"apareció un {numero} que nadie creó")
    return problemas


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


def huellas_de(dispositivo, numero):
    """Las huellas de una persona en un equipo. SOLO LECTURA."""
    conexion = None
    try:
        conexion, _t = _conectar(dispositivo)
        quien = next((u for u in conexion.get_users()
                      if str(u.user_id).strip() == str(numero).strip()), None)
        if quien is None:
            return None, "no está cargado en el equipo de asistencia"
        suyas = [h for h in conexion.get_templates()
                 if h.uid == quien.uid and getattr(h, "valid", 1)]
        if not suyas:
            return None, "está en el equipo de asistencia pero sin ninguna huella"
        return (quien, suyas), None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"
    finally:
        if conexion:
            try:
                conexion.disconnect()
            except Exception:
                pass


def cargar_en_puerta(puerta: dict, maestro: dict, numero: str,
                     nombre: str = None, grupo: str = None) -> dict:
    """
    Carga a una persona en una puerta, con su huella copiada del maestro.

    Sin huella no se carga. Un usuario cargado sin huella figura en la lista del
    equipo y no abre igual: parece hecho y no lo está, que es peor que no
    haberlo hecho — nadie vuelve a mirar algo que ya figura resuelto.

    El grupo, si no se indica, es el más frecuente de ese equipo. Si todos los
    que abren están en el grupo 1, crear a este en el 1 reproduce lo que ya
    funciona; elegir otro sería probar algo distinto sin querer.
    """
    from collections import Counter

    numero = str(numero).strip()
    traido, error = huellas_de(maestro, numero)
    if error:
        return {"ok": False, "error": f"No hay huella para copiar: {error}"}
    quien, suyas = traido

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
        if grupo is None:
            cuenta = Counter(u["grupo"] for u in antes["usuarios"].values() if u["grupo"])
            grupo = cuenta.most_common(1)[0][0] if cuenta else "1"
        # El nombre que se eligió para los lectores; si no hay, el del maestro,
        # que es lo que esa persona ya muestra en el otro equipo.
        texto = (nombre or "").strip() or (quien.name or "").strip() or numero

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
