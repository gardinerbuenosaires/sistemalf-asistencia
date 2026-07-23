from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from datetime import date, timedelta
from db.database import db_session
from auth.core import require_permiso, get_current_user
from api.periodos_cerrados import check_periodo_abierto, check_rango_abierto

router = APIRouter(prefix="/api/barmans", tags=["barmans"])

VALORES_VALIDOS = {'1', 'F', 'FT', 'FD', 'V', 'E', 'ILT', 'L', 'LSG', 'S', 'N'}


def _lunes(fecha_str: str) -> date:
    d = date.fromisoformat(fecha_str)
    return d - timedelta(days=d.weekday())


def _scope_departamentos(conn, user: dict) -> list[int]:
    rol = user.get("rol", "")
    if rol.lower() == "sistema":
        return [r["id"] for r in conn.execute(
            "SELECT id FROM departamentos WHERE activo=1 AND usa_barmans=1"
        ).fetchall()]
    uid = int(user["sub"])
    rows = conn.execute(
        "SELECT departamento_id FROM usuarios_barmans WHERE usuario_id=?", (uid,)
    ).fetchall()
    return [r["departamento_id"] for r in rows]


# ── Toggle usa_barmans ────────────────────────────────────────────────────────

class UsaBarmansIn(BaseModel):
    value: bool


@router.get("/departamentos/{dept_id}/usa-barmans-preview")
def preview_usa_barmans(dept_id: int, _user=Depends(require_permiso("barmans", "editar"))):
    with db_session() as conn:
        dept = conn.execute(
            "SELECT id, nombre, usa_barmans FROM departamentos WHERE id=?", (dept_id,)
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
        "usa_barmans": bool(dept["usa_barmans"]),
        "empleados_en_dept": len(emp_rows),
    }


@router.put("/departamentos/{dept_id}/usa-barmans")
def set_usa_barmans(dept_id: int, body: UsaBarmansIn,
                    _user=Depends(require_permiso("barmans", "editar"))):
    with db_session() as conn:
        dept = conn.execute("SELECT id FROM departamentos WHERE id=?", (dept_id,)).fetchone()
        if not dept:
            raise HTTPException(404, "Departamento no encontrado")
        conn.execute("UPDATE departamentos SET usa_barmans=? WHERE id=?", (int(body.value), dept_id))
    return {"ok": True}


# ── Departamentos accesibles ──────────────────────────────────────────────────

@router.get("/departamentos")
def get_departamentos(user=Depends(require_permiso("barmans", "ver"))):
    with db_session() as conn:
        ids = _scope_departamentos(conn, user)
        todos = conn.execute(
            "SELECT id, nombre, usa_barmans FROM departamentos WHERE activo=1 AND usa_barmans=1 ORDER BY nombre"
        ).fetchall()
        depts = [dict(d) for d in todos if d["id"] in ids]
    return {"departamentos": depts}


# ── Lectura de semana ─────────────────────────────────────────────────────────

@router.get("/semana")
def get_semana(departamento_id: int, semana_inicio: str,
               user=Depends(require_permiso("barmans", "ver"))):
    lunes  = _lunes(semana_inicio)
    lunes2 = lunes + timedelta(days=7)
    dias   = [str(lunes + timedelta(days=i)) for i in range(14)]

    with db_session() as conn:
        semana1 = conn.execute(
            "SELECT * FROM barmans_semana WHERE departamento_id=? AND semana_inicio=?",
            (departamento_id, str(lunes))
        ).fetchone()
        semana2 = conn.execute(
            "SELECT * FROM barmans_semana WHERE departamento_id=? AND semana_inicio=?",
            (departamento_id, str(lunes2))
        ).fetchone()

        detalles = []
        for s in (semana1, semana2):
            if s:
                detalles += conn.execute(
                    "SELECT empleado_id, fecha, turno, valor FROM barmans_detalle WHERE semana_id=?",
                    (s["id"],)
                ).fetchall()

        cargo_ids = [r["id"] for r in conn.execute(
            "SELECT id FROM cargos WHERE departamento_id=?", (departamento_id,)
        ).fetchall()]

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

        dept_info = conn.execute(
            "SELECT nombre FROM departamentos WHERE id=?", (departamento_id,)
        ).fetchone()

        depts_accesibles = _scope_departamentos(conn, user)
        todos_depts = conn.execute(
            "SELECT id, nombre FROM departamentos WHERE activo=1 AND usa_barmans=1 ORDER BY nombre"
        ).fetchall()
        depts_visibles = [dict(d) for d in todos_depts if d["id"] in depts_accesibles]

        emp_ids = [e["id"] for e in empleados]

        # Plan base desde planificacion — incluye grupo por turno real de ese día
        plan_base = []
        if emp_ids:
            ph = ",".join("?" * len(emp_ids))
            plan_rows = conn.execute(
                f"""SELECT p.empleado_id, p.fecha, p.es_franco,
                           CASE
                               WHEN p.es_franco = 1 THEN 'AMBOS'
                               WHEN h.tipo = 'cortado' THEN 'AMBOS'
                               WHEN lower(COALESCE(t.nombre,'')) LIKE '%noche%'
                                 OR lower(COALESCE(t.nombre,'')) LIKE '%madrugada%' THEN 'NOCHE'
                               ELSE 'DIA'
                           END AS grupo
                    FROM planificacion p
                    LEFT JOIN horarios h ON h.id = p.horario_id
                    LEFT JOIN turnos t ON t.id = h.turno_id
                    WHERE p.fecha >= ? AND p.fecha <= ? AND p.empleado_id IN ({ph})""",
                [dias[0], dias[-1]] + emp_ids
            ).fetchall()
            plan_base = [
                {"empleado_id": r["empleado_id"], "fecha": r["fecha"],
                 "es_franco": bool(r["es_franco"]), "grupo": r["grupo"]}
                for r in plan_rows
            ]

        feriados_rows = conn.execute(
            "SELECT fecha FROM feriados WHERE fecha >= ? AND fecha <= ?",
            (dias[0], dias[-1])
        ).fetchall()
        feriados = {r["fecha"][:10] for r in feriados_rows}

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

    return {
        "semanas": [dict(semana1) if semana1 else None, dict(semana2) if semana2 else None],
        "empleados": [dict(e) for e in empleados],
        "detalles": [dict(r) for r in detalles],
        "plan_base": plan_base,
        "dias": dias,
        "dept_nombre": dept_info["nombre"] if dept_info else "",
        "departamentos": depts_visibles,
        "periodos_cerrados": periodos_cerrados,
        "feriados": list(feriados),
    }


# ── Escritura de celda ────────────────────────────────────────────────────────

class CeldaIn(BaseModel):
    departamento_id: int
    semana_inicio: str
    empleado_id: int
    fecha: str
    turno: str
    valor: Optional[str] = None


@router.post("/celda")
def set_celda(body: CeldaIn, user=Depends(require_permiso("barmans", "editar"))):
    uid = int(user["sub"])
    lunes = str(_lunes(body.semana_inicio))
    if body.turno not in ('TM', 'TN'):
        raise HTTPException(400, "turno debe ser TM o TN")
    if body.valor and body.valor not in VALORES_VALIDOS:
        raise HTTPException(400, f"valor inválido: {body.valor}")

    with db_session() as conn:
        check_periodo_abierto(conn, body.fecha)
        semana = conn.execute(
            "SELECT id, estado FROM barmans_semana WHERE departamento_id=? AND semana_inicio=?",
            (body.departamento_id, lunes)
        ).fetchone()
        if not semana:
            cur = conn.execute(
                """INSERT INTO barmans_semana (departamento_id, semana_inicio, estado, creado_por, creado_en)
                   VALUES (?,?,'borrador',?,datetime('now'))""",
                (body.departamento_id, lunes, uid)
            )
            semana_id = cur.lastrowid
        else:
            if semana["estado"] == "confirmado":
                raise HTTPException(409, "Semana confirmada")
            semana_id = semana["id"]

        conn.execute(
            "UPDATE barmans_semana SET modificado_por=?, modificado_en=datetime('now') WHERE id=?",
            (uid, semana_id)
        )

        if not body.valor:
            conn.execute(
                "DELETE FROM barmans_detalle WHERE semana_id=? AND empleado_id=? AND fecha=? AND turno=?",
                (semana_id, body.empleado_id, body.fecha, body.turno)
            )
        else:
            conn.execute(
                """INSERT INTO barmans_detalle (semana_id, empleado_id, fecha, turno, valor, creado_por, creado_en)
                   VALUES (?,?,?,?,?,?,datetime('now'))
                   ON CONFLICT(semana_id, empleado_id, fecha, turno)
                   DO UPDATE SET valor=excluded.valor, creado_por=excluded.creado_por""",
                (semana_id, body.empleado_id, body.fecha, body.turno, body.valor, uid)
            )
    return {"ok": True}


# ── Confirmar / Desconfirmar ──────────────────────────────────────────────────

def _ejecutar_confirmacion_barmans(conn, semana, uid: int):
    semana_id = semana["id"]
    dept_id = semana["departamento_id"]
    lunes_str = semana["semana_inicio"]
    domingo_str = str(date.fromisoformat(lunes_str) + timedelta(days=6))

    detalles = conn.execute(
        "SELECT * FROM barmans_detalle WHERE semana_id=?", (semana_id,)
    ).fetchall()

    cargo_ids = [r["id"] for r in conn.execute(
        "SELECT id FROM cargos WHERE departamento_id=?", (dept_id,)
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

        dept_row = conn.execute(
            "SELECT horario_tm_id, horario_tn_id, horario_cortado_id FROM departamentos WHERE id=?",
            (dept_id,)
        ).fetchone()
        horario_tm = dept_row["horario_tm_id"] if dept_row else None
        horario_tn = dept_row["horario_tn_id"] if dept_row else None
        horario_ct = dept_row["horario_cortado_id"] if dept_row else None

        estado_dia: dict = {}
        for det in detalles:
            key = (det["empleado_id"], det["fecha"])
            if key not in estado_dia:
                estado_dia[key] = {}
            estado_dia[key][det["turno"]] = det["valor"]

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
                estados = estado_dia[(emp_id, dia)]
                tm = estados.get('TM')
                tn = estados.get('TN')
                # N = sin turno (override de plan_base), no genera planificacion
                if tm == 'N': tm = None
                if tn == 'N': tn = None
                if tm is None and tn is None:
                    continue

                nov_tipo = None
                for t in ('V', 'E', 'ILT', 'LSG', 'L', 'S'):
                    if t in (tm, tn):
                        nov_tipo = t
                        break
                if nov_tipo:
                    conn.execute(
                        """INSERT OR IGNORE INTO novedades
                           (empleado_id, fecha, bloque, tipo, descripcion, creado_por)
                           VALUES (?,?,0,?,'Desde barmans',?)""",
                        (emp_id, dia, nov_tipo, uid)
                    )
                    continue

                tm_trabaja = tm in ('1', 'FT')
                tn_trabaja = tn in ('1', 'FT')
                trabaja = tm_trabaja or tn_trabaja
                if trabaja:
                    if tm_trabaja and tn_trabaja:
                        horario_id = horario_ct
                    elif tm_trabaja:
                        horario_id = horario_tm
                    else:
                        horario_id = horario_tn
                    if horario_id is not None:
                        conn.execute(
                            """INSERT INTO planificacion
                               (empleado_id, fecha, es_franco, horario_id, origen)
                               VALUES (?,?,0,?,'barmans')""",
                            (emp_id, dia, horario_id)
                        )
                else:
                    conn.execute(
                        """INSERT INTO planificacion
                           (empleado_id, fecha, es_franco, origen)
                           VALUES (?,?,1,'barmans')""",
                        (emp_id, dia)
                    )

    conn.execute(
        "UPDATE barmans_semana SET estado='confirmado', modificado_por=?, modificado_en=datetime('now') WHERE id=?",
        (uid, semana_id)
    )


@router.post("/semana/{semana_id}/confirmar")
def confirmar_semana(semana_id: int, user=Depends(require_permiso("barmans", "confirmar"))):
    uid = int(user["sub"])
    with db_session() as conn:
        semana = conn.execute("SELECT * FROM barmans_semana WHERE id=?", (semana_id,)).fetchone()
        if not semana:
            raise HTTPException(404)
        if semana["estado"] == "confirmado":
            raise HTTPException(409, "Ya confirmada")
        lunes_str = semana["semana_inicio"]
        domingo_str = str(date.fromisoformat(lunes_str) + timedelta(days=6))
        check_rango_abierto(conn, lunes_str, domingo_str)
        _ejecutar_confirmacion_barmans(conn, semana, uid)
    return {"ok": True}


class ConfirmarPorFechaIn(BaseModel):
    departamento_id: int
    semana_inicio: str


@router.post("/confirmar-por-fecha")
def confirmar_por_fecha(body: ConfirmarPorFechaIn, user=Depends(require_permiso("barmans", "confirmar"))):
    uid = int(user["sub"])
    lunes = str(_lunes(body.semana_inicio))
    with db_session() as conn:
        semana = conn.execute(
            "SELECT * FROM barmans_semana WHERE departamento_id=? AND semana_inicio=?",
            (body.departamento_id, lunes)
        ).fetchone()
        if semana and semana["estado"] == "confirmado":
            raise HTTPException(409, "Ya confirmada")
        domingo = str(date.fromisoformat(lunes) + timedelta(days=6))
        check_rango_abierto(conn, lunes, domingo)
        if not semana:
            cur = conn.execute(
                """INSERT INTO barmans_semana (departamento_id, semana_inicio, estado, creado_por, creado_en)
                   VALUES (?,?,'borrador',?,datetime('now'))""",
                (body.departamento_id, lunes, uid)
            )
            semana = conn.execute("SELECT * FROM barmans_semana WHERE id=?", (cur.lastrowid,)).fetchone()
        _ejecutar_confirmacion_barmans(conn, semana, uid)
    return {"ok": True}


@router.post("/semana/{semana_id}/desconfirmar")
def desconfirmar_semana(semana_id: int, user=Depends(require_permiso("barmans", "confirmar"))):
    uid = int(user["sub"])
    with db_session() as conn:
        semana = conn.execute("SELECT * FROM barmans_semana WHERE id=?", (semana_id,)).fetchone()
        if not semana or semana["estado"] != "confirmado":
            raise HTTPException(409, "No está confirmada")
        lunes_str = semana["semana_inicio"]
        domingo_str = str(date.fromisoformat(lunes_str) + timedelta(days=6))
        check_rango_abierto(conn, lunes_str, domingo_str)
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
                        WHERE fecha >= ? AND fecha <= ? AND descripcion = 'Desde barmans'
                        AND empleado_id IN ({ph})""",
                    [lunes_str, domingo_str] + emp_ids
                )
        conn.execute(
            "UPDATE barmans_semana SET estado='borrador', modificado_por=?, modificado_en=datetime('now') WHERE id=?",
            (uid, semana_id)
        )
    return {"ok": True}
