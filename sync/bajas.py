"""Qué le queda al sistema de alguien que se fue.

Dar de baja a una persona es `activo = 0`, y hasta acá eso significaba dos
cosas muy distintas al mismo tiempo:

  · «lo di de baja hace cinco minutos y sigue cargado en seis puertas,
    con su número tomado y sus huellas guardadas» — eso es TRABAJO.
  · «se fue hace dos años y no queda nada suyo en ningún lado» — eso es
    HISTORIA.

Nada las distinguía, así que esconder a los inactivos de una lista escondía a
los dos por igual: el que ya está cerrado, que es lo que uno quiere, y el que
tiene cosas pendientes, que es justo el que hay que mirar.

Este módulo le pone nombre a esa diferencia. Tres estados en vez de dos:

  · **Trabaja** — activo, o con fecha de egreso futura. Una baja adelantada
    sigue entrando hasta la víspera, así que no es ninguna baja todavía.
  · **Baja pendiente** — ya no trabaja y el sistema todavía conserva algo
    suyo.
  · **Baja cerrada** — ya no trabaja y no queda nada suyo.

**Qué cuenta como «queda algo».** Dos cosas, y las dos se saben sin salir a la
red:

  1. El **número** sigue tomado. Mientras `user_id` no sea NULL, ese número es
     de esa persona y el lector no lo puede reusar.
  2. Quedan **huellas** respaldadas. Es un dato biométrico de alguien que ya
     no tiene nada que ver con el local.

Lo que este módulo NO sabe es si además sigue cargado en una puerta: eso solo
se averigua leyendo los equipos, y leer ocho equipos para dibujar una lista de
personas sería pagar varios segundos cada vez que alguien abre la pantalla. Esa
mitad la contesta el Plan de cambios, que lee y ya lo marca como que sobra.

Por eso el estado se llama «cerrada» y no «limpia»: afirma que el sistema no
conserva nada suyo, que es exactamente lo que acá se puede comprobar. La
diferencia importa, porque el orden en que uno hace las cosas a mano no está
garantizado: si alguien libera el número y olvida las huellas ANTES de sacarlo
de las puertas, acá va a figurar cerrada y el Plan va a seguir mostrándolo. No
es una contradicción: son dos preguntas distintas, y ninguna de las dos miente.
"""
from sync.plan_accesos import trabaja_hoy


def _ya_no_trabaja(alias="e"):
    return f"NOT {trabaja_hoy(alias)}"


def _queda_algo(alias="e"):
    """Lo que el sistema todavía conserva de esa persona, en SQL.

    El número y las huellas. Se escribe una sola vez y lo usan las dos
    consultas, porque el día que se agregue un tercer rastro —un remito sin
    devolver, lo que sea— tiene que aparecer en la lista Y en la marca de la
    ficha, o van a decir cosas distintas sobre la misma persona.
    """
    return (f"(({alias}.user_id IS NOT NULL AND TRIM({alias}.user_id) <> '')"
            f" OR EXISTS (SELECT 1 FROM huellas h WHERE h.empleado_id = {alias}.id))")


def ids_pendientes(conn) -> set:
    """Los `id` de empleados con la baja a medio terminar.

    Devuelve un conjunto y no una lista para que marcar un listado largo sea
    una pertenencia y no una búsqueda por cada fila.

    Si la tabla de huellas todavía no existe —una base que nunca migró el
    módulo de accesos— no se rompe: devuelve lo que se pueda saber sin ella.
    """
    import sqlite3
    try:
        filas = conn.execute(f"""
            SELECT e.id FROM empleados e
             WHERE {_ya_no_trabaja('e')} AND {_queda_algo('e')}
        """).fetchall()
    except sqlite3.OperationalError:
        filas = conn.execute(f"""
            SELECT e.id FROM empleados e
             WHERE {_ya_no_trabaja('e')}
               AND e.user_id IS NOT NULL AND TRIM(e.user_id) <> ''
        """).fetchall()
    return {f[0] for f in filas}


def pendientes(conn) -> list:
    """Las bajas a medio terminar, con qué le falta a cada una.

    Ordenadas por fecha de egreso, las más viejas primero: una baja de hace
    ocho meses sin cerrar dice algo peor que la de ayer, y el orden es lo único
    que hace que se vea sin tener que leer la lista entera.

    `dias` puede venir en None: hay bajas sin fecha de egreso cargada, y no
    inventar un número es preferible a mostrar un cero que parece recién hecho.
    """
    filas = conn.execute(f"""
        SELECT e.id, e.apellido, e.nombre, e.tipo, e.user_id, e.fecha_egreso,
               e.user_id_anterior, e.user_id_liberado_en,
               (SELECT COUNT(*) FROM huellas h WHERE h.empleado_id = e.id) AS huellas,
               CAST(julianday('now','localtime') - julianday(e.fecha_egreso) AS INTEGER) AS dias
          FROM empleados e
         WHERE {_ya_no_trabaja('e')} AND {_queda_algo('e')}
      ORDER BY e.fecha_egreso IS NULL, e.fecha_egreso, e.apellido, e.nombre
    """).fetchall()

    salida = []
    for f in filas:
        fila = dict(f)
        numero = (fila["user_id"] or "").strip()
        falta = []
        if numero:
            falta.append(f"el número {numero} sigue tomado")
        if fila["huellas"]:
            falta.append(f"{fila['huellas']} huella(s) guardadas")
        fila["falta"] = falta
        fila["solo_acceso"] = fila["tipo"] == "acceso"
        salida.append(fila)
    return salida


def resumen(conn) -> dict:
    """Las tres cuentas, para el botón y el encabezado de la lista.

    `cerradas` se informa aunque no haya nada que hacer con ellas. Es el número
    que da sentido al otro: «3 pendientes» solo, no dice si el circuito se usa;
    «3 pendientes y 48 cerradas» dice que normalmente se termina.
    """
    pend = ids_pendientes(conn)
    bajas = conn.execute(f"""
        SELECT COUNT(*) FROM empleados e WHERE {_ya_no_trabaja('e')}
    """).fetchone()[0]
    return {"pendientes": len(pend), "cerradas": bajas - len(pend), "bajas": bajas}
