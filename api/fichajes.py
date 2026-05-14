from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from collections import defaultdict

from auth.core import require_permiso
from db.database import db_session

router = APIRouter(prefix="/api/fichajes", tags=["fichajes"])


class FichajeManualIn(BaseModel):
    empleado_id: int
    timestamp: str       # "YYYY-MM-DD HH:MM"  o  "YYYY-MM-DD HH:MM:SS"
    motivo_manual: Optional[str] = None


class EmpleadoGrupalItem(BaseModel):
    empleado_id: int
    hora_entrada: Optional[str] = None  # "HH:MM"
    hora_salida:  Optional[str] = None  # "HH:MM"


class FichajeGrupalIn(BaseModel):
    fecha: str                        # "YYYY-MM-DD"
    empleados: list[EmpleadoGrupalItem]
    motivo_manual: Optional[str] = None


def _normalizar_ts(ts: str) -> str:
    """Acepta HH:MM o HH:MM:SS, devuelve siempre YYYY-MM-DD HH:MM:SS."""
    ts = ts.strip()
    if len(ts) == 16:   # YYYY-MM-DD HH:MM
        ts = ts + ":00"
    try:
        datetime.fromisoformat(ts)
    except ValueError:
        raise HTTPException(400, f"timestamp inválido: {ts!r}")
    return ts


@router.get("/horarios-sugeridos")
def horarios_sugeridos(
    fecha: str,
    ids: str,
    _user=Depends(require_permiso("asistencia", "editar")),
):
    """
    Devuelve los horarios planificados para los empleados indicados en esa fecha.
    Usado por el modal grupal (fuerza mayor) para pre-llenar entrada/salida.
    ids = "1,2,3"
    """
    try:
        empleado_ids = [int(x) for x in ids.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(400, "ids inválidos")
    if not empleado_ids:
        return []

    ph = ",".join("?" * len(empleado_ids))
    with db_session() as conn:
        rows = conn.execute(
            f"""SELECT p.empleado_id,
                       e.nombre, e.apellido, c.nombre AS cargo,
                       h.nombre  AS horario_nombre,
                       hb.bloque, hb.hora_entrada, hb.hora_salida, hb.cruza_medianoche
                FROM planificacion p
                JOIN empleados e ON e.id = p.empleado_id
                LEFT JOIN cargos c ON c.id = e.cargo_id
                JOIN horarios_bloques hb ON hb.horario_id = p.horario_id
                LEFT JOIN horarios h ON h.id = p.horario_id
                WHERE p.empleado_id IN ({ph}) AND p.fecha = ? AND p.es_franco = 0
                ORDER BY p.empleado_id, hb.bloque""",
            (*empleado_ids, fecha),
        ).fetchall()

    result: dict[int, dict] = {}
    for r in rows:
        eid = r["empleado_id"]
        if eid not in result:
            result[eid] = {
                "empleado_id":   eid,
                "nombre":        r["nombre"],
                "apellido":      r["apellido"],
                "cargo":         r["cargo"],
                "horario_nombre": r["horario_nombre"],
                "bloques":       [],
            }
        result[eid]["bloques"].append({
            "bloque":          r["bloque"],
            "hora_entrada":    r["hora_entrada"],
            "hora_salida":     r["hora_salida"],
            "cruza_medianoche": bool(r["cruza_medianoche"]),
        })
    return list(result.values())


@router.post("", status_code=201)
def crear_fichaje_manual(
    data: FichajeManualIn,
    user=Depends(require_permiso("asistencia", "editar")),
):
    """Agrega un fichaje manual para un empleado (entrada o salida individual)."""
    ts = _normalizar_ts(data.timestamp)

    from api.periodos_cerrados import check_periodo_abierto
    with db_session() as conn:
        check_periodo_abierto(conn, ts[:10])
        emp = conn.execute(
            "SELECT id, user_id FROM empleados WHERE id=?", (data.empleado_id,)
        ).fetchone()
        if not emp:
            raise HTTPException(404, "Empleado no encontrado")
        try:
            conn.execute(
                """INSERT INTO fichajes
                   (empleado_id, user_id, timestamp, es_manual, motivo_manual, cargado_por)
                   VALUES (?,?,?,1,?,?)""",
                (data.empleado_id, emp["user_id"], ts,
                 data.motivo_manual, int(user["sub"])),
            )
        except Exception:
            raise HTTPException(409, "Ya existe un fichaje en ese timestamp para este empleado")
        row = conn.execute(
            "SELECT * FROM fichajes WHERE empleado_id=? AND timestamp=?",
            (data.empleado_id, ts),
        ).fetchone()

    try:
        from sync.evaluador import evaluar_fecha
        evaluar_fecha(ts[:10], respetar_correcciones=True, solo_empleado_id=data.empleado_id)
    except Exception:
        pass

    return dict(row)


@router.post("/grupal", status_code=201)
def crear_fichajes_grupal(
    data: FichajeGrupalIn,
    user=Depends(require_permiso("asistencia", "editar")),
):
    """
    Fuerza mayor: crea fichajes manuales de entrada y/o salida para
    múltiples empleados en una fecha.  Las horas se pre-llenan desde
    el horario planificado pero el cliente puede editarlas antes de enviar.
    """
    from api.periodos_cerrados import check_periodo_abierto
    creados: list[dict] = []
    errores: list[dict] = []

    with db_session() as conn:
        check_periodo_abierto(conn, data.fecha)
        for item in data.empleados:
            emp = conn.execute(
                "SELECT id, user_id FROM empleados WHERE id=?", (item.empleado_id,)
            ).fetchone()
            if not emp:
                errores.append({"empleado_id": item.empleado_id, "error": "no encontrado"})
                continue

            pares = [
                (item.hora_entrada, "entrada"),
                (item.hora_salida,  "salida"),
            ]
            for hora, tipo in pares:
                if not hora:
                    continue
                ts = f"{data.fecha} {hora}:00" if len(hora) == 5 else f"{data.fecha} {hora}"
                try:
                    conn.execute(
                        """INSERT INTO fichajes
                           (empleado_id, user_id, timestamp, es_manual, motivo_manual, cargado_por)
                           VALUES (?,?,?,1,?,?)""",
                        (item.empleado_id, emp["user_id"], ts,
                         data.motivo_manual, int(user["sub"])),
                    )
                    creados.append({"empleado_id": item.empleado_id, "timestamp": ts, "tipo": tipo})
                except Exception:
                    errores.append({"empleado_id": item.empleado_id, "timestamp": ts, "error": "duplicado"})

    try:
        from sync.evaluador import evaluar_fecha
        eids_creados = {item.empleado_id for item in data.empleados}
        for eid in eids_creados:
            evaluar_fecha(data.fecha, respetar_correcciones=True, solo_empleado_id=eid)
    except Exception:
        pass

    return {"creados": len(creados), "errores": errores, "detalle": creados}


@router.get("/dia")
def fichajes_dia(
    empleado_id: int,
    fecha: str,
    _user=Depends(require_permiso("asistencia", "ver")),
):
    """Fichajes de un empleado en una fecha (para tooltip en planilla)."""
    with db_session() as conn:
        rows = conn.execute(
            "SELECT id, timestamp, es_manual FROM fichajes "
            "WHERE empleado_id=? AND date(timestamp)=? ORDER BY timestamp",
            (empleado_id, fecha),
        ).fetchall()
    return [dict(r) for r in rows]


@router.delete("/{fichaje_id}")
def eliminar_fichaje(
    fichaje_id: int,
    _user=Depends(require_permiso("asistencia", "editar")),
):
    """Elimina un fichaje manual. No permite borrar fichajes biométricos."""
    from sync.evaluador import recalcular_resultado
    from api.periodos_cerrados import check_periodo_abierto
    with db_session() as conn:
        f = conn.execute(
            "SELECT id, es_manual, empleado_id, date(timestamp) as fecha FROM fichajes WHERE id=?",
            (fichaje_id,)
        ).fetchone()
        if not f:
            raise HTTPException(404, "Fichaje no encontrado")
        if not f["es_manual"]:
            raise HTTPException(400, "Solo se pueden eliminar fichajes manuales")
        check_periodo_abierto(conn, f["fecha"])
        # Limpiar referencias en resultados_dia antes de borrar
        conn.execute(
            """UPDATE resultados_dia SET
               b1_entrada    = CASE WHEN b1_entrada_id=? THEN NULL ELSE b1_entrada END,
               b1_entrada_id = CASE WHEN b1_entrada_id=? THEN NULL ELSE b1_entrada_id END,
               b1_salida     = CASE WHEN b1_salida_id=?  THEN NULL ELSE b1_salida END,
               b1_salida_id  = CASE WHEN b1_salida_id=?  THEN NULL ELSE b1_salida_id END,
               b2_entrada    = CASE WHEN b2_entrada_id=? THEN NULL ELSE b2_entrada END,
               b2_entrada_id = CASE WHEN b2_entrada_id=? THEN NULL ELSE b2_entrada_id END,
               b2_salida     = CASE WHEN b2_salida_id=?  THEN NULL ELSE b2_salida END,
               b2_salida_id  = CASE WHEN b2_salida_id=?  THEN NULL ELSE b2_salida_id END
               WHERE empleado_id=? AND fecha=?""",
            (fichaje_id,)*8 + (f["empleado_id"], f["fecha"])
        )
        conn.execute("DELETE FROM fichajes WHERE id=?", (fichaje_id,))

    from sync.evaluador import evaluar_fecha
    try:
        evaluar_fecha(f["fecha"], respetar_correcciones=True, solo_empleado_id=f["empleado_id"])
    except Exception:
        recalcular_resultado(f["empleado_id"], f["fecha"])

    return {"ok": True}
