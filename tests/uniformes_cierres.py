"""Ropa pendiente, bandeja de egresados y cierre del circuito.

Lo que se fija acá es la regla que decidimos: el sistema no sabe qué ropa tiene
puesta alguien, así que muestra lo entregado en una ventana de meses, deja lo
anterior aparte, y el cierre —que no exige devolución registrada— saca a la
persona de la bandeja y funciona como fecha de corte.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _base import preparar, token_sistema, RAIZ

DB = preparar()   # una COPIA de la base: la real nunca se toca

import sqlite3
from datetime import date, timedelta
from fastapi.testclient import TestClient
import main
from auth.core import create_token, invalidar_cache

ok = fallos = 0
HOY = date.today()
RECIENTE = (HOY - timedelta(days=45)).isoformat()
VIEJA = (HOY - timedelta(days=900)).isoformat()


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

A = con.execute("""
    SELECT e.id FROM empleados e
    WHERE e.activo=1 AND e.tipo!='acceso' AND e.cargo_id IS NOT NULL
      AND e.id NOT IN (SELECT empleado_id FROM uniformes_movimientos)
    ORDER BY e.id LIMIT 1""").fetchone()["id"]
els = cli.get("/api/uniformes/elementos?solo_activos=true").json()
EL1, EL2 = els[0], els[1]


def emitir(fecha, elemento, cantidad=1, **extra):
    r = cli.post("/api/uniformes/constancias", json={
        "empleado_id": A, "fecha": fecha,
        "items": [{"elemento_id": elemento["id"], "cantidad": cantidad}], **extra})
    assert r.status_code == 201, r.text[:200]
    return r.json()


def pend():
    return cli.get(f"/api/uniformes/pendientes/{A}").json()


print("\n=== LA VENTANA ===")
cli.put("/api/uniformes/parametros", json={"meses_pendientes": 6})
chequear("la ventana se guarda y se lee",
         cli.get("/api/uniformes/parametros").json()["meses_pendientes"] == 6)
chequear("no se puede poner en 0",
         cli.put("/api/uniformes/parametros", json={"meses_pendientes": 0}).status_code == 400)

print("\n=== QUE PUEDE TENER ===")
d = pend()
chequear("sin entregas no hay nada pendiente",
         d["total_pendiente"] == 0 and d["prendas"] == [] and d["anteriores"] == [], d["total_pendiente"])

emitir(RECIENTE, EL1, 2)
emitir(RECIENTE, EL1, 1)
emitir(VIEJA, EL2, 1)
d = pend()
dentro = {p["elemento"]: p for p in d["prendas"]}
chequear("dos entregas de la misma prenda se suman en una linea",
         len(d["prendas"]) == 1 and dentro[EL1["nombre"]]["entregado"] == 3, d["prendas"])
chequear("y el total pendiente es 3", d["total_pendiente"] == 3, d["total_pendiente"])
chequear("lo de hace dos años no suma, va aparte con su fecha",
         len(d["anteriores"]) == 1 and d["anteriores"][0]["elemento"] == EL2["nombre"]
         and d["anteriores"][0]["antiguedad"]["fecha"] == VIEJA, d["anteriores"])
chequear("las constancias del periodo son las 2 de la ventana, no la vieja",
         len(d["constancias"]) == 2 and all(c["fecha"] >= d["desde"] for c in d["constancias"]),
         [c["fecha"] for c in d["constancias"]])

print("\n=== LA DEVOLUCION RESTA ===")
emitir(HOY.isoformat(), EL1, 2, tipo="devolucion")
d = pend()
p1 = next(p for p in d["prendas"] if p["elemento"] == EL1["nombre"])
chequear("devolver 2 de 3 deja 1 pendiente, y se ve cuanto devolvio",
         p1["pendiente"] == 1 and p1["devuelto"] == 2 and p1["entregado"] == 3, p1)
chequear("el total refleja la devolucion", d["total_pendiente"] == 1, d["total_pendiente"])

print("\n=== ACHICAR LA VENTANA MUEVE, NO BORRA ===")
cli.put("/api/uniformes/parametros", json={"meses_pendientes": 1})
d = pend()
chequear("con 1 mes, lo de hace 45 dias pasa a ser anterior",
         any(x["elemento"] == EL1["nombre"] for x in d["anteriores"]), d["anteriores"])
cli.put("/api/uniformes/parametros", json={"meses_pendientes": 6})

print("\n=== LA BANDEJA DE EGRESADOS ===")
con.execute("UPDATE empleados SET activo=0, fecha_egreso=? WHERE id=?", (HOY.isoformat(), A))
con.commit()
filas = {f["id"]: f for f in cli.get("/api/uniformes/egresados").json()["filas"]}
chequear("el egresado con ropa aparece en la bandeja", A in filas)
chequear("con lo que hay que pedirle", filas[A]["total_pendiente"] == 1 if A in filas else False,
         filas.get(A, {}).get("prendas"))

print("\n=== EL CIERRE LO SACA DE LA BANDEJA ===")
r = cli.post("/api/uniformes/cierres", json={
    "empleado_id": A, "resultado": "no_devolvio", "observacion": "Se le descontó de la liquidación"})
chequear("se puede cerrar sin registrar ninguna devolucion", r.status_code == 201, r.text[:200])
cierre = r.json()
chequear("el cierre congela lo que quedaba pendiente", bool(cierre["pendiente_json"]))
chequear("y guarda quien lo cerro", bool(cierre["cerrado_por"]))

ids = [f["id"] for f in cli.get("/api/uniformes/egresados").json()["filas"]]
chequear("ya no figura en la bandeja", A not in ids)
con_cerrados = cli.get("/api/uniformes/egresados?incluir_cerrados=true").json()["filas"]
chequear("pero se lo puede ver pidiendo los cerrados",
         any(f["id"] == A and f["cierre"] for f in con_cerrados))
chequear("y la ficha del empleado se entera de que su baja quedo completa",
         cli.get(f"/api/uniformes/resumen/{A}").json().get("cierre") is not None)
chequear("dos cierres vigentes para la misma persona -> 409",
         cli.post("/api/uniformes/cierres",
                  json={"empleado_id": A, "resultado": "devolvio_todo"}).status_code == 409)
chequear("un resultado inventado -> 400",
         cli.post("/api/uniformes/cierres",
                  json={"empleado_id": A, "resultado": "vaya_uno_a_saber"}).status_code == 400)

print("\n=== EL CIERRE ES FECHA DE CORTE ===")
d = pend()
chequear("despues de cerrar no arrastra nada de antes",
         d["total_pendiente"] == 0 and d["prendas"] == [] and d["anteriores"] == [], d)
# El cierre quedo hecho hace un minuto: si no, cae en el mismo segundo que la
# entrega que sigue y el desempate por hora no se podria probar.
con.execute("UPDATE uniformes_cierres SET cerrado_en=datetime(cerrado_en,'-1 minute') WHERE id=?",
            (cierre["id"],))
con.commit()
emitir(HOY.isoformat(), EL2, 1)
d = pend()
chequear("lo entregado el mismo dia pero despues del cierre vuelve a contar (recontratacion)",
         d["total_pendiente"] == 1, d["total_pendiente"])

print("\n=== REABRIR ===")
chequear("reabrir sin motivo -> 400",
         cli.post(f"/api/uniformes/cierres/{cierre['id']}/reabrir",
                  json={"motivo": "  "}).status_code == 400)
r = cli.post(f"/api/uniformes/cierres/{cierre['id']}/reabrir",
             json={"motivo": "Trajo la ropa una semana después"})
chequear("reabrir con motivo -> 200", r.status_code == 200, r.text[:150])
chequear("el cierre no se borra: queda como historia con su motivo",
         r.json()["estado"] == "reabierto" and bool(r.json()["reabierto_por"]))
chequear("reabrir dos veces -> 409",
         cli.post(f"/api/uniformes/cierres/{cierre['id']}/reabrir",
                  json={"motivo": "otra vez"}).status_code == 409)
chequear("reabierto, vuelve a contar lo viejo", pend()["total_pendiente"] >= 1)
chequear("y se puede cerrar de nuevo",
         cli.post("/api/uniformes/cierres",
                  json={"empleado_id": A, "resultado": "devolvio_todo"}).status_code == 201)
chequear("la ficha muestra los dos cierres", len(pend()["cierres"]) == 2, pend()["cierres"])

print("\n=== CIERRE MASIVO DE PUESTA EN MARCHA ===")
antes = con.execute("SELECT COUNT(*) FROM uniformes_cierres").fetchone()[0]
r = cli.post("/api/uniformes/cierres/masivo", json={"hasta": "2024-12-31"}).json()
ahora = con.execute("SELECT COUNT(*) FROM uniformes_cierres").fetchone()[0]
chequear("simula por defecto: dice a cuantos alcanza y no escribe nada",
         r["simulacion"] is True and ahora == antes, f"{antes} -> {ahora}")
r2 = cli.post("/api/uniformes/cierres/masivo",
              json={"hasta": "2024-12-31", "simular": False}).json()
ahora = con.execute("SELECT COUNT(*) FROM uniformes_cierres").fetchone()[0]
chequear("aplicado, cierra a los egresados viejos de una vez",
         r2["cantidad"] == r["cantidad"] and ahora == antes + r2["cantidad"], f"{antes} -> {ahora}")
chequear("con el resultado que dice que son anteriores al sistema",
         con.execute("SELECT COUNT(*) FROM uniformes_cierres WHERE resultado='previo_al_sistema'"
                     ).fetchone()[0] == r2["cantidad"])
r3 = cli.post("/api/uniformes/cierres/masivo", json={"hasta": "2024-12-31"}).json()
chequear("correrlo de nuevo no alcanza a nadie: ya estan cerrados", r3["cantidad"] == 0, r3["cantidad"])

print("\n=== PERMISOS ===")
rol = token_sistema()[1]
con.execute("DELETE FROM permisos WHERE rol_id=? AND modulo='uniformes' AND accion='carga_inicial'", (rol,))
con.commit(); invalidar_cache()
chequear("sin carga_inicial no se puede cerrar en masa",
         cli.post("/api/uniformes/cierres/masivo",
                  json={"hasta": "2024-12-31"}).status_code == 403)
con.execute("INSERT OR IGNORE INTO permisos (rol_id,modulo,accion) VALUES (?,'uniformes','carga_inicial')", (rol,))
con.commit(); invalidar_cache()

sin = con.execute("""SELECT r.id FROM roles r WHERE r.id NOT IN
                     (SELECT rol_id FROM permisos WHERE modulo='uniformes' AND accion='ver') LIMIT 1""").fetchone()
if sin:
    otro = TestClient(main.app)
    otro.cookies.set("session", create_token(999, "prueba@local", sin["id"], "prueba"))
    chequear("un rol sin uniformes:ver no ve la bandeja",
             otro.get("/api/uniformes/egresados").status_code == 403)

print("\n=== LAS PANTALLAS ===")
uni = open(os.path.join(RAIZ, "web", "templates", "uniformes.html"), encoding="utf-8-sig").read()
chequear("la bandeja de egresados es una sub-pestaña de Reportes", 'id="rp-sub-egresados"' in uni)
chequear("se filtra por fecha de egreso y se pueden ver los ya cerrados",
         'id="eg-desde"' in uni and 'id="eg-cerrados"' in uni)
chequear("el panel de ropa pendiente se dibuja dentro de la ficha", "panelPendientes(pend)" in uni)
chequear("y se pide junto con el resumen, en una sola vuelta",
         "/api/uniformes/pendientes/${id}" in uni and "Promise.all" in uni)
chequear("el cierre ofrece los tres resultados",
         all(f'value="{r}"' in uni for r in ("devolvio_todo", "devolvio_parcial", "no_devolvio")))
chequear("con observacion libre, sin exigir constancia de devolucion", 'id="cierre-obs"' in uni)
chequear("reabrir existe y pide permiso de eliminar",
         "reabrirCierre" in uni and 'canDo("uniformes", "eliminar")' in uni)
chequear("la ventana se configura en su propia sub-pestaña",
         'id="cfg-tab-parametros"' in uni and 'id="cfg-meses"' in uni)
chequear("las fechas salen del helper local, nunca de toISOString",
         "ymdLocal(" in uni and "toISOString" not in uni)
emp = open(os.path.join(RAIZ, "web", "templates", "empleados.html"), encoding="utf-8-sig").read()
chequear("la ficha del empleado avisa cuando la ropa quedo cerrada", "unif-cerrado" in emp)

print("\n=== BANDERA ===")
con.execute("UPDATE configuracion SET valor='0' WHERE clave='uniformes_activo'")
con.commit()
chequear("con la bandera en 0, la bandeja responde 404",
         cli.get("/api/uniformes/egresados").status_code == 404)
chequear("y el panel de la persona tambien",
         cli.get(f"/api/uniformes/pendientes/{A}").status_code == 404)
con.close()

print(f"\n{'=' * 52}\n  {ok} pasaron, {fallos} fallaron\n{'=' * 52}")
raise SystemExit(1 if fallos else 0)
