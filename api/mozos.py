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
    rows = conn.execute("SELECT id FROM departamentos WHERE activo=1 AND usa_mozos=1").fetchall()
    return [r["id"] for r in rows]


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


# ── Horarios (lista simple para override) ─────────────────────────────────────

@router.get("/horarios-lista")
def get_horarios_lista(dept_id: Optional[int] = None,
                       _user=Depends(require_permiso("mozos", "ver"))):
    """Horarios para el selector 'horario primero'.
    Devuelve por cada horario su grupo (DIA/NOCHE/CORTADO) y las celdas de la grilla
    que pinta (TM y/o TN), más un flag 'frecuente' (default del depto o usado por sus
    mozos en los últimos 150 días) para acortar la lista del selector."""
    with db_session() as conn:
        defaults = set()
        usados: dict = {}
        if dept_id:
            dc = conn.execute(
                "SELECT horario_tm_id, horario_tn_id, horario_cortado_id FROM departamentos WHERE id=?",
                (dept_id,)
            ).fetchone()
            if dc:
                for k in ("horario_tm_id", "horario_tn_id", "horario_cortado_id"):
                    if dc[k]:
                        defaults.add(dc[k])
            for r in conn.execute(
                """SELECT p.horario_id, COUNT(*) AS c
                   FROM planificacion p
                   JOIN empleados e ON e.id = p.empleado_id
                   JOIN cargos cg ON cg.id = e.cargo_id
                   WHERE cg.departamento_id = ? AND p.horario_id IS NOT NULL
                     AND p.fecha >= date('now','localtime','-150 days')
                   GROUP BY p.horario_id""",
                (dept_id,)
            ).fetchall():
                usados[r["horario_id"]] = r["c"]

        rows = conn.execute(
            """SELECT h.id, h.nombre, h.tipo, t.nombre AS turno,
                   (SELECT COUNT(*) FROM horarios_bloques hb
                     WHERE hb.horario_id=h.id AND hb.bloque=1 AND hb.aplica=1) AS b1,
                   (SELECT COUNT(*) FROM horarios_bloques hb
                     WHERE hb.horario_id=h.id AND hb.bloque=2 AND hb.aplica=1) AS b2
               FROM horarios h LEFT JOIN turnos t ON t.id = h.turno_id
               WHERE h.activo=1 ORDER BY h.nombre"""
        ).fetchall()

    out = []
    for r in rows:
        b1, b2 = bool(r["b1"]), bool(r["b2"])
        tn = (r["turno"] or "").lower()
        if r["tipo"] == "cortado":
            if b1 and b2:
                grupo, cells = "CORTADO", ["TM", "TN"]
            elif b2:
                grupo, cells = "NOCHE", ["TN"]
            else:
                grupo, cells = "DIA", ["TM"]
        elif "noche" in tn or "madrugada" in tn:
            grupo, cells = "NOCHE", ["TN"]
        else:
            grupo, cells = "DIA", ["TM"]
        out.append({
            "id": r["id"], "nombre": r["nombre"], "grupo": grupo, "cells": cells,
            "frecuente": (r["id"] in defaults) or (r["id"] in usados),
            "uso": usados.get(r["id"], 0),
        })
    out.sort(key=lambda x: (0 if x["frecuente"] else 1, -x["uso"], x["nombre"]))
    return out


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
                    "SELECT empleado_id, fecha, turno, estado, horario_id FROM mozos_detalle WHERE semana_id=?",
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
                   WHERE (e.activo=1 OR e.fecha_egreso > ?)
                     AND e.tipo != 'acceso' AND e.cargo_id IN ({ph})
                   ORDER BY e.apellido, e.nombre""",
                (semana_inicio, *cargo_ids)
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

        # Grupo REAL por bloques del horario dominante (para reubicar los medio turnos
        # de forma robusta, sin depender del texto de la etiqueta): un cortado con un
        # solo bloque aplicado es en realidad DIA (bloque 1) o NOCHE (bloque 2).
        grupo_real = {}
        if emp_ids:
            ph = ",".join("?" * len(emp_ids))
            gr_rows = conn.execute(
                f"""WITH freq AS (
                        SELECT p.empleado_id, p.horario_id, COUNT(*) AS cnt,
                               ROW_NUMBER() OVER (
                                   PARTITION BY p.empleado_id ORDER BY COUNT(*) DESC
                               ) AS rn
                        FROM planificacion p
                        WHERE p.fecha >= ? AND p.fecha <= ? AND p.horario_id IS NOT NULL
                          AND p.empleado_id IN ({ph})
                        GROUP BY p.empleado_id, p.horario_id
                    )
                    SELECT f.empleado_id,
                        CASE
                            WHEN lower(COALESCE(t.nombre,'')) LIKE '%noche%'
                              OR lower(COALESCE(t.nombre,'')) LIKE '%madrugada%' THEN 'NOCHE'
                            WHEN h.tipo = 'cortado' THEN
                                CASE
                                    WHEN EXISTS(SELECT 1 FROM horarios_bloques hb WHERE hb.horario_id=h.id AND hb.bloque=1 AND hb.aplica=1)
                                     AND EXISTS(SELECT 1 FROM horarios_bloques hb WHERE hb.horario_id=h.id AND hb.bloque=2 AND hb.aplica=1) THEN 'AMBOS'
                                    WHEN EXISTS(SELECT 1 FROM horarios_bloques hb WHERE hb.horario_id=h.id AND hb.bloque=2 AND hb.aplica=1) THEN 'NOCHE'
                                    ELSE 'DIA'
                                END
                            ELSE 'DIA'
                        END AS grupo_real
                    FROM freq f
                    JOIN horarios h ON h.id = f.horario_id
                    LEFT JOIN turnos t ON t.id = h.turno_id
                    WHERE f.rn = 1""",
                [fecha_desde, str(lunes)] + emp_ids
            ).fetchall()
            grupo_real = {r["empleado_id"]: r["grupo_real"] for r in gr_rows}

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
                f"""SELECT p.empleado_id, p.fecha, p.es_franco,
                           CASE
                               WHEN p.es_franco = 1 THEN 'AMBOS'
                               WHEN h.tipo = 'cortado' THEN
                                   CASE
                                       WHEN EXISTS(SELECT 1 FROM horarios_bloques hb
                                                   WHERE hb.horario_id=h.id AND hb.bloque=1 AND hb.aplica=1)
                                        AND EXISTS(SELECT 1 FROM horarios_bloques hb
                                                   WHERE hb.horario_id=h.id AND hb.bloque=2 AND hb.aplica=1) THEN 'AMBOS'
                                       WHEN EXISTS(SELECT 1 FROM horarios_bloques hb
                                                   WHERE hb.horario_id=h.id AND hb.bloque=2 AND hb.aplica=1) THEN 'NOCHE'
                                       ELSE 'DIA'
                                   END
                               WHEN lower(COALESCE(t.nombre,'')) LIKE '%noche%'
                                 OR lower(COALESCE(t.nombre,'')) LIKE '%madrugada%' THEN 'NOCHE'
                               ELSE 'DIA'
                           END AS grupo
                    FROM planificacion p
                    LEFT JOIN horarios h ON h.id = p.horario_id
                    LEFT JOIN turnos t ON t.id = h.turno_id
                    WHERE p.fecha >= ? AND p.fecha <= ? AND p.empleado_id IN ({ph})""",
                [dias[0], dias[13]] + emp_ids
            ).fetchall()
            plan_base = [
                {"empleado_id": r["empleado_id"], "fecha": r["fecha"],
                 "es_franco": bool(r["es_franco"]), "grupo": r["grupo"]}
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

        def _sem_dict(s):
            if not s:
                return None
            d = dict(s)
            uid_mod = d.get("modificado_por")
            nombre = None
            if uid_mod:
                u = conn.execute("SELECT nombre FROM usuarios WHERE id=?", (uid_mod,)).fetchone()
                nombre = u["nombre"] if u else None
            d["modificado_por_nombre"] = nombre
            return d

        sem1_dict = _sem_dict(semana1)
        sem2_dict = _sem_dict(semana2)

        # Orden manual de la planilla mensual (grupos CO, TM, TN en ese orden)
        orden_planilla = {}
        for g in ("CO", "TM", "TN"):
            orden_planilla[g] = [dict(r) for r in conn.execute(
                "SELECT empleado_id, etiqueta FROM planilla_orden WHERE grupo=? ORDER BY posicion", (g,)
            ).fetchall()]

    # Agrupar empleados por turno (para presencia/estilos internos)
    grupos: dict[str, list] = {g: [] for g in GRUPOS_ORDEN}
    for e in empleados:
        g = grupo_emp.get(e["id"], "DIA")
        grupos[g].append(dict(e))

    # Lista única de mozos, ordenada como en la planilla mensual (CO → TM → TN),
    # filtrando SOLO a los mozos del roster y colgando cada etiqueta del primer mozo real.
    # Los subgrupos "Medio Turno Mañana" / "Medio Turno Noche" (que están en CO pero son
    # de día/noche) se difieren y se ubican debajo de TM (día) y TN (noche) respectivamente.
    mozo_map = {e["id"]: dict(e) for e in empleados}
    orden_mozos, usados = [], set()
    defer_manana, defer_noche = [], []

    def _emit(g, diferir=False):
        pend = None
        for r in orden_planilla[g]:
            if r["empleado_id"] is None:
                pend = r["etiqueta"]
            elif r["empleado_id"] in mozo_map and r["empleado_id"] not in usados:
                m = mozo_map[r["empleado_id"]]
                m["_etiqueta"] = pend
                pend = None
                usados.add(r["empleado_id"])
                # Reubicación robusta: se decide por el horario real del mozo
                # (grupo_real), no por el texto de la etiqueta.
                gr = grupo_real.get(r["empleado_id"], "AMBOS")
                if diferir and gr == "DIA":
                    defer_manana.append(m)
                elif diferir and gr == "NOCHE":
                    defer_noche.append(m)
                else:
                    orden_mozos.append(m)

    _emit("CO", diferir=True)   # cortados reales quedan arriba; medio turnos se difieren
    _emit("TM")                 # mozos de día
    orden_mozos.extend(defer_manana)   # medio turno mañana, debajo de día
    _emit("TN")                 # mozos de noche
    orden_mozos.extend(defer_noche)    # medio turno noche, debajo de noche
    # Mozos que no están en ningún orden de la planilla → al final (alfabético)
    for e in empleados:
        if e["id"] not in usados:
            m = mozo_map[e["id"]]
            m["_etiqueta"] = None
            orden_mozos.append(m)
            usados.add(e["id"])

    return {
        "semanas": [sem1_dict, sem2_dict],
        "grupos": grupos,
        "orden_mozos": orden_mozos,
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
    horario_id: Optional[int] = None


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
                   VALUES (?,?,'borrador',?,datetime('now','localtime'))""",
                (body.departamento_id, lunes, uid)
            )
            semana_id = cur.lastrowid
        else:
            if semana["estado"] == "confirmado":
                raise HTTPException(409, "Semana confirmada")
            semana_id = semana["id"]

        conn.execute(
            "UPDATE mozos_semana SET modificado_por=?, modificado_en=datetime('now','localtime') WHERE id=?",
            (uid, semana_id)
        )

        if not body.estado:
            conn.execute(
                "DELETE FROM mozos_detalle WHERE semana_id=? AND empleado_id=? AND fecha=? AND turno=?",
                (semana_id, body.empleado_id, body.fecha, body.turno)
            )
        else:
            conn.execute(
                """INSERT INTO mozos_detalle (semana_id, empleado_id, fecha, turno, estado, horario_id, creado_por, creado_en)
                   VALUES (?,?,?,?,?,?,?,datetime('now','localtime'))
                   ON CONFLICT(semana_id, empleado_id, fecha, turno)
                   DO UPDATE SET estado=excluded.estado, horario_id=excluded.horario_id, creado_por=excluded.creado_por""",
                (semana_id, body.empleado_id, body.fecha, body.turno, body.estado, body.horario_id, uid)
            )
    return {"ok": True}


# ── Confirmar ──────────────────────────────────────────────────────────────────

def _ejecutar_confirmacion(conn, semana, uid: int):
    """Lógica central de confirmación. Recibe la fila de mozos_semana ya validada."""
    semana_id = semana["id"]
    detalles = conn.execute(
        "SELECT * FROM mozos_detalle WHERE semana_id=?", (semana_id,)
    ).fetchall()
    lunes_str = semana["semana_inicio"]
    domingo_str = str(date.fromisoformat(lunes_str) + timedelta(days=6))
    hoy_str = str(date.today())  # los horarios aplican solo a futuro (> hoy)
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

        dept_cfg = conn.execute(
            "SELECT horario_tm_id, horario_tn_id, horario_cortado_id FROM departamentos WHERE id=?",
            (semana["departamento_id"],)
        ).fetchone()
        horario_tm = dept_cfg["horario_tm_id"] if dept_cfg else None
        horario_tn = dept_cfg["horario_tn_id"] if dept_cfg else None
        horario_ct = dept_cfg["horario_cortado_id"] if dept_cfg else None

        estado_dia: dict = {}
        for det in detalles:
            key = (det["empleado_id"], det["fecha"])
            if key not in estado_dia:
                estado_dia[key] = {}
            estado_dia[key][det["turno"]] = det["estado"]
            if det["horario_id"]:
                estado_dia[key][f'{det["turno"]}_horario'] = det["horario_id"]
        dias_a_tocar = {(eid, f) for (eid, f) in estado_dia} | bloqueantes
        all_plan = conn.execute(
            f"""SELECT id, empleado_id, fecha FROM planificacion
                WHERE fecha >= ? AND fecha <= ? AND empleado_id IN ({ph})""",
            [lunes_str, domingo_str] + emp_ids
        ).fetchall()
        ids_borrar = [r["id"] for r in all_plan
                      if (r["empleado_id"], r["fecha"]) in dias_a_tocar
                      and (r["empleado_id"], r["fecha"]) not in bloqueantes
                      and r["fecha"] > hoy_str]
        if ids_borrar:
            ph2 = ",".join("?" * len(ids_borrar))
            conn.execute(f"DELETE FROM planificacion WHERE id IN ({ph2})", ids_borrar)
        lunes_dt = date.fromisoformat(lunes_str)
        dias_semana = [str(lunes_dt + timedelta(days=i)) for i in range(7)]
        for emp_id in emp_ids:
            for dia in dias_semana:
                if dia <= hoy_str:  # no modificar planificación de días ya transcurridos
                    continue
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
                    # "Horario primero": se usa el horario elegido y guardado por celda.
                    # Si falta (datos viejos con solo presencia), se cae al default del depto.
                    if tm_trabaja and tn_trabaja:
                        horario_id = (estados.get('TM_horario')
                                      or estados.get('TN_horario') or horario_ct)
                    elif tm_trabaja:
                        horario_id = estados.get('TM_horario') or horario_tm
                    else:
                        horario_id = estados.get('TN_horario') or horario_tn
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
        "UPDATE mozos_semana SET estado='confirmado', modificado_por=?, modificado_en=datetime('now','localtime') WHERE id=?",
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
                   VALUES (?,?,'borrador',?,datetime('now','localtime'))""",
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
            "UPDATE mozos_semana SET estado='borrador', modificado_por=?, modificado_en=datetime('now','localtime') WHERE id=?",
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


# ── Administración: usuarios_mozos ────────────────────────────────────────────

class UsuarioMozosIn(BaseModel):
    usuario_id: int
    departamento_id: int


@router.get("/usuarios-acceso")
def get_usuarios_acceso(_user=Depends(require_permiso("mozos", "ver"))):
    with db_session() as conn:
        rows = conn.execute(
            """SELECT um.id, um.usuario_id, um.departamento_id,
                      u.nombre AS usuario_nombre, u.email,
                      d.nombre AS departamento_nombre
               FROM usuarios_mozos um
               JOIN usuarios u ON u.id = um.usuario_id
               JOIN departamentos d ON d.id = um.departamento_id
               ORDER BY u.nombre, d.nombre"""
        ).fetchall()
    return [dict(r) for r in rows]


@router.post("/usuarios-acceso")
def add_usuario_acceso(body: UsuarioMozosIn,
                       _user=Depends(require_permiso("mozos", "editar"))):
    with db_session() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO usuarios_mozos (usuario_id, departamento_id) VALUES (?,?)",
                (body.usuario_id, body.departamento_id)
            )
            return {"id": cur.lastrowid}
        except Exception:
            raise HTTPException(409, "Ya existe ese acceso")


@router.delete("/usuarios-acceso/{uid}")
def delete_usuario_acceso(uid: int, _user=Depends(require_permiso("mozos", "editar"))):
    with db_session() as conn:
        conn.execute("DELETE FROM usuarios_mozos WHERE id=?", (uid,))
    return {"ok": True}
