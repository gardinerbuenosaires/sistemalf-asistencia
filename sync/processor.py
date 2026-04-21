"""
Procesa los fichajes sin tipo asignado:
- Alterna entrada/salida por orden cronológico dentro de cada día.
- Detecta y registra inconsistencias.
"""
import logging
from collections import defaultdict
from db.database import db_session

logger = logging.getLogger(__name__)

# Un día con fichajes se considera inconsistente si tiene número impar de marcas
# o si hay solo una marca (entrada sin salida o viceversa al final de jornada).


def process_pending() -> dict:
    """Calcula tipo entrada/salida para fichajes sin tipo y detecta inconsistencias."""
    result = {"procesados": 0, "inconsistencias": 0}

    with db_session() as conn:
        rows = conn.execute(
            """
            SELECT id, user_id, timestamp
            FROM fichajes
            WHERE tipo IS NULL
            ORDER BY user_id, timestamp
            """
        ).fetchall()

    if not rows:
        return result

    # Agrupar por empleado y día
    by_user_day: dict[tuple, list] = defaultdict(list)
    for row in rows:
        date = row["timestamp"][:10]
        by_user_day[(row["user_id"], date)].append(
            {"id": row["id"], "timestamp": row["timestamp"]}
        )

    updates = []      # (tipo, id)
    inconsistencias = []  # (user_id, fecha, motivo)

    for (user_id, date), marks in by_user_day.items():
        marks.sort(key=lambda x: x["timestamp"])

        for i, mark in enumerate(marks):
            tipo = "entrada" if i % 2 == 0 else "salida"
            updates.append((tipo, mark["id"]))

        if len(marks) % 2 != 0:
            inconsistencias.append(
                (user_id, date, f"Número impar de fichajes ({len(marks)})")
            )

    with db_session() as conn:
        conn.executemany(
            "UPDATE fichajes SET tipo = ? WHERE id = ?", updates
        )
        # Evitar inconsistencias duplicadas para el mismo user/fecha/motivo
        for user_id, fecha, motivo in inconsistencias:
            conn.execute(
                """
                INSERT INTO inconsistencias (user_id, fecha, tipo, motivo)
                SELECT ?, ?, 'fichaje_impar', ?
                WHERE NOT EXISTS (
                    SELECT 1 FROM inconsistencias
                    WHERE user_id = ? AND fecha = ? AND motivo = ? AND resuelta = 0
                )
                """,
                (user_id, fecha, motivo, user_id, fecha, motivo),
            )

    result["procesados"] = len(updates)
    result["inconsistencias"] = len(inconsistencias)
    logger.info(
        "Procesados: %d fichajes, %d inconsistencias detectadas",
        result["procesados"],
        result["inconsistencias"],
    )
    return result
