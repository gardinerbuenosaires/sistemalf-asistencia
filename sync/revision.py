"""La revisión nocturna: mira todo una vez por día y deja un informe.

**Para qué existe.** El sistema tiene media docena de pantallas que solo hablan
si alguien las abre: el plan de cambios, las bajas pendientes, el respaldo de
huellas, los relojes. Cada una contesta bien lo suyo, y ninguna avisa. Con una
baja por mes es perfectamente posible que pasen seis meses sin que nadie entre
a mirar, y lo que se acumula ahí no es desprolijidad: es gente que sigue
abriendo puertas después de irse.

**Solo lee.** No aplica nada, nunca, y es una decisión tomada y no una etapa.
Un proceso que corrige solo a las tres de la mañana es el que deja a alguien
afuera un domingo a las siete sin que nadie sepa por qué. Lee, cuenta y deja
dicho lo que encontró; decidir sigue siendo de una persona, a la luz del día.

**Cuenta, no lista.** Cada hallazgo es un número y un lugar donde mirarlo, no
la enumeración de cada caso. Una puerta con ochenta personas cargadas de más
tiene que ocupar un renglón —«Vinos: 80 personas que no corresponden»— y no
ochenta: un informe que no entra en la pantalla no se lee, y el detalle ya está
en la pantalla que corresponde, que además es donde se arregla.

**Dos niveles.** Rojo es lo que significa que alguien puede entrar donde no
debe, o que el sistema perdió contacto con un lector. Ámbar es trabajo que
puede esperar a la semana. Si todo fuera rojo, nada sería rojo.

**Cada hallazgo dice con qué permiso se ve.** El informe mezcla accesos con
asistencia, y quien no puede mirar quién abre qué puerta tampoco puede verlo
contado. Si no, la línea del tablero diría «3 cosas» y al abrirla mostraría una.
"""
import logging

logger = logging.getLogger(__name__)

ROJO, AMBAR = "rojo", "ambar"


def _hallazgo(nivel, clave, titulo, cuantos, donde, permiso, detalle=None):
    return {"nivel": nivel, "clave": clave, "titulo": titulo, "cuantos": cuantos,
            "donde": donde, "permiso": permiso, "detalle": detalle}


def _equipos_mudos(lecturas, equipos):
    """Los que no contestaron o contestaron a medias.

    Una lista corta cuenta como no leída, y va en rojo junto con el silencio
    total: las dos significan lo mismo para quien tiene que decidir algo —no se
    sabe qué hay en ese equipo— y de las dos la lista corta es la peor, porque
    parece una respuesta.
    """
    mudos = []
    for eq in equipos:
        lec = lecturas.get(eq["id"]) or {}
        if not lec.get("ok"):
            mudos.append(f"{eq['nombre']} ({lec.get('error') or 'no contestó'})")
        elif lec.get("completa") is False:
            mudos.append(f"{eq['nombre']} (dice tener {lec.get('declarados')} "
                         f"y se leyeron {len(lec['usuarios'])})")
    return mudos


def correr(conn, lecturas, equipos, plan=None) -> list:
    """Arma la lista de hallazgos. No escribe nada en ningún equipo."""
    from sync.bajas import resumen as resumen_bajas
    from sync.huellas import resumen as resumen_huellas

    hallazgos = []

    # ── Rojo ──────────────────────────────────────────────────────────────
    mudos = _equipos_mudos(lecturas, equipos)
    if mudos:
        hallazgos.append(_hallazgo(
            ROJO, "equipos_mudos", "equipo(s) sin leer", len(mudos),
            "Configuracion > Dispositivos", "accesos:ver", "; ".join(mudos)))

    bajas = resumen_bajas(conn)
    if bajas["pendientes"]:
        hallazgos.append(_hallazgo(
            ROJO, "bajas_sin_cerrar", "baja(s) sin cerrar", bajas["pendientes"],
            "Configuracion > Dispositivos > Bajas pendientes", "accesos:ver",
            "Siguen con el número tomado o con huellas guardadas"))

    # Fichadas de gente dada de baja. Se registran desde siempre y hasta hoy no
    # las miraba nadie: hay endpoint y no hay pantalla.
    try:
        fichadas = conn.execute(
            "SELECT COUNT(*) FROM inconsistencias "
            " WHERE tipo = 'fichaje_baja' AND resuelta = 0").fetchone()[0]
    except Exception:
        fichadas = 0
    if fichadas:
        hallazgos.append(_hallazgo(
            ROJO, "fichaje_baja", "fichada(s) de alguien dado de baja", fichadas,
            "Registro", "asistencia:ver",
            "O la baja no llegó al lector, o su número ya es de otra persona"))

    # Cargados donde no les corresponde, por puerta y contados. El detalle --
    # quién es cada uno -- está en el plan, que además es donde se saca.
    if plan:
        for puerta in plan.get("puertas", []):
            de_mas = [s for s in puerta.get("sacar", [])
                      if s.get("motivo") in ("egresado", "desconocido", "sin_derecho")]
            if de_mas:
                hallazgos.append(_hallazgo(
                    ROJO, f"sobran:{puerta['id']}",
                    f"en {puerta['nombre']}: persona(s) que no corresponden",
                    len(de_mas), "Configuracion > Dispositivos > Plan de cambios",
                    "accesos:ver",
                    "Abren esa puerta y no deberían"))

    # ── Ámbar ─────────────────────────────────────────────────────────────
    if plan:
        sin_perfil = sum(
            1 for p in plan.get("puertas", [])
            for s in p.get("sacar", []) if s.get("motivo") == "sin_perfil")
        if sin_perfil:
            hallazgos.append(_hallazgo(
                AMBAR, "sin_perfil", "cargado(s) sin perfil asignado", sin_perfil,
                "Configuracion > Dispositivos > Plan de cambios", "accesos:ver",
                "Hasta que se les asigne uno, el sistema no sabe qué les toca"))

        faltan = sum(len(p.get("agregar", [])) for p in plan.get("puertas", []))
        if faltan:
            hallazgos.append(_hallazgo(
                AMBAR, "faltan_cargar", "persona(s) a las que les falta una puerta",
                faltan, "Configuracion > Dispositivos > Plan de cambios",
                "accesos:ver", "Su perfil se la da y el lector no las tiene"))

    huellas = resumen_huellas(conn)
    if huellas.get("sin_respaldo"):
        hallazgos.append(_hallazgo(
            AMBAR, "sin_respaldo", "persona(s) sin huella respaldada",
            huellas["sin_respaldo"], "Configuracion > Dispositivos > Huellas",
            "accesos:ver",
            "Si el equipo de fichaje se pierde, vuelven a enrolar el dedo"))

    orden = {ROJO: 0, AMBAR: 1}
    hallazgos.sort(key=lambda h: (orden[h["nivel"]], -h["cuantos"]))
    return hallazgos


def guardar(conn, hallazgos, error=None) -> int:
    """Deja el informe en la base. Devuelve el id de la corrida.

    Se guarda en vez de calcularse cuando alguien abre el tablero porque leer
    ocho lectores son varios segundos: una pantalla que se abre cien veces por
    día no puede pagar eso. Y guardarlo da lo otro que hace falta —cuándo
    corrió— que es lo que distingue «no hay nada» de «no corrió».
    """
    import json

    cur = conn.execute(
        """INSERT INTO revisiones (hallazgos, rojos, ambares, error)
           VALUES (?, ?, ?, ?)""",
        (json.dumps(hallazgos, ensure_ascii=False),
         sum(1 for h in hallazgos if h["nivel"] == ROJO),
         sum(1 for h in hallazgos if h["nivel"] == AMBAR),
         error))
    # Se conservan las últimas; el valor de esto es el estado de hoy y poder
    # mirar unos días atrás, no un archivo histórico.
    conn.execute("""DELETE FROM revisiones WHERE id NOT IN
                      (SELECT id FROM revisiones ORDER BY id DESC LIMIT 30)""")
    return cur.lastrowid


def ultima(conn, permisos=None) -> dict:
    """La última corrida, con los hallazgos que ese usuario puede ver.

    `permisos` es un conjunto de "modulo:accion". Si es None no se filtra —lo
    usan las pruebas y los scripts—; la pantalla siempre pasa los del usuario.
    """
    import json

    fila = conn.execute(
        """SELECT id, corrida_en, hallazgos, rojos, ambares, error
             FROM revisiones ORDER BY id DESC LIMIT 1""").fetchone()
    if not fila:
        return {"corrio": False, "corrida_en": None, "hallazgos": [],
                "rojos": 0, "ambares": 0, "error": None}

    hallazgos = json.loads(fila["hallazgos"] or "[]")
    if permisos is not None:
        hallazgos = [h for h in hallazgos if h["permiso"] in permisos]
    return {"corrio": True, "corrida_en": fila["corrida_en"],
            "hallazgos": hallazgos,
            "rojos": sum(1 for h in hallazgos if h["nivel"] == ROJO),
            "ambares": sum(1 for h in hallazgos if h["nivel"] == AMBAR),
            "error": fila["error"]}
