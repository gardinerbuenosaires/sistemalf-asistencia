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
            CREATE TABLE IF NOT EXISTS empleados (
                user_id     TEXT PRIMARY KEY,
                nombre      TEXT NOT NULL,
                departamento TEXT,
                turno       TEXT,
                activo      INTEGER NOT NULL DEFAULT 1,
                creado_en   TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS fichajes (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT NOT NULL,
                timestamp   TEXT NOT NULL,
                tipo        TEXT,         -- 'entrada' | 'salida' | NULL (sin calcular)
                punch_raw   INTEGER NOT NULL DEFAULT 0,
                status_raw  INTEGER NOT NULL DEFAULT 0,
                creado_en   TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                UNIQUE (user_id, timestamp),
                FOREIGN KEY (user_id) REFERENCES empleados(user_id)
            );

            CREATE TABLE IF NOT EXISTS inconsistencias (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT NOT NULL,
                fecha       TEXT NOT NULL,
                motivo      TEXT NOT NULL,
                resuelta    INTEGER NOT NULL DEFAULT 0,
                creado_en   TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS sync_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                iniciado_en     TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                finalizado_en   TEXT,
                registros_leidos    INTEGER DEFAULT 0,
                registros_nuevos    INTEGER DEFAULT 0,
                error           TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_fichajes_user_fecha
                ON fichajes (user_id, timestamp);

            CREATE INDEX IF NOT EXISTS idx_inconsistencias_user_fecha
                ON inconsistencias (user_id, fecha);
        """)
    logger.info("Base de datos inicializada: %s", DB_PATH)
