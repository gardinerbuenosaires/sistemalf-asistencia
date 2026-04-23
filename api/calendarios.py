from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from datetime import date, timedelta
from db.database import db_session

router = APIRouter(prefix="/api/calendarios", tags=["calendarios"])

DIAS = ["lunes","martes","miercoles","jueves","viernes","sabado","domingo"]


class DiaIn(BaseModel):
    dia_semana: int
    horario_id: Optional[int] = None   # None = usa horario_base del empleado, o franco si es_franco
    es_franco: bool = False            # True = día libre


class CalendarioIn(BaseModel):
    nombre: str
    dias: list[DiaIn]  # 7 entradas


class AsignacionIn(BaseModel):
    empleado_ids: list[int]
    calendario_id: int
    fecha_desde: str
    fecha_hasta: Optional[str] = None


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
def list_calendarios():
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
def get_calendario(cid: int):
    with db_session() as conn:
        return _get_calendario(conn, cid)


@router.post("", status_code=201)
def create_calendario(data: CalendarioIn):
    if len(data.dias) != 7:
        raise HTTPException(422, "Se requieren exactamente 7 días")
    with db_session() as conn:
        cur = conn.execute("INSERT INTO calendarios (nombre) VALUES (?)", (data.nombre.strip(),))
        cid = cur.lastrowid
        for d in data.dias:
            conn.execute(
                "INSERT INTO calendarios_dias (calendario_id, dia_semana, horario_id, es_franco) VALUES (?,?,?,?)",
                (cid, d.dia_semana, d.horario_id, int(d.es_franco))
            )
        return _get_calendario(conn, cid)


@router.put("/{cid}")
def update_calendario(cid: int, data: CalendarioIn):
    if len(data.dias) != 7:
        raise HTTPException(422, "Se requieren exactamente 7 días")
    with db_session() as conn:
        c = conn.execute("SELECT id FROM calendarios WHERE id=?", (cid,)).fetchone()
        if not c:
            raise HTTPException(404, "Calendario no encontrado")
        conn.execute("UPDATE calendarios SET nombre=? WHERE id=?", (data.nombre.strip(), cid))
        conn.execute("DELETE FROM calendarios_dias WHERE calendario_id=?", (cid,))
        for d in data.dias:
            conn.execute(
                "INSERT INTO calendarios_dias (calendario_id, dia_semana, horario_id, es_franco) VALUES (?,?,?,?)",
                (cid, d.dia_semana, d.horario_id, int(d.es_franco))
            )
        return _get_calendario(conn, cid)


@router.delete("/{cid}")
def delete_calendario(cid: int):
    with db_session() as conn:
        en_uso = conn.execute(
            "SELECT COUNT(*) FROM asignaciones WHERE calendario_id=?", (cid,)
        ).fetchone()[0]
        if en_uso:
            raise HTTPException(409, "El calendario está asignado a empleados")
        conn.execute("DELETE FROM calendarios_dias WHERE calendario_id=?", (cid,))
        conn.execute("DELETE FROM calendarios WHERE id=?", (cid,))
    return {"ok": True}


# ── Asignaciones ─────────────────────────────────────────────────────────────

@router.post("/asignar")
def asignar(data: AsignacionIn):
    """Asigna un calendario a uno o varios empleados."""
    with db_session() as conn:
        for eid in data.empleado_ids:
            # Cerrar asignación vigente si existe
            conn.execute(
                "UPDATE asignaciones SET fecha_hasta=? "
                "WHERE empleado_id=? AND (fecha_hasta IS NULL OR fecha_hasta >= ?)",
                (data.fecha_desde, eid, data.fecha_desde)
            )
            conn.execute(
                "INSERT INTO asignaciones (empleado_id, calendario_id, horario_id, fecha_desde, fecha_hasta) "
                "VALUES (?, ?, NULL, ?, ?)",
                (eid, data.calendario_id, data.fecha_desde, data.fecha_hasta)
            )
    return {"ok": True, "asignados": len(data.empleado_ids)}


@router.get("/asignaciones/empleado/{empleado_id}")
def asignaciones_empleado(empleado_id: int):
    with db_session() as conn:
        rows = conn.execute(
            "SELECT a.*, c.nombre as calendario_nombre FROM asignaciones a "
            "LEFT JOIN calendarios c ON c.id=a.calendario_id "
            "WHERE a.empleado_id=? ORDER BY a.fecha_desde DESC",
            (empleado_id,)
        ).fetchall()
    return [dict(r) for r in rows]


@router.post("/generar-semana")
def generar_semana(fecha: str):
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
            SELECT a.empleado_id, a.calendario_id
            FROM asignaciones a
            WHERE a.fecha_desde <= ?
              AND (a.fecha_hasta IS NULL OR a.fecha_hasta >= ?)
            ORDER BY a.empleado_id, a.fecha_desde DESC
            """,
            (dias[6], dias[0])
        ).fetchall()

        # Quedarnos con la asignación más reciente por empleado
        asig_map = {}
        for a in asignaciones:
            if a["empleado_id"] not in asig_map:
                asig_map[a["empleado_id"]] = a["calendario_id"]

        # Cargar días de cada calendario (incluye es_franco)
        cal_cache = {}
        for eid, cid in asig_map.items():
            if cid not in cal_cache:
                dias_cal = conn.execute(
                    "SELECT dia_semana, horario_id, es_franco FROM calendarios_dias WHERE calendario_id=?", (cid,)
                ).fetchall()
                cal_cache[cid] = {r["dia_semana"]: dict(r) for r in dias_cal}

        # Planificación ya existente en la semana
        existentes = set()
        for r in conn.execute(
            "SELECT empleado_id, fecha FROM planificacion WHERE fecha >= ? AND fecha <= ?",
            (dias[0], dias[6])
        ).fetchall():
            existentes.add((r["empleado_id"], r["fecha"]))

        # Insertar los que faltan
        for eid, cid in asig_map.items():
            cal_dias = cal_cache.get(cid, {})
            for i, fecha_dia in enumerate(dias):
                if (eid, fecha_dia) in existentes:
                    omitidos += 1
                    continue
                dia = cal_dias.get(i, {})
                es_franco = int(dia.get("es_franco", 0))
                horario_id = None if es_franco else dia.get("horario_id")
                conn.execute(
                    "INSERT OR IGNORE INTO planificacion (empleado_id, fecha, horario_id, es_franco) VALUES (?,?,?,?)",
                    (eid, fecha_dia, horario_id, es_franco)
                )
                generados += 1

    return {"generados": generados, "omitidos": omitidos}
