"""Lectura del padrón de un lector y comparación contra los empleados.

Para qué sirve. Verificar que lo cargado en un lector coincida con la realidad
es la tarea más frecuente del control de accesos, y confirmar que una baja salió
de todos los equipos es la más riesgosa. Hoy las dos se hacen a ojo, equipo por
equipo, en pantallas distintas. Acá se hacen de una.

Es SOLO LECTURA. No escribe nada en ningún equipo.

Sobre el grupo. Cada usuario pertenece a un grupo dentro del lector, y de ahí
salen las reglas de acceso que el equipo aplica solo. Se lee y se informa, pero
no se toca: el día que el sistema escriba usuarios va a tener que respetarlo,
porque cambiarlo sin querer le cambia a alguien por dónde y cuándo entra.

Lo que todavía no se lee es la franja horaria por usuario. El equipo la tiene y
el protocolo la manda, pero pyzk la descarta al parsear, así que sacarla
requiere rehacer ese parseo. No hace falta para comparar padrones; sí va a hacer
falta antes de escribir, porque pyzk la pisa con cero al grabar un usuario.
"""
import logging

logger = logging.getLogger(__name__)


def _conectar(ip, puerto, password, timeout):
    """
    Primero TCP y, si no contesta, UDP: los equipos viejos a veces solo hablan
    UDP, y probar solo TCP los da por muertos sin serlo.
    """
    from zk import ZK

    ultimo = None
    for udp in (False, True):
        try:
            zk = ZK(ip, port=int(puerto), timeout=int(timeout),
                    password=int(password), force_udp=udp,
                    ommit_ping=True, encoding="latin-1")
            return zk.connect(), ("udp" if udp else "tcp")
        except Exception as exc:
            ultimo = exc
    raise ultimo


def _contar_huellas(conexion) -> dict | None:
    """
    Cuántas huellas tiene cada usuario en este equipo, por uid interno.

    Se lee aparte porque el padrón no la trae: el equipo devuelve los usuarios
    en un paquete y las huellas en otro. Importa porque un usuario cargado sin
    huella figura en la lista y no abre igual — parece hecho y no lo está.

    Devuelve None si no se pudo leer. Es a propósito: informar "no tiene huella"
    porque falló la lectura haría borrar y recargar gente que estaba bien.
    """
    try:
        cuenta: dict[int, int] = {}
        for h in conexion.get_templates():
            if getattr(h, "valid", 1):
                cuenta[h.uid] = cuenta.get(h.uid, 0) + 1
        return cuenta
    except Exception as exc:
        logger.warning("No se pudieron leer las huellas: %s", exc)
        return None


def leer_padron(dispositivo: dict, con_huellas: bool = False) -> dict:
    """
    Trae los usuarios cargados en un lector.

    `dispositivo` es una fila de la tabla. Devuelve
    {ok, transporte, usuarios:[...], error}. Nunca levanta excepción: un equipo
    apagado es un resultado válido y la pantalla tiene que poder mostrarlo.

    Con `con_huellas` trae además cuántas huellas tiene cada uno. Es una lectura
    más y bastante más pesada —son todos los templates del equipo— así que no va
    por defecto: sirve cuando la pregunta es "¿esta persona realmente puede
    abrir?", no cuando solo se comparan padrones.
    """
    if dispositivo.get("protocolo") == "push":
        return {"ok": False, "usuarios": [], "transporte": None,
                "error": "Es un equipo push: no atiende llamadas, es él quien "
                         "llama al sistema. Su padrón no se puede consultar así."}
    if not dispositivo.get("ip"):
        return {"ok": False, "usuarios": [], "transporte": None,
                "error": "El equipo no tiene IP cargada"}

    conexion = None
    try:
        conexion, transporte = _conectar(
            dispositivo["ip"], dispositivo.get("puerto", 4370),
            dispositivo.get("password", 0), dispositivo.get("timeout", 10),
        )
        usuarios = [
            {
                "uid":        u.uid,
                "user_id":    str(u.user_id).strip(),
                "nombre":     (u.name or "").strip(),
                "privilegio": u.privilege,
                "tarjeta":    u.card,
                "grupo":      str(u.group_id).strip() if u.group_id not in (None, "") else None,
            }
            for u in conexion.get_users()
        ]
        huellas = _contar_huellas(conexion) if con_huellas else None
        if con_huellas:
            for u in usuarios:
                u["huellas"] = huellas.get(u["uid"], 0) if huellas is not None else None
        return {"ok": True, "transporte": transporte, "usuarios": usuarios,
                "error": None, "huellas_leidas": None if not con_huellas else huellas is not None}
    except Exception as exc:
        logger.warning("No se pudo leer el padrón de %s: %s", dispositivo.get("ip"), exc)
        return {"ok": False, "usuarios": [], "transporte": None,
                "error": f"{type(exc).__name__}: {exc}"}
    finally:
        if conexion:
            try:
                conexion.disconnect()
            except Exception:
                pass


def leer_padrones(dispositivos: list, con_huellas=False) -> dict:
    """
    Lee varios lectores a la vez. Devuelve {id_dispositivo: resultado}.

    En paralelo y no de a uno: con seis equipos, cada uno reintentando por TCP y
    después por UDP, uno apagado hace esperar a todos los demás. Leer es una
    operación de red, así que los hilos sirven aunque sea Python.

    El límite de hilos existe porque un local puede tener muchos lectores y no
    tiene sentido abrirle una conexión a cada uno al mismo tiempo.
    """
    from concurrent.futures import ThreadPoolExecutor

    if not dispositivos:
        return {}
    # `con_huellas` puede ser un booleano para todos, o el conjunto de ids a los
    # que pedírselas. Existe la segunda forma porque el caso real es mixto: al
    # maestro hay que leerle las huellas y a las puertas no, y hacerlo en dos
    # tandas le sumaría a la pantalla el tiempo del maestro en serie.
    pedir = ((lambda d: bool(con_huellas)) if isinstance(con_huellas, bool)
             else (lambda d: d["id"] in con_huellas))
    with ThreadPoolExecutor(max_workers=min(8, len(dispositivos))) as pool:
        resultados = pool.map(lambda d: leer_padron(d, pedir(d)), dispositivos)
    return {d["id"]: r for d, r in zip(dispositivos, list(resultados))}


# Los equipos viejos guardan el nombre en un campo más corto y lo cortan al
# grabarlo, así que el nombre del lector CASI NUNCA es igual al del legajo. Un
# nombre cortado no es un problema; confundirlo con otra persona manda a
# investigar decenas de casos que no lo son, y una pantalla que marca cosas que
# no son problemas deja de mirarse.
#
# Por eso la comparación es por palabras y tolerante:
#
#   · sin acentos, sin mayúsculas, sin puntuación — el equipo guarda PEREZ y el
#     legajo dice Pérez, y son la misma persona
#   · sin importar el orden — "ANA GOMEZ" y "GOMEZ, ANA" son la misma persona
#   · cada palabra del lector alcanza con que sea el COMIENZO de alguna del
#     legajo, que es exactamente lo que hace el corte por largo
#
# Solo se marca cuando alguna palabra del lector no se parece a ninguna del
# legajo. Eso ya no es un corte: es otro nombre, y casi siempre un número
# reutilizado con el empleado anterior todavía adentro.
def _normalizar(texto: str) -> list:
    import unicodedata
    sin_tilde = "".join(c for c in unicodedata.normalize("NFKD", texto or "")
                        if not unicodedata.combining(c))
    limpio = "".join(c if c.isalnum() else " " for c in sin_tilde.upper())
    return [p for p in limpio.split() if p]


def _mismo_nombre(en_lector: str, en_sistema: str) -> bool:
    palabras_lector = _normalizar(en_lector)
    palabras_sistema = _normalizar(en_sistema)
    if not palabras_lector or not palabras_sistema:
        return True          # sin con qué comparar no se acusa a nadie
    return all(
        any(p.startswith(q) or q.startswith(p) for q in palabras_sistema)
        for p in palabras_lector
    )


def comparar_con_empleados(usuarios: list, empleados: dict) -> dict:
    """
    Cruza el padrón del lector contra los empleados del sistema.

    `empleados` es {user_id: fila}. Clasifica cada persona cargada en el equipo:

      · `de_baja`     está en el equipo y en el sistema figura desvinculada.
                      Si esa puerta está en servicio, sigue abriendo.
      · `desconocido` el número no existe en el sistema. O lo cargaron a mano en
                      el equipo, o el legajo se borró y el lector no se enteró.
      · `ok`          todo en orden.

    Además marca `nombre_distinto` cuando el nombre del equipo no coincide con el
    del legajo y no es un simple corte por largo: eso suele ser un número
    reutilizado, con el empleado anterior todavía cargado.
    """
    # El nombre más largo que hay en este equipo. Es informativo —da una idea de
    # a cuántos caracteres corta— y nada depende de él: la comparación de nombres
    # tolera el corte por sí sola, sin tener que adivinar el ancho del campo.
    limite = max((len(u["nombre"]) for u in usuarios), default=0)
    # Las huellas son una lectura aparte y no siempre se piden. Cuando no se
    # pidieron, el conteo va en None: informar "0 sin huella" sin haberlas leido
    # es decir que todos pueden abrir sin haberlo verificado.
    huellas_leidas = any("huellas" in u for u in usuarios)
    filas, resumen = [], {"total": len(usuarios), "de_baja": 0, "desconocidos": 0,
                          "nombre_distinto": 0, "ok": 0,
                          "sin_huella": 0 if huellas_leidas else None}

    for u in usuarios:
        emp = empleados.get(u["user_id"])
        fila = dict(u, estado="ok", empleado=None, nombre_distinto=False)

        if emp is None:
            fila["estado"] = "desconocido"
            resumen["desconocidos"] += 1
        else:
            nombre_sistema = f"{emp['apellido']}, {emp['nombre']}".strip(", ")
            fila["empleado"] = {
                "id": emp["id"], "nombre": nombre_sistema,
                "activo": emp["activo"], "fecha_egreso": emp["fecha_egreso"],
                "tipo": emp["tipo"],
            }
            if not emp["activo"]:
                fila["estado"] = "de_baja"
                resumen["de_baja"] += 1
            else:
                resumen["ok"] += 1

            if not _mismo_nombre(u["nombre"], nombre_sistema):
                fila["nombre_distinto"] = True
                resumen["nombre_distinto"] += 1

        # Cargado sin huella: figura en la lista y no abre igual. Se cuenta
        # aparte de los estados porque no es un problema de identidad —la
        # persona es quien dice ser— sino de que la carga quedó a medias.
        if u.get("huellas") == 0:
            resumen["sin_huella"] += 1

        filas.append(fila)

    # Primero lo que hay que mirar: las bajas arriba de todo, después los
    # desconocidos, y el resto por número.
    orden = {"de_baja": 0, "desconocido": 1, "ok": 2}
    filas.sort(key=lambda f: (orden[f["estado"]], len(f["user_id"]), f["user_id"]))
    return {"resumen": resumen, "filas": filas, "nombre_limite": limite,
            "huellas_leidas": huellas_leidas}
