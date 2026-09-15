"""Los permisos por defecto se aplican una sola vez por rol (ensure_admin).

Lo que se saca desde la pantalla de Roles no vuelve al reiniciar; Sistema nunca
pierde nada; un módulo nuevo sigue llegando solo a los roles estándar.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _base import preparar, token_sistema

DB = preparar(migrar=False)

import sqlite3

# Se simula el primer arranque con el arreglo: sin registro de lo aplicado.
c = sqlite3.connect(DB)
c.execute("DROP TABLE IF EXISTS permisos_default_aplicados")
c.commit()
c.close()

from db.database import init_db
import auth.core as core
from auth.core import ensure_admin, invalidar_cache, PERMISOS_DEFAULT

ok = fallos = 0


def chequear(desc, cond, extra=""):
    global ok, fallos
    if cond:
        ok += 1
        print(f"  OK   {desc}")
    else:
        fallos += 1
        print(f"  FALLA {desc}  {extra}")


def con():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def permisos():
    c = con()
    s = {(r["rol_id"], r["modulo"], r["accion"]) for r in c.execute("SELECT rol_id, modulo, accion FROM permisos")}
    c.close()
    return s


def rol_id(nombre):
    c = con()
    r = c.execute("SELECT id FROM roles WHERE lower(nombre)=lower(?)", (nombre,)).fetchone()
    c.close()
    return r["id"] if r else None


def tiene(rid, modulo, accion):
    return (rid, modulo, accion) in permisos()


init_db()   # lo que corre el arranque antes de ensure_admin()
antes = permisos()
c = con()
nombres_default = {n.lower() for n in PERMISOS_DEFAULT}
roles_propios = {r["id"] for r in c.execute("SELECT id, nombre FROM roles")
                 if r["nombre"].lower() not in nombres_default}
c.close()
propios_antes = {p for p in antes if p[0] in roles_propios}
defaults = set()
for rn, mods in PERMISOS_DEFAULT.items():
    rid = rol_id(rn)
    if rid:
        defaults |= {(rid, m, a) for m, acs in mods.items() for a in acs}

print("\n=== TRANSICIÓN: el primer arranque con el arreglo ===")
ensure_admin()
despues = permisos()
chequear("se comporta como antes una última vez (agrega solo los default que faltaran)",
         despues == antes | defaults, f"{len(despues ^ (antes | defaults))} diferencias")
c = con()
reg = c.execute("SELECT COUNT(*) FROM permisos_default_aplicados").fetchone()[0]
c.close()
chequear("quedan registrados todos los default aplicados", reg == len(defaults), f"{reg} vs {len(defaults)}")
ensure_admin()
chequear("un segundo arranque no cambia nada", permisos() == despues)

rrhh = rol_id("rrhh")
_, sis = token_sistema()

print("\n=== SACAR UN PERMISO DEFAULT Y REINICIAR ===")
c = con()
c.execute("DELETE FROM permisos WHERE rol_id=? AND modulo='asistencia' AND accion='fichaje_manual'", (rrhh,))
c.commit()
c.close()
ensure_admin()
chequear("RRHH sin asistencia:fichaje_manual: después de reiniciar sigue sin él",
         not tiene(rrhh, "asistencia", "fichaje_manual"))

print("\n=== EL CAMINO REAL: GUARDAR DESDE LA PANTALLA DE ROLES ===")
from fastapi.testclient import TestClient
import main
cli = TestClient(main.app, raise_server_exceptions=False)
cli.cookies.set("session", token_sistema()[0])
actuales = [{"modulo": m, "accion": a} for (rid, m, a) in permisos() if rid == rrhh]
sin_reabrir = [p for p in actuales if not (p["modulo"] == "periodos" and p["accion"] == "reabrir")]
r = cli.put(f"/api/roles/{rrhh}/permisos", json={"permisos": sin_reabrir})
chequear("guardar RRHH sin periodos:reabrir desde Roles -> 200", r.status_code == 200, r.status_code)
chequear("se guardó sin ese permiso", not tiene(rrhh, "periodos", "reabrir"))
init_db()
ensure_admin()
invalidar_cache()
chequear("después de reiniciar, sigue sin ese permiso", not tiene(rrhh, "periodos", "reabrir"))
chequear("y conserva el resto de lo que se guardó",
         len([p for p in permisos() if p[0] == rrhh]) == len(sin_reabrir))

print("\n=== SISTEMA NUNCA PIERDE NADA ===")
c = con()
c.execute("DELETE FROM permisos WHERE rol_id=? AND modulo='roles' AND accion='editar'", (sis,))
c.commit()
c.close()
ensure_admin()
chequear("a Sistema le vuelve roles:editar al reiniciar (nadie queda afuera)", tiene(sis, "roles", "editar"))

print("\n=== UN MÓDULO NUEVO SIGUE LLEGANDO SOLO ===")
core.MODULO_ACCIONES["zzmodulo"] = ["ver"]
core.PERMISOS_DEFAULT["rrhh"]["zzmodulo"] = ["ver"]
ensure_admin()
chequear("un default nuevo le llega a RRHH en el arranque siguiente", tiene(rrhh, "zzmodulo", "ver"))
c = con()
c.execute("DELETE FROM permisos WHERE rol_id=? AND modulo='zzmodulo'", (rrhh,))
c.commit()
c.close()
ensure_admin()
chequear("y si después se lo sacan, no vuelve", not tiene(rrhh, "zzmodulo", "ver"))
del core.PERMISOS_DEFAULT["rrhh"]["zzmodulo"]
del core.MODULO_ACCIONES["zzmodulo"]

print("\n=== LOS ROLES PROPIOS NO SE TOCAN ===")
chequear(f"los {len(roles_propios)} roles creados a mano tienen exactamente lo mismo",
         {p for p in permisos() if p[0] in roles_propios} == propios_antes)

print(f"\n{'=' * 52}\n  {ok} pasaron, {fallos} fallaron\n{'=' * 52}")
raise SystemExit(1 if fallos else 0)
