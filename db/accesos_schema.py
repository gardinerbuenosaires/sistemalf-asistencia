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

    # Lo que el sistema le escribió a un equipo, y por qué.
    #
    # Existe porque auditar sin registro no es auditar. El caso que lo pide es
    # el que se sale de lo previsto: alguien quedó cargado en una puerta por un
    # error que nadie anticipó, un operador lo saca a mano, y seis meses después
    # hay que poder contestar quién lo sacó, cuándo y con qué motivo.
    #
    # `motivo` no es opcional para las acciones fuera de la política. Una
    # excepción sin explicación, dos años después, nadie se anima a tocarla ni
    # sabe por qué está; una baja manual sin explicación es peor, porque ni
    # siquiera queda la persona para preguntarle.
    #
    # `resultado` guarda lo que se verificó releyendo el equipo, no lo que el
    # equipo contestó: un lector puede aceptar el comando y no hacer nada.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS accesos_operaciones (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            dispositivo_id INTEGER REFERENCES dispositivos(id),
            equipo         TEXT,
            user_id        TEXT,
            empleado_id    INTEGER REFERENCES empleados(id),
            nombre_equipo  TEXT,
            accion         TEXT NOT NULL,
            motivo         TEXT,
            resultado      TEXT NOT NULL,
            detalle        TEXT,
            respaldo       TEXT,
            usuario_id     INTEGER REFERENCES usuarios(id),
            creado_en      TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_accesos_op_fecha
                      ON accesos_operaciones (creado_en DESC)""")
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_accesos_op_user
                      ON accesos_operaciones (user_id)""")

    # Las huellas, copiadas del equipo de fichaje.
    #
    # Hoy el único lugar donde están completas es ese equipo. Enterprise tiene
    # la otra copia, y todo esto existe para apagar Enterprise: el día que se
    # apague, un equipo quemado significa que cada persona vuelve a enrolarse
    # con el dedo, de a una.
    #
    # Van en la base y no en una carpeta al lado porque `backup.ps1` copia
    # únicamente `fichajes.db`. Un respaldo que no incluye justo lo que se
    # quiere proteger es peor que no tenerlo: se descubre el día que no sirve.
    # El costo es chico —unos cientos de KB sobre 16 MB— y la contra, que la
    # base se copia a la máquina de pruebas, se resuelve del otro lado: el
    # script que copia también vacía esta tabla.
    #
    # Por `empleado_id` y no por número de dispositivo. El número se libera y se
    # reasigna; una huella pegada a un número reasignado es exactamente el
    # accidente que el módulo entero trata de impedir.
    #
    # `algoritmo` viaja con cada huella porque v10 y v12 no son intercambiables
    # ni convertibles: una huella guardada solo sirve para un equipo del mismo
    # algoritmo. Sin ese dato, un respaldo puede no entrar en el equipo nuevo y
    # nadie se entera hasta que lo necesita.
    #
    # La plantilla va en bytes crudos. Los respaldos en JSON la guardan en
    # hexadecimal, que ocupa el doble sin agregar nada.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS huellas (
            empleado_id  INTEGER NOT NULL REFERENCES empleados(id) ON DELETE CASCADE,
            dedo         INTEGER NOT NULL,
            algoritmo    TEXT,
            plantilla    BLOB NOT NULL,
            tamano       INTEGER NOT NULL,
            -- De qué equipo salió, solo como dato. Si ese equipo se borra, la
            -- huella se queda sin procedencia pero se queda: lo que importa
            -- acá es la plantilla, y perderla por un dato accesorio sería
            -- exactamente al revés.
            equipo_id    INTEGER REFERENCES dispositivos(id) ON DELETE SET NULL,
            leida_en     TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            PRIMARY KEY (empleado_id, dedo)
        )
    """)

    # Quién puede administrar el lector desde el lector: dar de alta gente y
    # tomarle la huella parado frente al equipo. Es UNA sola propiedad de la
    # persona y no una por equipo, aunque el campo exista en todos: las puertas
    # no tienen pantalla, así que ahí no hay nada que administrar. El valor vale
    # para el equipo de asistencia, que es el único con menú.
    #
    # Arranca en 0 para todos, a propósito. Que la mayoría no administre nada es
    # el estado correcto, y un valor que se hereda sin que nadie lo decida es la
    # forma en que se acumula gente con permisos que ya no le corresponden.
    if "nivel_lector" not in cols:
        conn.execute(
            "ALTER TABLE empleados ADD COLUMN nivel_lector INTEGER NOT NULL DEFAULT 0")
        logger.info("Migración: columna nivel_lector agregada a empleados")

    # El nombre corto que el lector muestra en pantalla al apoyar el dedo. Lo
    # elige una persona para que sea reconocible ahí, que no es lo mismo que el
    # nombre del legajo: el equipo de asistencia lo corta a 24 caracteres y las
    # puertas a 8, así que "Gómez Castro, Ana María" no sirve de nada.
    #
    # Vacío significa "no opinamos": al escribir se conserva el nombre que el
    # equipo ya tenga. Eso evita tener que adoptar los nombres de todos antes de
    # poder escribir, y evita que una columna vacía le borre el nombre a nadie.
    if "nombre_lector" not in cols:
        conn.execute("ALTER TABLE empleados ADD COLUMN nombre_lector TEXT")
        logger.info("Migración: columna nombre_lector agregada a empleados")

    # De quién era un número antes de liberarlo.
    #
    # Liberar el ID pone `user_id` en NULL para que el lector pueda volver a
    # usarlo. El legajo queda, pero el vínculo con ese número se corta, y la
    # huella sigue cargada en las puertas hasta que alguien la saque. A partir
    # de ahí el 42 de una puerta es un misterio: no se sabe si es basura de hace
    # años o alguien que se libero el mes pasado.
    #
    # Guardarlo cuesta una columna y es la diferencia entre «desconocido 42» y
    # «el 42 era de GONZALEZ, liberado el 18-09». Sin esto, cada liberación suma
    # un misterio más a la próxima auditoría.
    for columna in ("user_id_anterior", "user_id_liberado_en"):
        if columna not in cols:
            conn.execute(f"ALTER TABLE empleados ADD COLUMN {columna} TEXT")
            logger.info("Migración: columna %s agregada a empleados", columna)

    # El reloj de cada equipo. A las puertas nunca se les puso la hora —el
    # sistema se la sincroniza solo al de asistencia— y un lector de quince años
    # puede estar corrido meses. Guardar el desfase MEDIDO, y no solo cuándo se
    # corrigió, es lo que convierte esto en un diagnóstico: un equipo que se
    # atrasa cinco minutos por semana tiene la pila del reloj agotándose, y eso
    # no se ve corrigiéndolo en silencio todas las noches.
    cols_disp = {r[1] for r in conn.execute("PRAGMA table_info(dispositivos)").fetchall()}
    for columna, tipo in (("reloj_desfase_min", "INTEGER"),
                          ("reloj_visto_en", "TEXT"),
                          ("reloj_puesto_en", "TEXT")):
        if columna not in cols_disp:
            conn.execute(f"ALTER TABLE dispositivos ADD COLUMN {columna} {tipo}")
            logger.info("Migración: columna %s agregada a dispositivos", columna)

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
