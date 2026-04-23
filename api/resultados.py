from fastapi import APIRouter
from db.database import db_session

router = APIRouter(prefix="/api/resultados", tags=["resultados"])


@router.get("/bloques-pendientes")
def bloques_pendientes(fecha: str | None = None):
    """
    Bloques cuya hora de salida aún no llegó hoy.
    El frontend lo usa para advertir al operador antes de procesar manualmente.
    """
    from sync.evaluador import horarios_sin_cerrar
    return horarios_sin_cerrar(fecha)


@router.post("/procesar")
def procesar(fecha: str | None = None, fecha_desde: str | None = None, fecha_hasta: str | None = None):
    """
    Evalúa fichajes contra planificación y guarda resultados_dia.
    Acepta: ?fecha=2026-04-21  o  ?fecha_desde=...&fecha_hasta=...
    """
    from sync.evaluador import evaluar_fecha, evaluar_rango
    if fecha:
        return evaluar_fecha(fecha)
    if fecha_desde and fecha_hasta:
        return evaluar_rango(fecha_desde, fecha_hasta)
    from fastapi import HTTPException
    raise HTTPException(400, "Pasar fecha o fecha_desde+fecha_hasta")


@router.get("/dia")
def resultados_dia(fecha: str):
    """Resultados de todos los empleados en una fecha."""
    with db_session() as conn:
        rows = conn.execute(
            """SELECT r.*, e.nombre, e.apellido, e.cargo,
                      h.nombre as horario_nombre, h.tipo as horario_tipo
               FROM resultados_dia r
               JOIN empleados e ON e.id = r.empleado_id
               LEFT JOIN horarios h ON h.id = r.horario_id
               WHERE r.fecha = ?
               ORDER BY e.apellido, e.nombre""",
            (fecha,)
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/semana")
def resultados_semana(fecha: str):
    """Resultados de la semana que contiene la fecha dada."""
    from datetime import date, timedelta
    d = date.fromisoformat(fecha)
    lunes = d - timedelta(days=d.weekday())
    domingo = lunes + timedelta(days=6)
    with db_session() as conn:
        rows = conn.execute(
            """SELECT r.*, e.nombre, e.apellido, e.cargo,
                      h.nombre as horario_nombre
               FROM resultados_dia r
               JOIN empleados e ON e.id = r.empleado_id
               LEFT JOIN horarios h ON h.id = r.horario_id
               WHERE r.fecha BETWEEN ? AND ?
               ORDER BY r.fecha, e.apellido, e.nombre""",
            (str(lunes), str(domingo))
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/empleado/{empleado_id}")
def resultados_empleado(empleado_id: int, fecha_desde: str, fecha_hasta: str):
    """Historial de resultados de un empleado en un rango de fechas."""
    with db_session() as conn:
        rows = conn.execute(
            """SELECT r.*, h.nombre as horario_nombre
               FROM resultados_dia r
               LEFT JOIN horarios h ON h.id = r.horario_id
               WHERE r.empleado_id = ? AND r.fecha BETWEEN ? AND ?
               ORDER BY r.fecha""",
            (empleado_id, fecha_desde, fecha_hasta)
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/resumen")
def resumen_periodo(fecha_desde: str, fecha_hasta: str):
    """Resumen por empleado para un rango: días trabajados, ausentes, tardes."""
    with db_session() as conn:
        rows = conn.execute(
            """SELECT
                 e.id, e.apellido, e.nombre, e.cargo,
                 COUNT(CASE WHEN r.estado NOT IN ('franco','sin_horario') THEN 1 END) as dias_planificados,
                 COUNT(CASE WHEN r.estado = 'ok' THEN 1 END)      as dias_ok,
                 COUNT(CASE WHEN r.estado = 'franco' THEN 1 END)  as dias_franco,
                 COUNT(CASE WHEN r.estado = 'ausente' THEN 1 END) as dias_ausente,
                 COUNT(CASE WHEN r.estado LIKE '%tarde%' THEN 1 END) as dias_tarde,
                 COUNT(CASE WHEN r.estado LIKE '%anticipada%' THEN 1 END) as dias_salida_ant,
                 SUM(COALESCE(r.b1_minutos_tarde,0) + COALESCE(r.b2_minutos_tarde,0)) as total_minutos_tarde
               FROM empleados e
               LEFT JOIN resultados_dia r ON r.empleado_id = e.id
                 AND r.fecha BETWEEN ? AND ?
               WHERE e.activo = 1
               GROUP BY e.id
               ORDER BY e.apellido, e.nombre""",
            (fecha_desde, fecha_hasta)
        ).fetchall()
    return [dict(r) for r in rows]
