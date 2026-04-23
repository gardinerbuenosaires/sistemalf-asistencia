"""
Evalúa fichajes contra planificación usando asignación por proximidad temporal.

Para cada empleado y día:
  1. Toma todos sus fichajes (ignora el tipo almacenado — puede estar mal por alternancia simple)
  2. Asigna cada fichaje al slot del horario más cercano en tiempo
     (b1_entrada, b1_salida, b2_entrada, b2_salida)
  3. Calcula tardanza y salida anticipada sobre los fichajes asignados

Ventaja: una llegada muy tarde sigue siendo "entrada bloque 1 con X minutos de tardanza"
en lugar de generar una inconsistencia que requiere resolución manual.
"""
import logging
from datetime import datetime, date, timedelta
from collections import defaultdict
from db.database import db_session

logger = logging.getLogger(__name__)


def _dt(fecha: date, hora_str: str, dia_sig: bool = False) -> datetime:
    h, m = map(int, hora_str.split(":"))
    d = fecha + timedelta(days=1) if dia_sig else fecha
    return datetime(d.year, d.month, d.day, h, m)


def _clasificar_fichajes(fichajes: list, bloques: list, fecha: date) -> dict:
    """
    Asigna cada fichaje al slot del horario más cercano (greedy por distancia mínima).
    Retorna {slot_name: fichaje_dict | None}.
    """
    slots = []
    for b in bloques:
        cruza = bool(b["cruza_medianoche"])
        bn = b["bloque"]
        slots.append((f"b{bn}_entrada", _dt(fecha, b["hora_entrada"])))
        slots.append((f"b{bn}_salida",  _dt(fecha, b["hora_salida"], dia_sig=cruza)))

    resultado = {nombre: None for nombre, _ in slots}
    if not fichajes:
        return resultado

    # Todas las combinaciones (fichaje, slot) ordenadas por distancia
    candidatos = []
    for fi, f in enumerate(fichajes):
        ft = datetime.fromisoformat(f["timestamp"])
        for slot_nombre, slot_time in slots:
            dist = abs((ft - slot_time).total_seconds() / 60)
            candidatos.append((dist, fi, slot_nombre))
    candidatos.sort()

    usados_fichaje = set()
    for dist, fi, slot_nombre in candidatos:
        if fi in usados_fichaje or resultado[slot_nombre] is not None:
            continue
        usados_fichaje.add(fi)
        resultado[slot_nombre] = fichajes[fi]

    return resultado


def _evaluar_bloque(bloque: dict, fecha: date, f_entrada, f_salida) -> dict:
    """Evalúa un bloque dado los fichajes asignados a sus slots (pueden ser None)."""
    if f_entrada is None and f_salida is None:
        return {"ausente": True, "entrada": None, "salida": None,
                "minutos_tarde": None, "salida_anticipada": False}

    cruza = bool(bloque["cruza_medianoche"])
    t_entrada = _dt(fecha, bloque["hora_entrada"])
    t_salida  = _dt(fecha, bloque["hora_salida"], dia_sig=cruza)

    minutos_tarde = None
    if f_entrada:
        real_entrada  = datetime.fromisoformat(f_entrada["timestamp"])
        limite_tarde  = t_entrada + timedelta(minutes=bloque["tolerancia_tarde"])
        if real_entrada > limite_tarde:
            minutos_tarde = int((real_entrada - t_entrada).total_seconds() // 60)

    salida_anticipada = False
    if f_salida:
        real_salida   = datetime.fromisoformat(f_salida["timestamp"])
        limite_salida = t_salida - timedelta(minutes=bloque["tolerancia_salida_antes"])
        if real_salida < limite_salida:
            salida_anticipada = True

    return {
        "ausente":           False,
        "entrada":           f_entrada["timestamp"] if f_entrada else None,
        "salida":            f_salida["timestamp"]  if f_salida  else None,
        "minutos_tarde":     minutos_tarde,
        "salida_anticipada": salida_anticipada,
    }


def _calcular_estado(b1: dict, b2: dict | None) -> str:
    if b1["ausente"] and (b2 is None or b2["ausente"]):
        return "ausente"

    tarde = bool(b1.get("minutos_tarde")) or bool(b2 and b2.get("minutos_tarde"))
    ant   = b1["salida_anticipada"] or (b2 and b2["salida_anticipada"])

    if b2 is not None:
        if b1["ausente"]: return "b1_ausente"
        if b2["ausente"]: return "b2_ausente"

    if tarde and ant: return "tarde_y_salida_anticipada"
    if tarde:         return "tarde"
    if ant:           return "salida_anticipada"
    return "ok"


def horarios_sin_cerrar(fecha_str: str | None = None) -> list[dict]:
    """
    Devuelve los bloques cuya hora de salida aún no llegó hoy.
    Se usa para avisar al operador que los resultados pueden ser parciales.
    """
    from datetime import datetime as dt_now
    ahora = dt_now.now()
    hoy   = fecha_str or str(ahora.date())
    if hoy != str(ahora.date()):
        return []   # fechas pasadas ya cerraron

    with db_session() as conn:
        bloques = conn.execute(
            "SELECT h.nombre as horario_nombre, hb.bloque, hb.hora_salida "
            "FROM horarios_bloques hb JOIN horarios h ON h.id=hb.horario_id "
            "WHERE h.activo=1"
        ).fetchall()

    pendientes = []
    for b in bloques:
        h, m = map(int, b["hora_salida"].split(":"))
        t_salida = ahora.replace(hour=h, minute=m, second=0, microsecond=0)
        if ahora < t_salida:
            pendientes.append({
                "horario": b["horario_nombre"],
                "bloque":  b["bloque"],
                "hora_salida": b["hora_salida"],
            })
    return pendientes


def evaluar_fecha(fecha_str: str) -> dict:
    """Procesa todos los empleados con planificación en fecha_str."""
    fecha     = date.fromisoformat(fecha_str)
    fecha_sig = str(fecha + timedelta(days=1))
    resumen   = {"evaluados": 0, "ok": 0, "tarde": 0, "ausente": 0, "franco": 0, "otros": 0}

    with db_session() as conn:
        planes = conn.execute(
            "SELECT p.*, h.tipo as h_tipo FROM planificacion p "
            "LEFT JOIN horarios h ON h.id = p.horario_id WHERE p.fecha = ?",
            (fecha_str,)
        ).fetchall()

        if not planes:
            return resumen

        hids = list({p["horario_id"] for p in planes if p["horario_id"]})
        bloques_map: dict[int, list] = defaultdict(list)
        if hids:
            ph = ",".join("?" * len(hids))
            for b in conn.execute(
                f"SELECT * FROM horarios_bloques WHERE horario_id IN ({ph}) ORDER BY horario_id, bloque",
                hids
            ).fetchall():
                bloques_map[b["horario_id"]].append(dict(b))

        # Fichajes del día y siguiente — tipo ignorado, clasificamos por proximidad
        eids = [p["empleado_id"] for p in planes]
        pe   = ",".join("?" * len(eids))
        fichajes_rows = conn.execute(
            f"SELECT * FROM fichajes "
            f"WHERE empleado_id IN ({pe}) AND (date(timestamp)=? OR date(timestamp)=?) "
            f"ORDER BY empleado_id, timestamp",
            eids + [fecha_str, fecha_sig]
        ).fetchall()

        fich_map: dict[int, list] = defaultdict(list)
        for f in fichajes_rows:
            fich_map[f["empleado_id"]].append(dict(f))

        for plan in planes:
            eid = plan["empleado_id"]
            p   = dict(plan)

            if p["es_franco"]:
                estado, b1r, b2r = "franco", {}, {}
            elif not p["horario_id"]:
                estado, b1r, b2r = "sin_horario", {}, {}
            else:
                bloques = bloques_map.get(p["horario_id"], [])
                if not bloques:
                    estado, b1r, b2r = "sin_horario", {}, {}
                else:
                    slots = _clasificar_fichajes(fich_map[eid], bloques, fecha)
                    b1r = _evaluar_bloque(
                        bloques[0], fecha,
                        slots.get("b1_entrada"), slots.get("b1_salida")
                    )
                    b2r = None
                    if len(bloques) > 1:
                        b2r = _evaluar_bloque(
                            bloques[1], fecha,
                            slots.get("b2_entrada"), slots.get("b2_salida")
                        )
                    estado = _calcular_estado(b1r, b2r)

            conn.execute(
                """INSERT INTO resultados_dia
                   (empleado_id, fecha, horario_id, es_franco, estado,
                    b1_entrada, b1_salida, b1_minutos_tarde, b1_salida_anticipada, b1_ausente,
                    b2_entrada, b2_salida, b2_minutos_tarde, b2_salida_anticipada, b2_ausente,
                    procesado_en)
                   VALUES (?,?,?,?,?, ?,?,?,?,?, ?,?,?,?,?, datetime('now','localtime'))
                   ON CONFLICT(empleado_id, fecha) DO UPDATE SET
                   horario_id=excluded.horario_id, es_franco=excluded.es_franco, estado=excluded.estado,
                   b1_entrada=excluded.b1_entrada, b1_salida=excluded.b1_salida,
                   b1_minutos_tarde=excluded.b1_minutos_tarde,
                   b1_salida_anticipada=excluded.b1_salida_anticipada, b1_ausente=excluded.b1_ausente,
                   b2_entrada=excluded.b2_entrada, b2_salida=excluded.b2_salida,
                   b2_minutos_tarde=excluded.b2_minutos_tarde,
                   b2_salida_anticipada=excluded.b2_salida_anticipada, b2_ausente=excluded.b2_ausente,
                   procesado_en=datetime('now','localtime')""",
                (eid, fecha_str, p.get("horario_id"), p["es_franco"], estado,
                 b1r.get("entrada"), b1r.get("salida"), b1r.get("minutos_tarde"),
                 int(b1r.get("salida_anticipada", False)), int(b1r.get("ausente", False)),
                 (b2r or {}).get("entrada"), (b2r or {}).get("salida"), (b2r or {}).get("minutos_tarde"),
                 int((b2r or {}).get("salida_anticipada", False)), int((b2r or {}).get("ausente", False)))
            )

            resumen["evaluados"] += 1
            if estado == "ok":        resumen["ok"] += 1
            elif estado == "franco":  resumen["franco"] += 1
            elif estado == "ausente": resumen["ausente"] += 1
            elif "tarde" in estado:   resumen["tarde"] += 1
            else:                     resumen["otros"] += 1

    logger.info("Evaluados %s: %s", fecha_str, resumen)
    return resumen


def evaluar_rango(fecha_desde: str, fecha_hasta: str) -> dict:
    """Evalúa todos los días entre dos fechas."""
    d = date.fromisoformat(fecha_desde)
    h = date.fromisoformat(fecha_hasta)
    total = {"evaluados": 0, "ok": 0, "tarde": 0, "ausente": 0, "franco": 0, "otros": 0}
    while d <= h:
        r = evaluar_fecha(str(d))
        for k in total:
            total[k] += r.get(k, 0)
        d += timedelta(days=1)
    return total
