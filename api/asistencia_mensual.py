from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
from datetime import date, timedelta
from collections import defaultdict
from db.database import db_session

router = APIRouter(prefix="/api/asistencia", tags=["asistencia_mensual"])

# ── Mapeo estado evaluador → letra planilla ────────────────────────────────────
_EST_SIMPLE = {
    "ok": "I", "salida_anticipada": "I", "sin_salida": "I", "sin_horario": "I",
    "tarde": "T", "tarde_y_salida_anticipada": "T", "tarde_y_sin_salida": "T",
    "ausente": "A", "franco": "F", "ft": "FT",
    "b1_ausente": "A", "b2_ausente": "A",
    "nf": "NF",
}
_EST_B1 = {**_EST_SIMPLE, "b1_ausente": "A", "b2_ausente": "I"}
_EST_B2 = {
    "ok": "I", "salida_anticipada": "I", "sin_salida": "I", "sin_horario": "I",
    "tarde": "I",                        # tarde afecta la entrada del b1
    "tarde_y_salida_anticipada": "I",
    "tarde_y_sin_salida": "I",
    "ausente": "A", "franco": "F", "ft": "FT",
    "b1_ausente": "I", "b2_ausente": "A",
    "nf": "NF",
}

CONTABLES     = {"I", "T", "F", "FT", "FD", "V", "L", "LSG", "S", "@", "NF"}
LETRAS_VALIDAS = {"ILT", "LSG", "L", "E", "V", "S", "FT", "FD", "@", "NF"}
DIAS_SEMANA   = ["lu", "ma", "mi", "ju", "vi", "sá", "do"]


# ── Resolución de una celda/bloque ────────────────────────────────────────────
def _resolver(eid, fecha, bloque, f_ing, f_egr, nov_map, ali_set, res_map, plan_map):
    if f_ing and fecha < f_ing:
        return {"letra": None, "tipo": "fuera"}
    if f_egr and fecha > f_egr:
        return {"letra": "X", "tipo": "liquidacion"}

    # 1. Novedad manual: bloque específico primero, luego bloque=0 (día entero)
    nov = nov_map.get((eid, fecha, bloque))
    if nov is None and bloque != 0:
        nov = nov_map.get((eid, fecha, 0))
    if nov:
        return {"letra": nov["tipo"], "tipo": "normal", "nov_id": nov["id"]}

    # 2. Aliviada (solo bloques 1 y 2)
    if bloque in (1, 2) and (eid, fecha, bloque) in ali_set:
        return {"letra": "@", "tipo": "normal"}

    # 3. Resultado automático del evaluador
    res = res_map.get((eid, fecha))
    if res:
        estado = res["estado"]
        mapa   = _EST_B1 if bloque == 1 else (_EST_B2 if bloque == 2 else _EST_SIMPLE)
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
def asistencia_mensual(mes: str = Query(None)):
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
            SELECT e.id, e.user_id, e.nombre, e.apellido, e.cargo,
                   e.fecha_ingreso, e.fecha_egreso,
                   u.tipo AS hipo, u.turno_nombre
            FROM empleados e
            JOIN ult u ON u.empleado_id = e.id AND u.rn = 1
            WHERE e.tipo != 'acceso'
              AND (e.activo = 1
               OR (e.fecha_egreso >= ? AND e.fecha_egreso <= ?))
            ORDER BY e.apellido, e.nombre
        """, (f0, f1, f0, f1)).fetchall()

        if not emp_rows:
            return {"mes": mes, "dias": _build_dias(fechas, feriados),
                    "TM": [], "TN": [], "CO": []}

        eids = [r["id"] for r in emp_rows]
        ph   = ",".join("?" * len(eids))

        planes = conn.execute(
            f"SELECT empleado_id, fecha, es_franco, horario_id "
            f"FROM planificacion WHERE empleado_id IN ({ph}) AND fecha>=? AND fecha<=?",
            (*eids, f0, f1)
        ).fetchall()

        resultados = conn.execute(
            f"SELECT empleado_id, fecha, estado, horario_id "
            f"FROM resultados_dia WHERE empleado_id IN ({ph}) AND fecha>=? AND fecha<=?",
            (*eids, f0, f1)
        ).fetchall()

        novedades = conn.execute(
            f"SELECT id, empleado_id, fecha, bloque, tipo "
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
    res_map  = {(r["empleado_id"], r["fecha"]): {"estado": r["estado"], "horario_id": r["horario_id"]} for r in resultados}

    fm_count: dict[int, int] = defaultdict(int)
    for f in fichajes_manuales:
        fm_count[f["empleado_id"]] += 1
    nov_map  = {}
    for n in novedades:
        nov_map[(n["empleado_id"], n["fecha"], n["bloque"])] = {
            "id": n["id"], "tipo": n["tipo"]
        }
    ali_set       = {(a["empleado_id"], a["fecha"], a["bloque"]) for a in aliviadas}
    transporte_map = {s["empleado_id"]: s["saldo"] for s in saldos}

    grupos = {"TM": [], "TN": [], "CO": []}

    for emp in emp_rows:
        eid       = emp["id"]
        turno     = (emp["turno_nombre"] or "").lower()
        cortado   = emp["hipo"] == "cortado"
        grupo     = "CO" if cortado else (
                    "TN" if ("noche" in turno or "madrugada" in turno) else "TM")

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
                    if l and b["tipo"] == "normal":
                        tots[l] += 0.5
                        if l in CONTABLES:
                            tots["dias"] += 0.5
                if fecha in feriados:
                    letras_feri = [b["letra"] for b in (b1, b2)
                                   if b.get("tipo") == "normal" and b.get("letra") in ("I", "T", "FT", "@")]
                    if any(l != "@" for l in letras_feri):
                        feriados_trab += len(letras_feri) * 0.5
            else:
                c = _resolver(eid, fecha, 0, f_ing, f_egr, nov_map, ali_set, res_map, plan_map)
                celdas[fecha] = c
                l = c["letra"]
                if l and c["tipo"] == "normal":
                    tots[l] += 1.0
                    if l in CONTABLES:
                        tots["dias"] += 1.0
                    if fecha in feriados and l in ("I", "T", "FT"):
                        feriados_trab += 1.0

        transporte = transporte_map.get(eid, 0)
        saldo_mes  = tots.get("FD", 0) - tots.get("FT", 0)

        grupos[grupo].append({
            "id":       eid,
            "cod":      emp["user_id"],
            "nombre":   emp["nombre"],
            "apellido": emp["apellido"],
            "cargo":    emp["cargo"] or "",
            "celdas":   celdas,
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
    }


# ── Novedades CRUD ────────────────────────────────────────────────────────────
class NovedadIn(BaseModel):
    empleado_id: int
    fecha:       str
    bloque:      int = 0
    tipo:        str
    descripcion: Optional[str] = None


@router.post("/novedades", status_code=201)
def upsert_novedad(data: NovedadIn):
    if data.tipo not in LETRAS_VALIDAS:
        raise HTTPException(400, f"Tipo inválido. Válidos: {sorted(LETRAS_VALIDAS)}")
    if data.tipo == "@" and data.bloque not in (1, 2):
        raise HTTPException(400, "La aliviada (@) requiere bloque 1 o 2")
    with db_session() as conn:
        conn.execute(
            """INSERT INTO novedades (empleado_id, fecha, bloque, tipo, descripcion)
               VALUES (?,?,?,?,?)
               ON CONFLICT(empleado_id, fecha, bloque)
               DO UPDATE SET tipo=excluded.tipo, descripcion=excluded.descripcion""",
            (data.empleado_id, data.fecha, data.bloque, data.tipo, data.descripcion),
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
def eliminar_novedades_rango(data: NovedadRangoIn):
    if data.fecha_hasta < data.fecha_desde:
        raise HTTPException(400, "fecha_hasta debe ser >= fecha_desde")
    with db_session() as conn:
        conn.execute(
            """DELETE FROM novedades
               WHERE empleado_id=? AND fecha>=? AND fecha<=? AND bloque=?""",
            (data.empleado_id, data.fecha_desde, data.fecha_hasta, data.bloque),
        )
    return {"ok": True}


@router.delete("/novedades/{nov_id}")
def eliminar_novedad(nov_id: int):
    with db_session() as conn:
        if not conn.execute("SELECT id FROM novedades WHERE id=?", (nov_id,)).fetchone():
            raise HTTPException(404, "Novedad no encontrada")
        conn.execute("DELETE FROM novedades WHERE id=?", (nov_id,))
    return {"ok": True}


# ── Saldo francos (TRANSPORTE carry-forward) ──────────────────────────────────
@router.put("/saldo_francos/{empleado_id}/{mes}")
def set_saldo_francos(empleado_id: int, mes: str, body: dict):
    saldo = float(body.get("saldo", 0))
    with db_session() as conn:
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
def get_orden(grupo: str):
    if grupo not in ("TM", "TN", "CO"):
        raise HTTPException(400, "Grupo inválido")
    with db_session() as conn:
        rows = conn.execute(
            "SELECT id, posicion, empleado_id, etiqueta FROM planilla_orden "
            "WHERE grupo=? ORDER BY posicion",
            (grupo,),
        ).fetchall()
    return [dict(r) for r in rows]


@router.post("/orden/{grupo}", status_code=200)
def set_orden(grupo: str, items: list[OrdenItem]):
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
