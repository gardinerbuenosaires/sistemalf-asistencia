import json
from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from db.database import db_session
from auth.core import require_permiso

router = APIRouter(prefix="/api/premios", tags=["premios"])


# ── Modelos ────────────────────────────────────────────────────────────────────

class ParametroUpdate(BaseModel):
    clave: str
    valor: str


class EvaluacionUpdate(BaseModel):
    bpm: Optional[str] = None
    desempenio: Optional[str] = None
    monto_base: Optional[int] = None


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_params(conn) -> dict:
    rows = conn.execute("SELECT clave, valor, tipo FROM premios_parametros").fetchall()
    result = {}
    for r in rows:
        try:
            result[r["clave"]] = float(r["valor"]) if r["tipo"] == "porcentaje" else int(r["valor"])
        except (ValueError, TypeError):
            result[r["clave"]] = r["valor"]
    return result


def _calcular(ev: dict, params: dict) -> dict:
    base = ev["monto_base"] or params["monto_base"]
    no_fichadas = ev["no_fichadas"]
    dias_ausente = ev["dias_ausente"]
    dias_suspension = ev["dias_suspension"]
    dias_enfermo = ev["dias_enfermo"]
    dias_vacacion = ev["dias_vacacion"]
    minutos_retardo = ev["minutos_retardo"]
    bpm = ev["bpm"]

    desglose = {
        "monto_base": base,
        "descalificado": False,
        "motivo_descalificacion": None,
        "minutos_retardo_sistema": minutos_retardo,
        "minutos_ajuste_nf": 0,
        "minutos_efectivos": 0,
        "puntualidad_status": None,
        "deduccion_puntualidad": 0,
        "deduccion_bpm": 0,
        "deduccion_vacaciones": 0,
        "valor_bruto": 0,
        "valor_final": 0,
    }

    # Descalificadores absolutos
    if no_fichadas >= params["limite_no_fichadas"]:
        desglose.update({"descalificado": True, "motivo_descalificacion": "no_fichadas"})
        return _finalizar(desglose, 0, params)

    if dias_ausente > 0:
        desglose.update({"descalificado": True, "motivo_descalificacion": "ausente"})
        return _finalizar(desglose, 0, params)

    if dias_suspension > 0:
        desglose.update({"descalificado": True, "motivo_descalificacion": "suspension"})
        return _finalizar(desglose, 0, params)

    if dias_enfermo > params["limite_enfermedad_dias"]:
        desglose.update({"descalificado": True, "motivo_descalificacion": "enfermedad"})
        return _finalizar(desglose, 0, params)

    # Retardo efectivo
    ajuste_nf = no_fichadas * params["min_por_no_fichada"]
    minutos_efectivos = minutos_retardo + ajuste_nf
    desglose["minutos_ajuste_nf"] = ajuste_nf
    desglose["minutos_efectivos"] = minutos_efectivos

    # Penalidad por puntualidad
    umbral_ok = params["umbral_puntualidad_ok_min"]
    umbral_total = params["umbral_puntualidad_total_min"]

    if minutos_efectivos == 0:
        status = "PERFECTA"
        deduccion_puntualidad = 0
    elif minutos_efectivos < umbral_ok:
        status = "OK"
        deduccion_puntualidad = 0
    elif minutos_efectivos < umbral_total:
        status = "PARCIAL"
        deduccion_puntualidad = round(base * params["pct_descuento_puntualidad"] / 100)
    else:
        status = "PIERDE"
        desglose.update({"puntualidad_status": status, "descalificado": True, "motivo_descalificacion": "puntualidad"})
        return _finalizar(desglose, 0, params)

    desglose["puntualidad_status"] = status
    desglose["deduccion_puntualidad"] = deduccion_puntualidad

    # BPM
    deduccion_bpm = round(base * params["pct_descuento_bpm"] / 100) if bpm == "NO CONFORME" else 0
    desglose["deduccion_bpm"] = deduccion_bpm

    # Vacaciones (proporcional)
    deduccion_vacaciones = 0
    if dias_vacacion > 0:
        deduccion_vacaciones = round((base / params["dias_base_vacaciones"]) * dias_vacacion)
    desglose["deduccion_vacaciones"] = deduccion_vacaciones

    valor_bruto = max(0, base - deduccion_puntualidad - deduccion_bpm - deduccion_vacaciones)
    return _finalizar(desglose, valor_bruto, params)


def _finalizar(desglose: dict, valor_bruto: int, params: dict) -> dict:
    redondeo = int(params.get("redondeo", 0))
    if redondeo > 0 and valor_bruto > 0:
        valor_final = round(valor_bruto / redondeo) * redondeo
    else:
        valor_final = valor_bruto
    desglose["valor_bruto"] = valor_bruto
    desglose["valor_calculado"] = valor_bruto
    desglose["valor_final"] = valor_final
    return desglose


def _acumular_periodo(conn, empleado_id: int, periodo: str) -> dict:
    """Extrae los datos de asistencia del período para un empleado."""
    anio, mes = periodo.split("-")
    fecha_desde = f"{anio}-{mes}-01"
    # Último día del mes
    if mes == "12":
        fecha_hasta = f"{int(anio)+1}-01-01"
    else:
        fecha_hasta = f"{anio}-{int(mes)+1:02d}-01"

    # Tardanzas y ausencias de resultados_dia (excluye francos)
    r = conn.execute("""
        SELECT
            COALESCE(SUM(COALESCE(b1_minutos_tarde,0) + COALESCE(b2_minutos_tarde,0)), 0) AS minutos_retardo,
            COUNT(CASE WHEN estado='nf'     THEN 1 END) AS no_fichadas,
            COUNT(CASE WHEN estado='ausente' THEN 1 END) AS dias_ausente
        FROM resultados_dia
        WHERE empleado_id=? AND fecha>=? AND fecha<? AND es_franco=0
    """, (empleado_id, fecha_desde, fecha_hasta)).fetchone()

    # Novedades del período
    n = conn.execute("""
        SELECT
            COUNT(DISTINCT fecha) FILTER (WHERE tipo='V')              AS dias_vacacion,
            COUNT(DISTINCT fecha) FILTER (WHERE tipo IN ('E','ILT'))   AS dias_enfermo,
            COUNT(DISTINCT fecha) FILTER (WHERE tipo='S')              AS dias_suspension
        FROM novedades
        WHERE empleado_id=? AND fecha>=? AND fecha<?
    """, (empleado_id, fecha_desde, fecha_hasta)).fetchone()

    return {
        "minutos_retardo": r["minutos_retardo"],
        "no_fichadas":     r["no_fichadas"],
        "dias_ausente":    r["dias_ausente"],
        "dias_vacacion":   n["dias_vacacion"],
        "dias_enfermo":    n["dias_enfermo"],
        "dias_suspension": n["dias_suspension"],
    }


# ── Parámetros ─────────────────────────────────────────────────────────────────

@router.get("/parametros")
def get_parametros(_user=Depends(require_permiso("premios", "ver"))):
    with db_session() as conn:
        rows = conn.execute(
            "SELECT clave, valor, tipo, descripcion FROM premios_parametros ORDER BY clave"
        ).fetchall()
    return [dict(r) for r in rows]


@router.put("/parametros")
def update_parametros(updates: list[ParametroUpdate], _user=Depends(require_permiso("premios", "editar"))):
    with db_session() as conn:
        for u in updates:
            conn.execute(
                "UPDATE premios_parametros SET valor=? WHERE clave=?",
                (u.valor.strip(), u.clave)
            )
    return {"ok": True}


# ── Helpers internos ──────────────────────────────────────────────────────────

def _diagnostico(conn, fecha_desde: str, dias_min: int) -> dict:
    # Incluidos: cargo aplica + (sin fecha ingreso O cumple antigüedad)
    # sin_fecha_ingreso: advertencia, pero SON incluidos
    # sin_antiguedad: excluidos reales por fecha de ingreso muy reciente
    row = conn.execute("""
        SELECT
            COUNT(*)                                                                        AS total,
            COUNT(*) FILTER (WHERE e.cargo IS NULL OR e.cargo = '')                        AS sin_cargo,
            COUNT(*) FILTER (WHERE e.cargo IS NOT NULL AND e.cargo != ''
                               AND c.id IS NULL)                                            AS cargo_no_catalogado,
            COUNT(*) FILTER (WHERE c.id IS NOT NULL AND c.aplica_premio = 0)               AS cargo_sin_premio,
            COUNT(*) FILTER (WHERE c.aplica_premio = 1
                               AND (e.fecha_ingreso IS NULL OR e.fecha_ingreso = ''))       AS sin_fecha_ingreso,
            COUNT(*) FILTER (WHERE c.aplica_premio = 1
                               AND e.fecha_ingreso IS NOT NULL AND e.fecha_ingreso != ''
                               AND date(e.fecha_ingreso, '+' || ? || ' days') > ?)         AS sin_antiguedad,
            COUNT(*) FILTER (WHERE c.aplica_premio = 1
                               AND e.fecha_ingreso IS NOT NULL AND e.fecha_ingreso != ''
                               AND date(e.fecha_ingreso, '+' || ? || ' days') <= ?)        AS incluidos
        FROM empleados e
        LEFT JOIN cargos c ON c.nombre = e.cargo
        WHERE e.activo = 1 AND e.tipo != 'acceso'
    """, (dias_min, fecha_desde, dias_min, fecha_desde)).fetchone()
    return dict(row)


# ── Evaluaciones ───────────────────────────────────────────────────────────────

@router.get("/{periodo}")
def get_evaluaciones(periodo: str, _user=Depends(require_permiso("premios", "ver"))):
    with db_session() as conn:
        import calendar as _cal
        anio, mes = periodo.split("-")
        anio_i, mes_i = int(anio), int(mes)
        f0 = f"{anio_i:04d}-{mes_i:02d}-01"
        f1 = f"{anio_i:04d}-{mes_i:02d}-{_cal.monthrange(anio_i, mes_i)[1]:02d}"

        rows = conn.execute("""
            WITH ult AS (
                SELECT p.empleado_id, h.tipo, t.nombre AS turno_nombre,
                       ROW_NUMBER() OVER (
                           PARTITION BY p.empleado_id ORDER BY p.fecha DESC
                       ) AS rn
                FROM planificacion p
                JOIN horarios h ON h.id = p.horario_id
                LEFT JOIN turnos t ON t.id = h.turno_id
                WHERE p.fecha >= ? AND p.fecha <= ? AND p.horario_id IS NOT NULL
            )
            SELECT pe.*, e.apellido, e.nombre, e.cargo, e.departamento, e.categoria,
                   CASE
                       WHEN u.tipo = 'cortado' THEN 'CO'
                       WHEN lower(u.turno_nombre) LIKE '%noche%'
                         OR lower(u.turno_nombre) LIKE '%madrugada%' THEN 'TN'
                       WHEN u.tipo IS NOT NULL THEN 'TM'
                       ELSE NULL
                   END AS grupo
            FROM premios_evaluacion pe
            JOIN empleados e ON e.id = pe.empleado_id
            LEFT JOIN ult u ON u.empleado_id = e.id AND u.rn = 1
            WHERE pe.periodo=?
            ORDER BY
                CASE
                    WHEN u.tipo = 'cortado' THEN 3
                    WHEN lower(u.turno_nombre) LIKE '%noche%'
                      OR lower(u.turno_nombre) LIKE '%madrugada%' THEN 2
                    WHEN u.tipo IS NOT NULL THEN 1
                    ELSE 4
                END,
                e.apellido, e.nombre
        """, (f0, f1, periodo)).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        if d.get("desglose_json"):
            d["desglose"] = json.loads(d["desglose_json"])
        del d["desglose_json"]
        result.append(d)
    return result


@router.get("/{periodo}/diagnostico")
def diagnostico_periodo(periodo: str, _user=Depends(require_permiso("premios", "ver"))):
    with db_session() as conn:
        params = _get_params(conn)
        anio, mes = periodo.split("-")
        fecha_desde = f"{anio}-{mes}-01"
        dias_min = int(params.get("dias_minimos_antiguedad", 90))
        return _diagnostico(conn, fecha_desde, dias_min)


@router.post("/{periodo}/generar")
def generar_evaluaciones(periodo: str, _user=Depends(require_permiso("premios", "editar"))):
    """Genera o actualiza filas para todos los empleados activos del período."""
    with db_session() as conn:
        params = _get_params(conn)
        anio, mes = periodo.split("-")
        fecha_desde = f"{anio}-{mes}-01"
        dias_min = int(params.get("dias_minimos_antiguedad", 90))
        diag = _diagnostico(conn, fecha_desde, dias_min)
        empleados = conn.execute("""
            SELECT e.id FROM empleados e
            LEFT JOIN cargos c ON c.nombre = e.cargo
            WHERE e.activo=1 AND e.tipo != 'acceso'
              AND c.aplica_premio = 1
              AND e.fecha_ingreso IS NOT NULL AND e.fecha_ingreso != ''
              AND date(e.fecha_ingreso, '+' || ? || ' days') <= ?
        """, (dias_min, fecha_desde)).fetchall()

        # Eliminar filas del período para empleados que ya no califican
        ids_calificados = [emp["id"] for emp in empleados]
        if ids_calificados:
            placeholders = ",".join("?" * len(ids_calificados))
            conn.execute(
                f"DELETE FROM premios_evaluacion WHERE periodo=? AND empleado_id NOT IN ({placeholders})",
                [periodo] + ids_calificados
            )
        else:
            conn.execute("DELETE FROM premios_evaluacion WHERE periodo=?", (periodo,))

        generados = 0
        for emp in empleados:
            eid = emp["id"]
            datos = _acumular_periodo(conn, eid, periodo)
            desglose = _calcular({**datos, "bpm": "OK", "desempenio": None, "monto_base": params["monto_base"]}, params)

            conn.execute("""
                INSERT INTO premios_evaluacion
                    (empleado_id, periodo, minutos_retardo, no_fichadas, dias_vacacion,
                     dias_enfermo, dias_suspension, dias_ausente, bpm, monto_base,
                     desglose_json, valor_calculado, valor_final)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(empleado_id, periodo) DO UPDATE SET
                    minutos_retardo=excluded.minutos_retardo,
                    no_fichadas=excluded.no_fichadas,
                    dias_vacacion=excluded.dias_vacacion,
                    dias_enfermo=excluded.dias_enfermo,
                    dias_suspension=excluded.dias_suspension,
                    dias_ausente=excluded.dias_ausente,
                    monto_base=excluded.monto_base,
                    desglose_json=excluded.desglose_json,
                    valor_calculado=excluded.valor_calculado,
                    valor_final=excluded.valor_final,
                    modificado_en=datetime('now','localtime')
            """, (
                eid, periodo,
                datos["minutos_retardo"], datos["no_fichadas"], datos["dias_vacacion"],
                datos["dias_enfermo"], datos["dias_suspension"], datos["dias_ausente"],
                "", params["monto_base"],
                json.dumps(desglose), desglose["valor_calculado"], desglose["valor_final"]
            ))
            generados += 1

    return {"ok": True, "generados": generados, "diagnostico": diag}


@router.put("/evaluacion/{ev_id}")
def update_evaluacion(ev_id: int, data: EvaluacionUpdate, _user=Depends(require_permiso("premios", "editar"))):
    """Actualiza BPM, desempeño o monto base y recalcula."""
    with db_session() as conn:
        ev = conn.execute("SELECT * FROM premios_evaluacion WHERE id=?", (ev_id,)).fetchone()
        if not ev:
            raise HTTPException(404, "Evaluación no encontrada")
        ev = dict(ev)

        if data.bpm is not None:
            ev["bpm"] = data.bpm
        if data.desempenio is not None:
            ev["desempenio"] = data.desempenio
        if data.monto_base is not None:
            ev["monto_base"] = data.monto_base

        params = _get_params(conn)
        desglose = _calcular(ev, params)

        conn.execute("""
            UPDATE premios_evaluacion SET
                bpm=?, desempenio=?, monto_base=?,
                desglose_json=?, valor_calculado=?, valor_final=?,
                modificado_en=datetime('now','localtime')
            WHERE id=?
        """, (
            ev["bpm"], ev["desempenio"], ev["monto_base"],
            json.dumps(desglose), desglose["valor_calculado"], desglose["valor_final"],
            ev_id
        ))

        ev.update({"desglose": desglose, "valor_calculado": desglose["valor_calculado"], "valor_final": desglose["valor_final"]})
    return ev


@router.post("/{periodo}/recalcular")
def recalcular_periodo(periodo: str, _user=Depends(require_permiso("premios", "editar"))):
    """Recalcula todas las evaluaciones del período con los parámetros actuales."""
    with db_session() as conn:
        params = _get_params(conn)
        rows = conn.execute(
            "SELECT * FROM premios_evaluacion WHERE periodo=?", (periodo,)
        ).fetchall()
        for r in rows:
            ev = dict(r)
            desglose = _calcular(ev, params)
            conn.execute("""
                UPDATE premios_evaluacion SET
                    desglose_json=?, valor_calculado=?, valor_final=?,
                    modificado_en=datetime('now','localtime')
                WHERE id=?
            """, (json.dumps(desglose), desglose["valor_calculado"], desglose["valor_final"], ev["id"]))
    return {"ok": True, "recalculados": len(rows)}
