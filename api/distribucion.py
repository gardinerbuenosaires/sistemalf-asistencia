from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from datetime import date, timedelta
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


class UsuarioDistribucionIn(BaseModel):
    usuario_id: int
    departamento_id: int
    turno: str


# ── Helpers ────────────────────────────────────────────────────────────────────

def _lunes(fecha_str: str) -> date:
    d = date.fromisoformat(fecha_str)
    return d - timedelta(days=d.weekday())


def _scope_departamentos(conn, user: dict) -> list[int]:
    """Devuelve lista de departamento_ids accesibles para el usuario.
    El rol 'sistema' ve todo; el resto solo lo que tiene en usuarios_distribucion."""
    rol_nombre = user.get("rol", "")
    if rol_nombre == "sistema":
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
    if rol_nombre == "sistema":
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
            """SELECT d.*, u.nombre AS creado_por_nombre
               FROM distribucion_semana d
               LEFT JOIN usuarios u ON u.id = d.creado_por
               WHERE d.departamento_id=? AND d.turno=? AND d.semana_inicio=?""",
            (departamento_id, turno, str(lunes))
        ).fetchone()

        puestos = conn.execute(
            "SELECT * FROM puestos WHERE departamento_id=? AND activo=1 ORDER BY orden, nombre",
            (departamento_id,)
        ).fetchall()

        # Empleados activos (excluye registros de acceso biométrico)
        empleados = conn.execute(
            """SELECT e.id, e.nombre, e.apellido, e.tipo
               FROM empleados e
               WHERE e.activo=1 AND e.tipo != 'acceso'
               ORDER BY e.apellido, e.nombre""",
        ).fetchall()

        detalles = []
        sugeridos = []
        if dist:
            detalles = conn.execute(
                """SELECT dd.*, e.nombre AS emp_nombre, e.apellido AS emp_apellido,
                          p.nombre AS puesto_nombre
                   FROM distribucion_detalle dd
                   LEFT JOIN empleados e ON e.id = dd.empleado_id
                   LEFT JOIN puestos p ON p.id = dd.puesto_id
                   WHERE dd.distribucion_id=?
                   ORDER BY dd.fecha, p.orden""",
                (dist["id"],)
            ).fetchall()
        else:
            # Sin distribución propia: buscar la semana anterior como sugerencia
            lunes_ant = str(lunes - timedelta(days=7))
            dist_ant = conn.execute(
                """SELECT id FROM distribucion_semana
                   WHERE departamento_id=? AND turno=? AND semana_inicio=?""",
                (departamento_id, turno, lunes_ant)
            ).fetchone()
            if dist_ant:
                rows_ant = conn.execute(
                    """SELECT dd.empleado_id, dd.puesto_id, dd.es_franco,
                              e.nombre AS emp_nombre, e.apellido AS emp_apellido,
                              p.nombre AS puesto_nombre,
                              dd.fecha AS fecha_original
                       FROM distribucion_detalle dd
                       LEFT JOIN empleados e ON e.id = dd.empleado_id
                       LEFT JOIN puestos p ON p.id = dd.puesto_id
                       WHERE dd.distribucion_id=?
                       ORDER BY dd.fecha, p.orden""",
                    (dist_ant["id"],)
                ).fetchall()
                # Mapear fechas de la semana anterior a la semana actual
                for row in rows_ant:
                    row_d = dict(row)
                    try:
                        fecha_orig = date.fromisoformat(row_d["fecha_original"])
                        dow = fecha_orig.weekday()  # 0=lunes … 6=domingo
                        fecha_nueva = str(lunes + timedelta(days=dow))
                        row_d["fecha"] = fecha_nueva
                        sugeridos.append(row_d)
                    except Exception:
                        pass

        # Filtrar empleados por cargo vinculado al departamento si corresponde
        dept_cargos = conn.execute(
            "SELECT id FROM cargos WHERE departamento_id=?", (departamento_id,)
        ).fetchall()
        if dept_cargos:
            cargo_ids = [c["id"] for c in dept_cargos]
            placeholders = ",".join("?" * len(cargo_ids))
            empleados = conn.execute(
                f"""SELECT e.id, e.nombre, e.apellido, e.tipo
                   FROM empleados e
                   WHERE e.activo=1 AND e.cargo_id IN ({placeholders})
                   ORDER BY e.apellido, e.nombre""",
                cargo_ids
            ).fetchall()

    return {
        "distribucion": dict(dist) if dist else None,
        "puestos": [dict(r) for r in puestos],
        "empleados": [dict(r) for r in empleados],
        "detalles": [dict(r) for r in detalles],
        "sugeridos": sugeridos,
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
def upsert_detalle(body: DetalleIn, user=Depends(require_permiso("distribucion", "editar"))):
    uid = int(user["sub"])
    with db_session() as conn:
        # Verificar que la distribución pertenece al usuario
        dist = conn.execute(
            "SELECT * FROM distribucion_semana WHERE id=?", (body.distribucion_id,)
        ).fetchone()
        if not dist:
            raise HTTPException(404, "Distribución no encontrada")
        if dist["estado"] == "confirmado":
            raise HTTPException(409, "La distribución ya está confirmada")
        turnos_ok = _scope_turnos(conn, user, dist["departamento_id"])
        if dist["turno"] not in turnos_ok:
            raise HTTPException(403, "Sin acceso")

        emp_id = body.empleado_id if body.empleado_id else None
        existing = conn.execute(
            "SELECT id FROM distribucion_detalle WHERE distribucion_id=? AND puesto_id=? AND fecha=?",
            (body.distribucion_id, body.puesto_id, body.fecha)
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE distribucion_detalle
                   SET empleado_id=?, es_franco=?, modificado_por=?, modificado_en=datetime('now')
                   WHERE id=?""",
                (emp_id, 1 if body.es_franco else 0, uid, existing["id"])
            )
            det_id = existing["id"]
        else:
            cur = conn.execute(
                """INSERT INTO distribucion_detalle
                   (distribucion_id, empleado_id, puesto_id, fecha, es_franco, creado_por, creado_en)
                   VALUES (?,?,?,?,?,?,datetime('now'))""",
                (body.distribucion_id, emp_id, body.puesto_id,
                 body.fecha, 1 if body.es_franco else 0, uid)
            )
            det_id = cur.lastrowid

        conn.execute(
            "UPDATE distribucion_semana SET modificado_por=?, modificado_en=datetime('now') WHERE id=?",
            (uid, body.distribucion_id)
        )
    return {"id": det_id}


@router.delete("/detalle")
def delete_detalle(distribucion_id: int, puesto_id: int, fecha: str,
                   user=Depends(require_permiso("distribucion", "editar"))):
    with db_session() as conn:
        dist = conn.execute(
            "SELECT * FROM distribucion_semana WHERE id=?", (distribucion_id,)
        ).fetchone()
        if not dist or dist["estado"] == "confirmado":
            raise HTTPException(409, "No se puede modificar una distribución confirmada")
        conn.execute(
            "DELETE FROM distribucion_detalle WHERE distribucion_id=? AND puesto_id=? AND fecha=?",
            (distribucion_id, puesto_id, fecha)
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

        # Obtener horario tipo turno para cada empleado
        for det in detalles:
            # Buscar en planificacion si ya existe entrada para esa fecha+empleado
            existing_plan = conn.execute(
                "SELECT id, origen FROM planificacion WHERE empleado_id=? AND fecha=?",
                (det["empleado_id"], det["fecha"])
            ).fetchone()

            if det["es_franco"]:
                if existing_plan:
                    conn.execute(
                        """UPDATE planificacion
                           SET es_franco=1, horario_id=NULL, origen='distribucion',
                               modificado_por=?, modificado_en=datetime('now')
                           WHERE id=?""",
                        (uid, existing_plan["id"])
                    )
                else:
                    conn.execute(
                        """INSERT INTO planificacion
                           (empleado_id, fecha, es_franco, origen, modificado_por, modificado_en)
                           VALUES (?,?,1,'distribucion',?,datetime('now'))""",
                        (det["empleado_id"], det["fecha"], uid)
                    )
            else:
                # Buscar un horario del turno correcto para el empleado
                horario = conn.execute(
                    """SELECT h.id FROM horarios h
                       JOIN turnos t ON t.id = h.turno_id
                       WHERE h.activo=1 AND t.nombre=?
                       LIMIT 1""",
                    (dist["turno"],)
                ).fetchone()
                horario_id = horario["id"] if horario else None

                if existing_plan:
                    conn.execute(
                        """UPDATE planificacion
                           SET es_franco=0, horario_id=?, origen='distribucion',
                               modificado_por=?, modificado_en=datetime('now')
                           WHERE id=?""",
                        (horario_id, uid, existing_plan["id"])
                    )
                else:
                    conn.execute(
                        """INSERT INTO planificacion
                           (empleado_id, fecha, es_franco, horario_id, origen, modificado_por, modificado_en)
                           VALUES (?,?,0,?,'distribucion',?,datetime('now'))""",
                        (det["empleado_id"], det["fecha"], horario_id, uid)
                    )

        conn.execute(
            "UPDATE distribucion_semana SET estado='confirmado', modificado_por=?, modificado_en=datetime('now') WHERE id=?",
            (uid, dist_id)
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
                WHERE d.id IN ({placeholders}) AND d.activo=1
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
