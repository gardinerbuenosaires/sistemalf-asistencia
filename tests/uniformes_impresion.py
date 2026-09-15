"""Prueba de la tanda 4: la hoja imprimible y sus datos."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _base import preparar, token_sistema
DB = preparar()   # una COPIA de la base: la real nunca se toca

import sqlite3
from fastapi.testclient import TestClient
import main
from auth.core import create_token, ensure_admin, invalidar_cache

ensure_admin()
ok = fallos = 0
creadas = []


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
con.execute("UPDATE configuracion SET valor='1' WHERE clave='uniformes_activo'")
con.commit()

CLAVES = ["empresa_razon_social", "empresa_cuit", "nombre_empresa"]
previos = {k: con.execute("SELECT valor FROM configuracion WHERE clave=?", (k,)).fetchone()[0]
           for k in CLAVES}
TALLES_PREVIOS = [tuple(r) for r in con.execute(
    "SELECT empleado_id, tipo_talle_id, valor, modificado_en FROM uniformes_talles_empleado")]

cli = TestClient(main.app)
cli.cookies.set("session", token_sistema()[0])

print("\n=== DATOS DE EMPRESA ===")
con.execute("UPDATE configuracion SET valor='' WHERE clave='empresa_razon_social'")
con.commit()
e = cli.get("/api/uniformes/empresa")
chequear("GET empresa -> 200", e.status_code == 200, e.text[:150])
e = e.json()
chequear("trae los siete campos del encabezado",
         {"razon_social", "cuit", "direccion", "localidad", "cp", "provincia", "logo"} <= set(e), list(e))
chequear("sin razón social, usa el nombre comercial",
         e["razon_social"] == previos["nombre_empresa"], f"{e['razon_social']!r} vs {previos['nombre_empresa']!r}")

con.execute("UPDATE configuracion SET valor='ZZ PRUEBA SA' WHERE clave='empresa_razon_social'")
con.commit()
chequear("con razón social cargada, usa la razón social",
         cli.get("/api/uniformes/empresa").json()["razon_social"] == "ZZ PRUEBA SA")

# Un rol que ve uniformes pero no la configuración del sistema (como RRHH)
rol = con.execute("""
    SELECT p.rol_id FROM permisos p
    WHERE p.modulo='uniformes' AND p.accion='ver'
      AND p.rol_id NOT IN (SELECT rol_id FROM permisos WHERE modulo='usuarios' AND accion='ver')
    LIMIT 1""").fetchone()
if rol:
    rrhh = TestClient(main.app)
    rrhh.cookies.set("session", create_token(999, "prueba@local", rol["rol_id"], "prueba"))
    chequear("un rol sin usuarios:ver NO puede leer /api/configuracion",
             rrhh.get("/api/configuracion").status_code == 403)
    chequear("pero SÍ puede leer los datos para imprimir",
             rrhh.get("/api/uniformes/empresa").status_code == 200)
else:
    print("  (no hay un rol con uniformes:ver y sin usuarios:ver; se saltea)")

print("\n=== LA HOJA IMPRIMIBLE ===")
eid = con.execute("SELECT id FROM empleados WHERE activo=1 AND tipo!='acceso' LIMIT 1").fetchone()[0]
elid = con.execute("SELECT id FROM uniformes_elementos WHERE tipo_talle_id IS NULL LIMIT 1").fetchone()[0]
c = cli.post("/api/uniformes/constancias", json={
    "empleado_id": eid, "fecha": "2026-09-15",
    "items": [{"elemento_id": elid, "cantidad": 1}]}).json()
creadas.append(c["id"])

r = cli.get(f"/uniformes/{c['id']}/constancia")
chequear("la página de la constancia -> 200", r.status_code == 200, r.status_code)
chequear("es la hoja imprimible (no el listado)",
         "CONSTANCIA DE ENTREGA DE ROPA DE TRABAJO" in r.text and "cn-vista-listado" not in r.text)
chequear("lee los datos de empresa del endpoint del módulo",
         "/api/uniformes/empresa" in r.text and "/api/configuracion" not in r.text)
chequear("la maqueta ya no dice MAQUETA", "MAQUETA" not in r.text and "Maqueta" not in r.text)
chequear("la fila de dirección tiene la etiqueta PROVINCIA", "PROVINCIA:" in r.text)

anon = TestClient(main.app)
r = anon.get(f"/uniformes/{c['id']}/constancia", follow_redirects=False)
chequear("sin sesión, redirige al login",
         r.status_code in (302, 303, 307) and r.headers.get("location", "").endswith("/login"),
         f"{r.status_code} {r.headers.get('location')}")

print("\n=== BANDERA ===")
con.execute("UPDATE configuracion SET valor='0' WHERE clave='uniformes_activo'")
con.commit()
chequear("con la bandera en 0, la hoja responde 404",
         cli.get(f"/uniformes/{c['id']}/constancia").status_code == 404)
chequear("con la bandera en 0, los datos de empresa responden 404",
         cli.get("/api/uniformes/empresa").status_code == 404)
con.execute("UPDATE configuracion SET valor='1' WHERE clave='uniformes_activo'")
con.commit()

# ── Limpieza ────────────────────────────────────────────────────────────────
for cid in creadas:
    con.execute("DELETE FROM uniformes_items WHERE movimiento_id=?", (cid,))
    con.execute("DELETE FROM uniformes_movimientos WHERE id=?", (cid,))
for k, v in previos.items():
    con.execute("UPDATE configuracion SET valor=? WHERE clave=?", (v, k))
con.execute("DELETE FROM uniformes_talles_empleado")
con.executemany(
    "INSERT INTO uniformes_talles_empleado (empleado_id, tipo_talle_id, valor, modificado_en) "
    "VALUES (?,?,?,?)", TALLES_PREVIOS)
con.commit()
con.close()
invalidar_cache()

print(f"\n{'='*52}\n  {ok} pasaron, {fallos} fallaron\n{'='*52}")
raise SystemExit(1 if fallos else 0)
