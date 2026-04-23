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
                observaciones    TEXT,
                creado_en        TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                modificado_en    TEXT
            );

            -- ================================================================
            -- HORARIOS
            -- ================================================================
            CREATE TABLE IF NOT EXISTS horarios (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre    TEXT NOT NULL,
                tipo      TEXT NOT NULL CHECK(tipo IN ('simple','cortado')),
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
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                empleado_id   INTEGER NOT NULL,
                horario_id    INTEGER,
                calendario_id INTEGER NOT NULL,
                fecha_desde   TEXT NOT NULL,
                fecha_hasta   TEXT,
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

        """)
        _migrate(conn)
    logger.info("Base de datos inicializada: %s", DB_PATH)


def _migrate(conn):
    """Migraciones incrementales para bases de datos existentes."""
    cols_cd = {r[1] for r in conn.execute("PRAGMA table_info(calendarios_dias)").fetchall()}
    if "es_franco" not in cols_cd:
        conn.execute("ALTER TABLE calendarios_dias ADD COLUMN es_franco INTEGER NOT NULL DEFAULT 0")
        conn.execute("UPDATE calendarios_dias SET es_franco=1 WHERE horario_id IS NULL")
        logger.info("Migración: columna es_franco agregada a calendarios_dias")
