"""Prueba de punta a punta del ABM de dispositivos.

Cubre la migración que pasa el lector de `configuracion` a la tabla, las reglas
que tienen que fallar, y la que más importa: que no se pueda dejar al sistema
sin ningún equipo que alimente la asistencia.

No prueba la conexión real a un lector. La copia tiene la IP en 127.0.0.1
justamente para que nada salga a la red; lo único que se verifica de `probar`
es que un equipo push lo rechace sin intentar conectarse.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _base import preparar, token_sistema
DB = preparar()   # una COPIA de la base: la real nunca se toca

import sqlite3
from fastapi.testclient import TestClient
import main
from auth.core import ensure_admin

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


print("\n=== MIGRACION: el lector ya configurado pasa a la tabla ===")
r = cli.get("/api/dispositivos")
chequear("GET responde 200", r.status_code == 200, r.text[:120])
equipos = r.json()
chequear("hay exactamente un equipo sembrado", len(equipos) == 1, equipos)
maestro = equipos[0]
chequear("es pull", maestro["protocolo"] == "pull", maestro)
chequear("alimenta la asistencia", maestro["cuenta_asistencia"] == 1, maestro)
chequear("no esta marcado como de acceso", maestro["es_acceso"] == 0, maestro)
chequear("tomo la IP neutralizada de la copia", maestro["ip"] == "127.0.0.1", maestro)
chequear("arranca sin identidad, se completa al probar",
         maestro["numero_serie"] is None and maestro["algoritmo_huella"] is None, maestro)

print("\n=== EL DESCARGADOR LEE DE LA TABLA ===")
from sync.downloader import _get_device_config
cfg = _get_device_config()
chequear("_get_device_config sale de la tabla", cfg["ip"] == "127.0.0.1", cfg)

cli.put(f"/api/dispositivos/{maestro['id']}",
        json={**{k: maestro[k] for k in ("nombre", "protocolo", "ip", "puerto", "password",
                                         "timeout", "cuenta_asistencia", "es_acceso",
                                         "activo", "orden")},
              "puerto": 4371})
chequear("un cambio en la tabla se ve enseguida", _get_device_config()["port"] == 4371,
         _get_device_config())
cli.put(f"/api/dispositivos/{maestro['id']}",
        json={**{k: maestro[k] for k in ("nombre", "protocolo", "ip", "password", "timeout",
                                         "cuenta_asistencia", "es_acceso", "activo", "orden")},
              "puerto": 4370})


print("\n=== ALTA DE UN LECTOR DE PUERTA ===")
r = cli.post("/api/dispositivos", json={
    "nombre": "Puerta deposito", "ubicacion": "Deposito", "protocolo": "pull",
    "ip": "127.0.0.2", "puerto": 4370, "password": 0, "timeout": 10,
    "cuenta_asistencia": False, "es_acceso": True, "activo": True, "orden": 1,
})
chequear("POST crea el equipo", r.status_code == 201, r.text[:160])
puerta = r.json() if r.status_code == 201 else {}
chequear("no alimenta la asistencia", puerta.get("cuenta_asistencia") == 0, puerta)
chequear("queda marcado como de acceso", puerta.get("es_acceso") == 1, puerta)

r = cli.post("/api/dispositivos", json={
    "nombre": "Repetido", "protocolo": "pull", "ip": "127.0.0.2", "puerto": 4370,
})
chequear("no deja repetir IP y puerto", r.status_code == 409, r.text[:120])

r = cli.post("/api/dispositivos", json={"nombre": "Sin IP", "protocolo": "pull"})
chequear("un pull sin IP se rechaza", r.status_code == 400, r.text[:160])

r = cli.post("/api/dispositivos", json={"nombre": "Push sin serie", "protocolo": "push"})
chequear("un push sin numero de serie se rechaza", r.status_code == 400, r.text[:160])

r = cli.post("/api/dispositivos", json={
    "nombre": "Puerta nueva", "protocolo": "push", "numero_serie": "SN-NUEVO-1",
    "es_acceso": True,
})
chequear("un push con numero de serie se acepta sin IP", r.status_code == 201, r.text[:160])
push_id = r.json()["id"] if r.status_code == 201 else None

r = cli.post("/api/dispositivos", json={"nombre": "  ", "protocolo": "pull", "ip": "127.0.0.9"})
chequear("nombre vacio se rechaza", r.status_code == 422, r.text[:120])

r = cli.post("/api/dispositivos", json={
    "nombre": "Puerto raro", "protocolo": "pull", "ip": "127.0.0.9", "puerto": 99999})
chequear("puerto fuera de rango se rechaza", r.status_code == 422, r.text[:120])


print("\n=== NO QUEDARSE SIN EQUIPO DE ASISTENCIA ===")
r = cli.delete(f"/api/dispositivos/{maestro['id']}")
chequear("no deja borrar el unico equipo de asistencia", r.status_code == 409, r.text[:200])

r = cli.put(f"/api/dispositivos/{maestro['id']}", json={
    "nombre": maestro["nombre"], "protocolo": "pull", "ip": maestro["ip"],
    "puerto": 4370, "password": 0, "timeout": 10,
    "cuenta_asistencia": False, "es_acceso": False, "activo": True, "orden": 0,
})
chequear("tampoco deja desmarcarlo", r.status_code == 409, r.text[:200])

r = cli.put(f"/api/dispositivos/{maestro['id']}", json={
    "nombre": maestro["nombre"], "protocolo": "pull", "ip": maestro["ip"],
    "puerto": 4370, "password": 0, "timeout": 10,
    "cuenta_asistencia": True, "es_acceso": False, "activo": False, "orden": 0,
})
chequear("tampoco deja desactivarlo", r.status_code == 409, r.text[:200])

# Con un segundo equipo de asistencia, el primero ya se puede sacar.
r = cli.post("/api/dispositivos", json={
    "nombre": "Maestro de repuesto", "protocolo": "pull", "ip": "127.0.0.3",
    "puerto": 4370, "password": 0, "timeout": 10,
    "cuenta_asistencia": True, "es_acceso": False, "activo": True, "orden": 2,
})
chequear("se puede dar de alta un segundo equipo de asistencia", r.status_code == 201, r.text[:160])
repuesto = r.json() if r.status_code == 201 else {}

r = cli.delete(f"/api/dispositivos/{maestro['id']}")
chequear("con dos, ya deja borrar el primero", r.status_code == 200, r.text[:200])
chequear("el descargador pasa al que queda", _get_device_config()["ip"] == "127.0.0.3",
         _get_device_config())


print("\n=== PROBAR CONEXION ===")
if push_id:
    r = cli.post(f"/api/dispositivos/{push_id}/probar")
    chequear("a un push no se lo prueba desde aca", r.status_code == 400, r.text[:200])

r = cli.post("/api/dispositivos/999999/probar")
chequear("probar un id inexistente da 404", r.status_code == 404, r.text[:120])

r = cli.delete("/api/dispositivos/999999")
chequear("borrar un id inexistente da 404", r.status_code == 404, r.text[:120])


print("\n=== PERMISOS ===")
me = cli.get("/api/auth/me").json()
chequear("sistema tiene los tres permisos de dispositivos",
         sorted(p["accion"] for p in me["permisos"] if p["modulo"] == "dispositivos")
         == ["editar", "eliminar", "ver"],
         sorted(p["accion"] for p in me["permisos"] if p["modulo"] == "dispositivos"))

con = sqlite3.connect(DB)
sin_sesion = TestClient(main.app)
r = sin_sesion.get("/api/dispositivos")
chequear("sin sesion no se listan los equipos", r.status_code in (401, 403), r.status_code)
con.close()


print("\n=== PADRON: comparacion contra los empleados ===")
from sync.lectores import comparar_con_empleados

EMPLEADOS = {
    "10": {"id": 1, "user_id": "10", "nombre": "Juan",  "apellido": "Perez",
           "activo": 1, "tipo": "normal", "fecha_egreso": None},
    "11": {"id": 2, "user_id": "11", "nombre": "Ana",   "apellido": "Gomez Ruiz",
           "activo": 1, "tipo": "normal", "fecha_egreso": None},
    "12": {"id": 3, "user_id": "12", "nombre": "Luis",  "apellido": "Torres",
           "activo": 0, "tipo": "normal", "fecha_egreso": "2026-07-29"},
    "13": {"id": 4, "user_id": "13", "nombre": "Mario", "apellido": "Nuevo",
           "activo": 1, "tipo": "normal", "fecha_egreso": None},
}
# El lector corta los nombres a 8: "Gomez Ru" es Gomez Ruiz, no otra persona.
# En el 13 hay cargado alguien distinto: numero reutilizado.
PADRON = [
    {"uid": 1, "user_id": "10", "nombre": "Perez",    "privilegio": 0, "tarjeta": 0, "grupo": "1"},
    {"uid": 2, "user_id": "11", "nombre": "Gomez Ru", "privilegio": 0, "tarjeta": 0, "grupo": "1"},
    {"uid": 3, "user_id": "12", "nombre": "Torres",   "privilegio": 0, "tarjeta": 0, "grupo": "1"},
    {"uid": 4, "user_id": "99", "nombre": "Fantasma", "privilegio": 0, "tarjeta": 0, "grupo": "0"},
    {"uid": 5, "user_id": "13", "nombre": "VIEJO",    "privilegio": 0, "tarjeta": 0, "grupo": "1"},
]
res = comparar_con_empleados(PADRON, EMPLEADOS)
por_id = {f["user_id"]: f for f in res["filas"]}

chequear("cuenta el total del lector", res["resumen"]["total"] == 5, res["resumen"])
chequear("detecta al dado de baja", por_id["12"]["estado"] == "de_baja", por_id["12"])
chequear("trae la fecha de egreso del dado de baja",
         por_id["12"]["empleado"]["fecha_egreso"] == "2026-07-29", por_id["12"])
chequear("detecta el numero que no existe en el sistema",
         por_id["99"]["estado"] == "desconocido", por_id["99"])
chequear("el que esta bien queda en ok", por_id["10"]["estado"] == "ok", por_id["10"])
chequear("un nombre cortado a 8 NO se marca como distinto",
         por_id["11"]["nombre_distinto"] is False, por_id["11"])
chequear("un nombre realmente distinto SI se marca",
         por_id["13"]["nombre_distinto"] is True, por_id["13"])
chequear("resumen: una baja y un desconocido",
         (res["resumen"]["de_baja"], res["resumen"]["desconocidos"]) == (1, 1), res["resumen"])
chequear("las bajas se muestran primero", res["filas"][0]["estado"] == "de_baja",
         [f["estado"] for f in res["filas"]])
chequear("un padron vacio no rompe",
         comparar_con_empleados([], EMPLEADOS)["resumen"]["total"] == 0)

print("\n=== PADRON: endpoint ===")
import sync.lectores as lectores

_real = lectores.leer_padron
lectores.leer_padron = lambda d: {"ok": True, "transporte": "udp",
                                  "usuarios": PADRON, "error": None}
r = cli.get(f"/api/dispositivos/{puerta['id']}/padron")
chequear("GET padron responde 200", r.status_code == 200, r.text[:160])
cuerpo = r.json() if r.status_code == 200 else {}
chequear("informa por que transporte contesto", cuerpo.get("transporte") == "udp", cuerpo)
chequear("devuelve resumen y filas", "resumen" in cuerpo and "filas" in cuerpo, list(cuerpo))
chequear("deja constancia de que el equipo contesto",
         any(x["visto_en"] for x in cli.get("/api/dispositivos").json()
             if x["id"] == puerta["id"]))

lectores.leer_padron = lambda d: {"ok": False, "transporte": None, "usuarios": [],
                                  "error": "ZKNetworkError: timed out"}
r = cli.get(f"/api/dispositivos/{puerta['id']}/padron")
chequear("un equipo que no contesta devuelve ok=false, no un error 500",
         r.status_code == 200 and r.json()["ok"] is False, r.text[:160])
lectores.leer_padron = _real

if push_id:
    r = cli.get(f"/api/dispositivos/{push_id}/padron")
    chequear("a un push le avisa que no se puede consultar asi",
             r.status_code == 200 and r.json()["ok"] is False, r.text[:200])

r = cli.get("/api/dispositivos/999999/padron")
chequear("padron de un id inexistente da 404", r.status_code == 404, r.text[:120])


print(f"\n{'='*52}\n  {ok} pasaron, {fallos} fallaron\n{'='*52}")
raise SystemExit(1 if fallos else 0)
