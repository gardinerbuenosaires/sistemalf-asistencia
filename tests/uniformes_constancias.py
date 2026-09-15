"""Prueba de la tanda 3: el circuito de la constancia."""
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

cli = TestClient(main.app)
cli.cookies.set("session", token_sistema()[0])

# Respaldo de los talles reales: la prueba los toca, y al final hay que dejarlos
# como estaban. Vaciar la tabla se lleva puesto lo que el usuario haya cargado.
TALLES_PREVIOS = [tuple(r) for r in con.execute(
    "SELECT empleado_id, tipo_talle_id, valor, modificado_en FROM uniformes_talles_empleado")]

print("\n=== BUSCADOR DE EMPLEADOS ===")
emps = cli.get("/api/uniformes/empleados").json()
chequear("devuelve empleados", len(emps) > 0, len(emps))
chequear("no incluye los de tipo acceso",
         all(e["id"] not in {r["id"] for r in con.execute("SELECT id FROM empleados WHERE tipo='acceso'")}
             for e in emps))
chequear("incluye egresados (para devoluciones y reimpresiones)",
         any(e["activo"] == 0 for e in emps))
chequear("cada uno trae dni, cargo y sus talles",
         all({"dni", "cargo", "talles"} <= set(e) for e in emps))

emp = next(e for e in emps if e["activo"] == 1)
eid = emp["id"]

# Elementos con y sin talle
els = cli.get("/api/uniformes/elementos?solo_activos=true").json()
con_talle = next(e for e in els if e["tipo_talle_id"])
sin_talle = next(e for e in els if not e["tipo_talle_id"])
escala = cli.get(f"/api/uniformes/tipos-talle/{con_talle['tipo_talle_id']}/valores").json()
talle_ok = escala[0]["valor"]

print("\n=== EMITIR ===")
r = cli.post("/api/uniformes/constancias", json={
    "empleado_id": eid, "fecha": "2026-09-10",
    "items": [{"elemento_id": con_talle["id"], "cantidad": 2, "talle": talle_ok},
              {"elemento_id": sin_talle["id"], "cantidad": 1}]})
chequear("crear constancia -> 201", r.status_code == 201, r.text[:200])
c1 = r.json()
creadas.append(c1["id"])
chequear("queda emitida", c1["estado"] == "emitida", c1["estado"])
chequear("recibe numero", c1["numero"] is not None, c1["numero"])
chequear("guarda copia del apellido y nombre", bool(c1["empleado_apellido_nombre"]))
chequear("guarda copia del cargo", c1["cargo_nombre"] == emp["cargo"], c1["cargo_nombre"])
chequear("guarda quien la emitio", bool(c1["creado_por"]), c1["creado_por"])

det = cli.get(f"/api/uniformes/constancias/{c1['id']}").json()
chequear("el detalle trae los dos renglones", len(det["items"]) == 2, len(det["items"]))
chequear("los renglones respetan el orden",
         [i["orden"] for i in det["items"]] == [0, 1])
chequear("cada renglon guarda su copia del elemento",
         det["items"][0]["elemento_nombre"] == con_talle["nombre"])

print("\n=== LA COPIA NO CAMBIA ===")
cli.patch(f"/api/uniformes/elementos/{con_talle['id']}",
          json={"marca": "ZZ MARCA CAMBIADA"})
det2 = cli.get(f"/api/uniformes/constancias/{c1['id']}").json()
chequear("editar el catalogo NO altera la constancia firmada",
         det2["items"][0]["marca"] == con_talle["marca"],
         f"{det2['items'][0]['marca']!r} vs {con_talle['marca']!r}")
cli.patch(f"/api/uniformes/elementos/{con_talle['id']}",
          json={"marca": con_talle["marca"] or ""})

print("\n=== EL TALLE ACTUALIZA EL REGISTRO ===")
v = con.execute("SELECT valor FROM uniformes_talles_empleado WHERE empleado_id=? AND tipo_talle_id=?",
                (eid, con_talle["tipo_talle_id"])).fetchone()
chequear("entregar un talle lo deja cargado en la persona",
         v is not None and v["valor"] == talle_ok, v["valor"] if v else None)

print("\n=== LA OTRA ESCALA DE LA MISMA PRENDA ===")
# Un elemento cuya prenda tiene dos escalas (por ejemplo «Pantalón (número)» y
# «Pantalón (letra)»): tiene que poder entregarse en cualquiera de las dos.
tipos = {t["id"]: t["nombre"] for t in cli.get("/api/uniformes/tipos-talle").json()}


def base(n):
    import re
    return re.sub(r"\s*\([^)]*\)\s*$", "", n or "").strip()


par = None
for el in els:
    if not el["tipo_talle_id"]:
        continue
    mio = tipos.get(el["tipo_talle_id"], "")
    hermanos = [i for i, n in tipos.items() if base(n) == base(mio) and i != el["tipo_talle_id"]]
    if hermanos:
        par = (el, el["tipo_talle_id"], hermanos[0])
        break

if not par:
    print("  (ninguna prenda tiene dos escalas; se saltea)")
else:
    el_par, tipo_propio, tipo_hermano = par
    v_propio = cli.get(f"/api/uniformes/tipos-talle/{tipo_propio}/valores").json()[0]["valor"]
    v_otro = cli.get(f"/api/uniformes/tipos-talle/{tipo_hermano}/valores").json()[0]["valor"]

    # Se le carga el talle en la escala propia del elemento.
    cli.put("/api/uniformes/talles",
            json={"empleado_id": eid, "tipo_talle_id": tipo_propio, "valor": v_propio})

    r = cli.post("/api/uniformes/constancias", json={
        "empleado_id": eid, "fecha": "2026-09-10",
        "items": [{"elemento_id": el_par["id"], "cantidad": 1, "talle": v_otro}]})
    chequear(f"entregar «{el_par['nombre']}» en la otra escala ({v_otro}) -> 201",
             r.status_code == 201, r.text[:200])
    if r.status_code == 201:
        creadas.append(r.json()["id"])

    guardado = con.execute(
        "SELECT valor FROM uniformes_talles_empleado WHERE empleado_id=? AND tipo_talle_id=?",
        (eid, tipo_hermano)).fetchone()
    chequear("el talle se guarda en la escala a la que pertenece",
             guardado is not None and guardado["valor"] == v_otro,
             guardado["valor"] if guardado else None)

    intacto = con.execute(
        "SELECT valor FROM uniformes_talles_empleado WHERE empleado_id=? AND tipo_talle_id=?",
        (eid, tipo_propio)).fetchone()
    chequear("y no pisa el que tenía en la escala original",
             intacto is not None and intacto["valor"] == v_propio,
             intacto["valor"] if intacto else None)

    r = cli.post("/api/uniformes/constancias", json={
        "empleado_id": eid, "fecha": "2026-09-10",
        "items": [{"elemento_id": el_par["id"], "cantidad": 1, "talle": "ZZ99"}]})
    chequear("un valor que no está en ninguna escala de esa prenda -> 400",
             r.status_code == 400, r.status_code)

print("\n=== CORRELATIVO ===")
# Dos seguidas, para que la comparacion no dependa de lo que se emitio antes.
ca = cli.post("/api/uniformes/constancias", json={
    "empleado_id": eid, "fecha": "2026-09-10",
    "items": [{"elemento_id": sin_talle["id"], "cantidad": 1}]}).json()
r2 = cli.post("/api/uniformes/constancias", json={
    "empleado_id": eid, "fecha": "2026-09-10",
    "items": [{"elemento_id": sin_talle["id"], "cantidad": 1}]})
c2 = r2.json()
creadas.extend([ca["id"], c2["id"]])
chequear("el numero avanza de a uno", c2["numero"] == ca["numero"] + 1,
         f"{ca['numero']} -> {c2['numero']}")

r3 = cli.post("/api/uniformes/constancias", json={
    "empleado_id": eid, "fecha": "2026-09-10", "tipo": "devolucion",
    "items": [{"elemento_id": sin_talle["id"], "cantidad": 1}]})
c3 = r3.json()
creadas.append(c3["id"])
chequear("la devolucion lleva su propia serie", c3["numero"] == 1, c3["numero"])

print("\n=== CARGA HISTORICA ===")
r = cli.post("/api/uniformes/constancias", json={
    "empleado_id": eid, "fecha": "2023-05-10", "origen": "historico",
    "items": [{"elemento_id": con_talle["id"], "cantidad": 1, "talle": "ZZ raro"}]})
chequear("se puede cargar una entrega vieja del papel", r.status_code == 201, r.text[:200])
ch = r.json()
creadas.append(ch["id"])
chequear("el historico NO lleva numero", ch["numero"] is None, ch["numero"])
chequear("acepta un talle que no esta en la escala (viene del papel)",
         cli.get(f"/api/uniformes/constancias/{ch['id']}").json()["items"][0]["talle"] == "ZZ raro")

con.execute("DELETE FROM permisos WHERE rol_id=? AND modulo='uniformes' AND accion='carga_inicial'", (token_sistema()[1],))
con.commit(); invalidar_cache()
r = cli.post("/api/uniformes/constancias", json={
    "empleado_id": eid, "fecha": "2023-05-10", "origen": "historico",
    "items": [{"elemento_id": sin_talle["id"], "cantidad": 1}]})
chequear("sin permiso carga_inicial -> 403", r.status_code == 403, r.status_code)
con.execute("INSERT OR IGNORE INTO permisos (rol_id,modulo,accion) VALUES (?,'uniformes','carga_inicial')", (token_sistema()[1],))
con.commit(); invalidar_cache()

print("\n=== VALIDACIONES ===")
r = cli.post("/api/uniformes/constancias",
             json={"empleado_id": eid, "fecha": "2026-09-10", "items": []})
chequear("sin renglones -> 400", r.status_code == 400, r.status_code)

muchos = [{"elemento_id": sin_talle["id"], "cantidad": 1} for _ in range(15)]
r = cli.post("/api/uniformes/constancias",
             json={"empleado_id": eid, "fecha": "2026-09-10", "items": muchos})
chequear("mas de 14 renglones -> 400 (no entra en la hoja)", r.status_code == 400, r.status_code)

r = cli.post("/api/uniformes/constancias", json={
    "empleado_id": eid, "fecha": "10/09/2026",
    "items": [{"elemento_id": sin_talle["id"], "cantidad": 1}]})
chequear("fecha mal formada -> 400", r.status_code == 400, r.status_code)

r = cli.post("/api/uniformes/constancias", json={
    "empleado_id": eid, "fecha": "2026-09-10",
    "items": [{"elemento_id": con_talle["id"], "cantidad": 1, "talle": "ZZ inventado"}]})
chequear("talle fuera de la escala -> 400", r.status_code == 400, r.status_code)

r = cli.post("/api/uniformes/constancias", json={
    "empleado_id": eid, "fecha": "2026-09-10",
    "items": [{"elemento_id": sin_talle["id"], "cantidad": 0}]})
chequear("cantidad 0 -> 400", r.status_code == 400, r.status_code)

acc = con.execute("SELECT id FROM empleados WHERE tipo='acceso' LIMIT 1").fetchone()
if acc:
    r = cli.post("/api/uniformes/constancias", json={
        "empleado_id": acc["id"], "fecha": "2026-09-10",
        "items": [{"elemento_id": sin_talle["id"], "cantidad": 1}]})
    chequear("un empleado de tipo acceso -> 404", r.status_code == 404, r.status_code)

print("\n=== ANULAR ===")
r = cli.post(f"/api/uniformes/constancias/{c2['id']}/anular", json={"motivo": "  "})
chequear("anular sin motivo -> 400", r.status_code == 400, r.status_code)

r = cli.post(f"/api/uniformes/constancias/{c2['id']}/anular",
             json={"motivo": "Se cargó el empleado equivocado"})
chequear("anular con motivo -> 200", r.status_code == 200, r.text[:150])
an = r.json()
chequear("queda anulada", an["estado"] == "anulada")
chequear("guarda el motivo", an["motivo_anulacion"] == "Se cargó el empleado equivocado")
chequear("guarda quien y cuando", bool(an["anulada_por"]) and bool(an["anulada_en"]))

r = cli.post(f"/api/uniformes/constancias/{c2['id']}/anular", json={"motivo": "otra vez"})
chequear("anular dos veces -> 409", r.status_code == 409, r.status_code)

n = con.execute("SELECT COUNT(*) FROM uniformes_items WHERE movimiento_id=?", (c2["id"],)).fetchone()[0]
chequear("anular NO borra los renglones", n == 1, n)

print("\n=== LISTADO Y FILTROS ===")
todas = cli.get("/api/uniformes/constancias").json()
chequear("lista las constancias", len(todas) >= 4, len(todas))
chequear("trae el recuento de renglones y unidades",
         all({"renglones", "unidades"} <= set(x) for x in todas))
solo_dev = cli.get("/api/uniformes/constancias?tipo=devolucion").json()
chequear("filtra por tipo", all(x["tipo"] == "devolucion" for x in solo_dev) and solo_dev)
solo_hist = cli.get("/api/uniformes/constancias?origen=historico").json()
chequear("filtra por origen", all(x["origen"] == "historico" for x in solo_hist) and solo_hist)
viejas = cli.get("/api/uniformes/constancias?hasta=2024-01-01").json()
chequear("filtra por fecha", all(x["fecha"] <= "2024-01-01" for x in viejas) and viejas)

print("\n=== BANDERA ===")
con.execute("UPDATE configuracion SET valor='0' WHERE clave='uniformes_activo'")
con.commit()
chequear("con la bandera en 0, las constancias responden 404",
         cli.get("/api/uniformes/constancias").status_code == 404)
con.execute("UPDATE configuracion SET valor='1' WHERE clave='uniformes_activo'")
con.commit()

# ── Limpieza: la base queda como estaba ─────────────────────────────────────
for cid in creadas:
    con.execute("DELETE FROM uniformes_items WHERE movimiento_id=?", (cid,))
    con.execute("DELETE FROM uniformes_movimientos WHERE id=?", (cid,))
con.execute("DELETE FROM uniformes_talles_empleado")
con.executemany(
    "INSERT INTO uniformes_talles_empleado (empleado_id, tipo_talle_id, valor, modificado_en) "
    "VALUES (?,?,?,?)", TALLES_PREVIOS)
con.commit()
q = con.execute("SELECT COUNT(*) FROM uniformes_movimientos").fetchone()[0]
tt = con.execute("SELECT COUNT(*) FROM uniformes_talles_empleado").fetchone()[0]
print(f"\n  (constancias que quedan en la base: {q}"
      f" · talles restaurados: {tt} de {len(TALLES_PREVIOS)})")
con.close()

print(f"\n{'='*52}\n  {ok} pasaron, {fallos} fallaron\n{'='*52}")
raise SystemExit(1 if fallos else 0)
