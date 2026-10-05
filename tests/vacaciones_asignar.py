"""Permiso vacaciones:asignar: poner o sacar una V exige ese permiso.

Lo que se fija acá:
- La migración se lo da a todo rol que ya podía cargar vacaciones (corregir la
  planilla o editar alguna grilla), así nadie pierde nada al actualizar.
- Sin el permiso, en la planilla: no se pone una V, no se pisa una V con otra
  novedad, no se borra una V (sola ni en un rango). Las demás novedades, igual.
- Sin el permiso, en las grillas: ni la celda V de Mozos/Barmans (ni cambiar una
  V por otra cosa), ni agregar/quitar vacaciones en Cocina y Peones.
- Con el permiso, todo eso vuelve a andar.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _base import preparar, token_sistema

DB = preparar(migrar=False)

import sqlite3

c = sqlite3.connect(DB)
podian = {r[0] for r in c.execute(
    """SELECT DISTINCT rol_id FROM permisos
       WHERE (modulo='asistencia' AND accion='corregir')
          OR (modulo IN ('distribucion','mozos','barmans','peones') AND accion='editar')""")}
# Si la base ya pasó por la migración, lo que falte se lo sacaron a propósito
# desde Roles: ahí no hay nada que verificar.
ya_migrada = c.execute(
    "SELECT COUNT(*) FROM permisos WHERE modulo='vacaciones' AND accion='asignar'").fetchone()[0] > 0
c.close()

from db.database import init_db
from auth.core import ensure_admin, create_token, invalidar_cache
init_db()
ensure_admin()

from fastapi.testclient import TestClient
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
con.row_factory = sqlite3.Row

con_permiso = {r[0] for r in con.execute(
    "SELECT rol_id FROM permisos WHERE modulo='vacaciones' AND accion='asignar'")}
if ya_migrada:
    print("  --   la base ya estaba migrada: no se verifica la migración")
else:
    chequear("la migración conserva a quien ya cargaba vacaciones", podian <= con_permiso,
             podian - con_permiso)

# Un rol que corrige la planilla y edita las grillas, pero sin vacaciones:asignar.
rid = con.execute("INSERT INTO roles (nombre, descripcion, nivel) VALUES ('PruebaSinVac','',1)").lastrowid
for m, a in [("asistencia", "ver"), ("asistencia", "corregir"), ("mozos", "editar"),
             ("barmans", "editar"), ("distribucion", "editar"), ("peones", "editar")]:
    con.execute("INSERT INTO permisos (rol_id, modulo, accion) VALUES (?,?,?)", (rid, m, a))
emp = con.execute("SELECT id FROM empleados WHERE activo=1 AND tipo != 'acceso' ORDER BY id LIMIT 1").fetchone()[0]
dep = con.execute("SELECT id FROM departamentos ORDER BY id LIMIT 1").fetchone()[0]
# Saldo de sobra, para que el único motivo de rechazo sea el permiso.
con.execute("INSERT OR REPLACE INTO vacaciones_saldo_inicial (empleado_id, anio, dias_correspondian, dias_tomados) VALUES (?,2025,30,0)", (emp,))
con.commit()
invalidar_cache()

LUNES, F1, F2 = "2026-10-19", "2026-10-20", "2026-10-21"
cli = TestClient(main.app)


def como(token):
    # El servidor renueva la cookie en cada respuesta: sin limpiar, quedaría la anterior.
    cli.cookies.clear()
    cli.cookies.set("session", token)


tok_sis = token_sistema()[0]
tok_sin = create_token(1, "prueba", rid, "PruebaSinVac")


def nov(tipo, fecha):
    return cli.post("/api/asistencia/novedades",
                    json={"empleado_id": emp, "fecha": fecha, "bloque": 0, "tipo": tipo})


def celda(modulo, turno, fecha, valor):
    campo = "estado" if modulo == "mozos" else "valor"
    return cli.post(f"/api/{modulo}/celda", json={
        "departamento_id": dep, "semana_inicio": LUNES, "empleado_id": emp,
        "fecha": fecha, "turno": turno, campo: valor})


como(tok_sis)
r = nov("V", F1)
chequear("Sistema pone una V", r.status_code == 201, r.text)
r = celda("mozos", "TN", F1, "V")
chequear("Sistema pone una V en Mozos", r.status_code == 200, r.text)
id_v = con.execute("SELECT id FROM novedades WHERE empleado_id=? AND fecha=? AND tipo='V'", (emp, F1)).fetchone()[0]

como(tok_sin)
print("Sin vacaciones:asignar")
chequear("no pone una V", nov("V", F2).status_code == 403)
chequear("sí pone otra novedad", nov("E", F2).status_code == 201)
chequear("no pisa una V con otra novedad", nov("E", F1).status_code == 403)
chequear("no borra una V", cli.delete(f"/api/asistencia/novedades/{id_v}").status_code == 403)
chequear("no borra un rango que tiene una V", cli.post(
    "/api/asistencia/novedades/borrar-rango",
    json={"empleado_id": emp, "fecha_desde": F1, "fecha_hasta": F2}).status_code == 403)
chequear("sí borra un rango sin V", cli.post(
    "/api/asistencia/novedades/borrar-rango",
    json={"empleado_id": emp, "fecha_desde": F2, "fecha_hasta": F2}).status_code == 200)
chequear("la V sigue en la planilla",
         con.execute("SELECT tipo FROM novedades WHERE id=?", (id_v,)).fetchone()[0] == "V")
chequear("no pone V en Mozos", celda("mozos", "TM", F2, "V").status_code == 403)
chequear("sí pone P en Mozos", celda("mozos", "TM", F2, "P").status_code == 200)
chequear("no cambia una V de Mozos por P", celda("mozos", "TN", F1, "P").status_code == 403)
chequear("no pone V en Barmans", celda("barmans", "TM", F2, "V").status_code == 403)
chequear("no agrega vacaciones en Cocina",
         cli.post("/api/distribucion/semana/999999/vacaciones", json={"empleado_id": emp, "fecha": F2}).status_code == 403)
chequear("no quita vacaciones en Cocina",
         cli.delete(f"/api/distribucion/semana/999999/vacaciones/{F2}/{emp}").status_code == 403)
chequear("no agrega vacaciones en Peones",
         cli.post("/api/peones/semana/999999/vacaciones", json={"empleado_id": emp, "fecha": F2}).status_code == 403)
chequear("no quita vacaciones en Peones",
         cli.delete(f"/api/peones/semana/999999/vacaciones/{F2}/{emp}").status_code == 403)

con.execute("INSERT INTO permisos (rol_id, modulo, accion) VALUES (?,'vacaciones','asignar')", (rid,))
con.commit()
invalidar_cache()
print("Con vacaciones:asignar")
chequear("cambia una V de Mozos por P", celda("mozos", "TN", F1, "P").status_code == 200)
chequear("borra una V", cli.delete(f"/api/asistencia/novedades/{id_v}").status_code == 200)
chequear("pone una V", nov("V", F1).status_code == 201)
chequear("en Cocina pasa el permiso (la semana no existe: 404)",
         cli.post("/api/distribucion/semana/999999/vacaciones", json={"empleado_id": emp, "fecha": F2}).status_code == 404)

con.close()
print(f"\n{'=' * 52}\n  {ok} pasaron, {fallos} fallaron\n{'=' * 52}")
raise SystemExit(1 if fallos else 0)
