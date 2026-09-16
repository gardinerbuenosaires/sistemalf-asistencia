"""La ficha por persona: el resumen del botón de Empleados y la ficha en el módulo.

El resumen tiene que coincidir exactamente con la fila de esa persona en el
reporte de última entrega: es el mismo cálculo.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _base import preparar, token_sistema, RAIZ

DB = preparar()   # una COPIA de la base: la real nunca se toca

import sqlite3
from datetime import date
from fastapi.testclient import TestClient
import main
from auth.core import create_token

ok = fallos = 0
HOY = date.today().isoformat()


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
cli = TestClient(main.app)
cli.cookies.set("session", token_sistema()[0])

A = [r["id"] for r in con.execute("""
    SELECT e.id FROM empleados e
    WHERE e.activo=1 AND e.tipo!='acceso' AND e.cargo_id IS NOT NULL
      AND e.id NOT IN (SELECT empleado_id FROM uniformes_movimientos)
    ORDER BY e.id LIMIT 1""")][0]
rubros = {r["nombre"]: r for r in cli.get("/api/uniformes/rubros").json()}
ropa, epp = rubros["Ropa de trabajo"], rubros["EPP"]
els = cli.get("/api/uniformes/elementos?solo_activos=true").json()
el_ropa = next(e for e in els if e["categoria_id"] == ropa["id"] and e["tipo_talle_id"])
el_epp = next(e for e in els if e["categoria_id"] == epp["id"] and not e["tipo_talle_id"])
talle = cli.get(f"/api/uniformes/tipos-talle/{el_ropa['tipo_talle_id']}/valores").json()[0]["valor"]


def emitir(fecha, items, **extra):
    r = cli.post("/api/uniformes/constancias", json={"empleado_id": A, "fecha": fecha, "items": items, **extra})
    assert r.status_code == 201, r.text
    return r.json()


print("\n=== SIN ENTREGAS ===")
r = cli.get(f"/api/uniformes/resumen/{A}").json()
chequear("0 constancias", r["constancias"] == 0, r["constancias"])
chequear("nunca recibió nada", r["ultima"] is None)
chequear("sin avisos", r["alertas"] == [])

emitir("2024-01-10", [{"elemento_id": el_ropa["id"], "cantidad": 1, "talle": talle}])
emitir(HOY, [{"elemento_id": el_epp["id"], "cantidad": 1}])
anulada = emitir(HOY, [{"elemento_id": el_epp["id"], "cantidad": 5}])
cli.post(f"/api/uniformes/constancias/{anulada['id']}/anular", json={"motivo": "prueba"})
emitir(HOY, [{"elemento_id": el_epp["id"], "cantidad": 7}], tipo="devolucion")

print("\n=== CON ENTREGAS ===")
r = cli.get(f"/api/uniformes/resumen/{A}").json()
rr = {x["nombre"]: x for x in r["rubros"]}
chequear("cuenta 2 constancias: ni la anulada ni la devolución", r["constancias"] == 2, r["constancias"])
chequear("la última entrega es la de hoy", r["ultima"] and r["ultima"]["fecha"] == HOY)
chequear("ropa: la última es la de 2024", rr["Ropa de trabajo"]["ultima"]["fecha"] == "2024-01-10")
esperada = (ropa["meses_alerta"] is not None
            and rr["Ropa de trabajo"]["ultima"]["meses"] >= ropa["meses_alerta"])
chequear(f"ropa: el aviso respeta el umbral del rubro ({ropa['meses_alerta']} meses)",
         rr["Ropa de trabajo"]["alerta"] == esperada)
chequear("EPP de hoy: sin aviso", rr["EPP"]["alerta"] is False)
chequear("la lista de avisos es la de los rubros en alerta",
         [a["rubro"] for a in r["alertas"]] == [n for n, x in rr.items() if x["alerta"]], r["alertas"])

print("\n=== COINCIDE CON EL REPORTE DE ÚLTIMA ENTREGA ===")
rep = cli.get("/api/uniformes/reportes/antiguedad?incluir_inactivos=true&incluir_sin_uniforme=true").json()
fila = next(f for f in rep["filas"] if f["id"] == A)
chequear("la misma última entrega", fila["ultima"] == r["ultima"])
chequear("las mismas fechas, meses y avisos por rubro",
         all(fila["rubros"][str(x["id"])] == x["ultima"] for x in r["rubros"]))

print("\n=== PUESTO QUE NO RECIBE UNIFORME ===")
cargo = con.execute("SELECT cargo_id FROM empleados WHERE id=?", (A,)).fetchone()[0]
cli.put(f"/api/uniformes/puestos/{cargo}", json={"recibe": False})
r = cli.get(f"/api/uniformes/resumen/{A}").json()
chequear("el resumen avisa que su puesto no recibe, y sigue mostrando lo que recibió",
         r["sin_uniforme"] is True and r["constancias"] == 2)
cli.put(f"/api/uniformes/puestos/{cargo}", json={"recibe": True})

print("\n=== VALIDACIONES Y PERMISOS ===")
chequear("un empleado inexistente -> 404", cli.get("/api/uniformes/resumen/999999").status_code == 404)
acc = con.execute("SELECT id FROM empleados WHERE tipo='acceso' LIMIT 1").fetchone()
if acc:
    chequear("un empleado de tipo acceso -> 404",
             cli.get(f"/api/uniformes/resumen/{acc['id']}").status_code == 404)
sin = con.execute("""SELECT r.id FROM roles r WHERE r.id NOT IN
                     (SELECT rol_id FROM permisos WHERE modulo='uniformes' AND accion='ver') LIMIT 1""").fetchone()
if sin:
    otro = TestClient(main.app)
    otro.cookies.set("session", create_token(999, "prueba@local", sin["id"], "prueba"))
    chequear("un rol sin uniformes:ver no puede ver el resumen",
             otro.get(f"/api/uniformes/resumen/{A}").status_code == 403)

print("\n=== LAS PANTALLAS ===")
emp_html = open(os.path.join(RAIZ, "web", "templates", "empleados.html"), encoding="utf-8-sig").read()
foto = emp_html[emp_html.index("function buildFotoSection"):emp_html.index("function abrirQrFotos")]
chequear("el resumen va junto a la foto, arriba de la ficha", 'class="unif-resumen"' in foto)
chequear("solo se dibuja con permiso del módulo",
         "canDo('uniformes','ver') ? `<div class=\"unif-resumen\"" in foto)
chequear("y lleva a la ficha de uniformes de esa persona", "/uniformes?empleado=${e.id}" in foto)
chequear("reutilizando siempre la misma pestaña, sin acumular", 'target="sistemalf_uniformes"' in foto)
chequear("el pie de la ficha ya no tiene el botón viejo", 'id="btn-uniformes"' not in emp_html)
chequear("carga el resumen al abrir la ficha", "cargarResumenUniformes(id);" in emp_html)
r = cli.get(f"/uniformes?empleado={A}")
chequear("/uniformes?empleado=... responde", r.status_code == 200)
chequear("y la pantalla abre la ficha de esa persona", "abrirFichaDesdeUrl" in r.text)
chequear("Imprimir también reutiliza su propia pestaña", '"sistemalf_constancia"' in r.text
         and '/constancia`, "_blank"' not in r.text)

con.execute("UPDATE configuracion SET valor='0' WHERE clave='uniformes_activo'")
con.commit()
chequear("con la bandera en 0, el resumen responde 404",
         cli.get(f"/api/uniformes/resumen/{A}").status_code == 404)
con.close()

print(f"\n{'=' * 52}\n  {ok} pasaron, {fallos} fallaron\n{'=' * 52}")
raise SystemExit(1 if fallos else 0)
