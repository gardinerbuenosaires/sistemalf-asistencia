from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
from datetime import date, timedelta
from collections import defaultdict
from db.database import db_session
from auth.core import require_permiso

router = APIRouter(prefix="/api/asistencia", tags=["asistencia_mensual"])

# ── Mapeo estado evaluador → letra planilla ────────────────────────────────────
_EST_SIMPLE = {
    "ok": "I", "salida_anticipada": "I", "sin_salida": "I", "sin_horario": "I",
    "tarde": "T", "tarde_y_salida_anticipada": "T", "tarde_y_sin_salida": "T",
    "ausente": "A!!!", "franco": "F", "ft": "FT",
    "b1_ausente": "A!!!", "b2_ausente": "A!!!",
    "nf": "NF", "duda": "!",
}
_EST_B1 = {**_EST_SIMPLE, "b1_ausente": "A!!!", "b2_ausente": "I"}
_EST_B2 = {
    "ok": "I", "salida_anticipada": "I", "sin_salida": "I", "sin_horario": "I",
    "tarde": "I",                        # tarde afecta la entrada del b1
    "tarde_y_salida_anticipada": "I",
    "tarde_y_sin_salida": "I",
    "ausente": "A!!!", "franco": "F", "ft": "FT",
    "b1_ausente": "I", "b2_ausente": "A!!!",
    "nf": "NF", "duda": "!",
}

CONTABLES     = {"I", "T", "F", "FT", "FD", "V", "L", "LSG", "S", "@", "NF"}
LETRAS_VALIDAS = {"ILT", "LSG", "L", "E", "V", "S", "FT", "FD", "F", "@", "NF", "A", "CP"}
DIAS_SEMANA   = ["lu", "ma", "mi", "ju", "vi", "sá", "do"]


# ── Resolución de una celda/bloque ────────────────────────────────────────────
def _resolver(eid, fecha, bloque, f_ing, f_egr, nov_map, ali_set, res_map, plan_map):
    if f_ing and fecha < f_ing:
        return {"letra": "O", "tipo": "antes_ingreso"}
    if f_egr and fecha >= f_egr:
        return {"letra": "X", "tipo": "liquidacion"}

    # 1. Novedad manual: bloque específico primero, luego bloque=0 (día entero)
    nov = nov_map.get((eid, fecha, bloque))
    if nov is None and bloque != 0:
        nov = nov_map.get((eid, fecha, 0))
    if nov:
        if nov["tipo"] == "CP":
            r = {"letra": "I", "tipo": "normal", "cp": True, "nov_id": nov["id"]}
        else:
            r = {"letra": nov["tipo"], "tipo": "normal", "nov_id": nov["id"]}
        if nov.get("descripcion"):
            r["descripcion"] = nov["descripcion"]
        if nov.get("creado_por"):
            r["creado_por"] = nov["creado_por"]
        return r

    # 2. Aliviada (solo bloques 1 y 2)
    if bloque in (1, 2) and (eid, fecha, bloque) in ali_set:
        return {"letra": "@", "tipo": "normal"}

    # 3. Resultado automático del evaluador
    res = res_map.get((eid, fecha))
    if res:
        # Bloque MT (no aplica): mostrar dash o highlight si vino inesperadamente
        if bloque == 1 and res.get("b1_mt"):
            if res.get("b1_entrada"):
                return {"letra": "MT", "tipo": "mt_trabajado"}
            return {"letra": "MT", "tipo": "mt"}
        if bloque == 2 and res.get("b2_mt"):
            if res.get("b2_entrada"):
                return {"letra": "MT", "tipo": "mt_trabajado"}
            return {"letra": "MT", "tipo": "mt"}

        estado = res["estado"]

        if estado == "no_iniciado":
            return {"letra": None, "tipo": "no_iniciado"}

        if estado.endswith("_b2_ni"):
            if bloque == 2:
                return {"letra": None, "tipo": "no_iniciado"}
            base = estado[:-6]  # strip "_b2_ni"
            mapa = _EST_B1 if bloque == 1 else _EST_SIMPLE
            letra = mapa.get(base)
            if letra:
                return {"letra": letra, "tipo": "normal"}

        # Duda causada por un bloque con solo salida: el otro bloque completo muestra como presente
        if estado == "duda":
            if bloque == 1 and res.get("b1_entrada"):
                return {"letra": "I", "tipo": "normal"}
            if bloque == 2 and res.get("b2_entrada"):
                return {"letra": "I", "tipo": "normal"}

        # Si b1 es MT, el estado representa solo b2 evaluado como bloque simple → usar _EST_B1 para bloque 2
        if bloque == 2 and res.get("b1_mt"):
            mapa = _EST_B1
        else:
            mapa = _EST_B1 if bloque == 1 else (_EST_B2 if bloque == 2 else _EST_SIMPLE)
        letra  = mapa.get(estado)
        if letra:
            ft_pendiente = estado == "ft" and not res["horario_id"]
            return {"letra": letra, "tipo": "normal", **({"ft_pendiente": True} if ft_pendiente else {})}

    # 4. Planificacion (futuro o sin procesar)
    plan = plan_map.get((eid, fecha))
    if plan:
        if plan["es_franco"]:
            return {"letra": "F", "tipo": "normal"}
        if plan["horario_id"]:
            return {"letra": None, "tipo": "pendiente"}

    return {"letra": None, "tipo": "sin_plan"}


def _fmt(v: float):
    """Devuelve int si es entero, float si tiene decimal."""
    return int(v) if v == int(v) else v


def _calcular_control(fechas, f_egr, celdas, cortado, f0, f1, hoy_str):
    dias_duda          = []
    dias_faltantes     = []
    dias_ausentes_sc   = []
    dias_activos       = 0

    for fecha in fechas:
        celda = celdas[fecha]
        if cortado or celda.get("es_cortado_dia"):
            bloques = [celda["b1"], celda["b2"]]
        else:
            bloques = [celda]

        # Día entero fuera del período activo del empleado → ignorar
        if all(b["tipo"] in ("antes_ingreso", "liquidacion") for b in bloques):
            continue

        dias_activos += 1
        dia_num = int(fecha[8:])

        if any(b.get("letra") == "!" for b in bloques):
            dias_duda.append(dia_num)
            continue  # duda ≠ faltante

        if any(b.get("letra") == "A!!!" for b in bloques):
            dias_ausentes_sc.append(dia_num)
            continue  # ausente sin confirmar ≠ faltante

        if any(b["tipo"] in ("pendiente", "sin_plan") for b in bloques):
            dias_faltantes.append(dia_num)

    f1_mas1 = (date.fromisoformat(f1) + timedelta(days=1)).isoformat()
    tiene_egreso_mes = bool(f_egr) and f0 <= f_egr <= f1_mas1

    # Ventana de alerta: últimos 5 días del mes
    ventana_desde = (date.fromisoformat(f1) - timedelta(days=4)).isoformat()
    en_ventana    = hoy_str >= ventana_desde

    extra = {"dias_ausentes_sc": dias_ausentes_sc} if dias_ausentes_sc else {}

    if dias_activos < 3:
        return {"estado": "vacio", **extra}
    if not dias_duda and not dias_faltantes:
        if dias_ausentes_sc:
            return {"estado": "ausentes_sc", **extra}
        return {"estado": "liquidacion" if tiene_egreso_mes else "completado"}
    if dias_duda and not dias_faltantes:
        return {"estado": "revisar", "dias_duda": dias_duda, **extra}
    if not dias_duda:
        # Solo días faltantes: detalle solo en la ventana final
        if en_ventana:
            return {"estado": "faltan", "dias_faltantes": dias_faltantes, **extra}
        return {"estado": "en_curso", **extra}
    # Hay duda Y faltantes: siempre mostrar duda, faltantes solo en ventana
    if en_ventana:
        return {"estado": "mixto", "dias_duda": dias_duda, "dias_faltantes": dias_faltantes}
    return {"estado": "revisar", "dias_duda": dias_duda}


def get_control_estados_batch(conn, eids: list, f0: str, f1: str) -> dict:
    """
    Devuelve {empleado_id: estado_control} para una lista de empleados en el período f0-f1.
    Usa la misma lógica que la planilla (_resolver + _calcular_control).
    Premios lo importa para saber si el empleado tiene asistencia completada.
    """
    if not eids:
        return {}

    fechas = []
    d = date.fromisoformat(f0)
    last = date.fromisoformat(f1)
    while d <= last:
        fechas.append(d.isoformat())
        d += timedelta(days=1)

    ph = ",".join("?" * len(eids))

    emp_rows = conn.execute(
        f"SELECT id, fecha_ingreso, fecha_egreso FROM empleados WHERE id IN ({ph})", eids
    ).fetchall()

    # Tipo de horario predominante por empleado (para saber si es cortado)
    cortado_rows = conn.execute(f"""
        SELECT p.empleado_id, h.tipo
        FROM planificacion p
        JOIN horarios h ON h.id = p.horario_id
        WHERE p.empleado_id IN ({ph}) AND p.fecha >= ? AND p.fecha <= ?
          AND p.horario_id IS NOT NULL
        ORDER BY p.fecha DESC
    """, (*eids, f0, f1)).fetchall()
    cortado_map = {}
    for r in cortado_rows:
        if r["empleado_id"] not in cortado_map:
            cortado_map[r["empleado_id"]] = (r["tipo"] == "cortado")

    planes = conn.execute(
        f"SELECT p.empleado_id, p.fecha, p.es_franco, p.horario_id, h.tipo AS horario_tipo "
        f"FROM planificacion p LEFT JOIN horarios h ON h.id=p.horario_id "
        f"WHERE p.empleado_id IN ({ph}) AND p.fecha>=? AND p.fecha<=?",
        (*eids, f0, f1)
    ).fetchall()

    resultados = conn.execute(
        f"SELECT empleado_id, fecha, estado, horario_id, b1_mt, b2_mt, b1_entrada, b2_entrada "
        f"FROM resultados_dia WHERE empleado_id IN ({ph}) AND fecha>=? AND fecha<=?",
        (*eids, f0, f1)
    ).fetchall()

    novedades = conn.execute(
        f"SELECT id, empleado_id, fecha, bloque, tipo, descripcion, creado_por "
        f"FROM novedades WHERE empleado_id IN ({ph}) AND fecha>=? AND fecha<=?",
        (*eids, f0, f1)
    ).fetchall()

    aliviadas = conn.execute(
        f"SELECT empleado_id, fecha, bloque FROM aliviadas "
        f"WHERE empleado_id IN ({ph}) AND fecha>=? AND fecha<=?",
        (*eids, f0, f1)
    ).fetchall()

    plan_map = {(r["empleado_id"], r["fecha"]): dict(r) for r in planes}
    res_map  = {
        (r["empleado_id"], r["fecha"]): {
            "estado": r["estado"], "horario_id": r["horario_id"],
            "b1_mt": r["b1_mt"], "b2_mt": r["b2_mt"],
            "b1_entrada": r["b1_entrada"], "b2_entrada": r["b2_entrada"],
        }
        for r in resultados
    }
    nov_map = {}
    for n in novedades:
        nov_map[(n["empleado_id"], n["fecha"], n["bloque"])] = {
            "id": n["id"], "tipo": n["tipo"],
            "descripcion": n["descripcion"], "creado_por": n["creado_por"],
        }
    ali_set = {(a["empleado_id"], a["fecha"], a["bloque"]) for a in aliviadas}

    hoy_str = date.today().isoformat()
    resultado = {}

    for emp in emp_rows:
        eid   = emp["id"]
        f_ing = (emp["fecha_ingreso"] or "")[:10]
        f_egr = (emp["fecha_egreso"]  or "")[:10]
        cortado = cortado_map.get(eid, False)

        celdas = {}
        for fecha in fechas:
            plan = plan_map.get((eid, fecha))
            dia_cortado = plan and plan.get("horario_tipo") == "cortado"
            if cortado or dia_cortado:
                b1 = _resolver(eid, fecha, 1, f_ing, f_egr, nov_map, ali_set, res_map, plan_map)
                b2 = _resolver(eid, fecha, 2, f_ing, f_egr, nov_map, ali_set, res_map, plan_map)
                celdas[fecha] = {"b1": b1, "b2": b2, "es_cortado_dia": bool(dia_cortado)}
            else:
                c = _resolver(eid, fecha, 0, f_ing, f_egr, nov_map, ali_set, res_map, plan_map)
                celdas[fecha] = c

        ctrl = _calcular_control(fechas, f_egr, celdas, cortado, f0, f1, hoy_str)
        resultado[eid] = ctrl["estado"]

    return resultado


def _build_dias(fechas, feriados):
    return [
        {
            "fecha": f,
            "num":   int(f[8:]),
            "dow":   DIAS_SEMANA[date.fromisoformat(f).weekday()],
            "feriado": f in feriados,
            "domingo": date.fromisoformat(f).weekday() == 6,
        }
        for f in fechas
    ]


# ── Endpoint principal ────────────────────────────────────────────────────────
@router.get("/mensual")
def asistencia_mensual(mes: str = Query(None), _user=Depends(require_permiso("asistencia", "ver"))):
    if not mes:
        mes = date.today().strftime("%Y-%m")
    try:
        año, m = int(mes[:4]), int(mes[5:7])
        primer_dia = date(año, m, 1)
        ultimo_dia = (
            date(año + 1, 1, 1) - timedelta(days=1) if m == 12
            else date(año, m + 1, 1) - timedelta(days=1)
        )
    except Exception:
        raise HTTPException(400, "Formato de mes inválido (YYYY-MM)")

    fechas = []
    d = primer_dia
    while d <= ultimo_dia:
        fechas.append(d.isoformat())
        d += timedelta(days=1)
    f0, f1 = fechas[0], fechas[-1]
    mes_ant = f"{año}-{m-1:02d}" if m > 1 else f"{año-1}-12"

    with db_session() as conn:
        feriados = {
            r["fecha"][:10] for r in conn.execute(
                "SELECT fecha FROM feriados WHERE fecha >= ? AND fecha <= ?", (f0, f1)
            ).fetchall()
        }

        # Empleados: activos + desvinculados cuyo fecha_egreso cae dentro del mes
        emp_rows = conn.execute("""
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
            SELECT e.id, e.user_id, e.nombre, e.apellido, c.nombre AS cargo,
                   e.fecha_ingreso, e.fecha_egreso, e.en_dispositivo, e.activo,
                   u.tipo AS hipo, u.turno_nombre
            FROM empleados e
            LEFT JOIN cargos c ON c.id = e.cargo_id
            LEFT JOIN ult u ON u.empleado_id = e.id AND u.rn = 1
            WHERE e.tipo != 'acceso'
              AND (e.activo = 1
               OR (e.fecha_egreso > ? AND e.fecha_egreso <= ?))
            ORDER BY e.apellido, e.nombre
        """, (f0, f1, f0, f1)).fetchall()

        if not emp_rows:
            return {"mes": mes, "dias": _build_dias(fechas, feriados),
                    "TM": [], "TN": [], "CO": [], "ST": []}

        eids = [r["id"] for r in emp_rows]
        ph   = ",".join("?" * len(eids))

        planes = conn.execute(
            f"SELECT p.empleado_id, p.fecha, p.es_franco, p.horario_id, h.tipo AS horario_tipo "
            f"FROM planificacion p "
            f"LEFT JOIN horarios h ON h.id = p.horario_id "
            f"WHERE p.empleado_id IN ({ph}) AND p.fecha>=? AND p.fecha<=?",
            (*eids, f0, f1)
        ).fetchall()

        resultados = conn.execute(
            f"SELECT empleado_id, fecha, estado, horario_id, b1_mt, b2_mt, b1_entrada, b2_entrada "
            f"FROM resultados_dia WHERE empleado_id IN ({ph}) AND fecha>=? AND fecha<=?",
            (*eids, f0, f1)
        ).fetchall()

        novedades = conn.execute(
            f"SELECT id, empleado_id, fecha, bloque, tipo, descripcion, creado_por "
            f"FROM novedades WHERE empleado_id IN ({ph}) AND fecha>=? AND fecha<=?",
            (*eids, f0, f1)
        ).fetchall()

        aliviadas = conn.execute(
            f"SELECT empleado_id, fecha, bloque "
            f"FROM aliviadas WHERE empleado_id IN ({ph}) AND fecha>=? AND fecha<=?",
            (*eids, f0, f1)
        ).fetchall()

        saldos = conn.execute(
            f"SELECT empleado_id, saldo FROM saldo_francos "
            f"WHERE empleado_id IN ({ph}) AND mes=?",
            (*eids, mes_ant)
        ).fetchall()

        fichajes_manuales = conn.execute(
            f"SELECT empleado_id, date(timestamp) AS fecha "
            f"FROM fichajes "
            f"WHERE empleado_id IN ({ph}) AND date(timestamp)>=? AND date(timestamp)<=? AND es_manual=1 "
            f"GROUP BY empleado_id, date(timestamp)",
            (*eids, f0, f1)
        ).fetchall()

    # Lookup dicts
    plan_map = {(r["empleado_id"], r["fecha"]): dict(r) for r in planes}
    res_map  = {
        (r["empleado_id"], r["fecha"]): {
            "estado": r["estado"], "horario_id": r["horario_id"],
            "b1_mt": r["b1_mt"], "b2_mt": r["b2_mt"],
            "b1_entrada": r["b1_entrada"], "b2_entrada": r["b2_entrada"],
        }
        for r in resultados
    }

    fm_count: dict[int, int] = defaultdict(int)
    for f in fichajes_manuales:
        fm_count[f["empleado_id"]] += 1
    nov_map  = {}
    comentarios_map: dict[int, list] = defaultdict(list)
    for n in novedades:
        nov_map[(n["empleado_id"], n["fecha"], n["bloque"])] = {
            "id": n["id"], "tipo": n["tipo"], "descripcion": n["descripcion"],
            "creado_por": n["creado_por"],
        }
        comentarios_map[n["empleado_id"]].append({
            "fecha": n["fecha"], "bloque": n["bloque"],
            "tipo": n["tipo"], "descripcion": n["descripcion"],
            "creado_por": n["creado_por"],
        })
    ali_set       = {(a["empleado_id"], a["fecha"], a["bloque"]) for a in aliviadas}
    transporte_map = {s["empleado_id"]: s["saldo"] for s in saldos}

    grupos = {"TM": [], "TN": [], "CO": [], "ST": []}

    for emp in emp_rows:
        eid       = emp["id"]
        turno     = (emp["turno_nombre"] or "").lower()
        cortado   = emp["hipo"] == "cortado"
        if cortado:
            grupo = "CO"
        elif not emp["turno_nombre"]:
            if not emp["activo"]:
                continue
            grupo = "ST"
        elif "noche" in turno or "madrugada" in turno:
            grupo = "TN"
        else:
            grupo = "TM"

        f_ing = (emp["fecha_ingreso"] or "")[:10]
        f_egr = (emp["fecha_egreso"]  or "")[:10]

        celdas        = {}
        tots          = defaultdict(float)
        feriados_trab = 0.0

        for fecha in fechas:
            if cortado:
                b1 = _resolver(eid, fecha, 1, f_ing, f_egr, nov_map, ali_set, res_map, plan_map)
                b2 = _resolver(eid, fecha, 2, f_ing, f_egr, nov_map, ali_set, res_map, plan_map)
                celdas[fecha] = {"b1": b1, "b2": b2}
                for b in (b1, b2):
                    l = b["letra"]
                    if l and b["tipo"] == "normal" and l != "A!!!":
                        tots[l] += 0.5
                        if l in CONTABLES:
                            tots["dias"] += 0.5
                if fecha in feriados:
                    letras_feri = [b["letra"] for b in (b1, b2)
                                   if b.get("tipo") == "normal" and b.get("letra") in ("I", "T", "FT", "@", "NF")]
                    if any(l != "@" for l in letras_feri):
                        feriados_trab += len(letras_feri) * 0.5
            else:
                plan = plan_map.get((eid, fecha))
                dia_cortado = plan and plan.get("horario_tipo") == "cortado"
                if dia_cortado:
                    b1 = _resolver(eid, fecha, 1, f_ing, f_egr, nov_map, ali_set, res_map, plan_map)
                    b2 = _resolver(eid, fecha, 2, f_ing, f_egr, nov_map, ali_set, res_map, plan_map)
                    celdas[fecha] = {"b1": b1, "b2": b2, "es_cortado_dia": True}
                    for b in (b1, b2):
                        l = b["letra"]
                        if l and b["tipo"] == "normal" and l != "A!!!":
                            tots[l] += 0.5
                            if l in CONTABLES:
                                tots["dias"] += 0.5
                    if fecha in feriados:
                        letras_feri = [b["letra"] for b in (b1, b2)
                                       if b.get("tipo") == "normal" and b.get("letra") in ("I", "T", "FT", "@", "NF")]
                        if any(l != "@" for l in letras_feri):
                            feriados_trab += len(letras_feri) * 0.5
                else:
                    c = _resolver(eid, fecha, 0, f_ing, f_egr, nov_map, ali_set, res_map, plan_map)
                    celdas[fecha] = c
                    l = c["letra"]
                    if l and c["tipo"] == "normal" and l != "A!!!":
                        tots[l] += 1.0
                        if l in CONTABLES:
                            tots["dias"] += 1.0
                        if fecha in feriados and l in ("I", "T", "FT", "NF"):
                            feriados_trab += 1.0

        transporte = transporte_map.get(eid, 0)
        saldo_mes  = tots.get("FD", 0) - tots.get("FT", 0)
        control    = _calcular_control(fechas, f_egr, celdas, cortado, f0, f1,
                                       date.today().isoformat())

        grupos[grupo].append({
            "id":         eid,
            "cod":        emp["user_id"],
            "nombre":     emp["nombre"],
            "apellido":   emp["apellido"],
            "cargo":      emp["cargo"] or "",
            "fecha_ingreso": f_ing or None,
            "fecha_egreso": f_egr or None,
            "celdas":     celdas,
            "control":    control,
            "comentarios": comentarios_map.get(eid, []),
            "totales": {
                **{k: _fmt(v) for k, v in tots.items()},
                "transporte":         _fmt(transporte),
                "saldo_francos":      _fmt(saldo_mes),
                "feriados_trabajados": _fmt(feriados_trab),
                "FM":                 fm_count.get(eid, 0),
            },
        })

    return {
        "mes":  mes,
        "dias": _build_dias(fechas, feriados),
        "TM":   grupos["TM"],
        "TN":   grupos["TN"],
        "CO":   grupos["CO"],
        "ST":   grupos["ST"],
    }


# ── Novedades CRUD ────────────────────────────────────────────────────────────
class NovedadIn(BaseModel):
    empleado_id: int
    fecha:       str
    bloque:      int = 0
    tipo:        str
    descripcion: Optional[str] = None


@router.post("/novedades", status_code=201)
def upsert_novedad(data: NovedadIn, user=Depends(require_permiso("asistencia", "corregir"))):
    if data.tipo not in LETRAS_VALIDAS:
        raise HTTPException(400, f"Tipo inválido. Válidos: {sorted(LETRAS_VALIDAS)}")
    if data.tipo == "@" and data.bloque not in (1, 2):
        raise HTTPException(400, "La aliviada (@) requiere bloque 1 o 2")
    if data.tipo == "CP" and not data.descripcion:
        raise HTTPException(400, "La compensación de presencia requiere una descripción")
    if data.tipo == "V":
        from api.vacaciones import _calcular_dias_formula, _get_arrastre
        anio_periodo = int(data.fecha[:4]) - 1
        with db_session() as conn:
            emp = conn.execute("SELECT fecha_ingreso FROM empleados WHERE id=?", (data.empleado_id,)).fetchone()
            saldos = {(r["empleado_id"], r["anio"]): dict(r)
                      for r in conn.execute("SELECT * FROM vacaciones_saldo_inicial").fetchall()}
            dias_v_ya = conn.execute("""
                SELECT SUM(CASE WHEN bloque=0 THEN 1.0 ELSE 0.5 END)
                FROM novedades
                WHERE tipo='V' AND empleado_id=? AND strftime('%Y',fecha)=?
                  AND NOT (fecha=? AND bloque=?)
            """, (data.empleado_id, str(int(data.fecha[:4])),
                  data.fecha, data.bloque)).fetchone()[0] or 0.0
        fi = emp["fecha_ingreso"] if emp else None
        _, dias_formula = _calcular_dias_formula(fi, anio_periodo)
        saldo = saldos.get((data.empleado_id, anio_periodo))
        nueva = 0.5 if data.bloque > 0 else 1.0
        if saldo:
            dias_restan = saldo["dias_correspondian"] - saldo["dias_tomados"] - dias_v_ya
        else:
            arrastre = _get_arrastre(data.empleado_id, anio_periodo, fi, saldos,
                                     {(data.empleado_id, int(data.fecha[:4])): {"01": dias_v_ya} if dias_v_ya else {}})
            dias_restan = dias_formula + arrastre - dias_v_ya
        dias_restan = round(dias_restan, 1)
        if dias_restan < nueva:
            raise HTTPException(400, f"Sin días de vacaciones disponibles. Quedan {dias_restan:.1f} día(s).")
    creado_por = user.get("email") or user.get("sub")
    from api.periodos_cerrados import check_periodo_abierto
    with db_session() as conn:
        check_periodo_abierto(conn, data.fecha)
        conn.execute(
            """INSERT INTO novedades (empleado_id, fecha, bloque, tipo, descripcion, creado_por)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(empleado_id, fecha, bloque)
               DO UPDATE SET tipo=excluded.tipo, descripcion=excluded.descripcion,
                             creado_por=excluded.creado_por""",
            (data.empleado_id, data.fecha, data.bloque, data.tipo, data.descripcion, creado_por),
        )
        row = conn.execute(
            "SELECT * FROM novedades WHERE empleado_id=? AND fecha=? AND bloque=?",
            (data.empleado_id, data.fecha, data.bloque),
        ).fetchone()
    return dict(row)


class NovedadRangoIn(BaseModel):
    empleado_id: int
    fecha_desde: str
    fecha_hasta: str
    bloque:      int = 0


# Ruta estática antes que la parametrizada para evitar que {nov_id} la capture
@router.post("/novedades/borrar-rango", status_code=200)
def eliminar_novedades_rango(data: NovedadRangoIn, _user=Depends(require_permiso("asistencia", "corregir"))):
    if data.fecha_hasta < data.fecha_desde:
        raise HTTPException(400, "fecha_hasta debe ser >= fecha_desde")
    from api.periodos_cerrados import check_rango_abierto
    with db_session() as conn:
        check_rango_abierto(conn, data.fecha_desde, data.fecha_hasta)
        tipos_afectados = {
            r["tipo"] for r in conn.execute(
                "SELECT DISTINCT tipo FROM novedades WHERE empleado_id=? AND fecha>=? AND fecha<=? AND bloque=?",
                (data.empleado_id, data.fecha_desde, data.fecha_hasta, data.bloque),
            ).fetchall()
        }
        conn.execute(
            """DELETE FROM novedades
               WHERE empleado_id=? AND fecha>=? AND fecha<=? AND bloque=?""",
            (data.empleado_id, data.fecha_desde, data.fecha_hasta, data.bloque),
        )
    if tipos_afectados & {"CP", "NF"}:
        try:
            from sync.evaluador import evaluar_rango
            evaluar_rango(data.fecha_desde, data.fecha_hasta, respetar_correcciones=False)
        except Exception:
            pass
    return {"ok": True}


@router.delete("/novedades/{nov_id}")
def eliminar_novedad(nov_id: int, _user=Depends(require_permiso("asistencia", "corregir"))):
    from api.periodos_cerrados import check_periodo_abierto
    with db_session() as conn:
        row = conn.execute("SELECT id, fecha, tipo, empleado_id FROM novedades WHERE id=?", (nov_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Novedad no encontrada")
        check_periodo_abierto(conn, row["fecha"])
        conn.execute("DELETE FROM novedades WHERE id=?", (nov_id,))
        tipo, fecha, empleado_id = row["tipo"], row["fecha"], row["empleado_id"]
    if tipo in ("CP", "NF"):
        try:
            from sync.evaluador import evaluar_fecha
            evaluar_fecha(fecha, respetar_correcciones=False, solo_empleado_id=empleado_id)
        except Exception:
            pass
    return {"ok": True}


# ── Saldo francos (TRANSPORTE carry-forward) ──────────────────────────────────
@router.put("/saldo_francos/{empleado_id}/{mes}")
def set_saldo_francos(empleado_id: int, mes: str, body: dict, _user=Depends(require_permiso("asistencia", "corregir"))):
    saldo = float(body.get("saldo", 0))
    from api.periodos_cerrados import check_periodo_abierto
    with db_session() as conn:
        check_periodo_abierto(conn, f"{mes}-01")
        conn.execute(
            """INSERT INTO saldo_francos (empleado_id, mes, saldo) VALUES (?,?,?)
               ON CONFLICT(empleado_id, mes) DO UPDATE SET saldo=excluded.saldo""",
            (empleado_id, mes, saldo),
        )
    return {"ok": True}


# ── Orden de planilla ─────────────────────────────────────────────────────────
class OrdenItem(BaseModel):
    empleado_id: Optional[int] = None
    etiqueta:    Optional[str] = None


@router.get("/orden/{grupo}")
def get_orden(grupo: str, mes: str | None = None, _user=Depends(require_permiso("asistencia", "ver"))):
    if grupo not in ("TM", "TN", "CO"):
        raise HTTPException(400, "Grupo inválido")
    with db_session() as conn:
        rows = conn.execute(
            "SELECT id, posicion, empleado_id, etiqueta FROM planilla_orden "
            "WHERE grupo=? ORDER BY posicion",
            (grupo,),
        ).fetchall()

        if mes:
            try:
                from datetime import date
                y, m = map(int, mes.split("-"))
                import calendar
                f0 = f"{y}-{m:02d}-01"
                f1 = f"{y}-{m:02d}-{calendar.monthrange(y, m)[1]:02d}"
            except Exception:
                return [dict(r) for r in rows]

            # Determinar grupo real de cada empleado para ese mes
            emp_grupo = conn.execute("""
                WITH ult AS (
                    SELECT p.empleado_id,
                           h.tipo, t.nombre AS turno_nombre,
                           ROW_NUMBER() OVER (
                               PARTITION BY p.empleado_id ORDER BY p.fecha DESC
                           ) AS rn
                    FROM planificacion p
                    JOIN horarios h ON h.id = p.horario_id
                    LEFT JOIN turnos t ON t.id = h.turno_id
                    WHERE p.fecha >= ? AND p.fecha <= ? AND p.horario_id IS NOT NULL
                )
                SELECT empleado_id,
                    CASE
                        WHEN tipo = 'cortado' THEN 'CO'
                        WHEN lower(COALESCE(turno_nombre,'')) LIKE '%noche%'
                          OR lower(COALESCE(turno_nombre,'')) LIKE '%madrugada%' THEN 'TN'
                        ELSE 'TM'
                    END AS grupo
                FROM ult WHERE rn = 1
            """, (f0, f1)).fetchall()
            ids_del_grupo = {r["empleado_id"] for r in emp_grupo if r["grupo"] == grupo}

            rows = [r for r in rows
                    if r["empleado_id"] is None or r["empleado_id"] in ids_del_grupo]

    return [dict(r) for r in rows]


@router.post("/orden/{grupo}", status_code=200)
def set_orden(grupo: str, items: list[OrdenItem], _user=Depends(require_permiso("asistencia", "editar"))):
    if grupo not in ("TM", "TN", "CO"):
        raise HTTPException(400, "Grupo inválido")
    with db_session() as conn:
        conn.execute("DELETE FROM planilla_orden WHERE grupo=?", (grupo,))
        for pos, item in enumerate(items):
            conn.execute(
                "INSERT INTO planilla_orden (grupo, posicion, empleado_id, etiqueta) VALUES (?,?,?,?)",
                (grupo, pos, item.empleado_id, item.etiqueta),
            )
    return {"ok": True}
