"""
Ejecutar en esta PC después de copiar usuarios_zk.json del equipo local.
Crea los empleados nuevos en fichajes.db (no toca los que ya existen).
"""
import json
from db.database import init_db, db_session

INPUT_FILE = "usuarios_zk.json"

def main():
    init_db()
    with open(INPUT_FILE, encoding="utf-8") as f:
        users = json.load(f)
    print(f"Usuarios en archivo: {len(users)}")

    nuevos = 0
    omitidos = 0
    with db_session() as conn:
        existentes = {
            row[0]
            for row in conn.execute(
                "SELECT user_id FROM empleados WHERE user_id IS NOT NULL"
            )
        }
        for u in users:
            uid = u["user_id"]
            if not uid or uid in existentes:
                omitidos += 1
                continue
            nombre_raw = u["name"]
            partes = nombre_raw.split()
            if len(partes) >= 2:
                apellido = partes[0]
                nombre = " ".join(partes[1:])
            else:
                apellido = nombre_raw
                nombre = ""
            conn.execute(
                "INSERT OR IGNORE INTO empleados (user_id, nombre, apellido, activo) VALUES (?, ?, ?, 1)",
                (uid, nombre, apellido),
            )
            if conn.execute("SELECT changes()").fetchone()[0]:
                nuevos += 1
                existentes.add(uid)
                print(f"  + {uid:>6}  {apellido}, {nombre}")

    print(f"\nNuevos: {nuevos} | Omitidos (ya existían): {omitidos}")

if __name__ == "__main__":
    main()
