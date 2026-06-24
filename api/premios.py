import json
from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from db.database import db_session
from auth.core import require_permiso, tiene_permiso, verify_password
from api.asistencia_mensual import get_control_estados_batch

router = APIRouter(prefix="/api/premios", tags=["premios"])


# ── Modelos ────────────────────────────────────────────────────────────────────

class ParametroUpdate(BaseModel):
    clave: str
    valor: str


class EvaluacionUpdate(BaseModel):
    bpm: Optional[str] = None
    desempenio: Optional[str] = None
    monto_base: Optional[int] = None
    tolerar_nf: Optional[bool] = None
    tolerar_e: Optional[bool] = None
    tolerar_retardo: Optional[bool] = None
    anular_premio: Optional[bool] = None


class PremiosPeriodoIn(BaseModel):
    anio: int
    mes:  int


class PremiosReabrirIn(BaseModel):
    password: str


# ── Helpers de bloqueo de período ─────────────────────────────────────────────

def es_premios_cerrado(conn, anio: int, mes: int) -> bool:
    return conn.execute(
        "SELECT 1 FROM premios_periodos_cerrados WHERE anio=? AND mes=?", (anio, mes)
    ).fetchone() is not None


def check_premios_abierto(conn, periodo: str):
    anio, mes = periodo.split("-")
    if es_premios_cerrado(conn, int(anio), int(mes)):
        raise HTTPException(409, f"El período {anio}-{mes} de premios está cerrado y no puede modificarse")


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

    if ev.get("tolerar_nf"):      no_fichadas = 0
    if ev.get("tolerar_e"):       dias_enfermo = 0
    if ev.get("tolerar_retardo"): minutos_retardo = 0

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
        "deduccion_trapos": 0,
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
        status = "NO CUMPLE"
        desglose.update({"puntualidad_status": status, "descalificado": True, "motivo_descalificacion": "puntualidad"})
        return _finalizar(desglose, 0, params)

    desglose["puntualidad_status"] = status
    desglose["deduccion_puntualidad"] = deduccion_puntualidad

    # BPM  ("NO APLICA" y "OK" no generan descuento)
    deduccion_bpm = round(base * params["pct_descuento_bpm"] / 100) if bpm == "NO CONFORME" else 0
    desglose["deduccion_bpm"] = deduccion_bpm

    # Vacaciones (proporcional)
    deduccion_vacaciones = 0
    if dias_vacacion > 0:
        deduccion_vacaciones = round((base / params["dias_base_vacaciones"]) * dias_vacacion)
    desglose["deduccion_vacaciones"] = deduccion_vacaciones

    deduccion_trapos = int(ev.get("deduccion_trapos") or 0)
    desglose["deduccion_trapos"] = deduccion_trapos

    valor_bruto = max(0, base - deduccion_puntualidad - deduccion_bpm - deduccion_vacaciones - deduccion_trapos)
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
    """
    Lee los contadores de asistencia aplicando la misma resolución de novedades
    que usa la planilla: las novedades tienen prioridad sobre resultados_dia.
    """
    anio, mes = periodo.split("-")
    fecha_desde = f"{anio}-{mes}-01"
    fecha_hasta = f"{int(anio)+1}-01-01" if mes == "12" else f"{anio}-{int(mes)+1:02d}-01"

    # Contadores desde resultados_dia respetando overrides de novedades.
    # Un día con novedad E/ILT/S/V/NF/CP no se cuenta como ausente ni NF.
    # El retardo solo se acumula en días efectivamente presentes (sin novedad de ausencia).
    r = conn.execute("""
        SELECT
            COALESCE(SUM(CASE
                WHEN rd.estado IN ('ok','tarde','tarde_y_sin_salida','tarde_y_salida_anticipada','nf','sin_salida')
                 AND NOT EXISTS (
                     SELECT 1 FROM novedades n
                     WHERE n.empleado_id = rd.empleado_id
                       AND n.fecha = rd.fecha
                       AND n.tipo IN ('E','ILT','S','V','L','LSG')
                 )
                THEN COALESCE(rd.b1_minutos_tarde,0) + COALESCE(rd.b2_minutos_tarde,0)
                ELSE 0
            END), 0) AS minutos_retardo,
            COUNT(CASE
                WHEN rd.estado IN ('ok','tarde','tarde_y_sin_salida','tarde_y_salida_anticipada','nf','sin_salida')
                 AND NOT EXISTS (
                     SELECT 1 FROM novedades n
                     WHERE n.empleado_id = rd.empleado_id
                       AND n.fecha = rd.fecha
                       AND n.tipo IN ('E','ILT','S','V','L','LSG')
                 )
                 AND (COALESCE(rd.b1_minutos_tarde,0) + COALESCE(rd.b2_minutos_tarde,0)) > 0
                THEN 1
            END) AS dias_tarde,
            COUNT(CASE
                WHEN (
                    rd.estado = 'nf'
                     AND NOT EXISTS (
                         SELECT 1 FROM novedades n
                         WHERE n.empleado_id = rd.empleado_id
                           AND n.fecha = rd.fecha
                           AND n.tipo IN ('E','ILT','S','V','L','LSG')
                     )
                ) OR (
                    rd.estado = 'ausente'
                     AND EXISTS (
                         SELECT 1 FROM novedades n
                         WHERE n.empleado_id = rd.empleado_id
                           AND n.fecha = rd.fecha
                           AND n.tipo = 'NF'
                     )
                )
                THEN 1
            END) AS no_fichadas
        FROM resultados_dia rd
        WHERE rd.empleado_id=? AND rd.fecha>=? AND rd.fecha<? AND rd.es_franco=0
    """, (empleado_id, fecha_desde, fecha_hasta)).fetchone()

    # Novedades: fuente de verdad para ausencias categorizadas (igual que planilla)
    # 'A' = ausencia confirmada (lo que planilla muestra como A, no A!!!)
    # A!!! = estado previo sin confirmar, no cuenta como ausente
    n = conn.execute("""
        SELECT
            COUNT(DISTINCT fecha) FILTER (WHERE tipo='V')            AS dias_vacacion,
            COUNT(DISTINCT fecha) FILTER (WHERE tipo IN ('E','ILT')) AS dias_enfermo,
            COUNT(DISTINCT fecha) FILTER (WHERE tipo='S')            AS dias_suspension,
            COUNT(DISTINCT fecha) FILTER (WHERE tipo IN ('A','L','LSG')) AS dias_ausente
        FROM novedades
        WHERE empleado_id=? AND fecha>=? AND fecha<?
    """, (empleado_id, fecha_desde, fecha_hasta)).fetchone()

    return {
        "minutos_retardo": r["minutos_retardo"],
        "dias_tarde":      r["dias_tarde"],
        "no_fichadas":     r["no_fichadas"],
        "dias_ausente":    n["dias_ausente"],
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
            COUNT(*) FILTER (WHERE e.cargo_id IS NULL)                                      AS sin_cargo,
            COUNT(*) FILTER (WHERE e.cargo_id IS NOT NULL AND c.id IS NULL)                AS cargo_no_catalogado,
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
        LEFT JOIN cargos c ON c.id = e.cargo_id
        WHERE e.activo = 1 AND e.tipo != 'acceso'
    """, (dias_min, fecha_desde, dias_min, fecha_desde)).fetchone()
    return dict(row)


# ── Evaluaciones ───────────────────────────────────────────────────────────────

_QUERY_EVALUACIONES = """
    WITH ult AS (
        SELECT p.empleado_id, h.tipo, h.grupo_premios, t.nombre AS turno_nombre,
               COUNT(*) AS cnt,
               ROW_NUMBER() OVER (
                   PARTITION BY p.empleado_id ORDER BY COUNT(*) DESC
               ) AS rn
        FROM planificacion p
        JOIN horarios h ON h.id = p.horario_id
        LEFT JOIN turnos t ON t.id = h.turno_id
        WHERE p.fecha >= ? AND p.fecha <= ? AND p.horario_id IS NOT NULL
        GROUP BY p.empleado_id, h.tipo, h.grupo_premios, t.nombre
    )
    SELECT pe.*, e.apellido, e.nombre,
           c.nombre AS cargo, d.nombre AS departamento, cat.nombre AS categoria,
           CASE
               WHEN u.grupo_premios IS NOT NULL THEN u.grupo_premios
               WHEN u.tipo = 'cortado' THEN 'CO'
               WHEN lower(u.turno_nombre) LIKE '%noche%'
                 OR lower(u.turno_nombre) LIKE '%madrugada%' THEN 'TN'
               WHEN u.tipo IS NOT NULL THEN 'TM'
               ELSE NULL
           END AS grupo
    FROM premios_evaluacion pe
    JOIN empleados e ON e.id = pe.empleado_id
    LEFT JOIN cargos c ON c.id = e.cargo_id
    LEFT JOIN departamentos d ON d.id = e.departamento_id
    LEFT JOIN categorias cat ON cat.id = e.categoria_id
    LEFT JOIN ult u ON u.empleado_id = e.id AND u.rn = 1
    WHERE pe.periodo=?
    ORDER BY
        CASE
            WHEN u.grupo_premios = 'CO' OR (u.grupo_premios IS NULL AND u.tipo = 'cortado') THEN 3
            WHEN u.grupo_premios = 'TN' OR (u.grupo_premios IS NULL AND (lower(u.turno_nombre) LIKE '%noche%' OR lower(u.turno_nombre) LIKE '%madrugada%')) THEN 2
            WHEN u.tipo IS NOT NULL THEN 1
            ELSE 4
        END,
        e.apellido, e.nombre
"""


@router.get("/periodos-cerrados/{anio}/{mes}")
def get_premios_periodo(anio: int, mes: int, _user=Depends(require_permiso("premios", "ver"))):
    with db_session() as conn:
        row = conn.execute(
            "SELECT ppc.*, COALESCE(u.nombre, 'Automático') as cerrado_por_nombre "
            "FROM premios_periodos_cerrados ppc "
            "LEFT JOIN usuarios u ON u.id = ppc.cerrado_por "
            "WHERE ppc.anio=? AND ppc.mes=?",
            (anio, mes)
        ).fetchone()
    if not row:
        return {"cerrado": False}
    return {**dict(row), "cerrado": True}


@router.post("/periodos-cerrados", status_code=201)
def cerrar_premios_periodo(data: PremiosPeriodoIn, user=Depends(require_permiso("premios", "cerrar"))):
    if data.mes < 1 or data.mes > 12:
        raise HTTPException(400, "Mes inválido")
    with db_session() as conn:
        if es_premios_cerrado(conn, data.anio, data.mes):
            raise HTTPException(409, "El período de premios ya está cerrado")
        conn.execute(
            "INSERT INTO premios_periodos_cerrados (anio, mes, cerrado_por) VALUES (?,?,?)",
            (data.anio, data.mes, int(user["sub"]))
        )
    return {"ok": True}


@router.delete("/periodos-cerrados/{anio}/{mes}")
def reabrir_premios_periodo(anio: int, mes: int, data: PremiosReabrirIn,
                            user=Depends(require_permiso("premios", "reabrir"))):
    with db_session() as conn:
        u = conn.execute(
            "SELECT password_hash FROM usuarios WHERE id=?", (int(user["sub"]),)
        ).fetchone()
        if not u or not verify_password(data.password, u["password_hash"]):
            raise HTTPException(400, "Contraseña incorrecta")
        if not es_premios_cerrado(conn, anio, mes):
            raise HTTPException(404, "El período de premios no está cerrado")
        conn.execute(
            "DELETE FROM premios_periodos_cerrados WHERE anio=? AND mes=?", (anio, mes)
        )
    return {"ok": True}


@router.get("/{periodo}")
def get_evaluaciones(periodo: str, _user=Depends(require_permiso("premios", "ver"))):
    with db_session() as conn:
        import calendar as _cal
        anio, mes = periodo.split("-")
        anio_i, mes_i = int(anio), int(mes)
        f0 = f"{anio_i:04d}-{mes_i:02d}-01"
        f1 = f"{anio_i:04d}-{mes_i:02d}-{_cal.monthrange(anio_i, mes_i)[1]:02d}"

        # Auto-refresh de contadores de asistencia solo si ambos períodos están abiertos
        cerrado = conn.execute(
            "SELECT 1 FROM periodos_cerrados WHERE anio=? AND mes=?",
            (anio_i, mes_i)
        ).fetchone()
        premios_cerrado = conn.execute(
            "SELECT 1 FROM premios_periodos_cerrados WHERE anio=? AND mes=?",
            (anio_i, mes_i)
        ).fetchone()

        if not cerrado and not premios_cerrado:
            params = _get_params(conn)
            evs = conn.execute(
                "SELECT id, empleado_id FROM premios_evaluacion WHERE periodo=?", (periodo,)
            ).fetchall()
            for ev in evs:
                datos = _acumular_periodo(conn, ev["empleado_id"], periodo)
                ev_full = dict(conn.execute(
                    "SELECT * FROM premios_evaluacion WHERE id=?", (ev["id"],)
                ).fetchone())
                ev_full.update(datos)
                desglose = _calcular(ev_full, params)
                conn.execute("""
                    UPDATE premios_evaluacion SET
                        minutos_retardo=?, dias_tarde=?, no_fichadas=?, dias_vacacion=?,
                        dias_enfermo=?, dias_suspension=?, dias_ausente=?,
                        desglose_json=?, valor_calculado=?, valor_final=?,
                        modificado_en=datetime('now','localtime')
                    WHERE id=?
                """, (
                    datos["minutos_retardo"], datos["dias_tarde"], datos["no_fichadas"], datos["dias_vacacion"],
                    datos["dias_enfermo"], datos["dias_suspension"], datos["dias_ausente"],
                    json.dumps(desglose), desglose["valor_calculado"], desglose["valor_final"],
                    ev["id"]
                ))

        rows = conn.execute(_QUERY_EVALUACIONES, (f0, f1, periodo)).fetchall()

        eids = [r["empleado_id"] for r in rows]
        control_estados = get_control_estados_batch(conn, eids, f0, f1)

    result = []
    for r in rows:
        if control_estados.get(r["empleado_id"]) == "liquidacion":
            continue
        d = dict(r)
        if d.get("desglose_json"):
            d["desglose"] = json.loads(d["desglose_json"])
        del d["desglose_json"]
        d["asistencia_incompleta"] = control_estados.get(d["empleado_id"]) not in ("completado", "vacio")
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
        check_premios_abierto(conn, periodo)
        params = _get_params(conn)
        anio, mes = periodo.split("-")
        fecha_desde = f"{anio}-{mes}-01"
        dias_min = int(params.get("dias_minimos_antiguedad", 90))
        diag = _diagnostico(conn, fecha_desde, dias_min)
        trapos_cfg = conn.execute(
            "SELECT valor FROM configuracion WHERE clave='trapos_cocina_activo'"
        ).fetchone()
        trapos_activo = trapos_cfg and trapos_cfg["valor"] == "1"
        trapos_valor_cfg = conn.execute(
            "SELECT valor FROM configuracion WHERE clave='trapos_cocina_valor'"
        ).fetchone()
        trapos_valor = int(trapos_valor_cfg["valor"]) if trapos_activo and trapos_valor_cfg else 0

        empleados = conn.execute("""
            SELECT e.id, c.aplica_trapos FROM empleados e
            JOIN cargos c ON c.id = e.cargo_id
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

        # Preserve BPM and monto_base already saved by editors
        existentes = {
            r["empleado_id"]: dict(r)
            for r in conn.execute(
                "SELECT empleado_id, bpm, monto_base, monto_base_manual FROM premios_evaluacion WHERE periodo=?",
                (periodo,)
            ).fetchall()
        }

        # Pasada 1: calcular todos sin trapos
        generados = 0
        for emp in empleados:
            eid = emp["id"]
            ev_existente = existentes.get(eid, {})
            bpm_actual = ev_existente.get("bpm") or ""
            monto_actual = ev_existente.get("monto_base") if ev_existente.get("monto_base_manual") else params["monto_base"]
            datos = _acumular_periodo(conn, eid, periodo)
            desglose = _calcular({**datos, "bpm": bpm_actual, "desempenio": None, "monto_base": monto_actual, "deduccion_trapos": 0}, params)

            conn.execute("""
                INSERT INTO premios_evaluacion
                    (empleado_id, periodo, minutos_retardo, dias_tarde, no_fichadas, dias_vacacion,
                     dias_enfermo, dias_suspension, dias_ausente, bpm, monto_base,
                     deduccion_trapos, desglose_json, valor_calculado, valor_final)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(empleado_id, periodo) DO UPDATE SET
                    minutos_retardo=excluded.minutos_retardo,
                    dias_tarde=excluded.dias_tarde,
                    no_fichadas=excluded.no_fichadas,
                    dias_vacacion=excluded.dias_vacacion,
                    dias_enfermo=excluded.dias_enfermo,
                    dias_suspension=excluded.dias_suspension,
                    dias_ausente=excluded.dias_ausente,
                    monto_base=CASE WHEN monto_base_manual=1 THEN monto_base ELSE excluded.monto_base END,
                    deduccion_trapos=0,
                    desglose_json=excluded.desglose_json,
                    valor_calculado=excluded.valor_calculado,
                    valor_final=excluded.valor_final,
                    modificado_en=datetime('now','localtime')
            """, (
                eid, periodo,
                datos["minutos_retardo"], datos["dias_tarde"], datos["no_fichadas"], datos["dias_vacacion"],
                datos["dias_enfermo"], datos["dias_suspension"], datos["dias_ausente"],
                bpm_actual, monto_actual, 0,
                json.dumps(desglose), desglose["valor_calculado"], desglose["valor_final"]
            ))
            generados += 1

        # Pasada 2: aplicar trapos proporcionalmente
        trapos_distribucion = None
        if trapos_activo and trapos_valor > 0:
            elegibles = conn.execute("""
                SELECT pe.id, pe.empleado_id, pe.bpm, pe.monto_base, pe.tolerar_nf,
                       pe.tolerar_e, pe.tolerar_retardo, pe.minutos_retardo, pe.dias_tarde,
                       pe.no_fichadas, pe.dias_vacacion, pe.dias_enfermo, pe.dias_suspension,
                       pe.dias_ausente, pe.valor_calculado
                FROM premios_evaluacion pe
                JOIN empleados e ON e.id = pe.empleado_id
                JOIN cargos c ON c.id = e.cargo_id
                WHERE pe.periodo=? AND c.aplica_trapos=1 AND pe.valor_calculado > 0
            """, (periodo,)).fetchall()

            if elegibles:
                count = len(elegibles)
                deduccion_per_emp = round(trapos_valor / count)
                trapos_distribucion = {"monto_total": trapos_valor, "count": count, "deduccion": deduccion_per_emp}
                for ev in elegibles:
                    ev_dict = dict(ev)
                    ev_dict["deduccion_trapos"] = deduccion_per_emp
                    desglose = _calcular(ev_dict, params)
                    conn.execute("""
                        UPDATE premios_evaluacion SET
                            deduccion_trapos=?, desglose_json=?, valor_calculado=?, valor_final=?,
                            modificado_en=datetime('now','localtime')
                        WHERE id=?
                    """, (deduccion_per_emp, json.dumps(desglose), desglose["valor_calculado"], desglose["valor_final"], ev["id"]))

    return {"ok": True, "generados": generados, "diagnostico": diag, "trapos_distribucion": trapos_distribucion}


@router.put("/evaluacion/{ev_id}")
def update_evaluacion(ev_id: int, data: EvaluacionUpdate, _user=Depends(require_permiso("premios", "editar"))):
    """Actualiza BPM, desempeño o monto base y recalcula."""
    with db_session() as conn:
        ev = conn.execute("SELECT * FROM premios_evaluacion WHERE id=?", (ev_id,)).fetchone()
        if not ev:
            raise HTTPException(404, "Evaluación no encontrada")
        ev = dict(ev)
        check_premios_abierto(conn, ev["periodo"])

        campos_corregir = [data.tolerar_nf, data.tolerar_e, data.tolerar_retardo, data.anular_premio]
        if any(v is not None for v in campos_corregir):
            if not tiene_permiso(_user.get("rol_id"), "premios", "corregir"):
                raise HTTPException(403, "Sin permiso: premios.corregir")

        if data.bpm is not None:
            ev["bpm"] = data.bpm
        if data.desempenio is not None:
            ev["desempenio"] = data.desempenio
        if data.monto_base is not None:
            ev["monto_base"] = data.monto_base
            ev["monto_base_manual"] = 1
        if data.tolerar_nf is not None:
            ev["tolerar_nf"] = int(data.tolerar_nf)
        if data.tolerar_e is not None:
            ev["tolerar_e"] = int(data.tolerar_e)
        if data.tolerar_retardo is not None:
            ev["tolerar_retardo"] = int(data.tolerar_retardo)
        if data.anular_premio is not None:
            ev["anular_premio"] = int(data.anular_premio)

        params = _get_params(conn)
        desglose = _calcular(ev, params)

        conn.execute("""
            UPDATE premios_evaluacion SET
                bpm=?, desempenio=?, monto_base=?, monto_base_manual=?,
                tolerar_nf=?, tolerar_e=?, tolerar_retardo=?, anular_premio=?,
                desglose_json=?, valor_calculado=?, valor_final=?,
                modificado_en=datetime('now','localtime')
            WHERE id=?
        """, (
            ev["bpm"], ev["desempenio"], ev["monto_base"], ev.get("monto_base_manual", 0),
            ev.get("tolerar_nf", 0), ev.get("tolerar_e", 0), ev.get("tolerar_retardo", 0), ev.get("anular_premio", 0),
            json.dumps(desglose), desglose["valor_calculado"], desglose["valor_final"],
            ev_id
        ))

        ev.update({"desglose": desglose, "valor_calculado": desglose["valor_calculado"], "valor_final": desglose["valor_final"]})
    return ev


@router.post("/{periodo}/recalcular")
def recalcular_periodo(periodo: str, _user=Depends(require_permiso("premios", "editar"))):
    """Recalcula todas las evaluaciones del período con los parámetros actuales."""
    with db_session() as conn:
        check_premios_abierto(conn, periodo)
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
