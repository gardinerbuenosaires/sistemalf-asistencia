"""Esquema del módulo de control de accesos.

Vive en un archivo aparte, por el mismo motivo que `uniformes_schema.py`: el
módulo se desarrolla en una rama larga y un bloque grande dentro de `_migrate()`
garantiza un merge conflictivo con main. Acá el punto de contacto es una línea.

Todo es aditivo: crea la tabla `dispositivos` y no modifica ninguna existente.

Qué resuelve. Hasta ahora el lector biométrico eran cuatro claves sueltas en
`configuracion` (`device_ip`, `device_port`, `device_password`, `device_timeout`),
que solo alcanzan para un equipo. El relevamiento mostró que un local tiene un
maestro de asistencia y varios lectores de puerta, así que la configuración pasa
a ser una tabla con una fila por equipo.

Por qué estas columnas y no menos. Hay tres que hoy no usa nadie y están a
propósito, porque agregarlas después de tener equipos cargados es una migración:

  · `protocolo`    pull  = el sistema llama al equipo por IP (lo que hacemos hoy)
                   push  = el equipo llama al sistema, y se identifica por número
                           de serie, no por IP. Los equipos nuevos vienen así.
  · `numero_serie` la identidad de un equipo push. Un equipo pull se direcciona
                   por IP; uno push puede ni tenerla fija.
  · `algoritmo_huella` v10 y v12 no son intercambiables y no hay conversión
                   posible. Una huella solo se le puede mandar a un equipo del
                   mismo algoritmo, así que el dato tiene que viajar con el equipo.

`cuenta_asistencia` es la que evita el accidente más caro: el procesador alterna
entrada/salida por orden cronológico, así que si las pasadas por una puerta
entraran a `fichajes` darían vuelta todo el día y romperían planilla, evaluador y
premios. Solo los equipos con esta marca alimentan la asistencia.
"""

import logging

logger = logging.getLogger(__name__)


def migrar_accesos(conn):
    """Crea las tablas del módulo y siembra el equipo ya configurado."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dispositivos (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre            TEXT NOT NULL,
            ubicacion         TEXT,

            -- Cómo se le habla
            protocolo         TEXT    NOT NULL DEFAULT 'pull',
            ip                TEXT,
            puerto            INTEGER NOT NULL DEFAULT 4370,
            password          INTEGER NOT NULL DEFAULT 0,
            timeout           INTEGER NOT NULL DEFAULT 10,
            transporte        TEXT,

            -- Qué es, según lo que informa el propio equipo
            numero_serie      TEXT,
            modelo            TEXT,
            firmware          TEXT,
            plataforma        TEXT,
            algoritmo_huella  TEXT,

            -- Para qué se usa
            cuenta_asistencia INTEGER NOT NULL DEFAULT 0,
            es_acceso         INTEGER NOT NULL DEFAULT 0,

            activo            INTEGER NOT NULL DEFAULT 1,
            orden             INTEGER NOT NULL DEFAULT 0,
            visto_en          TEXT,
            creado_en         TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            modificado_en     TEXT,

            CHECK (protocolo IN ('pull','push')),
            CHECK (transporte IS NULL OR transporte IN ('tcp','udp'))
        )
    """)
    # Un equipo pull se identifica por dónde está; uno push, por quién es. Los
    # índices son parciales para que las filas sin ese dato no choquen entre sí.
    conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS ix_dispositivos_ip
                    ON dispositivos (ip, puerto) WHERE ip IS NOT NULL""")
    conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS ix_dispositivos_serie
                    ON dispositivos (numero_serie) WHERE numero_serie IS NOT NULL""")

    _migrar_perfiles(conn)
    _sembrar_equipo_actual(conn)


def _migrar_perfiles(conn):
    """
    Perfiles de acceso: un nombre y el conjunto de puertas que abre.

    Es el modelo con el que el usuario ya piensa el problema —"todas las
    puertas", "todas menos oficina", "solo oficina", "solo cámaras"— y el mismo
    que usa Enterprise hoy. De los diez que tiene definidos usa tres.

    La pertenencia se guarda explícita, una fila por perfil y puerta, y no como
    una regla tipo "todas". Con una regla, una puerta nueva entraría sola en el
    perfil "todas" sin que nadie lo decida; con la lista explícita aparece como
    una casilla vacía en la matriz y se ve. El olvido silencioso es justamente
    el problema que este módulo viene a resolver.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS perfiles_acceso (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre      TEXT NOT NULL UNIQUE,
            descripcion TEXT,
            activo      INTEGER NOT NULL DEFAULT 1,
            orden       INTEGER NOT NULL DEFAULT 0,
            creado_en   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS perfiles_dispositivos (
            perfil_id      INTEGER NOT NULL REFERENCES perfiles_acceso(id) ON DELETE CASCADE,
            dispositivo_id INTEGER NOT NULL REFERENCES dispositivos(id)    ON DELETE CASCADE,
            PRIMARY KEY (perfil_id, dispositivo_id)
        )
    """)

    # El perfil del empleado. Puede quedar en NULL: alguien de administración
    # que no abre ninguna puerta es un caso válido, no un dato faltante.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(empleados)").fetchall()}
    if "perfil_acceso_id" not in cols:
        conn.execute(
            "ALTER TABLE empleados ADD COLUMN perfil_acceso_id INTEGER "
            "REFERENCES perfiles_acceso(id)"
        )
        logger.info("Migración: columna perfil_acceso_id agregada a empleados")

    # El cargo llego a proponer un perfil, como comodidad para no elegirlo de a
    # uno en cada alta. Se saco: se usaba un par de veces por mes y su valor
    # dependia de que el acceso se dedujera limpio del puesto, que no es el
    # caso. Una sugerencia equivocada es peor que ninguna, porque invita a
    # aceptarla sin pensar. Si la columna quedo de una version anterior, se
    # elimina para que nadie la lea creyendo que significa algo.
    cols_cargo = {r[1] for r in conn.execute("PRAGMA table_info(cargos)").fetchall()}
    if "perfil_acceso_id" in cols_cargo:
        conn.execute("ALTER TABLE cargos DROP COLUMN perfil_acceso_id")
        logger.info("Migración: columna perfil_acceso_id eliminada de cargos")

    # Excepciones por persona: sacarle o darle una puerta suelta sin tocar el
    # perfil. La alternativa sería crear un perfil nuevo por cada caso, que es
    # como se llega a tener diez perfiles y usar tres.
    #
    # `motivo` y `creado_por` no son adorno: una excepción sin explicación, dos
    # años después, nadie se anima a sacarla ni sabe por qué está.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS accesos_excepciones (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            empleado_id    INTEGER NOT NULL REFERENCES empleados(id),
            dispositivo_id INTEGER NOT NULL REFERENCES dispositivos(id),
            modo           TEXT    NOT NULL,
            motivo         TEXT,
            creado_por     INTEGER REFERENCES usuarios(id),
            creado_en      TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            UNIQUE (empleado_id, dispositivo_id),
            CHECK (modo IN ('agregar','quitar'))
        )
    """)


def _sembrar_equipo_actual(conn):
    """
    Pasa el lector ya configurado a la tabla, una sola vez.

    Las claves `device_*` de `configuracion` NO se borran, y es deliberado: si
    alguna vez hay que volver a una versión anterior del código, son el único
    lugar donde está la IP real de ese local. Cada instancia tiene la suya
    (Gardiner .1.201, Happening .0.201) y el default de `config.py` sirve para
    una sola de las dos. Mientras la tabla tenga filas, nadie las lee.
    """
    if conn.execute("SELECT 1 FROM dispositivos LIMIT 1").fetchone():
        return

    cfg = {
        r["clave"]: r["valor"]
        for r in conn.execute(
            "SELECT clave, valor FROM configuracion WHERE clave LIKE 'device_%'"
        )
    }

    def _entero(clave, defecto):
        try:
            return int(str(cfg.get(clave, defecto)).strip())
        except (TypeError, ValueError):
            return defecto

    conn.execute(
        """INSERT INTO dispositivos
               (nombre, protocolo, ip, puerto, password, timeout,
                cuenta_asistencia, es_acceso, activo, orden)
           VALUES (?, 'pull', ?, ?, ?, ?, 1, 0, 1, 0)""",
        (
            "Lector principal",
            (cfg.get("device_ip") or "").strip() or "192.168.1.201",
            _entero("device_port", 4370),
            _entero("device_password", 0),
            _entero("device_timeout", 10),
        ),
    )
    logger.info("Migración: dispositivos sembrado con el lector ya configurado")
