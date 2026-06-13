"""
Regenera planificacion auto-generada para los cocineros de Gardiner
a partir de su asignacion de calendario activa.

Solo inserta dias que no tienen planificacion existente.
"""
import sqlite3
import os
from datetime import date, timedelta

DB_PATH = os.environ.get("DB_PATH", "data/fichajes.db")
SEMANAS = int(os.environ.get("SEMANAS", "8"))  # cuantas semanas hacia adelante generar

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

hoy = date.today()
hasta = hoy + timedelta(weeks=SEMANAS)

feriados = {
    r["fecha"] for r in conn.execute(
        "SELECT fecha FROM feriados WHERE fecha >= ? AND fecha <= ?",
        (str(hoy), str(hasta))
    ).fetchall()
}

# Empleados activos de Cocineros (departamento_id=1)
empleados = conn.execute("""
    SELECT e.id, e.nombre, e.apellido
    FROM empleados e JOIN cargos c ON c.id = e.cargo_id
    WHERE c.departamento_id = 1 AND e.activo = 1
""").fetchall()

total_insertados = 0

for emp in empleados:
    eid = emp["id"]

    asig = conn.execute("""
        SELECT a.calendario_id, a.franco_rotativo, a.franco_dia_semana
        FROM asignaciones a
        WHERE a.empleado_id = ?
          AND a.fecha_desde <= ?
          AND (a.fecha_hasta IS NULL OR a.fecha_hasta >= ?)
        ORDER BY a.fecha_desde DESC LIMIT 1
    """, (eid, str(hoy), str(hoy))).fetchone()

    if not asig:
        print(f"  {emp['apellido']} {emp['nombre']}: sin asignacion activa, se omite")
        continue

    cal_dias = {
        r["dia_semana"]: {"horario_id": r["horario_id"], "es_franco": r["es_franco"]}
        for r in conn.execute(
            "SELECT dia_semana, horario_id, es_franco FROM calendarios_dias WHERE calendario_id = ?",
            (asig["calendario_id"],)
        ).fetchall()
    }

    # Fechas que ya tienen planificacion (no tocar)
    existentes = {
        r["fecha"] for r in conn.execute(
            "SELECT fecha FROM planificacion WHERE empleado_id = ? AND fecha >= ?",
            (eid, str(hoy))
        ).fetchall()
    }

    insertados = 0
    cur = hoy
    while cur <= hasta:
        fecha_str = str(cur)
        if fecha_str not in existentes:
            wd = cur.weekday()
            es_feriado = fecha_str in feriados

            if asig["franco_rotativo"] and asig["franco_dia_semana"] is not None and wd == asig["franco_dia_semana"]:
                es_franco, horario_id = 1, None
            else:
                cal_dia = cal_dias.get(wd, {})
                es_franco = int(cal_dia.get("es_franco", 0))
                horario_id = None if es_franco else cal_dia.get("horario_id")
                if not es_franco and not horario_id:
                    es_franco = 1

            conn.execute(
                "INSERT INTO planificacion (empleado_id, fecha, horario_id, es_franco, auto_generado) VALUES (?,?,?,?,1)",
                (eid, fecha_str, horario_id, es_franco)
            )
            insertados += 1

        cur += timedelta(days=1)

    print(f"  {emp['apellido']} {emp['nombre']}: {insertados} dias insertados")
    total_insertados += insertados

conn.commit()
conn.close()
print(f"\nTotal: {total_insertados} entradas generadas hasta {hasta}")
