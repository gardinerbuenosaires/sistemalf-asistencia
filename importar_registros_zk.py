"""
Importa registros_zk.json a la tabla fichajes.
Usar cuando no hay acceso directo al dispositivo.
"""
import json
from db.database import init_db, db_session

INPUT_FILE = "registros_zk.json"

def main():
    init_db()
    with open(INPUT_FILE, encoding="utf-8") as f:
        registros = json.load(f)
    print(f"Registros en archivo: {len(registros)}")

    nuevos = 0
    with db_session() as conn:
        for r in registros:
            user_id = str(r["user_id"]).strip()
            timestamp = r["timestamp"]
            punch = r.get("punch", 0)
            status = r.get("status", 0)

            empleado = conn.execute(
                "SELECT id FROM empleados WHERE user_id = ?", (user_id,)
            ).fetchone()
            empleado_id = empleado["id"] if empleado else None

            cur = conn.execute(
                """
                INSERT OR IGNORE INTO fichajes
                    (empleado_id, user_id, timestamp, punch_raw, status_raw)
                VALUES (?, ?, ?, ?, ?)
                """,
                (empleado_id, user_id, timestamp, punch, status),
            )
            if cur.rowcount:
                nuevos += 1

    print(f"Nuevos fichajes importados: {nuevos}")
    print(f"Omitidos (ya existían): {len(registros) - nuevos}")

if __name__ == "__main__":
    main()
