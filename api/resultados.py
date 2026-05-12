from datetime import date, timedelta

from fastapi import APIRouter, Body, Depends, HTTPException

from auth.core import require_permiso
from db.database import db_session

router = APIRouter(prefix="/api/resultados", tags=["resultados"])


ESTADO_LABELS: dict[str, str] = {
    "ok":                       "OK",
    "tarde":                    "Tarde",
    "ausente":                  "Ausente",
    "franco":                   "Franco",
    "salida_anticipada":        "Salida anticipada",
    "tarde_y_salida_anticipada":"Tarde + s.anticipada",
    "sin_salida":               "Sin salida",
    "tarde_y_sin_salida":       "Tarde + sin salida",
    "b1_ausente":               "Falta bloque 1",
    "b2_ausente":               "Falta bloque 2",
    "sin_horario":              "Sin horario",
    "nf":                       "No fichó",
    "ft":                       "Franco trabajado",
    "duda":                     "Presencia dudosa",
}


@router.get("/estados")
def estados(_user=Depends(require_permiso("resultados", "ver"))):
    """Mapa de estado → etiqueta legible. Usado por el frontend para renderizar badges."""
    return ESTADO_LABELS


@router.get("/bloques-pendientes")
def bloques_pendientes(fecha: str | None = None, _user=Depends(require_permiso("resultados", "ver"))):
    """
    Bloques cuya hora de salida aún no llegó hoy.
    El frontend lo usa para advertir al operador antes de procesar manualmente.
    """
    from sync.evaluador import horarios_sin_cerrar
    return horarios_sin_cerrar(fecha)


@router.post("/procesar")
def procesar(
    fecha: str | None = None,
    fecha_desde: str | None = None,
    fecha_hasta: str | None = None,
    respetar_correcciones: bool = True,
    empleado_id: int | None = None,
    _user=Depends(require_permiso("resultados", "procesar")),
):
    """
    Evalúa fichajes contra planificación y guarda resultados_dia.
    Acepta: ?fecha=2026-04-21  o  ?fecha_desde=...&fecha_hasta=...
    empleado_id opcional para re-evaluar solo un empleado.
    respetar_correcciones=false para pisar correcciones manuales al re-procesar.
    """
    from sync.evaluador import evaluar_fecha, evaluar_rango
    from api.periodos_cerrados import check_periodo_abierto, check_rango_abierto
    if fecha:
        with db_session() as conn:
            check_periodo_abierto(conn, fecha)
        return evaluar_fecha(fecha, respetar_correcciones=respetar_correcciones,
                             solo_empleado_id=empleado_id)
    if fecha_desde and fecha_hasta:
        with db_session() as conn:
            check_rango_abierto(conn, fecha_desde, fecha_hasta)
        return evaluar_rango(fecha_desde, fecha_hasta, respetar_correcciones=respetar_correcciones)
    raise HTTPException(400, "Pasar fecha o fecha_desde+fecha_hasta")


@router.get("/dia")
def resultados_dia(fecha: str, _user=Depends(require_permiso("resultados", "ver"))):
    """Resultados de todos los empleados en una fecha."""
    with db_session() as conn:
        rows = conn.execute(
            """SELECT r.*, e.nombre, e.apellido, c.nombre AS cargo,
                      h.nombre as horario_nombre, h.tipo as horario_tipo,
                      u.nombre as corregido_por_nombre
               FROM resultados_dia r
               JOIN empleados e ON e.id = r.empleado_id
               LEFT JOIN cargos c ON c.id = e.cargo_id
               LEFT JOIN horarios h ON h.id = r.horario_id
               LEFT JOIN usuarios u ON u.id = r.corregido_por
               WHERE r.fecha = ?
               ORDER BY e.apellido, e.nombre""",
            (fecha,)
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/fichajes-disponibles")
def fichajes_disponibles(empleado_id: int, fecha: str, _user=Depends(require_permiso("resultados", "ver"))):
    """
    Fichajes del empleado en esa fecha (y siguiente para horarios que cruzan medianoche).
    Usado por la UI para mostrar opciones al corregir un slot.
    """
    fecha_sig = str(date.fromisoformat(fecha) + timedelta(days=1))
    with db_session() as conn:
        rows = conn.execute(
            """SELECT id, timestamp, punch_raw, es_manual
               FROM fichajes
               WHERE empleado_id=? AND date(timestamp) IN (?,?)
               ORDER BY timestamp""",
            (empleado_id, fecha, fecha_sig)
        ).fetchall()
    return [dict(r) for r in rows]


@router.patch("/corregir")
def corregir_fichaje(
    empleado_id: int = Body(...),
    fecha: str = Body(...),
    slot: str = Body(...),
    fichaje_id: int | None = Body(default=None),
    user=Depends(require_permiso("resultados", "corregir")),
):
    """
    Corrige manualmente qué fichaje se usa para un slot de un resultado.
    fichaje_id=null limpia el slot (lo deja sin fichaje asignado).
    Registra el usuario que realizó la corrección.
    """
    SLOTS_VALIDOS = ("b1_entrada", "b1_salida", "b2_entrada", "b2_salida")
    if slot not in SLOTS_VALIDOS:
        raise HTTPException(400, "slot inválido")

    from sync.evaluador import recalcular_resultado
    from api.periodos_cerrados import check_periodo_abierto

    with db_session() as conn:
        check_periodo_abierto(conn, fecha)
        r = conn.execute(
            "SELECT id FROM resultados_dia WHERE empleado_id=? AND fecha=?",
            (empleado_id, fecha)
        ).fetchone()
        if not r:
            raise HTTPException(404, "No hay resultado para ese empleado/fecha")

        nuevo_ts = None
        if fichaje_id is not None:
            fecha_sig = str(date.fromisoformat(fecha) + timedelta(days=1))
            f = conn.execute(
                "SELECT timestamp FROM fichajes WHERE id=? AND empleado_id=? AND date(timestamp) IN (?,?)",
                (fichaje_id, empleado_id, fecha, fecha_sig)
            ).fetchone()
            if not f:
                raise HTTPException(400, "El fichaje no corresponde al empleado o fecha")
            nuevo_ts = f["timestamp"]

        col_ts = slot
        col_id = f"{slot}_id"
        conn.execute(
            f"""UPDATE resultados_dia
                SET {col_ts}=?, {col_id}=?,
                    corregido_manualmente=1,
                    corregido_por=?,
                    corregido_en=datetime('now','localtime')
                WHERE empleado_id=? AND fecha=?""",
            (nuevo_ts, fichaje_id, int(user["sub"]), empleado_id, fecha)
        )

    recalcular_resultado(empleado_id, fecha)
    return {"ok": True}


@router.get("/semana")
def resultados_semana(fecha: str, _user=Depends(require_permiso("resultados", "ver"))):
    """Resultados de la semana que contiene la fecha dada."""
    from datetime import date, timedelta
    d = date.fromisoformat(fecha)
    lunes = d - timedelta(days=d.weekday())
    domingo = lunes + timedelta(days=6)
    with db_session() as conn:
        rows = conn.execute(
            """SELECT r.*, e.nombre, e.apellido, c.nombre AS cargo,
                      h.nombre as horario_nombre
               FROM resultados_dia r
               JOIN empleados e ON e.id = r.empleado_id
               LEFT JOIN cargos c ON c.id = e.cargo_id
               LEFT JOIN horarios h ON h.id = r.horario_id
               WHERE r.fecha BETWEEN ? AND ?
               ORDER BY r.fecha, e.apellido, e.nombre""",
            (str(lunes), str(domingo))
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/empleado/{empleado_id}")
def resultados_empleado(empleado_id: int, fecha_desde: str, fecha_hasta: str, _user=Depends(require_permiso("resultados", "ver"))):
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
def resumen_periodo(fecha_desde: str, fecha_hasta: str, _user=Depends(require_permiso("resultados", "ver"))):
    """Resumen por empleado para un rango: días trabajados, ausentes, tardes."""
    with db_session() as conn:
        rows = conn.execute(
            """SELECT
                 e.id, e.apellido, e.nombre, c.nombre AS cargo,
                 COUNT(CASE WHEN r.estado NOT IN ('franco','sin_horario') THEN 1 END) as dias_planificados,
                 COUNT(CASE WHEN r.estado = 'ok' THEN 1 END)      as dias_ok,
                 COUNT(CASE WHEN r.estado = 'franco' THEN 1 END)  as dias_franco,
                 COUNT(CASE WHEN r.estado = 'ausente' THEN 1 END) as dias_ausente,
                 COUNT(CASE WHEN r.estado LIKE '%tarde%' THEN 1 END) as dias_tarde,
                 COUNT(CASE WHEN r.estado LIKE '%anticipada%' THEN 1 END) as dias_salida_ant,
                 SUM(COALESCE(r.b1_minutos_tarde,0) + COALESCE(r.b2_minutos_tarde,0)) as total_minutos_tarde
               FROM empleados e
               LEFT JOIN cargos c ON c.id = e.cargo_id
               LEFT JOIN resultados_dia r ON r.empleado_id = e.id
                 AND r.fecha BETWEEN ? AND ?
               WHERE e.activo = 1
               GROUP BY e.id
               ORDER BY e.apellido, e.nombre""",
            (fecha_desde, fecha_hasta)
        ).fetchall()
    return [dict(r) for r in rows]
