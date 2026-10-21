"""Cerrar la baja de una persona: sacarla de todos lados y soltar su número.

El circuito que hasta ahora hacía Enterprise a mano. Cuando alguien se va hay
que sacarlo de todas las puertas y del equipo de fichaje, olvidar sus huellas y
liberar su número para que vuelva a estar disponible. Eran cuatro pasos en
cuatro lugares, y cuatro pasos manuales garantizan que «a medio terminar» sea
el estado normal.

**La regla que ordena todo: el número se libera último, y solo si lo anterior
salió completo.** Liberarlo es lo único irreversible del circuito — a partir de
ahí el lector lo va a ofrecer en el próximo alta, porque sugiere siempre el
libre más bajo. Si se libera mientras la persona sigue cargada en una puerta
que no contestó, esa puerta queda con una huella vieja abriendo bajo el nombre
de alguien nuevo. Por eso, ante cualquier cosa que no se pudo completar, el
número **queda ocupado** y la baja sigue en la lista de pendientes hasta que
alguien la retome. Que el trabajo quede a la vista es preferible a soltar un
número sin estar seguro.

**Un equipo que no contesta no es «ya no está ahí».** Es «no sé», y con «no sé»
no se libera nada. Es el mismo criterio de todo el módulo.

**Por qué se sincroniza antes de tocar el equipo de fichaje.** Las últimas
fichadas de esa persona viven en la memoria del lector hasta que alguien las
baja. Si la sacamos del equipo sin bajarlas, se pierden los días que entran en
la liquidación final. Y peor: si llegan después, con el número ya reasignado,
el importador busca quién tiene ese número HOY y se las acredita al nuevo, que
está activo y no dispara ninguna inconsistencia. Sincronizar primero cierra esa
ventana, igual que hace la limpieza automática de los días 1 y 15.

**Lo que no se borra nunca es la fila de la persona.** Se borra lo que es suyo
—la huella, que es un dato biométrico de alguien que ya no tiene nada que ver
con el local— y se conserva lo que es del sistema: que existió, qué número tuvo
y cuándo lo soltó. Sin eso, un número huérfano en una puerta no se puede
explicar, que es justo el problema que este módulo vino a resolver.
"""
import logging

logger = logging.getLogger(__name__)


def _lista_equipos(conn):
    """Los lectores que hay que revisar: las puertas y el de fichaje.

    Todos los que el sistema administra por pull y tienen IP. Un equipo que el
    sistema no administra no puede afirmar nada sobre él, y por eso tampoco
    puede decir que alguien ya no está en ningún lado.

    Trae `ubicacion` aunque acá no se use: estas filas se le pasan tal cual a
    `armar_plan`, que la necesita para nombrar cada puerta. Son las columnas
    que un equipo tiene que tener para poder entrar en el plan, no las que esta
    función mira.
    """
    return [dict(r) for r in conn.execute(
        """SELECT id, nombre, ubicacion, ip, puerto, password, timeout, protocolo,
                  es_acceso, cuenta_asistencia
             FROM dispositivos
            WHERE activo = 1 AND protocolo = 'pull' AND ip IS NOT NULL
              AND (es_acceso = 1 OR cuenta_asistencia = 1)
         ORDER BY es_acceso DESC, orden, id""")]


def _trabaja(conn, eid):
    from sync.plan_accesos import trabaja_hoy
    fila = conn.execute(
        f"SELECT {trabaja_hoy('e')} AS trabaja FROM empleados e WHERE e.id = ?",
        (eid,)).fetchone()
    return None if fila is None else bool(fila["trabaja"])


def cerrar(conn, empleado: dict, sincronizar=None, sacar=None, leer=None) -> dict:
    """
    Deja a esa persona fuera de todos los equipos y, si lo logra, suelta su número.

    `sincronizar`, `sacar` y `leer` se pueden pasar para las pruebas; en
    producción son las funciones de verdad. No se importan arriba a propósito:
    traer el lector y el downloader al importar este módulo ataría la base a la
    red para cualquiera que lo use.

    Devuelve siempre el detalle equipo por equipo, haya terminado o no. Un
    cierre a medias no es una falla: es trabajo que quedó, y la próxima pasada
    lo retoma desde donde está.
    """
    if sincronizar is None:
        from sync.downloader import sync_attendances as sincronizar
    if sacar is None:
        from sync.escritura import sacar_de_puerta as sacar
    if leer is None:
        from sync.lectores import leer_cargados as leer

    numero = str(empleado.get("user_id") or "").strip()
    eid = empleado["id"]

    # El portón: mientras la persona todavía trabaje no se toca nada. Una baja
    # adelantada se carga hoy con fecha futura y sigue fichando y entrando hasta
    # la víspera; cerrarla el día que se cargó la deja afuera antes de tiempo.
    if _trabaja(conn, eid):
        return {"ok": False, "no_corresponde": True, "equipos": [],
                "error": "Todavía trabaja: la baja tiene fecha futura. "
                         "Se puede cerrar recién a partir del día de egreso."}
    if not numero:
        return {"ok": False, "sin_numero": True, "equipos": [],
                "error": "No tiene número asignado: no hay nada que liberar."}

    equipos = _lista_equipos(conn)
    if not equipos:
        return {"ok": False, "equipos": [],
                "error": "No hay ningún lector que el sistema administre."}

    # El de fichaje primero, pero sincronizando antes de tocarlo: sus últimas
    # fichadas están en la memoria del equipo y se van con él.
    sync = None
    if any(e["cuenta_asistencia"] for e in equipos):
        try:
            sync = sincronizar()
        except Exception as exc:
            sync = {"error": f"{type(exc).__name__}: {exc}"}

    resultados, pendientes = [], []
    for eq in equipos:
        es_maestro = bool(eq["cuenta_asistencia"]) and not eq["es_acceso"]

        # Sin un sync limpio no se saca a nadie del equipo de fichaje: sería
        # perder sus últimos días de trabajo.
        if es_maestro and (sync is None or sync.get("error")):
            motivo = (sync or {}).get("error") or "no se pudo sincronizar"
            resultados.append({"equipo": eq["nombre"], "id": eq["id"],
                               "accion": "no se tocó", "ok": False,
                               "detalle": f"no se bajaron sus fichadas: {motivo}"})
            pendientes.append(eq["nombre"])
            continue

        lectura = leer(eq)
        if not lectura.get("ok"):
            resultados.append({"equipo": eq["nombre"], "id": eq["id"],
                               "accion": "no contestó", "ok": False,
                               "detalle": lectura.get("error")})
            pendientes.append(eq["nombre"])
            continue

        # Una lista corta no autoriza a concluir que no está: puede estar en la
        # parte que no se leyó.
        if lectura.get("completa") is False:
            resultados.append({"equipo": eq["nombre"], "id": eq["id"],
                               "accion": "lectura incompleta", "ok": False,
                               "detalle": f"dice tener {lectura.get('declarados')} "
                                          f"y se leyeron {len(lectura['usuarios'])}"})
            pendientes.append(eq["nombre"])
            continue

        if not any(u["user_id"] == numero for u in lectura["usuarios"]):
            resultados.append({"equipo": eq["nombre"], "id": eq["id"],
                               "accion": "ya no estaba", "ok": True, "detalle": None})
            continue

        r = sacar(eq, numero)
        salio = bool(r.get("ok")) or bool(r.get("no_estaba"))
        resultados.append({"equipo": eq["nombre"], "id": eq["id"],
                           "accion": "sacado" if salio else "no se pudo sacar",
                           "ok": salio, "detalle": r.get("error"),
                           "respaldo": r.get("respaldo")})
        if not salio:
            pendientes.append(eq["nombre"])

    if pendientes:
        return {"ok": False, "equipos": resultados, "pendientes": pendientes,
                "liberado": False, "huellas_olvidadas": 0,
                "error": "Quedó sin terminar en: " + ", ".join(pendientes)
                         + ". El número sigue ocupado hasta completarlo."}

    # Salió de todos lados. Recién acá se borra lo que es suyo y se suelta el
    # número, en una sola transacción: que quede la huella borrada y el número
    # tomado es recuperable, al revés no.
    from sync.huellas import olvidar

    olvidadas = olvidar(conn, eid)
    conn.execute("""UPDATE empleados
                       SET user_id_anterior = user_id,
                           user_id_liberado_en = datetime('now','localtime'),
                           user_id = NULL
                     WHERE id = ?""", (eid,))
    logger.info("Baja cerrada: empleado %s, número %s liberado, %s huella(s) olvidadas",
                eid, numero, olvidadas)
    return {"ok": True, "equipos": resultados, "pendientes": [],
            "liberado": True, "numero": numero, "huellas_olvidadas": olvidadas,
            "error": None}


def _donde_esta(numero, lecturas, equipos):
    """En qué equipos figura ese número, según una lectura ya hecha."""
    estaba = []
    for eq in equipos:
        lec = lecturas.get(eq["id"]) or {}
        if not lec.get("ok"):
            continue
        if any(u["user_id"] == numero for u in lec["usuarios"]):
            estaba.append(eq["nombre"])
    return estaba


def _lecturas_confiables(lecturas, equipos):
    """Los equipos que no se pudieron leer enteros, con el motivo.

    Una lista corta cuenta como no leída. Que alguien no aparezca en los 149
    usuarios que se leyeron de los 150 que el equipo dice tener no significa
    que no esté: puede estar en el que falta.
    """
    problemas = []
    for eq in equipos:
        lec = lecturas.get(eq["id"]) or {}
        if not lec.get("ok"):
            problemas.append((eq["nombre"], lec.get("error") or "no contestó"))
        elif lec.get("completa") is False:
            problemas.append((eq["nombre"],
                              f"dice tener {lec.get('declarados')} y se leyeron "
                              f"{len(lec['usuarios'])}"))
    return problemas


def comprobar(conn, lecturas, equipos) -> dict:
    """Para cada baja pendiente, en qué equipos sigue cargada.

    Es lo que la lista sola no puede decir: ella mira la base, y la base no
    sabe qué tiene cargado un lector. Se hace a pedido y no al abrir la lista
    porque son ocho equipos y varios segundos; pagarlo siempre, para una
    pantalla que se abre a mirar, sería cobrarle a todos lo que necesita uno.

    No cierra nada. Sirve para saber antes de apretar cuáles van a salir
    limpias y cuáles tienen trabajo.
    """
    from sync.bajas import pendientes as _pendientes

    personas = []
    for p in _pendientes(conn):
        numero = (p["user_id"] or "").strip()
        donde = _donde_esta(numero, lecturas, equipos) if numero else []
        personas.append({**p, "en_equipos": donde, "afuera": not donde})
    problemas = _lecturas_confiables(lecturas, equipos)
    return {"personas": personas,
            "equipos_con_problema": [{"equipo": n, "motivo": m} for n, m in problemas],
            "confiable": not problemas}


def cerrar_afuera(conn, lecturas, equipos) -> dict:
    """Cierra de una vez todas las bajas que ya no están en ningún equipo.

    No le escribe nada a ningún lector: esta gente ya salió, lo único que
    queda es la parte de base —olvidar las huellas y soltar el número—. Es el
    caso de la puesta en marcha, donde se juntaron años de bajas que nadie
    podía cerrar porque no había con qué.

    **Si algún equipo no contestó, no cierra a nadie.** No es exceso de celo:
    «no aparece» en una lectura que falló no significa «no está», y soltar un
    número mientras la persona sigue cargada en esa puerta lo pone en
    circulación con la huella vieja todavía abriendo. Con un equipo mudo no se
    puede afirmar de NADIE que esté afuera, así que la tanda entera espera.

    A los que siguen cargados no los toca. Para ésos el camino es el plan de
    cambios, que los saca de cada puerta en una sola conexión, y después esta
    misma tanda los cierra.
    """
    from sync.huellas import olvidar

    problemas = _lecturas_confiables(lecturas, equipos)
    if problemas:
        return {"ok": False, "cerradas": 0, "siguen": 0, "detalle": [],
                "equipos_con_problema": [{"equipo": n, "motivo": m}
                                         for n, m in problemas],
                "error": "No se pudo leer entero: "
                         + ", ".join(n for n, _ in problemas)
                         + ". Con un equipo sin leer no se puede afirmar que "
                           "nadie esté afuera, así que no se cerró ninguna."}

    from sync.bajas import pendientes as _pendientes

    cerradas, siguen, detalle = 0, 0, []
    for p in _pendientes(conn):
        numero = (p["user_id"] or "").strip()
        quien = f"{p['apellido']}, {p['nombre']}".strip(", ")
        donde = _donde_esta(numero, lecturas, equipos) if numero else []
        if donde:
            siguen += 1
            detalle.append({"empleado_id": p["id"], "empleado": quien,
                            "cerrada": False, "en_equipos": donde})
            continue

        olvidadas = olvidar(conn, p["id"])
        if numero:
            conn.execute("""UPDATE empleados
                               SET user_id_anterior = user_id,
                                   user_id_liberado_en = datetime('now','localtime'),
                                   user_id = NULL
                             WHERE id = ?""", (p["id"],))
        cerradas += 1
        detalle.append({"empleado_id": p["id"], "empleado": quien, "cerrada": True,
                        "numero": numero or None, "huellas_olvidadas": olvidadas})

    logger.info("Cierre en tanda: %s baja(s) cerradas, %s siguen cargadas",
                cerradas, siguen)
    return {"ok": True, "cerradas": cerradas, "siguen": siguen,
            "detalle": detalle, "equipos_con_problema": [], "error": None}
