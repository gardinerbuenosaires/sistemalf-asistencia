"""
Carga los horarios reales extraídos de ZKTime Enterprise.
Borra cualquier horario existente sin asignaciones antes de importar.
Faltan en las capturas: 0010, 0019, 0020 (DIA), 0021 (NOCHE) — cargar manualmente.
"""
from db.database import init_db, db_session

HORARIOS = [
    # 0001
    {"nombre": "09:00 a 17:30", "tipo": "simple", "bloques": [
        {"bloque": 1, "hora_entrada": "09:00", "hora_salida": "17:30", "cruza_medianoche": False,
         "te_antes": 120, "te_despues": 10, "tard": 10, "ts_antes": 30, "ts_despues": 60},
    ]},
    # 0002
    {"nombre": "08:00 a 16:30", "tipo": "simple", "bloques": [
        {"bloque": 1, "hora_entrada": "08:00", "hora_salida": "16:30", "cruza_medianoche": False,
         "te_antes": 0, "te_despues": 0, "tard": 10, "ts_antes": 30, "ts_despues": 30},
    ]},
    # 0003 - Cortado (B2 salida estimada 00:00, cruza medianoche)
    {"nombre": "Cortado", "tipo": "cortado", "bloques": [
        {"bloque": 1, "hora_entrada": "12:00", "hora_salida": "16:00", "cruza_medianoche": False,
         "te_antes": 15, "te_despues": 10, "tard": 10, "ts_antes": 0, "ts_despues": 0},
        {"bloque": 2, "hora_entrada": "20:00", "hora_salida": "00:00", "cruza_medianoche": True,
         "te_antes": 15, "te_despues": 10, "tard": 10, "ts_antes": 30, "ts_despues": 60},
    ]},
    # 0004
    {"nombre": "07:00 a 15:30", "tipo": "simple", "bloques": [
        {"bloque": 1, "hora_entrada": "07:00", "hora_salida": "15:30", "cruza_medianoche": False,
         "te_antes": 60, "te_despues": 10, "tard": 10, "ts_antes": 0, "ts_despues": 30},
    ]},
    # 0005
    {"nombre": "06:00 a 14:30", "tipo": "simple", "bloques": [
        {"bloque": 1, "hora_entrada": "06:00", "hora_salida": "14:30", "cruza_medianoche": False,
         "te_antes": 15, "te_despues": 10, "tard": 10, "ts_antes": 30, "ts_despues": 30},
    ]},
    # 0006
    {"nombre": "17:30 a 01:30", "tipo": "simple", "bloques": [
        {"bloque": 1, "hora_entrada": "17:30", "hora_salida": "01:30", "cruza_medianoche": True,
         "te_antes": 60, "te_despues": 15, "tard": 10, "ts_antes": 30, "ts_despues": 120},
    ]},
    # 0007
    {"nombre": "11:30 a 16:30", "tipo": "simple", "bloques": [
        {"bloque": 1, "hora_entrada": "11:30", "hora_salida": "16:30", "cruza_medianoche": False,
         "te_antes": 0, "te_despues": 10, "tard": 10, "ts_antes": 0, "ts_despues": 0},
    ]},
    # 0008
    {"nombre": "20:00 a 02:00", "tipo": "simple", "bloques": [
        {"bloque": 1, "hora_entrada": "20:00", "hora_salida": "02:00", "cruza_medianoche": True,
         "te_antes": 15, "te_despues": 0, "tard": 10, "ts_antes": 30, "ts_despues": 0},
    ]},
    # 0009
    {"nombre": "12:00 a 20:30", "tipo": "simple", "bloques": [
        {"bloque": 1, "hora_entrada": "12:45", "hora_salida": "20:30", "cruza_medianoche": False,
         "te_antes": 0, "te_despues": 25, "tard": 10, "ts_antes": 0, "ts_despues": 0},
    ]},
    # 0011
    {"nombre": "16:30 a 01:00", "tipo": "simple", "bloques": [
        {"bloque": 1, "hora_entrada": "16:30", "hora_salida": "01:00", "cruza_medianoche": True,
         "te_antes": 0, "te_despues": 10, "tard": 10, "ts_antes": 0, "ts_despues": 60},
    ]},
    # 0012
    {"nombre": "08:30 a 17:00", "tipo": "simple", "bloques": [
        {"bloque": 1, "hora_entrada": "08:30", "hora_salida": "17:00", "cruza_medianoche": False,
         "te_antes": 30, "te_despues": 0, "tard": 10, "ts_antes": 0, "ts_despues": 60},
    ]},
    # 0013
    {"nombre": "05:00 a 13:30", "tipo": "simple", "bloques": [
        {"bloque": 1, "hora_entrada": "05:00", "hora_salida": "13:30", "cruza_medianoche": False,
         "te_antes": 60, "te_despues": 10, "tard": 10, "ts_antes": 0, "ts_despues": 30},
    ]},
    # 0014
    {"nombre": "09:30 a 18:00", "tipo": "simple", "bloques": [
        {"bloque": 1, "hora_entrada": "09:30", "hora_salida": "18:00", "cruza_medianoche": False,
         "te_antes": 15, "te_despues": 0, "tard": 10, "ts_antes": 0, "ts_despues": 0},
    ]},
    # 0015
    {"nombre": "18:00 a 01:30", "tipo": "simple", "bloques": [
        {"bloque": 1, "hora_entrada": "18:00", "hora_salida": "02:00", "cruza_medianoche": True,
         "te_antes": 0, "te_despues": 10, "tard": 10, "ts_antes": 30, "ts_despues": 90},
    ]},
    # 0016 - Doble Turno (09:00 a ~01:00, cruza medianoche)
    {"nombre": "Doble Turno", "tipo": "simple", "bloques": [
        {"bloque": 1, "hora_entrada": "09:00", "hora_salida": "01:00", "cruza_medianoche": True,
         "te_antes": 0, "te_despues": 10, "tard": 10, "ts_antes": 30, "ts_despues": 180},
    ]},
    # 0017
    {"nombre": "12 a 16:30", "tipo": "simple", "bloques": [
        {"bloque": 1, "hora_entrada": "11:45", "hora_salida": "16:30", "cruza_medianoche": False,
         "te_antes": 15, "te_despues": 0, "tard": 10, "ts_antes": 0, "ts_despues": 0},
    ]},
    # 0018
    {"nombre": "07:30 a 15:30", "tipo": "simple", "bloques": [
        {"bloque": 1, "hora_entrada": "07:30", "hora_salida": "15:30", "cruza_medianoche": False,
         "te_antes": 60, "te_despues": 30, "tard": 10, "ts_antes": 0, "ts_despues": 30},
    ]},
    # 0022 - LUNA
    {"nombre": "LUNA", "tipo": "simple", "bloques": [
        {"bloque": 1, "hora_entrada": "10:30", "hora_salida": "18:30", "cruza_medianoche": False,
         "te_antes": 0, "te_despues": 10, "tard": 10, "ts_antes": 0, "ts_despues": 0},
    ]},
    # 0023
    {"nombre": "00:00 a 08:00", "tipo": "simple", "bloques": [
        {"bloque": 1, "hora_entrada": "00:00", "hora_salida": "08:00", "cruza_medianoche": False,
         "te_antes": 0, "te_despues": 0, "tard": 10, "ts_antes": 0, "ts_despues": 0},
    ]},
    # 0024
    {"nombre": "06:00 a 14:00", "tipo": "simple", "bloques": [
        {"bloque": 1, "hora_entrada": "06:00", "hora_salida": "14:00", "cruza_medianoche": False,
         "te_antes": 0, "te_despues": 0, "tard": 10, "ts_antes": 0, "ts_despues": 0},
    ]},
    # 0025
    {"nombre": "15:30 a 23:00", "tipo": "simple", "bloques": [
        {"bloque": 1, "hora_entrada": "15:30", "hora_salida": "23:00", "cruza_medianoche": False,
         "te_antes": 0, "te_despues": 10, "tard": 10, "ts_antes": 0, "ts_despues": 10},
    ]},
    # 0026
    {"nombre": "16:00 a 00:00", "tipo": "simple", "bloques": [
        {"bloque": 1, "hora_entrada": "16:00", "hora_salida": "00:00", "cruza_medianoche": True,
         "te_antes": 0, "te_despues": 0, "tard": 10, "ts_antes": 0, "ts_despues": 0},
    ]},
]

def main():
    init_db()
    with db_session() as conn:
        # Borrar horarios sin asignaciones
        conn.execute("""
            DELETE FROM horarios_bloques WHERE horario_id IN (
                SELECT id FROM horarios WHERE id NOT IN (SELECT horario_id FROM asignaciones)
            )
        """)
        conn.execute("""
            DELETE FROM horarios WHERE id NOT IN (SELECT horario_id FROM asignaciones)
        """)

        for h in HORARIOS:
            cur = conn.execute(
                "INSERT INTO horarios (nombre, tipo) VALUES (?, ?)",
                (h["nombre"], h["tipo"])
            )
            hid = cur.lastrowid
            for b in h["bloques"]:
                conn.execute("""
                    INSERT INTO horarios_bloques
                        (horario_id, bloque, hora_entrada, hora_salida, cruza_medianoche,
                         tolerancia_entrada_antes, tolerancia_entrada_despues, tolerancia_tarde,
                         tolerancia_salida_antes, tolerancia_salida_despues)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (hid, b["bloque"], b["hora_entrada"], b["hora_salida"],
                      int(b["cruza_medianoche"]), b["te_antes"], b["te_despues"],
                      b["tard"], b["ts_antes"], b["ts_despues"]))
            print(f"  + {h['nombre']} ({h['tipo']})")

    print(f"\nImportados: {len(HORARIOS)} horarios")
    print("\nFaltan (no habia captura): 0010, 0019, 0020 DIA, 0021 NOCHE")
    print("Verificar manualmente: 0003 Cortado (B2 salida), 0016 Doble Turno")

if __name__ == "__main__":
    main()
