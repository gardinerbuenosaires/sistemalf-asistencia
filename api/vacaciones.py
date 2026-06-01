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
                  saldos: dict, nov_map: dict, depth: int = 0) -> float:
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
    # Las vacaciones del período prev se toman durante el año siguiente (anio)
    meses_prev = nov_map.get((eid, anio), {})
    dias_v_prev = sum(meses_prev.values())

    if not saldo_prev and not meses_prev:
        # Sin novedades ni saldo_inicial: no hay datos del año anterior → sin arrastre
        return 0.0

    if saldo_prev:
        dias_corr_prev = saldo_prev["dias_correspondian"]
        dias_tom_prev  = saldo_prev["dias_tomados"] + dias_v_prev
    else:
        _, dias_formula_prev = _calcular_dias_formula(fecha_ingreso_str, prev)
        arrastre_prev  = _get_arrastre(eid, prev, fecha_ingreso_str, saldos, nov_map, depth + 1)
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
                      c.nombre AS cargo, cat.nombre AS categoria
               FROM empleados e
               LEFT JOIN cargos c ON c.id = e.cargo_id
               LEFT JOIN categorias cat ON cat.id = e.categoria_id
               WHERE e.activo = 1 AND e.tipo != 'acceso' ORDER BY e.apellido COLLATE NOCASE, e.nombre COLLATE NOCASE"""
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

    hoy = date.today()

    result = []
    for e in empleados:
        eid = e["id"]
        anios, dias_formula = _calcular_dias_formula(e["fecha_ingreso"], anio)

        saldo      = saldos.get((eid, anio))
        # Las vacaciones del período anio se toman durante el año siguiente
        meses_emp  = nov_map.get((eid, anio + 1), {})
        dias_v     = sum(meses_emp.values())

        # Cálculo auxiliar proporcional a hoy:
        #   - cuando fórmula = 0 (no estaba contratado al 31/12)
        #   - cuando primer año y no llegó a 180 días al 31/12 (la fórmula da un valor parcial)
        usa_proporcional = False
        sin_180_aun      = False
        dias_proporcional = 0.0
        primer_anio_sin_180 = not saldo and anios == 0 and 0 < dias_formula < 14
        if not saldo and (dias_formula == 0 or primer_anio_sin_180) and e["fecha_ingreso"]:
            try:
                fi = date.fromisoformat(e["fecha_ingreso"])
                if fi <= hoy:
                    dias_trabajados = (hoy - fi).days + 1
                    anios_aux = math.trunc(dias_trabajados / 365.25)
                    if anios_aux >= 20:   dias_proporcional = 35.0
                    elif anios_aux >= 10: dias_proporcional = 28.0
                    elif anios_aux >= 5:  dias_proporcional = 21.0
                    elif anios_aux >= 1:  dias_proporcional = 14.0
                    elif dias_trabajados >= 180: dias_proporcional = 14.0
                    else: dias_proporcional = float(math.floor(dias_trabajados / 20 + 0.5))
                    if dias_proporcional > 0:
                        usa_proporcional = True
                        sin_180_aun = dias_trabajados < 180
            except ValueError:
                pass

        if saldo:
            dias_correspondian = saldo["dias_correspondian"]
            dias_tomados       = saldo["dias_tomados"] + dias_v
            arrastre           = 0.0
        else:
            arrastre           = _get_arrastre(eid, anio, e["fecha_ingreso"], saldos, nov_map)
            dias_correspondian = (dias_proporcional if usa_proporcional else dias_formula) + arrastre
            dias_tomados       = dias_v

        dias_restan = round(dias_correspondian - dias_tomados, 1)

        result.append({
            "id":                  eid,
            "apellido":            e["apellido"],
            "nombre":              e["nombre"],
            "cargo":               e["cargo"],
            "categoria":           e["categoria"],
            "fecha_ingreso":       e["fecha_ingreso"],
            "anios_antiguedad":    anios,
            "dias_formula":        dias_formula,
            "dias_proporcional":   dias_proporcional,
            "usa_proporcional":    usa_proporcional,
            "sin_180_aun":         sin_180_aun,
            "arrastre":            arrastre,
            "dias_correspondian":  dias_correspondian,
            "dias_tomados":        dias_tomados,
            "dias_restan":         dias_restan,
            "meses":               {k: v for k, v in meses_emp.items()},
            "tiene_saldo_inicial": saldo is not None,
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
               WHERE e.activo = 1 AND e.tipo != 'acceso' ORDER BY e.apellido COLLATE NOCASE, e.nombre COLLATE NOCASE"""
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
            "SELECT fecha_ingreso FROM empleados WHERE id=?", (empleado_id,)
        ).fetchone()
        if not emp:
            raise HTTPException(404, "Empleado no encontrado")

        saldos = {
            (r["empleado_id"], r["anio"]): dict(r)
            for r in conn.execute("SELECT * FROM vacaciones_saldo_inicial").fetchall()
        }

        nov_rows = conn.execute("""
            SELECT CAST(strftime('%Y', fecha) AS INTEGER) AS anio,
                   SUM(CASE WHEN bloque = 0 THEN 1.0 ELSE 0.5 END) AS dias
            FROM novedades
            WHERE tipo = 'V' AND empleado_id = ?
            GROUP BY anio
        """, (empleado_id,)).fetchall()

    nov_map: dict = {}
    for r in nov_rows:
        # novedades del año N+1 corresponden al período N
        period = r["anio"] - 1
        nov_map[period] = nov_map.get(period, 0.0) + r["dias"]

    _, dias_formula = _calcular_dias_formula(emp["fecha_ingreso"], anio)
    saldo = saldos.get((empleado_id, anio))
    dias_v = nov_map.get(anio, 0.0)

    if saldo:
        dias_correspondian = saldo["dias_correspondian"]
        dias_tomados       = saldo["dias_tomados"] + dias_v
        arrastre           = 0.0
    else:
        arrastre           = _get_arrastre(empleado_id, anio, emp["fecha_ingreso"], saldos,
                                           {(empleado_id, anio + 1): {"01": dias_v} if dias_v else {}})
        dias_correspondian = dias_formula + arrastre
        dias_tomados       = dias_v

    dias_restan = round(dias_correspondian - dias_tomados, 1)
    return {
        "empleado_id":     empleado_id,
        "anio":            anio,
        "dias_correspondian": dias_correspondian,
        "dias_tomados":    dias_tomados,
        "dias_restan":     dias_restan,
    }


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
