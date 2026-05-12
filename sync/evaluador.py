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

MAX_DIST_MIN = 240  # ventana máxima (minutos) para asignar un fichaje a un slot


def _dt(fecha: date, hora_str: str, dia_sig: bool = False) -> datetime:
    h, m = map(int, hora_str.split(":"))
    d = fecha + timedelta(days=1) if dia_sig else fecha
    return datetime(d.year, d.month, d.day, h, m)


def _clasificar_fichajes(fichajes: list, bloques: list, fecha: date) -> dict:
    """
    Asigna cada fichaje al slot del horario más cercano. Si varios fichajes
    caen en el mismo slot (vestuario, re-fichaje por duda), se resuelve por
    dirección: el más temprano para entradas, el más tardío para salidas.
    """
    slots = []
    for b in bloques:
        if not b.get("aplica", 1):
            continue  # bloque MT: no crear slots, no atraer fichajes
        cruza = bool(b["cruza_medianoche"])
        bn = b["bloque"]
        slots.append((f"b{bn}_entrada", _dt(fecha, b["hora_entrada"])))
        slots.append((f"b{bn}_salida",  _dt(fecha, b["hora_salida"], dia_sig=cruza)))

    # Inicializar todas las keys posibles (incluso las de bloques MT que no tienen slot)
    todas_keys = [f"b{b['bloque']}_{lado}" for b in bloques for lado in ("entrada", "salida")]
    acumulados: dict[str, list] = {k: [] for k in todas_keys}

    if slots:
        for f in fichajes:
            ft = datetime.fromisoformat(f["timestamp"])
            nearest = min(slots, key=lambda s: abs((ft - s[1]).total_seconds()))
            dist_min = abs((ft - nearest[1]).total_seconds()) / 60
            if dist_min > MAX_DIST_MIN:
                continue
            acumulados[nearest[0]].append(f)

    resultado: dict[str, dict | None] = {}
    for slot_nombre, candidatos in acumulados.items():
        if not candidatos:
            resultado[slot_nombre] = None
        elif slot_nombre.endswith("_entrada"):
            resultado[slot_nombre] = min(candidatos, key=lambda f: f["timestamp"])
        else:
            resultado[slot_nombre] = max(candidatos, key=lambda f: f["timestamp"])

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
    sin_salida = f_entrada is not None and f_salida is None
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
        "sin_salida":        sin_salida,
    }


def _calcular_estado(b1: dict, b2: dict | None) -> str:
    if b1["ausente"] and (b2 is None or b2["ausente"]):
        return "ausente"

    tarde     = bool(b1.get("minutos_tarde")) or bool(b2 and b2.get("minutos_tarde"))
    ant       = b1["salida_anticipada"] or (b2 and b2["salida_anticipada"])
    sin_sal   = b1.get("sin_salida") or (b2 and b2.get("sin_salida"))

    if b2 is not None:
        if b1["ausente"]: return "b1_ausente"
        if b2["ausente"]: return "b2_ausente"

    if tarde and sin_sal: return "tarde_y_sin_salida"
    if tarde and ant:     return "tarde_y_salida_anticipada"
    if tarde:             return "tarde"
    if sin_sal:           return "sin_salida"
    if ant:               return "salida_anticipada"
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


def recalcular_resultado(empleado_id: int, fecha_str: str):
    """
    Re-evalúa estado/tardanzas de un resultado ya existente usando los timestamps
    actuales en la fila (tras una corrección manual). No toca los fichaje_ids
    ni los campos de auditoría.
    """
    fecha = date.fromisoformat(fecha_str)
    with db_session() as conn:
        r = conn.execute(
            "SELECT * FROM resultados_dia WHERE empleado_id=? AND fecha=?",
            (empleado_id, fecha_str)
        ).fetchone()
        if not r or r["es_franco"] or not r["horario_id"]:
            return
        r = dict(r)
        bloques = [dict(b) for b in conn.execute(
            "SELECT * FROM horarios_bloques WHERE horario_id=? ORDER BY bloque",
            (r["horario_id"],)
        ).fetchall()]
        if not bloques:
            return

        def _faux(ts):
            return {"timestamp": ts} if ts else None

        b1r = _evaluar_bloque(bloques[0], fecha, _faux(r["b1_entrada"]), _faux(r["b1_salida"]))
        b2r = None
        if len(bloques) > 1:
            b2r = _evaluar_bloque(bloques[1], fecha, _faux(r["b2_entrada"]), _faux(r["b2_salida"]))

        b1_mt = bool(r.get("b1_mt"))
        b2_mt = bool(r.get("b2_mt"))
        if b1_mt and b2r is not None:
            estado = _calcular_estado(b2r, None)
        elif b2_mt and b2r is not None:
            estado = _calcular_estado(b1r, None)
        else:
            estado = _calcular_estado(b1r, b2r)

        conn.execute(
            """UPDATE resultados_dia SET
               estado=?,
               b1_minutos_tarde=?, b1_salida_anticipada=?, b1_ausente=?, b1_sin_salida=?,
               b2_minutos_tarde=?, b2_salida_anticipada=?, b2_ausente=?, b2_sin_salida=?
               WHERE empleado_id=? AND fecha=?""",
            (estado,
             b1r.get("minutos_tarde"), int(b1r.get("salida_anticipada", False)),
             int(b1r.get("ausente", False)), int(b1r.get("sin_salida", False)),
             (b2r or {}).get("minutos_tarde"), int((b2r or {}).get("salida_anticipada", False)),
             int((b2r or {}).get("ausente", False)), int((b2r or {}).get("sin_salida", False)),
             empleado_id, fecha_str)
        )


def evaluar_fecha(fecha_str: str, respetar_correcciones: bool = True,
                  solo_empleado_id: int | None = None) -> dict:
    """Procesa todos los empleados con planificación en fecha_str."""
    fecha     = date.fromisoformat(fecha_str)
    fecha_sig = str(fecha + timedelta(days=1))
    resumen   = {"evaluados": 0, "ok": 0, "tarde": 0, "ausente": 0, "franco": 0, "otros": 0, "saltados": 0}

    with db_session() as conn:
        sql    = ("SELECT p.*, h.tipo as h_tipo FROM planificacion p "
                  "LEFT JOIN horarios h ON h.id = p.horario_id WHERE p.fecha = ?")
        params: tuple = (fecha_str,)
        if solo_empleado_id:
            sql    += " AND p.empleado_id = ?"
            params += (solo_empleado_id,)
        planes = conn.execute(sql, params).fetchall()

        if not planes:
            return resumen

        corregidos: set[int] = set()
        if respetar_correcciones:
            rows_c = conn.execute(
                "SELECT empleado_id FROM resultados_dia WHERE fecha=? AND corregido_manualmente=1",
                (fecha_str,)
            ).fetchall()
            corregidos = {r["empleado_id"] for r in rows_c}

        nf_set: set[int] = {
            r["empleado_id"] for r in conn.execute(
                "SELECT DISTINCT empleado_id FROM novedades WHERE fecha=? AND tipo='NF'",
                (fecha_str,)
            ).fetchall()
        }

        # Cargar tipo de empleado para los que tienen planificación
        eids_plan = [p["empleado_id"] for p in planes]
        if eids_plan:
            pe_t = ",".join("?" * len(eids_plan))
            emp_tipos: dict[int, str] = {
                r["id"]: r["tipo"] for r in conn.execute(
                    f"SELECT id, tipo FROM empleados WHERE id IN ({pe_t})", eids_plan
                ).fetchall()
            }
        else:
            emp_tipos = {}
        jerarquico_set = {eid for eid, t in emp_tipos.items() if t == "jerarquico"}
        acceso_set     = {eid for eid, t in emp_tipos.items() if t == "acceso"}

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

            if eid in acceso_set:
                continue  # solo acceso: ignorar completamente

            if eid in corregidos:
                resumen["saltados"] += 1
                continue

            b1_entrada_id = b1_salida_id = b2_entrada_id = b2_salida_id = None
            b1_mt_val = b2_mt_val = 0
            horario_id_result = p.get("horario_id")

            if p["es_franco"]:
                # Excluir fichajes madrugada que pertenecen al turno anterior (cruza_medianoche)
                excluir_antes: datetime | None = None
                plan_ant = conn.execute(
                    "SELECT horario_id FROM planificacion WHERE empleado_id=? AND fecha=? AND es_franco=0",
                    (eid, str(fecha - timedelta(days=1)))
                ).fetchone()
                if plan_ant and plan_ant["horario_id"]:
                    bloques_noche = conn.execute(
                        "SELECT hora_salida FROM horarios_bloques "
                        "WHERE horario_id=? AND cruza_medianoche=1 ORDER BY bloque DESC LIMIT 1",
                        (plan_ant["horario_id"],)
                    ).fetchone()
                    if bloques_noche:
                        excluir_antes = _dt(fecha - timedelta(days=1), bloques_noche["hora_salida"], dia_sig=True) + timedelta(minutes=30)
                fichajes_franco = [
                    f for f in fich_map.get(eid, [])
                    if f["timestamp"][:10] == fecha_str
                    and (excluir_antes is None or datetime.fromisoformat(f["timestamp"]) > excluir_antes)
                ]
                if fichajes_franco:
                    hid_ft = p.get("horario_id")  # NULL hasta que el encargado lo asigne
                    if hid_ft and hid_ft not in bloques_map:
                        bloques_map[hid_ft] = [dict(b) for b in conn.execute(
                            "SELECT * FROM horarios_bloques WHERE horario_id=? ORDER BY bloque",
                            (hid_ft,)
                        ).fetchall()]
                    bloques_ft = bloques_map.get(hid_ft, []) if hid_ft else []

                    if bloques_ft:
                        # Horario asignado: evaluación completa con tardanza
                        horario_id_result = hid_ft
                        fichajes_franco.sort(key=lambda f: f["timestamp"])
                        slots = _clasificar_fichajes(fichajes_franco, bloques_ft, fecha)
                        b1_entrada_id = (slots.get("b1_entrada") or {}).get("id")
                        b1_salida_id  = (slots.get("b1_salida")  or {}).get("id")
                        b1r = _evaluar_bloque(bloques_ft[0], fecha, slots.get("b1_entrada"), slots.get("b1_salida"))
                        b2r = None
                        if len(bloques_ft) > 1:
                            b2_entrada_id = (slots.get("b2_entrada") or {}).get("id")
                            b2_salida_id  = (slots.get("b2_salida")  or {}).get("id")
                            b2r = _evaluar_bloque(bloques_ft[1], fecha, slots.get("b2_entrada"), slots.get("b2_salida"))
                        estado = _calcular_estado(b1r, b2r)
                    else:
                        # Pendiente: encargado aún no asignó horario, registrar tiempos sin tardanza
                        fichajes_franco.sort(key=lambda f: f["timestamp"])
                        tiene_salida = len(fichajes_franco) >= 2
                        b1r = {
                            "entrada":           fichajes_franco[0]["timestamp"],
                            "salida":            fichajes_franco[-1]["timestamp"] if tiene_salida else None,
                            "minutos_tarde":     None,
                            "salida_anticipada": False,
                            "ausente":           False,
                            "sin_salida":        not tiene_salida,
                        }
                        b2r = None
                        estado = "ft"
                else:
                    estado, b1r, b2r = "franco", {}, {}
            elif not p["horario_id"]:
                estado, b1r, b2r = "sin_horario", {}, {}
            else:
                bloques = bloques_map.get(p["horario_id"], [])
                if not bloques:
                    estado, b1r, b2r = "sin_horario", {}, {}
                else:
                    slots = _clasificar_fichajes(fich_map[eid], bloques, fecha)
                    b1_entrada_id = (slots.get("b1_entrada") or {}).get("id")
                    b1_salida_id  = (slots.get("b1_salida")  or {}).get("id")
                    b2_entrada_id = (slots.get("b2_entrada") or {}).get("id")
                    b2_salida_id  = (slots.get("b2_salida")  or {}).get("id")
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

                    b1_aplica = bool(bloques[0].get("aplica", 1))
                    b2_aplica = len(bloques) > 1 and bool(bloques[1].get("aplica", 1))
                    b1_mt_val = not b1_aplica
                    b2_mt_val = len(bloques) > 1 and not b2_aplica

                    if b1_mt_val and b2r is not None:
                        # b1 no aplica: evaluar solo b2 como si fuera bloque único
                        estado = _calcular_estado(b2r, None)
                    elif b2_mt_val and b2r is not None:
                        # b2 no aplica: evaluar solo b1 como bloque único
                        estado = _calcular_estado(b1r, None)
                    else:
                        estado = _calcular_estado(b1r, b2r)

                    if estado == "ausente":
                        # Para "duda": solo revisar bloques que aplican
                        bloques_aplica = [b for b in bloques if b.get("aplica", 1)]
                        exit_slots = [
                            _dt(fecha, b["hora_salida"], dia_sig=bool(b["cruza_medianoche"]))
                            for b in bloques_aplica
                        ]
                        duda = exit_slots and any(
                            min(
                                abs((datetime.fromisoformat(f["timestamp"]) - slot).total_seconds()) / 60
                                for slot in exit_slots
                            ) <= MAX_DIST_MIN
                            for f in fich_map.get(eid, [])
                            if f["timestamp"][:10] == fecha_sig
                        )
                        if duda:
                            estado = "duda"
                    elif estado != "duda":
                        # Solo salida sin entrada en algún bloque que aplica → presencia no confirmada
                        solo_salida = (b1r.get("salida") and not b1r.get("entrada") and b1_aplica) or \
                                      (b2r and b2r.get("salida") and not b2r.get("entrada") and b2_aplica)
                        if solo_salida:
                            estado = "duda"

            # Jerárquico sin fichaje: marcar como presente según horario planificado
            if estado == "ausente" and eid in jerarquico_set:
                bloques = bloques_map.get(p.get("horario_id"), [])
                if bloques:
                    b1 = bloques[0]
                    cruza1 = bool(b1["cruza_medianoche"])
                    b1r = {
                        "entrada":           f"{fecha_str} {b1['hora_entrada']}:00",
                        "salida":            f"{str(fecha + timedelta(days=1)) if cruza1 else fecha_str} {b1['hora_salida']}:00",
                        "minutos_tarde":     None,
                        "salida_anticipada": False,
                        "ausente":           False,
                        "sin_salida":        False,
                    }
                    b2r = None
                    if len(bloques) > 1:
                        b2 = bloques[1]
                        cruza2 = bool(b2["cruza_medianoche"])
                        b2r = {
                            "entrada":           f"{fecha_str} {b2['hora_entrada']}:00",
                            "salida":            f"{str(fecha + timedelta(days=1)) if cruza2 else fecha_str} {b2['hora_salida']}:00",
                            "minutos_tarde":     None,
                            "salida_anticipada": False,
                            "ausente":           False,
                            "sin_salida":        False,
                        }
                    estado = "ok"

            # Si el empleado tiene novedad NF y resultó ausente, usar horario planificado
            if estado == "ausente" and eid in nf_set:
                bloques = bloques_map.get(p.get("horario_id"), [])
                if bloques:
                    b1 = bloques[0]
                    cruza1 = bool(b1["cruza_medianoche"])
                    b1r = {
                        "entrada":          f"{fecha_str} {b1['hora_entrada']}:00",
                        "salida":           f"{str(fecha + timedelta(days=1)) if cruza1 else fecha_str} {b1['hora_salida']}:00",
                        "minutos_tarde":    None,
                        "salida_anticipada": False,
                        "ausente":          False,
                        "sin_salida":       False,
                    }
                    b2r = None
                    if len(bloques) > 1:
                        b2 = bloques[1]
                        cruza2 = bool(b2["cruza_medianoche"])
                        b2r = {
                            "entrada":          f"{fecha_str} {b2['hora_entrada']}:00",
                            "salida":           f"{str(fecha + timedelta(days=1)) if cruza2 else fecha_str} {b2['hora_salida']}:00",
                            "minutos_tarde":    None,
                            "salida_anticipada": False,
                            "ausente":          False,
                            "sin_salida":       False,
                        }
                    estado = "nf"

            conn.execute(
                """INSERT INTO resultados_dia
                   (empleado_id, fecha, horario_id, es_franco, estado,
                    b1_entrada, b1_salida, b1_minutos_tarde, b1_salida_anticipada, b1_ausente, b1_sin_salida,
                    b2_entrada, b2_salida, b2_minutos_tarde, b2_salida_anticipada, b2_ausente, b2_sin_salida,
                    b1_entrada_id, b1_salida_id, b2_entrada_id, b2_salida_id,
                    b1_mt, b2_mt,
                    corregido_manualmente, corregido_por, corregido_en,
                    procesado_en)
                   VALUES (?,?,?,?,?, ?,?,?,?,?,?, ?,?,?,?,?,?, ?,?,?,?, ?,?, 0,NULL,NULL, datetime('now','localtime'))
                   ON CONFLICT(empleado_id, fecha) DO UPDATE SET
                   horario_id=excluded.horario_id, es_franco=excluded.es_franco, estado=excluded.estado,
                   b1_entrada=excluded.b1_entrada, b1_salida=excluded.b1_salida,
                   b1_minutos_tarde=excluded.b1_minutos_tarde,
                   b1_salida_anticipada=excluded.b1_salida_anticipada, b1_ausente=excluded.b1_ausente,
                   b1_sin_salida=excluded.b1_sin_salida,
                   b2_entrada=excluded.b2_entrada, b2_salida=excluded.b2_salida,
                   b2_minutos_tarde=excluded.b2_minutos_tarde,
                   b2_salida_anticipada=excluded.b2_salida_anticipada, b2_ausente=excluded.b2_ausente,
                   b2_sin_salida=excluded.b2_sin_salida,
                   b1_entrada_id=excluded.b1_entrada_id, b1_salida_id=excluded.b1_salida_id,
                   b2_entrada_id=excluded.b2_entrada_id, b2_salida_id=excluded.b2_salida_id,
                   b1_mt=excluded.b1_mt, b2_mt=excluded.b2_mt,
                   corregido_manualmente=0, corregido_por=NULL, corregido_en=NULL,
                   procesado_en=datetime('now','localtime')""",
                (eid, fecha_str, horario_id_result, p["es_franco"], estado,
                 b1r.get("entrada"), b1r.get("salida"), b1r.get("minutos_tarde"),
                 int(b1r.get("salida_anticipada", False)), int(b1r.get("ausente", False)),
                 int(b1r.get("sin_salida", False)),
                 (b2r or {}).get("entrada"), (b2r or {}).get("salida"), (b2r or {}).get("minutos_tarde"),
                 int((b2r or {}).get("salida_anticipada", False)), int((b2r or {}).get("ausente", False)),
                 int((b2r or {}).get("sin_salida", False)),
                 b1_entrada_id, b1_salida_id, b2_entrada_id, b2_salida_id,
                 b1_mt_val, b2_mt_val)
            )

            resumen["evaluados"] += 1
            if estado == "ok":        resumen["ok"] += 1
            elif estado == "franco":  resumen["franco"] += 1
            elif estado == "ausente": resumen["ausente"] += 1
            elif "tarde" in estado:   resumen["tarde"] += 1
            else:                     resumen["otros"] += 1

    logger.info("Evaluados %s: %s", fecha_str, resumen)
    return resumen


def evaluar_rango(fecha_desde: str, fecha_hasta: str, respetar_correcciones: bool = True) -> dict:
    """Evalúa todos los días entre dos fechas."""
    d = date.fromisoformat(fecha_desde)
    h = date.fromisoformat(fecha_hasta)
    total = {"evaluados": 0, "ok": 0, "tarde": 0, "ausente": 0, "franco": 0, "otros": 0, "saltados": 0}
    while d <= h:
        r = evaluar_fecha(str(d), respetar_correcciones=respetar_correcciones)
        for k in total:
            total[k] += r.get(k, 0)
        d += timedelta(days=1)
    return total
