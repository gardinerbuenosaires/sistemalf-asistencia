from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from datetime import date, timedelta
from db.database import db_session

router = APIRouter(prefix="/api/planificacion", tags=["planificacion"])


class PlanDiaIn(BaseModel):
    empleado_id: int
    fecha: str
    horario_id: Optional[int] = None
    es_franco: bool = False
    observacion: Optional[str] = None


def _lunes(fecha_str: str) -> date:
    d = date.fromisoformat(fecha_str)
    return d - timedelta(days=d.weekday())


@router.get("/semana")
def get_semana(fecha: str):
    """
    Devuelve la planificación de la semana que contiene la fecha dada.
    Incluye todos los empleados activos con sus asignaciones día a día.
    """
    lunes = _lunes(fecha)
    dias = [str(lunes + timedelta(days=i)) for i in range(7)]

    with db_session() as conn:
        empleados = conn.execute(
            "SELECT id, nombre, apellido, cargo FROM empleados WHERE activo = 1 ORDER BY cargo, apellido, nombre"
        ).fetchall()

        plan_rows = conn.execute(
            """
            SELECT p.*, h.nombre as horario_nombre, h.tipo as horario_tipo
            FROM planificacion p
            LEFT JOIN horarios h ON h.id = p.horario_id
            WHERE p.fecha >= ? AND p.fecha <= ?
            """,
            (dias[0], dias[6]),
        ).fetchall()

        horarios = conn.execute(
            "SELECT id, nombre, tipo FROM horarios WHERE activo = 1 ORDER BY nombre"
        ).fetchall()

    # Indexar plan por empleado_id + fecha
    plan_idx = {}
    for r in plan_rows:
        plan_idx[(r["empleado_id"], r["fecha"])] = dict(r)

    result = []
    for e in empleados:
        dias_plan = {}
        for d in dias:
            p = plan_idx.get((e["id"], d))
            dias_plan[d] = p if p else None
        result.append({
            "empleado": dict(e),
            "dias": dias_plan,
        })

    return {
        "lunes": str(lunes),
        "dias": dias,
        "horarios": [dict(h) for h in horarios],
        "empleados": result,
    }


@router.post("", status_code=200)
def set_dia(data: PlanDiaIn):
    """
    Crea o actualiza la planificación de un empleado en una fecha.
    Si horario_id es None y es_franco es False, elimina la asignación.
    """
    if data.horario_id is None and not data.es_franco:
        # Borrar asignación
        with db_session() as conn:
            conn.execute(
                "DELETE FROM planificacion WHERE empleado_id = ? AND fecha = ?",
                (data.empleado_id, data.fecha),
            )
        return {"ok": True, "accion": "eliminado"}

    with db_session() as conn:
        conn.execute(
            """
            INSERT INTO planificacion (empleado_id, fecha, horario_id, es_franco, observacion)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(empleado_id, fecha) DO UPDATE SET
                horario_id    = excluded.horario_id,
                es_franco     = excluded.es_franco,
                observacion   = excluded.observacion,
                modificado_en = datetime('now','localtime')
            """,
            (data.empleado_id, data.fecha, data.horario_id,
             int(data.es_franco), data.observacion),
        )
        # Devolver la fila actualizada con nombre de horario
        row = conn.execute(
            """
            SELECT p.*, h.nombre as horario_nombre, h.tipo as horario_tipo
            FROM planificacion p
            LEFT JOIN horarios h ON h.id = p.horario_id
            WHERE p.empleado_id = ? AND p.fecha = ?
            """,
            (data.empleado_id, data.fecha),
        ).fetchone()
    return dict(row)


@router.delete("/{empleado_id}/{fecha}")
def delete_dia(empleado_id: int, fecha: str):
    with db_session() as conn:
        conn.execute(
            "DELETE FROM planificacion WHERE empleado_id = ? AND fecha = ?",
            (empleado_id, fecha),
        )
    return {"ok": True}
