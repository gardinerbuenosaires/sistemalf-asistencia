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



print("\n=== REVISION DE TODOS LOS LECTORES ===")
import sync.lectores as lectores

# Un equipo contesta con problemas, el otro esta caido: el caido no tiene que
# impedir que se vea lo del primero.
_real2 = lectores.leer_padron


# El endpoint cruza contra los empleados REALES de la copia, no contra la lista
# de arriba. Para que haya al menos una fila sin novedad hace falta alguien que
# exista de verdad, con su apellido tal cual esta en el legajo.
_con = sqlite3.connect(DB)
_real_emp = _con.execute(
    """SELECT user_id, apellido FROM empleados
        WHERE activo = 1 AND user_id IS NOT NULL AND apellido <> '' LIMIT 1"""
).fetchone()
_con.close()
PADRON_REV = list(PADRON)
if _real_emp:
    PADRON_REV.append({"uid": 9, "user_id": str(_real_emp[0]).strip(),
                       "nombre": str(_real_emp[1]).strip()[:8],
                       "privilegio": 0, "tarjeta": 0, "grupo": "1"})


def _falso(d):
    if d["ip"] == "127.0.0.2":   # la Puerta deposito que se creo mas arriba
        return {"ok": True, "transporte": "udp", "usuarios": PADRON_REV, "error": None}
    return {"ok": False, "transporte": None, "usuarios": [],
            "error": "ZKNetworkError: timed out"}


lectores.leer_padron = _falso
r = cli.get("/api/dispositivos/revision/todos")
chequear("GET revision responde 200", r.status_code == 200, r.text[:160])
rev = r.json() if r.status_code == 200 else {}

chequear("revisa mas de un equipo", rev["total"]["equipos"] >= 2, rev.get("total"))
chequear("cuenta el que no contesto", rev["total"]["sin_responder"] >= 1, rev["total"])
chequear("suma los dados de baja de todos", rev["total"]["de_baja"] == 1, rev["total"])
chequear("suma los desconocidos de todos", rev["total"]["desconocidos"] == 1, rev["total"])

con_datos = [e for e in rev["equipos"] if e["ok"]]
chequear("el equipo caido no impide ver el que si contesto", len(con_datos) >= 1,
         [(e["nombre"], e["ok"]) for e in rev["equipos"]])

eq = con_datos[0]
chequear("solo devuelve lo que hay que mirar, no el padron entero",
         not _real_emp or len(eq["problemas"]) < eq["resumen"]["total"],
         (len(eq["problemas"]), eq["resumen"]["total"]))
chequear("ninguna fila sin novedad se cuela en problemas",
         all(f["estado"] != "ok" or f["nombre_distinto"] for f in eq["problemas"]),
         [f["estado"] for f in eq["problemas"]])

caidos = [e for e in rev["equipos"] if not e["ok"]]
chequear("el equipo caido informa el motivo", caidos and caidos[0]["error"], caidos[:1])

# Un equipo push no atiende llamadas: no tiene sentido incluirlo en la revision.
ids_revisados = {e["id"] for e in rev["equipos"]}
chequear("no intenta revisar equipos push", push_id not in ids_revisados, ids_revisados)

lectores.leer_padron = _real2

print("\n=== LECTURA EN PARALELO ===")
import time
from sync.lectores import leer_padrones

_lento = lambda d: (time.sleep(0.4), {"ok": True, "transporte": "tcp",
                                      "usuarios": [], "error": None})[1]
lectores.leer_padron = _lento
equipos = [{"id": i, "ip": f"127.0.0.{i}", "protocolo": "pull"} for i in range(1, 6)]
arranque = time.time()
res_par = leer_padrones(equipos)
tardanza = time.time() - arranque
chequear("devuelve un resultado por equipo", len(res_par) == 5, len(res_par))
chequear("los lee en paralelo y no de a uno",
         tardanza < 1.2, f"tardo {tardanza:.2f}s; de a uno serian 2s")
chequear("una lista vacia no rompe", leer_padrones([]) == {})
lectores.leer_padron = _real2



print("\n=== PERFILES DE ACCESO ===")

# Tres puertas para armar los perfiles del ejemplo del usuario.
_ids_puertas = []
for n, ip in [("Personal", "127.0.1.1"), ("Oficina", "127.0.1.2"), ("Camaras", "127.0.1.3")]:
    _r = cli.post("/api/dispositivos", json={
        "nombre": n, "protocolo": "pull", "ip": ip, "puerto": 4370,
        "cuenta_asistencia": False, "es_acceso": True, "activo": True,
    })
    _ids_puertas.append(_r.json()["id"])
p_personal, p_oficina, p_camaras = _ids_puertas

r = cli.get("/api/perfiles-acceso")
chequear("GET perfiles responde 200", r.status_code == 200, r.text[:160])
inicial = r.json()
chequear("arranca sin perfiles", inicial["perfiles"] == [], inicial["perfiles"])
chequear("ofrece como columnas solo las puertas",
         {x["id"] for x in inicial["puertas"]} >= set(_ids_puertas),
         [x["nombre"] for x in inicial["puertas"]])
chequear("el lector de asistencia no aparece como puerta",
         all(x["nombre"] != "Maestro de repuesto" for x in inicial["puertas"]),
         [x["nombre"] for x in inicial["puertas"]])

r = cli.post("/api/perfiles-acceso", json={
    "nombre": "Todas las puertas", "dispositivos": _ids_puertas})
chequear("crea un perfil con todas las puertas", r.status_code == 201, r.text[:160])
todas = r.json() if r.status_code == 201 else {}
chequear("guarda las tres puertas", sorted(todas.get("dispositivos", [])) == sorted(_ids_puertas),
         todas.get("dispositivos"))

r = cli.post("/api/perfiles-acceso", json={
    "nombre": "Todas menos oficina", "dispositivos": [p_personal, p_camaras]})
chequear("crea el perfil con exclusion", r.status_code == 201, r.text[:160])
menos_oficina = r.json() if r.status_code == 201 else {}
chequear("no incluye oficina", p_oficina not in menos_oficina.get("dispositivos", []),
         menos_oficina.get("dispositivos"))

r = cli.post("/api/perfiles-acceso", json={"nombre": "Solo oficina", "dispositivos": [p_oficina]})
solo_oficina = r.json() if r.status_code == 201 else {}
chequear("crea el perfil de una sola puerta", r.status_code == 201, r.text[:160])

r = cli.post("/api/perfiles-acceso", json={"nombre": "Todas las puertas", "dispositivos": []})
chequear("no deja repetir el nombre", r.status_code == 409, r.text[:120])

r = cli.post("/api/perfiles-acceso", json={"nombre": "  ", "dispositivos": []})
chequear("nombre vacio se rechaza", r.status_code == 422, r.text[:120])

r = cli.post("/api/perfiles-acceso", json={"nombre": "Fantasma", "dispositivos": [999999]})
chequear("no deja apuntar a una puerta que no existe", r.status_code == 400, r.text[:160])

# Un perfil sin ninguna puerta es valido: alguien que no abre nada.
r = cli.post("/api/perfiles-acceso", json={"nombre": "Sin acceso", "dispositivos": []})
chequear("un perfil sin puertas es valido", r.status_code == 201, r.text[:160])
sin_acceso = r.json() if r.status_code == 201 else {}

print("\n=== EDITAR LA MATRIZ ===")
r = cli.put(f"/api/perfiles-acceso/{solo_oficina['id']}", json={
    "nombre": "Solo oficina", "activo": True, "orden": 0,
    "dispositivos": [p_oficina, p_camaras]})
chequear("agregar una puerta al perfil", r.status_code == 200
         and sorted(r.json()["dispositivos"]) == sorted([p_oficina, p_camaras]), r.text[:160])

r = cli.put(f"/api/perfiles-acceso/{solo_oficina['id']}", json={
    "nombre": "Solo oficina", "activo": True, "orden": 0, "dispositivos": [p_oficina]})
chequear("quitar una puerta del perfil",
         r.status_code == 200 and r.json()["dispositivos"] == [p_oficina], r.text[:160])

print("\n=== UNA PUERTA NUEVA NO ENTRA SOLA EN NINGUN PERFIL ===")
r = cli.post("/api/dispositivos", json={
    "nombre": "Deposito nuevo", "protocolo": "pull", "ip": "127.0.1.9", "puerto": 4370,
    "cuenta_asistencia": False, "es_acceso": True, "activo": True})
nueva = r.json()["id"]
d = cli.get("/api/perfiles-acceso").json()
chequear("aparece como columna nueva en la matriz",
         any(x["id"] == nueva for x in d["puertas"]), [x["nombre"] for x in d["puertas"]])
chequear("ningun perfil la incluye todavia",
         all(nueva not in pf["dispositivos"] for pf in d["perfiles"]),
         [(pf["nombre"], pf["dispositivos"]) for pf in d["perfiles"]])
chequear("ni siquiera el perfil llamado Todas las puertas",
         nueva not in next(pf["dispositivos"] for pf in d["perfiles"]
                           if pf["nombre"] == "Todas las puertas"))

print("\n=== BORRAR UN PERFIL EN USO ===")
_con2 = sqlite3.connect(DB)
_emp = _con2.execute(
    "SELECT id FROM empleados WHERE activo=1 AND user_id IS NOT NULL LIMIT 1").fetchone()
if _emp:
    _con2.execute("UPDATE empleados SET perfil_acceso_id=? WHERE id=?",
                  (todas["id"], _emp[0]))
    _con2.commit()
_con2.close()

r = cli.delete(f"/api/perfiles-acceso/{todas['id']}")
chequear("no deja borrar un perfil que alguien usa", r.status_code == 409, r.text[:200])

r = cli.delete(f"/api/perfiles-acceso/{sin_acceso['id']}")
chequear("si deja borrar uno que no usa nadie", r.status_code == 200, r.text[:160])

r = cli.delete("/api/perfiles-acceso/999999")
chequear("borrar un perfil inexistente da 404", r.status_code == 404, r.text[:120])

print("\n=== PERMISOS ===")
_sin = TestClient(main.app)
r = _sin.get("/api/perfiles-acceso")
chequear("sin sesion no se ven los perfiles", r.status_code in (401, 403), r.status_code)



print("\n=== ASIGNACION: perfil, cargo y excepciones ===")
_c3 = sqlite3.connect(DB)
_c3.row_factory = sqlite3.Row
_e = _c3.execute("""SELECT id, cargo_id FROM empleados
                     WHERE activo=1 AND cargo_id IS NOT NULL LIMIT 1""").fetchone()
_c3.close()
emp_id = _e["id"] if _e else None
cargo_id = _e["cargo_id"] if _e else None

if emp_id:
    r = cli.get(f"/api/accesos/empleado/{emp_id}")
    chequear("GET del acceso de una persona responde 200", r.status_code == 200, r.text[:160])

    # 1) Sin nada: no abre ninguna puerta, y eso es valido.
    cli.put(f"/api/accesos/empleado/{emp_id}/perfil", json={"perfil_acceso_id": None})
    cli.put(f"/api/accesos/cargo/{cargo_id}/perfil", json={"perfil_acceso_id": None})
    a = cli.get(f"/api/accesos/empleado/{emp_id}").json()
    chequear("sin perfil ni cargo no abre nada", a["puertas"] == [], a["puertas"])
    chequear("y aparece en la lista de los que no tienen acceso",
             any(x["id"] == emp_id for x in cli.get("/api/accesos/sin-perfil").json()))

    # 2) El cargo PROPONE, no aplica: la persona sigue sin abrir nada.
    r = cli.put(f"/api/accesos/cargo/{cargo_id}/perfil",
                json={"perfil_acceso_id": menos_oficina["id"]})
    chequear("se le puede poner perfil a un cargo", r.status_code == 200, r.text[:160])
    a = cli.get(f"/api/accesos/empleado/{emp_id}").json()
    chequear("el cargo NO le da acceso solo", a["perfil"] is None, a.get("perfil"))
    chequear("sigue sin abrir ninguna puerta", a["puertas"] == [], a["puertas"])
    chequear("pero aparece la sugerencia del cargo",
             a["sugerencia_del_cargo"]["id"] == menos_oficina["id"],
             a.get("sugerencia_del_cargo"))
    chequear("y sigue en la lista de pendientes de asignar",
             any(x["id"] == emp_id for x in cli.get("/api/accesos/sin-perfil").json()))
    _pend = next(x for x in cli.get("/api/accesos/sin-perfil").json() if x["id"] == emp_id)
    chequear("la lista de pendientes trae la sugerencia",
             _pend["sugerencia_id"] == menos_oficina["id"], _pend)

    # Aplicar el perfil del cargo a los que no tienen: lo decide alguien con permiso.
    r = cli.post(f"/api/accesos/cargo/{cargo_id}/aplicar")
    chequear("aplicar el perfil del cargo responde 200", r.status_code == 200, r.text[:160])
    chequear("y dice a cuantos alcanzo", r.json()["asignados"] >= 1, r.json())
    a = cli.get(f"/api/accesos/empleado/{emp_id}").json()
    chequear("recien ahi la persona tiene el perfil",
             a["perfil"]["id"] == menos_oficina["id"], a.get("perfil"))
    chequear("y abre las puertas del perfil",
             sorted(a["puertas"]) == sorted([p_personal, p_camaras]), a["puertas"])
    chequear("ya no figura entre los pendientes",
             all(x["id"] != emp_id for x in cli.get("/api/accesos/sin-perfil").json()))

    r = cli.post(f"/api/accesos/cargo/{cargo_id}/aplicar")
    chequear("aplicar de nuevo no toca a nadie mas", r.json()["asignados"] == 0, r.json())

    # 3) CAMBIAR EL CARGO NO CAMBIA EL ACCESO. Es lo que motivo este modelo:
    # el cargo lo edita quien tiene permiso de empleados, no de accesos.
    _c5 = sqlite3.connect(DB)
    _otro = _c5.execute("SELECT id FROM cargos WHERE id <> ? LIMIT 1", (cargo_id,)).fetchone()
    _c5.close()
    if _otro:
        cli.put(f"/api/accesos/cargo/{_otro[0]}/perfil",
                json={"perfil_acceso_id": solo_oficina["id"]})
        _c5 = sqlite3.connect(DB)
        _c5.execute("UPDATE empleados SET cargo_id=? WHERE id=?", (_otro[0], emp_id))
        _c5.commit(); _c5.close()
        a = cli.get(f"/api/accesos/empleado/{emp_id}").json()
        chequear("cambiarle el cargo NO le cambia el perfil",
                 a["perfil"]["id"] == menos_oficina["id"], a.get("perfil"))
        chequear("ni las puertas que abre",
                 sorted(a["puertas"]) == sorted([p_personal, p_camaras]), a["puertas"])
        chequear("y no aparece sugerencia porque ya tiene perfil propio",
                 a["sugerencia_del_cargo"] is None, a.get("sugerencia_del_cargo"))
        # Aplicar el perfil del cargo nuevo tampoco lo pisa.
        r = cli.post(f"/api/accesos/cargo/{_otro[0]}/aplicar")
        a = cli.get(f"/api/accesos/empleado/{emp_id}").json()
        chequear("aplicar en masa no pisa a quien ya tiene perfil propio",
                 a["perfil"]["id"] == menos_oficina["id"], a.get("perfil"))

    r = cli.post(f"/api/accesos/cargo/999999/aplicar")
    chequear("aplicar sobre un cargo inexistente da 404", r.status_code == 404, r.text[:120])

    # 4) Perfil propio: se elige a mano y manda.
    cli.put(f"/api/accesos/empleado/{emp_id}/perfil",
            json={"perfil_acceso_id": solo_oficina["id"]})
    a = cli.get(f"/api/accesos/empleado/{emp_id}").json()
    chequear("el perfil propio le gana al del cargo",
             a["perfil"]["id"] == solo_oficina["id"], a.get("perfil"))
    chequear("y no queda sugerencia pendiente", a["sugerencia_del_cargo"] is None, a)
    chequear("sus puertas son las del perfil propio", a["puertas"] == [p_oficina], a["puertas"])

    # 4) Excepcion: agregarle una puerta suelta.
    r = cli.post(f"/api/accesos/empleado/{emp_id}/excepcion",
                 json={"dispositivo_id": p_camaras, "modo": "agregar",
                       "motivo": "pedido del encargado"})
    chequear("se le puede agregar una puerta suelta", r.status_code == 201, r.text[:200])
    a = r.json() if r.status_code == 201 else {}
    chequear("la puerta agregada entra en la lista final",
             sorted(a.get("puertas", [])) == sorted([p_oficina, p_camaras]), a.get("puertas"))
    chequear("el perfil NO se modifico",
             a["puertas_del_perfil"] == [p_oficina], a.get("puertas_del_perfil"))
    chequear("la excepcion guarda el motivo",
             a["excepciones"][0]["motivo"] == "pedido del encargado", a.get("excepciones"))

    # 5) Excepcion: quitarle una que el perfil si le da.
    r = cli.post(f"/api/accesos/empleado/{emp_id}/excepcion",
                 json={"dispositivo_id": p_oficina, "modo": "quitar", "motivo": "por seguridad"})
    chequear("se le puede quitar una puerta del perfil", r.status_code == 201, r.text[:200])
    a = r.json() if r.status_code == 201 else {}
    chequear("la puerta quitada sale de la lista final",
             a.get("puertas") == [p_camaras], a.get("puertas"))

    # 6) Excepciones que no cambian nada: se rechazan.
    r = cli.post(f"/api/accesos/empleado/{emp_id}/excepcion",
                 json={"dispositivo_id": p_personal, "modo": "quitar"})
    chequear("quitar algo que el perfil no da se rechaza", r.status_code == 409, r.text[:200])

    cli.delete(f"/api/accesos/empleado/{emp_id}/excepcion/{p_camaras}")
    r = cli.post(f"/api/accesos/empleado/{emp_id}/excepcion",
                 json={"dispositivo_id": p_oficina, "modo": "agregar"})
    chequear("agregar algo que el perfil ya da se rechaza", r.status_code == 409, r.text[:200])

    # 7) Sacar la excepcion devuelve a la persona a su perfil.
    r = cli.delete(f"/api/accesos/empleado/{emp_id}/excepcion/{p_oficina}")
    chequear("sacar la excepcion vuelve al perfil",
             r.status_code == 200 and r.json()["puertas"] == [p_oficina], r.text[:200])
    r = cli.delete(f"/api/accesos/empleado/{emp_id}/excepcion/{p_oficina}")
    chequear("sacar una excepcion que no existe da 404", r.status_code == 404, r.text[:120])

    # 8) Validaciones.
    r = cli.put(f"/api/accesos/empleado/{emp_id}/perfil", json={"perfil_acceso_id": 999999})
    chequear("no deja asignar un perfil inexistente", r.status_code == 400, r.text[:160])
    r = cli.post(f"/api/accesos/empleado/{emp_id}/excepcion",
                 json={"dispositivo_id": 999999, "modo": "agregar"})
    chequear("no deja una excepcion sobre un equipo inexistente", r.status_code == 400, r.text[:160])
    r = cli.post(f"/api/accesos/empleado/{emp_id}/excepcion",
                 json={"dispositivo_id": p_personal, "modo": "cualquiera"})
    chequear("un modo invalido se rechaza", r.status_code == 422, r.text[:120])
    r = cli.get("/api/accesos/empleado/999999")
    chequear("un empleado inexistente da 404", r.status_code == 404, r.text[:120])

print("\n=== PERMISOS DEL MODULO ACCESOS ===")
me2 = cli.get("/api/auth/me").json()
chequear("sistema tiene las cuatro acciones de accesos",
         sorted(p["accion"] for p in me2["permisos"] if p["modulo"] == "accesos")
         == ["asignar", "editar", "eliminar", "ver"],
         sorted(p["accion"] for p in me2["permisos"] if p["modulo"] == "accesos"))

_sin2 = TestClient(main.app)
chequear("sin sesion no se ve el acceso de nadie",
         _sin2.get("/api/accesos/empleado/1").status_code in (401, 403))



print("\n=== EXCEPCIONES QUE QUEDAN OBSOLETAS ===")
if emp_id:
    # Perfil que NO da oficina, mas una excepcion que se la agrega.
    cli.put(f"/api/accesos/empleado/{emp_id}/perfil",
            json={"perfil_acceso_id": menos_oficina["id"]})
    r = cli.post(f"/api/accesos/empleado/{emp_id}/excepcion",
                 json={"dispositivo_id": p_oficina, "modo": "agregar", "motivo": "temporal"})
    a = r.json()
    chequear("la excepcion arranca con efecto",
             a["excepciones"][0]["sin_efecto"] is False, a["excepciones"])
    chequear("y le suma la puerta", p_oficina in a["puertas"], a["puertas"])

    # Le cambio el perfil a uno que YA incluye oficina: la excepcion queda muerta.
    r = cli.put(f"/api/accesos/empleado/{emp_id}/perfil",
                json={"perfil_acceso_id": todas["id"]})
    a = r.json()
    vieja = next(x for x in a["excepciones"] if x["dispositivo_id"] == p_oficina)
    chequear("al cambiar el perfil la excepcion queda marcada sin efecto",
             vieja["sin_efecto"] is True, vieja)
    chequear("pero no se borro sola", len(a["excepciones"]) == 1, a["excepciones"])
    chequear("y las puertas siguen siendo correctas",
             p_oficina in a["puertas"], a["puertas"])

    # Al revés: un "quitar" sobre algo que el perfil ya no da.
    cli.delete(f"/api/accesos/empleado/{emp_id}/excepcion/{p_oficina}")
    cli.post(f"/api/accesos/empleado/{emp_id}/excepcion",
             json={"dispositivo_id": p_oficina, "modo": "quitar"})
    r = cli.put(f"/api/accesos/empleado/{emp_id}/perfil",
                json={"perfil_acceso_id": menos_oficina["id"]})
    a = r.json()
    vieja2 = next(x for x in a["excepciones"] if x["dispositivo_id"] == p_oficina)
    chequear("un 'quitar' sobre algo que el perfil ya no da tambien queda sin efecto",
             vieja2["sin_efecto"] is True, vieja2)
    cli.delete(f"/api/accesos/empleado/{emp_id}/excepcion/{p_oficina}")

print("\n=== BORRAR UNA PUERTA QUE LA POLITICA USA ===")
# Una puerta suelta, sin perfiles ni excepciones: se borra sin drama.
r = cli.post("/api/dispositivos", json={
    "nombre": "Puerta suelta", "protocolo": "pull", "ip": "127.0.2.1",
    "cuenta_asistencia": False, "es_acceso": True})
suelta = r.json()["id"]
chequear("una puerta sin uso se borra", cli.delete(f"/api/dispositivos/{suelta}").status_code == 200)

# Una que esta en un perfil: se niega y dice en cual.
r = cli.delete(f"/api/dispositivos/{p_oficina}")
chequear("no deja borrar una puerta que esta en un perfil", r.status_code == 409, r.text[:240])
chequear("y nombra el perfil que la usa", "Solo oficina" in r.text or "Todas" in r.text, r.text[:240])

# Una con excepciones de empleados: antes tiraba error 500.
if emp_id:
    r = cli.post("/api/dispositivos", json={
        "nombre": "Solo excepcion", "protocolo": "pull", "ip": "127.0.2.2",
        "cuenta_asistencia": False, "es_acceso": True})
    solo_exc = r.json()["id"]
    cli.post(f"/api/accesos/empleado/{emp_id}/excepcion",
             json={"dispositivo_id": solo_exc, "modo": "agregar", "motivo": "prueba"})
    r = cli.delete(f"/api/dispositivos/{solo_exc}")
    chequear("una puerta con excepciones no tira error 500", r.status_code == 409, r.status_code)
    chequear("y avisa que hay excepciones", "excepci" in r.text.lower(), r.text[:240])
    cli.delete(f"/api/accesos/empleado/{emp_id}/excepcion/{solo_exc}")
    chequear("sacada la excepcion, ya se puede borrar",
             cli.delete(f"/api/dispositivos/{solo_exc}").status_code == 200)



print("\n=== PLAN: estado deseado ===")
from db.database import db_session
from sync.plan_accesos import estado_deseado, armar_plan

if emp_id:
    # Dejo a la persona con "Todas menos oficina": Personal y Camaras, no Oficina.
    cli.put(f"/api/accesos/empleado/{emp_id}/perfil",
            json={"perfil_acceso_id": menos_oficina["id"]})
    with db_session() as _cn:
        des = estado_deseado(_cn)
        _uid = _cn.execute("SELECT user_id FROM empleados WHERE id=?", (emp_id,)).fetchone()[0]
    _uid = str(_uid).strip()
    chequear("la persona figura en las puertas de su perfil",
             _uid in des.get(p_personal, {}) and _uid in des.get(p_camaras, {}),
             {k: list(v)[:3] for k, v in des.items()})
    chequear("y NO en la que el perfil no le da", _uid not in des.get(p_oficina, {}),
             list(des.get(p_oficina, {}))[:5])

    # Una excepcion la suma a oficina.
    cli.post(f"/api/accesos/empleado/{emp_id}/excepcion",
             json={"dispositivo_id": p_oficina, "modo": "agregar", "motivo": "plan"})
    with db_session() as _cn:
        des = estado_deseado(_cn)
    chequear("la excepcion la suma a esa puerta", _uid in des.get(p_oficina, {}))

    # Sin perfil y sin cargo: no figura en ninguna.
    cli.post(f"/api/accesos/empleado/{emp_id}/excepcion",
             json={"dispositivo_id": p_oficina, "modo": "quitar"}) if False else None
    cli.delete(f"/api/accesos/empleado/{emp_id}/excepcion/{p_oficina}")
    cli.put(f"/api/accesos/empleado/{emp_id}/perfil", json={"perfil_acceso_id": None})
    cli.put(f"/api/accesos/cargo/{cargo_id}/perfil", json={"perfil_acceso_id": None})
    with db_session() as _cn:
        des = estado_deseado(_cn)
    chequear("sin perfil ni cargo no figura en ninguna puerta",
             all(_uid not in v for v in des.values()))

    # Un egresado nunca entra al deseado, aunque tenga perfil.
    _c4 = sqlite3.connect(DB)
    _baja = _c4.execute("""SELECT id, user_id FROM empleados
                            WHERE activo=0 AND user_id IS NOT NULL LIMIT 1""").fetchone()
    if _baja:
        _c4.execute("UPDATE empleados SET perfil_acceso_id=? WHERE id=?",
                    (menos_oficina["id"], _baja[0]))
        _c4.commit()
    _c4.close()
    if _baja:
        with db_session() as _cn:
            des = estado_deseado(_cn)
        chequear("un egresado con perfil NO entra en el deseado",
                 all(str(_baja[1]).strip() not in v for v in des.values()))

print("\n=== PLAN: comparacion contra lo que hay ===")
if emp_id:
    cli.put(f"/api/accesos/empleado/{emp_id}/perfil",
            json={"perfil_acceso_id": menos_oficina["id"]})

    with db_session() as _cn:
        _puertas = [dict(r) for r in _cn.execute(
            "SELECT id, nombre, ubicacion FROM dispositivos WHERE id IN (?,?,?)",
            (p_personal, p_oficina, p_camaras))]

        # Personal: esta cargado alguien que ya no corresponde, y falta la persona.
        _sobra = "99999"
        lecturas = {
            p_personal: {"ok": True, "transporte": "udp", "error": None,
                         "usuarios": [{"user_id": _sobra, "nombre": "X", "uid": 1,
                                       "privilegio": 0, "tarjeta": 0, "grupo": "1"}]},
            p_oficina:  {"ok": True, "transporte": "udp", "error": None, "usuarios": []},
            p_camaras:  {"ok": False, "transporte": None, "error": "timed out", "usuarios": []},
        }
        maestro = {"ok": True, "usuarios": [{"user_id": _uid}]}
        plan = armar_plan(_cn, _puertas, lecturas, maestro)

    por_puerta = {p["id"]: p for p in plan["puertas"]}
    chequear("marca para cargar a quien falta",
             any(a["user_id"] == _uid for a in por_puerta[p_personal]["agregar"]),
             por_puerta[p_personal]["agregar"])
    chequear("marca para sacar a quien sobra",
             any(s["user_id"] == _sobra for s in por_puerta[p_personal]["sacar"]),
             por_puerta[p_personal]["sacar"])
    _s = next(s for s in por_puerta[p_personal]["sacar"] if s["user_id"] == _sobra)
    chequear("y explica por que sobra", _s["motivo"] == "desconocido", _s)
    chequear("la puerta que el perfil no da no pide cargar a nadie",
             all(a["user_id"] != _uid for a in por_puerta[p_oficina]["agregar"]),
             por_puerta[p_oficina]["agregar"])
    chequear("una puerta que no contesta no genera plan",
             por_puerta[p_camaras]["ok"] is False
             and not por_puerta[p_camaras]["agregar"], por_puerta[p_camaras])
    chequear("cuenta la que no contesto", plan["total"]["sin_leer"] == 1, plan["total"])

    # Sin huella en el maestro: se marca en vez de prometer que se puede cargar.
    with db_session() as _cn:
        plan2 = armar_plan(_cn, _puertas, lecturas, {"ok": True, "usuarios": []})
    _a = next(a for a in plan2["puertas"][0]["agregar"] if a["user_id"] == _uid) \
        if plan2["puertas"][0]["agregar"] else None
    chequear("sin huella en el maestro lo marca", _a and _a["sin_huella"] is True, _a)
    chequear("y lo cuenta en el total", plan2["total"]["sin_huella"] >= 1, plan2["total"])

    # Maestro caido: se avisa que no se pudo verificar.
    with db_session() as _cn:
        plan3 = armar_plan(_cn, _puertas, lecturas, {"ok": False, "usuarios": []})
    chequear("si el maestro no contesta avisa que no verifico huellas",
             plan3["huellas_verificadas"] is False, plan3["huellas_verificadas"])

print("\n=== PLAN: endpoint ===")
import sync.lectores as _lec
_guardado = _lec.leer_padrones
_lec.leer_padrones = lambda ds: {d["id"]: {"ok": True, "transporte": "udp",
                                           "usuarios": [], "error": None} for d in ds}
r = cli.get("/api/accesos/plan")
chequear("GET plan responde 200", r.status_code == 200, r.text[:200])
cuerpo = r.json() if r.status_code == 200 else {}
chequear("trae total y puertas", "total" in cuerpo and "puertas" in cuerpo, list(cuerpo))
_lec.leer_padrones = _guardado

_sin3 = TestClient(main.app)
chequear("sin sesion no se ve el plan",
         _sin3.get("/api/accesos/plan").status_code in (401, 403))



print("\n=== LOS CARGOS VIENEN CON LOS PERFILES ===")
d = cli.get("/api/perfiles-acceso").json()
chequear("el listado de perfiles trae tambien los cargos", "cargos" in d, list(d))
chequear("cada cargo dice a cuantos les cambia el acceso",
         all("heredan" in c and "empleados" in c for c in d["cargos"]),
         d["cargos"][:2])

if cargo_id:
    cli.put(f"/api/accesos/cargo/{cargo_id}/perfil",
            json={"perfil_acceso_id": menos_oficina["id"]})
    d = cli.get("/api/perfiles-acceso").json()
    _c = next(c for c in d["cargos"] if c["id"] == cargo_id)
    chequear("y refleja el perfil asignado",
             _c["perfil_acceso_id"] == menos_oficina["id"], _c)
    chequear("heredan nunca supera a los empleados del cargo",
             _c["heredan"] <= _c["empleados"], _c)

    # Quien tiene perfil propio no hereda: es el numero que dice a cuantos les
    # cambia el acceso si se toca la fila del cargo.
    # Se arranca sin perfil propio a proposito: otras pruebas ya le pusieron
    # uno. Y se lee el cargo que tiene AHORA, porque tambien se lo cambiaron.
    if emp_id:
        _cn = sqlite3.connect(DB)
        _cargo_actual = _cn.execute(
            "SELECT cargo_id FROM empleados WHERE id=?", (emp_id,)).fetchone()[0]
        _cn.close()
        cli.put(f"/api/accesos/empleado/{emp_id}/perfil", json={"perfil_acceso_id": None})
        _antes = next(c for c in cli.get("/api/perfiles-acceso").json()["cargos"]
                      if c["id"] == _cargo_actual)["heredan"]
        chequear("quien no tiene perfil propio se cuenta como pendiente", _antes >= 1, _antes)
        cli.put(f"/api/accesos/empleado/{emp_id}/perfil",
                json={"perfil_acceso_id": solo_oficina["id"]})
        _despues = next(c for c in cli.get("/api/perfiles-acceso").json()["cargos"]
                        if c["id"] == _cargo_actual)["heredan"]
        chequear("al darle perfil propio deja de contarse como pendiente",
                 _despues == _antes - 1, (_antes, _despues))
        cli.put(f"/api/accesos/empleado/{emp_id}/perfil", json={"perfil_acceso_id": None})
        _vuelta = next(c for c in cli.get("/api/perfiles-acceso").json()["cargos"]
                       if c["id"] == _cargo_actual)["heredan"]
        chequear("y al sacarselo vuelve a contarse", _vuelta == _antes, (_antes, _vuelta))

    cli.put(f"/api/accesos/cargo/{cargo_id}/perfil", json={"perfil_acceso_id": None})


print(f"\n{'='*52}\n  {ok} pasaron, {fallos} fallaron\n{'='*52}")
raise SystemExit(1 if fallos else 0)
