from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from datetime import date, timedelta
from db.database import db_session
from auth.core import require_permiso, get_current_user

router = APIRouter(prefix="/api/calendarios", tags=["calendarios"])

DIAS = ["lunes","martes","miercoles","jueves","viernes","sabado","domingo"]


class DiaIn(BaseModel):
    dia_semana: int
    horario_id: Optional[int] = None   # None = usa horario_base del empleado, o franco si es_franco
    es_franco: bool = False            # True = día libre


class CalendarioIn(BaseModel):
    nombre: str
    dias: list[DiaIn]  # 7 entradas
    franco_en_feriado: bool = False


class AsignacionIn(BaseModel):
    empleado_ids:     list[int]
    calendario_id:    int
    fecha_desde:      str
    fecha_hasta:      Optional[str] = None
    franco_rotativo:  bool = False
    franco_dia_semana: Optional[int] = None  # 0=lunes … 6=domingo


DIAS_SEMANA_NOMBRES = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]


def _resolver_dia(weekday: int, cal_dia: dict, franco_rotativo: bool,
                  franco_dia_semana, es_feriado: bool, franco_en_feriado: bool):
    """
    Devuelve (es_franco, horario_id) para un día dado.
    Prioridad: feriado > franco rotativo de asignación > calendario.
    """
    if franco_en_feriado and es_feriado:
        return 1, None
    if franco_rotativo and franco_dia_semana is not None and weekday == franco_dia_semana:
        return 1, None
    es_franco = int(cal_dia.get("es_franco", 0))
    horario_id = None if es_franco else cal_dia.get("horario_id")
    if not es_franco and not horario_id:
        es_franco = 1
    return es_franco, horario_id


def _get_calendario(conn, cid):
    c = conn.execute("SELECT * FROM calendarios WHERE id=?", (cid,)).fetchone()
    if not c:
        raise HTTPException(404, "Calendario no encontrado")
    dias = conn.execute(
        "SELECT cd.*, h.nombre as horario_nombre, h.tipo as horario_tipo "
        "FROM calendarios_dias cd LEFT JOIN horarios h ON h.id=cd.horario_id "
        "WHERE cd.calendario_id=? ORDER BY cd.dia_semana", (cid,)
    ).fetchall()
    asignados = conn.execute(
        "SELECT DISTINCT empleado_id FROM asignaciones "
        "WHERE calendario_id=? AND (fecha_hasta IS NULL OR fecha_hasta > date('now','localtime'))",
        (cid,)
    ).fetchall()
    return {**dict(c), "dias": [dict(d) for d in dias],
            "empleados_asignados_ids": [r["empleado_id"] for r in asignados]}


@router.get("")
def list_calendarios(_user=Depends(require_permiso("calendarios", "ver"))):
    with db_session() as conn:
        rows = conn.execute("SELECT * FROM calendarios WHERE activo=1 ORDER BY nombre").fetchall()
        result = []
        for c in rows:
            dias = conn.execute(
                "SELECT cd.*, h.nombre as horario_nombre, h.tipo as horario_tipo "
                "FROM calendarios_dias cd LEFT JOIN horarios h ON h.id=cd.horario_id "
                "WHERE cd.calendario_id=? ORDER BY cd.dia_semana", (c["id"],)
            ).fetchall()
            # Contar asignaciones activas
            asig = conn.execute(
                "SELECT COUNT(DISTINCT empleado_id) FROM asignaciones WHERE calendario_id=? AND (fecha_hasta IS NULL OR fecha_hasta > date('now','localtime'))",
                (c["id"],)
            ).fetchone()[0]
            result.append({**dict(c), "dias": [dict(d) for d in dias], "empleados_asignados": asig})
    return result


@router.get("/{cid}")
def get_calendario(cid: int, _user=Depends(require_permiso("calendarios", "ver"))):
    with db_session() as conn:
        return _get_calendario(conn, cid)


@router.post("", status_code=201)
def create_calendario(data: CalendarioIn, _user=Depends(require_permiso("calendarios", "editar"))):
    if len(data.dias) != 7:
        raise HTTPException(422, "Se requieren exactamente 7 días")
    with db_session() as conn:
        cur = conn.execute(
            "INSERT INTO calendarios (nombre, franco_en_feriado) VALUES (?,?)",
            (data.nombre.strip(), int(data.franco_en_feriado))
        )
        cid = cur.lastrowid
        for d in data.dias:
            conn.execute(
                "INSERT INTO calendarios_dias (calendario_id, dia_semana, horario_id, es_franco) VALUES (?,?,?,?)",
                (cid, d.dia_semana, d.horario_id, int(d.es_franco))
            )
        return _get_calendario(conn, cid)


@router.put("/{cid}")
def update_calendario(cid: int, data: CalendarioIn, _user=Depends(require_permiso("calendarios", "editar"))):
    if len(data.dias) != 7:
        raise HTTPException(422, "Se requieren exactamente 7 días")
    with db_session() as conn:
        c = conn.execute("SELECT id FROM calendarios WHERE id=?", (cid,)).fetchone()
        if not c:
            raise HTTPException(404, "Calendario no encontrado")
        conn.execute(
            "UPDATE calendarios SET nombre=?, franco_en_feriado=? WHERE id=?",
            (data.nombre.strip(), int(data.franco_en_feriado), cid)
        )
        conn.execute("DELETE FROM calendarios_dias WHERE calendario_id=?", (cid,))
        for d in data.dias:
            conn.execute(
                "INSERT INTO calendarios_dias (calendario_id, dia_semana, horario_id, es_franco) VALUES (?,?,?,?)",
                (cid, d.dia_semana, d.horario_id, int(d.es_franco))
            )
        return _get_calendario(conn, cid)


@router.delete("/{cid}")
def delete_calendario(cid: int, _user=Depends(require_permiso("calendarios", "eliminar"))):
    from datetime import date
    hoy = str(date.today())
    with db_session() as conn:
        activas = conn.execute(
            "SELECT COUNT(*) FROM asignaciones WHERE calendario_id=? "
            "AND (fecha_hasta IS NULL OR fecha_hasta > ?)",
            (cid, hoy)
        ).fetchone()[0]
        if activas:
            raise HTTPException(409, "El calendario está asignado a empleados activos")
        # Elimina asignaciones históricas (ya cerradas); planificación y asistencia no se tocan
        conn.execute("DELETE FROM asignaciones WHERE calendario_id=?", (cid,))
        conn.execute("DELETE FROM calendarios_dias WHERE calendario_id=?", (cid,))
        conn.execute("DELETE FROM calendarios WHERE id=?", (cid,))
    return {"ok": True}


# ── Asignaciones ─────────────────────────────────────────────────────────────

@router.post("/asignar")
def asignar(data: AsignacionIn, _user=Depends(require_permiso("calendarios", "editar"))):
    """
    Asigna un calendario a uno o varios empleados.
    Borra todo lo auto-generado desde fecha_desde en adelante y
    regenera los próximos 30 días. Las entradas manuales no se tocan.
    """
    from datetime import date as dt
    hoy = dt.today()
    d_desde = dt.fromisoformat(data.fecha_desde)
    d_hasta = max(d_desde + timedelta(days=30), hoy + timedelta(days=30))

    with db_session() as conn:
        dias_cal = conn.execute(
            "SELECT dia_semana, horario_id, es_franco FROM calendarios_dias WHERE calendario_id=?",
            (data.calendario_id,)
        ).fetchall()
        cal_map = {r["dia_semana"]: dict(r) for r in dias_cal}

        cal_feriado_flag = conn.execute(
            "SELECT franco_en_feriado FROM calendarios WHERE id=?", (data.calendario_id,)
        ).fetchone()
        franco_en_feriado = cal_feriado_flag["franco_en_feriado"] if cal_feriado_flag else 0

        feriados_rango = {r["fecha"] for r in conn.execute(
            "SELECT fecha FROM feriados WHERE fecha >= ? AND fecha <= ?",
            (data.fecha_desde, str(d_hasta))
        ).fetchall()}

        for eid in data.empleado_ids:
            # Limpiar historial cerrado siempre (independientemente de si hay cambio)
            conn.execute(
                "DELETE FROM asignaciones WHERE empleado_id=? AND fecha_hasta IS NOT NULL",
                (eid,)
            )

            # Saltear solo si ya existe exactamente la misma asignación (mismo calendario,
            # misma fecha_desde, mismo franco_rotativo y franco_dia_semana)
            ya_activo = conn.execute(
                "SELECT id FROM asignaciones "
                "WHERE empleado_id=? AND calendario_id=? AND fecha_desde=? "
                "AND franco_rotativo=? AND COALESCE(franco_dia_semana,-1)=COALESCE(?,-1) "
                "AND (fecha_hasta IS NULL OR fecha_hasta > ?)",
                (eid, data.calendario_id, data.fecha_desde,
                 int(data.franco_rotativo), data.franco_dia_semana, data.fecha_desde)
            ).fetchone()
            if ya_activo:
                continue

            conn.execute(
                "UPDATE asignaciones SET fecha_hasta=? WHERE empleado_id=?",
                (data.fecha_desde, eid)
            )
            conn.execute(
                "INSERT INTO asignaciones "
                "(empleado_id, calendario_id, horario_id, fecha_desde, fecha_hasta, franco_rotativo, franco_dia_semana) "
                "VALUES (?, ?, NULL, ?, ?, ?, ?)",
                (eid, data.calendario_id, data.fecha_desde, data.fecha_hasta,
                 int(data.franco_rotativo), data.franco_dia_semana)
            )
            # Borrar TODO lo auto-generado desde fecha_desde en adelante
            conn.execute(
                "DELETE FROM planificacion WHERE empleado_id=? AND fecha >= ? AND auto_generado=1",
                (eid, data.fecha_desde)
            )
            conn.execute(
                "DELETE FROM resultados_dia WHERE empleado_id=? AND fecha >= ? AND corregido_manualmente=0",
                (eid, data.fecha_desde)
            )
            # Entradas manuales existentes en el rango (no tocar)
            manuales = {r["fecha"] for r in conn.execute(
                "SELECT fecha FROM planificacion WHERE empleado_id=? AND fecha >= ? AND fecha <= ? AND auto_generado=0",
                (eid, data.fecha_desde, str(d_hasta))
            ).fetchall()}
            # Generar día a día los próximos 30 días
            cur = d_desde
            while cur <= d_hasta:
                fecha_str = str(cur)
                if fecha_str not in manuales:
                    es_franco, horario_id = _resolver_dia(
                        cur.weekday(), cal_map.get(cur.weekday(), {}),
                        data.franco_rotativo, data.franco_dia_semana,
                        fecha_str in feriados_rango, bool(franco_en_feriado)
                    )
                    conn.execute(
                        "INSERT INTO planificacion (empleado_id, fecha, horario_id, es_franco, auto_generado) VALUES (?,?,?,?,1)",
                        (eid, fecha_str, horario_id, es_franco)
                    )
                cur += timedelta(days=1)

    # Recalcular resultados para fechas pasadas con fichajes
    from sync.evaluador import evaluar_fecha
    for eid in data.empleado_ids:
        cur = d_desde
        while cur <= hoy:
            evaluar_fecha(str(cur), respetar_correcciones=False, solo_empleado_id=eid)
            cur += timedelta(days=1)

    return {"ok": True, "asignados": len(data.empleado_ids)}


@router.get("/asignaciones/empleado/{empleado_id}")
def asignaciones_empleado(empleado_id: int, _user=Depends(require_permiso("calendarios", "ver"))):
    with db_session() as conn:
        rows = conn.execute(
            "SELECT a.*, c.nombre as calendario_nombre FROM asignaciones a "
            "LEFT JOIN calendarios c ON c.id=a.calendario_id "
            "WHERE a.empleado_id=? ORDER BY a.fecha_desde DESC",
            (empleado_id,)
        ).fetchall()
    return [dict(r) for r in rows]


@router.delete("/asignaciones/{asignacion_id}")
def eliminar_asignacion(asignacion_id: int, _user=Depends(require_permiso("calendarios", "editar"))):
    """
    Elimina una asignación de calendario y borra la planificación futura
    auto-generada del empleado (desde hoy en adelante).
    Las entradas manuales (auto_generado=0) se respetan.
    """
    with db_session() as conn:
        row = conn.execute("SELECT empleado_id FROM asignaciones WHERE id=?", (asignacion_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Asignación no encontrada")
        eid = row["empleado_id"]
        conn.execute("DELETE FROM asignaciones WHERE id=?", (asignacion_id,))
        conn.execute(
            "DELETE FROM planificacion WHERE empleado_id=? AND fecha >= date('now','localtime') AND auto_generado=1",
            (eid,)
        )
        conn.execute(
            "DELETE FROM resultados_dia WHERE empleado_id=? AND fecha >= date('now','localtime') AND corregido_manualmente=0",
            (eid,)
        )
    return {"ok": True}


@router.post("/generar-semana")
def generar_semana(fecha: str, _user=Depends(require_permiso("calendarios", "editar"))):
    """
    Pre-rellena la planificación de la semana con los calendarios asignados.
    Solo completa los días que no tienen planificación manual.
    Devuelve resumen {generados, omitidos}.
    """
    from datetime import date as dt
    d = dt.fromisoformat(fecha)
    lunes = d - timedelta(days=d.weekday())
    dias = [str(lunes + timedelta(days=i)) for i in range(7)]

    generados = 0
    omitidos = 0

    with db_session() as conn:
        # Empleados activos con asignación vigente
        asignaciones = conn.execute(
            """
            SELECT a.empleado_id, a.calendario_id, a.franco_rotativo, a.franco_dia_semana
            FROM asignaciones a
            WHERE a.fecha_desde <= ?
              AND (a.fecha_hasta IS NULL OR a.fecha_hasta > ?)
            ORDER BY a.empleado_id, a.fecha_desde DESC, a.id DESC
            """,
            (dias[6], dias[0])
        ).fetchall()

        # Quedarnos con la asignación más reciente por empleado
        asig_map: dict[int, dict] = {}
        for a in asignaciones:
            if a["empleado_id"] not in asig_map:
                asig_map[a["empleado_id"]] = {
                    "calendario_id":    a["calendario_id"],
                    "franco_rotativo":  bool(a["franco_rotativo"]),
                    "franco_dia_semana": a["franco_dia_semana"],
                }

        # Cargar días de cada calendario (incluye es_franco) y flag feriado
        cal_cache: dict[int, dict] = {}
        cal_feriado: dict[int, int] = {}
        for eid, asig in asig_map.items():
            cid = asig["calendario_id"]
            if cid not in cal_cache:
                dias_cal = conn.execute(
                    "SELECT dia_semana, horario_id, es_franco FROM calendarios_dias WHERE calendario_id=?", (cid,)
                ).fetchall()
                cal_cache[cid] = {r["dia_semana"]: dict(r) for r in dias_cal}
            if cid not in cal_feriado:
                row_c = conn.execute(
                    "SELECT franco_en_feriado FROM calendarios WHERE id=?", (cid,)
                ).fetchone()
                cal_feriado[cid] = row_c["franco_en_feriado"] if row_c else 0

        feriados_semana = {r["fecha"] for r in conn.execute(
            "SELECT fecha FROM feriados WHERE fecha >= ? AND fecha <= ?", (dias[0], dias[6])
        ).fetchall()}

        # Borrar entradas auto-generadas de la semana (respeta las manuales auto_generado=0)
        conn.execute(
            "DELETE FROM planificacion WHERE fecha >= ? AND fecha <= ? AND auto_generado = 1",
            (dias[0], dias[6])
        )

        # Planificación manual que queda (no tocar)
        manuales = set()
        for r in conn.execute(
            "SELECT empleado_id, fecha FROM planificacion WHERE fecha >= ? AND fecha <= ? AND auto_generado = 0",
            (dias[0], dias[6])
        ).fetchall():
            manuales.add((r["empleado_id"], r["fecha"]))

        # Insertar desde calendarios, respetando entradas manuales y feriados
        for eid, asig in asig_map.items():
            cid = asig["calendario_id"]
            cal_dias = cal_cache.get(cid, {})
            for i, fecha_dia in enumerate(dias):
                if (eid, fecha_dia) in manuales:
                    omitidos += 1
                    continue
                es_franco, horario_id = _resolver_dia(
                    i, cal_dias.get(i, {}),
                    asig["franco_rotativo"], asig["franco_dia_semana"],
                    fecha_dia in feriados_semana, bool(cal_feriado.get(cid, 0))
                )
                conn.execute(
                    "INSERT INTO planificacion (empleado_id, fecha, horario_id, es_franco, auto_generado) VALUES (?,?,?,?,1)",
                    (eid, fecha_dia, horario_id, es_franco)
                )
                generados += 1

    return {"generados": generados, "omitidos": omitidos}
