"""Las huellas guardadas en la base: respaldo del equipo de fichaje.

Para qué. Hoy el único lugar donde están todas las huellas es el equipo de
fichaje. Hay una segunda copia, y no es nuestra: la tiene Enterprise. Como el
objetivo de todo este módulo es apagar Enterprise, estamos por quedarnos sin el
único respaldo vivo que existe — y sin él, un equipo quemado significa que las
casi doscientas personas vuelven a enrolarse con el dedo, de a una.

Tres cosas que no son obvias y que ordenan el diseño:

  · **Se guardan por legajo, no por número de dispositivo.** El número se libera
    cuando alguien se va y se le da a otro. Una huella atada a un número
    reasignado es el accidente que el módulo entero trata de impedir.

  · **El algoritmo viaja con cada huella.** v10 y v12 no son intercambiables ni
    convertibles. Un respaldo sin ese dato puede no entrar en el equipo de
    reemplazo, y eso se descubre el día que hace falta.

  · **Se borran cuando la baja termina.** No por espacio —son unos cientos de KB
    sobre dieciséis megas— sino porque guardar datos biométricos de gente que no
    trabaja más no cumple ninguna función.

Y lo que habilita de paso: cargar a alguien en una puerta deja de necesitar leer
el equipo de fichaje, que es el que está tomando asistencia mientras tanto.
"""
import logging

logger = logging.getLogger(__name__)


def respaldar_desde(conn, equipo: dict) -> dict:
    """
    Lee las huellas del equipo de fichaje y las guarda por legajo.

    Solo de quienes tienen legajo: una huella que no se puede atribuir a nadie
    no sirve para restaurar nada, porque al restaurarla habría que decidir de
    quién es. Esas quedan contadas aparte para que se vea que existen.

    Reemplaza lo guardado de cada persona que vino en esta lectura. Alguien
    puede haber agregado un dedo —pasa cuando se le degrada uno— y el respaldo
    tiene que quedar igual al equipo, no ser la suma de todo lo que alguna vez
    tuvo.

    No borra a los que no vinieron. Una lectura parcial no es una baja, y si el
    equipo contesta de menos lo que no se leyó tiene que seguir respaldado.
    """
    from sync.lectores import _conectar

    resumen = {"ok": False, "personas": 0, "huellas": 0, "sin_legajo": 0,
               "sin_huella": 0, "error": None}
    conexion = None
    try:
        conexion, _t = _conectar(equipo["ip"], equipo.get("puerto", 4370),
                                 equipo.get("password", 0), equipo.get("timeout", 10))
        usuarios = conexion.get_users()
        por_uid = {}
        for h in conexion.get_templates():
            if getattr(h, "valid", 1):
                por_uid.setdefault(h.uid, []).append(h)
    except Exception as exc:
        logger.warning("No se pudieron respaldar las huellas de %s: %s",
                       equipo.get("ip"), exc)
        resumen["error"] = f"{type(exc).__name__}: {exc}"
        return resumen
    finally:
        if conexion:
            try:
                conexion.disconnect()
            except Exception:
                pass

    legajos = {str(r["user_id"]).strip(): r["id"] for r in conn.execute(
        """SELECT id, user_id FROM empleados
            WHERE user_id IS NOT NULL AND TRIM(user_id) <> ''""")}
    algoritmo = equipo.get("algoritmo_huella")

    for u in usuarios:
        numero = str(u.user_id).strip()
        eid = legajos.get(numero)
        suyas = por_uid.get(u.uid, [])
        if eid is None:
            resumen["sin_legajo"] += 1
            continue
        if not suyas:
            resumen["sin_huella"] += 1
            continue
        # Reemplazo completo de esta persona: el respaldo tiene que quedar igual
        # al equipo, no ser la suma de todo lo que alguna vez tuvo.
        conn.execute("DELETE FROM huellas WHERE empleado_id = ?", (eid,))
        for h in suyas:
            conn.execute(
                """INSERT INTO huellas
                     (empleado_id, dedo, algoritmo, plantilla, tamano, equipo_id,
                      leida_en)
                   VALUES (?,?,?,?,?,?, datetime('now','localtime'))""",
                (eid, int(h.fid), algoritmo, h.template, len(h.template),
                 equipo.get("id")))
            resumen["huellas"] += 1
        resumen["personas"] += 1

    resumen["ok"] = True
    return resumen


def guardadas_de(conn, numeros):
    """
    Las huellas guardadas de varias personas, listas para escribir en un equipo.

    Devuelve {numero: [Finger]} con las que hay. Los que faltan no se inventan:
    quien llama decide si va a buscarlas al equipo de fichaje o avisa.
    """
    from zk.finger import Finger

    pedidos = [str(n).strip() for n in numeros]
    if not pedidos:
        return {}
    marcas = ",".join("?" * len(pedidos))
    filas = conn.execute(
        f"""SELECT e.user_id, h.dedo, h.plantilla
              FROM huellas h JOIN empleados e ON e.id = h.empleado_id
             WHERE TRIM(e.user_id) IN ({marcas})
             ORDER BY e.user_id, h.dedo""", pedidos).fetchall()

    salida = {}
    for f in filas:
        numero = str(f["user_id"]).strip()
        # El uid del equipo se completa al escribir: es el índice interno de ESE
        # lector, y acá todavía no se sabe cuál va a tocar.
        salida.setdefault(numero, []).append(
            Finger(uid=0, fid=f["dedo"], valid=1, template=f["plantilla"]))
    return salida


def olvidar(conn, empleado_id) -> int:
    """
    Borra las huellas guardadas de una persona. Devuelve cuántas borró.

    Se llama cuando la baja termina: la persona ya no está en ningún equipo y el
    número volvió a circulación. A partir de ahí la huella no tiene para qué
    existir.

    Queda el respaldo del borrado de cada equipo, que es otra cosa y tiene otro
    propósito: poder deshacer un borrado que estuvo mal.
    """
    cur = conn.execute("DELETE FROM huellas WHERE empleado_id = ?", (empleado_id,))
    return cur.rowcount or 0


def resumen(conn) -> dict:
    """Cuántas hay guardadas y de cuándo, para poder mirarlo sin consultar nada."""
    fila = conn.execute(
        """SELECT COUNT(DISTINCT empleado_id) AS personas, COUNT(*) AS huellas,
                  MAX(leida_en) AS ultima, SUM(tamano) AS bytes
             FROM huellas""").fetchone()
    # Cuántos de los que trabajan hoy no tienen respaldo. Es el número que
    # importa: el respaldo sirve por los que cubre, no por los que tiene.
    from sync.plan_accesos import trabaja_hoy
    faltan = conn.execute(
        f"""SELECT COUNT(*) FROM empleados e
             WHERE {trabaja_hoy('e')}
               AND e.user_id IS NOT NULL AND TRIM(e.user_id) <> ''
               AND NOT EXISTS (SELECT 1 FROM huellas h WHERE h.empleado_id = e.id)"""
    ).fetchone()[0]
    return {"personas": fila["personas"] or 0, "huellas": fila["huellas"] or 0,
            "ultima": fila["ultima"], "bytes": fila["bytes"] or 0,
            "sin_respaldo": faltan}
