from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from datetime import date, timedelta, datetime
from db.database import db_session
from auth.core import require_permiso, get_current_user

router = APIRouter(prefix="/api/distribucion", tags=["distribucion"])


# ── Modelos ────────────────────────────────────────────────────────────────────

class PuestoIn(BaseModel):
    nombre: str
    departamento_id: int
    orden: int = 0
    activo: bool = True


class DistribucionIn(BaseModel):
    departamento_id: int
    turno: str  # "TM" | "TN" | "CO"
    semana_inicio: str  # ISO date — lunes de la semana


class DetalleIn(BaseModel):
    distribucion_id: int
    empleado_id: int
    puesto_id: Optional[int] = None
    fecha: str
    es_franco: bool = False
    horario_id: Optional[int] = None


class UsuarioDistribucionIn(BaseModel):
    usuario_id: int
    departamento_id: int
    turno: str


class FrancoIn(BaseModel):
    empleado_id: int
    fecha: str


# ── Helpers ────────────────────────────────────────────────────────────────────

def _lunes(fecha_str: str) -> date:
    d = date.fromisoformat(fecha_str)
    return d - timedelta(days=d.weekday())


def _scope_departamentos(conn, user: dict) -> list[int]:
    """Devuelve lista de departamento_ids accesibles para el usuario.
    El rol 'sistema' ve todo; el resto solo lo que tiene en usuarios_distribucion."""
    rol_nombre = user.get("rol", "")
    if rol_nombre.lower() == "sistema":
        rows = conn.execute("SELECT id FROM departamentos WHERE activo=1").fetchall()
        return [r["id"] for r in rows]
    uid = int(user["sub"])
    rows = conn.execute(
        "SELECT DISTINCT departamento_id FROM usuarios_distribucion WHERE usuario_id=?", (uid,)
    ).fetchall()
    return [r["departamento_id"] for r in rows]


def _scope_turnos(conn, user: dict, departamento_id: int) -> list[str]:
    """Devuelve turnos accesibles para el usuario en ese departamento."""
    rol_nombre = user.get("rol", "")
    if rol_nombre.lower() == "sistema":
        return ["TM", "TN", "CO"]
    uid = int(user["sub"])
    rows = conn.execute(
        "SELECT turno FROM usuarios_distribucion WHERE usuario_id=? AND departamento_id=?",
        (uid, departamento_id)
    ).fetchall()
    return [r["turno"] for r in rows]


# ── Departamentos ──────────────────────────────────────────────────────────────

@router.get("/departamentos")
def get_departamentos(_user=Depends(require_permiso("distribucion", "ver"))):
    with db_session() as conn:
        rows = conn.execute(
            """SELECT d.id, d.nombre, d.activo, d.sector_id,
                      d.usa_distribucion, d.escribe_planificacion,
                      s.nombre AS sector_nombre
               FROM departamentos d
               LEFT JOIN sectores_legajo s ON s.id = d.sector_id
               WHERE d.activo = 1
               ORDER BY s.nombre, d.nombre"""
        ).fetchall()
    return [dict(r) for r in rows]


# ── Puestos ────────────────────────────────────────────────────────────────────

@router.get("/puestos")
def get_puestos(departamento_id: Optional[int] = None,
                _user=Depends(require_permiso("distribucion", "ver"))):
    with db_session() as conn:
        if departamento_id:
            rows = conn.execute(
                "SELECT * FROM puestos WHERE departamento_id=? AND activo=1 ORDER BY orden, nombre",
                (departamento_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM puestos WHERE activo=1 ORDER BY departamento_id, orden, nombre"
            ).fetchall()
    return [dict(r) for r in rows]


@router.post("/puestos")
def create_puesto(body: PuestoIn, user=Depends(require_permiso("distribucion", "editar"))):
    with db_session() as conn:
        cur = conn.execute(
            "INSERT INTO puestos (nombre, departamento_id, orden, activo) VALUES (?,?,?,?)",
            (body.nombre, body.departamento_id, body.orden, 1 if body.activo else 0)
        )
        return {"id": cur.lastrowid}


class ReordenarPuestosIn(BaseModel):
    ids: list[int]


@router.put("/puestos/reordenar")
def reordenar_puestos(departamento_id: int, body: ReordenarPuestosIn,
                       user=Depends(require_permiso("distribucion", "editar"))):
    with db_session() as conn:
        for i, pid in enumerate(body.ids):
            conn.execute(
                "UPDATE puestos SET orden=? WHERE id=? AND departamento_id=?",
                (i * 10, pid, departamento_id)
            )
    return {"ok": True}


@router.put("/puestos/{pid}")
def update_puesto(pid: int, body: PuestoIn, user=Depends(require_permiso("distribucion", "editar"))):
    with db_session() as conn:
        conn.execute(
            "UPDATE puestos SET nombre=?, departamento_id=?, orden=?, activo=? WHERE id=?",
            (body.nombre, body.departamento_id, body.orden, 1 if body.activo else 0, pid)
        )
    return {"ok": True}


@router.delete("/puestos/{pid}")
def delete_puesto(pid: int, user=Depends(require_permiso("distribucion", "editar"))):
    with db_session() as conn:
        conn.execute("UPDATE puestos SET activo=0 WHERE id=?", (pid,))
        # Limpiar asignaciones en distribuciones no confirmadas
        conn.execute(
            """DELETE FROM distribucion_detalle
               WHERE puesto_id = ?
                 AND distribucion_id IN (
                   SELECT id FROM distribucion_semana WHERE estado != 'confirmado'
                 )""",
            (pid,)
        )
    return {"ok": True}


# ── Semanas de distribución ────────────────────────────────────────────────────

@router.get("/semana")
def get_semana(departamento_id: int, turno: str, semana_inicio: str,
               user=Depends(require_permiso("distribucion", "ver"))):
    lunes = _lunes(semana_inicio)
    dias = [str(lunes + timedelta(days=i)) for i in range(7)]

    with db_session() as conn:
        # Verificar acceso
        turnos_ok = _scope_turnos(conn, user, departamento_id)
        if turno not in turnos_ok:
            raise HTTPException(403, "Sin acceso a este turno/departamento")

        dist = conn.execute(
            """SELECT d.*,
                      uc.nombre AS creado_por_nombre,
                      um.nombre AS modificado_por_nombre
               FROM distribucion_semana d
               LEFT JOIN usuarios uc ON uc.id = d.creado_por
               LEFT JOIN usuarios um ON um.id = CAST(d.modificado_por AS INTEGER)
               WHERE d.departamento_id=? AND d.turno=? AND d.semana_inicio=?""",
            (departamento_id, turno, str(lunes))
        ).fetchone()

        puestos = conn.execute(
            "SELECT * FROM puestos WHERE departamento_id=? AND activo=1 ORDER BY orden, nombre",
            (departamento_id,)
        ).fetchall()

        detalles = []
        francos = []
        comida_personal = []
        if dist:
            detalles = conn.execute(
                """SELECT dd.*, e.nombre AS emp_nombre, e.apellido AS emp_apellido,
                          p.nombre AS puesto_nombre, h.nombre AS horario_nombre
                   FROM distribucion_detalle dd
                   LEFT JOIN empleados e ON e.id = dd.empleado_id
                   LEFT JOIN puestos p ON p.id = dd.puesto_id
                   LEFT JOIN horarios h ON h.id = dd.horario_id
                   WHERE dd.distribucion_id=? AND dd.es_comida_personal=0
                   ORDER BY dd.fecha, p.orden""",
                (dist["id"],)
            ).fetchall()
            comida_personal = conn.execute(
                """SELECT dd.id, dd.empleado_id, dd.fecha,
                          e.apellido AS emp_apellido, e.nombre AS emp_nombre
                   FROM distribucion_detalle dd
                   JOIN empleados e ON e.id = dd.empleado_id
                   WHERE dd.distribucion_id=? AND dd.es_comida_personal=1
                   ORDER BY dd.fecha""",
                (dist["id"],)
            ).fetchall()
            francos = conn.execute(
                """SELECT df.id, df.empleado_id, df.fecha,
                          e.nombre AS emp_nombre, e.apellido AS emp_apellido
                   FROM distribucion_franco df
                   LEFT JOIN empleados e ON e.id = df.empleado_id
                   WHERE df.distribucion_id=?
                   ORDER BY df.fecha, e.apellido, e.nombre""",
                (dist["id"],)
            ).fetchall()

        dept_info = conn.execute(
            "SELECT usa_distribucion FROM departamentos WHERE id=?", (departamento_id,)
        ).fetchone()
        usa_dist = bool(dept_info and dept_info["usa_distribucion"])

        dept_cargos = conn.execute(
            "SELECT id FROM cargos WHERE departamento_id=?", (departamento_id,)
        ).fetchall()
        cargo_ids = [c["id"] for c in dept_cargos]
        lunes_str = str(lunes)

        if usa_dist:
            # Filtra por turno_id del empleado usando la misma lógica de grupo TM/TN/CO
            cargo_filter = ""
            if cargo_ids:
                ph = ",".join("?" * len(cargo_ids))
                cargo_filter = f"AND e.cargo_id IN ({ph})"
            if turno == "CO":
                turno_cond = "AND (lower(COALESCE(t.nombre,'')) LIKE '%cortado%')"
            elif turno == "TN":
                turno_cond = "AND (lower(COALESCE(t.nombre,'')) LIKE '%noche%' OR lower(COALESCE(t.nombre,'')) LIKE '%madrugada%')"
            else:  # TM — todo lo que no es noche/madrugada/cortado, incluyendo sin turno asignado
                turno_cond = "AND (e.turno_id IS NULL OR (lower(COALESCE(t.nombre,'')) NOT LIKE '%noche%' AND lower(COALESCE(t.nombre,'')) NOT LIKE '%madrugada%' AND lower(COALESCE(t.nombre,'')) NOT LIKE '%cortado%'))"
            params: list = cargo_ids if cargo_ids else []
            empleados = conn.execute(
                f"""SELECT DISTINCT e.id, e.nombre, e.apellido, e.tipo,
                       e.horario_habitual_id AS horario_actual_id
                   FROM empleados e
                   LEFT JOIN turnos t ON t.id = e.turno_id
                   WHERE e.activo=1 AND e.tipo != 'acceso'
                   {turno_cond}
                   {cargo_filter}
                   ORDER BY e.apellido, e.nombre""",
                params
            ).fetchall()
        elif cargo_ids:
            placeholders = ",".join("?" * len(cargo_ids))
            empleados = conn.execute(
                f"""SELECT DISTINCT e.id, e.nombre, e.apellido, e.tipo,
                       COALESCE(
                         (SELECT COALESCE(a.horario_id,
                              (SELECT cd.horario_id FROM calendarios_dias cd
                               WHERE cd.calendario_id = a.calendario_id
                                 AND cd.es_franco = 0 AND cd.horario_id IS NOT NULL
                               LIMIT 1))
                          FROM asignaciones a
                          WHERE a.empleado_id = e.id
                            AND a.fecha_desde <= ? AND (a.fecha_hasta IS NULL OR a.fecha_hasta >= ?)
                          ORDER BY a.fecha_desde DESC LIMIT 1),
                         e.horario_habitual_id
                       ) AS horario_actual_id
                   FROM empleados e
                   JOIN planilla_orden po ON po.empleado_id = e.id AND po.grupo = ?
                   WHERE e.activo=1 AND e.tipo != 'acceso'
                     AND e.cargo_id IN ({placeholders})
                   ORDER BY e.apellido, e.nombre""",
                [lunes_str, lunes_str, turno] + cargo_ids
            ).fetchall()
        else:
            empleados = conn.execute(
                """SELECT DISTINCT e.id, e.nombre, e.apellido, e.tipo,
                       COALESCE(
                         (SELECT COALESCE(a.horario_id,
                              (SELECT cd.horario_id FROM calendarios_dias cd
                               WHERE cd.calendario_id = a.calendario_id
                                 AND cd.es_franco = 0 AND cd.horario_id IS NOT NULL
                               LIMIT 1))
                          FROM asignaciones a
                          WHERE a.empleado_id = e.id
                            AND a.fecha_desde <= ? AND (a.fecha_hasta IS NULL OR a.fecha_hasta >= ?)
                          ORDER BY a.fecha_desde DESC LIMIT 1),
                         e.horario_habitual_id
                       ) AS horario_actual_id
                   FROM empleados e
                   JOIN planilla_orden po ON po.empleado_id = e.id AND po.grupo = ?
                   WHERE e.activo=1 AND e.tipo != 'acceso'
                   ORDER BY e.apellido, e.nombre""",
                (lunes_str, lunes_str, turno)
            ).fetchall()

        horarios = conn.execute(
            """SELECT h.id, h.nombre, h.tipo, t.nombre AS turno_nombre, t.hora_desde
               FROM horarios h
               LEFT JOIN turnos t ON t.id = h.turno_id
               WHERE h.activo=1
               ORDER BY COALESCE(t.hora_desde,'00:00'), h.nombre"""
        ).fetchall()

        # Novedades de la semana — fuente: asistencia mensual
        novedades_rows = conn.execute(
            """SELECT n.empleado_id, n.fecha, n.tipo, n.descripcion
               FROM novedades n
               WHERE n.fecha >= ? AND n.fecha <= ?
               AND n.bloque = 0""",
            (dias[0], dias[6])
        ).fetchall()
        novedades = [dict(r) for r in novedades_rows]

    return {
        "distribucion": dict(dist) if dist else None,
        "puestos": [dict(r) for r in puestos],
        "empleados": [dict(r) for r in empleados],
        "detalles": [dict(r) for r in detalles],
        "francos": [dict(r) for r in francos],
        "comida_personal": [dict(r) for r in comida_personal],
        "horarios": [dict(r) for r in horarios],
        "novedades": novedades,
        "dias": dias,
    }


@router.post("/semana")
def create_semana(body: DistribucionIn, user=Depends(require_permiso("distribucion", "editar"))):
    lunes = str(_lunes(body.semana_inicio))
    uid = int(user["sub"])
    with db_session() as conn:
        turnos_ok = _scope_turnos(conn, user, body.departamento_id)
        if body.turno not in turnos_ok:
            raise HTTPException(403, "Sin acceso a este turno/departamento")
        try:
            cur = conn.execute(
                """INSERT INTO distribucion_semana
                   (departamento_id, turno, semana_inicio, estado, creado_por, creado_en)
                   VALUES (?,?,?,'borrador',?,datetime('now'))""",
                (body.departamento_id, body.turno, lunes, uid)
            )
            return {"id": cur.lastrowid}
        except Exception:
            existing = conn.execute(
                "SELECT id FROM distribucion_semana WHERE departamento_id=? AND turno=? AND semana_inicio=?",
                (body.departamento_id, body.turno, lunes)
            ).fetchone()
            if existing:
                return {"id": existing["id"]}
            raise


# ── Detalles de distribución ───────────────────────────────────────────────────

@router.post("/detalle")
def add_detalle(body: DetalleIn, user=Depends(require_permiso("distribucion", "editar"))):
    uid = int(user["sub"])
    with db_session() as conn:
        dist = conn.execute(
            "SELECT * FROM distribucion_semana WHERE id=?", (body.distribucion_id,)
        ).fetchone()
        if not dist:
            raise HTTPException(404, "Distribución no encontrada")
        if dist["estado"] == "confirmado":
            raise HTTPException(409, "Los horarios ya están confirmados")
        turnos_ok = _scope_turnos(conn, user, dist["departamento_id"])
        if dist["turno"] not in turnos_ok:
            raise HTTPException(403, "Sin acceso")

        # Evitar duplicado del mismo empleado en el mismo puesto+día
        dup = conn.execute(
            """SELECT id FROM distribucion_detalle
               WHERE distribucion_id=? AND puesto_id=? AND empleado_id=? AND fecha=?
               AND es_comida_personal=0""",
            (body.distribucion_id, body.puesto_id, body.empleado_id, body.fecha)
        ).fetchone()
        if dup:
            raise HTTPException(409, "El empleado ya está asignado a este puesto ese día")

        cur = conn.execute(
            """INSERT INTO distribucion_detalle
               (distribucion_id, empleado_id, puesto_id, fecha, es_franco, horario_id, creado_por, creado_en)
               VALUES (?,?,?,?,0,?,?,datetime('now'))""",
            (body.distribucion_id, body.empleado_id, body.puesto_id,
             body.fecha, body.horario_id, uid)
        )
        det_id = cur.lastrowid
        conn.execute(
            "UPDATE distribucion_semana SET modificado_por=?, modificado_en=datetime('now') WHERE id=?",
            (uid, body.distribucion_id)
        )
        row = conn.execute(
            """SELECT dd.id, dd.empleado_id, dd.puesto_id, dd.fecha, dd.horario_id,
                      e.apellido AS emp_apellido, e.nombre AS emp_nombre
               FROM distribucion_detalle dd
               JOIN empleados e ON e.id = dd.empleado_id
               WHERE dd.id=?""", (det_id,)
        ).fetchone()
    return dict(row)


@router.delete("/detalle/{det_id}")
def delete_detalle(det_id: int, user=Depends(require_permiso("distribucion", "editar"))):
    with db_session() as conn:
        det = conn.execute(
            "SELECT dd.*, ds.estado, ds.departamento_id, ds.turno FROM distribucion_detalle dd "
            "JOIN distribucion_semana ds ON ds.id = dd.distribucion_id WHERE dd.id=?", (det_id,)
        ).fetchone()
        if not det:
            raise HTTPException(404, "Detalle no encontrado")
        if det["estado"] == "confirmado":
            raise HTTPException(409, "No se puede modificar horarios confirmados")
        turnos_ok = _scope_turnos(conn, user, det["departamento_id"])
        if det["turno"] not in turnos_ok:
            raise HTTPException(403, "Sin acceso")
        conn.execute("DELETE FROM distribucion_detalle WHERE id=?", (det_id,))
    return {"ok": True}


# ── Franco por empleado (fila inferior de la grilla) ──────────────────────────

@router.post("/semana/{dist_id}/franco")
def add_franco(dist_id: int, body: FrancoIn, user=Depends(require_permiso("distribucion", "editar"))):
    uid = int(user["sub"])
    with db_session() as conn:
        dist = conn.execute("SELECT * FROM distribucion_semana WHERE id=?", (dist_id,)).fetchone()
        if not dist:
            raise HTTPException(404, "Distribución no encontrada")
        if dist["estado"] == "confirmado":
            raise HTTPException(409, "Los horarios ya están confirmados")
        turnos_ok = _scope_turnos(conn, user, dist["departamento_id"])
        if dist["turno"] not in turnos_ok:
            raise HTTPException(403, "Sin acceso")
        try:
            cur = conn.execute(
                """INSERT INTO distribucion_franco (distribucion_id, empleado_id, fecha, creado_por, creado_en)
                   VALUES (?,?,?,?,datetime('now'))""",
                (dist_id, body.empleado_id, body.fecha, uid)
            )
            conn.execute(
                "UPDATE distribucion_semana SET modificado_por=?, modificado_en=datetime('now') WHERE id=?",
                (uid, dist_id)
            )
            return {"id": cur.lastrowid}
        except Exception:
            raise HTTPException(409, "El empleado ya tiene franco asignado ese día")


@router.delete("/semana/{dist_id}/franco/{fid}")
def delete_franco(dist_id: int, fid: int, user=Depends(require_permiso("distribucion", "editar"))):
    uid = int(user["sub"])
    with db_session() as conn:
        dist = conn.execute("SELECT * FROM distribucion_semana WHERE id=?", (dist_id,)).fetchone()
        if not dist or dist["estado"] == "confirmado":
            raise HTTPException(409, "No se puede modificar horarios confirmados")
        conn.execute(
            "DELETE FROM distribucion_franco WHERE id=? AND distribucion_id=?", (fid, dist_id)
        )
        conn.execute(
            "UPDATE distribucion_semana SET modificado_por=?, modificado_en=datetime('now') WHERE id=?",
            (uid, dist_id)
        )
    return {"ok": True}


# ── Confirmar distribución → escribe en planificacion ─────────────────────────

@router.post("/semana/{dist_id}/confirmar")
def confirmar_semana(dist_id: int, user=Depends(require_permiso("distribucion", "confirmar"))):
    uid = int(user["sub"])
    with db_session() as conn:
        dist = conn.execute(
            "SELECT * FROM distribucion_semana WHERE id=?", (dist_id,)
        ).fetchone()
        if not dist:
            raise HTTPException(404, "Distribución no encontrada")
        if dist["estado"] == "confirmado":
            raise HTTPException(409, "Ya confirmada")
        turnos_ok = _scope_turnos(conn, user, dist["departamento_id"])
        if dist["turno"] not in turnos_ok:
            raise HTTPException(403, "Sin acceso")

        detalles = conn.execute(
            "SELECT * FROM distribucion_detalle WHERE distribucion_id=?", (dist_id,)
        ).fetchall()
        francos = conn.execute(
            "SELECT * FROM distribucion_franco WHERE distribucion_id=?", (dist_id,)
        ).fetchall()

        dept_row = conn.execute(
            "SELECT usa_distribucion, escribe_planificacion FROM departamentos WHERE id=?",
            (dist["departamento_id"],)
        ).fetchone()
        escribe_planificacion = bool(dept_row and dept_row["escribe_planificacion"])

        if escribe_planificacion:
            # Limpiar planificacion auto-generada de la semana para los empleados afectados
            emp_ids = list({det["empleado_id"] for det in detalles} | {fr["empleado_id"] for fr in francos})
            if emp_ids:
                lunes_str = dist["semana_inicio"]
                domingo_str = str(date.fromisoformat(lunes_str) + timedelta(days=6))
                ph = ",".join("?" * len(emp_ids))
                conn.execute(
                    f"DELETE FROM planificacion WHERE fecha >= ? AND fecha <= ?"
                    f" AND auto_generado = 1 AND empleado_id IN ({ph})",
                    [lunes_str, domingo_str] + emp_ids
                )

        if escribe_planificacion:
            for fr in francos:
                existing_plan = conn.execute(
                    "SELECT id FROM planificacion WHERE empleado_id=? AND fecha=?",
                    (fr["empleado_id"], fr["fecha"])
                ).fetchone()
                if existing_plan:
                    conn.execute(
                        """UPDATE planificacion
                           SET es_franco=1, horario_id=NULL, origen='distribucion'
                           WHERE id=?""",
                        (existing_plan["id"],)
                    )
                else:
                    conn.execute(
                        """INSERT INTO planificacion
                           (empleado_id, fecha, es_franco, origen)
                           VALUES (?,?,1,'distribucion')""",
                        (fr["empleado_id"], fr["fecha"])
                    )

            for det in detalles:
                existing_plan = conn.execute(
                    "SELECT id FROM planificacion WHERE empleado_id=? AND fecha=?",
                    (det["empleado_id"], det["fecha"])
                ).fetchone()

                if det["es_franco"]:
                    if existing_plan:
                        conn.execute(
                            """UPDATE planificacion
                               SET es_franco=1, horario_id=NULL, origen='distribucion'
                               WHERE id=?""",
                            (existing_plan["id"],)
                        )
                    else:
                        conn.execute(
                            """INSERT INTO planificacion
                               (empleado_id, fecha, es_franco, origen)
                               VALUES (?,?,1,'distribucion')""",
                            (det["empleado_id"], det["fecha"])
                        )
                else:
                    horario_id = det["horario_id"]
                    if not horario_id:
                        asig = conn.execute(
                            """SELECT COALESCE(a.horario_id,
                                   (SELECT cd.horario_id FROM calendarios_dias cd
                                    WHERE cd.calendario_id = a.calendario_id
                                      AND cd.es_franco = 0 AND cd.horario_id IS NOT NULL
                                    LIMIT 1)) AS horario_id
                               FROM asignaciones a
                               WHERE a.empleado_id = ? AND a.fecha_desde <= ?
                                 AND (a.fecha_hasta IS NULL OR a.fecha_hasta >= ?)
                               ORDER BY a.fecha_desde DESC LIMIT 1""",
                            (det["empleado_id"], det["fecha"], det["fecha"])
                        ).fetchone()
                        horario_id = asig["horario_id"] if asig else None

                    if existing_plan:
                        conn.execute(
                            """UPDATE planificacion
                               SET es_franco=0, horario_id=?, origen='distribucion'
                               WHERE id=?""",
                            (horario_id, existing_plan["id"])
                        )
                    else:
                        conn.execute(
                            """INSERT INTO planificacion
                               (empleado_id, fecha, es_franco, horario_id, origen)
                               VALUES (?,?,0,?,'distribucion')""",
                            (det["empleado_id"], det["fecha"], horario_id)
                        )

        conn.execute(
            "UPDATE distribucion_semana SET estado='confirmado', modificado_por=?, modificado_en=datetime('now') WHERE id=?",
            (uid, dist_id)
        )

        # Replicar como borrador en las próximas 4 semanas (si no tienen distribución propia)
        lunes_actual = date.fromisoformat(dist["semana_inicio"])
        for semanas_adelante in range(1, 5):
            lunes_fut = lunes_actual + timedelta(weeks=semanas_adelante)
            lunes_fut_str = str(lunes_fut)

            ya_existe = conn.execute(
                "SELECT id FROM distribucion_semana WHERE departamento_id=? AND turno=? AND semana_inicio=?",
                (dist["departamento_id"], dist["turno"], lunes_fut_str)
            ).fetchone()
            if ya_existe:
                continue  # El chef ya tiene algo ahí, no pisamos

            cur_fut = conn.execute(
                """INSERT INTO distribucion_semana
                   (departamento_id, turno, semana_inicio, estado, creado_por, creado_en)
                   VALUES (?,?,?,'borrador',?,datetime('now'))""",
                (dist["departamento_id"], dist["turno"], lunes_fut_str, uid)
            )
            dist_fut_id = cur_fut.lastrowid

            # Copiar detalles y francos mapeando fechas al día de semana equivalente
            for det in detalles:
                fecha_orig = date.fromisoformat(det["fecha"])
                dow = fecha_orig.weekday()
                fecha_fut = str(lunes_fut + timedelta(days=dow))
                conn.execute(
                    """INSERT INTO distribucion_detalle
                       (distribucion_id, empleado_id, puesto_id, fecha, es_franco, horario_id, creado_por, creado_en)
                       VALUES (?,?,?,?,?,?,?,datetime('now'))""",
                    (dist_fut_id, det["empleado_id"], det["puesto_id"],
                     fecha_fut, det["es_franco"], det["horario_id"], uid)
                )
            for fr in francos:
                fecha_orig = date.fromisoformat(fr["fecha"])
                dow = fecha_orig.weekday()
                fecha_fut = str(lunes_fut + timedelta(days=dow))
                conn.execute(
                    """INSERT OR IGNORE INTO distribucion_franco
                       (distribucion_id, empleado_id, fecha, creado_por, creado_en)
                       VALUES (?,?,?,?,datetime('now'))""",
                    (dist_fut_id, fr["empleado_id"], fecha_fut, uid)
                )

    return {"ok": True}


@router.post("/semana/{dist_id}/desconfirmar")
def desconfirmar_semana(dist_id: int, user=Depends(require_permiso("distribucion", "confirmar"))):
    uid = int(user["sub"])
    with db_session() as conn:
        dist = conn.execute("SELECT * FROM distribucion_semana WHERE id=?", (dist_id,)).fetchone()
        if not dist:
            raise HTTPException(404, "Distribución no encontrada")
        if dist["estado"] != "confirmado":
            raise HTTPException(409, "Los horarios no están confirmados")
        turnos_ok = _scope_turnos(conn, user, dist["departamento_id"])
        if dist["turno"] not in turnos_ok:
            raise HTTPException(403, "Sin acceso")
        conn.execute(
            "UPDATE distribucion_semana SET estado='borrador', modificado_por=?, modificado_en=datetime('now') WHERE id=?",
            (uid, dist_id)
        )
    return {"ok": True}


# ── Comida de personal ────────────────────────────────────────────────────────

class ComidaPersonalIn(BaseModel):
    fecha: str
    empleado_id: int


@router.put("/semana/{dist_id}/comida-personal")
def set_comida_personal(dist_id: int, body: ComidaPersonalIn,
                        user=Depends(require_permiso("distribucion", "editar"))):
    uid = int(user["sub"])
    with db_session() as conn:
        dist = conn.execute("SELECT * FROM distribucion_semana WHERE id=?", (dist_id,)).fetchone()
        if not dist:
            raise HTTPException(404, "Distribución no encontrada")
        if dist["estado"] == "confirmado":
            raise HTTPException(409, "Distribución confirmada")
        turnos_ok = _scope_turnos(conn, user, dist["departamento_id"])
        if dist["turno"] not in turnos_ok:
            raise HTTPException(403, "Sin acceso")
        # Upsert: un solo cocinero por día
        existing = conn.execute(
            "SELECT id FROM distribucion_detalle WHERE distribucion_id=? AND fecha=? AND es_comida_personal=1",
            (dist_id, body.fecha)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE distribucion_detalle SET empleado_id=?, modificado_por=?, modificado_en=datetime('now') WHERE id=?",
                (body.empleado_id, str(uid), existing["id"])
            )
        else:
            conn.execute(
                """INSERT INTO distribucion_detalle
                   (distribucion_id, empleado_id, puesto_id, fecha, es_franco, es_comida_personal, creado_por, creado_en)
                   VALUES (?,?,NULL,?,0,1,?,datetime('now'))""",
                (dist_id, body.empleado_id, body.fecha, str(uid))
            )
        row = conn.execute(
            """SELECT dd.id, dd.empleado_id, dd.fecha,
                      e.apellido AS emp_apellido, e.nombre AS emp_nombre
               FROM distribucion_detalle dd
               JOIN empleados e ON e.id = dd.empleado_id
               WHERE dd.distribucion_id=? AND dd.fecha=? AND dd.es_comida_personal=1""",
            (dist_id, body.fecha)
        ).fetchone()
    return dict(row)


@router.delete("/semana/{dist_id}/comida-personal/{fecha}")
def delete_comida_personal(dist_id: int, fecha: str,
                           user=Depends(require_permiso("distribucion", "editar"))):
    with db_session() as conn:
        dist = conn.execute("SELECT * FROM distribucion_semana WHERE id=?", (dist_id,)).fetchone()
        if not dist:
            raise HTTPException(404, "Distribución no encontrada")
        if dist["estado"] == "confirmado":
            raise HTTPException(409, "Distribución confirmada")
        conn.execute(
            "DELETE FROM distribucion_detalle WHERE distribucion_id=? AND fecha=? AND es_comida_personal=1",
            (dist_id, fecha)
        )
    return {"ok": True}


# ── Mis accesos (scope del usuario actual) ─────────────────────────────────────

@router.get("/mis-accesos")
def get_mis_accesos(user=Depends(require_permiso("distribucion", "ver"))):
    """Devuelve departamentos y turnos que puede planificar el usuario actual."""
    with db_session() as conn:
        dept_ids = _scope_departamentos(conn, user)
        if not dept_ids:
            return {"departamentos": []}

        placeholders = ",".join("?" * len(dept_ids))
        depts = conn.execute(
            f"""SELECT d.id, d.nombre, d.sector_id, s.nombre AS sector_nombre
                FROM departamentos d
                LEFT JOIN sectores_legajo s ON s.id = d.sector_id
                WHERE d.id IN ({placeholders}) AND d.activo=1 AND d.usa_distribucion=1
                ORDER BY s.nombre, d.nombre""",
            dept_ids
        ).fetchall()

        result = []
        for d in depts:
            turnos = _scope_turnos(conn, user, d["id"])
            result.append({**dict(d), "turnos": turnos})

    return {"departamentos": result}


# ── Empleados por departamento+turno ───────────────────────────────────────────

@router.get("/empleados")
def get_empleados_dept(departamento_id: int, turno: str,
                       user=Depends(require_permiso("distribucion", "ver"))):
    with db_session() as conn:
        turnos_ok = _scope_turnos(conn, user, departamento_id)
        if turno not in turnos_ok:
            raise HTTPException(403, "Sin acceso a este turno/departamento")
        rows = conn.execute(
            """SELECT e.id, e.nombre, e.apellido, e.tipo
               FROM empleados e
               WHERE e.activo=1 AND e.tipo != 'acceso'
               ORDER BY e.apellido, e.nombre"""
        ).fetchall()
    return [dict(r) for r in rows]


# ── Administración: usuarios_distribucion ──────────────────────────────────────

@router.get("/usuarios-acceso")
def get_usuarios_acceso(_user=Depends(require_permiso("distribucion", "ver"))):
    with db_session() as conn:
        rows = conn.execute(
            """SELECT ud.id, ud.usuario_id, ud.departamento_id, ud.turno,
                      u.nombre AS usuario_nombre, u.email,
                      d.nombre AS departamento_nombre
               FROM usuarios_distribucion ud
               JOIN usuarios u ON u.id = ud.usuario_id
               JOIN departamentos d ON d.id = ud.departamento_id
               ORDER BY u.nombre, d.nombre, ud.turno"""
        ).fetchall()
    return [dict(r) for r in rows]


@router.post("/usuarios-acceso")
def add_usuario_acceso(body: UsuarioDistribucionIn,
                       _user=Depends(require_permiso("distribucion", "editar"))):
    with db_session() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO usuarios_distribucion (usuario_id, departamento_id, turno) VALUES (?,?,?)",
                (body.usuario_id, body.departamento_id, body.turno)
            )
            return {"id": cur.lastrowid}
        except Exception:
            raise HTTPException(409, "Ya existe ese acceso")


@router.delete("/usuarios-acceso/{uid}")
def delete_usuario_acceso(uid: int, _user=Depends(require_permiso("distribucion", "editar"))):
    with db_session() as conn:
        conn.execute("DELETE FROM usuarios_distribucion WHERE id=?", (uid,))
    return {"ok": True}


# ── usa_distribucion: activar / desactivar por departamento ───────────────────

@router.get("/departamentos/{dept_id}/usa-distribucion-preview")
def preview_usa_distribucion(dept_id: int, _user=Depends(require_permiso("distribucion", "editar"))):
    """Devuelve info básica del departamento para confirmar la activación."""
    with db_session() as conn:
        dept = conn.execute(
            "SELECT id, nombre, usa_distribucion FROM departamentos WHERE id=?", (dept_id,)
        ).fetchone()
        if not dept:
            raise HTTPException(404, "Departamento no encontrado")
        emp_rows = conn.execute(
            """SELECT e.id FROM empleados e
               JOIN cargos c ON c.id = e.cargo_id
               WHERE c.departamento_id = ? AND e.activo = 1""",
            (dept_id,)
        ).fetchall()
    return {
        "departamento_id": dept_id,
        "nombre": dept["nombre"],
        "usa_distribucion": bool(dept["usa_distribucion"]),
        "empleados_en_dept": len(emp_rows),
    }


class UsaDistribucionIn(BaseModel):
    value: bool


@router.put("/departamentos/{dept_id}/usa-distribucion")
def set_usa_distribucion(dept_id: int, body: UsaDistribucionIn,
                         _user=Depends(require_permiso("distribucion", "editar"))):
    """Activa o desactiva la visibilidad del departamento en el módulo de distribución."""
    with db_session() as conn:
        dept = conn.execute("SELECT id FROM departamentos WHERE id=?", (dept_id,)).fetchone()
        if not dept:
            raise HTTPException(404, "Departamento no encontrado")
        conn.execute(
            "UPDATE departamentos SET usa_distribucion=? WHERE id=?",
            (int(body.value), dept_id)
        )
        if not body.value:
            # Al desactivar visibilidad también se desactiva escritura en planificación
            conn.execute(
                "UPDATE departamentos SET escribe_planificacion=0 WHERE id=?", (dept_id,)
            )
    return {"ok": True}


@router.get("/departamentos/{dept_id}/escribe-planificacion-preview")
def preview_escribe_planificacion(dept_id: int, _user=Depends(require_permiso("distribucion", "editar"))):
    """Devuelve cuántas entradas de planificación auto-generada futura se eliminarían al activar."""
    hoy = str(date.today())
    with db_session() as conn:
        dept = conn.execute(
            "SELECT id, nombre, escribe_planificacion FROM departamentos WHERE id=?", (dept_id,)
        ).fetchone()
        if not dept:
            raise HTTPException(404, "Departamento no encontrado")
        emp_rows = conn.execute(
            """SELECT e.id FROM empleados e
               JOIN cargos c ON c.id = e.cargo_id
               WHERE c.departamento_id = ? AND e.activo = 1""",
            (dept_id,)
        ).fetchall()
        emp_ids = [r[0] for r in emp_rows]
        plan_count, emp_afectados = 0, 0
        if emp_ids:
            ph = ",".join("?" * len(emp_ids))
            plan_rows = conn.execute(
                f"""SELECT empleado_id, COUNT(*) AS n FROM planificacion
                    WHERE auto_generado = 1 AND fecha >= ? AND empleado_id IN ({ph})
                    GROUP BY empleado_id""",
                [hoy] + emp_ids
            ).fetchall()
            emp_afectados = len(plan_rows)
            plan_count = sum(r["n"] for r in plan_rows)
    return {
        "departamento_id": dept_id,
        "nombre": dept["nombre"],
        "escribe_planificacion": bool(dept["escribe_planificacion"]),
        "empleados_en_dept": len(emp_ids),
        "emp_afectados": emp_afectados,
        "plan_auto_futuras": plan_count,
    }


@router.put("/departamentos/{dept_id}/escribe-planificacion")
def set_escribe_planificacion(dept_id: int, body: UsaDistribucionIn,
                              _user=Depends(require_permiso("distribucion", "editar"))):
    """
    Activa o desactiva que las distribuciones confirmadas escriban en planificación.
    Al activar, elimina planificación auto-generada futura de los empleados del departamento.
    """
    hoy = str(date.today())
    with db_session() as conn:
        dept = conn.execute(
            "SELECT id, usa_distribucion FROM departamentos WHERE id=?", (dept_id,)
        ).fetchone()
        if not dept:
            raise HTTPException(404, "Departamento no encontrado")
        if body.value and not dept["usa_distribucion"]:
            raise HTTPException(400, "Activá primero el módulo de distribución para este departamento")
        conn.execute(
            "UPDATE departamentos SET escribe_planificacion=? WHERE id=?",
            (int(body.value), dept_id)
        )
        eliminadas = 0
        if body.value:
            emp_rows = conn.execute(
                """SELECT e.id FROM empleados e
                   JOIN cargos c ON c.id = e.cargo_id
                   WHERE c.departamento_id = ? AND e.activo = 1""",
                (dept_id,)
            ).fetchall()
            emp_ids = [r[0] for r in emp_rows]
            if emp_ids:
                ph = ",".join("?" * len(emp_ids))
                cur = conn.execute(
                    f"DELETE FROM planificacion WHERE auto_generado = 1 AND fecha >= ? AND empleado_id IN ({ph})",
                    [hoy] + emp_ids
                )
                eliminadas = cur.rowcount
    return {"ok": True, "eliminadas": eliminadas}


# ── Config de avisos por turno ────────────────────────────────────────────────

class AvisoConfigIn(BaseModel):
    dia_semana: int
    hora: str
    activo: bool = True

@router.get("/aviso-config")
def get_aviso_config(_user=Depends(require_permiso("distribucion", "ver"))):
    with db_session() as conn:
        rows = conn.execute(
            "SELECT turno, dia_semana, hora, activo FROM distribucion_aviso_config ORDER BY turno"
        ).fetchall()
    return [dict(r) for r in rows]

@router.put("/aviso-config/{turno}")
def update_aviso_config(turno: str, data: AvisoConfigIn,
                        _user=Depends(require_permiso("distribucion", "editar"))):
    if turno not in ("TM", "TN", "CO"):
        raise HTTPException(400, "Turno inválido")
    try:
        datetime.strptime(data.hora, "%H:%M")
    except ValueError:
        raise HTTPException(400, "Hora inválida, usar formato HH:MM")
    if not 1 <= data.dia_semana <= 7:
        raise HTTPException(400, "Día inválido, usar 1=lunes … 7=domingo")
    with db_session() as conn:
        conn.execute(
            "INSERT INTO distribucion_aviso_config (turno, dia_semana, hora, activo) VALUES (?,?,?,?) "
            "ON CONFLICT(turno) DO UPDATE SET dia_semana=excluded.dia_semana, hora=excluded.hora, activo=excluded.activo",
            (turno, data.dia_semana, data.hora, int(data.activo))
        )
    return {"ok": True}


# ── Avisos de borradores pendientes ───────────────────────────────────────────

@router.get("/avisos")
def get_avisos(_user=Depends(require_permiso("distribucion", "ver"))):
    with db_session() as conn:
        cfg_rows = conn.execute(
            "SELECT turno, dia_semana, hora, activo FROM distribucion_aviso_config"
        ).fetchall()
        cfg = {r["turno"]: {"dia": r["dia_semana"], "hora": r["hora"], "activo": r["activo"]}
               for r in cfg_rows}

        ahora = datetime.now()
        lunes_esta_semana = date.today() - timedelta(days=date.today().weekday())
        borradores = conn.execute(
            """SELECT ds.id, ds.semana_inicio, ds.turno,
                      d.nombre AS departamento_nombre
               FROM distribucion_semana ds
               JOIN departamentos d ON d.id = ds.departamento_id
               WHERE ds.estado = 'borrador'
                 AND ds.semana_inicio >= ?
               ORDER BY ds.semana_inicio""",
            (str(lunes_esta_semana),)
        ).fetchall()

        avisos = []
        for b in borradores:
            turno_cfg = cfg.get(b["turno"], {"dia": 4, "hora": "18:00", "activo": 1})
            if not turno_cfg.get("activo", 1):
                continue
            aviso_dia  = turno_cfg["dia"]
            aviso_hora = turno_cfg["hora"]

            lunes_semana = date.fromisoformat(b["semana_inicio"])
            # Retroceder al día de aviso de la semana anterior al lunes del borrador
            dias_antes = (7 - aviso_dia) % 7
            fecha_aviso = lunes_semana - timedelta(days=dias_antes)
            dt_aviso = datetime.combine(fecha_aviso, datetime.strptime(aviso_hora, "%H:%M").time())

            if ahora >= dt_aviso:
                avisos.append({
                    "id": b["id"],
                    "semana_inicio": b["semana_inicio"],
                    "turno": b["turno"],
                    "departamento_nombre": b["departamento_nombre"],
                })

    return avisos
