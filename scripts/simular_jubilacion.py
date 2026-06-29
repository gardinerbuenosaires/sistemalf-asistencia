"""
Simulación del comportamiento de vacaciones para empleados recontratados por jubilación.
No modifica ningún dato — solo lee y muestra resultados.

Uso:
    python scripts/simular_jubilacion.py
"""
import math
import os
import sys
from datetime import date

os.environ.setdefault("DB_PATH", "data_happening/fichajes.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from db.database import db_session


# ── Funciones copiadas de api/vacaciones.py (sin modificar) ──────────────────

def _calcular_dias_formula(fecha_ingreso_str, anio):
    if not fecha_ingreso_str:
        return 0, 0.0
    try:
        fecha_ingreso = date.fromisoformat(fecha_ingreso_str)
    except ValueError:
        return 0, 0.0
    ref = date(anio, 12, 31)
    if fecha_ingreso > ref:
        return 0, 0.0
    days = (ref - fecha_ingreso).days
    anios = math.trunc(days / 365.25)
    if anios >= 20:   tramo = 35.0
    elif anios >= 10: tramo = 28.0
    elif anios >= 5:  tramo = 21.0
    elif anios >= 1:  tramo = 14.0
    else:
        dias_empleo = days + 1
        tramo = 14.0 if dias_empleo >= 180 else float(math.floor(dias_empleo / 20 + 0.5))
    return anios, tramo


def _get_arrastre(eid, anio, fecha_ingreso_str, saldos, nov_map, depth=0,
                  vac_dias_jubilacion=None, fecha_recontratacion=None):
    if depth > 15:
        return 0.0
    prev = anio - 1
    if fecha_ingreso_str:
        try:
            if date.fromisoformat(fecha_ingreso_str).year > prev:
                return 0.0
        except ValueError:
            return 0.0
    else:
        return 0.0

    saldo_prev = saldos.get((eid, prev))
    dic_prev   = {k: v for k, v in nov_map.get((eid, prev), {}).items() if k == "12"}
    resto_anio = {k: v for k, v in nov_map.get((eid, anio), {}).items() if k < "12"}
    meses_prev = {**dic_prev, **resto_anio}
    dias_v_prev = sum(meses_prev.values())

    if not saldo_prev and not meses_prev:
        return 0.0

    if saldo_prev:
        dias_corr_prev = saldo_prev["dias_correspondian"]
        dias_tom_prev  = saldo_prev["dias_tomados"] + dias_v_prev
    else:
        # NUEVA LÓGICA: aplicar excepción al período prev si corresponde
        anio_recont = date.fromisoformat(fecha_recontratacion).year if fecha_recontratacion else None
        if vac_dias_jubilacion and anio_recont and prev >= anio_recont:
            dias_formula_prev = vac_dias_jubilacion
        else:
            _, dias_formula_prev = _calcular_dias_formula(fecha_ingreso_str, prev)
        arrastre_prev  = _get_arrastre(eid, prev, fecha_ingreso_str, saldos, nov_map,
                                       depth + 1, vac_dias_jubilacion, fecha_recontratacion)
        dias_corr_prev = dias_formula_prev + arrastre_prev
        dias_tom_prev  = dias_v_prev

    return max(0.0, round(dias_corr_prev - dias_tom_prev, 1))


def calcular_empleado(eid, nombre, fecha_ingreso, anio,
                      saldos, nov_map,
                      fecha_recontratacion=None, vac_dias_jubilacion=None,
                      etiqueta=""):
    """Calcula vacaciones para un empleado en un período dado."""
    anio_recont = date.fromisoformat(fecha_recontratacion).year if fecha_recontratacion else None

    # NUEVA LÓGICA: si tiene excepción y el período aplica, usar días fijos
    if vac_dias_jubilacion and anio_recont and anio >= anio_recont:
        dias_formula = vac_dias_jubilacion
        anios_antig  = 0
        excepcion    = True
    else:
        anios_antig, dias_formula = _calcular_dias_formula(fecha_ingreso, anio)
        excepcion = False

    saldo = saldos.get((eid, anio))
    dic_anio   = {k: v for k, v in nov_map.get((eid, anio), {}).items() if k == "12"}
    resto_sig  = {k: v for k, v in nov_map.get((eid, anio + 1), {}).items() if k < "12"}
    dias_v     = sum(dic_anio.values()) + sum(resto_sig.values())

    if saldo:
        dias_correspondian = saldo["dias_correspondian"]
        dias_tomados       = saldo["dias_tomados"] + dias_v
        arrastre           = 0.0
    else:
        arrastre = _get_arrastre(eid, anio, fecha_ingreso, saldos, nov_map, 0,
                                 vac_dias_jubilacion, fecha_recontratacion)
        dias_correspondian = dias_formula + arrastre
        dias_tomados       = dias_v

    dias_restan = round(dias_correspondian - dias_tomados, 1)

    marca = " [EXCEPCIÓN JUBILACIÓN]" if excepcion else ""
    print(f"  {nombre} — Período {anio}{etiqueta}{marca}")
    print(f"    Antigüedad: {anios_antig} años | Fórmula/excepción: {dias_formula} días")
    if arrastre:
        print(f"    Arrastre: +{arrastre}")
    print(f"    Correspondían: {dias_correspondian} | Tomados: {dias_tomados} | Restan: {dias_restan}")
    return dias_restan


# ── Carga de datos reales ─────────────────────────────────────────────────────

with db_session() as conn:
    empleados = conn.execute(
        "SELECT id, nombre, apellido, fecha_ingreso FROM empleados "
        "WHERE activo=1 AND tipo != 'acceso' ORDER BY apellido LIMIT 3"
    ).fetchall()

    saldos = {}
    for r in conn.execute("SELECT * FROM vacaciones_saldo_inicial").fetchall():
        saldos[(r["empleado_id"], r["anio"])] = dict(r)

    nov_rows = conn.execute("""
        SELECT empleado_id,
               CAST(strftime('%Y', fecha) AS INTEGER) AS anio,
               strftime('%m', fecha) AS mes,
               SUM(CASE WHEN bloque = 0 THEN 1.0 ELSE 0.5 END) AS dias
        FROM novedades WHERE tipo = 'V'
        GROUP BY empleado_id, anio, mes
    """).fetchall()

nov_map = {}
for r in nov_rows:
    key = (r["empleado_id"], r["anio"])
    if key not in nov_map:
        nov_map[key] = {}
    nov_map[key][r["mes"]] = r["dias"]


# ── Caso 1: empleados reales sin excepción (verificar que nada cambia) ────────

print("=" * 60)
print("CASO 1: Empleados reales — comportamiento sin cambios")
print("=" * 60)
anio_actual = date.today().year - 1
for e in empleados:
    calcular_empleado(e["id"], f"{e['apellido']}, {e['nombre']}", e["fecha_ingreso"],
                      anio_actual, saldos, nov_map)
    print()


# ── Caso 2: empleado ficticio que se jubila y vuelve ──────────────────────────

print("=" * 60)
print("CASO 2: Empleado ficticio — jubilación y recontratación")
print("Ingreso original: 2000-03-15 | Recontratación: 2026-07-01 | Días otorgados: 21")
print("=" * 60)

EID_FICTICIO   = -1
FECHA_INGRESO  = "2000-03-15"   # 26 años de antigüedad → 35 días por fórmula
FECHA_RECONT   = "2026-07-01"
VAC_JUBILACION = 21.0

# Simular novedades: tomó 10 días en 2025 (período 2024) y 5 días en 2026 (período 2025)
nov_map_ficticio = {
    (EID_FICTICIO, 2025): {"03": 10.0},   # consumo período 2024
    (EID_FICTICIO, 2026): {"02": 5.0},    # consumo período 2025
}
saldos_ficticio = {}

print("\n-- Sin excepción (como funciona hoy) --")
for anio in [2024, 2025, 2026, 2027]:
    calcular_empleado(EID_FICTICIO, "García, Juan (ficticio)", FECHA_INGRESO, anio,
                      saldos_ficticio, nov_map_ficticio, etiqueta=" (sin excepción)")

print("\n-- Con excepción de jubilación --")
for anio in [2024, 2025, 2026, 2027]:
    calcular_empleado(EID_FICTICIO, "García, Juan (ficticio)", FECHA_INGRESO, anio,
                      saldos_ficticio, nov_map_ficticio,
                      fecha_recontratacion=FECHA_RECONT,
                      vac_dias_jubilacion=VAC_JUBILACION)


# ── Caso 3: transición con días tomados en diciembre ──────────────────────────

print()
print("=" * 60)
print("CASO 3: Toma días en diciembre 2026 (primer mes del nuevo período)")
print("=" * 60)

nov_map_dic = {
    (EID_FICTICIO, 2025): {"03": 10.0},   # consumo período 2024
    (EID_FICTICIO, 2026): {"02": 5.0, "12": 8.0},  # 5 días en feb (período 2025) + 8 en dic (período 2026)
}

for anio in [2025, 2026, 2027]:
    calcular_empleado(EID_FICTICIO, "García, Juan (ficticio)", FECHA_INGRESO, anio,
                      saldos_ficticio, nov_map_dic,
                      fecha_recontratacion=FECHA_RECONT,
                      vac_dias_jubilacion=VAC_JUBILACION)
