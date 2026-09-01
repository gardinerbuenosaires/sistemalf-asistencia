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
                cargo_id         INTEGER,
                departamento_id  INTEGER,
                categoria_id     INTEGER,
                telefono         TEXT,
                email            TEXT,
                domicilio        TEXT,
                activo           INTEGER NOT NULL DEFAULT 1,
                tipo             TEXT NOT NULL DEFAULT 'normal'
                                      CHECK(tipo IN ('normal','jerarquico','acceso','parking')),
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
            -- OVERRIDE DE GRUPO — pisa la clasificación por frecuencia en un mes
            -- puntual (meses de transición de turno). Se vence solo: al mes
            -- siguiente la frecuencia ya refleja el turno nuevo.
            -- ================================================================
            CREATE TABLE IF NOT EXISTS planilla_grupo_override (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                empleado_id INTEGER NOT NULL REFERENCES empleados(id),
                mes         TEXT NOT NULL,                       -- 'YYYY-MM'
                grupo       TEXT NOT NULL CHECK(grupo IN ('TM','TN','CO')),
                creado_por  INTEGER REFERENCES usuarios(id),
                creado_en   TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                UNIQUE (empleado_id, mes)
            );
            CREATE INDEX IF NOT EXISTS idx_planilla_grupo_override_mes
                ON planilla_grupo_override (mes);

            -- ================================================================
            -- CATÁLOGOS — cargos, departamentos y categorías
            -- ================================================================
            CREATE TABLE IF NOT EXISTS cargos (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre        TEXT NOT NULL UNIQUE,
                aplica_premio INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS departamentos (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre                TEXT    NOT NULL UNIQUE,
                usa_distribucion      INTEGER NOT NULL DEFAULT 0,
                usa_mozos             INTEGER NOT NULL DEFAULT 0,
                usa_barmans           INTEGER NOT NULL DEFAULT 0,
                escribe_planificacion INTEGER NOT NULL DEFAULT 0,
                horario_tm_id         INTEGER REFERENCES horarios(id),
                horario_tn_id         INTEGER REFERENCES horarios(id),
                horario_cortado_id    INTEGER REFERENCES horarios(id),
                horario_doble_id      INTEGER REFERENCES horarios(id)
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
            ('device_ip',       '192.168.1.201', 'Dirección IP del lector de huellas ZKTeco'),
            ('device_port',     '4370',          'Puerto TCP del lector de huellas ZKTeco'),
            ('device_password', '0',             'Contraseña del lector de huellas ZKTeco (0 = sin contraseña)'),
            ('device_timeout',  '10',            'Tiempo de espera de conexión al dispositivo (segundos)'),
            ('nombre_empresa',  '',              'Nombre del restaurante o empresa (aparece en login y reportes'),
            ('inactividad_minutos', '10',        'Minutos sin actividad antes de cerrar la sesión automáticamente'),
            ('dia_cierre_periodo', '6',          'Día del mes en que se cierra automáticamente el período anterior'),
            ('dia_cierre_premios', '10',         'Día del mes en que se cierra automáticamente el período de premios del mes anterior'),
            ('distribucion_reemplaza_planificacion', '0',
             'Si está en 1, los empleados con cargo vinculado a un departamento no generan planificación automática por calendarios'),
            ('limpiar_dispositivo_auto', '0',
             'Si está en 1, borra automáticamente los registros del dispositivo los días 1 y 15 de cada mes (previa sincronización)'),
            ('trapos_cocina_activo', '0',
             'Si está en 1, aplica descuento de trapos de cocina en el cálculo de premios (específico por restaurante)'),
            ('trapos_cocina_valor', '0',
             'Valor mensual de descuento por trapos de cocina ($). Solo se usa cuando trapos_cocina_activo=1'),
            ('vp_activo', '0',
             'Si está en 1, habilita la columna VP (vacaciones pagadas) en la planilla mensual (específico por restaurante)'),
            ('parking_corte_turno', '16:00',
             'Hora de corte entre turno mañana y turno noche para empleados tipo parking (formato HH:MM)'),
            ('parking_corte_madrugada', '06:00',
             'Fichajes antes de esta hora se asignan al TN del día anterior (para turnos noche que cruzan medianoche)'),
        ]
    )

    conn.execute("""CREATE TABLE IF NOT EXISTS vacaciones_pagadas (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        empleado_id  INTEGER NOT NULL,
        periodo      TEXT NOT NULL,
        dias         REAL NOT NULL DEFAULT 0,
        creado_en    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')),
        UNIQUE(empleado_id, periodo)
    )""")

    conn.execute("""CREATE TABLE IF NOT EXISTS distribucion_aviso_config (
        turno       TEXT PRIMARY KEY,
        dia_semana  INTEGER NOT NULL DEFAULT 4,
        hora        TEXT NOT NULL DEFAULT '18:00',
        activo      INTEGER NOT NULL DEFAULT 1
    )""")
    for turno in ('TM', 'TN', 'CO'):
        conn.execute(
            "INSERT OR IGNORE INTO distribucion_aviso_config (turno) VALUES (?)", (turno,)
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

    # grupo_premios y turno_id en horarios
    cols_h = {r[1] for r in conn.execute("PRAGMA table_info(horarios)").fetchall()}
    if "grupo_premios" not in cols_h:
        conn.execute("ALTER TABLE horarios ADD COLUMN grupo_premios TEXT CHECK(grupo_premios IN ('TM','TN','CO'))")
        logger.info("Migración: columna grupo_premios agregada a horarios")
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
        "b1_mt":                 "INTEGER NOT NULL DEFAULT 0",
        "b2_mt":                 "INTEGER NOT NULL DEFAULT 0",
    }
    for col, tipo in nuevas_rd.items():
        if col not in cols_rd:
            conn.execute(f"ALTER TABLE resultados_dia ADD COLUMN {col} {tipo}")
            logger.info("Migración: columna %s agregada a resultados_dia", col)

    cols_hb = {r[1] for r in conn.execute("PRAGMA table_info(horarios_bloques)").fetchall()}
    if "aplica" not in cols_hb:
        conn.execute("ALTER TABLE horarios_bloques ADD COLUMN aplica INTEGER NOT NULL DEFAULT 1")
        logger.info("Migración: columna aplica agregada a horarios_bloques")

    cols_p = {r[1] for r in conn.execute("PRAGMA table_info(planificacion)").fetchall()}
    if "auto_generado" not in cols_p:
        conn.execute("ALTER TABLE planificacion ADD COLUMN auto_generado INTEGER NOT NULL DEFAULT 0")
        # Todo lo existente vino del generador automático — tratar como auto_generado
        conn.execute("UPDATE planificacion SET auto_generado = 1")
        logger.info("Migración: columna auto_generado agregada a planificacion")
    if "modificado_por" not in cols_p:
        conn.execute("ALTER TABLE planificacion ADD COLUMN modificado_por INTEGER REFERENCES usuarios(id)")
        logger.info("Migración: columna modificado_por agregada a planificacion")

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
    # Primero limpiar residuo de migración previa fallida
    old_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='novedades_old'"
    ).fetchone()
    if old_exists:
        conn.execute("DROP TABLE novedades_old")
    # Recrear SOLO si la tabla realmente tiene un CHECK en 'tipo' (detección por esquema,
    # no por un insert de prueba que fallaba por la FK y disparaba la recreación en cada arranque).
    sql_nov = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='novedades'"
    ).fetchone()
    if sql_nov and "CHECK" in sql_nov[0].upper():
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
            INSERT INTO novedades (id, empleado_id, fecha, bloque, tipo, descripcion, creado_por, creado_en)
                SELECT id, empleado_id, fecha, bloque, tipo, descripcion, creado_por, creado_en FROM novedades_old;
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

    # Determinantes de premios (tolerar_nf, tolerar_e, tolerar_retardo, tolerar_ausente, anular_premio)
    cols_pe = {r[1] for r in conn.execute("PRAGMA table_info(premios_evaluacion)").fetchall()}
    for col in ("tolerar_nf", "tolerar_e", "tolerar_retardo", "tolerar_ausente",
                "anular_premio", "dias_lsg", "tolerar_lsg", "desempenio_penaliza"):
        if col not in cols_pe:
            conn.execute(f"ALTER TABLE premios_evaluacion ADD COLUMN {col} INTEGER NOT NULL DEFAULT 0")
            logger.info("Migración: columna %s agregada a premios_evaluacion", col)
    if "monto_base_manual" not in cols_pe:
        conn.execute("ALTER TABLE premios_evaluacion ADD COLUMN monto_base_manual INTEGER NOT NULL DEFAULT 0")
        logger.info("Migración: columna monto_base_manual agregada a premios_evaluacion")
    if "dias_tarde" not in cols_pe:
        conn.execute("ALTER TABLE premios_evaluacion ADD COLUMN dias_tarde INTEGER NOT NULL DEFAULT 0")
        logger.info("Migración: columna dias_tarde agregada a premios_evaluacion")

    # CP con reemplazante: quién cubre al ausente (compensación de presencia verificable)
    cols_nov = {r[1] for r in conn.execute("PRAGMA table_info(novedades)").fetchall()}
    if "reemplazante_id" not in cols_nov:
        conn.execute("ALTER TABLE novedades ADD COLUMN reemplazante_id INTEGER REFERENCES empleados(id)")
        logger.info("Migración: columna reemplazante_id agregada a novedades")

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

    # Tabla premios_periodos_cerrados
    conn.execute("""
        CREATE TABLE IF NOT EXISTS premios_periodos_cerrados (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            anio        INTEGER NOT NULL,
            mes         INTEGER NOT NULL,
            cerrado_en  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            cerrado_por INTEGER REFERENCES usuarios(id),
            UNIQUE (anio, mes)
        )
    """)

    # Períodos de premios reabiertos: se muestran los valores del cierre y NO se
    # recalculan solos. Se marca al reabrir y se limpia al volver a cerrar.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS premios_periodos_reabiertos (
            anio         INTEGER NOT NULL,
            mes          INTEGER NOT NULL,
            reabierto_en TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            reabierto_por INTEGER REFERENCES usuarios(id),
            PRIMARY KEY (anio, mes)
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

    # Override de grupo por empleado y mes (meses de transición de turno)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS planilla_grupo_override (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            empleado_id INTEGER NOT NULL REFERENCES empleados(id),
            mes         TEXT NOT NULL,
            grupo       TEXT NOT NULL CHECK(grupo IN ('TM','TN','CO')),
            creado_por  INTEGER REFERENCES usuarios(id),
            creado_en   TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            UNIQUE (empleado_id, mes)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_planilla_grupo_override_mes
            ON planilla_grupo_override (mes)
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

    # Eliminar columna rol legacy (rol_id es la fuente autorizada)
    cols_usr = {c[1] for c in conn.execute("PRAGMA table_info(usuarios)").fetchall()}
    if "rol" in cols_usr:
        try:
            conn.execute("ALTER TABLE usuarios DROP COLUMN rol")
            logger.info("Migración: columna usuarios.rol eliminada")
        except Exception:
            pass

    cols_emp = {r[1] for r in conn.execute("PRAGMA table_info(empleados)").fetchall()}
    if "tipo" not in cols_emp:
        conn.execute("ALTER TABLE empleados ADD COLUMN tipo TEXT NOT NULL DEFAULT 'normal'")
        logger.info("Migración: columna tipo agregada a empleados")
    # Actualizar CHECK de tipo para incluir 'parking' si todavía tiene la lista vieja
    emp_schema = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='empleados'").fetchone()
    if emp_schema and "CHECK(tipo IN ('normal','jerarquico','acceso'))" in (emp_schema["sql"] or ""):
        conn.execute("PRAGMA writable_schema=ON")
        conn.execute("""UPDATE sqlite_master SET sql=replace(sql,
            "CHECK(tipo IN ('normal','jerarquico','acceso'))",
            "CHECK(tipo IN ('normal','jerarquico','acceso','parking'))")
            WHERE type='table' AND name='empleados'""")
        conn.execute("PRAGMA writable_schema=OFF")
        logger.info("Migración: tipo 'parking' agregado al CHECK de empleados")
    if "en_dispositivo" not in cols_emp:
        conn.execute("ALTER TABLE empleados ADD COLUMN en_dispositivo INTEGER NOT NULL DEFAULT 1")
        logger.info("Migración: columna en_dispositivo agregada a empleados")

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
    if "departamento_id" not in cols_cargos:
        conn.execute("ALTER TABLE cargos ADD COLUMN departamento_id INTEGER REFERENCES departamentos(id)")
        logger.info("Migración: columna departamento_id agregada a cargos")
    if "aplica_trapos" not in cols_cargos:
        conn.execute("ALTER TABLE cargos ADD COLUMN aplica_trapos INTEGER NOT NULL DEFAULT 0")
        logger.info("Migración: columna aplica_trapos agregada a cargos")
    cols_emp_check = {r[1] for r in conn.execute("PRAGMA table_info(empleados)").fetchall()}
    if "departamento_id" in cols_emp_check:
        conn.execute("ALTER TABLE empleados DROP COLUMN departamento_id")
        logger.info("Migración: columna departamento_id eliminada de empleados (se deriva de cargos)")
    conn.execute("CREATE TABLE IF NOT EXISTS departamentos (id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL UNIQUE)")
    conn.execute("CREATE TABLE IF NOT EXISTS categorias (id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL UNIQUE)")
    cols_emp_now = {r[1] for r in conn.execute("PRAGMA table_info(empleados)").fetchall()}
    if conn.execute("SELECT COUNT(*) FROM cargos").fetchone()[0] == 0 and "cargo" in cols_emp_now:
        rows = conn.execute(
            "SELECT DISTINCT cargo FROM empleados WHERE cargo IS NOT NULL AND cargo != '' ORDER BY cargo"
        ).fetchall()
        if rows:
            conn.executemany("INSERT OR IGNORE INTO cargos (nombre) VALUES (?)", [(r[0],) for r in rows])
            logger.info("Migración: %d cargos importados desde empleados", len(rows))
    if conn.execute("SELECT COUNT(*) FROM categorias").fetchone()[0] == 0 and "categoria" in cols_emp_now:
        rows = conn.execute(
            "SELECT DISTINCT categoria FROM empleados WHERE categoria IS NOT NULL AND categoria != '' ORDER BY categoria"
        ).fetchall()
        if rows:
            conn.executemany("INSERT OR IGNORE INTO categorias (nombre) VALUES (?)", [(r[0],) for r in rows])
            logger.info("Migración: %d categorías importadas desde empleados", len(rows))

    # Migrar columnas de texto a IDs si aún existen (upgrade desde versión anterior)
    _txt_to_id = [
        ("cargo",         "cargo_id",         "cargos"),
        ("departamento",  "departamento_id",  "departamentos"),
        ("categoria",     "categoria_id",     "categorias"),
        ("turno",         "turno_id",         "turnos_legajo"),
        ("sector",        "sector_id",        "sectores_legajo"),
        ("nivel_estudio", "nivel_estudio_id", "niveles_estudio"),
    ]
    cols_emp_now2 = {r[1] for r in conn.execute("PRAGMA table_info(empleados)").fetchall()}
    for txt_col, id_col, tabla in _txt_to_id:
        if txt_col in cols_emp_now2:
            conn.execute(f"""
                UPDATE empleados SET {id_col} = (
                    SELECT id FROM {tabla} WHERE nombre = empleados.{txt_col}
                ) WHERE {txt_col} IS NOT NULL AND {txt_col} != '' AND {id_col} IS NULL
            """)
            try:
                conn.execute(f"ALTER TABLE empleados DROP COLUMN {txt_col}")
                logger.info("Migración: columna %s migrada a %s en empleados", txt_col, id_col)
            except Exception:
                pass

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
    cols_pe = {r[1] for r in conn.execute("PRAGMA table_info(premios_evaluacion)").fetchall()}
    if "deduccion_trapos" not in cols_pe:
        conn.execute("ALTER TABLE premios_evaluacion ADD COLUMN deduccion_trapos INTEGER NOT NULL DEFAULT 0")
        logger.info("Migración: columna deduccion_trapos agregada a premios_evaluacion")
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
    conn.executemany(
        "INSERT OR IGNORE INTO premios_parametros (clave, valor, tipo, descripcion) VALUES (?,?,?,?)",
        [
            ("dias_minimos_antiguedad", "90", "entero",
             "Días de antigüedad mínima al 1° del mes para aplicar al premio"),
            ("dia_corte_mes_anterior", "10", "entero",
             "Hasta qué día del mes se muestra el mes anterior por defecto en premios (1-28)"),
            ("usa_bpm", "1", "bool",
             "Usar columna BPM. Desactivar para ocultarla y no exigirla (locales que no usan BPM)"),
            ("lsg_proporcional", "0", "bool",
             "L/LSG descuenta proporcional (como vacaciones) en vez de anular el premio"),
            ("pct_descuento_desempenio", "33.33", "porcentaje",
             "Descuento sobre el monto base al marcar el checkbox de desempeño (un tercio por defecto)"),
        ]
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
        "turno_id":                       "INTEGER",
        "sector_id":                      "INTEGER",
        "contacto_emergencia_nombre":     "TEXT",
        "contacto_emergencia_telefono":   "TEXT",
        "contacto_emergencia_parentesco": "TEXT",
        "nivel_estudio_id":               "INTEGER",
        "cargo_id":                       "INTEGER",
        "departamento_id":                "INTEGER",
        "categoria_id":                   "INTEGER",
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
        "foto_path":                      "TEXT",
        "horario_habitual_id":            "INTEGER REFERENCES horarios(id)",
        "fecha_recontratacion":           "TEXT",
        "vac_dias_jubilacion":            "REAL",
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

    # Página de inicio por rol
    cols_roles = {r[1] for r in conn.execute("PRAGMA table_info(roles)").fetchall()}
    if "pagina_inicio" not in cols_roles:
        conn.execute("ALTER TABLE roles ADD COLUMN pagina_inicio TEXT NOT NULL DEFAULT '/'")
        logger.info("Migración: columna pagina_inicio agregada a roles")
    cols_usuarios = {r[1] for r in conn.execute("PRAGMA table_info(usuarios)").fetchall()}
    if "pagina_inicio" not in cols_usuarios:
        conn.execute("ALTER TABLE usuarios ADD COLUMN pagina_inicio TEXT")
        logger.info("Migración: columna pagina_inicio agregada a usuarios")
    if "turno_dist" not in cols_usuarios:
        conn.execute("ALTER TABLE usuarios ADD COLUMN turno_dist TEXT")
        logger.info("Migración: columna turno_dist agregada a usuarios")
    if "departamento_dist" not in cols_usuarios:
        conn.execute("ALTER TABLE usuarios ADD COLUMN departamento_dist INTEGER")
        logger.info("Migración: columna departamento_dist agregada a usuarios")

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
    if not conn.execute("SELECT 1 FROM turnos_legajo LIMIT 1").fetchone():
        for t in [("TM", 1), ("TN", 2), ("CORTADO", 3)]:
            conn.execute("INSERT INTO turnos_legajo (nombre, orden) VALUES (?, ?)", t)
    if not conn.execute("SELECT 1 FROM sectores_legajo LIMIT 1").fetchone():
        for s in [("SALÓN", 1), ("COCINA", 2)]:
            conn.execute("INSERT INTO sectores_legajo (nombre, orden) VALUES (?, ?)", s)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS niveles_estudio (
            id     INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT UNIQUE NOT NULL,
            orden  INTEGER NOT NULL DEFAULT 0
        )
    """)
    if not conn.execute("SELECT 1 FROM niveles_estudio LIMIT 1").fetchone():
        for n in [("Primario", 1), ("Secundario", 2), ("Terciario", 3), ("Universitario", 4)]:
            conn.execute("INSERT INTO niveles_estudio (nombre, orden) VALUES (?, ?)", n)

    # ── Módulo distribución semanal ───────────────────────────────────────────
    # sector_id y activo en departamentos
    cols_dept = {r[1] for r in conn.execute("PRAGMA table_info(departamentos)").fetchall()}
    if "sector_id" not in cols_dept:
        conn.execute("ALTER TABLE departamentos ADD COLUMN sector_id INTEGER REFERENCES sectores_legajo(id)")
        logger.info("Migración: columna sector_id agregada a departamentos")
    if "activo" not in cols_dept:
        conn.execute("ALTER TABLE departamentos ADD COLUMN activo INTEGER NOT NULL DEFAULT 1")
        logger.info("Migración: columna activo agregada a departamentos")
    if "usa_distribucion" not in cols_dept:
        conn.execute("ALTER TABLE departamentos ADD COLUMN usa_distribucion INTEGER NOT NULL DEFAULT 0")
        logger.info("Migración: columna usa_distribucion agregada a departamentos")
    if "usa_mozos" not in cols_dept:
        conn.execute("ALTER TABLE departamentos ADD COLUMN usa_mozos INTEGER NOT NULL DEFAULT 0")
        logger.info("Migración: columna usa_mozos agregada a departamentos")
    if "usa_barmans" not in cols_dept:
        conn.execute("ALTER TABLE departamentos ADD COLUMN usa_barmans INTEGER NOT NULL DEFAULT 0")
        logger.info("Migración: columna usa_barmans agregada a departamentos")
    if "usa_peones" not in cols_dept:
        conn.execute("ALTER TABLE departamentos ADD COLUMN usa_peones INTEGER NOT NULL DEFAULT 0")
        logger.info("Migración: columna usa_peones agregada a departamentos")
    if "escribe_planificacion" not in cols_dept:
        conn.execute("ALTER TABLE departamentos ADD COLUMN escribe_planificacion INTEGER NOT NULL DEFAULT 0")
        logger.info("Migración: columna escribe_planificacion agregada a departamentos")
    if "horario_tm_id" not in cols_dept:
        conn.execute("ALTER TABLE departamentos ADD COLUMN horario_tm_id INTEGER REFERENCES horarios(id)")
        logger.info("Migración: columna horario_tm_id agregada a departamentos")
    if "horario_tn_id" not in cols_dept:
        conn.execute("ALTER TABLE departamentos ADD COLUMN horario_tn_id INTEGER REFERENCES horarios(id)")
        logger.info("Migración: columna horario_tn_id agregada a departamentos")
    if "horario_cortado_id" not in cols_dept:
        conn.execute("ALTER TABLE departamentos ADD COLUMN horario_cortado_id INTEGER REFERENCES horarios(id)")
        logger.info("Migración: columna horario_cortado_id agregada a departamentos")
    if "horario_doble_id" not in cols_dept:
        conn.execute("ALTER TABLE departamentos ADD COLUMN horario_doble_id INTEGER REFERENCES horarios(id)")
        logger.info("Migración: columna horario_doble_id agregada a departamentos")

    cols_mdet = {r[1] for r in conn.execute("PRAGMA table_info(mozos_detalle)").fetchall()}
    if cols_mdet and "horario_id" not in cols_mdet:
        conn.execute("ALTER TABLE mozos_detalle ADD COLUMN horario_id INTEGER REFERENCES horarios(id)")
        logger.info("Migración: columna horario_id agregada a mozos_detalle")

    cols_puestos = {r[1] for r in conn.execute("PRAGMA table_info(puestos)").fetchall()}
    if cols_puestos:
        if "es_chef" not in cols_puestos:
            conn.execute("ALTER TABLE puestos ADD COLUMN es_chef INTEGER NOT NULL DEFAULT 0")
            logger.info("Migración: columna es_chef agregada a puestos")
        if "turno" not in cols_puestos:
            conn.execute("ALTER TABLE puestos ADD COLUMN turno TEXT DEFAULT NULL")
            logger.info("Migración: columna turno agregada a puestos")

    # puestos de trabajo dentro de cada departamento
    conn.execute("""
        CREATE TABLE IF NOT EXISTS puestos (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre          TEXT    NOT NULL,
            departamento_id INTEGER NOT NULL REFERENCES departamentos(id),
            orden           INTEGER NOT NULL DEFAULT 0,
            activo          INTEGER NOT NULL DEFAULT 1
        )
    """)

    # semana de distribución (una por departamento+turno+semana)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS distribucion_semana (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            departamento_id INTEGER NOT NULL REFERENCES departamentos(id),
            turno           TEXT    NOT NULL,
            semana_inicio   TEXT    NOT NULL,
            estado          TEXT    NOT NULL DEFAULT 'borrador',
            creado_por      TEXT,
            creado_en       TEXT,
            modificado_por  TEXT,
            modificado_en   TEXT,
            UNIQUE(departamento_id, turno, semana_inicio)
        )
    """)

    # detalle: qué empleado va a qué puesto cada día
    conn.execute("""
        CREATE TABLE IF NOT EXISTS distribucion_detalle (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            distribucion_id INTEGER NOT NULL REFERENCES distribucion_semana(id) ON DELETE CASCADE,
            empleado_id     INTEGER NOT NULL REFERENCES empleados(id),
            puesto_id       INTEGER REFERENCES puestos(id),
            fecha           TEXT    NOT NULL,
            es_franco       INTEGER NOT NULL DEFAULT 0,
            creado_por      TEXT,
            creado_en       TEXT,
            modificado_por  TEXT,
            modificado_en   TEXT
        )
    """)

    # horario elegido por el chef para cada asignación en la grilla
    cols_dd = {r[1] for r in conn.execute("PRAGMA table_info(distribucion_detalle)").fetchall()}
    if "horario_id" not in cols_dd:
        conn.execute("ALTER TABLE distribucion_detalle ADD COLUMN horario_id INTEGER REFERENCES horarios(id)")
        logger.info("Migración: columna horario_id agregada a distribucion_detalle")
    if "es_comida_personal" not in cols_dd:
        conn.execute("ALTER TABLE distribucion_detalle ADD COLUMN es_comida_personal INTEGER NOT NULL DEFAULT 0")
        logger.info("Migración: columna es_comida_personal agregada a distribucion_detalle")

    # francos explícitos por empleado (fila FRANCO al pie de la grilla)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS distribucion_franco (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            distribucion_id INTEGER NOT NULL REFERENCES distribucion_semana(id) ON DELETE CASCADE,
            empleado_id     INTEGER NOT NULL REFERENCES empleados(id),
            fecha           TEXT    NOT NULL,
            creado_por      INTEGER REFERENCES usuarios(id),
            creado_en       TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
            UNIQUE(distribucion_id, empleado_id, fecha)
        )
    """)

    # vacaciones staging por empleado — se pasan a novedades al confirmar
    conn.execute("""
        CREATE TABLE IF NOT EXISTS distribucion_vacacion (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            distribucion_id INTEGER NOT NULL REFERENCES distribucion_semana(id) ON DELETE CASCADE,
            empleado_id     INTEGER NOT NULL REFERENCES empleados(id),
            fecha           TEXT    NOT NULL,
            creado_por      INTEGER REFERENCES usuarios(id),
            creado_en       TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
            UNIQUE(distribucion_id, empleado_id, fecha)
        )
    """)

    # Autorización de cambio de turno / doble turno (por día, excepcional)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS distribucion_pase (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            departamento_id INTEGER NOT NULL REFERENCES departamentos(id),
            empleado_id     INTEGER NOT NULL REFERENCES empleados(id),
            fecha           TEXT    NOT NULL,
            tipo            TEXT    NOT NULL CHECK(tipo IN ('cambio','doble')),
            turno_origen    TEXT    NOT NULL,
            turno_destino   TEXT,
            autorizado_por  INTEGER REFERENCES usuarios(id),
            creado_en       TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
            UNIQUE(departamento_id, empleado_id, fecha)
        )
    """)

    # Licencias (L / LSG) cargadas desde distribución (borrador → novedades al confirmar)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS distribucion_licencia (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            distribucion_id INTEGER NOT NULL REFERENCES distribucion_semana(id) ON DELETE CASCADE,
            empleado_id     INTEGER NOT NULL REFERENCES empleados(id),
            fecha           TEXT    NOT NULL,
            tipo            TEXT    NOT NULL CHECK(tipo IN ('L','LSG')),
            creado_por      INTEGER REFERENCES usuarios(id),
            creado_en       TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
            UNIQUE(distribucion_id, empleado_id, fecha)
        )
    """)

    # ── Módulo Mozos ──────────────────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS mozos_semana (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            departamento_id INTEGER NOT NULL REFERENCES departamentos(id),
            semana_inicio   TEXT    NOT NULL,
            estado          TEXT    NOT NULL DEFAULT 'borrador',
            creado_por      INTEGER REFERENCES usuarios(id),
            creado_en       TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
            modificado_por  INTEGER REFERENCES usuarios(id),
            modificado_en   TEXT,
            UNIQUE(departamento_id, semana_inicio)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS mozos_detalle (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            semana_id   INTEGER NOT NULL REFERENCES mozos_semana(id) ON DELETE CASCADE,
            empleado_id INTEGER NOT NULL REFERENCES empleados(id),
            fecha       TEXT    NOT NULL,
            turno       TEXT    NOT NULL,
            estado      TEXT    NOT NULL,
            creado_por  INTEGER REFERENCES usuarios(id),
            creado_en   TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
            UNIQUE(semana_id, empleado_id, fecha, turno)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS usuarios_mozos (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id      INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
            departamento_id INTEGER NOT NULL REFERENCES departamentos(id) ON DELETE CASCADE,
            UNIQUE(usuario_id, departamento_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS mozos_config (
            departamento_id INTEGER NOT NULL REFERENCES departamentos(id) ON DELETE CASCADE,
            clave           TEXT NOT NULL,
            valor           TEXT,
            PRIMARY KEY (departamento_id, clave)
        )
    """)

    # ── Módulo Barmans ────────────────────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS barmans_semana (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            departamento_id INTEGER NOT NULL REFERENCES departamentos(id),
            semana_inicio   TEXT    NOT NULL,
            estado          TEXT    NOT NULL DEFAULT 'borrador',
            creado_por      INTEGER REFERENCES usuarios(id),
            creado_en       TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
            modificado_por  INTEGER REFERENCES usuarios(id),
            modificado_en   TEXT,
            UNIQUE(departamento_id, semana_inicio)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS barmans_detalle (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            semana_id   INTEGER NOT NULL REFERENCES barmans_semana(id) ON DELETE CASCADE,
            empleado_id INTEGER NOT NULL REFERENCES empleados(id),
            fecha       TEXT    NOT NULL,
            turno       TEXT    NOT NULL,
            valor       TEXT    NOT NULL,
            creado_por  INTEGER REFERENCES usuarios(id),
            creado_en   TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
            UNIQUE(semana_id, empleado_id, fecha, turno)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS usuarios_barmans (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id      INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
            departamento_id INTEGER NOT NULL REFERENCES departamentos(id) ON DELETE CASCADE,
            UNIQUE(usuario_id, departamento_id)
        )
    """)

    # qué usuarios pueden planificar qué departamento+turno
    conn.execute("""
        CREATE TABLE IF NOT EXISTS usuarios_distribucion (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id      INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
            departamento_id INTEGER NOT NULL REFERENCES departamentos(id) ON DELETE CASCADE,
            turno           TEXT    NOT NULL,
            UNIQUE(usuario_id, departamento_id, turno)
        )
    """)

    # ── Módulo Peones (tablas propias, independientes de distribución) ─────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS peones_semana (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            departamento_id INTEGER NOT NULL REFERENCES departamentos(id),
            turno           TEXT    NOT NULL,
            semana_inicio   TEXT    NOT NULL,
            estado          TEXT    NOT NULL DEFAULT 'borrador',
            creado_por      TEXT,
            creado_en       TEXT,
            modificado_por  TEXT,
            modificado_en   TEXT,
            UNIQUE(departamento_id, turno, semana_inicio)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS peones_detalle (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            distribucion_id INTEGER NOT NULL REFERENCES peones_semana(id) ON DELETE CASCADE,
            empleado_id     INTEGER NOT NULL REFERENCES empleados(id),
            puesto_id       INTEGER REFERENCES puestos(id),
            fecha           TEXT    NOT NULL,
            es_franco       INTEGER NOT NULL DEFAULT 0,
            horario_id      INTEGER REFERENCES horarios(id),
            creado_por      TEXT,
            creado_en       TEXT,
            modificado_por  TEXT,
            modificado_en   TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS peones_franco (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            distribucion_id INTEGER NOT NULL REFERENCES peones_semana(id) ON DELETE CASCADE,
            empleado_id     INTEGER NOT NULL REFERENCES empleados(id),
            fecha           TEXT    NOT NULL,
            creado_por      INTEGER REFERENCES usuarios(id),
            creado_en       TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
            UNIQUE(distribucion_id, empleado_id, fecha)
        )
    """)
    # Un peón no puede tener franco y estar asignado a un puesto el mismo día:
    # al confirmar, ambos escriben planificacion(empleado_id, fecha), que es UNIQUE.
    # Los triggers cierran TODOS los caminos de escritura, no sólo los endpoints.
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS trg_peones_franco_sin_puesto
        BEFORE INSERT ON peones_franco
        WHEN EXISTS (
            SELECT 1 FROM peones_detalle d
            WHERE d.distribucion_id = NEW.distribucion_id
              AND d.empleado_id     = NEW.empleado_id
              AND d.fecha           = NEW.fecha
              AND d.es_franco       = 0
        )
        BEGIN
            SELECT RAISE(ABORT, 'El empleado ya está asignado a un puesto ese día');
        END
    """)
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS trg_peones_detalle_sin_franco
        BEFORE INSERT ON peones_detalle
        WHEN NEW.es_franco = 0 AND EXISTS (
            SELECT 1 FROM peones_franco f
            WHERE f.distribucion_id = NEW.distribucion_id
              AND f.empleado_id     = NEW.empleado_id
              AND f.fecha           = NEW.fecha
        )
        BEGIN
            SELECT RAISE(ABORT, 'El empleado tiene franco ese día');
        END
    """)
    # Mismo criterio para distribución. Ojo: acá SÍ son válidos un empleado en dos
    # puestos el mismo día y la comida de personal (marcador sin puesto ni horario),
    # así que el trigger sólo prohíbe la contradicción franco ↔ puesto real.
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS trg_distribucion_franco_sin_puesto
        BEFORE INSERT ON distribucion_franco
        WHEN EXISTS (
            SELECT 1 FROM distribucion_detalle d
            WHERE d.distribucion_id     = NEW.distribucion_id
              AND d.empleado_id         = NEW.empleado_id
              AND d.fecha               = NEW.fecha
              AND d.es_franco           = 0
              AND d.es_comida_personal  = 0
        )
        BEGIN
            SELECT RAISE(ABORT, 'El empleado ya está asignado a un puesto ese día');
        END
    """)
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS trg_distribucion_detalle_sin_franco
        BEFORE INSERT ON distribucion_detalle
        WHEN NEW.es_franco = 0 AND NEW.es_comida_personal = 0 AND EXISTS (
            SELECT 1 FROM distribucion_franco f
            WHERE f.distribucion_id = NEW.distribucion_id
              AND f.empleado_id     = NEW.empleado_id
              AND f.fecha           = NEW.fecha
        )
        BEGIN
            SELECT RAISE(ABORT, 'El empleado tiene franco ese día');
        END
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS peones_vacacion (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            distribucion_id INTEGER NOT NULL REFERENCES peones_semana(id) ON DELETE CASCADE,
            empleado_id     INTEGER NOT NULL REFERENCES empleados(id),
            fecha           TEXT    NOT NULL,
            creado_por      INTEGER REFERENCES usuarios(id),
            creado_en       TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
            UNIQUE(distribucion_id, empleado_id, fecha)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS usuarios_peones (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id      INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
            departamento_id INTEGER NOT NULL REFERENCES departamentos(id) ON DELETE CASCADE,
            turno           TEXT    NOT NULL,
            UNIQUE(usuario_id, departamento_id, turno)
        )
    """)
    conn.execute("""CREATE TABLE IF NOT EXISTS peones_aviso_config (
        turno       TEXT PRIMARY KEY,
        dia_semana  INTEGER NOT NULL DEFAULT 4,
        hora        TEXT    NOT NULL DEFAULT '18:00',
        activo      INTEGER NOT NULL DEFAULT 1
    )""")
    for turno in ('TM', 'TN', 'CO'):
        conn.execute(
            "INSERT OR IGNORE INTO peones_aviso_config (turno) VALUES (?)", (turno,)
        )

    # origen en planificacion: NULL=manual, 'distribucion'=generado por grilla
    cols_plan = {r[1] for r in conn.execute("PRAGMA table_info(planificacion)").fetchall()}
    if "origen" not in cols_plan:
        conn.execute("ALTER TABLE planificacion ADD COLUMN origen TEXT DEFAULT NULL")
        logger.info("Migración: columna origen agregada a planificacion")

    # Departamentos operativos por defecto (se agregan si no existen)
    _depts_default = [
        ("Cocineros",         "COCINA"),
        ("Peones de limpieza","COCINA"),
        ("Mozos",             "SALÓN"),
        ("Mozos de barra",    "SALÓN"),
    ]
    for dept_nombre, sector_nombre in _depts_default:
        exists = conn.execute("SELECT id FROM departamentos WHERE nombre=?", (dept_nombre,)).fetchone()
        if not exists:
            sector = conn.execute("SELECT id FROM sectores_legajo WHERE nombre=?", (sector_nombre,)).fetchone()
            sector_id = sector["id"] if sector else None
            conn.execute(
                "INSERT INTO departamentos (nombre, sector_id, activo) VALUES (?,?,1)",
                (dept_nombre, sector_id)
            )
            logger.info("Departamento creado: %s (sector %s)", dept_nombre, sector_nombre)

    # Puestos de trabajo por departamento (se agregan si no existen)
    # Cada entrada es (nombre_puesto, nombre_departamento).
    # Los puestos repetidos representan slots: PARRILLA ×3 = hasta 3 personas ese día.
    _puestos_default = [
        ("HORNO",          "Cocineros"),
        ("JOSPER",         "Cocineros"),
        ("PARRILLA",       "Cocineros"),
        ("PARRILLA",       "Cocineros"),
        ("PARRILLA",       "Cocineros"),
        ("ENSALADAS",      "Cocineros"),
        ("ENSALADAS",      "Cocineros"),
        ("PLANCHA",        "Cocineros"),
        ("COCINA",         "Cocineros"),
        ("COCINA",         "Cocineros"),
        ("PASTAS",         "Cocineros"),
        ("PASTAS",         "Cocineros"),
        ("MINUTA",         "Cocineros"),
        ("MINUTA",         "Cocineros"),
        ("POSTRES",        "Cocineros"),
        ("POSTRES",        "Cocineros"),
        ("PREPARACIONES",  "Cocineros"),
        ("PANADERIA",      "Cocineros"),
        ("PANADERIA",      "Cocineros"),
        ("PANADERIA",      "Cocineros"),
        ("PANADERIA",      "Cocineros"),
        ("PROD PASTAS",    "Cocineros"),
        ("PROD PASTAS",    "Cocineros"),
    ]
    # asistencia:fichaje_manual se separó de asistencia:editar. Los roles que ya
    # venían corrigiendo asistencia lo conservan, para no cortar la operación al
    # actualizar; sacárselo a alguno es después una decisión de la pantalla de Roles.
    ya_migrado = conn.execute(
        "SELECT COUNT(*) FROM permisos WHERE modulo='asistencia' AND accion='fichaje_manual'"
    ).fetchone()[0]
    if not ya_migrado:
        n = conn.execute(
            """INSERT OR IGNORE INTO permisos (rol_id, modulo, accion)
               SELECT p.rol_id, 'asistencia', 'fichaje_manual'
               FROM permisos p JOIN roles r ON r.id = p.rol_id
               WHERE p.modulo='asistencia' AND p.accion='editar'"""
        ).rowcount
        if n:
            logger.info("Migración: asistencia:fichaje_manual otorgado a %d rol(es) con asistencia:editar", n)

    # Filas de permisos que no pueden corresponder a nada. Se recalcula en cada
    # arranque a propósito: son invariantes, no una migración de una sola vez.
    #
    # 1) De roles que ya no existen. Los dejó el sembrado duplicado de roles default
    #    que arrastraba ensure_admin, cuando esos duplicados se borraron sin que la
    #    FK cascadeara. Son inalcanzables porque roles.id es AUTOINCREMENT y nunca
    #    reasigna un id, pero ensucian la tabla.
    huerfanos = conn.execute(
        "SELECT COUNT(*) FROM permisos WHERE rol_id NOT IN (SELECT id FROM roles)"
    ).fetchone()[0]
    if huerfanos:
        conn.execute("DELETE FROM permisos WHERE rol_id NOT IN (SELECT id FROM roles)")
        logger.info("Migración: %d permisos de roles inexistentes eliminados", huerfanos)

    # 2) De combinaciones módulo+acción que el código ya no reconoce, como
    #    empleados:eliminar (nunca hubo baja física de empleados). No las otorga
    #    nadie ni las consulta nadie, pero quedan tildadas en la base.
    #    Import local: auth.core importa este módulo, al revés sería circular.
    from auth.core import MODULO_ACCIONES
    validas = {(m, a) for m, acs in MODULO_ACCIONES.items() for a in acs}
    sobrantes = [
        r["id"] for r in conn.execute("SELECT id, modulo, accion FROM permisos").fetchall()
        if (r["modulo"], r["accion"]) not in validas
    ]
    if sobrantes:
        conn.executemany("DELETE FROM permisos WHERE id=?", [(i,) for i in sobrantes])
        logger.info("Migración: %d permisos de acciones inexistentes eliminados", len(sobrantes))

    for orden, (puesto_nombre, dept_nombre) in enumerate(_puestos_default):
        dept = conn.execute("SELECT id FROM departamentos WHERE nombre=?", (dept_nombre,)).fetchone()
        if not dept:
            continue
        # Cuenta cuántos puestos con ese nombre ya existen para evitar duplicar en reinicios
        ya_existen = conn.execute(
            "SELECT COUNT(*) FROM puestos WHERE nombre=? AND departamento_id=?",
            (puesto_nombre, dept["id"])
        ).fetchone()[0]
        # Calcula cuántas veces aparece este nombre en la lista hasta este índice (inclusive)
        slot_num = sum(1 for i, (n, d) in enumerate(_puestos_default) if i <= orden and n == puesto_nombre and d == dept_nombre)
        if ya_existen < slot_num:
            conn.execute(
                "INSERT INTO puestos (nombre, departamento_id, orden, activo) VALUES (?,?,?,1)",
                (puesto_nombre, dept["id"], orden)
            )
            logger.info("Puesto creado: %s (%s)", puesto_nombre, dept_nombre)
