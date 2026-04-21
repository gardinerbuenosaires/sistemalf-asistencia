"""
Descarga registros y usuarios del dispositivo ZKTeco y los almacena en SQLite.

No se llama a disable_device() ni clear_attendance() para no interferir
con el software Enterprise del fabricante que corre en paralelo.
"""
import logging
from datetime import datetime
from zk import ZK
from config import DEVICE_IP, DEVICE_PORT, DEVICE_PASSWORD, DEVICE_TIMEOUT
from db.database import db_session

logger = logging.getLogger(__name__)


def _connect():
    zk = ZK(
        DEVICE_IP,
        port=DEVICE_PORT,
        timeout=DEVICE_TIMEOUT,
        password=DEVICE_PASSWORD,
        force_udp=False,
        ommit_ping=False,
    )
    return zk.connect()


def sync_attendances() -> dict:
    """
    Conecta al dispositivo, descarga todos los registros y guarda los nuevos.
    Devuelve un resumen {leidos, nuevos, errores}.
    """
    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    result = {"leidos": 0, "nuevos": 0, "error": None}
    log_id = _start_log(started_at)

    conn_zk = None
    try:
        logger.info("Conectando a %s:%s...", DEVICE_IP, DEVICE_PORT)
        conn_zk = _connect()

        attendances = conn_zk.get_attendance()
        result["leidos"] = len(attendances)
        logger.info("Registros leídos del dispositivo: %d", result["leidos"])

        result["nuevos"] = _save_attendances(attendances)
        logger.info("Registros nuevos guardados: %d", result["nuevos"])

    except Exception as exc:
        result["error"] = str(exc)
        logger.error("Error en sync: %s", exc)
    finally:
        if conn_zk:
            try:
                conn_zk.disconnect()
            except Exception:
                pass
        _finish_log(log_id, result, started_at)

    return result


def _save_attendances(attendances) -> int:
    """Inserta registros nuevos (ignora duplicados por user_id + timestamp)."""
    nuevos = 0
    rows = [
        (
            str(a.user_id),
            a.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            a.punch,
            a.status,
        )
        for a in attendances
        if a.timestamp is not None
    ]

    with db_session() as conn:
        # Cache user_id → empleado_id para evitar consultas repetidas
        cache: dict[str, int | None] = {}

        for user_id, ts, punch, status in rows:
            if user_id not in cache:
                row = conn.execute(
                    "SELECT id FROM empleados WHERE user_id = ?", (user_id,)
                ).fetchone()
                cache[user_id] = row["id"] if row else None

            empleado_id = cache[user_id]

            cur = conn.execute(
                """
                INSERT OR IGNORE INTO fichajes
                    (empleado_id, user_id, timestamp, punch_raw, status_raw)
                VALUES (?, ?, ?, ?, ?)
                """,
                (empleado_id, user_id, ts, punch, status),
            )
            if cur.rowcount:
                nuevos += 1

    return nuevos


def sync_users() -> dict:
    """
    Conecta al dispositivo, descarga la lista de usuarios y crea los empleados
    que aún no existen en la BD (por user_id). No modifica registros existentes.
    Devuelve {leidos, nuevos, errores}.
    """
    result = {"leidos": 0, "nuevos": 0, "error": None}
    conn_zk = None
    try:
        logger.info("Conectando a %s:%s para sync de usuarios...", DEVICE_IP, DEVICE_PORT)
        conn_zk = _connect()
        users = conn_zk.get_users()
        result["leidos"] = len(users)
        logger.info("Usuarios leídos del dispositivo: %d", result["leidos"])
        result["nuevos"] = _save_users(users)
        logger.info("Empleados nuevos creados: %d", result["nuevos"])
    except Exception as exc:
        result["error"] = str(exc)
        logger.error("Error en sync_users: %s", exc)
    finally:
        if conn_zk:
            try:
                conn_zk.disconnect()
            except Exception:
                pass
    return result


def _save_users(users) -> int:
    """
    Inserta empleados nuevos a partir de los usuarios del dispositivo.
    Usa user_id como clave — si ya existe, lo omite.
    El campo name del ZKTeco suele ser 'APELLIDO NOMBRE' o 'NOMBRE APELLIDO';
    se guarda el nombre completo en apellido y nombre vacío hasta enriquecer con Excel.
    """
    nuevos = 0
    with db_session() as conn:
        existentes = {
            row[0]
            for row in conn.execute("SELECT user_id FROM empleados WHERE user_id IS NOT NULL")
        }
        for u in users:
            uid = str(u.user_id).strip()
            if not uid or uid in existentes:
                continue
            nombre_raw = (u.name or "").strip()
            partes = nombre_raw.split()
            if len(partes) >= 2:
                apellido = partes[0]
                nombre = " ".join(partes[1:])
            else:
                apellido = nombre_raw
                nombre = ""
            conn.execute(
                """
                INSERT OR IGNORE INTO empleados (user_id, nombre, apellido, activo)
                VALUES (?, ?, ?, 1)
                """,
                (uid, nombre, apellido),
            )
            if conn.execute(
                "SELECT changes()"
            ).fetchone()[0]:
                nuevos += 1
                existentes.add(uid)
    return nuevos


def _start_log(started_at: str) -> int:
    with db_session() as conn:
        cur = conn.execute(
            "INSERT INTO sync_log (iniciado_en) VALUES (?)", (started_at,)
        )
        return cur.lastrowid


def _finish_log(log_id: int, result: dict, started_at: str):
    finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with db_session() as conn:
        conn.execute(
            """
            UPDATE sync_log
            SET finalizado_en = ?, registros_leidos = ?, registros_nuevos = ?, error = ?
            WHERE id = ?
            """,
            (
                finished_at,
                result["leidos"],
                result["nuevos"],
                result["error"],
                log_id,
            ),
        )
