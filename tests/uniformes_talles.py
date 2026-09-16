"""Prueba de la tanda 2: talles por empleado."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _base import preparar, token_sistema
DB = preparar()   # una COPIA de la base: la real nunca se toca

import sqlite3
from fastapi.testclient import TestClient
import main
from auth.core import create_token, ensure_admin

ensure_admin()
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
con.execute("UPDATE configuracion SET valor='1' WHERE clave='uniformes_activo'")
con.commit()

cli = TestClient(main.app)
cli.cookies.set("session", token_sistema()[0])

# ── Preparar un tipo con escala en letras, para probar el orden ──────────────
# Idempotente: barre lo que haya quedado de una corrida anterior.
viejo = con.execute("SELECT id FROM uniformes_tipos_talle WHERE nombre=?",
                    ("ZZ Chaqueta (prueba)",)).fetchone()
if viejo:
    con.execute("DELETE FROM uniformes_talles_empleado WHERE tipo_talle_id=?", (viejo[0],))
    con.execute("DELETE FROM uniformes_talle_valores WHERE tipo_talle_id=?", (viejo[0],))
    con.execute("DELETE FROM uniformes_tipos_talle WHERE id=?", (viejo[0],))
    con.commit()

r = cli.post("/api/uniformes/tipos-talle", json={"nombre": "ZZ Chaqueta (prueba)"})
if r.status_code != 201:
    raise SystemExit(f"No se pudo preparar el tipo de prueba: {r.status_code} {r.text[:200]}")
tid = r.json()["id"]
for v in ["S", "M", "L", "XL"]:
    cli.post(f"/api/uniformes/tipos-talle/{tid}/valores", json={"valor": v})

print("\n=== LA GRILLA ===")
r = cli.get("/api/uniformes/talles")
chequear("GET talles responde 200", r.status_code == 200, r.text[:120])
g = r.json()
chequear("trae columnas y filas", "tipos" in g and "empleados" in g, list(g))
tipo = next((t for t in g["tipos"] if t["id"] == tid), None)
chequear("el tipo nuevo es una columna", tipo is not None)
chequear("la escala viene en orden de carga, no alfabetico",
         tipo["escala"] == ["S", "M", "L", "XL"], tipo["escala"])
chequear("hay empleados en la grilla", len(g["empleados"]) > 0, len(g["empleados"]))

emp = g["empleados"][0]
chequear("cada empleado trae su cargo", "cargo" in emp, list(emp))
chequear("y su diccionario de talles", isinstance(emp["talles"], dict))
eid = emp["id"]

print("\n=== CARGAR Y BORRAR ===")
r = cli.put("/api/uniformes/talles",
            json={"empleado_id": eid, "tipo_talle_id": tid, "valor": "L"})
chequear("cargar un talle -> 200", r.status_code == 200, r.text[:120])

g = cli.get("/api/uniformes/talles").json()
emp = next(e for e in g["empleados"] if e["id"] == eid)
chequear("el talle vuelve en la grilla", emp["talles"].get(str(tid)) == "L", emp["talles"])

r = cli.put("/api/uniformes/talles",
            json={"empleado_id": eid, "tipo_talle_id": tid, "valor": "XL"})
g = cli.get("/api/uniformes/talles").json()
emp = next(e for e in g["empleados"] if e["id"] == eid)
chequear("cambiarlo pisa el anterior (no duplica)", emp["talles"].get(str(tid)) == "XL")
n = con.execute("SELECT COUNT(*) FROM uniformes_talles_empleado WHERE empleado_id=? AND tipo_talle_id=?",
                (eid, tid)).fetchone()[0]
chequear("hay una sola fila por empleado y tipo", n == 1, n)

r = cli.put("/api/uniformes/talles",
            json={"empleado_id": eid, "tipo_talle_id": tid, "valor": "XXXL"})
chequear("un valor fuera de la escala -> 400", r.status_code == 400, r.status_code)

r = cli.put("/api/uniformes/talles",
            json={"empleado_id": 999999, "tipo_talle_id": tid, "valor": "L"})
chequear("empleado inexistente -> 404", r.status_code == 404, r.status_code)

r = cli.put("/api/uniformes/talles",
            json={"empleado_id": eid, "tipo_talle_id": 999999, "valor": "L"})
chequear("tipo inexistente -> 404", r.status_code == 404, r.status_code)

r = cli.put("/api/uniformes/talles",
            json={"empleado_id": eid, "tipo_talle_id": tid, "valor": None})
chequear("vaciarlo -> 200", r.status_code == 200)
n = con.execute("SELECT COUNT(*) FROM uniformes_talles_empleado WHERE empleado_id=? AND tipo_talle_id=?",
                (eid, tid)).fetchone()[0]
chequear("vaciarlo borra la fila, no guarda vacio", n == 0, n)

print("\n=== FILTROS ===")
todos = len(cli.get("/api/uniformes/talles").json()["empleados"])
con_inact = len(cli.get("/api/uniformes/talles?incluir_inactivos=true").json()["empleados"])
chequear("incluir las bajas suma gente", con_inact >= todos, f"{todos} vs {con_inact}")

cargo = con.execute(
    "SELECT cargo_id FROM empleados WHERE cargo_id IS NOT NULL AND activo=1 LIMIT 1").fetchone()
if cargo:
    filtrados = cli.get(f"/api/uniformes/talles?cargo_id={cargo[0]}").json()["empleados"]
    chequear("filtrar por cargo achica la lista", 0 < len(filtrados) <= todos,
             f"{len(filtrados)} de {todos}")
    chequear("y todos son de ese cargo",
             len({e["cargo"] for e in filtrados}) == 1, {e["cargo"] for e in filtrados})

print("\n=== REPORTE DE TALLES POR PUESTO ===")
# La lista para comprar: nomina por puesto arriba y total por talle al pie.
valores_tid = cli.get(f"/api/uniformes/tipos-talle/{tid}/valores").json()
if not valores_tid:
    print("  (ese tipo de talle no tiene escala; se saltea)")
else:
    valor_mio = valores_tid[0]["valor"]
    cli.put("/api/uniformes/talles",
            json={"empleado_id": eid, "tipo_talle_id": tid, "valor": valor_mio})
    d = cli.get("/api/uniformes/reportes/talles").json()

    mio = next((p for g in d["grupos"] for p in g["personas"] if p["id"] == eid), None)
    chequear("la persona aparece con su talle, dentro de su puesto",
             mio is not None and mio["talles"].get(str(tid)) == valor_mio,
             mio["talles"] if mio else "no aparece")
    chequear("cada grupo trae su puesto y su gente",
             all(g["cargo"] and g["personas_total"] == len(g["personas"]) for g in d["grupos"]))

    # El total tiene que ser exactamente la suma de los puestos.
    suma = {}
    for g in d["grupos"]:
        for tipo_id, vals in g["resumen"].items():
            for v in vals:
                suma.setdefault(tipo_id, {}).setdefault(v["talle"], 0)
                suma[tipo_id][v["talle"]] += v["personas"]
    total = {k: {v["talle"]: v["personas"] for v in vals} for k, vals in d["total"].items()}
    chequear("el total para comprar es la suma exacta de los puestos", suma == total,
             f"suma {suma} vs total {total}")

    inactivos = {r[0] for r in con.execute("SELECT id FROM empleados WHERE activo=0")}
    acceso = {r[0] for r in con.execute("SELECT id FROM empleados WHERE tipo='acceso'")}
    gente = {p["id"] for g in d["grupos"] for p in g["personas"]}
    chequear("solo personal activo: ni bajas ni tarjetas de acceso",
             not (gente & inactivos) and not (gente & acceso))

    sin_vacios = cli.get("/api/uniformes/reportes/talles?incluir_sin_talle=false").json()
    chequear("se puede pedir solo a los que ya tienen talle cargado",
             all(p["talles"] for g in sin_vacios["grupos"] for p in g["personas"]))

    marcado = con.execute("""SELECT s.cargo_id FROM uniformes_cargos_sin_uniforme s
                             JOIN empleados e ON e.cargo_id = s.cargo_id AND e.activo=1
                             LIMIT 1""").fetchone()
    if marcado:
        sin = {g["cargo_id"] for g in d["grupos"]}
        con_todos = cli.get("/api/uniformes/reportes/talles?incluir_sin_uniforme=true").json()
        chequear("los puestos que no reciben uniforme no inflan la compra",
                 marcado[0] not in sin
                 and marcado[0] in {g["cargo_id"] for g in con_todos["grupos"]})

    r = cli.get("/api/uniformes/reportes/talles.xlsx")
    chequear("el Excel sale", r.status_code == 200 and "spreadsheet" in r.headers.get("content-type", ""),
             r.status_code)

    uni_html = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                 "web", "templates", "uniformes.html"), encoding="utf-8-sig").read()
    chequear("vive en Reportes, que es de leer, y no en la grilla, que es de cargar",
             'id="rp-sub-talles"' in uni_html and 'id="rp-talles"' in uni_html)
    chequear("y agrupa las escalas de una prenda con el mismo criterio que la grilla",
             "agruparTipos(d.tipos)" in uni_html)

print("\n=== BANDERA ===")
con.execute("UPDATE configuracion SET valor='0' WHERE clave='uniformes_activo'")
con.commit()
chequear("con la bandera en 0, la grilla responde 404",
         cli.get("/api/uniformes/talles").status_code == 404)
chequear("y el reporte por puesto tambien",
         cli.get("/api/uniformes/reportes/talles").status_code == 404)
con.execute("UPDATE configuracion SET valor='1' WHERE clave='uniformes_activo'")
con.commit()

# ── Limpieza ────────────────────────────────────────────────────────────────
cli.delete(f"/api/uniformes/tipos-talle/{tid}")
resto = con.execute("SELECT COUNT(*) FROM uniformes_talles_empleado").fetchone()[0]
print(f"\n  (talles que quedan cargados en la base: {resto})")
con.close()

print(f"\n{'='*52}\n  {ok} pasaron, {fallos} fallaron\n{'='*52}")
raise SystemExit(1 if fallos else 0)
