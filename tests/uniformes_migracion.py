"""La migración del módulo y el circuito completo, sobre una copia de la base.

Pensada para correr antes de pasar a producción, contra una copia de la base de
cada instancia:  python tests/correr_todo.py --origen ruta/de/la/base.db
Si la base ya tiene el módulo, prueba que volver a migrar no rompa nada.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _base import preparar, token_sistema

DB = preparar(migrar=False)   # sin migrar: primero se mira cómo estaba

import sqlite3
import subprocess

ok = fallos = 0


def chequear(desc, cond, extra=""):
    global ok, fallos
    if cond:
        ok += 1
        print(f"  OK   {desc}")
    else:
        fallos += 1
        print(f"  FALLA {desc}  {extra}")


c = sqlite3.connect(DB)
c.row_factory = sqlite3.Row
tenia = [r[0] for r in c.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'uniformes_%'")]
fila = c.execute("SELECT valor FROM configuracion WHERE clave='nombre_empresa'").fetchone()
nombre = fila[0] if fila else ""
ultimo_numero = (c.execute("SELECT COALESCE(MAX(numero), 0) FROM uniformes_movimientos WHERE tipo='entrega'")
                 .fetchone()[0] if tenia else 0)
c.close()

print("\n=== ANTES DE MIGRAR ===")
if tenia:
    print("  (la base ya tenía el módulo: se prueba que volver a migrar no rompa nada)")
else:
    chequear("la base no tenía nada del módulo", True)

from db.database import init_db
from auth.core import ensure_admin
init_db()
ensure_admin()
init_db()          # dos veces: tiene que ser idempotente
ensure_admin()

print("\n=== DESPUÉS DE MIGRAR ===")
c = sqlite3.connect(DB)
c.row_factory = sqlite3.Row
tablas = sorted(r[0] for r in c.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'uniformes_%'"))
chequear("están las 9 tablas del módulo", len(tablas) == 9, tablas)
claves = {r[0]: r[1] for r in c.execute(
    "SELECT clave, valor FROM configuracion WHERE clave LIKE 'empresa_%' OR clave LIKE 'uniformes_%'")}
chequear("están las 8 claves de configuración", len(claves) == 8, sorted(claves))
if not tenia:
    chequear("en una base nueva, la bandera arranca APAGADA", claves.get("uniformes_activo") == "0")
    chequear("y la ventana de ropa pendiente, en 6 meses",
             claves.get("uniformes_meses_pendientes") == "6",
             claves.get("uniformes_meses_pendientes"))
chequear("los rubros iniciales no se duplicaron",
         all(c.execute("SELECT COUNT(*) FROM uniformes_categorias WHERE nombre=?", (n,)).fetchone()[0] == 1
             for n in ("Ropa de trabajo", "EPP")))
fila = c.execute("SELECT valor FROM configuracion WHERE clave='nombre_empresa'").fetchone()
chequear("nombre_empresa no se tocó", (fila[0] if fila else "") == nombre)

print("\n  Roles y sus permisos del módulo:")
for r in c.execute("""SELECT r.nombre,
        (SELECT group_concat(accion, ', ') FROM permisos p WHERE p.rol_id=r.id AND p.modulo='uniformes') perms,
        (SELECT COUNT(*) FROM usuarios u WHERE u.rol_id=r.id AND u.activo=1) usu
      FROM roles r ORDER BY r.nivel DESC, r.nombre"""):
    print(f"    {r['nombre'][:20]:20} {r['usu']:3} usuarios   {r['perms'] or '(ninguno)'}")
c.close()

print("\n=== SIEMBRA DEL CATÁLOGO ===")
env = dict(os.environ, PYTHONIOENCODING="utf-8")
sal = subprocess.run([sys.executable, "scripts/sembrar_uniformes.py", "--aplicar"],
                     capture_output=True, text=True, env=env, encoding="utf-8")
chequear("el script de siembra corre", sal.returncode == 0, sal.stderr[-300:])
chequear("escribió en la copia y no en otra base",
         os.path.basename(os.path.dirname(DB)) in sal.stdout, sal.stdout.splitlines()[:1])
sal2 = subprocess.run([sys.executable, "scripts/sembrar_uniformes.py"],
                      capture_output=True, text=True, env=env, encoding="utf-8")
chequear("una segunda siembra no agrega nada",
         "elementos nuevos      : 0" in sal2.stdout and "tipos de talle nuevos : 0" in sal2.stdout)

print("\n=== CIRCUITO COMPLETO ===")
c = sqlite3.connect(DB)
c.execute("UPDATE configuracion SET valor='1' WHERE clave='uniformes_activo'")
c.commit()
c.close()

from fastapi.testclient import TestClient
import main
cli = TestClient(main.app, raise_server_exceptions=False)   # sin lifespan: sin scheduler
cli.cookies.set("session", token_sistema()[0])

els = cli.get("/api/uniformes/elementos?solo_activos=true").json()
chequear("hay elementos en el catálogo", len(els) >= 1, len(els))
emps = cli.get("/api/uniformes/empleados").json()
chequear("el buscador trae empleados", len(emps) > 0, len(emps))
con_talle = next(e for e in els if e["tipo_talle_id"])
escala = cli.get(f"/api/uniformes/tipos-talle/{con_talle['tipo_talle_id']}/valores").json()
emp = next(e for e in emps if e["activo"] == 1)
r = cli.post("/api/uniformes/constancias", json={
    "empleado_id": emp["id"], "fecha": "2026-09-15",
    "items": [{"elemento_id": con_talle["id"], "cantidad": 1, "talle": escala[0]["valor"]}]})
chequear("emitir una constancia -> 201", r.status_code == 201, r.text[:200])
cst = r.json()
chequear("recibe el número siguiente de la serie de esta base",
         cst.get("numero") == ultimo_numero + 1, f"{cst.get('numero')} vs {ultimo_numero + 1}")
chequear("la hoja imprimible responde", cli.get(f"/uniformes/{cst['id']}/constancia").status_code == 200)
c = sqlite3.connect(DB)
rs = c.execute("SELECT valor FROM configuracion WHERE clave='empresa_razon_social'").fetchone()
c.close()
esperada = (rs[0] if rs and rs[0] else "") or nombre
chequear("el encabezado usa la razón social, o el nombre comercial si falta",
         cli.get("/api/uniformes/empresa").json()["razon_social"] == esperada)
r = cli.post(f"/api/uniformes/constancias/{cst['id']}/anular", json={"motivo": "prueba"})
chequear("anular -> 200", r.status_code == 200, r.status_code)

print("\n=== EL RESTO DEL SISTEMA ===")
EXCLUIR = ("sync/now", "restart", "limpiar", "clear", "backup", "reiniciar")
rutas = sorted({rt.path for rt in main.app.routes
                if "GET" in getattr(rt, "methods", set()) and "{" not in rt.path
                and not any(x in rt.path for x in EXCLUIR)
                and not rt.path.startswith(("/static", "/docs", "/redoc", "/openapi"))})
malos = [(p, s) for p in rutas if (s := cli.get(p, follow_redirects=False).status_code) >= 500]
chequear(f"ninguna de las {len(rutas)} rutas GET da error del servidor", not malos, malos)

print(f"\n{'=' * 52}\n  {ok} pasaron, {fallos} fallaron\n{'=' * 52}")
raise SystemExit(1 if fallos else 0)
