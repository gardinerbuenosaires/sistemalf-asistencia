from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from datetime import date, timedelta
from db.database import db_session
from auth.core import require_permiso
from api.periodos_cerrados import check_periodo_abierto, check_rango_abierto

router = APIRouter(prefix="/api/mozos", tags=["mozos"])

ESTADOS_VALIDOS = {'F', 'P', 'FT', 'FD', 'V', 'E', 'ILT', 'LSG', 'L', 'S'}
NOVEDADES_BLOQUEANTES = frozenset({'ILT', 'LSG', 'L', 'E', 'V', 'S'})
GRUPOS_ORDEN = ["CORTADOS", "DIA", "NOCHE"]


def _lunes(fecha_str: str) -> date:
    d = date.fromisoformat(fecha_str)
    return d - timedelta(days=d.weekday())


def _grupo_turno(nombre_turno: Optional[str]) -> str:
    if not nombre_turno:
        return "CORTADOS"
    n = nombre_turno.lower()
    if "cortado" in n:
        return "CORTADOS"
    if "noche" in n or "madrugada" in n:
        return "NOCHE"
    return "DIA"


def _scope_departamentos(conn, user: dict) -> list[int]:
    rol = user.get("rol", "")
    if rol.lower() == "sistema":
        return [r["id"] for r in conn.execute("SELECT id FROM departamentos WHERE activo=1 AND usa_mozos=1").fetchall()]
    uid = int(user["sub"])
    rows = conn.execute(
        "SELECT departamento_id FROM usuarios_mozos WHERE usuario_id=?", (uid,)
    ).fetchall()
    return [r["departamento_id"] for r in rows]


# ── Toggle usa_mozos ──────────────────────────────────────────────────────────

class UsaMozosIn(BaseModel):
    value: bool


@router.get("/departamentos/{dept_id}/usa-mozos-preview")
def preview_usa_mozos(dept_id: int, _user=Depends(require_permiso("mozos", "editar"))):
    with db_session() as conn:
        dept = conn.execute(
            "SELECT id, nombre, usa_mozos FROM departamentos WHERE id=?", (dept_id,)
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
        "usa_mozos": bool(dept["usa_mozos"]),
        "empleados_en_dept": len(emp_rows),
    }


@router.put("/departamentos/{dept_id}/usa-mozos")
def set_usa_mozos(dept_id: int, body: UsaMozosIn,
                  _user=Depends(require_permiso("mozos", "editar"))):
    with db_session() as conn:
        dept = conn.execute("SELECT id FROM departamentos WHERE id=?", (dept_id,)).fetchone()
        if not dept:
            raise HTTPException(404, "Departamento no encontrado")
        conn.execute("UPDATE departamentos SET usa_mozos=? WHERE id=?", (int(body.value), dept_id))
    return {"ok": True}


# ── Departamentos accesibles ───────────────────────────────────────────────────

@router.get("/departamentos")
def get_departamentos(user=Depends(require_permiso("mozos", "ver"))):
    with db_session() as conn:
        ids = _scope_departamentos(conn, user)
        todos = conn.execute(
            """SELECT id, nombre, usa_mozos FROM departamentos WHERE activo=1 AND usa_mozos=1
               ORDER BY CASE WHEN lower(nombre) LIKE '%mozo%' THEN 0 ELSE 1 END, nombre"""
        ).fetchall()
        depts = [dict(d) for d in todos if d["id"] in ids]
    return {"departamentos": depts}


# ── Lectura de semana ──────────────────────────────────────────────────────────

@router.get("/semana")
def get_semana(departamento_id: int, semana_inicio: str,
               user=Depends(require_permiso("mozos", "ver"))):
    lunes  = _lunes(semana_inicio)
    lunes2 = lunes + timedelta(days=7)
    dias   = [str(lunes + timedelta(days=i)) for i in range(14)]

    with db_session() as conn:
        semana1 = conn.execute(
            "SELECT * FROM mozos_semana WHERE departamento_id=? AND semana_inicio=?",
            (departamento_id, str(lunes))
        ).fetchone()
        semana2 = conn.execute(
            "SELECT * FROM mozos_semana WHERE departamento_id=? AND semana_inicio=?",
            (departamento_id, str(lunes2))
        ).fetchone()

        detalles = []
        for s in (semana1, semana2):
            if s:
                detalles += conn.execute(
                    "SELECT empleado_id, fecha, turno, estado FROM mozos_detalle WHERE semana_id=?",
                    (s["id"],)
                ).fetchall()

        dept_cargos = conn.execute(
            "SELECT id FROM cargos WHERE departamento_id=?", (departamento_id,)
        ).fetchall()
        cargo_ids = [c["id"] for c in dept_cargos]

        empleados = []
        if cargo_ids:
            ph = ",".join("?" * len(cargo_ids))
            empleados = conn.execute(
                f"""SELECT e.id, e.nombre, e.apellido
                   FROM empleados e
                   WHERE e.activo=1 AND e.tipo != 'acceso' AND e.cargo_id IN ({ph})
                   ORDER BY e.apellido, e.nombre""",
                cargo_ids
            ).fetchall()

        emp_ids = [e["id"] for e in empleados]

        # Grupo por planificación reciente — misma lógica que planilla mensual
        grupo_emp = {}
        if emp_ids:
            ph = ",".join("?" * len(emp_ids))
            fecha_desde = str(lunes - timedelta(days=90))
            freq_rows = conn.execute(
                f"""WITH freq AS (
                        SELECT p.empleado_id, h.tipo, t.nombre AS turno_nombre,
                               COUNT(*) AS cnt,
                               ROW_NUMBER() OVER (
                                   PARTITION BY p.empleado_id ORDER BY COUNT(*) DESC
                               ) AS rn
                        FROM planificacion p
                        JOIN horarios h ON h.id = p.horario_id
                        LEFT JOIN turnos t ON t.id = h.turno_id
                        WHERE p.fecha >= ? AND p.fecha <= ?
                          AND p.horario_id IS NOT NULL
                          AND p.empleado_id IN ({ph})
                        GROUP BY p.empleado_id, h.tipo, t.nombre
                    )
                    SELECT empleado_id, tipo AS hipo, turno_nombre FROM freq WHERE rn = 1""",
                [fecha_desde, str(lunes)] + emp_ids
            ).fetchall()
            for r in freq_rows:
                hipo = (r["hipo"] or "").lower()
                tn   = (r["turno_nombre"] or "").lower()
                if hipo == "cortado":
                    grupo_emp[r["empleado_id"]] = "CORTADOS"
                elif "noche" in tn or "madrugada" in tn:
                    grupo_emp[r["empleado_id"]] = "NOCHE"
                else:
                    grupo_emp[r["empleado_id"]] = "DIA"

        novedades = []
        if emp_ids:
            ph = ",".join("?" * len(emp_ids))
            novedades = conn.execute(
                f"""SELECT empleado_id, fecha, tipo FROM novedades
                    WHERE fecha >= ? AND fecha <= ? AND bloque=0 AND tipo != 'CO'
                    AND empleado_id IN ({ph})""",
                [dias[0], dias[13]] + emp_ids
            ).fetchall()

        dept_info = conn.execute(
            "SELECT nombre, escribe_planificacion FROM departamentos WHERE id=?",
            (departamento_id,)
        ).fetchone()

        depts_accesibles = _scope_departamentos(conn, user)
        todos_depts = conn.execute(
            "SELECT id, nombre FROM departamentos WHERE activo=1 ORDER BY nombre"
        ).fetchall()
        depts_visibles = [dict(d) for d in todos_depts if d["id"] in depts_accesibles]

        # Plan base desde planificacion para (emp, fecha) sin mozos_detalle
        plan_base = []
        if emp_ids:
            detalle_keys = {(d["empleado_id"], d["fecha"]) for d in detalles}
            ph = ",".join("?" * len(emp_ids))
            plan_rows = conn.execute(
                f"""SELECT empleado_id, fecha, es_franco
                    FROM planificacion
                    WHERE fecha >= ? AND fecha <= ? AND empleado_id IN ({ph})""",
                [dias[0], dias[13]] + emp_ids
            ).fetchall()
            plan_base = [
                {"empleado_id": r["empleado_id"], "fecha": r["fecha"], "es_franco": bool(r["es_franco"])}
                for r in plan_rows
                if (r["empleado_id"], r["fecha"]) not in detalle_keys
            ]

        feriados_rows = conn.execute(
            "SELECT fecha FROM feriados WHERE fecha >= ? AND fecha <= ?",
            (dias[0], dias[13])
        ).fetchall()
        feriados = {r["fecha"][:10] for r in feriados_rows}

        # Períodos cerrados que intersectan el rango visible
        meses_rango = set()
        for d in dias:
            dd = date.fromisoformat(d)
            meses_rango.add((dd.year, dd.month))
        periodos_cerrados = []
        for anio, mes in meses_rango:
            if conn.execute(
                "SELECT 1 FROM periodos_cerrados WHERE anio=? AND mes=?", (anio, mes)
            ).fetchone():
                periodos_cerrados.append({"anio": anio, "mes": mes})

    # Agrupar empleados — misma lógica que planilla mensual
    grupos: dict[str, list] = {g: [] for g in GRUPOS_ORDEN}
    for e in empleados:
        g = grupo_emp.get(e["id"], "DIA")
        grupos[g].append(dict(e))

    return {
        "semanas": [dict(semana1) if semana1 else None, dict(semana2) if semana2 else None],
        "grupos": grupos,
        "detalles": [dict(r) for r in detalles],
        "novedades": [dict(r) for r in novedades],
        "plan_base": plan_base,
        "dias": dias,
        "dept_nombre": dept_info["nombre"] if dept_info else "",
        "escribe_planificacion": bool(dept_info and dept_info["escribe_planificacion"]),
        "departamentos": depts_visibles,
        "periodos_cerrados": periodos_cerrados,
        "feriados": list(feriados),
    }


# ── Escritura de celda ─────────────────────────────────────────────────────────

class CeldaIn(BaseModel):
    departamento_id: int
    semana_inicio: str
    empleado_id: int
    fecha: str
    turno: str
    estado: Optional[str] = None


@router.post("/celda")
def set_celda(body: CeldaIn, user=Depends(require_permiso("mozos", "editar"))):
    uid = int(user["sub"])
    lunes = str(_lunes(body.semana_inicio))
    if body.turno not in ('TM', 'TN'):
        raise HTTPException(400, "turno debe ser TM o TN")
    if body.estado and body.estado not in ESTADOS_VALIDOS:
        raise HTTPException(400, f"estado inválido: {body.estado}")

    with db_session() as conn:
        check_periodo_abierto(conn, body.fecha)
        semana = conn.execute(
            "SELECT id, estado FROM mozos_semana WHERE departamento_id=? AND semana_inicio=?",
            (body.departamento_id, lunes)
        ).fetchone()
        if not semana:
            cur = conn.execute(
                """INSERT INTO mozos_semana (departamento_id, semana_inicio, estado, creado_por, creado_en)
                   VALUES (?,?,'borrador',?,datetime('now'))""",
                (body.departamento_id, lunes, uid)
            )
            semana_id = cur.lastrowid
        else:
            if semana["estado"] == "confirmado":
                raise HTTPException(409, "Semana confirmada")
            semana_id = semana["id"]

        conn.execute(
            "UPDATE mozos_semana SET modificado_por=?, modificado_en=datetime('now') WHERE id=?",
            (uid, semana_id)
        )

        if not body.estado:
            conn.execute(
                "DELETE FROM mozos_detalle WHERE semana_id=? AND empleado_id=? AND fecha=? AND turno=?",
                (semana_id, body.empleado_id, body.fecha, body.turno)
            )
        else:
            conn.execute(
                """INSERT INTO mozos_detalle (semana_id, empleado_id, fecha, turno, estado, creado_por, creado_en)
                   VALUES (?,?,?,?,?,?,datetime('now'))
                   ON CONFLICT(semana_id, empleado_id, fecha, turno)
                   DO UPDATE SET estado=excluded.estado, creado_por=excluded.creado_por""",
                (semana_id, body.empleado_id, body.fecha, body.turno, body.estado, uid)
            )
    return {"ok": True}


# ── Confirmar ──────────────────────────────────────────────────────────────────

def _ejecutar_confirmacion(conn, semana, uid: int):
    """Lógica central de confirmación. Recibe la fila de mozos_semana ya validada."""
    semana_id = semana["id"]
    detalles = conn.execute(
        "SELECT * FROM mozos_detalle WHERE semana_id=?", (semana_id,)
    ).fetchall()
    dept_row = conn.execute(
        "SELECT escribe_planificacion FROM departamentos WHERE id=?",
        (semana["departamento_id"],)
    ).fetchone()
    escribe = bool(dept_row and dept_row["escribe_planificacion"])
    lunes_str = semana["semana_inicio"]
    domingo_str = str(date.fromisoformat(lunes_str) + timedelta(days=6))
    if escribe:
        cargo_ids = [r["id"] for r in conn.execute(
            "SELECT id FROM cargos WHERE departamento_id=?", (semana["departamento_id"],)
        ).fetchall()]
        emp_ids = []
        if cargo_ids:
            ph = ",".join("?" * len(cargo_ids))
            emp_ids = [r["id"] for r in conn.execute(
                f"SELECT id FROM empleados WHERE activo=1 AND tipo!='acceso' AND cargo_id IN ({ph})",
                cargo_ids
            ).fetchall()]
        if emp_ids:
            ph = ",".join("?" * len(emp_ids))
            nov_bloq = conn.execute(
                f"""SELECT empleado_id, fecha FROM novedades
                    WHERE fecha >= ? AND fecha <= ? AND bloque=0
                    AND tipo IN ('ILT','LSG','L','E','V','S')
                    AND empleado_id IN ({ph})""",
                [lunes_str, domingo_str] + emp_ids
            ).fetchall()
            bloqueantes = {(r["empleado_id"], r["fecha"]) for r in nov_bloq}

            # Grupo habitual de cada empleado (DIA/NOCHE/CORTADOS) por frecuencia histórica
            fecha_hist = str(date.fromisoformat(lunes_str) - timedelta(days=90))
            freq_emp = conn.execute(
                f"""WITH freq AS (
                        SELECT p.empleado_id, h.tipo, t.nombre AS tn,
                               COUNT(*) AS cnt,
                               ROW_NUMBER() OVER (
                                   PARTITION BY p.empleado_id ORDER BY COUNT(*) DESC
                               ) AS rn
                        FROM planificacion p
                        JOIN horarios h ON h.id = p.horario_id
                        LEFT JOIN turnos t ON t.id = h.turno_id
                        WHERE p.fecha >= ? AND p.fecha <= ?
                          AND p.horario_id IS NOT NULL
                          AND p.empleado_id IN ({ph})
                        GROUP BY p.empleado_id, h.tipo, t.nombre
                    )
                    SELECT empleado_id, tipo, tn AS turno_nombre FROM freq WHERE rn = 1""",
                [fecha_hist, lunes_str] + emp_ids
            ).fetchall()
            grupo_emp: dict = {}
            for r in freq_emp:
                hipo = (r["tipo"] or "").lower()
                tnombre = (r["turno_nombre"] or "").lower()
                if hipo == "cortado":
                    grupo_emp[r["empleado_id"]] = "CORTADOS"
                elif "noche" in tnombre or "madrugada" in tnombre:
                    grupo_emp[r["empleado_id"]] = "NOCHE"
                else:
                    grupo_emp[r["empleado_id"]] = "DIA"

            # Horario representativo del depto por grupo (para turnos cruzados)
            dept_freq = conn.execute(
                f"""WITH freq AS (
                        SELECT p.horario_id,
                               CASE
                                   WHEN h.tipo = 'cortado' THEN 'CORTADOS'
                                   WHEN lower(COALESCE(t.nombre,'')) LIKE '%noche%'
                                     OR lower(COALESCE(t.nombre,'')) LIKE '%madrugada%' THEN 'NOCHE'
                                   ELSE 'DIA'
                               END AS grupo,
                               COUNT(*) AS cnt,
                               ROW_NUMBER() OVER (
                                   PARTITION BY CASE
                                       WHEN h.tipo = 'cortado' THEN 'CORTADOS'
                                       WHEN lower(COALESCE(t.nombre,'')) LIKE '%noche%'
                                         OR lower(COALESCE(t.nombre,'')) LIKE '%madrugada%' THEN 'NOCHE'
                                       ELSE 'DIA'
                                   END
                                   ORDER BY COUNT(*) DESC
                               ) AS rn
                        FROM planificacion p
                        JOIN horarios h ON h.id = p.horario_id
                        LEFT JOIN turnos t ON t.id = h.turno_id
                        WHERE p.empleado_id IN ({ph}) AND p.fecha >= ?
                          AND p.horario_id IS NOT NULL
                        GROUP BY p.horario_id, grupo
                    )
                    SELECT horario_id, grupo FROM freq WHERE rn = 1""",
                emp_ids + [fecha_hist]
            ).fetchall()
            horarios_dept: dict = {r["grupo"]: r["horario_id"] for r in dept_freq}

            estado_dia: dict = {}
            for det in detalles:
                key = (det["empleado_id"], det["fecha"])
                if key not in estado_dia:
                    estado_dia[key] = {}
                estado_dia[key][det["turno"]] = det["estado"]
            dias_a_tocar = {(eid, f) for (eid, f) in estado_dia} | bloqueantes
            all_plan = conn.execute(
                f"""SELECT id, empleado_id, fecha FROM planificacion
                    WHERE fecha >= ? AND fecha <= ? AND empleado_id IN ({ph})""",
                [lunes_str, domingo_str] + emp_ids
            ).fetchall()
            ids_borrar = [r["id"] for r in all_plan
                          if (r["empleado_id"], r["fecha"]) in dias_a_tocar
                          and (r["empleado_id"], r["fecha"]) not in bloqueantes]
            if ids_borrar:
                ph2 = ",".join("?" * len(ids_borrar))
                conn.execute(f"DELETE FROM planificacion WHERE id IN ({ph2})", ids_borrar)
            lunes_dt = date.fromisoformat(lunes_str)
            dias_semana = [str(lunes_dt + timedelta(days=i)) for i in range(7)]
            for emp_id in emp_ids:
                for dia in dias_semana:
                    if (emp_id, dia) in bloqueantes:
                        continue
                    if (emp_id, dia) not in estado_dia:
                        continue
                    estados = estado_dia.get((emp_id, dia), {})
                    tm, tn = estados.get('TM'), estados.get('TN')
                    nov_tipo = None
                    for t in ('V', 'E', 'ILT', 'LSG', 'L', 'S'):
                        if t in (tm, tn):
                            nov_tipo = t
                            break
                    if nov_tipo:
                        conn.execute(
                            """INSERT OR IGNORE INTO novedades
                               (empleado_id, fecha, bloque, tipo, descripcion, creado_por)
                               VALUES (?,?,0,?,'Desde mozos',?)""",
                            (emp_id, dia, nov_tipo, uid)
                        )
                        continue
                    tm_trabaja = tm in ('P', 'FT')
                    tn_trabaja = tn in ('P', 'FT')
                    trabaja = tm_trabaja or tn_trabaja
                    if trabaja:
                        grupo = grupo_emp.get(emp_id, 'DIA')
                        # Determinar si es turno propio o cruzado
                        if tm_trabaja and tn_trabaja:
                            target = None if grupo == 'CORTADOS' else 'CORTADOS'
                        elif tm_trabaja:
                            target = None if grupo in ('DIA', 'CORTADOS') else 'DIA'
                        else:
                            target = None if grupo in ('NOCHE', 'CORTADOS') else 'NOCHE'

                        if target is not None:
                            # Turno cruzado → horario representativo del depto
                            horario_id = horarios_dept.get(target)
                        else:
                            # Turno habitual → horario personal
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
                                (emp_id, dia, dia)
                            ).fetchone()
                            horario_id = asig["horario_id"] if asig else None
                        if horario_id is not None:
                            conn.execute(
                                """INSERT INTO planificacion
                                   (empleado_id, fecha, es_franco, horario_id, origen)
                                   VALUES (?,?,0,?,'mozos')""",
                                (emp_id, dia, horario_id)
                            )
                    else:
                        conn.execute(
                            """INSERT INTO planificacion
                               (empleado_id, fecha, es_franco, origen)
                               VALUES (?,?,1,'mozos')""",
                            (emp_id, dia)
                        )
    conn.execute(
        "UPDATE mozos_semana SET estado='confirmado', modificado_por=?, modificado_en=datetime('now') WHERE id=?",
        (uid, semana_id)
    )


@router.post("/semana/{semana_id}/confirmar")
def confirmar_semana(semana_id: int, user=Depends(require_permiso("mozos", "confirmar"))):
    uid = int(user["sub"])
    with db_session() as conn:
        semana = conn.execute("SELECT * FROM mozos_semana WHERE id=?", (semana_id,)).fetchone()
        if not semana:
            raise HTTPException(404)
        if semana["estado"] == "confirmado":
            raise HTTPException(409, "Ya confirmada")
        lunes_str = semana["semana_inicio"]
        domingo_str = str(date.fromisoformat(lunes_str) + timedelta(days=6))
        check_rango_abierto(conn, lunes_str, domingo_str)
        _ejecutar_confirmacion(conn, semana, uid)
    return {"ok": True}


class ConfirmarPorFechaIn(BaseModel):
    departamento_id: int
    semana_inicio: str


@router.post("/confirmar-por-fecha")
def confirmar_por_fecha(body: ConfirmarPorFechaIn, user=Depends(require_permiso("mozos", "confirmar"))):
    """Confirma una semana creándola si aún no existe (caso: sin celdas editadas explícitamente)."""
    uid = int(user["sub"])
    lunes = str(_lunes(body.semana_inicio))
    with db_session() as conn:
        semana = conn.execute(
            "SELECT * FROM mozos_semana WHERE departamento_id=? AND semana_inicio=?",
            (body.departamento_id, lunes)
        ).fetchone()
        if semana and semana["estado"] == "confirmado":
            raise HTTPException(409, "Ya confirmada")
        domingo = str(date.fromisoformat(lunes) + timedelta(days=6))
        check_rango_abierto(conn, lunes, domingo)
        if not semana:
            cur = conn.execute(
                """INSERT INTO mozos_semana (departamento_id, semana_inicio, estado, creado_por, creado_en)
                   VALUES (?,?,'borrador',?,datetime('now'))""",
                (body.departamento_id, lunes, uid)
            )
            semana = conn.execute("SELECT * FROM mozos_semana WHERE id=?", (cur.lastrowid,)).fetchone()
        _ejecutar_confirmacion(conn, semana, uid)
    return {"ok": True}


@router.post("/semana/{semana_id}/desconfirmar")
def desconfirmar_semana(semana_id: int, user=Depends(require_permiso("mozos", "confirmar"))):
    uid = int(user["sub"])
    with db_session() as conn:
        semana = conn.execute("SELECT * FROM mozos_semana WHERE id=?", (semana_id,)).fetchone()
        if not semana or semana["estado"] != "confirmado":
            raise HTTPException(409, "No está confirmada")
        lunes_str = semana["semana_inicio"]
        domingo_str = str(date.fromisoformat(lunes_str) + timedelta(days=6))
        check_rango_abierto(conn, lunes_str, domingo_str)
        # Borrar novedades creadas por mozos durante el confirm
        cargo_ids = [r["id"] for r in conn.execute(
            "SELECT id FROM cargos WHERE departamento_id=?", (semana["departamento_id"],)
        ).fetchall()]
        if cargo_ids:
            ph = ",".join("?" * len(cargo_ids))
            emp_ids = [r["id"] for r in conn.execute(
                f"SELECT id FROM empleados WHERE activo=1 AND tipo!='acceso' AND cargo_id IN ({ph})",
                cargo_ids
            ).fetchall()]
            if emp_ids:
                ph = ",".join("?" * len(emp_ids))
                conn.execute(
                    f"""DELETE FROM novedades
                        WHERE fecha >= ? AND fecha <= ? AND descripcion = 'Desde mozos'
                        AND empleado_id IN ({ph})""",
                    [lunes_str, domingo_str] + emp_ids
                )
        conn.execute(
            "UPDATE mozos_semana SET estado='borrador', modificado_por=?, modificado_en=datetime('now') WHERE id=?",
            (uid, semana_id)
        )
    return {"ok": True}


# ── Configuración por departamento ────────────────────────────────────────────

class MozosConfigIn(BaseModel):
    departamento_id: int
    excluir_conteo: list[int] = []

@router.get("/config")
def get_config(departamento_id: int, user=Depends(require_permiso("mozos", "ver"))):
    with db_session() as conn:
        row = conn.execute(
            "SELECT valor FROM mozos_config WHERE departamento_id=? AND clave='excluir_conteo'",
            (departamento_id,)
        ).fetchone()
    ids = [int(x) for x in (row["valor"] or "").split(",") if x.strip()] if row else []
    return {"excluir_conteo": ids}

@router.post("/config")
def save_config(data: MozosConfigIn, user=Depends(require_permiso("mozos", "editar"))):
    valor = ",".join(str(i) for i in data.excluir_conteo)
    with db_session() as conn:
        conn.execute(
            """INSERT INTO mozos_config (departamento_id, clave, valor)
               VALUES (?, 'excluir_conteo', ?)
               ON CONFLICT(departamento_id, clave) DO UPDATE SET valor=excluded.valor""",
            (data.departamento_id, valor)
        )
    return {"ok": True}
