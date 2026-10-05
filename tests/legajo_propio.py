"""Propio legajo: planificacion:propia y asistencia:fichaje_propio.

Lo que se fija acá:
- /api/auth/me dice cuál es el legajo del usuario (la pantalla avisa con eso).
- Sin los permisos, el servidor rechaza con 403 cambiar la propia planificación
  (Planificación, planilla, quitar franco, calendario) y cargar o borrar
  fichadas propias, con un texto que manda a pedírselo a otro encargado.
- Sobre otro empleado, todo sigue andando.
- Con los permisos, sobre uno mismo también.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _base import preparar

DB = preparar()

import sqlite3
from fastapi.testclient import TestClient
from auth.core import create_token, invalidar_cache
import main

ok = fallos = 0


def chequear(desc, cond, extra=""):
    global ok, fallos
    if cond:
        ok += 1
        print(f"  OK   {desc}")
    else:
        fallos += 1
        print(f"  FALLA {desc}  {extra}")


con = sqlite3.connect(DB)
yo, otro = [r[0] for r in con.execute(
    "SELECT id FROM empleados WHERE activo=1 AND tipo = 'normal' ORDER BY id LIMIT 2")]
hor = con.execute("SELECT id FROM horarios WHERE activo=1 ORDER BY id LIMIT 1").fetchone()[0]
cal = con.execute("SELECT id FROM calendarios ORDER BY id LIMIT 1").fetchone()[0]
rid = con.execute("INSERT INTO roles (nombre, descripcion, nivel) VALUES ('PruebaEncargado','',1)").lastrowid
for m, a in [("planificacion", "ver"), ("planificacion", "editar"), ("calendarios", "editar"),
             ("asistencia", "ver"), ("asistencia", "fichaje_manual"), ("asistencia", "corregir"),
             ("asistencia", "carga_inicial"), ("vacaciones", "ver"), ("vacaciones", "editar"),
             ("vacaciones", "carga_inicial"), ("empleados", "ver"), ("empleados", "editar"),
             ("empleados", "jubilacion")]:
    con.execute("INSERT INTO permisos (rol_id, modulo, accion) VALUES (?,?,?)", (rid, m, a))
uid = con.execute(
    "INSERT INTO usuarios (nombre, email, password_hash, rol_id, activo, empleado_id) "
    "VALUES ('Prueba Encargado','prueba.encargado@prueba','x',?,1,?)", (rid, yo)).lastrowid
con.commit()
invalidar_cache()

cli = TestClient(main.app)
cli.cookies.set("session", create_token(uid, "x", rid, "PruebaEncargado"))
F = "2026-10-21"

me = cli.get("/api/auth/me").json()
chequear("/api/auth/me trae el legajo propio", me.get("empleado_id") == yo, me.get("empleado_id"))


def plan(eid):
    return cli.post("/api/planificacion", json={"empleado_id": eid, "fecha": F, "horario_id": hor})


def fichada(eid, hora):
    return cli.post("/api/fichajes", json={"empleado_id": eid, "timestamp": f"{F} {hora}"})


print("Sin los permisos, sobre uno mismo")
r = plan(yo)
chequear("no cambia su horario (403)", r.status_code == 403, r.status_code)
chequear("el texto manda a pedírselo a otro encargado", "otro encargado" in r.json().get("detail", ""), r.text)
chequear("no se quita un franco",
         cli.post("/api/planificacion/quitar-franco", json={"empleado_id": yo, "fecha": F}).status_code == 403)
chequear("no se asigna un calendario",
         cli.post("/api/calendarios/asignar", json={"empleado_ids": [yo], "calendario_id": cal,
                                                    "fecha_desde": F}).status_code == 403)
r = fichada(yo, "08:55")
chequear("no se carga una fichada (403)", r.status_code == 403, r.status_code)
chequear("el texto de fichadas manda a otro encargado", "otro encargado" in r.json().get("detail", ""), r.text)



def novedad(eid, tipo, fecha=F, desc=None):
    return cli.post("/api/asistencia/novedades",
                    json={"empleado_id": eid, "fecha": fecha, "bloque": 0, "tipo": tipo, "descripcion": desc})


def saldo(eid):
    return cli.put(f"/api/asistencia/saldo_francos/{eid}/2026-10", json={"saldo": 3})


r = novedad(yo, "NF")
chequear("no se carga una novedad (403)", r.status_code == 403, r.status_code)
chequear("el texto de novedades manda a otro encargado", "otro encargado" in r.json().get("detail", ""), r.text)
chequear("no se pone un CP", novedad(yo, "CP", desc="x").status_code == 403)
chequear("sí deja una observación", novedad(yo, "CO", "2026-10-22", "nota").status_code == 201)
chequear("no edita su saldo de francos", saldo(yo).status_code == 403)
# Una novedad que le cargó otro: no la puede borrar.
con.execute("INSERT INTO novedades (empleado_id, fecha, bloque, tipo, creado_por) VALUES (?, '2026-10-23', 0, 'E', 'otro')", (yo,))
con.commit()
nid = con.execute("SELECT id FROM novedades WHERE empleado_id=? AND fecha='2026-10-23'", (yo,)).fetchone()[0]
chequear("no borra una novedad suya", cli.delete(f"/api/asistencia/novedades/{nid}").status_code == 403)
chequear("no la borra con un rango", cli.post("/api/asistencia/novedades/borrar-rango", json={
    "empleado_id": yo, "fecha_desde": "2026-10-23", "fecha_hasta": "2026-10-23"}).status_code == 403)
chequear("la novedad sigue ahí",
         con.execute("SELECT COUNT(*) FROM novedades WHERE id=?", (nid,)).fetchone()[0] == 1)


def vp(eid):
    return cli.put(f"/api/vacaciones/vacaciones-pagadas/{eid}/2026-10", json={"dias": 2})


def saldo_vac(eid):
    return cli.post("/api/vacaciones/saldo-inicial", json={
        "empleado_id": eid, "anio": 2025, "dias_correspondian": 30, "dias_tomados": 0})


def saldo_francos_inicial(eid):
    return cli.post("/api/francos/saldo-inicial", json={"empleado_id": eid, "mes": "2026-10", "saldo": 5})


def legajo(eid, **cambios):
    actual = cli.get(f"/api/empleados/{eid}").json()
    return cli.put(f"/api/empleados/{eid}", json={**actual, **cambios})


r = vp(yo)
chequear("no se pone vacaciones pagadas (403)", r.status_code == 403, r.status_code)
chequear("el texto de vacaciones manda a otro encargado", "otro encargado" in r.json().get("detail", ""), r.text)
chequear("no se carga su saldo inicial de vacaciones", saldo_vac(yo).status_code == 403)
chequear("no se carga su saldo inicial de francos", saldo_francos_inicial(yo).status_code == 403)
r = legajo(yo, tipo="jerarquico")
chequear("no se pasa a jerárquico", r.status_code == 403, r.status_code)
chequear("no se cambia la fecha de ingreso", legajo(yo, fecha_ingreso="2000-01-01").status_code == 403)
chequear("sí edita su teléfono", legajo(yo, telefono="1144445555").status_code == 200)
chequear("el tipo quedó como estaba",
         con.execute("SELECT tipo FROM empleados WHERE id=?", (yo,)).fetchone()[0] != "jerarquico")
chequear("no se carga una jubilación", cli.put(f"/api/empleados/{yo}/jubilacion", json={
    "fecha_recontratacion": "2026-01-01", "vac_dias_jubilacion": 35}).status_code == 403)

print("Sobre otro empleado, todo sigue")
chequear("cambia el horario de otro", plan(otro).status_code == 200)
chequear("le carga una fichada a otro", fichada(otro, "08:55").status_code == 201)
chequear("le carga una novedad a otro", novedad(otro, "NF").status_code == 201)
chequear("le edita el saldo a otro", saldo(otro).status_code == 200)
chequear("le pone vacaciones pagadas a otro", vp(otro).status_code == 200)
chequear("le carga el saldo inicial de vacaciones a otro", saldo_vac(otro).status_code == 200)
r = legajo(otro, tipo="jerarquico")
chequear("le cambia el tipo a otro", r.status_code == 200, r.text)

for modulo, accion in (("planificacion", "propia"), ("asistencia", "fichaje_propio"),
                       ("asistencia", "correccion_propia"), ("vacaciones", "propia"),
                       ("empleados", "propia")):
    con.execute("INSERT INTO permisos (rol_id, modulo, accion) VALUES (?,?,?)", (rid, modulo, accion))
con.commit()
invalidar_cache()
print("Con los permisos, sobre uno mismo")
chequear("cambia su horario", plan(yo).status_code == 200)
chequear("se carga una fichada", fichada(yo, "08:56").status_code == 201)
chequear("se carga una novedad", novedad(yo, "NF").status_code == 201)
chequear("borra una novedad suya", cli.delete(f"/api/asistencia/novedades/{nid}").status_code == 200)
chequear("se pone vacaciones pagadas", vp(yo).status_code == 200)
chequear("se cambia el tipo", legajo(yo, tipo="jerarquico").status_code == 200)

con.close()
print(f"\n{'=' * 52}\n  {ok} pasaron, {fallos} fallaron\n{'=' * 52}")
raise SystemExit(1 if fallos else 0)
