"""Planilla mensual: aviso de días que no se evalúan por falta de calendario.

Lo que se fija acá:
- Quitar un calendario ya no borra la asignación: la cierra y guarda quién.
- Un día sin planificación en el que el empleado fichó se marca
  (tipo sin_calendario, con "?") en vez de quedar en blanco.
- Una fichada de madrugada que es la salida del turno noche anterior no marca.
- Un día planificado sin horario ya no se muestra "I": se marca, haya fichado o no.
- Esos días cuentan como faltantes en el control del mes.
- El empleado lleva la info del distintivo SIN CAL.
- Al volver a asignar el calendario la marca desaparece y el registro de quién
  lo quitó se conserva.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _base import preparar, token_sistema

DB = preparar()   # una COPIA de la base: la real nunca se toca

import sqlite3
from datetime import date, timedelta
from fastapi.testclient import TestClient
import main
from sync.evaluador import evaluar_fecha

ok = fallos = 0
HOY = date.today()


def chequear(desc, cond, extra=""):
    global ok, fallos
    if cond:
        ok += 1
        print(f"  OK   {desc}")
    else:
        fallos += 1
        print(f"  FALLA {desc}  {extra}")


def d(n):
    return (HOY - timedelta(days=n)).isoformat()


con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
cli = TestClient(main.app)
cli.cookies.set("session", token_sistema()[0])

# A: empleado activo con calendario vigente cuyo calendario tiene algún horario
A_row = con.execute("""
    SELECT a.id AS asig_id, a.empleado_id, a.calendario_id, e.user_id,
           (SELECT horario_id FROM calendarios_dias cd
             WHERE cd.calendario_id=a.calendario_id AND cd.horario_id IS NOT NULL LIMIT 1) AS hid
    FROM asignaciones a JOIN empleados e ON e.id=a.empleado_id
    WHERE e.activo=1 AND e.tipo NOT IN ('acceso','parking')
      AND (e.fecha_ingreso IS NULL OR e.fecha_ingreso <= ?)
      AND a.fecha_desde <= ? AND (a.fecha_hasta IS NULL OR a.fecha_hasta > ?)
      AND EXISTS (SELECT 1 FROM calendarios_dias cd
                  WHERE cd.calendario_id=a.calendario_id AND cd.horario_id IS NOT NULL)
    ORDER BY a.empleado_id LIMIT 1""", (d(30), d(0), d(0))).fetchone()
A, A_UID, CAL, HID = A_row["empleado_id"], A_row["user_id"], A_row["calendario_id"], A_row["hid"]
B_row = con.execute("""
    SELECT id, user_id FROM empleados
    WHERE activo=1 AND tipo NOT IN ('acceso','parking') AND id != ?
      AND (fecha_ingreso IS NULL OR fecha_ingreso <= ?)
    ORDER BY id LIMIT 1""", (A, d(30))).fetchone()
B, B_UID = B_row["id"], B_row["user_id"]


def fichar(eid, uid, fecha, hora):
    con.execute("INSERT INTO fichajes (empleado_id, user_id, timestamp) VALUES (?,?,?)",
                (eid, uid, f"{fecha} {hora}:00"))


def limpiar_dia(eid, fecha, plan=True):
    con.execute("DELETE FROM fichajes WHERE empleado_id=? AND date(timestamp)=?", (eid, fecha))
    con.execute("DELETE FROM resultados_dia WHERE empleado_id=? AND fecha=?", (eid, fecha))
    con.execute("DELETE FROM novedades WHERE empleado_id=? AND fecha=?", (eid, fecha))
    con.execute("DELETE FROM aliviadas WHERE empleado_id=? AND fecha=?", (eid, fecha))
    if plan:
        con.execute("DELETE FROM planificacion WHERE empleado_id=? AND fecha=?", (eid, fecha))


def mensual(fecha):
    r = cli.get(f"/api/asistencia/mensual?mes={fecha[:7]}")
    assert r.status_code == 200, r.text[:300]
    return r.json()


def emp_de(datos, eid):
    for g in ("TM", "TN", "CO", "ST"):
        for e in datos[g]:
            if e["id"] == eid:
                return e
    return None


def tipos(emp, fecha):
    c = emp["celdas"][fecha]
    bloques = [c["b1"], c["b2"]] if "b1" in c else [c]
    return [(b["tipo"], b.get("letra"), b.get("fichado")) for b in bloques]


# ── 1. Quitar el calendario cierra la asignación y guarda quién ─────────────
r = cli.delete(f"/api/calendarios/asignaciones/{A_row['asig_id']}")
chequear("quitar calendario responde 200", r.status_code == 200, r.text[:200])
asig = con.execute("SELECT * FROM asignaciones WHERE id=?", (A_row["asig_id"],)).fetchone()
chequear("la asignación sigue en la base", asig is not None)
chequear("cerrada hoy", asig and asig["fecha_hasta"] == d(0), asig and asig["fecha_hasta"])
chequear("con quién la quitó y cuándo", asig and asig["quitado_por"] and asig["quitado_en"])
lista = cli.get(f"/api/calendarios/asignaciones/empleado/{A}").json()
chequear("el historial del empleado trae el nombre de quien la quitó",
         any(a.get("quitado_por_nombre") for a in lista))

# ── 2. Armar los días de prueba (sobre la copia) ────────────────────────────
D_FICHO, D_VACIO, D_NOCHE, D_ANT_NOCHE = d(5), d(2), d(3), d(4)
for f in (D_FICHO, D_VACIO, D_NOCHE):
    limpiar_dia(A, f)
fichar(A, A_UID, D_FICHO, "10:00")
fichar(A, A_UID, D_FICHO, "18:00")
# Salida de madrugada del turno del día anterior, que sí tiene horario
limpiar_dia(A, D_ANT_NOCHE, plan=False)
con.execute("INSERT OR REPLACE INTO planificacion (empleado_id, fecha, horario_id, es_franco, auto_generado) "
            "VALUES (?,?,?,0,0)", (A, D_ANT_NOCHE, HID))
fichar(A, A_UID, D_NOCHE, "03:00")

# B: planificado sin horario (como un mozo confirmado sin calendario)
D_SH, D_SH_FICHO = d(6), d(7)
for f in (D_SH, D_SH_FICHO):
    limpiar_dia(B, f)
    con.execute("INSERT INTO planificacion (empleado_id, fecha, horario_id, es_franco, auto_generado) "
                "VALUES (?,?,NULL,0,0)", (B, f))
fichar(B, B_UID, D_SH_FICHO, "09:00")
con.commit()
for f in (D_SH, D_SH_FICHO):
    evaluar_fecha(f, respetar_correcciones=False, solo_empleado_id=B)
est = {r["fecha"]: r["estado"] for r in con.execute(
    "SELECT fecha, estado FROM resultados_dia WHERE empleado_id=? AND fecha IN (?,?)", (B, D_SH, D_SH_FICHO))}
chequear("el evaluador deja esos días de B como sin_horario",
         est.get(D_SH) == "sin_horario" and est.get(D_SH_FICHO) == "sin_horario", est)

# ── 3. La planilla ──────────────────────────────────────────────────────────
ea = emp_de(mensual(D_FICHO), A)
chequear("A aparece en la planilla", ea is not None)
t = tipos(ea, D_FICHO)
chequear("día sin planificación en el que fichó: marcado con fichado",
         all(x == ("sin_calendario", None, True) for x in t), t)
t = tipos(emp_de(mensual(D_VACIO), A), D_VACIO)
chequear("día sin planificación ni fichadas: en blanco como antes",
         all(x[0] == "sin_plan" for x in t), t)
t = tipos(emp_de(mensual(D_NOCHE), A), D_NOCHE)
chequear("fichada de madrugada del turno noche anterior: no marca",
         all(x[0] == "sin_plan" for x in t), t)

eb = emp_de(mensual(D_SH), B)
t = tipos(eb, D_SH)
chequear("planificado sin horario y sin fichar: marcado, ya no 'I'",
         all(x == ("sin_calendario", None, False) for x in t), t)
t = tipos(emp_de(mensual(D_SH_FICHO), B), D_SH_FICHO)
chequear("planificado sin horario y fichó: marcado con fichado",
         all(x == ("sin_calendario", None, True) for x in t), t)

sc = ea["sin_cal"] or {}
chequear("A lleva el distintivo, sin calendario vigente", sc and sc["vigente"] is False, sc)
chequear("con el día marcado", int(D_FICHO[8:]) in sc.get("dias", []), sc)
# Días marcados anteriores a la baja (armados a mano acá): la baja no los explica
chequear("la baja de hoy no se atribuye a días anteriores", sc.get("quitado") is None, sc)
# Realista: hoy, ya sin calendario, ficha
limpiar_dia(A, d(0))
fichar(A, A_UID, d(0), "08:00")
con.commit()
sc_hoy = emp_de(mensual(d(0)), A)["sin_cal"] or {}
chequear("fichó hoy sin calendario: el distintivo dice quién lo quitó",
         (sc_hoy.get("quitado") or {}).get("quien") is not None
         and sc_hoy["quitado"]["fecha"] == d(0), sc_hoy)
chequear("B lleva el distintivo con sus días", eb["sin_cal"] and int(D_SH[8:]) in eb["sin_cal"]["dias"],
         eb["sin_cal"])
chequear("el control de B no queda completado",
         eb["control"]["estado"] not in ("completado", "liquidacion"), eb["control"])

from api.asistencia_mensual import get_control_estados_batch
f0 = D_SH[:8] + "01"
chequear("premios usa el mismo control sin romperse",
         get_control_estados_batch(con, [A, B], f0, D_SH).get(B) not in (None, "completado"))

# ── 4. Volver a asignar el calendario ───────────────────────────────────────
r = cli.post("/api/calendarios/asignar", json={
    "empleado_ids": [A], "calendario_id": CAL, "fecha_desde": D_FICHO})
chequear("reasignar responde 200", r.status_code == 200, r.text[:200])
t = tipos(emp_de(mensual(D_FICHO), A), D_FICHO)
chequear("el día ya no está marcado", all(x[0] != "sin_calendario" for x in t), t)
chequear("el registro de quién lo quitó se conserva",
         con.execute("SELECT COUNT(*) FROM asignaciones WHERE empleado_id=? AND quitado_en IS NOT NULL",
                     (A,)).fetchone()[0] == 1)
chequear("y quedó una sola asignación vigente",
         con.execute("SELECT COUNT(*) FROM asignaciones WHERE empleado_id=? AND fecha_desde<=? "
                     "AND (fecha_hasta IS NULL OR fecha_hasta>?)", (A, d(0), d(0))).fetchone()[0] == 1)

con.close()
print(f"\n{'=' * 52}\n  {ok} pasaron, {fallos} fallaron\n{'=' * 52}")
raise SystemExit(1 if fallos else 0)
