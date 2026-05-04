from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from datetime import date, timedelta
from db.database import db_session
from auth.core import require_permiso

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
def get_semana(fecha: str, _user=Depends(require_permiso("planificacion", "ver"))):
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
            SELECT p.*, h.nombre as horario_nombre, h.tipo as horario_tipo,
                   h.turno_id, t.nombre as turno_nombre
            FROM planificacion p
            LEFT JOIN horarios h ON h.id = p.horario_id
            LEFT JOIN turnos t ON t.id = h.turno_id
            WHERE p.fecha >= ? AND p.fecha <= ?
            """,
            (dias[0], dias[6]),
        ).fetchall()

        horarios = conn.execute(
            """SELECT h.id, h.nombre, h.tipo, h.turno_id, t.nombre as turno_nombre
               FROM horarios h LEFT JOIN turnos t ON t.id = h.turno_id
               WHERE h.activo = 1 ORDER BY h.nombre"""
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
def set_dia(data: PlanDiaIn, _user=Depends(require_permiso("planificacion", "editar"))):
    """
    Crea o actualiza la planificación de un empleado en una fecha.
    Si horario_id es None y es_franco es False, elimina la asignación.
    """
    if data.horario_id is None and not data.es_franco:
        # Borrar asignación y resultado asociado (si no fue corregido manualmente)
        with db_session() as conn:
            conn.execute(
                "DELETE FROM planificacion WHERE empleado_id = ? AND fecha = ?",
                (data.empleado_id, data.fecha),
            )
            conn.execute(
                "DELETE FROM resultados_dia WHERE empleado_id=? AND fecha=? AND corregido_manualmente=0",
                (data.empleado_id, data.fecha),
            )
        return {"ok": True, "accion": "eliminado"}

    with db_session() as conn:
        conn.execute(
            """
            INSERT INTO planificacion (empleado_id, fecha, horario_id, es_franco, observacion, auto_generado)
            VALUES (?, ?, ?, ?, ?, 0)
            ON CONFLICT(empleado_id, fecha) DO UPDATE SET
                horario_id     = excluded.horario_id,
                es_franco      = excluded.es_franco,
                observacion    = excluded.observacion,
                auto_generado  = 0,
                modificado_en  = datetime('now','localtime')
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


@router.patch("/ft-horario")
def asignar_horario_ft(body: dict, _user=Depends(require_permiso("planificacion", "editar"))):
    """
    Asigna un horario a un día planificado como franco pero con fichajes (FT pendiente).
    Actualiza planificacion y re-evalúa ese empleado/fecha para calcular tardanza.
    """
    from fastapi import HTTPException
    from sync.evaluador import _clasificar_fichajes, _evaluar_bloque
    from datetime import date, timedelta

    empleado_id = body.get("empleado_id")
    fecha_str   = body.get("fecha")
    horario_id  = body.get("horario_id")
    if not all([empleado_id, fecha_str, horario_id]):
        raise HTTPException(400, "Requeridos: empleado_id, fecha, horario_id")

    fecha     = date.fromisoformat(fecha_str)
    fecha_sig = str(fecha + timedelta(days=1))

    with db_session() as conn:
        plan = conn.execute(
            "SELECT id FROM planificacion WHERE empleado_id=? AND fecha=? AND es_franco=1",
            (empleado_id, fecha_str)
        ).fetchone()
        if not plan:
            raise HTTPException(404, "No existe planificación franco para ese empleado y fecha")

        bloques = [dict(b) for b in conn.execute(
            "SELECT * FROM horarios_bloques WHERE horario_id=? ORDER BY bloque",
            (horario_id,)
        ).fetchall()]
        if not bloques:
            raise HTTPException(400, "El horario no tiene bloques definidos")

        conn.execute(
            "UPDATE planificacion SET horario_id=?, modificado_en=datetime('now','localtime') WHERE empleado_id=? AND fecha=?",
            (horario_id, empleado_id, fecha_str)
        )

        fichajes = [dict(f) for f in conn.execute(
            "SELECT * FROM fichajes WHERE empleado_id=? AND (date(timestamp)=? OR date(timestamp)=?) ORDER BY timestamp",
            (empleado_id, fecha_str, fecha_sig)
        ).fetchall()]

        slots         = _clasificar_fichajes(fichajes, bloques, fecha)
        b1_entrada_id = (slots.get("b1_entrada") or {}).get("id")
        b1_salida_id  = (slots.get("b1_salida")  or {}).get("id")
        b1r           = _evaluar_bloque(bloques[0], fecha, slots.get("b1_entrada"), slots.get("b1_salida"))
        b2r           = None
        b2_entrada_id = b2_salida_id = None
        if len(bloques) > 1:
            b2_entrada_id = (slots.get("b2_entrada") or {}).get("id")
            b2_salida_id  = (slots.get("b2_salida")  or {}).get("id")
            b2r = _evaluar_bloque(bloques[1], fecha, slots.get("b2_entrada"), slots.get("b2_salida"))

        conn.execute(
            """INSERT INTO resultados_dia
               (empleado_id, fecha, horario_id, es_franco, estado,
                b1_entrada, b1_salida, b1_minutos_tarde, b1_salida_anticipada, b1_ausente, b1_sin_salida,
                b2_entrada, b2_salida, b2_minutos_tarde, b2_salida_anticipada, b2_ausente, b2_sin_salida,
                b1_entrada_id, b1_salida_id, b2_entrada_id, b2_salida_id,
                corregido_manualmente, corregido_por, corregido_en, procesado_en)
               VALUES (?,?,?,1,'ft', ?,?,?,?,?,?, ?,?,?,?,?,?, ?,?,?,?, 0,NULL,NULL, datetime('now','localtime'))
               ON CONFLICT(empleado_id, fecha) DO UPDATE SET
               horario_id=excluded.horario_id, estado='ft',
               b1_entrada=excluded.b1_entrada, b1_salida=excluded.b1_salida,
               b1_minutos_tarde=excluded.b1_minutos_tarde,
               b1_salida_anticipada=excluded.b1_salida_anticipada, b1_ausente=excluded.b1_ausente,
               b1_sin_salida=excluded.b1_sin_salida,
               b2_entrada=excluded.b2_entrada, b2_salida=excluded.b2_salida,
               b2_minutos_tarde=excluded.b2_minutos_tarde,
               b2_salida_anticipada=excluded.b2_salida_anticipada, b2_ausente=excluded.b2_ausente,
               b2_sin_salida=excluded.b2_sin_salida,
               b1_entrada_id=excluded.b1_entrada_id, b1_salida_id=excluded.b1_salida_id,
               b2_entrada_id=excluded.b2_entrada_id, b2_salida_id=excluded.b2_salida_id,
               procesado_en=datetime('now','localtime')""",
            (empleado_id, fecha_str, horario_id,
             b1r.get("entrada"), b1r.get("salida"), b1r.get("minutos_tarde"),
             int(b1r.get("salida_anticipada", False)), int(b1r.get("ausente", False)), int(b1r.get("sin_salida", False)),
             (b2r or {}).get("entrada"), (b2r or {}).get("salida"), (b2r or {}).get("minutos_tarde"),
             int((b2r or {}).get("salida_anticipada", False)), int((b2r or {}).get("ausente", False)), int((b2r or {}).get("sin_salida", False)),
             b1_entrada_id, b1_salida_id, b2_entrada_id, b2_salida_id)
        )
    return {"ok": True}


@router.delete("/{empleado_id}/{fecha}")
def delete_dia(empleado_id: int, fecha: str, _user=Depends(require_permiso("planificacion", "editar"))):
    with db_session() as conn:
        conn.execute(
            "DELETE FROM planificacion WHERE empleado_id = ? AND fecha = ?",
            (empleado_id, fecha),
        )
        conn.execute(
            "DELETE FROM resultados_dia WHERE empleado_id=? AND fecha=? AND corregido_manualmente=0",
            (empleado_id, fecha),
        )
    return {"ok": True}
