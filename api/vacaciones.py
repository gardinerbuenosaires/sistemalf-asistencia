import math
from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from db.database import db_session
from auth.core import require_permiso

router = APIRouter(prefix="/api/vacaciones", tags=["vacaciones"])


def _calcular_dias_formula(fecha_ingreso_str: str | None, anio: int) -> tuple[int, float]:
    """
    Implementa exactamente las fórmulas del Excel de la contadora:
      D = TRUNCAR((ref - ingreso) / 365.25, 0)          → años antigüedad
      F = tramo según D (14/21/28/35/0)
      G = si F==0: (si días>=180 → 14, si no → REDONDEAR(días/20, 0))
    Fecha de referencia: 31/12 del período (anio).
    """
    if not fecha_ingreso_str:
        return 0, 0.0
    try:
        fecha_ingreso = date.fromisoformat(fecha_ingreso_str)
    except ValueError:
        return 0, 0.0

    ref = date(anio, 12, 31)
    if fecha_ingreso > ref:
        return 0, 0.0

    days = (ref - fecha_ingreso).days                    # Excel: P2 - C
    anios = math.trunc(days / 365.25)                    # TRUNCAR(days/365.25, 0)

    if anios >= 20:
        tramo = 35.0
    elif anios >= 10:
        tramo = 28.0
    elif anios >= 5:
        tramo = 21.0
    elif anios >= 1:
        tramo = 14.0
    else:
        dias_empleo = days + 1                           # Excel: P2-(C-1) = days+1
        if dias_empleo >= 180:
            tramo = 14.0
        else:
            # REDONDEAR(x, 0): round half away from zero → math.floor(x + 0.5) para positivos
            tramo = float(math.floor(dias_empleo / 20 + 0.5))

    return anios, tramo


def _get_arrastre(eid: int, anio: int, fecha_ingreso_str: str | None,
                  saldos: dict, nov_map: dict, depth: int = 0,
                  vac_dias_jubilacion: float | None = None,
                  fecha_recontratacion_anio: int | None = None) -> float:
    """
    Calcula recursivamente el sobrante del año anterior para usarlo como arrastre.
    Se detiene si:
      - No hay datos previos (pre-sistema sin saldo_inicial)
      - El empleado no estaba contratado ese año
      - depth > 15 (protección anti-loop)
    """
    if depth > 15:
        return 0.0

    prev = anio - 1

    # El empleado no existía en el año anterior
    if fecha_ingreso_str:
        try:
            if date.fromisoformat(fecha_ingreso_str).year > prev:
                return 0.0
        except ValueError:
            return 0.0
    else:
        return 0.0

    saldo_prev = saldos.get((eid, prev))
    # Consumo del período prev: dic de prev + ene-nov de anio (prev+1)
    dic_prev    = {k: v for k, v in nov_map.get((eid, prev), {}).items() if k == "12"}
    resto_anio  = {k: v for k, v in nov_map.get((eid, anio), {}).items() if k < "12"}
    meses_prev  = {**dic_prev, **resto_anio}
    dias_v_prev = sum(meses_prev.values())

    if not saldo_prev and not meses_prev:
        # Sin novedades ni saldo_inicial: no hay datos del año anterior → sin arrastre
        return 0.0

    if saldo_prev:
        dias_corr_prev = saldo_prev["dias_correspondian"]
        dias_tom_prev  = saldo_prev["dias_tomados"] + dias_v_prev
    else:
        if vac_dias_jubilacion and fecha_recontratacion_anio and prev >= fecha_recontratacion_anio:
            dias_formula_prev = float(vac_dias_jubilacion)
        else:
            _, dias_formula_prev = _calcular_dias_formula(fecha_ingreso_str, prev)
        arrastre_prev  = _get_arrastre(eid, prev, fecha_ingreso_str, saldos, nov_map, depth + 1,
                                       vac_dias_jubilacion, fecha_recontratacion_anio)
        dias_corr_prev = dias_formula_prev + arrastre_prev
        dias_tom_prev  = dias_v_prev

    return max(0.0, round(dias_corr_prev - dias_tom_prev, 1))


# ── Modelos ────────────────────────────────────────────────────────────────────

class SaldoInicialIn(BaseModel):
    empleado_id: int
    anio: int
    dias_correspondian: float
    dias_tomados: float


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("")
def get_vacaciones(anio: int = 0, _user=Depends(require_permiso("vacaciones", "ver"))):
    """Devuelve todos los empleados activos con su cálculo de vacaciones para el año."""
    if not anio:
        anio = date.today().year - 1

    with db_session() as conn:
        empleados = conn.execute(
            """SELECT e.id, e.nombre, e.apellido, e.fecha_ingreso,
                      e.fecha_recontratacion, e.vac_dias_jubilacion,
                      c.nombre AS cargo, cat.nombre AS categoria
               FROM empleados e
               LEFT JOIN cargos c ON c.id = e.cargo_id
               LEFT JOIN categorias cat ON cat.id = e.categoria_id
               WHERE e.activo = 1 AND e.tipo NOT IN ('acceso','parking') ORDER BY e.apellido COLLATE NOCASE, e.nombre COLLATE NOCASE"""
        ).fetchall()

        # Todos los saldos_iniciales (cualquier año)
        saldos: dict = {}
        for r in conn.execute("SELECT * FROM vacaciones_saldo_inicial").fetchall():
            saldos[(r["empleado_id"], r["anio"])] = dict(r)

        # Novedades V agrupadas por empleado, año y mes (todos los años)
        nov_rows = conn.execute("""
            SELECT empleado_id,
                   CAST(strftime('%Y', fecha) AS INTEGER) AS anio,
                   strftime('%m', fecha)                  AS mes,
                   SUM(CASE WHEN bloque = 0 THEN 1.0 ELSE 0.5 END) AS dias
            FROM novedades
            WHERE tipo = 'V'
            GROUP BY empleado_id, anio, mes
        """).fetchall()

    nov_map: dict = {}
    for r in nov_rows:
        key = (r["empleado_id"], r["anio"])
        if key not in nov_map:
            nov_map[key] = {}
        nov_map[key][r["mes"]] = r["dias"]

    result = []
    for e in empleados:
        eid = e["id"]
        vac_jub    = e["vac_dias_jubilacion"]
        recont_str = e["fecha_recontratacion"]
        anio_recont = date.fromisoformat(recont_str).year if recont_str else None
        es_jubilacion = bool(vac_jub and anio_recont and anio >= anio_recont)

        if es_jubilacion:
            anios        = 0
            dias_formula = float(vac_jub)
        else:
            anios, dias_formula = _calcular_dias_formula(e["fecha_ingreso"], anio)

        saldo      = saldos.get((eid, anio))
        # Consumo del período anio: dic de anio + ene-nov de anio+1
        dic_anio   = {k: v for k, v in nov_map.get((eid, anio), {}).items() if k == "12"}
        resto_sig  = {k: v for k, v in nov_map.get((eid, anio + 1), {}).items() if k < "12"}
        meses_emp  = {**dic_anio, **resto_sig}
        dias_v     = sum(meses_emp.values())

        if saldo:
            dias_correspondian = saldo["dias_correspondian"]
            dias_tomados       = saldo["dias_tomados"] + dias_v
            arrastre           = 0.0
        else:
            arrastre           = _get_arrastre(eid, anio, e["fecha_ingreso"], saldos, nov_map,
                                               vac_dias_jubilacion=vac_jub,
                                               fecha_recontratacion_anio=anio_recont)
            dias_correspondian = dias_formula + arrastre
            dias_tomados       = dias_v

        dias_restan = round(dias_correspondian - dias_tomados, 1)

        result.append({
            "id":                   eid,
            "apellido":             e["apellido"],
            "nombre":               e["nombre"],
            "cargo":                e["cargo"],
            "categoria":            e["categoria"],
            "fecha_ingreso":        e["fecha_ingreso"],
            "fecha_recontratacion": recont_str,
            "es_jubilacion":        es_jubilacion,
            "anios_antiguedad":     anios,
            "dias_formula":         dias_formula,
            "arrastre":             arrastre,
            "dias_correspondian":   dias_correspondian,
            "dias_tomados":         dias_tomados,
            "dias_restan":          dias_restan,
            "meses":                {k: v for k, v in meses_emp.items()},
            "tiene_saldo_inicial":  saldo is not None,
        })

    return result


@router.get("/saldo-inicial")
def get_saldo_inicial(anio: int, _user=Depends(require_permiso("vacaciones", "carga_inicial"))):
    """Devuelve todos los empleados activos con sus datos de saldo inicial (para edición)."""
    with db_session() as conn:
        empleados = conn.execute(
            """SELECT e.id, e.nombre, e.apellido, e.fecha_ingreso, c.nombre AS cargo
               FROM empleados e
               LEFT JOIN cargos c ON c.id = e.cargo_id
               WHERE e.activo = 1 AND e.tipo NOT IN ('acceso','parking') ORDER BY e.apellido COLLATE NOCASE, e.nombre COLLATE NOCASE"""
        ).fetchall()
        saldos = {
            r["empleado_id"]: dict(r)
            for r in conn.execute(
                "SELECT empleado_id, dias_correspondian, dias_tomados FROM vacaciones_saldo_inicial WHERE anio=?",
                (anio,)
            ).fetchall()
        }

    result = []
    for e in empleados:
        _, dias_formula = _calcular_dias_formula(e["fecha_ingreso"], anio)
        saldo = saldos.get(e["id"])
        result.append({
            "empleado_id":      e["id"],
            "apellido":         e["apellido"],
            "nombre":           e["nombre"],
            "cargo":            e["cargo"],
            "fecha_ingreso":    e["fecha_ingreso"],
            "dias_formula":     dias_formula,
            "dias_correspondian": saldo["dias_correspondian"] if saldo else dias_formula,
            "dias_tomados":     saldo["dias_tomados"] if saldo else 0.0,
            "tiene_saldo":      saldo is not None,
        })
    return result


@router.get("/saldo-empleado")
def get_saldo_empleado(empleado_id: int, anio: int = 0,
                       _user=Depends(require_permiso("asistencia", "corregir"))):
    """Saldo de vacaciones de un empleado para un período (anio = año del período vacacional)."""
    if not anio:
        anio = date.today().year - 1

    with db_session() as conn:
        emp = conn.execute(
            "SELECT fecha_ingreso, fecha_recontratacion, vac_dias_jubilacion FROM empleados WHERE id=?",
            (empleado_id,)
        ).fetchone()
        if not emp:
            raise HTTPException(404, "Empleado no encontrado")

        saldos = {
            (r["empleado_id"], r["anio"]): dict(r)
            for r in conn.execute("SELECT * FROM vacaciones_saldo_inicial").fetchall()
        }

        nov_rows = conn.execute("""
            SELECT CAST(strftime('%Y', fecha) AS INTEGER) AS anio,
                   strftime('%m', fecha) AS mes,
                   SUM(CASE WHEN bloque = 0 THEN 1.0 ELSE 0.5 END) AS dias
            FROM novedades
            WHERE tipo = 'V' AND empleado_id = ?
            GROUP BY anio, mes
        """, (empleado_id,)).fetchall()

        vp_row = conn.execute("""
            SELECT COALESCE(SUM(dias), 0) AS dias FROM vacaciones_pagadas
            WHERE empleado_id=? AND (
                periodo = ? OR (
                    CAST(substr(periodo, 1, 4) AS INTEGER) = ? AND
                    substr(periodo, 6, 2) < '12'
                )
            )
        """, (empleado_id, f"{anio}-12", anio + 1)).fetchone()
        dias_vp = float(vp_row["dias"]) if vp_row else 0.0

    nov_map: dict = {}
    for r in nov_rows:
        key = (empleado_id, r["anio"])
        if key not in nov_map:
            nov_map[key] = {}
        nov_map[key][r["mes"]] = r["dias"]

    vac_jub     = emp["vac_dias_jubilacion"]
    recont_str  = emp["fecha_recontratacion"]
    anio_recont = date.fromisoformat(recont_str).year if recont_str else None
    es_jubilacion = bool(vac_jub and anio_recont and anio >= anio_recont)

    if es_jubilacion:
        _, dias_formula = 0, float(vac_jub)
    else:
        _, dias_formula = _calcular_dias_formula(emp["fecha_ingreso"], anio)

    saldo = saldos.get((empleado_id, anio))
    # Consumo del período anio: dic de anio + ene-nov de anio+1
    dic_anio  = {k: v for k, v in nov_map.get((empleado_id, anio), {}).items() if k == "12"}
    resto_sig = {k: v for k, v in nov_map.get((empleado_id, anio + 1), {}).items() if k < "12"}
    dias_v = sum(dic_anio.values()) + sum(resto_sig.values())

    if saldo:
        dias_correspondian = saldo["dias_correspondian"]
        dias_tomados       = saldo["dias_tomados"] + dias_v + dias_vp
        arrastre           = 0.0
    else:
        arrastre           = _get_arrastre(empleado_id, anio, emp["fecha_ingreso"], saldos, nov_map,
                                           vac_dias_jubilacion=vac_jub,
                                           fecha_recontratacion_anio=anio_recont)
        dias_correspondian = dias_formula + arrastre
        dias_tomados       = dias_v + dias_vp

    dias_restan = round(dias_correspondian - dias_tomados, 1)
    return {
        "empleado_id":     empleado_id,
        "anio":            anio,
        "dias_correspondian": dias_correspondian,
        "dias_tomados":    dias_tomados,
        "dias_restan":     dias_restan,
    }


@router.get("/vacaciones-pagadas")
def get_vp_periodo(periodo: str, _user=Depends(require_permiso("vacaciones", "ver"))):
    """Devuelve todos los registros VP del período (formato YYYY-MM)."""
    with db_session() as conn:
        rows = conn.execute(
            "SELECT empleado_id, dias FROM vacaciones_pagadas WHERE periodo=?", (periodo,)
        ).fetchall()
    return [dict(r) for r in rows]


class VpIn(BaseModel):
    dias: float


@router.put("/vacaciones-pagadas/{empleado_id}/{periodo}", status_code=200)
def upsert_vp(empleado_id: int, periodo: str, data: VpIn,
              _user=Depends(require_permiso("vacaciones", "editar"))):
    """Inserta o actualiza los días VP de un empleado para un período."""
    with db_session() as conn:
        if not conn.execute("SELECT id FROM empleados WHERE id=?", (empleado_id,)).fetchone():
            raise HTTPException(404, "Empleado no encontrado")
        if data.dias <= 0:
            conn.execute(
                "DELETE FROM vacaciones_pagadas WHERE empleado_id=? AND periodo=?",
                (empleado_id, periodo)
            )
        else:
            conn.execute("""
                INSERT INTO vacaciones_pagadas (empleado_id, periodo, dias)
                VALUES (?, ?, ?)
                ON CONFLICT(empleado_id, periodo) DO UPDATE SET dias=excluded.dias
            """, (empleado_id, periodo, data.dias))
    return {"ok": True}


@router.post("/saldo-inicial", status_code=200)
def upsert_saldo_inicial(data: SaldoInicialIn, _user=Depends(require_permiso("vacaciones", "carga_inicial"))):
    """Inserta o actualiza el saldo inicial de un empleado para un año."""
    with db_session() as conn:
        if not conn.execute("SELECT id FROM empleados WHERE id=?", (data.empleado_id,)).fetchone():
            raise HTTPException(404, "Empleado no encontrado")
        conn.execute("""
            INSERT INTO vacaciones_saldo_inicial (empleado_id, anio, dias_correspondian, dias_tomados)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(empleado_id, anio) DO UPDATE SET
                dias_correspondian = excluded.dias_correspondian,
                dias_tomados       = excluded.dias_tomados
        """, (data.empleado_id, data.anio, data.dias_correspondian, data.dias_tomados))
    return {"ok": True}
