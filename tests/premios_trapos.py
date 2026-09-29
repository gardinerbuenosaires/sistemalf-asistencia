"""Premios: descuento de trapos de cocina por checkbox.

Lo que se fija acá:
- Tildar a un empleado le descuenta el valor cargado; destildarlo lo vuelve a 0.
- Sin valor cargado no se puede tildar.
- El premio nunca queda negativo aunque el valor supere lo que cobra.
- Generar y "Recalcular todo" conservan lo tildado (no hay reparto).
- Cambiar el valor actualiza solo a los tildados del período indicado, si está abierto.
- Un período cerrado no se toca: ni el cambio de valor ni el checkbox.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _base import preparar, token_sistema

DB = preparar()   # una COPIA de la base: la real nunca se toca

import sqlite3
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
con.execute("UPDATE configuracion SET valor='1' WHERE clave='trapos_cocina_activo'")
con.commit()
cli = TestClient(main.app)
cli.cookies.set("session", token_sistema()[0])


def fila(ev_id):
    return con.execute("SELECT * FROM premios_evaluacion WHERE id=?", (ev_id,)).fetchone()


def tildar(ev_id, valor):
    return cli.put(f"/api/premios/evaluacion/{ev_id}", json={"trapos": valor})


# Período abierto con evaluaciones: el más reciente no cerrado
abierto = con.execute("""
    SELECT pe.periodo FROM premios_evaluacion pe
    WHERE NOT EXISTS (SELECT 1 FROM premios_periodos_cerrados c
                      WHERE printf('%04d-%02d', c.anio, c.mes) = pe.periodo)
    GROUP BY pe.periodo ORDER BY pe.periodo DESC LIMIT 1""").fetchone()
if not abierto:
    sys.exit("La base no tiene un período de premios abierto con evaluaciones")
P = abierto["periodo"]
print(f"Período abierto de prueba: {P}")

# Arranca vacío, como un mes nuevo
con.execute("UPDATE premios_evaluacion SET deduccion_trapos=0 WHERE periodo=?", (P,))
con.commit()
r = cli.post(f"/api/premios/{P}/generar")
chequear("generar responde 200", r.status_code == 200, r.text[:200])

# Sin valor cargado no se puede tildar
r = cli.put("/api/premios/trapos-valor", json={"valor": 0, "periodo": P})
chequear("guardar valor 0", r.status_code == 200, r.text[:200])
cualquiera = con.execute("SELECT id FROM premios_evaluacion WHERE periodo=? LIMIT 1", (P,)).fetchone()["id"]
r = tildar(cualquiera, True)
chequear("sin valor cargado no deja tildar (400)", r.status_code == 400, f"{r.status_code} {r.text[:120]}")

VALOR = 5000
cli.put("/api/premios/trapos-valor", json={"valor": VALOR, "periodo": P})

# A: cobra bastante más que el valor
A = con.execute("""SELECT id, valor_calculado FROM premios_evaluacion
                   WHERE periodo=? AND valor_calculado > ? AND anular_premio=0
                   ORDER BY valor_calculado DESC LIMIT 1""", (P, VALOR * 2)).fetchone()
# B: otro con premio, que no se tilda
B = con.execute("""SELECT id, valor_calculado FROM premios_evaluacion
                   WHERE periodo=? AND valor_calculado > 0 AND id != ? LIMIT 1""", (P, A["id"])).fetchone()
# C: cobra $0 (descalificado o similar)
C = con.execute("""SELECT id FROM premios_evaluacion
                   WHERE periodo=? AND valor_calculado = 0 LIMIT 1""", (P,)).fetchone()

base_A = A["valor_calculado"]
r = tildar(A["id"], True)
chequear("tildar A responde 200", r.status_code == 200, r.text[:200])
fa = fila(A["id"])
chequear("A tildado guarda el valor", fa["deduccion_trapos"] == VALOR, fa["deduccion_trapos"])
chequear("A cobra el valor menos trapos", fa["valor_calculado"] == base_A - VALOR,
         f"{base_A} -> {fa['valor_calculado']}")
chequear("B sin tildar no descuenta", fila(B["id"])["deduccion_trapos"] == 0)

# El valor más grande que el premio: nunca negativo
grande = base_A + 100000
cli.put("/api/premios/trapos-valor", json={"valor": grande, "periodo": P})
fa = fila(A["id"])
chequear("cambiar valor actualiza al tildado", fa["deduccion_trapos"] == grande, fa["deduccion_trapos"])
chequear("valor mayor al premio deja $0, no negativo",
         fa["valor_calculado"] == 0 and fa["valor_final"] == 0, f"{fa['valor_calculado']} / {fa['valor_final']}")
chequear("B sigue sin descuento tras cambiar valor", fila(B["id"])["deduccion_trapos"] == 0)
cli.put("/api/premios/trapos-valor", json={"valor": VALOR, "periodo": P})

if C:
    r = tildar(C["id"], True)
    fc = fila(C["id"])
    chequear("tildar a quien cobra $0 lo deja en $0",
             r.status_code == 200 and fc["valor_final"] == 0, f"{r.status_code} {fc['valor_final']}")
    tildar(C["id"], False)

# Generar y recalcular no tocan lo tildado
cli.post(f"/api/premios/{P}/generar")
fa = fila(A["id"])
chequear("generar conserva el tildado", fa["deduccion_trapos"] == VALOR, fa["deduccion_trapos"])
chequear("generar conserva el descuento en el valor",
         fa["valor_calculado"] == base_A - VALOR, f"{fa['valor_calculado']}")
cli.post(f"/api/premios/{P}/recalcular")
chequear("recalcular conserva el tildado", fila(A["id"])["deduccion_trapos"] == VALOR)

# Una corrección de otro empleado no cambia el descuento de A (ya no hay reparto)
cli.put(f"/api/premios/evaluacion/{B['id']}", json={"anular_premio": True})
chequear("anular a otro no cambia el descuento de A", fila(A["id"])["deduccion_trapos"] == VALOR)
cli.put(f"/api/premios/evaluacion/{B['id']}", json={"anular_premio": False})

# Destildar
tildar(A["id"], False)
fa = fila(A["id"])
chequear("destildar vuelve a 0", fa["deduccion_trapos"] == 0 and fa["valor_calculado"] == base_A,
         f"{fa['deduccion_trapos']} / {fa['valor_calculado']}")

# Período cerrado: nada cambia
cerrado = con.execute("""
    SELECT pe.periodo FROM premios_evaluacion pe
    JOIN premios_periodos_cerrados c ON printf('%04d-%02d', c.anio, c.mes) = pe.periodo
    GROUP BY pe.periodo ORDER BY pe.periodo DESC LIMIT 1""").fetchone()
if cerrado:
    PC = cerrado["periodo"]
    antes = con.execute("""SELECT id, deduccion_trapos, valor_final FROM premios_evaluacion
                           WHERE periodo=? ORDER BY id""", (PC,)).fetchall()
    r = cli.put("/api/premios/trapos-valor", json={"valor": 777, "periodo": PC})
    chequear("cambiar valor con período cerrado responde 200", r.status_code == 200)
    chequear("cambiar valor no actualiza filas del cerrado", r.json().get("actualizados") == 0, r.text[:120])
    despues = con.execute("""SELECT id, deduccion_trapos, valor_final FROM premios_evaluacion
                             WHERE periodo=? ORDER BY id""", (PC,)).fetchall()
    chequear(f"período cerrado {PC} queda igual",
             [tuple(x) for x in antes] == [tuple(x) for x in despues])
    r = tildar(antes[0]["id"], True)
    chequear("tildar en período cerrado es rechazado (409)", r.status_code == 409, r.status_code)
else:
    print("  (sin período cerrado en esta base: se saltea esa parte)")

print(f"\n{ok} pasaron, {fallos} fallaron")
sys.exit(1 if fallos else 0)
