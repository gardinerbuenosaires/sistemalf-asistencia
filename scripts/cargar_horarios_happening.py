"""
Carga los horarios de Happening en la BD.

Uso:
    python scripts/cargar_horarios_happening.py
"""
import os
import sys

os.environ["DB_PATH"] = "data_happening/fichajes.db"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.database import db_session

# Cada horario: nombre, tipo, lista de bloques
# Bloque: (num, hora_entrada, hora_salida, cruza_medianoche,
#          t_entrada_antes, t_entrada_despues, t_tarde, t_salida_antes, t_salida_despues)

HORARIOS = [
    # cod 0001
    {
        "nombre": "08:00 a 16:30",
        "tipo": "simple",
        "bloques": [(1, "08:00", "16:30", 0,  0, 10, 10,  0, 30)],
    },
    # cod 0002
    {
        "nombre": "16:30 a 01:00",
        "tipo": "simple",
        "bloques": [(1, "16:30", "01:00", 1,  0, 10, 10,  0, 30)],
    },
    # cod 0003 — Enterprise description dice "02:00" pero salida teórica en el form es 02:30
    {
        "nombre": "17:30 a 02:30",
        "tipo": "simple",
        "bloques": [(1, "17:30", "02:30", 1,  0, 10, 10,  0, 30)],
    },
    # cod 0004
    {
        "nombre": "09:00 a 17:30",
        "tipo": "simple",
        "bloques": [(1, "09:00", "17:30", 0,  0, 10, 10,  0, 30)],
    },
    # cod 0005 — cortado; bloque 1 usa t_s_despues=15 (deben volver para la segunda sesión)
    {
        "nombre": "11:45 a 16:15 y 19:45 a 00:30",
        "tipo": "cortado",
        "bloques": [
            (1, "11:45", "16:15", 0,  0, 10, 10,  0, 15),
            (2, "19:45", "00:30", 1,  0, 10, 10,  0, 30),
        ],
    },
    # cod 0006
    {
        "nombre": "08:00 a 12:00",
        "tipo": "simple",
        "bloques": [(1, "08:00", "12:00", 0,  0, 10, 10, 10, 30)],
    },
    # cod 0007
    {
        "nombre": "10:30 a 17:30",
        "tipo": "simple",
        "bloques": [(1, "10:30", "17:30", 0, 10, 10, 10, 10, 10)],
    },
    # cod 0008
    {
        "nombre": "19:00 a 02:00",
        "tipo": "simple",
        "bloques": [(1, "19:00", "02:00", 1, 10, 10, 10, 10, 30)],
    },
    # cod 0009 — Enterprise muestra fin salida 01:15 antes de teórica 01:30; usamos 30 min de gracia
    {
        "nombre": "17:30 a 01:30",
        "tipo": "simple",
        "bloques": [(1, "17:30", "01:30", 1, 15, 15, 15, 40, 30)],
    },
    # cod 0010
    {
        "nombre": "00:00 a 08:00",
        "tipo": "simple",
        "bloques": [(1, "00:00", "08:00", 1, 10, 10, 10, 10, 10)],
    },
    # cod 0011
    {
        "nombre": "11:30 a 17:30",
        "tipo": "simple",
        "bloques": [(1, "11:30", "17:30", 0, 30, 15, 15, 15, 30)],
    },
    # cod 0012
    {
        "nombre": "16:00 a 00:00",
        "tipo": "simple",
        "bloques": [(1, "16:00", "00:00", 1, 15, 30, 30, 40, 30)],
    },
    # cod 0013
    {
        "nombre": "20:00 a 01:30",
        "tipo": "simple",
        "bloques": [(1, "20:00", "01:30", 1, 10, 10, 10, 10, 15)],
    },
    # cod 0014 — Sereno; Enterprise usaba 00:01 como entrada teórica, redondeado a 00:00
    {
        "nombre": "Sereno",
        "tipo": "simple",
        "bloques": [(1, "00:00", "08:00", 1, 15, 30, 30, 30, 30)],
    },
    # cod 0015
    {
        "nombre": "18:30 a 02:00",
        "tipo": "simple",
        "bloques": [(1, "18:30", "02:00", 1,  0, 15, 15,  0, 30)],
    },
]


def main():
    insertados = 0
    omitidos = 0

    with db_session() as conn:
        for h in HORARIOS:
            existing = conn.execute(
                "SELECT id FROM horarios WHERE nombre = ?", (h["nombre"],)
            ).fetchone()
            if existing:
                print(f"  = ya existe: {h['nombre']}")
                omitidos += 1
                continue

            cur = conn.execute(
                "INSERT INTO horarios (nombre, tipo) VALUES (?, ?)",
                (h["nombre"], h["tipo"]),
            )
            hid = cur.lastrowid

            for blq in h["bloques"]:
                num, he, hs, cm, tea, ted, tt, tsa, tsd = blq
                conn.execute(
                    """INSERT INTO horarios_bloques
                       (horario_id, bloque, hora_entrada, hora_salida, cruza_medianoche,
                        tolerancia_entrada_antes, tolerancia_entrada_despues, tolerancia_tarde,
                        tolerancia_salida_antes, tolerancia_salida_despues)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (hid, num, he, hs, cm, tea, ted, tt, tsa, tsd),
                )

            print(f"  + [{h['tipo']:7}] {h['nombre']}")
            insertados += 1

    print(f"\nListo — Insertados: {insertados} | Omitidos: {omitidos}")


if __name__ == "__main__":
    main()
