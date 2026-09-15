"""Prueba de punta a punta de la tanda 1: catálogos de entregas.

Levanta la app en memoria con TestClient, entra como el usuario 1 (rol sistema)
y ejercita cada endpoint, incluidas las reglas que tienen que fallar.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _base import preparar, token_sistema
DB = preparar()   # una COPIA de la base: la real nunca se toca

import sqlite3
from fastapi.testclient import TestClient
import main
from auth.core import create_token, ensure_admin

# La app otorga los permisos default en el arranque; el TestClient no lo dispara.
ensure_admin()

ok = fallos = 0


def chequear(descripcion, condicion, extra=""):
    global ok, fallos
    if condicion:
        ok += 1
        print(f"  OK   {descripcion}")
    else:
        fallos += 1
        print(f"  FALLA {descripcion}  {extra}")


cli = TestClient(main.app)
cli.cookies.set("session", token_sistema()[0])

print("\n=== RUBROS (sembrados por la migracion) ===")
r = cli.get("/api/uniformes/rubros")
chequear("GET rubros responde 200", r.status_code == 200, r.text[:120])
rubros = r.json()
chequear("vienen los dos rubros iniciales", {"Ropa de trabajo", "EPP"} <= {r["nombre"] for r in rubros}, rubros)
ropa = next((x for x in rubros if x["nombre"] == "Ropa de trabajo"), None)
chequear("Ropa de trabajo avisa a los 6 meses", ropa and ropa["meses_alerta"] == 6)
epp = next((x for x in rubros if x["nombre"] == "EPP"), None)
chequear("EPP no avisa (null)", epp and epp["meses_alerta"] is None)

r = cli.post("/api/uniformes/rubros", json={"nombre": "Ropa de trabajo"})
chequear("rubro duplicado -> 409", r.status_code == 409, r.status_code)

r = cli.post("/api/uniformes/rubros", json={"nombre": "   "})
chequear("rubro sin nombre -> 400", r.status_code == 400, r.status_code)

print("\n=== TIPOS DE TALLE ===")
r = cli.post("/api/uniformes/tipos-talle", json={"nombre": "ZZ Pantalon (prueba)"})
chequear("crear tipo -> 201", r.status_code == 201, r.text[:120])
tid = r.json()["id"]

r = cli.post("/api/uniformes/tipos-talle", json={"nombre": "ZZ Pantalon (prueba)"})
chequear("tipo duplicado -> 409", r.status_code == 409)

print("\n=== VALORES (la escala) ===")
for v in ["42", "44", "46"]:
    cli.post(f"/api/uniformes/tipos-talle/{tid}/valores", json={"valor": v})
r = cli.post(f"/api/uniformes/tipos-talle/{tid}/valores", json={"valor": "42"})
chequear("valor repetido en el mismo tipo -> 409", r.status_code == 409)

valores = cli.get(f"/api/uniformes/tipos-talle/{tid}/valores").json()
chequear("quedaron 3 valores", len(valores) == 3, valores)
chequear("respetan el orden de carga", [v["valor"] for v in valores] == ["42", "44", "46"],
         [v["valor"] for v in valores])

r = cli.post("/api/uniformes/tipos-talle/99999/valores", json={"valor": "X"})
chequear("valor sobre tipo inexistente -> 404", r.status_code == 404)

print("\n=== ELEMENTOS ===")
r = cli.post("/api/uniformes/elementos", json={
    "nombre": "ZZ Pantalon cargo (prueba)", "tipo_modelo": "Grafa", "marca": "Ombu",
    "posee_certificado": False, "categoria_id": ropa["id"], "tipo_talle_id": tid})
chequear("crear elemento -> 201", r.status_code == 201, r.text[:160])
el = r.json()
eid = el["id"]
chequear("resuelve el nombre del rubro", el["categoria_nombre"] == "Ropa de trabajo", el)
chequear("resuelve el nombre del tipo de talle", el["tipo_talle_nombre"] == "ZZ Pantalon (prueba)")
chequear("arranca activo", el["activo"] == 1)
chequear("nunca se entrego", el["entregado_veces"] == 0)

r = cli.post("/api/uniformes/elementos", json={"nombre": "ZZ malo", "categoria_id": 99999})
chequear("rubro inexistente -> 400", r.status_code == 400, r.status_code)

r = cli.patch(f"/api/uniformes/elementos/{eid}", json={"marca": "Pampero"})
chequear("editar marca -> 200", r.status_code == 200 and r.json()["marca"] == "Pampero", r.text[:120])

r = cli.patch(f"/api/uniformes/elementos/{eid}", json={"tipo_talle_id": None})
chequear("poder dejarlo sin talle (null explicito)",
         r.status_code == 200 and r.json()["tipo_talle_id"] is None, r.text[:120])
cli.patch(f"/api/uniformes/elementos/{eid}", json={"tipo_talle_id": tid})

r = cli.patch(f"/api/uniformes/elementos/{eid}", json={"activo": False})
chequear("desactivar -> activo 0", r.status_code == 200 and r.json()["activo"] == 0)
cli.patch(f"/api/uniformes/elementos/{eid}", json={"activo": True})

solo_act = cli.get("/api/uniformes/elementos?solo_activos=true").json()
chequear("el filtro de activos devuelve el elemento", any(x["id"] == eid for x in solo_act))

print("\n=== REGLAS DE BORRADO ===")
r = cli.delete(f"/api/uniformes/rubros/{ropa['id']}")
chequear("no deja borrar un rubro con elementos -> 409", r.status_code == 409, r.status_code)

r = cli.delete(f"/api/uniformes/tipos-talle/{tid}")
chequear("no deja borrar un tipo de talle en uso -> 409", r.status_code == 409, r.status_code)

# Un elemento nunca entregado sí se puede borrar (corregir un alta equivocada).
con = sqlite3.connect(DB)
con.execute("INSERT INTO uniformes_movimientos (empleado_id, fecha, empleado_apellido_nombre) "
            "SELECT id, '2026-09-09', 'PRUEBA' FROM empleados LIMIT 1")
mid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
con.execute("INSERT INTO uniformes_items (movimiento_id, elemento_id, cantidad, elemento_nombre) "
            "VALUES (?,?,1,'copia congelada')", (mid, eid))
con.commit()

r = cli.delete(f"/api/uniformes/elementos/{eid}")
chequear("no deja borrar un elemento ya entregado -> 409", r.status_code == 409, r.status_code)

el = next(x for x in cli.get("/api/uniformes/elementos").json() if x["id"] == eid)
chequear("cuenta las veces entregado", el["entregado_veces"] == 1, el["entregado_veces"])

# La copia del renglon no cambia aunque se edite el catalogo.
cli.patch(f"/api/uniformes/elementos/{eid}", json={"nombre": "ZZ nombre cambiado"})
copia = con.execute("SELECT elemento_nombre FROM uniformes_items WHERE movimiento_id=?", (mid,)).fetchone()[0]
chequear("editar el catalogo NO altera la copia del renglon firmado",
         copia == "copia congelada", copia)

con.execute("DELETE FROM uniformes_items WHERE movimiento_id=?", (mid,))
con.execute("DELETE FROM uniformes_movimientos WHERE id=?", (mid,))
con.commit()

r = cli.delete(f"/api/uniformes/elementos/{eid}")
chequear("borrar un elemento nunca entregado -> 200", r.status_code == 200, r.status_code)

print("\n=== CASCADA Y LIMPIEZA ===")
r = cli.delete(f"/api/uniformes/tipos-talle/{tid}")
chequear("borrar el tipo ya libre -> 200", r.status_code == 200, r.status_code)
quedan = con.execute("SELECT COUNT(*) FROM uniformes_talle_valores WHERE tipo_talle_id=?", (tid,)).fetchone()[0]
chequear("los valores caen por cascada", quedan == 0, quedan)

print("\n=== LA BANDERA ===")
con.execute("UPDATE configuracion SET valor='0' WHERE clave='uniformes_activo'")
con.commit()
chequear("con la bandera en 0, la API responde 404",
         cli.get("/api/uniformes/rubros").status_code == 404)
chequear("con la bandera en 0, la pagina responde 404",
         cli.get("/uniformes").status_code == 404)
mods = cli.get("/api/roles/modulos").json()
chequear("con la bandera en 0, no figura en la matriz de roles",
         "uniformes" not in mods["modulos"], mods["modulos"][-3:])
me = cli.get("/api/auth/me").json()
chequear("con la bandera en 0, /me no devuelve permisos de entregas",
         not any(p["modulo"] == "uniformes" for p in me["permisos"]))

con.execute("UPDATE configuracion SET valor='1' WHERE clave='uniformes_activo'")
con.commit()
chequear("al prenderla, vuelve a responder", cli.get("/api/uniformes/rubros").status_code == 200)
mods = cli.get("/api/roles/modulos").json()
chequear("al prenderla, aparece en la matriz de roles", "uniformes" in mods["modulos"])
me = cli.get("/api/auth/me").json()
chequear("al prenderla, /me devuelve los 4 permisos",
         sorted(p["accion"] for p in me["permisos"] if p["modulo"] == "uniformes")
         == ["carga_inicial", "editar", "eliminar", "ver"],
         sorted(p["accion"] for p in me["permisos"] if p["modulo"] == "uniformes"))

con.close()
print(f"\n{'='*52}\n  {ok} pasaron, {fallos} fallaron\n{'='*52}")
raise SystemExit(1 if fallos else 0)
