import sqlite3
import logging
from contextlib import contextmanager
from config import DB_PATH

logger = logging.getLogger(__name__)


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def db_session():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with db_session() as conn:
        conn.executescript("""

            -- ================================================================
            -- ROLES Y PERMISOS
            -- ================================================================
            CREATE TABLE IF NOT EXISTS roles (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre      TEXT UNIQUE NOT NULL,
                descripcion TEXT,
                nivel       INTEGER NOT NULL DEFAULT 1,
                activo      INTEGER NOT NULL DEFAULT 1,
                creado_en   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS permisos (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                rol_id  INTEGER NOT NULL,
                modulo  TEXT NOT NULL,
                accion  TEXT NOT NULL,
                UNIQUE (rol_id, modulo, accion),
                FOREIGN KEY (rol_id) REFERENCES roles(id) ON DELETE CASCADE
            );

            -- ================================================================
            -- USUARIOS DEL SISTEMA
            -- ================================================================
            CREATE TABLE IF NOT EXISTS usuarios (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre        TEXT NOT NULL,
                email         TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                rol_id        INTEGER,
                activo        INTEGER NOT NULL DEFAULT 1,
                creado_en     TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                FOREIGN KEY (rol_id) REFERENCES roles(id)
            );

            -- ================================================================
            -- EMPLEADOS — legajo completo
            -- ================================================================
            CREATE TABLE IF NOT EXISTS empleados (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id          TEXT UNIQUE,
                nombre           TEXT NOT NULL,
                apellido         TEXT NOT NULL,
                dni              TEXT,
                cuil             TEXT,
                fecha_nacimiento TEXT,
                fecha_ingreso    TEXT,
                fecha_egreso     TEXT,
                cargo            TEXT,
                departamento     TEXT,
                categoria        TEXT,
                telefono         TEXT,
                email            TEXT,
                domicilio        TEXT,
                activo           INTEGER NOT NULL DEFAULT 1,
                tipo             TEXT NOT NULL DEFAULT 'normal'
                                      CHECK(tipo IN ('normal','jerarquico','acceso')),
                observaciones    TEXT,
                creado_en        TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                modificado_en    TEXT
            );

            -- ================================================================
            -- CONFIGURACIÓN — parámetros del sistema editables desde la UI
            -- ================================================================
            CREATE TABLE IF NOT EXISTS configuracion (
                clave       TEXT PRIMARY KEY,
                valor       TEXT NOT NULL,
                descripcion TEXT
            );

            -- ================================================================
            -- TURNOS — configurables (ej: Mañana, Noche, Madrugada)
            -- ================================================================
            CREATE TABLE IF NOT EXISTS turnos (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre     TEXT NOT NULL UNIQUE,
                hora_desde TEXT NOT NULL DEFAULT '00:00'
            );

            -- ================================================================
            -- HORARIOS
            -- ================================================================
            CREATE TABLE IF NOT EXISTS horarios (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre    TEXT NOT NULL,
                tipo      TEXT NOT NULL CHECK(tipo IN ('simple','cortado')),
                turno_id  INTEGER REFERENCES turnos(id),
                activo    INTEGER NOT NULL DEFAULT 1,
                creado_en TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            );

            -- 1 bloque para simple, 2 para cortado
            CREATE TABLE IF NOT EXISTS horarios_bloques (
                id                         INTEGER PRIMARY KEY AUTOINCREMENT,
                horario_id                 INTEGER NOT NULL,
                bloque                     INTEGER NOT NULL CHECK(bloque IN (1,2)),
                hora_entrada               TEXT NOT NULL,
                hora_salida                TEXT NOT NULL,
                cruza_medianoche           INTEGER NOT NULL DEFAULT 0,
                tolerancia_entrada_antes   INTEGER NOT NULL DEFAULT 15,
                tolerancia_entrada_despues INTEGER NOT NULL DEFAULT 60,
                tolerancia_tarde           INTEGER NOT NULL DEFAULT 10,
                tolerancia_salida_antes    INTEGER NOT NULL DEFAULT 30,
                tolerancia_salida_despues  INTEGER NOT NULL DEFAULT 60,
                UNIQUE (horario_id, bloque),
                FOREIGN KEY (horario_id) REFERENCES horarios(id)
            );

            -- ================================================================
            -- CALENDARIOS — patrón de semana laboral
            -- ================================================================
            CREATE TABLE IF NOT EXISTS calendarios (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre            TEXT NOT NULL,
                cantidad_francos  INTEGER NOT NULL DEFAULT 1,
                trabaja_lunes     INTEGER NOT NULL DEFAULT 1,
                trabaja_martes    INTEGER NOT NULL DEFAULT 1,
                trabaja_miercoles INTEGER NOT NULL DEFAULT 1,
                trabaja_jueves    INTEGER NOT NULL DEFAULT 1,
                trabaja_viernes   INTEGER NOT NULL DEFAULT 1,
                trabaja_sabado    INTEGER NOT NULL DEFAULT 1,
                trabaja_domingo   INTEGER NOT NULL DEFAULT 1,
                franco_en_feriado INTEGER NOT NULL DEFAULT 0,
                activo            INTEGER NOT NULL DEFAULT 1,
                creado_en         TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            );

            -- ================================================================
            -- CALENDARIOS — detalle por día de semana
            -- ================================================================
            CREATE TABLE IF NOT EXISTS calendarios_dias (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                calendario_id INTEGER NOT NULL,
                dia_semana    INTEGER NOT NULL CHECK(dia_semana BETWEEN 0 AND 6),
                horario_id    INTEGER,
                es_franco     INTEGER NOT NULL DEFAULT 0,
                UNIQUE (calendario_id, dia_semana),
                FOREIGN KEY (calendario_id) REFERENCES calendarios(id),
                FOREIGN KEY (horario_id)    REFERENCES horarios(id)
            );

            -- ================================================================
            -- ASIGNACIONES — empleado → calendario base
            -- ================================================================
            CREATE TABLE IF NOT EXISTS asignaciones (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                empleado_id       INTEGER NOT NULL,
                horario_id        INTEGER,
                calendario_id     INTEGER NOT NULL,
                fecha_desde       TEXT NOT NULL,
                fecha_hasta       TEXT,
                franco_rotativo   INTEGER NOT NULL DEFAULT 0,
                franco_dia_semana INTEGER,
                FOREIGN KEY (empleado_id)   REFERENCES empleados(id),
                FOREIGN KEY (horario_id)    REFERENCES horarios(id),
                FOREIGN KEY (calendario_id) REFERENCES calendarios(id)
            );

            -- ================================================================
            -- PROGRAMACIÓN SEMANAL — define el franco rotativo
            -- NULL en un día = franco ese día
            -- ================================================================
            CREATE TABLE IF NOT EXISTS programacion_semanal (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                empleado_id          INTEGER NOT NULL,
                fecha_lunes          TEXT NOT NULL,
                lunes_horario_id     INTEGER,
                martes_horario_id    INTEGER,
                miercoles_horario_id INTEGER,
                jueves_horario_id    INTEGER,
                viernes_horario_id   INTEGER,
                sabado_horario_id    INTEGER,
                domingo_horario_id   INTEGER,
                UNIQUE (empleado_id, fecha_lunes),
                FOREIGN KEY (empleado_id) REFERENCES empleados(id)
            );

            -- ================================================================
            -- FERIADOS
            -- ================================================================
            CREATE TABLE IF NOT EXISTS feriados (
                id     INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha  TEXT UNIQUE NOT NULL,
                nombre TEXT NOT NULL,
                tipo   TEXT NOT NULL DEFAULT 'nacional'
                           CHECK(tipo IN ('nacional','provincial','local'))
            );

            -- ================================================================
            -- FICHAJES
            -- ================================================================
            CREATE TABLE IF NOT EXISTS fichajes (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                empleado_id    INTEGER,
                user_id        TEXT NOT NULL,
                timestamp      TEXT NOT NULL,
                tipo           TEXT CHECK(tipo IN ('entrada','salida')),
                bloque         INTEGER,
                punch_raw      INTEGER NOT NULL DEFAULT 0,
                status_raw     INTEGER NOT NULL DEFAULT 0,
                es_manual      INTEGER NOT NULL DEFAULT 0,
                motivo_manual  TEXT,
                cargado_por    INTEGER,
                creado_en      TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                UNIQUE (user_id, timestamp),
                FOREIGN KEY (empleado_id) REFERENCES empleados(id),
                FOREIGN KEY (cargado_por) REFERENCES usuarios(id)
            );

            -- ================================================================
            -- ALIVIADAS — ausencia autorizada en bloque de turno cortado
            -- ================================================================
            CREATE TABLE IF NOT EXISTS aliviadas (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                empleado_id    INTEGER NOT NULL,
                fecha          TEXT NOT NULL,
                bloque         TEXT NOT NULL CHECK(bloque IN ('1','2','ambos')),
                autorizado_por INTEGER NOT NULL,
                observaciones  TEXT,
                creado_en      TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                UNIQUE (empleado_id, fecha, bloque),
                FOREIGN KEY (empleado_id)    REFERENCES empleados(id),
                FOREIGN KEY (autorizado_por) REFERENCES usuarios(id)
            );

            -- ================================================================
            -- INCONSISTENCIAS
            -- ================================================================
            CREATE TABLE IF NOT EXISTS inconsistencias (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                empleado_id  INTEGER,
                user_id      TEXT,
                fecha        TEXT NOT NULL,
                tipo         TEXT NOT NULL,
                motivo       TEXT NOT NULL,
                resuelta     INTEGER NOT NULL DEFAULT 0,
                resuelta_por INTEGER,
                creado_en    TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                FOREIGN KEY (empleado_id)  REFERENCES empleados(id),
                FOREIGN KEY (resuelta_por) REFERENCES usuarios(id)
            );

            -- ================================================================
            -- PLANIFICACIÓN DIARIA
            -- ================================================================
            CREATE TABLE IF NOT EXISTS planificacion (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                empleado_id  INTEGER NOT NULL,
                fecha        TEXT NOT NULL,
                horario_id   INTEGER,
                es_franco    INTEGER NOT NULL DEFAULT 0,
                observacion  TEXT,
                creado_por   INTEGER,
                creado_en    TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                modificado_en TEXT,
                UNIQUE (empleado_id, fecha),
                FOREIGN KEY (empleado_id) REFERENCES empleados(id),
                FOREIGN KEY (horario_id)  REFERENCES horarios(id),
                FOREIGN KEY (creado_por)  REFERENCES usuarios(id)
            );

            CREATE INDEX IF NOT EXISTS idx_planificacion_fecha
                ON planificacion (fecha, empleado_id);

            -- ================================================================
            -- RESULTADOS DIARIOS — veredicto por empleado por día
            -- ================================================================
            CREATE TABLE IF NOT EXISTS resultados_dia (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                empleado_id           INTEGER NOT NULL,
                fecha                 TEXT NOT NULL,
                horario_id            INTEGER,
                es_franco             INTEGER NOT NULL DEFAULT 0,
                estado                TEXT NOT NULL DEFAULT 'pendiente',
                -- bloque 1
                b1_entrada            TEXT,
                b1_salida             TEXT,
                b1_minutos_tarde      INTEGER,
                b1_salida_anticipada  INTEGER NOT NULL DEFAULT 0,
                b1_ausente            INTEGER NOT NULL DEFAULT 0,
                -- bloque 2 (solo turnos cortados)
                b2_entrada            TEXT,
                b2_salida             TEXT,
                b2_minutos_tarde      INTEGER,
                b2_salida_anticipada  INTEGER NOT NULL DEFAULT 0,
                b2_ausente            INTEGER NOT NULL DEFAULT 0,
                procesado_en          TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                UNIQUE (empleado_id, fecha),
                FOREIGN KEY (empleado_id) REFERENCES empleados(id),
                FOREIGN KEY (horario_id)  REFERENCES horarios(id)
            );

            CREATE INDEX IF NOT EXISTS idx_resultados_fecha
                ON resultados_dia (fecha, empleado_id);

            -- ================================================================
            -- LOG DE SINCRONIZACIÓN
            -- ================================================================
            CREATE TABLE IF NOT EXISTS sync_log (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                iniciado_en      TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                finalizado_en    TEXT,
                registros_leidos INTEGER DEFAULT 0,
                registros_nuevos INTEGER DEFAULT 0,
                error            TEXT
            );

            -- ================================================================
            -- ÍNDICES
            -- ================================================================
            CREATE INDEX IF NOT EXISTS idx_fichajes_user_ts
                ON fichajes (user_id, timestamp);
            CREATE INDEX IF NOT EXISTS idx_fichajes_empleado_ts
                ON fichajes (empleado_id, timestamp);
            CREATE INDEX IF NOT EXISTS idx_asignaciones_empleado
                ON asignaciones (empleado_id, fecha_desde);
            CREATE INDEX IF NOT EXISTS idx_programacion_empleado
                ON programacion_semanal (empleado_id, fecha_lunes);
            CREATE INDEX IF NOT EXISTS idx_aliviadas_empleado
                ON aliviadas (empleado_id, fecha);
            CREATE INDEX IF NOT EXISTS idx_inconsistencias_empleado
                ON inconsistencias (empleado_id, fecha);

            -- ================================================================
            -- NOVEDADES — ILT, licencias, vacaciones, suspensiones, FT, FD
            -- ================================================================
            CREATE TABLE IF NOT EXISTS novedades (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                empleado_id INTEGER NOT NULL REFERENCES empleados(id),
                fecha       TEXT NOT NULL,
                bloque      INTEGER NOT NULL DEFAULT 0,
                tipo        TEXT NOT NULL,
                descripcion TEXT,
                creado_por  TEXT,
                creado_en   TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                UNIQUE(empleado_id, fecha, bloque)
            );

            CREATE INDEX IF NOT EXISTS idx_novedades_empleado
                ON novedades (empleado_id, fecha);

            -- ================================================================
            -- SALDO FRANCOS — carry-forward mensual (TRANSPORTE)
            -- ================================================================
            CREATE TABLE IF NOT EXISTS saldo_francos (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                empleado_id INTEGER NOT NULL REFERENCES empleados(id),
                mes         TEXT NOT NULL,
                saldo       REAL NOT NULL DEFAULT 0,
                UNIQUE(empleado_id, mes)
            );

            -- ================================================================
            -- ORDEN DE PLANILLA — posición y separadores por tab
            -- ================================================================
            CREATE TABLE IF NOT EXISTS planilla_orden (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                grupo       TEXT NOT NULL CHECK(grupo IN ('TM','TN','CO')),
                posicion    INTEGER NOT NULL,
                empleado_id INTEGER REFERENCES empleados(id),
                etiqueta    TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_planilla_orden
                ON planilla_orden (grupo, posicion);

            -- ================================================================
            -- CATÁLOGOS — cargos, departamentos y categorías
            -- ================================================================
            CREATE TABLE IF NOT EXISTS cargos (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre        TEXT NOT NULL UNIQUE,
                aplica_premio INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS departamentos (
                id     INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL UNIQUE
            );

            CREATE TABLE IF NOT EXISTS categorias (
                id     INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL UNIQUE
            );

            -- ================================================================
            -- PREMIOS — parámetros y evaluaciones mensuales
            -- ================================================================
            CREATE TABLE IF NOT EXISTS premios_parametros (
                clave       TEXT PRIMARY KEY,
                valor       TEXT NOT NULL,
                tipo        TEXT NOT NULL DEFAULT 'numero',
                descripcion TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS premios_evaluacion (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                empleado_id      INTEGER NOT NULL REFERENCES empleados(id),
                periodo          TEXT NOT NULL,
                minutos_retardo  INTEGER NOT NULL DEFAULT 0,
                no_fichadas      INTEGER NOT NULL DEFAULT 0,
                dias_vacacion    INTEGER NOT NULL DEFAULT 0,
                dias_enfermo     INTEGER NOT NULL DEFAULT 0,
                dias_suspension  INTEGER NOT NULL DEFAULT 0,
                dias_ausente     INTEGER NOT NULL DEFAULT 0,
                bpm              TEXT NOT NULL DEFAULT 'OK',
                desempenio       TEXT,
                monto_base       INTEGER NOT NULL DEFAULT 0,
                desglose_json    TEXT,
                valor_calculado  INTEGER,
                valor_final      INTEGER,
                generado_en      TEXT DEFAULT (datetime('now','localtime')),
                modificado_en    TEXT,
                UNIQUE(empleado_id, periodo)
            );

        """)
        _migrate(conn)
    logger.info("Base de datos inicializada: %s", DB_PATH)


def get_config(conn, clave: str, default=None):
    """Lee un valor de la tabla configuracion."""
    row = conn.execute("SELECT valor FROM configuracion WHERE clave=?", (clave,)).fetchone()
    return row["valor"] if row else default


def _sugerir_turno_id(conn, hora_entrada: str) -> int | None:
    """Devuelve el turno_id cuyo hora_desde más alto sea <= hora_entrada."""
    turnos = conn.execute(
        "SELECT id, hora_desde FROM turnos ORDER BY hora_desde DESC"
    ).fetchall()
    for t in turnos:
        if hora_entrada >= t["hora_desde"]:
            return t["id"]
    return None


def _migrate(conn):
    """Migraciones incrementales para bases de datos existentes."""

    # Tabla configuracion
    conn.execute("""
        CREATE TABLE IF NOT EXISTS configuracion (
            clave       TEXT PRIMARY KEY,
            valor       TEXT NOT NULL,
            descripcion TEXT
        )
    """)
    conn.executemany(
        "INSERT OR IGNORE INTO configuracion (clave, valor, descripcion) VALUES (?,?,?)",
        [
            ('umbral_ciclo_abierto_horas', '4',
             'Horas sin salida para considerar ciclo abierto en empleados sin planificación'),
            ('sync_interval_minutos', '5',
             'Intervalo de sincronización con el dispositivo ZKTeco (minutos)'),
        ]
    )

    # Tabla turnos (puede no existir en instalaciones anteriores al CREATE TABLE IF NOT EXISTS)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS turnos (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre     TEXT NOT NULL UNIQUE,
            hora_desde TEXT NOT NULL DEFAULT '00:00'
        )
    """)

    # Seed de turnos por defecto si la tabla está vacía
    if conn.execute("SELECT COUNT(*) FROM turnos").fetchone()[0] == 0:
        conn.executemany(
            "INSERT INTO turnos (nombre, hora_desde) VALUES (?, ?)",
            [("Mañana", "06:00"), ("Noche", "17:00"), ("Madrugada", "23:00")],
        )
        logger.info("Turnos por defecto creados: Mañana, Noche, Madrugada")

    # turno_id en horarios
    cols_h = {r[1] for r in conn.execute("PRAGMA table_info(horarios)").fetchall()}
    if "turno_id" not in cols_h:
        conn.execute("ALTER TABLE horarios ADD COLUMN turno_id INTEGER REFERENCES turnos(id)")
        logger.info("Migración: columna turno_id agregada a horarios")
        # Auto-asignar turno a horarios existentes según hora_entrada del primer bloque
        horarios = conn.execute("""
            SELECT h.id, b.hora_entrada
            FROM horarios h
            JOIN horarios_bloques b ON b.horario_id = h.id AND b.bloque = 1
        """).fetchall()
        asignados = 0
        for h in horarios:
            tid = _sugerir_turno_id(conn, h["hora_entrada"])
            if tid:
                conn.execute("UPDATE horarios SET turno_id=? WHERE id=?", (tid, h["id"]))
                asignados += 1
        logger.info("Auto-asignación de turnos: %d horarios actualizados", asignados)

    cols_cd = {r[1] for r in conn.execute("PRAGMA table_info(calendarios_dias)").fetchall()}
    if "es_franco" not in cols_cd:
        conn.execute("ALTER TABLE calendarios_dias ADD COLUMN es_franco INTEGER NOT NULL DEFAULT 0")
        conn.execute("UPDATE calendarios_dias SET es_franco=1 WHERE horario_id IS NULL")
        logger.info("Migración: columna es_franco agregada a calendarios_dias")

    cols_rd = {r[1] for r in conn.execute("PRAGMA table_info(resultados_dia)").fetchall()}
    nuevas_rd = {
        "b1_entrada_id":         "INTEGER REFERENCES fichajes(id)",
        "b1_salida_id":          "INTEGER REFERENCES fichajes(id)",
        "b2_entrada_id":         "INTEGER REFERENCES fichajes(id)",
        "b2_salida_id":          "INTEGER REFERENCES fichajes(id)",
        "b1_sin_salida":         "INTEGER NOT NULL DEFAULT 0",
        "b2_sin_salida":         "INTEGER NOT NULL DEFAULT 0",
        "corregido_manualmente": "INTEGER NOT NULL DEFAULT 0",
        "corregido_por":         "INTEGER REFERENCES usuarios(id)",
        "corregido_en":          "TEXT",
    }
    for col, tipo in nuevas_rd.items():
        if col not in cols_rd:
            conn.execute(f"ALTER TABLE resultados_dia ADD COLUMN {col} {tipo}")
            logger.info("Migración: columna %s agregada a resultados_dia", col)

    cols_p = {r[1] for r in conn.execute("PRAGMA table_info(planificacion)").fetchall()}
    if "auto_generado" not in cols_p:
        conn.execute("ALTER TABLE planificacion ADD COLUMN auto_generado INTEGER NOT NULL DEFAULT 0")
        # Todo lo existente vino del generador automático — tratar como auto_generado
        conn.execute("UPDATE planificacion SET auto_generado = 1")
        logger.info("Migración: columna auto_generado agregada a planificacion")

    # Tabla novedades — sin CHECK en tipo para poder incluir '@' (aliviada manual)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS novedades (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            empleado_id INTEGER NOT NULL REFERENCES empleados(id),
            fecha       TEXT NOT NULL,
            bloque      INTEGER NOT NULL DEFAULT 0,
            tipo        TEXT NOT NULL,
            descripcion TEXT,
            creado_por  TEXT,
            creado_en   TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            UNIQUE(empleado_id, fecha, bloque)
        )
    """)
    # Si la tabla existente tiene el CHECK constraint que bloquea '@', la recreamos
    try:
        conn.execute("INSERT INTO novedades (empleado_id, fecha, bloque, tipo) VALUES (0,'1900-01-01',99,'@')")
        conn.execute("DELETE FROM novedades WHERE bloque=99")
    except Exception:
        conn.executescript("""
            ALTER TABLE novedades RENAME TO novedades_old;
            CREATE TABLE novedades (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                empleado_id INTEGER NOT NULL REFERENCES empleados(id),
                fecha       TEXT NOT NULL,
                bloque      INTEGER NOT NULL DEFAULT 0,
                tipo        TEXT NOT NULL,
                descripcion TEXT,
                creado_por  TEXT,
                creado_en   TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                UNIQUE(empleado_id, fecha, bloque)
            );
            INSERT INTO novedades SELECT * FROM novedades_old;
            DROP TABLE novedades_old;
        """)
        logger.info("Migración: tabla novedades recreada sin CHECK constraint en tipo")

    # Tabla saldo_francos
    conn.execute("""
        CREATE TABLE IF NOT EXISTS saldo_francos (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            empleado_id INTEGER NOT NULL REFERENCES empleados(id),
            mes         TEXT NOT NULL,
            saldo       REAL NOT NULL DEFAULT 0,
            UNIQUE(empleado_id, mes)
        )
    """)

    # Tabla periodos_cerrados
    conn.execute("""
        CREATE TABLE IF NOT EXISTS periodos_cerrados (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            anio        INTEGER NOT NULL,
            mes         INTEGER NOT NULL,
            cerrado_en  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            cerrado_por INTEGER REFERENCES usuarios(id),
            UNIQUE (anio, mes)
        )
    """)

    # Tabla planilla_orden
    conn.execute("""
        CREATE TABLE IF NOT EXISTS planilla_orden (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            grupo       TEXT NOT NULL CHECK(grupo IN ('TM','TN','CO')),
            posicion    INTEGER NOT NULL,
            empleado_id INTEGER REFERENCES empleados(id),
            etiqueta    TEXT
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_planilla_orden
            ON planilla_orden (grupo, posicion)
    """)

    # Filas generadas antes de que es_franco existiera en calendarios_dias:
    # horario_id=NULL + es_franco=0 es estado inválido — era franco sin el flag correcto
    n = conn.execute(
        "UPDATE planificacion SET es_franco=1 WHERE horario_id IS NULL AND es_franco=0"
    ).rowcount
    if n:
        logger.info("Migración: %d filas de planificacion corregidas a es_franco=1", n)

    # Hacer nullable la columna `rol` en usuarios (era NOT NULL con CHECK hardcodeado).
    # rol_id es la fuente autorizada desde la implementación de roles dinámicos.
    rol_col = next(
        (c for c in conn.execute("PRAGMA table_info(usuarios)").fetchall() if c[1] == "rol"),
        None
    )
    if rol_col and rol_col[3]:  # notnull=1 → necesita migración
        conn.executescript("""
            CREATE TABLE usuarios_new (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre        TEXT NOT NULL,
                email         TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                rol           TEXT,
                activo        INTEGER NOT NULL DEFAULT 1,
                creado_en     TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                rol_id        INTEGER REFERENCES roles(id)
            );
            INSERT INTO usuarios_new SELECT id, nombre, email, password_hash, rol, activo, creado_en, rol_id FROM usuarios;
            DROP TABLE usuarios;
            ALTER TABLE usuarios_new RENAME TO usuarios;
        """)
        logger.info("Migración: columna rol en usuarios convertida a nullable")

    cols_emp = {r[1] for r in conn.execute("PRAGMA table_info(empleados)").fetchall()}
    if "tipo" not in cols_emp:
        conn.execute("ALTER TABLE empleados ADD COLUMN tipo TEXT NOT NULL DEFAULT 'normal'")
        logger.info("Migración: columna tipo agregada a empleados")

    cols_cal = {r[1] for r in conn.execute("PRAGMA table_info(calendarios)").fetchall()}
    if "franco_en_feriado" not in cols_cal:
        conn.execute("ALTER TABLE calendarios ADD COLUMN franco_en_feriado INTEGER NOT NULL DEFAULT 0")
        logger.info("Migración: columna franco_en_feriado agregada a calendarios")

    # Franco rotativo en asignaciones
    cols_asig = {r[1] for r in conn.execute("PRAGMA table_info(asignaciones)").fetchall()}
    if "franco_rotativo" not in cols_asig:
        conn.execute("ALTER TABLE asignaciones ADD COLUMN franco_rotativo INTEGER NOT NULL DEFAULT 0")
        logger.info("Migración: columna franco_rotativo agregada a asignaciones")
    if "franco_dia_semana" not in cols_asig:
        conn.execute("ALTER TABLE asignaciones ADD COLUMN franco_dia_semana INTEGER")
        logger.info("Migración: columna franco_dia_semana agregada a asignaciones")

    # Catálogos de cargos, departamentos y categorías
    conn.execute("CREATE TABLE IF NOT EXISTS cargos (id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL UNIQUE, aplica_premio INTEGER NOT NULL DEFAULT 0)")
    cols_cargos = {r[1] for r in conn.execute("PRAGMA table_info(cargos)").fetchall()}
    if "aplica_premio" not in cols_cargos:
        conn.execute("ALTER TABLE cargos ADD COLUMN aplica_premio INTEGER NOT NULL DEFAULT 0")
        logger.info("Migración: columna aplica_premio agregada a cargos")
    conn.execute("CREATE TABLE IF NOT EXISTS departamentos (id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL UNIQUE)")
    conn.execute("CREATE TABLE IF NOT EXISTS categorias (id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL UNIQUE)")
    if conn.execute("SELECT COUNT(*) FROM cargos").fetchone()[0] == 0:
        rows = conn.execute(
            "SELECT DISTINCT cargo FROM empleados WHERE cargo IS NOT NULL AND cargo != '' ORDER BY cargo"
        ).fetchall()
        if rows:
            conn.executemany("INSERT OR IGNORE INTO cargos (nombre) VALUES (?)", [(r[0],) for r in rows])
            logger.info("Migración: %d cargos importados desde empleados", len(rows))
    if conn.execute("SELECT COUNT(*) FROM categorias").fetchone()[0] == 0:
        rows = conn.execute(
            "SELECT DISTINCT categoria FROM empleados WHERE categoria IS NOT NULL AND categoria != '' ORDER BY categoria"
        ).fetchall()
        if rows:
            conn.executemany("INSERT OR IGNORE INTO categorias (nombre) VALUES (?)", [(r[0],) for r in rows])
            logger.info("Migración: %d categorías importadas desde empleados", len(rows))

    # Premios
    conn.execute("""CREATE TABLE IF NOT EXISTS premios_parametros (
        clave TEXT PRIMARY KEY, valor TEXT NOT NULL,
        tipo TEXT NOT NULL DEFAULT 'numero', descripcion TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS premios_evaluacion (
        id INTEGER PRIMARY KEY AUTOINCREMENT, empleado_id INTEGER NOT NULL,
        periodo TEXT NOT NULL, minutos_retardo INTEGER NOT NULL DEFAULT 0,
        no_fichadas INTEGER NOT NULL DEFAULT 0, dias_vacacion INTEGER NOT NULL DEFAULT 0,
        dias_enfermo INTEGER NOT NULL DEFAULT 0, dias_suspension INTEGER NOT NULL DEFAULT 0,
        dias_ausente INTEGER NOT NULL DEFAULT 0, bpm TEXT NOT NULL DEFAULT 'OK',
        desempenio TEXT, monto_base INTEGER NOT NULL DEFAULT 0,
        desglose_json TEXT, valor_calculado INTEGER, valor_final INTEGER,
        generado_en TEXT DEFAULT (datetime('now','localtime')), modificado_en TEXT,
        UNIQUE(empleado_id, periodo))""")
    if conn.execute("SELECT COUNT(*) FROM premios_parametros").fetchone()[0] == 0:
        defaults = [
            ("monto_base",                   "88000", "entero",     "Monto base del premio ($)"),
            ("umbral_puntualidad_ok_min",     "60",    "entero",     "Retardo máximo considerado OK (minutos)"),
            ("umbral_puntualidad_total_min",  "120",   "entero",     "Retardo a partir del cual pierde la totalidad (minutos)"),
            ("pct_descuento_puntualidad",     "33.33", "porcentaje", "% del monto base a descontar por puntualidad parcial"),
            ("min_por_no_fichada",            "15",    "entero",     "Minutos a sumar al retardo por cada no fichada"),
            ("limite_no_fichadas",            "3",     "entero",     "No fichadas a partir de las cuales pierde la totalidad"),
            ("limite_enfermedad_dias",        "5",     "entero",     "Días de enfermedad a partir de los cuales pierde la totalidad"),
            ("pct_descuento_bpm",             "33.33", "porcentaje", "% del monto base a descontar por BPM no conforme"),
            ("dias_base_vacaciones",          "30",    "entero",     "Base de días para prorrateo de vacaciones"),
            ("redondeo",                      "1000",  "entero",     "Redondear el resultado final al múltiplo de este valor (0 = sin redondeo)"),
            ("dias_minimos_antiguedad",        "90",    "entero",     "Días de antigüedad mínima al 1° del mes para aplicar al premio"),
        ]
        conn.executemany(
            "INSERT OR IGNORE INTO premios_parametros (clave, valor, tipo, descripcion) VALUES (?,?,?,?)",
            defaults
        )
        logger.info("Migración: parámetros de premios inicializados con valores por defecto")
    conn.execute(
        "INSERT OR IGNORE INTO premios_parametros (clave, valor, tipo, descripcion) VALUES (?,?,?,?)",
        ("dias_minimos_antiguedad", "90", "entero",
         "Días de antigüedad mínima al 1° del mes para aplicar al premio")
    )

    # Saldo inicial de vacaciones (carga única al arrancar el sistema)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS vacaciones_saldo_inicial (
            empleado_id        INTEGER NOT NULL REFERENCES empleados(id),
            anio               INTEGER NOT NULL,
            dias_correspondian REAL    NOT NULL DEFAULT 0,
            dias_tomados       REAL    NOT NULL DEFAULT 0,
            PRIMARY KEY (empleado_id, anio)
        )
    """)

    # Campos adicionales del legajo de personal
    cols_emp = {r[1] for r in conn.execute("PRAGMA table_info(empleados)").fetchall()}
    nuevas_emp = {
        "nacionalidad":                   "TEXT",
        "estado_civil":                   "TEXT",
        "turno":                          "TEXT",
        "sector":                         "TEXT",
        "contacto_emergencia_nombre":     "TEXT",
        "contacto_emergencia_telefono":   "TEXT",
        "contacto_emergencia_parentesco": "TEXT",
        "nivel_estudio":                  "TEXT",
        "dom_calle":                      "TEXT",
        "dom_numero":                     "TEXT",
        "dom_piso":                       "TEXT",
        "dom_entre_calles":               "TEXT",
        "dom_barrio":                     "TEXT",
        "dom_localidad":                  "TEXT",
        "dom_provincia":                  "TEXT",
        "dom_cp":                         "TEXT",
        "dom_entre_calle1":               "TEXT",
        "dom_entre_calle2":               "TEXT",
        "dom_lat":                        "REAL",
        "dom_lng":                        "REAL",
        "dom_mapa":                       "TEXT",
    }
    # Migrar dom_entre_calles existente a los nuevos campos separados
    cols_after = {r[1] for r in conn.execute("PRAGMA table_info(empleados)").fetchall()}
    if "dom_entre_calle1" in cols_after and "dom_entre_calles" in cols_after:
        filas = conn.execute(
            "SELECT id, dom_entre_calles FROM empleados WHERE dom_entre_calles IS NOT NULL AND dom_entre_calle1 IS NULL"
        ).fetchall()
        for fila in filas:
            raw = fila["dom_entre_calles"].strip()
            partes = None
            for sep in [" y ", " Y ", " / ", "/"]:
                if sep in raw:
                    partes = [p.strip() for p in raw.split(sep, 1)]
                    break
            if partes and len(partes) == 2:
                conn.execute(
                    "UPDATE empleados SET dom_entre_calle1=?, dom_entre_calle2=? WHERE id=?",
                    (partes[0], partes[1], fila["id"])
                )
            else:
                conn.execute(
                    "UPDATE empleados SET dom_entre_calle1=? WHERE id=?",
                    (raw, fila["id"])
                )
        if filas:
            logger.info("Migración: %d filas migradas de dom_entre_calles a dom_entre_calle1/2", len(filas))
    for col, tipo in nuevas_emp.items():
        if col not in cols_emp:
            conn.execute(f"ALTER TABLE empleados ADD COLUMN {col} {tipo}")
            logger.info("Migración: columna %s agregada a empleados", col)

    # Catálogos configurables del legajo (turno y sector)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS turnos_legajo (
            id     INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT UNIQUE NOT NULL,
            orden  INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sectores_legajo (
            id     INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT UNIQUE NOT NULL,
            orden  INTEGER NOT NULL DEFAULT 0
        )
    """)
    for t in [("TM", 1), ("TN", 2), ("CORTADO", 3)]:
        conn.execute("INSERT OR IGNORE INTO turnos_legajo (nombre, orden) VALUES (?, ?)", t)
    for s in [("SALÓN", 1), ("COCINA", 2)]:
        conn.execute("INSERT OR IGNORE INTO sectores_legajo (nombre, orden) VALUES (?, ?)", s)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS niveles_estudio (
            id     INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT UNIQUE NOT NULL,
            orden  INTEGER NOT NULL DEFAULT 0
        )
    """)
    for n in [("Primario", 1), ("Secundario", 2), ("Terciario", 3), ("Universitario", 4)]:
        conn.execute("INSERT OR IGNORE INTO niveles_estudio (nombre, orden) VALUES (?, ?)", n)
