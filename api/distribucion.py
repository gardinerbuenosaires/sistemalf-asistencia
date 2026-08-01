from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from datetime import date, timedelta, datetime
from db.database import db_session
from auth.core import require_permiso, get_current_user
from api.vacaciones import _calcular_dias_formula, _get_arrastre

router = APIRouter(prefix="/api/distribucion", tags=["distribucion"])

NOVEDADES_BLOQUEANTES = frozenset({'ILT', 'LSG', 'L', 'E', 'V', 'S'})


# ── Modelos ────────────────────────────────────────────────────────────────────

class PuestoIn(BaseModel):
    nombre: str
    departamento_id: int
    orden: int = 0
    activo: bool = True
    es_chef: bool = False
    turno: Optional[str] = None


class DistribucionIn(BaseModel):
    departamento_id: int
    turno: str  # "TM" | "TN" | "CO"
    semana_inicio: str  # ISO date — lunes de la semana


class CopiarSemanaIn(BaseModel):
    departamento_id: int
    turno: str
    semana_inicio: str  # lunes de la semana NUEVA a poblar


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
    """Chef: solo sus departamentos asignados. Cualquier otro rol: todos los departamentos activos."""
    rol = user.get("rol", "").lower()
    if rol != "chef":
        rows = conn.execute("SELECT id FROM departamentos WHERE activo=1 AND usa_distribucion=1").fetchall()
        return [r["id"] for r in rows]
    uid = int(user["sub"])
    rows = conn.execute(
        "SELECT DISTINCT departamento_id FROM usuarios_distribucion WHERE usuario_id=?", (uid,)
    ).fetchall()
    return [r["departamento_id"] for r in rows]


def _scope_turnos(conn, user: dict, departamento_id: int) -> list[str]:
    """Chef: solo sus turnos asignados. Cualquier otro rol: todos los turnos."""
    rol = user.get("rol", "").lower()
    if rol != "chef":
        return ["TM", "TN", "CO"]
    uid = int(user["sub"])
    rows = conn.execute(
        "SELECT turno FROM usuarios_distribucion WHERE usuario_id=? AND departamento_id=?",
        (uid, departamento_id)
    ).fetchall()
    return [r["turno"] for r in rows]


def _vac_balance_bulk(conn, emp_ids: list[int]) -> dict[int, float]:
    """Remaining vacation days (current period) for a list of employee IDs."""
    if not emp_ids:
        return {}
    anio = date.today().year - 1
    ph = ",".join("?" * len(emp_ids))
    emps = conn.execute(
        f"SELECT id, fecha_ingreso, fecha_recontratacion, vac_dias_jubilacion FROM empleados WHERE id IN ({ph})",
        emp_ids
    ).fetchall()
    saldos = {
        (r["empleado_id"], r["anio"]): dict(r)
        for r in conn.execute("SELECT * FROM vacaciones_saldo_inicial").fetchall()
    }
    nov_rows = conn.execute(f"""
        SELECT empleado_id,
               CAST(strftime('%Y', fecha) AS INTEGER) AS anio,
               strftime('%m', fecha) AS mes,
               SUM(dias_fecha) AS dias
        FROM (
            SELECT empleado_id, fecha,
                   CASE WHEN SUM(bloque = 0) > 0 THEN 1.0
                        ELSE SUM(CASE WHEN bloque IN (1,2) THEN 0.5 ELSE 0 END)
                   END AS dias_fecha
            FROM novedades
            WHERE tipo = 'V' AND empleado_id IN ({ph})
            GROUP BY empleado_id, fecha
        )
        GROUP BY empleado_id, anio, mes
    """, emp_ids).fetchall()
    nov_map: dict = {}
    for r in nov_rows:
        key = (r["empleado_id"], r["anio"])
        if key not in nov_map:
            nov_map[key] = {}
        nov_map[key][r["mes"]] = r["dias"]
    result = {}
    for emp in emps:
        eid = emp["id"]
        vac_jub = emp["vac_dias_jubilacion"]
        recont_str = emp["fecha_recontratacion"]
        anio_recont = date.fromisoformat(recont_str).year if recont_str else None
        es_jubilacion = bool(vac_jub and anio_recont and anio >= anio_recont)
        if es_jubilacion:
            dias_formula = float(vac_jub)
        else:
            _, dias_formula = _calcular_dias_formula(emp["fecha_ingreso"], anio)
        saldo = saldos.get((eid, anio))
        dic_anio = {k: v for k, v in nov_map.get((eid, anio), {}).items() if k == "12"}
        resto_sig = {k: v for k, v in nov_map.get((eid, anio + 1), {}).items() if k < "12"}
        dias_v = sum(dic_anio.values()) + sum(resto_sig.values())
        if saldo:
            dias_disp = saldo["dias_correspondian"] - saldo["dias_tomados"] - dias_v
        else:
            arrastre = _get_arrastre(eid, anio, emp["fecha_ingreso"], saldos, nov_map,
                                     vac_dias_jubilacion=vac_jub, fecha_recontratacion_anio=anio_recont)
            dias_disp = dias_formula + arrastre - dias_v
        result[eid] = round(max(0.0, dias_disp), 1)
    return result


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
        turno_val = body.turno if body.turno in ("TM", "TN", "CO") else None
        cur = conn.execute(
            "INSERT INTO puestos (nombre, departamento_id, orden, activo, es_chef, turno) VALUES (?,?,?,?,?,?)",
            (body.nombre, body.departamento_id, body.orden, 1 if body.activo else 0, 1 if body.es_chef else 0, turno_val)
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
        turno_val = body.turno if body.turno in ("TM", "TN", "CO") else None
        conn.execute(
            "UPDATE puestos SET nombre=?, departamento_id=?, orden=?, activo=?, es_chef=?, turno=? WHERE id=?",
            (body.nombre, body.departamento_id, body.orden, 1 if body.activo else 0, 1 if body.es_chef else 0, turno_val, pid)
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
            "SELECT * FROM puestos WHERE departamento_id=? AND activo=1 AND (turno IS NULL OR turno=?) ORDER BY orden, nombre",
            (departamento_id, turno)
        ).fetchall()

        detalles = []
        francos = []
        comida_personal = []
        vac_dist = []
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
            vac_dist = conn.execute(
                "SELECT id, empleado_id, fecha FROM distribucion_vacacion WHERE distribucion_id=?",
                (dist["id"],)
            ).fetchall()

        dept_info = conn.execute(
            """SELECT usa_distribucion, horario_tm_id, horario_tn_id, horario_cortado_id, horario_doble_id
               FROM departamentos WHERE id=?""", (departamento_id,)
        ).fetchone()
        usa_dist = bool(dept_info and dept_info["usa_distribucion"])
        # Horario por defecto del turno actual (para cocineros que vienen de otro turno)
        if dept_info and turno == "TM":
            horario_turno_default = dept_info["horario_tm_id"]
        elif dept_info and turno == "TN":
            horario_turno_default = dept_info["horario_tn_id"]
        elif dept_info and turno == "CO":
            horario_turno_default = dept_info["horario_cortado_id"]
        else:
            horario_turno_default = None

        dept_cargos = conn.execute(
            "SELECT id FROM cargos WHERE departamento_id=?", (departamento_id,)
        ).fetchall()
        cargo_ids = [c["id"] for c in dept_cargos]
        lunes_str = str(lunes)

        if usa_dist:
            if not cargo_ids:
                empleados = []
            else:
                # Filtra por turno_id del empleado usando la misma lógica de grupo TM/TN/CO
                ph = ",".join("?" * len(cargo_ids))
                cargo_filter = f"AND e.cargo_id IN ({ph})"
                if turno == "CO":
                    turno_cond = "AND (lower(COALESCE(t.nombre,'')) LIKE '%cortado%')"
                elif turno == "TN":
                    turno_cond = "AND (lower(COALESCE(t.nombre,'')) LIKE '%noche%' OR lower(COALESCE(t.nombre,'')) LIKE '%madrugada%')"
                else:  # TM — todo lo que no es noche/madrugada/cortado, incluyendo sin turno asignado
                    turno_cond = "AND (e.turno_id IS NULL OR (lower(COALESCE(t.nombre,'')) NOT LIKE '%noche%' AND lower(COALESCE(t.nombre,'')) NOT LIKE '%madrugada%' AND lower(COALESCE(t.nombre,'')) NOT LIKE '%cortado%'))"
                params: list = [lunes_str, lunes_str] + cargo_ids
                empleados = conn.execute(
                    f"""SELECT DISTINCT e.id, e.nombre, e.apellido, e.tipo,
                           (SELECT COALESCE(a.horario_id,
                                (SELECT cd.horario_id FROM calendarios_dias cd
                                 WHERE cd.calendario_id = a.calendario_id
                                   AND cd.es_franco = 0 AND cd.horario_id IS NOT NULL
                                 LIMIT 1))
                            FROM asignaciones a
                            WHERE a.empleado_id = e.id
                              AND a.fecha_desde <= ? AND (a.fecha_hasta IS NULL OR a.fecha_hasta >= ?)
                            ORDER BY a.fecha_desde DESC LIMIT 1
                           ) AS horario_actual_id
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
                         NULL
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
                         NULL
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

        emp_ids_list = [e["id"] for e in empleados]

        # Novedades de la semana — excluye CO (observaciones, no bloquean asignación)
        novedades_rows = conn.execute(
            """SELECT n.empleado_id, n.fecha, n.tipo, n.descripcion
               FROM novedades n
               WHERE n.fecha >= ? AND n.fecha <= ?
               AND n.bloque = 0 AND n.tipo != 'CO'""",
            (dias[0], dias[6])
        ).fetchall()
        novedades = [dict(r) for r in novedades_rows]

        # Plan base desde planificacion — para pre-poblar cuando no hay borrador
        plan_base = []
        if emp_ids_list:
            ph = ",".join("?" * len(emp_ids_list))
            plan_rows = conn.execute(
                f"""SELECT empleado_id, fecha, es_franco, horario_id
                    FROM planificacion
                    WHERE fecha >= ? AND fecha <= ? AND empleado_id IN ({ph})""",
                [dias[0], dias[6]] + emp_ids_list
            ).fetchall()
            plan_base = [dict(r) for r in plan_rows]

        vac_balance = _vac_balance_bulk(conn, emp_ids_list)

        # Pases (cambio de turno / doble) de la semana que tocan este turno
        pases_rows = conn.execute(
            """SELECT dp.id, dp.empleado_id, dp.fecha, dp.tipo,
                      dp.turno_origen, dp.turno_destino,
                      e.nombre AS emp_nombre, e.apellido AS emp_apellido
               FROM distribucion_pase dp
               JOIN empleados e ON e.id = dp.empleado_id
               WHERE dp.departamento_id=? AND dp.fecha >= ? AND dp.fecha <= ?
                 AND (dp.turno_origen=? OR dp.turno_destino=?)
               ORDER BY dp.fecha""",
            (departamento_id, dias[0], dias[6], turno, turno)
        ).fetchall()
        pases = [dict(r) for r in pases_rows]

        # Cocineros que ENTRAN a este turno (destino) y no están en el roster base
        base_ids = {e["id"] for e in empleados}
        entrante_ids = list({p["empleado_id"] for p in pases
                             if p["turno_destino"] == turno and p["empleado_id"] not in base_ids})
        empleados_entrantes = []
        if entrante_ids:
            ph = ",".join("?" * len(entrante_ids))
            empleados_entrantes = [dict(r) for r in conn.execute(
                f"""SELECT DISTINCT e.id, e.nombre, e.apellido, e.tipo,
                       (SELECT COALESCE(a.horario_id,
                            (SELECT cd.horario_id FROM calendarios_dias cd
                             WHERE cd.calendario_id = a.calendario_id
                               AND cd.es_franco = 0 AND cd.horario_id IS NOT NULL
                             LIMIT 1))
                        FROM asignaciones a
                        WHERE a.empleado_id = e.id
                          AND a.fecha_desde <= ? AND (a.fecha_hasta IS NULL OR a.fecha_hasta >= ?)
                        ORDER BY a.fecha_desde DESC LIMIT 1
                       ) AS horario_actual_id
                   FROM empleados e
                   WHERE e.id IN ({ph}) AND e.activo=1
                   ORDER BY e.apellido, e.nombre""",
                [lunes_str, lunes_str] + entrante_ids
            ).fetchall()]

    return {
        "distribucion": dict(dist) if dist else None,
        "puestos": [dict(r) for r in puestos],
        "empleados": [dict(r) for r in empleados],
        "detalles": [dict(r) for r in detalles],
        "francos": [dict(r) for r in francos],
        "vac_dist": [dict(r) for r in vac_dist],
        "comida_personal": [dict(r) for r in comida_personal],
        "horarios": [dict(r) for r in horarios],
        "novedades": novedades,
        "plan_base": plan_base,
        "dias": dias,
        "vacaciones_balance": vac_balance,
        "pases": pases,
        "empleados_entrantes": empleados_entrantes,
        "horario_turno_default": horario_turno_default,
        "horario_doble_id": (dept_info["horario_doble_id"] if dept_info else None),
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
                   VALUES (?,?,?,'borrador',?,datetime('now','localtime'))""",
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


@router.post("/semana/copiar-anterior", status_code=200)
def copiar_semana_anterior(body: CopiarSemanaIn, user=Depends(require_permiso("distribucion", "editar"))):
    """Copia las asignaciones de la semana anterior a la semana indicada."""
    lunes_nuevo = _lunes(body.semana_inicio)
    lunes_ant   = lunes_nuevo - timedelta(days=7)
    uid = int(user["sub"])
    hoy_str = str(date.today())  # no copiar sobre días ya transcurridos (<= hoy)

    with db_session() as conn:
        turnos_ok = _scope_turnos(conn, user, body.departamento_id)
        if body.turno not in turnos_ok:
            raise HTTPException(403, "Sin acceso a este turno/departamento")

        # Semana anterior
        dist_ant = conn.execute(
            "SELECT id FROM distribucion_semana WHERE departamento_id=? AND turno=? AND semana_inicio=?",
            (body.departamento_id, body.turno, str(lunes_ant))
        ).fetchone()
        if not dist_ant:
            raise HTTPException(404, "No hay semana anterior para copiar")

        # Verificar que la nueva semana no esté confirmada
        dist_nueva = conn.execute(
            "SELECT id, estado FROM distribucion_semana WHERE departamento_id=? AND turno=? AND semana_inicio=?",
            (body.departamento_id, body.turno, str(lunes_nuevo))
        ).fetchone()
        if dist_nueva and dist_nueva["estado"] == "confirmado":
            raise HTTPException(409, "La semana de destino ya está confirmada")

        # Crear borrador si no existe
        if not dist_nueva:
            cur = conn.execute(
                """INSERT INTO distribucion_semana
                   (departamento_id, turno, semana_inicio, estado, creado_por, creado_en)
                   VALUES (?,?,?,'borrador',?,datetime('now','localtime'))""",
                (body.departamento_id, body.turno, str(lunes_nuevo), uid)
            )
            nueva_id = cur.lastrowid
        else:
            nueva_id = dist_nueva["id"]

        # Empleados activos del dept/turno
        dias_nuevos = [str(lunes_nuevo + timedelta(days=i)) for i in range(7)]
        emp_activos = {r["id"] for r in conn.execute(
            "SELECT e.id FROM empleados e WHERE e.activo=1 AND e.tipo != 'acceso'"
        ).fetchall()}

        # Novedades de la semana nueva (bloquean copia)
        nov_bloq = {(r["empleado_id"], r["fecha"]) for r in conn.execute(
            """SELECT empleado_id, fecha FROM novedades
               WHERE fecha >= ? AND fecha <= ? AND bloque = 0 AND tipo != 'CO'""",
            (dias_nuevos[0], dias_nuevos[6])
        ).fetchall()}

        # Detalles de la semana anterior (solo asignaciones a puesto, sin is_comida_personal)
        detalles_ant = conn.execute(
            """SELECT empleado_id, puesto_id, fecha, horario_id
               FROM distribucion_detalle
               WHERE distribucion_id=? AND puesto_id IS NOT NULL AND es_comida_personal=0""",
            (dist_ant["id"],)
        ).fetchall()

        # Francos de la semana anterior
        francos_ant = conn.execute(
            "SELECT empleado_id, fecha FROM distribucion_franco WHERE distribucion_id=?",
            (dist_ant["id"],)
        ).fetchall()

        copiados_det = 0
        omitidos_det = 0
        copiados_fr  = 0
        omitidos_fr  = 0

        for det in detalles_ant:
            fecha_nueva = str(date.fromisoformat(det["fecha"]) + timedelta(days=7))
            if fecha_nueva not in dias_nuevos:
                continue
            if fecha_nueva <= hoy_str:  # días <= hoy no se modifican
                continue
            emp_id = det["empleado_id"]
            if emp_id not in emp_activos or (emp_id, fecha_nueva) in nov_bloq:
                omitidos_det += 1
                continue
            # Evitar duplicado
            dup = conn.execute(
                """SELECT id FROM distribucion_detalle
                   WHERE distribucion_id=? AND empleado_id=? AND puesto_id=? AND fecha=? AND es_comida_personal=0""",
                (nueva_id, emp_id, det["puesto_id"], fecha_nueva)
            ).fetchone()
            if not dup:
                conn.execute(
                    """INSERT INTO distribucion_detalle
                       (distribucion_id, empleado_id, puesto_id, fecha, es_franco, horario_id, creado_por, creado_en)
                       VALUES (?,?,?,?,0,?,?,datetime('now','localtime'))""",
                    (nueva_id, emp_id, det["puesto_id"], fecha_nueva, det["horario_id"], uid)
                )
                copiados_det += 1

        for fr in francos_ant:
            fecha_nueva = str(date.fromisoformat(fr["fecha"]) + timedelta(days=7))
            if fecha_nueva not in dias_nuevos:
                continue
            if fecha_nueva <= hoy_str:  # días <= hoy no se modifican
                continue
            emp_id = fr["empleado_id"]
            if emp_id not in emp_activos or (emp_id, fecha_nueva) in nov_bloq:
                omitidos_fr += 1
                continue
            dup = conn.execute(
                "SELECT id FROM distribucion_franco WHERE distribucion_id=? AND empleado_id=? AND fecha=?",
                (nueva_id, emp_id, fecha_nueva)
            ).fetchone()
            if not dup:
                conn.execute(
                    "INSERT INTO distribucion_franco (distribucion_id, empleado_id, fecha) VALUES (?,?,?)",
                    (nueva_id, emp_id, fecha_nueva)
                )
                copiados_fr += 1

    return {
        "semana_id": nueva_id,
        "copiados_asignaciones": copiados_det,
        "copiados_francos": copiados_fr,
        "omitidos": omitidos_det + omitidos_fr,
    }


# ── Pases: cambio de turno / doble turno ───────────────────────────────────────

class PaseIn(BaseModel):
    departamento_id: int
    empleado_id: int
    fecha: str
    tipo: str            # 'cambio' | 'doble'
    turno_origen: str
    turno_destino: str


@router.post("/pase")
def crear_pase(body: PaseIn, user=Depends(require_permiso("distribucion", "editar"))):
    """Autoriza (por día) el cambio de turno o doble turno de un cocinero.
    Lo marca el chef del turno de ORIGEN. Solo días > hoy."""
    uid = int(user["sub"])
    if body.tipo not in ("cambio", "doble"):
        raise HTTPException(400, "tipo inválido")
    if body.turno_origen == body.turno_destino:
        raise HTTPException(400, "El turno destino debe ser distinto al de origen")
    if body.fecha <= str(date.today()):
        raise HTTPException(409, "No se puede autorizar en días ya transcurridos")
    with db_session() as conn:
        if body.turno_origen not in _scope_turnos(conn, user, body.departamento_id):
            raise HTTPException(403, "Sin acceso al turno de origen")
        conn.execute(
            """INSERT INTO distribucion_pase
               (departamento_id, empleado_id, fecha, tipo, turno_origen, turno_destino, autorizado_por)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(departamento_id, empleado_id, fecha)
               DO UPDATE SET tipo=excluded.tipo, turno_origen=excluded.turno_origen,
                             turno_destino=excluded.turno_destino,
                             autorizado_por=excluded.autorizado_por,
                             creado_en=datetime('now','localtime')""",
            (body.departamento_id, body.empleado_id, body.fecha, body.tipo,
             body.turno_origen, body.turno_destino, uid)
        )
    return {"ok": True}


@router.delete("/pase/{pase_id}")
def borrar_pase(pase_id: int, user=Depends(require_permiso("distribucion", "editar"))):
    with db_session() as conn:
        p = conn.execute("SELECT * FROM distribucion_pase WHERE id=?", (pase_id,)).fetchone()
        if not p:
            raise HTTPException(404, "Pase no encontrado")
        if p["turno_origen"] not in _scope_turnos(conn, user, p["departamento_id"]):
            raise HTTPException(403, "Sin acceso al turno de origen")
        if p["fecha"] <= str(date.today()):
            raise HTTPException(409, "No se puede modificar días ya transcurridos")
        conn.execute("DELETE FROM distribucion_pase WHERE id=?", (pase_id,))
    return {"ok": True}


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
               VALUES (?,?,?,?,0,?,?,datetime('now','localtime'))""",
            (body.distribucion_id, body.empleado_id, body.puesto_id,
             body.fecha, body.horario_id, uid)
        )
        det_id = cur.lastrowid
        conn.execute(
            "UPDATE distribucion_semana SET modificado_por=?, modificado_en=datetime('now','localtime') WHERE id=?",
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
                   VALUES (?,?,?,?,datetime('now','localtime'))""",
                (dist_id, body.empleado_id, body.fecha, uid)
            )
            conn.execute(
                "UPDATE distribucion_semana SET modificado_por=?, modificado_en=datetime('now','localtime') WHERE id=?",
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
            "UPDATE distribucion_semana SET modificado_por=?, modificado_en=datetime('now','localtime') WHERE id=?",
            (uid, dist_id)
        )
    return {"ok": True}


# ── Vacaciones desde distribución ─────────────────────────────────────────────

class VacDistIn(BaseModel):
    empleado_id: int
    fecha: str


@router.post("/semana/{dist_id}/vacaciones")
def add_vac_dist(dist_id: int, body: VacDistIn,
                 user=Depends(require_permiso("distribucion", "editar"))):
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
        # Bloquear si ya existe una novedad real (no-V bloquea, V real ya cubre)
        existing_nov = conn.execute(
            "SELECT tipo FROM novedades WHERE empleado_id=? AND fecha=? AND bloque=0",
            (body.empleado_id, body.fecha)
        ).fetchone()
        if existing_nov:
            raise HTTPException(409, f"Ya existe novedad {existing_nov['tipo']} para ese día")
        conn.execute(
            """INSERT OR IGNORE INTO distribucion_vacacion
               (distribucion_id, empleado_id, fecha, creado_por, creado_en)
               VALUES (?,?,?,?,datetime('now','localtime'))""",
            (dist_id, body.empleado_id, body.fecha, uid)
        )
        conn.execute(
            "UPDATE distribucion_semana SET modificado_por=?, modificado_en=datetime('now','localtime') WHERE id=?",
            (uid, dist_id)
        )
    return {"ok": True}


@router.delete("/semana/{dist_id}/vacaciones/{fecha}/{emp_id}")
def del_vac_dist(dist_id: int, fecha: str, emp_id: int,
                 user=Depends(require_permiso("distribucion", "editar"))):
    with db_session() as conn:
        dist = conn.execute("SELECT * FROM distribucion_semana WHERE id=?", (dist_id,)).fetchone()
        if not dist:
            raise HTTPException(404, "Distribución no encontrada")
        if dist["estado"] == "confirmado":
            raise HTTPException(409, "Los horarios ya están confirmados")
        turnos_ok = _scope_turnos(conn, user, dist["departamento_id"])
        if dist["turno"] not in turnos_ok:
            raise HTTPException(403, "Sin acceso")
        conn.execute(
            "DELETE FROM distribucion_vacacion WHERE distribucion_id=? AND empleado_id=? AND fecha=?",
            (dist_id, emp_id, fecha)
        )
        # Fallback: borrar V novedades escritas por el código anterior directamente en novedades
        conn.execute(
            "DELETE FROM novedades WHERE empleado_id=? AND fecha=? AND bloque=0 AND tipo='V' AND descripcion='Desde distribución'",
            (emp_id, fecha)
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
        vac_staged = conn.execute(
            "SELECT * FROM distribucion_vacacion WHERE distribucion_id=?", (dist_id,)
        ).fetchall()

        lunes_str = dist["semana_inicio"]
        domingo_str = str(date.fromisoformat(lunes_str) + timedelta(days=6))
        hoy_str = str(date.today())  # los horarios aplican solo a futuro (> hoy)

        emp_ids = list(
            {det["empleado_id"] for det in detalles}
            | {fr["empleado_id"] for fr in francos}
            | {v["empleado_id"] for v in vac_staged}
        )

        # Pases de la semana: doble → horario doble turno; cambio saliente → no escribir en origen
        dept_row = conn.execute(
            "SELECT horario_doble_id FROM departamentos WHERE id=?", (dist["departamento_id"],)
        ).fetchone()
        horario_doble = dept_row["horario_doble_id"] if dept_row else None
        pases_rows = conn.execute(
            """SELECT empleado_id, fecha, tipo, turno_origen, turno_destino FROM distribucion_pase
               WHERE departamento_id=? AND fecha >= ? AND fecha <= ?""",
            (dist["departamento_id"], lunes_str, domingo_str)
        ).fetchall()
        doble_dias = {(p["empleado_id"], p["fecha"]) for p in pases_rows if p["tipo"] == "doble"}
        cambio_out_dias = {(p["empleado_id"], p["fecha"]) for p in pases_rows
                           if p["tipo"] == "cambio" and p["turno_origen"] == dist["turno"]}
        # Cocineros que ENTRAN a este turno por un pase: solo "pertenecen" a este turno en sus
        # días de pase. Sus otros días son de su turno de origen y no hay que tocarlos.
        entrante_ids = {p["empleado_id"] for p in pases_rows if p["turno_destino"] == dist["turno"]}
        pase_in_dias = {(p["empleado_id"], p["fecha"]) for p in pases_rows if p["turno_destino"] == dist["turno"]}

        # Novedades bloqueantes reales de la semana (no tocamos planificacion de esos días)
        bloqueantes: set[tuple] = set()
        franco_previo: set[tuple] = set()   # francos existentes (calendario) — a preservar
        if emp_ids:
            ph = ",".join("?" * len(emp_ids))
            nov_bloq = conn.execute(
                f"""SELECT empleado_id, fecha FROM novedades
                    WHERE fecha >= ? AND fecha <= ? AND bloque = 0
                    AND tipo IN ('ILT','LSG','L','E','V','S')
                    AND empleado_id IN ({ph})""",
                [lunes_str, domingo_str] + emp_ids
            ).fetchall()
            bloqueantes = {(r["empleado_id"], r["fecha"]) for r in nov_bloq}

            # Borrar TODA la planificacion del turno en la semana, excepto días bloqueantes
            all_plan = conn.execute(
                f"""SELECT id, empleado_id, fecha, es_franco FROM planificacion
                    WHERE fecha >= ? AND fecha <= ? AND empleado_id IN ({ph})""",
                [lunes_str, domingo_str] + emp_ids
            ).fetchall()
            franco_previo = {(r["empleado_id"], r["fecha"]) for r in all_plan if r["es_franco"]}
            ids_borrar = [r["id"] for r in all_plan
                          if (r["empleado_id"], r["fecha"]) not in bloqueantes
                          and r["fecha"] > hoy_str
                          and (r["empleado_id"], r["fecha"]) not in cambio_out_dias
                          and not (r["empleado_id"] in entrante_ids
                                   and (r["empleado_id"], r["fecha"]) not in pase_in_dias)]
            if ids_borrar:
                ph2 = ",".join("?" * len(ids_borrar))
                conn.execute(f"DELETE FROM planificacion WHERE id IN ({ph2})", ids_borrar)

        # Días de vacaciones staged (no escribimos planificacion en esos días)
        vac_dias: set[tuple] = {(v["empleado_id"], v["fecha"]) for v in vac_staged}

        for fr in francos:
            if fr["fecha"] <= hoy_str:  # no modificar días ya transcurridos
                continue
            if (fr["empleado_id"], fr["fecha"]) in cambio_out_dias:  # cambió de turno: no escribe origen
                continue
            if (fr["empleado_id"], fr["fecha"]) in bloqueantes:
                continue
            conn.execute(
                """INSERT INTO planificacion (empleado_id, fecha, es_franco, origen)
                   VALUES (?,?,1,'distribucion')""",
                (fr["empleado_id"], fr["fecha"])
            )

        for det in detalles:
            if det["fecha"] <= hoy_str:  # no modificar días ya transcurridos
                continue
            if (det["empleado_id"], det["fecha"]) in cambio_out_dias:  # cambió de turno: no escribe origen
                continue
            if (det["empleado_id"], det["fecha"]) in bloqueantes:
                continue
            if (det["empleado_id"], det["fecha"]) in vac_dias:
                continue
            if det["es_franco"]:
                conn.execute(
                    """INSERT INTO planificacion (empleado_id, fecha, es_franco, origen)
                       VALUES (?,?,1,'distribucion')""",
                    (det["empleado_id"], det["fecha"])
                )
            elif (det["empleado_id"], det["fecha"]) in doble_dias and horario_doble:
                # Doble turno: horario que abarca ambos turnos (mismo valor lo escriba TM o TN)
                conn.execute(
                    """INSERT INTO planificacion (empleado_id, fecha, es_franco, horario_id, origen)
                       VALUES (?,?,0,?,'distribucion')""",
                    (det["empleado_id"], det["fecha"], horario_doble)
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
                conn.execute(
                    """INSERT INTO planificacion (empleado_id, fecha, es_franco, horario_id, origen)
                       VALUES (?,?,0,?,'distribucion')""",
                    (det["empleado_id"], det["fecha"], horario_id)
                )

        # Escribir vacaciones staged a novedades (si no hay novedad real bloqueante)
        for v in vac_staged:
            if v["fecha"] <= hoy_str:  # no modificar días ya transcurridos
                continue
            if (v["empleado_id"], v["fecha"]) in bloqueantes:
                continue
            conn.execute(
                """INSERT OR IGNORE INTO novedades
                   (empleado_id, fecha, bloque, tipo, descripcion, creado_por)
                   VALUES (?,?,0,'V','Desde distribución',?)""",
                (v["empleado_id"], v["fecha"], v["creado_por"])
            )

        # Preservar los francos del calendario: días que tenían franco y no fueron
        # reasignados ni marcados franco manual quedan como franco en la planificación.
        asignado_o_franco = {(det["empleado_id"], det["fecha"]) for det in detalles} \
                          | {(fr["empleado_id"], fr["fecha"]) for fr in francos}
        for (emp, fecha) in franco_previo:
            if fecha <= hoy_str:
                continue
            if (emp, fecha) in asignado_o_franco:
                continue
            if (emp, fecha) in bloqueantes or (emp, fecha) in vac_dias:
                continue
            if (emp, fecha) in cambio_out_dias:
                continue
            if emp in entrante_ids and (emp, fecha) not in pase_in_dias:
                continue
            conn.execute(
                """INSERT INTO planificacion (empleado_id, fecha, es_franco, origen)
                   VALUES (?,?,1,'distribucion')""",
                (emp, fecha)
            )

        conn.execute(
            "UPDATE distribucion_semana SET estado='confirmado', modificado_por=?, modificado_en=datetime('now','localtime') WHERE id=?",
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
                   VALUES (?,?,?,'borrador',?,datetime('now','localtime'))""",
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
                       VALUES (?,?,?,?,?,?,?,datetime('now','localtime'))""",
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
                       VALUES (?,?,?,?,datetime('now','localtime'))""",
                    (dist_fut_id, fr["empleado_id"], fecha_fut, uid)
                )
            for v in vac_staged:
                fecha_orig = date.fromisoformat(v["fecha"])
                dow = fecha_orig.weekday()
                fecha_fut = str(lunes_fut + timedelta(days=dow))
                conn.execute(
                    """INSERT OR IGNORE INTO distribucion_vacacion
                       (distribucion_id, empleado_id, fecha, creado_por, creado_en)
                       VALUES (?,?,?,?,datetime('now','localtime'))""",
                    (dist_fut_id, v["empleado_id"], fecha_fut, uid)
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
            "UPDATE distribucion_semana SET estado='borrador', modificado_por=?, modificado_en=datetime('now','localtime') WHERE id=?",
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
                "UPDATE distribucion_detalle SET empleado_id=?, modificado_por=?, modificado_en=datetime('now','localtime') WHERE id=?",
                (body.empleado_id, str(uid), existing["id"])
            )
        else:
            conn.execute(
                """INSERT INTO distribucion_detalle
                   (distribucion_id, empleado_id, puesto_id, fecha, es_franco, es_comida_personal, creado_por, creado_en)
                   VALUES (?,?,NULL,?,0,1,?,datetime('now','localtime'))""",
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


# ── Personal del departamento (horario habitual) ───────────────────────────────

@router.get("/personal-dept")
def get_personal_dept(departamento_id: int, user=Depends(require_permiso("distribucion", "editar"))):
    """Empleados del departamento con nombre y cargo."""
    with db_session() as conn:
        depts_ok = _scope_departamentos(conn, user)
        if departamento_id not in depts_ok:
            raise HTTPException(403, "Sin acceso a este departamento")
        rows = conn.execute(
            """SELECT e.id, e.nombre, e.apellido,
                      c.nombre AS cargo
               FROM empleados e
               LEFT JOIN cargos c ON c.id = e.cargo_id
               WHERE e.activo = 1 AND e.tipo != 'acceso'
                 AND c.departamento_id = ?
               ORDER BY e.apellido, e.nombre""",
            (departamento_id,)
        ).fetchall()
        horarios = conn.execute(
            "SELECT id, nombre FROM horarios WHERE activo=1 ORDER BY nombre"
        ).fetchall()
    return {
        "empleados": [dict(r) for r in rows],
        "horarios":  [dict(h) for h in horarios],
    }


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
    return {"ok": True}



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
            dias_antes = 8 - aviso_dia
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
