import calendar
from datetime import date
from fastapi import APIRouter, Depends
from db.database import db_session, get_config
from auth.core import require_permiso

router = APIRouter(prefix="/api/parking", tags=["parking"])


@router.get("/mensual")
def get_parking_mensual(anio: int, mes: int,
                        _user=Depends(require_permiso("asistencia", "ver"))):
    """Cantidad de valets (tipo='parking') por turno por día del mes.

    Lógica de turnos:
      - Fichaje < corte_madrugada          → TN del día ANTERIOR (turno noche que cruzó medianoche)
      - Fichaje >= corte_madrugada y < corte_turno → TM del mismo día
      - Fichaje >= corte_turno             → TN del mismo día

    El rango de query arranca en corte_madrugada del día 1 y termina en
    corte_madrugada del día 1 del mes siguiente, para capturar los fichajes
    de madrugada del último día del mes.
    """
    with db_session() as conn:
        corte     = get_config(conn, "parking_corte_turno",    "19:00")
        madrugada = get_config(conn, "parking_corte_madrugada", "06:00")

        # Rango de timestamps a consultar
        first_day  = date(anio, mes, 1)
        if mes == 12:
            next_first = date(anio + 1, 1, 1)
        else:
            next_first = date(anio, mes + 1, 1)
        inicio = f"{first_day}  {madrugada}:00"
        fin    = f"{next_first} {madrugada}:00"
        n_dias = calendar.monthrange(anio, mes)[1]

        base_params = {
            "corte": corte, "madrugada": madrugada,
            "inicio": inicio, "fin": fin, "n_dias": n_dias,
        }

        # Expresiones SQL reutilizables
        _dia = """
            CASE WHEN strftime('%H:%M', f.timestamp) < :madrugada
                 THEN CAST(strftime('%d', datetime(f.timestamp, '-1 day')) AS INTEGER)
                 ELSE CAST(strftime('%d', f.timestamp) AS INTEGER)
            END"""
        _turno = """
            CASE WHEN strftime('%H:%M', f.timestamp) < :madrugada THEN 'TN'
                 WHEN strftime('%H:%M', f.timestamp) < :corte     THEN 'TM'
                 ELSE 'TN'
            END"""
        _where = """
            e.tipo = 'parking'
            AND f.timestamp >= :inicio
            AND f.timestamp <  :fin"""

        # Primer fichaje por empleado por día ajustado (determina el turno)
        # Usamos MIN(timestamp) para tomar solo la entrada y evitar que la salida
        # cambie el turno (ej: entrada 18:50 TM + salida 23:45 TN → no es DT)
        first_fich = f"""
            SELECT f.empleado_id,
                   {_dia} AS dia,
                   MIN(f.timestamp) AS primer_ts
            FROM fichajes f
            JOIN empleados e ON e.id = f.empleado_id
            WHERE {_where}
            GROUP BY f.empleado_id, dia
            HAVING dia BETWEEN 1 AND :n_dias"""

        # Conteo por turno por día (basado en primer fichaje)
        count_rows = conn.execute(f"""
            SELECT dia,
                   CASE WHEN strftime('%H:%M', primer_ts) < :madrugada THEN 'TN'
                        WHEN strftime('%H:%M', primer_ts) < :corte     THEN 'TM'
                        ELSE 'TN' END AS turno,
                   COUNT(*) AS cantidad
            FROM ({first_fich})
            GROUP BY dia, turno
        """, base_params).fetchall()

        # Presencia por empleado por día: turno según primer fichaje, horas = todos los fichajes
        presence_rows = conn.execute(f"""
            SELECT pf.empleado_id AS id,
                   pf.dia,
                   CASE WHEN strftime('%H:%M', pf.primer_ts) < :madrugada THEN 'TN'
                        WHEN strftime('%H:%M', pf.primer_ts) < :corte     THEN 'TM'
                        ELSE 'TN' END AS turnos,
                   GROUP_CONCAT(strftime('%H:%M', f.timestamp) ORDER BY f.timestamp) AS horas
            FROM ({first_fich}) pf
            JOIN fichajes f ON f.empleado_id = pf.empleado_id
            JOIN empleados e ON e.id = f.empleado_id
            WHERE {_where}
              AND ({_dia}) = pf.dia
            GROUP BY pf.empleado_id, pf.dia
        """, base_params).fetchall()

        # Empleados parking activos
        emp_rows = conn.execute("""
            SELECT id, apellido || ', ' || nombre AS nombre
            FROM empleados
            WHERE tipo = 'parking' AND activo = 1
            ORDER BY apellido, nombre
        """).fetchall()

        # Empleados con presencia este mes (aunque estén inactivos)
        emp_con_presencia = conn.execute(f"""
            SELECT DISTINCT e.id, e.apellido || ', ' || e.nombre AS nombre
            FROM fichajes f
            JOIN empleados e ON e.id = f.empleado_id
            WHERE {_where}
            ORDER BY e.apellido, e.nombre
        """, base_params).fetchall()

        # Feriados del mes
        feriado_rows = conn.execute(
            "SELECT fecha FROM feriados WHERE fecha LIKE ?",
            (f"{anio}-{mes:02d}-%",)
        ).fetchall()

    tm: dict = {}
    tn: dict = {}
    for r in count_rows:
        if r["turno"] == "TM":
            tm[r["dia"]] = r["cantidad"]
        else:
            tn[r["dia"]] = r["cantidad"]

    emp_dias: dict = {}
    for r in presence_rows:
        eid = r["id"]
        if eid not in emp_dias:
            emp_dias[eid] = {}
        emp_dias[eid][r["dia"]] = {
            "turnos": sorted((r["turnos"] or "").split(",")),
            "horas":  (r["horas"] or "").split(","),
        }

    seen: set = set()
    empleados = []
    for r in emp_rows:
        seen.add(r["id"])
        empleados.append({"id": r["id"], "nombre": r["nombre"], "dias": emp_dias.get(r["id"], {})})
    for r in emp_con_presencia:
        if r["id"] not in seen:
            seen.add(r["id"])
            empleados.append({"id": r["id"], "nombre": r["nombre"], "dias": emp_dias.get(r["id"], {})})
    empleados.sort(key=lambda e: e["nombre"])

    feriados = [int(r["fecha"][8:10]) for r in feriado_rows]

    return {
        "corte":     corte,
        "madrugada": madrugada,
        "n_dias":    n_dias,
        "tm":        tm,
        "tn":        tn,
        "empleados": empleados,
        "feriados":  feriados,
    }
