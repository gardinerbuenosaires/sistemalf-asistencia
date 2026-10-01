"""Pestaña Actualización: quién está conectado y quién puede actualizar.

Lo que se fija acá:
- Un usuario que hace algo figura como recién activo.
- Uno que solo tiene la página abierta (el ping de cada minuto, con X-Poll)
  figura con la hora de su última acción real, no la del ping.
- Uno con la sesión vencida por inactividad no figura.
- El que consulta figura marcado como "soy yo".
- Sin actualizacion:procesar no se ve ni se actualiza; fuera del servicio de
  Windows no se puede actualizar (nadie reabriría el sistema).
"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _base import preparar, token_sistema

DB = preparar()

import sqlite3
from fastapi.testclient import TestClient
from auth.core import create_token, invalidar_cache
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
con.execute("UPDATE configuracion SET valor='10' WHERE clave='inactividad_minutos'")
rid = con.execute("INSERT INTO roles (nombre, descripcion, nivel) VALUES ('PruebaSinAct','',1)").lastrowid
con.execute("INSERT INTO permisos (rol_id, modulo, accion) VALUES (?,'usuarios','ver')", (rid,))
ids = []
for nombre in ("Prueba Activo", "Prueba Ocioso", "Prueba Vencido"):
    ids.append(con.execute(
        "INSERT INTO usuarios (nombre, email, password_hash, rol_id, activo) VALUES (?,?,'x',?,1)",
        (nombre, nombre.replace(" ", ".").lower() + "@prueba", rid)).lastrowid)
con.commit()
con.close()
invalidar_cache()

activo, ocioso, vencido = ids
ahora = time.time()


def token(uid, hace_seg):
    # create_token pone "la" (última actividad) = ahora; se rearma con la deseada.
    from jose import jwt
    from auth.core import decode_token, SECRET_KEY, ALGORITHM
    p = decode_token(create_token(uid, "x", rid, "PruebaSinAct"))
    return jwt.encode({**p, "la": ahora - hace_seg}, SECRET_KEY, algorithm=ALGORITHM)


def pedido(tok, poll=False):
    c = TestClient(main.app)
    c.cookies.set("session", tok)
    return c.get("/api/ping", headers={"X-Poll": "1"} if poll else {})


pedido(token(activo, 0))                       # hace algo
pedido(token(ocioso, 7 * 60), poll=True)       # página abierta, 7 min sin tocar nada
r = pedido(token(vencido, 11 * 60), poll=True) # pasó el límite de 10 min
chequear("la sesión vencida es rechazada", r.status_code == 401, r.status_code)

adm = TestClient(main.app)
tok_sis, _ = token_sistema()
adm.cookies.set("session", tok_sis)
d = adm.get("/api/actualizacion/estado", params={"buscar": 0}).json()
por_id = {c["usuario_id"]: c for c in d["conectados"]}

chequear("el que trabaja figura recién activo", activo in por_id and por_id[activo]["inactivo_seg"] < 30,
         por_id.get(activo))
chequear("la página abierta figura con su última acción (7 min), no con el ping",
         ocioso in por_id and 400 <= por_id[ocioso]["inactivo_seg"] <= 440, por_id.get(ocioso))
chequear("la sesión vencida no figura", vencido not in por_id, por_id.get(vencido))
chequear("el que consulta figura como 'soy yo'", any(c["soy_yo"] for c in d["conectados"]), d["conectados"])
chequear("trae los nombres", por_id.get(activo, {}).get("nombre") == "Prueba Activo", por_id.get(activo))
chequear("el más activo primero",
         [c["inactivo_seg"] for c in d["conectados"]] == sorted(c["inactivo_seg"] for c in d["conectados"]))

sin = TestClient(main.app)
sin.cookies.set("session", token(activo, 0))
chequear("sin el permiso no ve el estado", sin.get("/api/actualizacion/estado").status_code == 403)
chequear("sin el permiso no actualiza", sin.post("/api/actualizacion/actualizar").status_code == 403)
os.environ.pop("ACTUALIZACION_FORZAR", None)
r = adm.post("/api/actualizacion/actualizar")
chequear("fuera del servicio de Windows no se actualiza", r.status_code == 409, r.text)

print(f"\n{'=' * 52}\n  {ok} pasaron, {fallos} fallaron\n{'=' * 52}")
raise SystemExit(1 if fallos else 0)
