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
lectores.leer_padron = lambda d, con_huellas=False: {"ok": True, "transporte": "udp",
                                  "usuarios": PADRON, "error": None}
r = cli.get(f"/api/dispositivos/{puerta['id']}/padron")
chequear("GET padron responde 200", r.status_code == 200, r.text[:160])
cuerpo = r.json() if r.status_code == 200 else {}
chequear("informa por que transporte contesto", cuerpo.get("transporte") == "udp", cuerpo)
chequear("devuelve resumen y filas", "resumen" in cuerpo and "filas" in cuerpo, list(cuerpo))
chequear("deja constancia de que el equipo contesto",
         any(x["visto_en"] for x in cli.get("/api/dispositivos").json()
             if x["id"] == puerta["id"]))

lectores.leer_padron = lambda d, con_huellas=False: {"ok": False, "transporte": None, "usuarios": [],
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


def _falso(d, con_huellas=False):
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

_lento = lambda d, con_huellas=False: (time.sleep(0.4), {"ok": True, "transporte": "tcp",
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
    a = cli.get(f"/api/accesos/empleado/{emp_id}").json()
    chequear("sin perfil ni cargo no abre nada", a["puertas"] == [], a["puertas"])
    chequear("y aparece en la lista de los que no tienen acceso",
             any(x["id"] == emp_id for x in cli.get("/api/accesos/sin-perfil").json()))

    # 2) El acceso sale solo del perfil propio. El cargo no interviene: la
    # sugerencia por cargo existio y se saco porque se usaba un par de veces al
    # mes y su valor dependia de que el acceso se dedujera limpio del puesto.
    r = cli.put(f"/api/accesos/empleado/{emp_id}/perfil",
                json={"perfil_acceso_id": menos_oficina["id"]})
    chequear("se le asigna el perfil desde el legajo", r.status_code == 200, r.text[:160])
    a = cli.get(f"/api/accesos/empleado/{emp_id}").json()
    chequear("la persona tiene el perfil", a["perfil"]["id"] == menos_oficina["id"],
             a.get("perfil"))
    chequear("y abre las puertas del perfil",
             sorted(a["puertas"]) == sorted([p_personal, p_camaras]), a["puertas"])
    chequear("ya no figura entre los pendientes",
             all(x["id"] != emp_id for x in cli.get("/api/accesos/sin-perfil").json()))
    chequear("el resultado no habla de cargos", "sugerencia_del_cargo" not in a, list(a))

    # 3) CAMBIAR EL CARGO NO CAMBIA EL ACCESO.
    _c5 = sqlite3.connect(DB)
    _otro = _c5.execute("SELECT id FROM cargos WHERE id <> ? LIMIT 1", (cargo_id,)).fetchone()
    if _otro:
        _c5.execute("UPDATE empleados SET cargo_id=? WHERE id=?", (_otro[0], emp_id))
        _c5.commit()
    _c5.close()
    if _otro:
        a = cli.get(f"/api/accesos/empleado/{emp_id}").json()
        chequear("cambiarle el cargo NO le cambia el perfil",
                 a["perfil"]["id"] == menos_oficina["id"], a.get("perfil"))
        chequear("ni las puertas que abre",
                 sorted(a["puertas"]) == sorted([p_personal, p_camaras]), a["puertas"])

    # 4) Perfil propio: se elige a mano y manda.
    cli.put(f"/api/accesos/empleado/{emp_id}/perfil",
            json={"perfil_acceso_id": solo_oficina["id"]})
    a = cli.get(f"/api/accesos/empleado/{emp_id}").json()
    chequear("el perfil propio le gana al del cargo",
             a["perfil"]["id"] == solo_oficina["id"], a.get("perfil"))
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
chequear("sistema tiene las cinco acciones de accesos",
         sorted(p["accion"] for p in me2["permisos"] if p["modulo"] == "accesos")
         == ["asignar", "editar", "eliminar", "excepcion", "ver"],
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
_lec.leer_padrones = lambda ds, con_huellas=False: {d["id"]: {"ok": True, "transporte": "udp",
                                           "usuarios": [], "error": None} for d in ds}
r = cli.get("/api/accesos/plan")
chequear("GET plan responde 200", r.status_code == 200, r.text[:200])
cuerpo = r.json() if r.status_code == 200 else {}
chequear("trae total y puertas", "total" in cuerpo and "puertas" in cuerpo, list(cuerpo))
_lec.leer_padrones = _guardado

_sin3 = TestClient(main.app)
chequear("sin sesion no se ve el plan",
         _sin3.get("/api/accesos/plan").status_code in (401, 403))



print("\n=== EL CARGO YA NO PROPONE NINGUN PERFIL ===")
d = cli.get("/api/perfiles-acceso").json()
chequear("el listado de perfiles ya no trae cargos", "cargos" not in d, list(d))
chequear("y sigue trayendo perfiles y puertas",
         "perfiles" in d and "puertas" in d, list(d))

r = cli.put(f"/api/accesos/cargo/{cargo_id}/perfil", json={"perfil_acceso_id": 1})
chequear("el endpoint del perfil por cargo fue eliminado", r.status_code == 404, r.status_code)

_cn3 = sqlite3.connect(DB)
_cols = {r[1] for r in _cn3.execute("PRAGMA table_info(cargos)")}
_cn3.close()
chequear("la columna se fue de la tabla cargos",
         "perfil_acceso_id" not in _cols, sorted(_cols))

_cargos = cli.get("/api/cargos").json()
chequear("el catalogo de cargos tampoco la expone",
         all("perfil_acceso_id" not in c for c in _cargos),
         list(_cargos[0]) if _cargos else [])


print("\n=== LISTA DE PENDIENTES DE ASIGNAR ===")
if emp_id:
    cli.put(f"/api/accesos/empleado/{emp_id}/perfil", json={"perfil_acceso_id": None})
    lista = cli.get("/api/accesos/sin-perfil").json()
    _yo = next((x for x in lista if x["id"] == emp_id), None)
    chequear("quien no tiene perfil aparece en la lista", _yo is not None, len(lista))
    chequear("la lista trae el nombre del cargo", _yo and "cargo" in _yo, _yo)
    chequear("la lista no habla de sugerencias por cargo",
             _yo and "sugerencia_id" not in _yo, list(_yo) if _yo else [])

    cli.put(f"/api/accesos/empleado/{emp_id}/perfil",
            json={"perfil_acceso_id": menos_oficina["id"]})
    chequear("al asignarle el perfil sale de la lista",
             all(x["id"] != emp_id for x in cli.get("/api/accesos/sin-perfil").json()))

    # Un egresado no es un pendiente: no tiene que abrir nada.
    _cn2 = sqlite3.connect(DB)
    _baja2 = _cn2.execute("""SELECT id FROM empleados
                              WHERE activo=0 AND perfil_acceso_id IS NULL LIMIT 1""").fetchone()
    _cn2.close()
    if _baja2:
        chequear("un egresado no figura como pendiente",
                 all(x["id"] != _baja2[0] for x in cli.get("/api/accesos/sin-perfil").json()))

print("\n=== EL CARGO NO DA ACCESO POR SI SOLO ===")
# Lo que se elimino fue que el cargo DIERA acceso: con eso, cambiarle el cargo a
# alguien le cambiaba las puertas, y eso lo puede hacer quien edita empleados sin
# permiso de accesos. Asignar perfiles POR cargo si existe (mas abajo), pero ahi
# el cargo solo elige a quien y no queda guardada ninguna relacion.
r = cli.post(f"/api/accesos/cargo/{cargo_id}/aplicar")
chequear("el viejo endpoint por cargo no existe mas", r.status_code == 404, r.status_code)
_c_ac = sqlite3.connect(DB)
_cols_ac = {c[1] for c in _c_ac.execute("PRAGMA table_info(cargos)").fetchall()}
_c_ac.close()
chequear("y el cargo no guarda ningun perfil",
         not any("perfil" in c for c in _cols_ac), _cols_ac)




print("\n=== LAS EXCEPCIONES NECESITAN UN PERFIL ===")
if emp_id:
    cli.put(f"/api/accesos/empleado/{emp_id}/perfil",
            json={"perfil_acceso_id": menos_oficina["id"]})
    for _d in (p_oficina, p_personal, p_camaras):
        cli.delete(f"/api/accesos/empleado/{emp_id}/excepcion/{_d}")
    cli.post(f"/api/accesos/empleado/{emp_id}/excepcion",
             json={"dispositivo_id": p_oficina, "modo": "agregar", "motivo": "caso raro"})

    # Sacarle el perfil borra las excepciones: una excepcion modifica un perfil,
    # y sin perfil no hay nada que modificar.
    a = cli.put(f"/api/accesos/empleado/{emp_id}/perfil",
                json={"perfil_acceso_id": None}).json()
    chequear("sacar el perfil borra las excepciones", a["excepciones"] == [], a["excepciones"])
    chequear("y dice cuantas borro", a["excepciones_borradas"] == 1, a.get("excepciones_borradas"))
    chequear("no queda abriendo nada por un residuo", a["puertas"] == [], a["puertas"])

    # Sin perfil no se puede crear una excepcion.
    r = cli.post(f"/api/accesos/empleado/{emp_id}/excepcion",
                 json={"dispositivo_id": p_oficina, "modo": "agregar"})
    chequear("sin perfil no se puede crear una excepcion", r.status_code == 409, r.text[:200])
    chequear("y el mensaje explica que hacer",
             "perfil" in r.text.lower() and "cre" in r.text.lower(), r.text[:200])

    # Cambiar de un perfil a otro NO las borra: la decision es sobre la persona.
    cli.put(f"/api/accesos/empleado/{emp_id}/perfil",
            json={"perfil_acceso_id": menos_oficina["id"]})
    cli.post(f"/api/accesos/empleado/{emp_id}/excepcion",
             json={"dispositivo_id": p_oficina, "modo": "agregar", "motivo": "sigue valiendo"})
    a = cli.put(f"/api/accesos/empleado/{emp_id}/perfil",
                json={"perfil_acceso_id": solo_oficina["id"]}).json()
    chequear("cambiar de perfil NO borra las excepciones",
             len(a["excepciones"]) == 1, a["excepciones"])
    chequear("no informa borrados al cambiar de perfil",
             a["excepciones_borradas"] == 0, a.get("excepciones_borradas"))
    chequear("la que quedo sin efecto se marca",
             a["excepciones"][0]["sin_efecto"] is True, a["excepciones"])

    # Y al sacarle el perfil, esa tambien se va.
    a = cli.put(f"/api/accesos/empleado/{emp_id}/perfil",
                json={"perfil_acceso_id": None}).json()
    chequear("sacar el perfil las borra aunque esten sin efecto",
             a["excepciones"] == [] and a["excepciones_borradas"] == 1, a)

    # Sin excepciones, sacar el perfil no borra nada y no molesta.
    a = cli.put(f"/api/accesos/empleado/{emp_id}/perfil",
                json={"perfil_acceso_id": None}).json()
    chequear("sin excepciones no informa ningun borrado",
             a["excepciones_borradas"] == 0, a.get("excepciones_borradas"))



print("\n=== UN USUARIO SIN PERMISO DE ACCESOS ===")
from auth.core import create_token as _mk_token, invalidar_cache as _inval


def _usuario_con(permisos, etiqueta):
    """Crea un rol con exactamente esos permisos y devuelve su token."""
    c = sqlite3.connect(DB)
    c.execute("INSERT OR IGNORE INTO roles (nombre) VALUES (?)", (etiqueta,))
    rol = c.execute("SELECT id FROM roles WHERE nombre=?", (etiqueta,)).fetchone()[0]
    c.execute("DELETE FROM permisos WHERE rol_id=?", (rol,))
    for m, a in permisos:
        c.execute("INSERT INTO permisos (rol_id, modulo, accion) VALUES (?,?,?)", (rol, m, a))
    c.execute("""INSERT OR IGNORE INTO usuarios (nombre, email, password_hash, rol_id, activo)
                 VALUES (?,?,?,?,1)""", (etiqueta, etiqueta + "@x.test", "x", rol))
    c.execute("UPDATE usuarios SET rol_id=?, activo=1 WHERE email=?", (rol, etiqueta + "@x.test"))
    uid = c.execute("SELECT id FROM usuarios WHERE email=?", (etiqueta + "@x.test",)).fetchone()[0]
    c.commit()
    c.close()
    _inval(rol)
    return _mk_token(uid, etiqueta + "@x.test", rol, etiqueta)


# Solo mira: ve el acceso de la gente pero no puede tocarlo.
_mirar = TestClient(main.app)
_mirar.cookies.set("session", _usuario_con(
    [("empleados", "ver"), ("empleados", "editar"), ("accesos", "ver")], "prueba_solo_ver"))

if emp_id:
    r = _mirar.get(f"/api/accesos/empleado/{emp_id}")
    chequear("con accesos:ver puede consultar el acceso de alguien",
             r.status_code == 200, r.status_code)
    r = _mirar.put(f"/api/accesos/empleado/{emp_id}/perfil",
                   json={"perfil_acceso_id": menos_oficina["id"]})
    chequear("pero NO puede asignarle un perfil", r.status_code == 403, r.status_code)
    r = _mirar.post(f"/api/accesos/empleado/{emp_id}/excepcion",
                    json={"dispositivo_id": p_oficina, "modo": "agregar"})
    chequear("ni ponerle una excepcion", r.status_code == 403, r.status_code)
    r = _mirar.delete(f"/api/accesos/empleado/{emp_id}/excepcion/{p_oficina}")
    chequear("ni sacarle una", r.status_code == 403, r.status_code)
    r = _mirar.post("/api/perfiles-acceso", json={"nombre": "No deberia", "dispositivos": []})
    chequear("ni crear perfiles", r.status_code == 403, r.status_code)

# Edita empleados pero no ve accesos: la seccion no le existe.
_rrhh = TestClient(main.app)
_rrhh.cookies.set("session", _usuario_con(
    [("empleados", "ver"), ("empleados", "editar")], "prueba_sin_accesos"))
if emp_id:
    r = _rrhh.get(f"/api/accesos/empleado/{emp_id}")
    chequear("sin accesos:ver ni siquiera puede mirar", r.status_code == 403, r.status_code)
    r = _rrhh.get("/api/perfiles-acceso")
    chequear("ni ver los perfiles", r.status_code == 403, r.status_code)
    r = _rrhh.get("/api/accesos/sin-perfil")
    chequear("ni la lista de pendientes", r.status_code == 403, r.status_code)
    # Pero si puede editar el legajo: el cargo es un dato de empleados.
    r = _rrhh.get(f"/api/empleados/{emp_id}")
    chequear("y sigue pudiendo abrir el legajo", r.status_code == 200, r.status_code)

# Asigna accesos pero no edita empleados: el caso inverso.
_acc = TestClient(main.app)
_acc.cookies.set("session", _usuario_con(
    [("empleados", "ver"), ("accesos", "ver"), ("accesos", "asignar")], "prueba_solo_accesos"))
if emp_id:
    r = _acc.put(f"/api/accesos/empleado/{emp_id}/perfil",
                 json={"perfil_acceso_id": menos_oficina["id"]})
    chequear("con accesos:asignar puede asignar sin editar empleados",
             r.status_code == 200, r.status_code)
    r = _acc.post("/api/perfiles-acceso", json={"nombre": "Tampoco", "dispositivos": []})
    chequear("pero asignar no alcanza para redefinir perfiles",
             r.status_code == 403, r.status_code)



print("\n=== LAS EXCEPCIONES TIENEN SU PROPIO PERMISO ===")
# Puede aplicar la politica pero no desviarse de ella: le pone el perfil que
# corresponde a alguien, y no puede decidir que esa persona sea distinta.
_aplica = TestClient(main.app)
_aplica.cookies.set("session", _usuario_con(
    [("empleados", "ver"), ("accesos", "ver"), ("accesos", "asignar")], "prueba_aplica"))

if emp_id:
    r = _aplica.put(f"/api/accesos/empleado/{emp_id}/perfil",
                    json={"perfil_acceso_id": menos_oficina["id"]})
    chequear("con asignar puede ponerle el perfil", r.status_code == 200, r.status_code)
    r = _aplica.post(f"/api/accesos/empleado/{emp_id}/excepcion",
                     json={"dispositivo_id": p_oficina, "modo": "agregar"})
    chequear("pero NO puede ponerle una excepcion", r.status_code == 403, r.status_code)
    r = _aplica.delete(f"/api/accesos/empleado/{emp_id}/excepcion/{p_oficina}")
    chequear("ni sacarle una", r.status_code == 403, r.status_code)

# Y al reves: puede manejar excepciones sin poder asignar perfiles. Es una
# combinacion rara pero coherente: solo modifica a quien ya tiene uno.
_exc = TestClient(main.app)
_exc.cookies.set("session", _usuario_con(
    [("empleados", "ver"), ("accesos", "ver"), ("accesos", "excepcion")], "prueba_excepcion"))

if emp_id:
    r = _exc.post(f"/api/accesos/empleado/{emp_id}/excepcion",
                  json={"dispositivo_id": p_oficina, "modo": "agregar", "motivo": "permiso"})
    chequear("con excepcion puede poner una", r.status_code == 201, r.text[:160])
    r = _exc.put(f"/api/accesos/empleado/{emp_id}/perfil",
                 json={"perfil_acceso_id": solo_oficina["id"]})
    chequear("pero NO puede cambiarle el perfil", r.status_code == 403, r.status_code)
    r = _exc.delete(f"/api/accesos/empleado/{emp_id}/excepcion/{p_oficina}")
    chequear("y si puede sacar la que puso", r.status_code == 200, r.status_code)

# Sistema sigue teniendo las cinco acciones.
me3 = cli.get("/api/auth/me").json()
chequear("sistema tiene las cinco acciones de accesos",
         sorted(p["accion"] for p in me3["permisos"] if p["modulo"] == "accesos")
         == ["asignar", "editar", "eliminar", "excepcion", "ver"],
         sorted(p["accion"] for p in me3["permisos"] if p["modulo"] == "accesos"))



print("\n=== SACAR EL PERFIL NO ES UN ATAJO PARA BORRAR EXCEPCIONES ===")
# Sin este freno alcanzaba con sacar el perfil y volver a ponerlo para
# devolverle a alguien una puerta que le habian quitado a proposito.
if emp_id:
    cli.put(f"/api/accesos/empleado/{emp_id}/perfil",
            json={"perfil_acceso_id": todas["id"]})
    for _d in (p_oficina, p_personal, p_camaras):
        cli.delete(f"/api/accesos/empleado/{emp_id}/excepcion/{_d}")
    cli.post(f"/api/accesos/empleado/{emp_id}/excepcion",
             json={"dispositivo_id": p_oficina, "modo": "quitar", "motivo": "por seguridad"})
    _antes = cli.get(f"/api/accesos/empleado/{emp_id}").json()
    chequear("arranca con la puerta quitada por excepcion",
             p_oficina not in _antes["puertas"], _antes["puertas"])

    # El que solo asigna no puede sacarle el perfil mientras tenga excepciones.
    r = _aplica.put(f"/api/accesos/empleado/{emp_id}/perfil", json={"perfil_acceso_id": None})
    chequear("con asignar pero sin excepcion, sacar el perfil se rechaza",
             r.status_code == 403, r.status_code)
    chequear("y el mensaje dice cuantas excepciones lo impiden",
             "excepc" in r.text.lower(), r.text[:200])

    _despues = cli.get(f"/api/accesos/empleado/{emp_id}").json()
    chequear("la excepcion sigue ahi", len(_despues["excepciones"]) == 1, _despues["excepciones"])
    chequear("y la puerta sigue quitada", p_oficina not in _despues["puertas"], _despues["puertas"])

    # Cambiar de un perfil a otro si puede: no borra nada.
    r = _aplica.put(f"/api/accesos/empleado/{emp_id}/perfil",
                    json={"perfil_acceso_id": menos_oficina["id"]})
    chequear("pero cambiar de un perfil a otro si lo puede hacer",
             r.status_code == 200, r.status_code)
    chequear("y la excepcion sobrevive", len(r.json()["excepciones"]) == 1, r.json()["excepciones"])

    # Sin excepciones, sacar el perfil no necesita el permiso extra.
    cli.delete(f"/api/accesos/empleado/{emp_id}/excepcion/{p_oficina}")
    r = _aplica.put(f"/api/accesos/empleado/{emp_id}/perfil", json={"perfil_acceso_id": None})
    chequear("sin excepciones, sacar el perfil no necesita el permiso extra",
             r.status_code == 200, r.status_code)

    # Y quien si tiene el permiso puede sacarlo aunque haya excepciones.
    cli.put(f"/api/accesos/empleado/{emp_id}/perfil",
            json={"perfil_acceso_id": todas["id"]})
    cli.post(f"/api/accesos/empleado/{emp_id}/excepcion",
             json={"dispositivo_id": p_oficina, "modo": "quitar"})
    r = cli.put(f"/api/accesos/empleado/{emp_id}/perfil", json={"perfil_acceso_id": None})
    chequear("quien tiene el permiso de excepciones si puede sacarlo",
             r.status_code == 200 and r.json()["excepciones_borradas"] == 1, r.text[:160])



print("\n=== NO SE PUEDE SACAR DE LA POLITICA UNA PUERTA DESDE LOS EQUIPOS ===")
# Destildar "abre una puerta" la saca de la matriz y del plan, pero los perfiles
# la siguen conteniendo: seria deshacer politica de acceso desde la pantalla de
# equipos, y en silencio.
_base = {"nombre": "Oficina", "protocolo": "pull", "ip": "127.0.1.2", "puerto": 4370,
         "cuenta_asistencia": False, "activo": True}

r = cli.put(f"/api/dispositivos/{p_oficina}", json={**_base, "es_acceso": False})
chequear("no deja destildar «abre una puerta» si un perfil la usa",
         r.status_code == 409, r.text[:240])
chequear("y dice quien la esta usando", "perfil" in r.text.lower(), r.text[:240])

d = cli.get("/api/perfiles-acceso").json()
chequear("la puerta sigue en la matriz",
         any(x["id"] == p_oficina for x in d["puertas"]), [x["nombre"] for x in d["puertas"]])

# Desactivarla SI se puede: un lector se rompe y hay que desactivarlo. Pero el
# plan tiene que decir que dejo de administrarse.
r = cli.put(f"/api/dispositivos/{p_oficina}", json={**_base, "es_acceso": True, "activo": False})
chequear("desactivar una puerta si se permite", r.status_code == 200, r.text[:200])

import sync.lectores as _lec2
_g = _lec2.leer_padrones
_lec2.leer_padrones = lambda ds, con_huellas=False: {x["id"]: {"ok": True, "transporte": "udp",
                                            "usuarios": [], "error": None} for x in ds}
plan = cli.get("/api/accesos/plan").json()
_lec2.leer_padrones = _g

_fuera = {f["id"] for f in plan.get("fuera_de_plan", [])}
chequear("el plan avisa que esa puerta quedo sin administrar",
         p_oficina in _fuera, plan.get("fuera_de_plan"))
_f = next(f for f in plan["fuera_de_plan"] if f["id"] == p_oficina)
chequear("y explica por que", _f["motivo"] == "está desactivada", _f)
chequear("mientras tanto no aparece entre las puertas del plan",
         all(x["id"] != p_oficina for x in plan["puertas"]),
         [x["nombre"] for x in plan["puertas"]])

# Al reactivarla, vuelve a administrarse sola.
cli.put(f"/api/dispositivos/{p_oficina}", json={**_base, "es_acceso": True, "activo": True})
_lec2.leer_padrones = lambda ds, con_huellas=False: {x["id"]: {"ok": True, "transporte": "udp",
                                            "usuarios": [], "error": None} for x in ds}
plan2 = cli.get("/api/accesos/plan").json()
_lec2.leer_padrones = _g
chequear("al reactivarla vuelve al plan",
         any(x["id"] == p_oficina for x in plan2["puertas"]),
         [x["nombre"] for x in plan2["puertas"]])
chequear("y sale del aviso", all(f["id"] != p_oficina for f in plan2.get("fuera_de_plan", [])),
         plan2.get("fuera_de_plan"))



print("\n=== DESCUBRIR PERFILES DESDE LOS LECTORES ===")
from sync.descubrir_perfiles import agrupar_por_puertas, emparejar_con_perfiles

_PUERTAS = [{"id": p_personal, "nombre": "Personal"},
            {"id": p_oficina,  "nombre": "Oficina"},
            {"id": p_camaras,  "nombre": "Camaras"}]
_EMPS = {
    "10": {"id": 101, "nombre": "Ana",   "apellido": "Perez",  "activo": 1, "perfil_acceso_id": None},
    "11": {"id": 102, "nombre": "Beto",  "apellido": "Gomez",  "activo": 1, "perfil_acceso_id": None},
    "12": {"id": 103, "nombre": "Carla", "apellido": "Lopez",  "activo": 1, "perfil_acceso_id": None},
    "13": {"id": 104, "nombre": "Dario", "apellido": "Ruiz",   "activo": 1, "perfil_acceso_id": 7},
    "14": {"id": 105, "nombre": "Elsa",  "apellido": "Torres", "activo": 0, "perfil_acceso_id": None},
}


def _lect(por_puerta):
    return {d: {"ok": True, "transporte": "udp", "error": None,
                "usuarios": [{"user_id": u} for u in us]}
            for d, us in por_puerta.items()}


# 10, 11 y 13 estan en Personal+Camaras. 12 solo en Oficina. 14 esta de baja.
# 99 no existe en el sistema.
_L = _lect({
    p_personal: ["10", "11", "13", "14"],
    p_oficina:  ["12", "99"],
    p_camaras:  ["10", "11", "13"],
})
res = agrupar_por_puertas(_L, _PUERTAS, _EMPS)

chequear("la lectura quedo completa", res["completo"] is True, res["completo"])
chequear("arma dos grupos", len(res["grupos"]) == 2,
         [(g["nombres_puertas"], g["total"]) for g in res["grupos"]])

_g1 = res["grupos"][0]
chequear("el grupo mas grande va primero", _g1["total"] == 3, _g1["total"])
chequear("y son los de Personal + Camaras",
         sorted(_g1["puertas"]) == sorted([p_personal, p_camaras]), _g1["nombres_puertas"])
chequear("cuenta cuantos ya tienen perfil propio", _g1["con_perfil"] == 1, _g1)
chequear("y cuantos quedan por asignar", _g1["sin_perfil"] == 2, _g1)
chequear("marca a cada uno si ya tiene perfil",
         sum(1 for p in _g1["personas"] if p["tiene_perfil"]) == 1, _g1["personas"])

_g2 = res["grupos"][1]
chequear("el otro grupo es el de una sola puerta",
         _g2["puertas"] == [p_oficina] and _g2["total"] == 1, _g2)

_ign = {i["user_id"]: i for i in res["ignorados"]}
chequear("ignora a quien no existe en el sistema",
         "99" in _ign and "no existe" in _ign["99"]["motivo"], res["ignorados"])
chequear("e ignora a los dados de baja",
         "14" in _ign and "baja" in _ign["14"]["motivo"], res["ignorados"])
chequear("los ignorados no ensucian ningun grupo",
         all(all(p["user_id"] not in ("14", "99") for p in g["personas"])
             for g in res["grupos"]))

print("\n=== CON UNA PUERTA SIN LEER NO PROPONE NADA ===")
_L2 = dict(_L)
_L2[p_camaras] = {"ok": False, "transporte": None, "usuarios": [], "error": "timed out"}
res2 = agrupar_por_puertas(_L2, _PUERTAS, _EMPS)
chequear("no arma grupos si falta leer una puerta", res2["grupos"] == [], res2["grupos"])
chequear("avisa cual falto", len(res2["sin_leer"]) == 1, res2["sin_leer"])
chequear("y lo marca como incompleto", res2["completo"] is False, res2["completo"])

print("\n=== EMPAREJAR CON PERFILES QUE YA EXISTEN ===")
_PERF = [
    {"id": 1, "nombre": "Todas menos oficina", "dispositivos": [p_personal, p_camaras]},
    {"id": 2, "nombre": "Todo", "dispositivos": [p_personal, p_oficina, p_camaras]},
]
emparejar_con_perfiles(res["grupos"], _PERF)
chequear("reconoce el perfil que coincide exacto",
         res["grupos"][0]["perfil_existente"]["id"] == 1, res["grupos"][0].get("perfil_existente"))
chequear("y deja en None al que no tiene ninguno igual",
         res["grupos"][1]["perfil_existente"] is None, res["grupos"][1].get("perfil_existente"))

# Un perfil que incluye esas puertas Y ALGUNA MAS no sirve: le daria a esa
# gente acceso que hoy no tiene.
_solo_mas = [{"id": 3, "nombre": "De mas", "dispositivos": [p_personal, p_camaras, p_oficina]}]
_copia = [dict(g) for g in res["grupos"]]
emparejar_con_perfiles(_copia, _solo_mas)
chequear("un perfil que incluye de mas NO se propone",
         _copia[0]["perfil_existente"] is None, _copia[0].get("perfil_existente"))

print("\n=== APLICAR UN GRUPO ===")
import sync.lectores as _lec3
_g3 = _lec3.leer_padrones
_lec3.leer_padrones = lambda ds, con_huellas=False: {d["id"]: {"ok": True, "transporte": "udp",
                                            "usuarios": [], "error": None} for d in ds}
r = cli.get("/api/accesos/descubrir")
chequear("GET descubrir responde 200", r.status_code == 200, r.text[:160])
chequear("trae grupos, ignorados y si quedo completo",
         all(k in r.json() for k in ("grupos", "ignorados", "completo")), list(r.json()))
_lec3.leer_padrones = _g3

_c6 = sqlite3.connect(DB)
_libres = [x[0] for x in _c6.execute(
    """SELECT id FROM empleados WHERE activo=1 AND perfil_acceso_id IS NULL LIMIT 3""")]
_c6.close()
if len(_libres) >= 2:
    r = cli.post("/api/accesos/descubrir/aplicar",
                 json={"empleados": _libres, "perfil_acceso_id": menos_oficina["id"]})
    chequear("aplicar un grupo responde 200", r.status_code == 200, r.text[:160])
    chequear("y asigna a todos los que no tenian", r.json()["asignados"] == len(_libres), r.json())

    r = cli.post("/api/accesos/descubrir/aplicar",
                 json={"empleados": _libres, "perfil_acceso_id": solo_oficina["id"]})
    chequear("aplicar de nuevo NO pisa a quien ya tiene perfil",
             r.json()["asignados"] == 0 and r.json()["sin_tocar"] == len(_libres), r.json())
    for _e in _libres:
        cli.put(f"/api/accesos/empleado/{_e}/perfil", json={"perfil_acceso_id": None})

r = cli.post("/api/accesos/descubrir/aplicar", json={"empleados": [], "perfil_acceso_id": 1})
chequear("sin empleados se rechaza", r.status_code == 400, r.status_code)
r = cli.post("/api/accesos/descubrir/aplicar", json={"empleados": [1], "perfil_acceso_id": 999999})
chequear("con un perfil inexistente se rechaza", r.status_code == 400, r.status_code)

r = _aplica.get("/api/accesos/descubrir")
chequear("descubrir necesita accesos:ver", r.status_code in (200, 403), r.status_code)
r = _mirar.post("/api/accesos/descubrir/aplicar",
                json={"empleados": [1], "perfil_acceso_id": 1})
chequear("aplicar necesita accesos:asignar", r.status_code == 403, r.status_code)



print("\n=== UNA PUERTA QUE NINGUN PERFIL INCLUYE ===")
# El caso real: una puerta estaba caida al armar los perfiles, quedo fuera de
# todos, y despues volvio. Su gente NO perdio el acceso: la politica todavia no
# la contempla, que es otra cosa y lleva a otra decision.
from sync.plan_accesos import armar_plan

_c7 = sqlite3.connect(DB)
_emp_real = _c7.execute(
    """SELECT user_id FROM empleados WHERE activo=1 AND user_id IS NOT NULL LIMIT 1""").fetchone()[0]
_c7.close()
_uid_real = str(_emp_real).strip()

# p_oficina se saca de todos los perfiles, como si nunca hubiera entrado.
for _pf in (todas, menos_oficina, solo_oficina):
    _actual = cli.get("/api/perfiles-acceso").json()["perfiles"]
    _p = next((x for x in _actual if x["id"] == _pf["id"]), None)
    if _p:
        cli.put(f"/api/perfiles-acceso/{_p['id']}",
                json={"nombre": _p["nombre"], "activo": True, "orden": 0,
                      "dispositivos": [d for d in _p["dispositivos"] if d != p_oficina]})

_puertas = [{"id": p_oficina, "nombre": "Oficina", "ubicacion": None}]
_lect = {p_oficina: {"ok": True, "transporte": "udp", "error": None,
                     "usuarios": [{"user_id": _uid_real}]}}
with db_session() as _cn:
    plan = armar_plan(_cn, _puertas, _lect, {"ok": True, "usuarios": []})

_p0 = plan["puertas"][0]
chequear("marca que ningun perfil incluye esa puerta",
         _p0["en_algun_perfil"] is False, _p0.get("en_algun_perfil"))
_s = next((x for x in _p0["sacar"] if x["user_id"] == _uid_real), None)
chequear("la persona figura para sacar", _s is not None, _p0["sacar"][:3])
chequear("pero el motivo NO es que le sacaron el acceso",
         _s and _s["motivo"] == "sin_politica", _s)
chequear("y el texto lo explica",
         _s and "Ningún perfil incluye" in _s["motivo_texto"], _s)

# Con la puerta en un perfil, el motivo vuelve a ser el de siempre.
_actual = cli.get("/api/perfiles-acceso").json()["perfiles"]
_p = next(x for x in _actual if x["id"] == todas["id"])
cli.put(f"/api/perfiles-acceso/{todas['id']}",
        json={"nombre": _p["nombre"], "activo": True, "orden": 0,
              "dispositivos": sorted(set(_p["dispositivos"]) | {p_oficina})})
with db_session() as _cn:
    plan2 = armar_plan(_cn, _puertas, _lect, {"ok": True, "usuarios": []})
_p1 = plan2["puertas"][0]
chequear("con la puerta en un perfil ya no se marca",
         _p1["en_algun_perfil"] is True, _p1.get("en_algun_perfil"))
_s2 = next((x for x in _p1["sacar"] if x["user_id"] == _uid_real), None)
chequear("y el motivo pasa a ser el de siempre",
         _s2 is None or _s2["motivo"] == "sin_derecho", _s2)



print("\n=== UNA PERSONA CONTRA LOS LECTORES ===")
# La pregunta de todos los dias: "a Fulano le quedo el deposito?". La funcion es
# pura, asi que se prueba sin tocar ningun equipo.
from sync.verificar_acceso import verificar

_eq = [
    {"id": 1, "nombre": "Personal", "ubicacion": None, "es_acceso": 1, "cuenta_asistencia": 0},
    {"id": 2, "nombre": "Oficina",  "ubicacion": None, "es_acceso": 1, "cuenta_asistencia": 0},
    {"id": 3, "nombre": "Camaras",  "ubicacion": None, "es_acceso": 1, "cuenta_asistencia": 0},
    {"id": 4, "nombre": "Caida",    "ubicacion": None, "es_acceso": 1, "cuenta_asistencia": 0},
    {"id": 9, "nombre": "Maestro",  "ubicacion": None, "es_acceso": 0, "cuenta_asistencia": 1},
]


def _lec(*usuarios):
    return {"ok": True, "error": None, "transporte": "udp", "usuarios": list(usuarios)}


_yo = {"uid": 7, "user_id": "42", "nombre": "PEREZ", "grupo": "1", "huellas": 2}
_lecturas = {
    1: _lec(_yo),                                  # le toca y esta, con huella
    2: _lec(dict(_yo, huellas=0)),                 # le toca y esta, SIN huella
    3: _lec({"uid": 3, "user_id": "99", "nombre": "OTRO", "grupo": "0", "huellas": 1}),
    4: {"ok": False, "error": "timeout", "usuarios": []},
    9: _lec(_yo),
}
_v = verificar("42", _eq, _lecturas, {1, 2, 4})
_por_id = {e["id"]: e for e in _v["equipos"]}

chequear("cargado con huella en una puerta que le toca: abre",
         _por_id[1]["estado"] == "abre", _por_id[1])
chequear("cargado SIN huella no se informa como que abre",
         _por_id[2]["estado"] == "sin_huella", _por_id[2])
chequear("y el diagnostico dice que no abre",
         "no abre" in _por_id[2]["diagnostico"], _por_id[2]["diagnostico"])
chequear("el equipo que no contesto queda como sin leer",
         _por_id[4]["estado"] == "sin_leer", _por_id[4])
chequear("y no se cuenta como que falta cargarlo",
         _v["resumen"]["falta"] == 0, _v["resumen"])
chequear("una puerta que no le toca y no lo tiene no molesta",
         _por_id[3]["estado"] == "no_abre", _por_id[3])
chequear("del equipo de asistencia no se opina si deberia o no",
         _por_id[9]["deberia"] is None, _por_id[9])
chequear("pero se informa que la huella esta ahi para copiar",
         _por_id[9]["huellas"] == 2, _por_id[9])
# El maestro no abre puertas. Reusar la etiqueta de las puertas hacia que la
# pantalla dijera que abre un equipo que no abre nada.
chequear("del equipo de asistencia NUNCA se dice que abre",
         _por_id[9]["estado"] == "enrolado", _por_id[9]["estado"])
chequear("y su diagnostico aclara que no abre puertas",
         "no abre puertas" in _por_id[9]["diagnostico"], _por_id[9]["diagnostico"])
chequear("ningun estado de puerta se usa para el equipo de asistencia",
         all(e["estado"] not in ("abre", "no_abre", "falta", "sobra", "sin_huella")
             for e in _v["equipos"] if not e["es_puerta"]),
         [(e["nombre"], e["estado"]) for e in _v["equipos"]])

# Si en el maestro no tiene huella no hay nada que copiar a ninguna puerta: es la
# causa de raiz de que falte en todas, y se avisa como tal.
_v0 = verificar("42", _eq, {**_lecturas, 9: _lec(dict(_yo, huellas=0))}, {1})
_m0 = {e["id"]: e for e in _v0["equipos"]}[9]
chequear("maestro sin huella tiene su propio estado",
         _m0["estado"] == "maestro_sin_huella", _m0["estado"])
chequear("y se marca como la causa de raiz",
         _v0["resumen"]["sin_huella_maestro"] is True, _v0["resumen"])
chequear("y va primero en la lista",
         _v0["equipos"][0]["id"] == 9, [e["id"] for e in _v0["equipos"]])
_v0b = verificar("42", _eq, {**_lecturas, 9: _lec()}, {1})
chequear("no estar enrolado en el maestro tambien se avisa",
         {e["id"]: e["estado"] for e in _v0b["equipos"]}[9] == "no_enrolado"
         and _v0b["resumen"]["sin_huella_maestro"] is True, _v0b["resumen"])
chequear("con huella en el maestro no se avisa nada",
         _v["resumen"]["sin_huella_maestro"] is False, _v["resumen"])
chequear("trae el nombre con el que figura en el equipo",
         _por_id[1]["nombre_en_equipo"] == "PEREZ", _por_id[1])
chequear("y el grupo, de donde el equipo saca sus reglas",
         _por_id[1]["grupo"] == "1", _por_id[1])

# Cargado donde no corresponde: el caso del egresado que sigue abriendo.
_v2 = verificar("42", _eq, {**_lecturas, 3: _lec(_yo)}, {1})
chequear("cargado en una puerta que no le toca: sobra",
         {e["id"]: e["estado"] for e in _v2["equipos"]}[3] == "sobra", _v2["equipos"])
# Sobra en las dos: en Camaras y en Oficina, donde esta cargado y ya no le toca.
# Que en Oficina no tenga huella no lo salva —igual figura y hay que sacarlo.
chequear("cuenta todas las puertas donde sobra", _v2["resumen"]["sobra"] == 2, _v2["resumen"])

# No poder leer las huellas no es lo mismo que no tener huella: confundirlos
# manda a recargar a alguien que estaba bien.
_v3 = verificar("42", _eq, {**_lecturas, 1: _lec(dict(_yo, huellas=None))}, {1})
_f3 = {e["id"]: e for e in _v3["equipos"]}[1]
chequear("huella no leida no se confunde con huella ausente",
         _f3["estado"] == "abre" and _f3["huellas"] is None, _f3)

# Lo que esta mal va primero: la lista se mira de arriba.
_v4 = verificar("42", _eq, _lecturas, {1, 2, 3, 4})
chequear("los problemas se muestran antes que lo que esta bien",
         _v4["equipos"][0]["estado"] in ("falta", "sin_huella"),
         [e["estado"] for e in _v4["equipos"]])
chequear("una puerta que le toca y el equipo no lo tiene: falta",
         {e["id"]: e["estado"] for e in _v4["equipos"]}[3] == "falta", _v4["equipos"])

# El padron de un equipo: la consulta de siempre, ahora con las huellas. Es lo
# que se usaba en Enterprise —"quien esta cargado en esta terminal"— y sin la
# huella dice quien esta cargado, no quien puede abrir.
from sync.lectores import comparar_con_empleados

_emps = {"10": {"id": 1, "nombre": "Ana", "apellido": "Gomez", "activo": 1,
                "tipo": "mensual", "fecha_egreso": None},
         "11": {"id": 2, "nombre": "Luis", "apellido": "Diaz", "activo": 1,
                "tipo": "mensual", "fecha_egreso": None}}
_pad = [{"uid": 1, "user_id": "10", "nombre": "GOMEZ", "privilegio": 0, "tarjeta": 0,
         "grupo": "1", "huellas": 2},
        {"uid": 2, "user_id": "11", "nombre": "DIAZ", "privilegio": 0, "tarjeta": 0,
         "grupo": "1", "huellas": 0}]
_cmp = comparar_con_empleados(_pad, _emps)
chequear("el padron cuenta a los cargados sin huella",
         _cmp["resumen"]["sin_huella"] == 1, _cmp["resumen"])
chequear("y avisa que las huellas se leyeron",
         _cmp["huellas_leidas"] is True, _cmp)
chequear("la huella viaja en cada fila",
         {f["user_id"]: f["huellas"] for f in _cmp["filas"]} == {"10": 2, "11": 0},
         _cmp["filas"])
# Sin pedir las huellas no se puede decir que nadie le falta: seria afirmar que
# todos pueden abrir sin haberlo verificado.
_sin = comparar_con_empleados([{k: v for k, v in u.items() if k != "huellas"}
                               for u in _pad], _emps)
chequear("sin leer las huellas el conteo va en None, no en cero",
         _sin["resumen"]["sin_huella"] is None, _sin["resumen"])
chequear("y lo dice", _sin["huellas_leidas"] is False, _sin)

# El endpoint, con la lectura interceptada para no salir a la red.
_c7 = sqlite3.connect(DB)
_fila = _c7.execute(
    "SELECT id, user_id FROM empleados WHERE activo=1 AND user_id IS NOT NULL LIMIT 1").fetchone()
_c7.close()
_eid, _uid = _fila[0], str(_fila[1]).strip()

_real3 = lectores.leer_padrones
lectores.leer_padrones = lambda ds, con_huellas=False: {
    d["id"]: _lec({"uid": 1, "user_id": _uid, "nombre": "X", "grupo": "0",
                   "huellas": 1 if con_huellas else None})
    for d in ds}
r = cli.get(f"/api/accesos/empleado/{_eid}/en-lectores")
chequear("el endpoint responde 200", r.status_code == 200, r.text[:200])
_d = r.json()
chequear("devuelve la ficha junto con lo leido",
         all(k in _d for k in ("ficha", "equipos", "resumen")), list(_d))
chequear("pide las huellas y no solo el padron",
         all(e["huellas"] == 1 for e in _d["equipos"] if e["cargado"]),
         [(e["nombre"], e["huellas"]) for e in _d["equipos"]])

# Sin numero de dispositivo no hay a quien buscar: se avisa y no se sale a la red.
_c8 = sqlite3.connect(DB)
_c8.execute("UPDATE empleados SET user_id=NULL WHERE id=?", (_eid,))
_c8.commit()
_c8.close()
_salio = {"red": False}


def _no_deberia(ds, con_huellas=False):
    _salio["red"] = True
    return {}


lectores.leer_padrones = _no_deberia
r = cli.get(f"/api/accesos/empleado/{_eid}/en-lectores")
chequear("sin numero de dispositivo avisa en vez de fallar",
         r.status_code == 200 and r.json().get("aviso"), r.text[:200])
chequear("y no sale a la red al vicio", _salio["red"] is False)
lectores.leer_padrones = _real3



print("\n=== EN LAS PUERTAS Y NO EN EL EQUIPO DE ASISTENCIA ===")
# El criterio del usuario: si alguien tiene huella en una puerta y no esta en el
# .201, es una baja que no se propago. La dieron de baja, se sincronizo con el
# maestro, y en las puertas quedo. Es evidencia del propio equipo, asi que sirve
# incluso cuando el legajo ya no dice nada de esa persona.
_pu = [{"id": p_personal, "nombre": "Personal", "ubicacion": None}]
_fantasma = "888777"     # no existe en el sistema: el egresado purgado
_lec_p = {p_personal: {"ok": True, "transporte": "udp", "error": None,
                       "usuarios": [{"user_id": _fantasma}, {"user_id": _uid_real}]}}

# Maestro leido CON huellas: tiene al real, no al fantasma.
_maestro_ok = {"ok": True, "huellas_leidas": True, "usuarios": [
    {"user_id": _uid_real, "huellas": 2}]}
with db_session() as _cn:
    _pl = armar_plan(_cn, _pu, _lec_p, _maestro_ok)

_s_f = next((x for x in _pl["puertas"][0]["sacar"] if x["user_id"] == _fantasma), None)
chequear("marca que no esta en el equipo de asistencia",
         _s_f and _s_f["en_maestro"] is False, _s_f)
chequear("y lo cuenta aparte", _pl["total"]["no_en_maestro"] == 1, _pl["total"])
chequear("avisa que el maestro se pudo leer", _pl["maestro_leido"] is True, _pl)

# Si el maestro no contesto no se puede concluir nada: no es lo mismo "no esta"
# que "no se pudo averiguar". Informarlo como baja mandaria a borrar a alguien.
with db_session() as _cn:
    _pl2 = armar_plan(_cn, _pu, _lec_p, {"ok": False, "usuarios": []})
_s_f2 = next((x for x in _pl2["puertas"][0]["sacar"] if x["user_id"] == _fantasma), None)
chequear("sin leer el maestro no se afirma que falte",
         _s_f2 and _s_f2["en_maestro"] is None, _s_f2)
chequear("y no se cuenta como baja no propagada",
         _pl2["total"]["no_en_maestro"] == 0, _pl2["total"])
chequear("y la pantalla sabe que el maestro no se leyo",
         _pl2["maestro_leido"] is False, _pl2)

# Figurar en el maestro no es tener huella, y antes estaban confundidos: alguien
# cargado ahi sin huella se informaba como que la tenia, y el plan proponia
# copiar algo que no existe.
_c9 = sqlite3.connect(DB)
_otro = _c9.execute(
    """SELECT user_id FROM empleados WHERE activo=1 AND user_id IS NOT NULL
        AND user_id <> ? LIMIT 1""", (_uid_real,)).fetchone()[0]
_c9.close()
_otro = str(_otro).strip()
_r_o = cli.get("/api/perfiles-acceso").json()["perfiles"]
_p_t = next(x for x in _r_o if x["id"] == todas["id"])
cli.put(f"/api/perfiles-acceso/{todas['id']}",
        json={"nombre": _p_t["nombre"], "activo": True, "orden": 0,
              "dispositivos": sorted(set(_p_t["dispositivos"]) | {p_personal})})
_c9 = sqlite3.connect(DB)
_eid_otro = _c9.execute("SELECT id FROM empleados WHERE user_id=?", (_otro,)).fetchone()[0]
_c9.close()
cli.put(f"/api/accesos/empleado/{_eid_otro}/perfil",
        json={"perfil_acceso_id": todas["id"]})

# Esta en el maestro pero SIN huella: hay que cargarlo y no hay nada que copiar.
_maestro_sin = {"ok": True, "huellas_leidas": True, "usuarios": [
    {"user_id": _otro, "huellas": 0}]}
with db_session() as _cn:
    _pl3 = armar_plan(_cn, [{"id": p_personal, "nombre": "Personal", "ubicacion": None}],
                      {p_personal: {"ok": True, "transporte": "udp", "error": None,
                                    "usuarios": []}}, _maestro_sin)
_a3 = next((x for x in _pl3["puertas"][0]["agregar"] if x["user_id"] == _otro), None)
chequear("estar en el maestro sin huella no cuenta como tener huella",
         _a3 and _a3["sin_huella"] is True, _a3)

# Y con huella, no se molesta.
_maestro_con = {"ok": True, "huellas_leidas": True, "usuarios": [
    {"user_id": _otro, "huellas": 1}]}
with db_session() as _cn:
    _pl4 = armar_plan(_cn, [{"id": p_personal, "nombre": "Personal", "ubicacion": None}],
                      {p_personal: {"ok": True, "transporte": "udp", "error": None,
                                    "usuarios": []}}, _maestro_con)
_a4 = next((x for x in _pl4["puertas"][0]["agregar"] if x["user_id"] == _otro), None)
chequear("con huella en el maestro no se marca nada",
         _a4 and _a4["sin_huella"] is False, _a4)

# Sin leer las huellas del maestro no se puede opinar de la huella de nadie.
_maestro_sin_templates = {"ok": True, "usuarios": [{"user_id": _otro}]}
with db_session() as _cn:
    _pl5 = armar_plan(_cn, [{"id": p_personal, "nombre": "Personal", "ubicacion": None}],
                      {p_personal: {"ok": True, "transporte": "udp", "error": None,
                                    "usuarios": []}}, _maestro_sin_templates)
_a5 = next((x for x in _pl5["puertas"][0]["agregar"] if x["user_id"] == _otro), None)
chequear("sin leer las huellas del maestro no se afirma que falten",
         _a5 and _a5["sin_huella"] is False and _pl5["huellas_verificadas"] is False,
         (_a5, _pl5["huellas_verificadas"]))

# Leer varios equipos pidiendo las huellas solo a algunos, en una sola tanda.
_reg = []
lectores.leer_padron = lambda d, con_huellas=False: (
    _reg.append((d["id"], con_huellas)),
    {"ok": True, "transporte": "udp", "usuarios": [], "error": None})[1]
leer_padrones([{"id": 1, "ip": "127.0.0.1", "protocolo": "pull"},
               {"id": 2, "ip": "127.0.0.2", "protocolo": "pull"}],
              con_huellas={2})
chequear("las huellas se piden solo a los equipos indicados",
         sorted(_reg) == [(1, False), (2, True)], _reg)
lectores.leer_padron = _real2



# Hay huella para copiar? Son tres situaciones y la certeza de cada una es
# distinta. Afirmar que a alguien le falta sin haberlo verificado manda a
# enrolar de nuevo a gente que estaba bien.
from sync.plan_accesos import _falta_huella

chequear("no estar en el maestro es certeza sin leer ninguna huella",
         _falta_huella("5", {"1", "2"}, None) == (True, "no_en_maestro"),
         _falta_huella("5", {"1", "2"}, None))
chequear("estar sin huella enrolada se distingue de lo anterior",
         _falta_huella("1", {"1", "2"}, {"2"}) == (True, "sin_template"),
         _falta_huella("1", {"1", "2"}, {"2"}))
chequear("estar y no haber leido los templates no afirma nada",
         _falta_huella("1", {"1", "2"}, None) == (False, None),
         _falta_huella("1", {"1", "2"}, None))
chequear("estar con huella no marca nada",
         _falta_huella("1", {"1"}, {"1"}) == (False, None),
         _falta_huella("1", {"1"}, {"1"}))
chequear("sin poder leer el maestro no se opina",
         _falta_huella("1", None, None) == (False, None),
         _falta_huella("1", None, None))


print("\n=== ASIGNAR PERFILES POR CARGO ===")
# El arranque real: nadie tiene perfil y son cientos de personas. El cargo se usa
# para elegir a quien, y NO queda guardada ninguna relacion cargo -> perfil: eso
# es justo lo que se saco cuando el cargo daba acceso.

r = cli.get("/api/accesos/por-cargo")
chequear("GET por-cargo responde 200", r.status_code == 200, r.text[:200])
_pc = r.json()
chequear("trae los cargos, los perfiles y los totales",
         all(k in _pc for k in ("cargos", "perfiles", "activos", "sin_perfil")), list(_pc))

_c10 = sqlite3.connect(DB)
_activos = _c10.execute("SELECT COUNT(*) FROM empleados WHERE activo=1").fetchone()[0]
_c10.close()
chequear("cuenta a todos los activos una sola vez",
         sum(c["total"] for c in _pc["cargos"]) == _activos,
         (sum(c["total"] for c in _pc["cargos"]), _activos))
chequear("ofrece solo perfiles activos",
         {p["id"] for p in _pc["perfiles"]} <= {todas["id"], menos_oficina["id"], solo_oficina["id"]},
         _pc["perfiles"])

# Un cargo con gente, para probar contra algo real.
_elegido = next((c for c in _pc["cargos"] if c["cargo_id"] is not None and c["sin_perfil"] > 1), None)
chequear("hay un cargo con gente sin perfil para probar", _elegido is not None, _pc["cargos"][:4])

if _elegido:
    _cid, _faltan = _elegido["cargo_id"], _elegido["sin_perfil"]
    r = cli.post("/api/accesos/por-cargo/aplicar",
                 json={"cargo_id": _cid, "perfil_acceso_id": todas["id"]})
    chequear("aplicar responde 200", r.status_code == 200, r.text[:200])
    _res = r.json()
    chequear("asigna a todos los que no tenian perfil",
             _res["asignados"] == _faltan, (_res, _faltan))

    # Idempotente: volver a aplicarlo no cambia nada y no pisa a nadie.
    r2 = cli.post("/api/accesos/por-cargo/aplicar",
                  json={"cargo_id": _cid, "perfil_acceso_id": menos_oficina["id"]})
    chequear("sin pisar, no toca a quien ya tiene perfil",
             r2.json()["asignados"] == 0, r2.json())
    _pc2 = cli.get("/api/accesos/por-cargo").json()
    _e2 = next(c for c in _pc2["cargos"] if c["cargo_id"] == _cid)
    chequear("y el perfil de la gente quedo como estaba",
             [p["id"] for p in _e2["perfiles_actuales"]] == [todas["id"]],
             _e2["perfiles_actuales"])
    chequear("el cargo ya no tiene gente sin perfil", _e2["sin_perfil"] == 0, _e2)

    # Pisar existe porque el caso real es asignarle el perfil equivocado a un
    # cargo entero. Sin eso, el unico camino serian treinta legajos de a uno.
    r3 = cli.post("/api/accesos/por-cargo/aplicar",
                  json={"cargo_id": _cid, "perfil_acceso_id": menos_oficina["id"],
                        "pisar": True})
    chequear("con pisar corrige el perfil equivocado",
             r3.json()["asignados"] == _elegido["total"], (r3.json(), _elegido["total"]))
    chequear("y dice a cuantos les cambio el que ya tenian",
             r3.json()["pisados"] == _faltan, r3.json())
    _pc3 = cli.get("/api/accesos/por-cargo").json()
    _e3 = next(c for c in _pc3["cargos"] if c["cargo_id"] == _cid)
    chequear("la gente quedo con el perfil nuevo",
             [p["id"] for p in _e3["perfiles_actuales"]] == [menos_oficina["id"]],
             _e3["perfiles_actuales"])

    # Lo que NO tiene que pasar: que quede guardada una relacion cargo->perfil.
    # Si quedara, cambiarle el cargo a alguien le cambiaria las puertas, y eso lo
    # puede hacer quien edita empleados sin permiso de accesos.
    _c11 = sqlite3.connect(DB)
    _cols = {c[1] for c in _c11.execute("PRAGMA table_info(cargos)").fetchall()}
    _c11.close()
    chequear("la tabla cargos no guarda ningun perfil",
             not any("perfil" in c for c in _cols), _cols)

# Un egresado no recibe perfil: no tiene que abrir nada.
_c12 = sqlite3.connect(DB)
_baja = _c12.execute(
    """SELECT id, cargo_id FROM empleados WHERE activo=0 AND cargo_id IS NOT NULL
        LIMIT 1""").fetchone()
_c12.close()
if _baja:
    cli.post("/api/accesos/por-cargo/aplicar",
             json={"cargo_id": _baja[1], "perfil_acceso_id": todas["id"], "pisar": True})
    _c12 = sqlite3.connect(DB)
    _q = _c12.execute("SELECT perfil_acceso_id FROM empleados WHERE id=?", (_baja[0],)).fetchone()[0]
    _c12.close()
    chequear("a un egresado no se le asigna perfil", _q is None, _q)

# "Sin cargo" es un grupo real: gente que existe y no entra en ningun cargo.
_pc4 = cli.get("/api/accesos/por-cargo").json()
_sin = next((c for c in _pc4["cargos"] if c["cargo_id"] is None), None)
if _sin and _sin["sin_perfil"]:
    r5 = cli.post("/api/accesos/por-cargo/aplicar",
                  json={"cargo_id": None, "perfil_acceso_id": solo_oficina["id"]})
    chequear("se les puede asignar a los que no tienen cargo",
             r5.status_code == 200 and r5.json()["asignados"] == _sin["sin_perfil"],
             (r5.status_code, r5.text[:160]))

# Rechazos.
r = cli.post("/api/accesos/por-cargo/aplicar",
             json={"cargo_id": 999999, "perfil_acceso_id": todas["id"]})
chequear("un cargo inexistente se rechaza", r.status_code == 400, r.status_code)
r = cli.post("/api/accesos/por-cargo/aplicar",
             json={"cargo_id": None, "perfil_acceso_id": 999999})
chequear("un perfil inexistente se rechaza", r.status_code == 400, r.status_code)
# Nunca deja a nadie sin perfil: quitarlo en masa borraria las excepciones de
# cada uno, y eso necesita permiso de excepciones.
r = cli.post("/api/accesos/por-cargo/aplicar",
             json={"cargo_id": None, "perfil_acceso_id": None})
chequear("no se puede usar para dejar a un cargo sin perfil",
         r.status_code == 422, r.status_code)

# Permisos: ver para mirar la foto, asignar para aplicarla.
chequear("con accesos:ver se puede mirar la foto por cargo",
         _mirar.get("/api/accesos/por-cargo").status_code == 200)
chequear("pero no aplicarla",
         _mirar.post("/api/accesos/por-cargo/aplicar",
                     json={"cargo_id": None, "perfil_acceso_id": todas["id"]}).status_code == 403)
chequear("con accesos:asignar si",
         _aplica.post("/api/accesos/por-cargo/aplicar",
                      json={"cargo_id": None, "perfil_acceso_id": todas["id"]}).status_code == 200)


print("\n=== COMPARAR EL NOMBRE DEL LECTOR CONTRA EL LEGAJO ===")
# El equipo corta el nombre, asi que casi nunca es igual al del legajo. Marcar
# cada corte como sospecha manda a investigar decenas de casos que no lo son, y
# una pantalla que marca cosas que no son problemas deja de mirarse.
from sync.lectores import _mismo_nombre

# Lo que NO se marca: el mismo nombre escrito como lo escribe un equipo viejo.
for _lector, _legajo, _por_que in [
    ("GOMEZ", "Gomez, Ana Maria", "apellido solo"),
    ("GOMEZ, A", "Gomez, Ana Maria", "cortado a la mitad del nombre"),
    ("GOMEZ CA", "Gomez Castro, Ana", "cortado a la mitad del apellido"),
    ("PEREZ", "Perez, Juan", "sin acento contra un legajo con acento"),
    ("ANA GOMEZ", "Gomez, Ana", "nombre y apellido al reves"),
    ("G", "Gomez, Ana", "cortado a una letra"),
    ("ANA MARIA", "Gomez, Ana Maria", "solo los nombres de pila"),
    ("", "Gomez, Ana", "el equipo no guardo nombre"),
    ("GOMEZ", "", "el legajo no tiene nombre"),
]:
    chequear(f"no marca: {_por_que}", _mismo_nombre(_lector, _legajo),
             (_lector, _legajo))

# Lo que SI se marca: otro nombre. Es el caso que importa —un numero reutilizado
# con el empleado anterior todavia cargado en el equipo.
for _lector, _legajo, _por_que in [
    ("PEREZ", "Gomez, Ana", "otro apellido"),
    ("GOMEZ, LUIS", "Gomez, Ana", "mismo apellido, otra persona"),
    ("RODRIGUEZ", "Gomez, Ana Maria", "nada que ver"),
]:
    chequear(f"marca: {_por_que}", not _mismo_nombre(_lector, _legajo),
             (_lector, _legajo))

# Y no depende de adivinar el ancho del campo: el metodo viejo media el nombre
# mas largo del equipo y solo perdonaba el corte si medía exactamente eso, asi
# que cualquier nombre cortado a otro largo quedaba marcado.
_emps_n = {"10": {"id": 1, "nombre": "Ana", "apellido": "Gomez", "activo": 1,
                  "tipo": "mensual", "fecha_egreso": None},
           "11": {"id": 2, "nombre": "Juan", "apellido": "Perez", "activo": 1,
                  "tipo": "mensual", "fecha_egreso": None}}
_pad_n = [{"uid": 1, "user_id": "10", "nombre": "GOM", "privilegio": 0, "tarjeta": 0,
           "grupo": "1"},
          {"uid": 2, "user_id": "11", "nombre": "PEREZ, JUAN", "privilegio": 0,
           "tarjeta": 0, "grupo": "1"}]
_cmp_n = comparar_con_empleados(_pad_n, _emps_n)
chequear("un corte de otro largo tampoco se marca",
         _cmp_n["resumen"]["nombre_distinto"] == 0,
         [(f["nombre"], f["nombre_distinto"]) for f in _cmp_n["filas"]])

print(f"\n{'='*52}\n  {ok} pasaron, {fallos} fallaron\n{'='*52}")
raise SystemExit(1 if fallos else 0)
