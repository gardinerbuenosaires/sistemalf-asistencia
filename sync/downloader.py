"""
Descarga registros y usuarios del dispositivo ZKTeco y los almacena en SQLite.

No se llama a disable_device() ni clear_attendance() para no interferir
con el software Enterprise del fabricante que corre en paralelo.
"""
import logging
from datetime import datetime
from zk import ZK
from config import DEVICE_IP, DEVICE_PORT, DEVICE_PASSWORD, DEVICE_TIMEOUT
from db.database import db_session, get_config

logger = logging.getLogger(__name__)


def _get_device_config() -> dict:
    """Lee config del dispositivo desde la DB; cae a config.py si falla."""
    try:
        with db_session() as conn:
            return {
                "ip":       get_config(conn, "device_ip",       DEVICE_IP),
                "port":     int(get_config(conn, "device_port",     str(DEVICE_PORT))),
                "password": int(get_config(conn, "device_password", str(DEVICE_PASSWORD))),
                "timeout":  int(get_config(conn, "device_timeout",  str(DEVICE_TIMEOUT))),
            }
    except Exception:
        return {"ip": DEVICE_IP, "port": DEVICE_PORT,
                "password": DEVICE_PASSWORD, "timeout": DEVICE_TIMEOUT}


def _connect():
    cfg = _get_device_config()
    zk = ZK(
        cfg["ip"],
        port=cfg["port"],
        timeout=cfg["timeout"],
        password=cfg["password"],
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
        cfg = _get_device_config()
        logger.info("Conectando a %s:%s...", cfg["ip"], cfg["port"])
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
        # Cache user_id → {id, activo}
        cache: dict[str, dict | None] = {}

        for user_id, ts, punch, status in rows:
            if user_id not in cache:
                row = conn.execute(
                    "SELECT id, activo FROM empleados WHERE user_id = ?", (user_id,)
                ).fetchone()
                cache[user_id] = dict(row) if row else None

            emp = cache[user_id]
            empleado_id = emp["id"] if emp else None

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
                # Si el empleado está dado de baja, registrar inconsistencia
                if emp and emp["activo"] == 0:
                    fecha = ts[:10]
                    conn.execute(
                        """INSERT INTO inconsistencias (empleado_id, user_id, fecha, tipo, motivo)
                           SELECT ?, ?, ?, 'fichaje_baja',
                               'Fichaje recibido de un empleado dado de baja'
                           WHERE NOT EXISTS (
                               SELECT 1 FROM inconsistencias
                               WHERE empleado_id=? AND fecha=? AND tipo='fichaje_baja' AND resuelta=0
                           )""",
                        (empleado_id, user_id, fecha, empleado_id, fecha),
                    )

    return nuevos


def sync_time() -> dict:
    """Sincroniza la hora del servidor con el dispositivo ZKTeco."""
    result = {"ok": False, "error": None}
    conn_zk = None
    try:
        conn_zk = _connect()
        conn_zk.set_time(datetime.now())
        result["ok"] = True
        logger.info("Hora del dispositivo sincronizada correctamente")
    except Exception as exc:
        result["error"] = str(exc)
        logger.error("Error sincronizando hora del dispositivo: %s", exc)
    finally:
        if conn_zk:
            try:
                conn_zk.disconnect()
            except Exception:
                pass
    return result


def restart_device() -> dict:
    """Reinicia el dispositivo ZKTeco."""
    result = {"ok": False, "error": None}
    conn_zk = None
    try:
        conn_zk = _connect()
        conn_zk.restart()
        result["ok"] = True
        logger.info("Dispositivo ZKTeco reiniciado correctamente")
    except Exception as exc:
        result["error"] = str(exc)
        logger.error("Error al reiniciar el dispositivo: %s", exc)
    finally:
        if conn_zk:
            try:
                conn_zk.disconnect()
            except Exception:
                pass
    return result


def clear_attendance() -> dict:
    """
    Borra todos los registros de asistencia del dispositivo ZKTeco.
    Solo llamar después de verificar que el sync trajo 0 registros nuevos.
    """
    result = {"ok": False, "error": None}
    conn_zk = None
    try:
        conn_zk = _connect()
        conn_zk.clear_attendance()
        result["ok"] = True
        logger.info("Registros de asistencia del dispositivo eliminados correctamente")
    except Exception as exc:
        result["error"] = str(exc)
        logger.error("Error al limpiar registros del dispositivo: %s", exc)
    finally:
        if conn_zk:
            try:
                conn_zk.disconnect()
            except Exception:
                pass
    return result


def sync_users() -> dict:
    """
    Conecta al dispositivo, descarga la lista de usuarios y crea los empleados
    que aún no existen en la BD (por user_id). Solo lectura del dispositivo.
    Devuelve {leidos, nuevos, fichajes_vinculados, error}.
    """
    result = {"leidos": 0, "nuevos": 0, "nuevos_ids": [], "fichajes_vinculados": 0, "fuera_de_dispositivo": [], "error": None}
    conn_zk = None
    try:
        cfg = _get_device_config()
        logger.info("Conectando a %s:%s para sync de usuarios...", cfg["ip"], cfg["port"])
        conn_zk = _connect()
        users = conn_zk.get_users()
        result["leidos"] = len(users)
        logger.info("Usuarios leídos del dispositivo: %d", result["leidos"])
        saved = _save_users(users)
        result["nuevos"] = saved["nuevos"]
        result["nuevos_ids"] = saved["nuevos_ids"]
        result["fichajes_vinculados"] = saved["fichajes_vinculados"]
        result["fuera_de_dispositivo"] = saved["fuera_de_dispositivo"]
        logger.info("Empleados nuevos: %d | Fichajes vinculados: %d | Fuera de dispositivo: %d",
                    result["nuevos"], result["fichajes_vinculados"], len(result["fuera_de_dispositivo"]))
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


def _save_users(users) -> dict:
    """
    Inserta empleados nuevos a partir de los usuarios del dispositivo.
    Usa user_id como clave — si ya existe (activo o inactivo), lo omite.
    Nombre y apellido se inicializan con el user_id para facilitar identificación.
    Marca en_dispositivo=0 para empleados activos que ya no aparecen en el dispositivo.
    Luego revincula fichajes huérfanos (empleado_id IS NULL) con los empleados existentes.
    """
    device_ids = {str(u.user_id).strip() for u in users if str(u.user_id).strip()}
    nuevos_ids = []
    fuera_ids  = []

    with db_session() as conn:
        existentes = {
            row[0]
            for row in conn.execute(
                "SELECT user_id FROM empleados WHERE user_id IS NOT NULL"
            )
        }
        for uid in device_ids:
            if not uid or uid in existentes:
                continue
            conn.execute(
                "INSERT OR IGNORE INTO empleados (user_id, nombre, apellido, activo) VALUES (?,?,?,1)",
                (uid, uid, uid),
            )
            if conn.execute("SELECT changes()").fetchone()[0]:
                eid = conn.execute("SELECT id FROM empleados WHERE user_id=?", (uid,)).fetchone()[0]
                nuevos_ids.append(eid)
                existentes.add(uid)

        # Marcar en_dispositivo según presencia en la lista del ZKTeco
        activos = conn.execute(
            "SELECT id, user_id FROM empleados WHERE activo=1 AND user_id IS NOT NULL AND tipo != 'acceso'"
        ).fetchall()
        for emp in activos:
            en_disp = 1 if emp["user_id"] in device_ids else 0
            conn.execute("UPDATE empleados SET en_dispositivo=? WHERE id=?", (en_disp, emp["id"]))
            if not en_disp:
                fuera_ids.append(emp["id"])
        if fuera_ids:
            logger.warning("Empleados activos no encontrados en dispositivo: %s", fuera_ids)

        # Revincular fichajes huérfanos con todos los empleados (no solo los recién creados)
        cur = conn.execute("""
            UPDATE fichajes
            SET empleado_id = (
                SELECT id FROM empleados WHERE empleados.user_id = fichajes.user_id
            )
            WHERE empleado_id IS NULL
              AND EXISTS (
                SELECT 1 FROM empleados WHERE empleados.user_id = fichajes.user_id
              )
        """)
        vinculados = cur.rowcount

    return {"nuevos": len(nuevos_ids), "nuevos_ids": nuevos_ids,
            "fichajes_vinculados": vinculados, "fuera_de_dispositivo": fuera_ids}


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
