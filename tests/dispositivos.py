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


print("\n=== CARGADOS EN UN EQUIPO: comparacion contra los empleados ===")
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
CARGADOS = [
    {"uid": 1, "user_id": "10", "nombre": "Perez",    "privilegio": 0, "tarjeta": 0, "grupo": "1"},
    {"uid": 2, "user_id": "11", "nombre": "Gomez Ru", "privilegio": 0, "tarjeta": 0, "grupo": "1"},
    {"uid": 3, "user_id": "12", "nombre": "Torres",   "privilegio": 0, "tarjeta": 0, "grupo": "1"},
    {"uid": 4, "user_id": "99", "nombre": "Fantasma", "privilegio": 0, "tarjeta": 0, "grupo": "0"},
    {"uid": 5, "user_id": "13", "nombre": "VIEJO",    "privilegio": 0, "tarjeta": 0, "grupo": "1"},
]
res = comparar_con_empleados(CARGADOS, EMPLEADOS)
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
chequear("un lista vacio no rompe",
         comparar_con_empleados([], EMPLEADOS)["resumen"]["total"] == 0)

print("\n=== CARGADOS EN UN EQUIPO: endpoint ===")
import sync.lectores as lectores

_real = lectores.leer_cargados
lectores.leer_cargados = lambda d, **kw: {"ok": True, "transporte": "udp",
                                  "usuarios": CARGADOS, "error": None}
r = cli.get(f"/api/dispositivos/{puerta['id']}/cargados")
chequear("GET lista responde 200", r.status_code == 200, r.text[:160])
cuerpo = r.json() if r.status_code == 200 else {}
chequear("informa por que transporte contesto", cuerpo.get("transporte") == "udp", cuerpo)
chequear("devuelve resumen y filas", "resumen" in cuerpo and "filas" in cuerpo, list(cuerpo))
chequear("deja constancia de que el equipo contesto",
         any(x["visto_en"] for x in cli.get("/api/dispositivos").json()
             if x["id"] == puerta["id"]))

lectores.leer_cargados = lambda d, **kw: {"ok": False, "transporte": None, "usuarios": [],
                                  "error": "ZKNetworkError: timed out"}
r = cli.get(f"/api/dispositivos/{puerta['id']}/cargados")
chequear("un equipo que no contesta devuelve ok=false, no un error 500",
         r.status_code == 200 and r.json()["ok"] is False, r.text[:160])
lectores.leer_cargados = _real

if push_id:
    r = cli.get(f"/api/dispositivos/{push_id}/cargados")
    chequear("a un push le avisa que no se puede consultar asi",
             r.status_code == 200 and r.json()["ok"] is False, r.text[:200])

r = cli.get("/api/dispositivos/999999/cargados")
chequear("lista de un id inexistente da 404", r.status_code == 404, r.text[:120])



print("\n=== REVISION DE TODOS LOS LECTORES ===")
import sync.lectores as lectores

# Un equipo contesta con problemas, el otro esta caido: el caido no tiene que
# impedir que se vea lo del primero.
_real2 = lectores.leer_cargados


# El endpoint cruza contra los empleados REALES de la copia, no contra la lista
# de arriba. Para que haya al menos una fila sin novedad hace falta alguien que
# exista de verdad, con su apellido tal cual esta en el legajo.
_con = sqlite3.connect(DB)
_real_emp = _con.execute(
    """SELECT user_id, apellido FROM empleados
        WHERE activo = 1 AND user_id IS NOT NULL AND apellido <> '' LIMIT 1"""
).fetchone()
_con.close()
CARGADOS_REV = list(CARGADOS)
if _real_emp:
    CARGADOS_REV.append({"uid": 9, "user_id": str(_real_emp[0]).strip(),
                       "nombre": str(_real_emp[1]).strip()[:8],
                       "privilegio": 0, "tarjeta": 0, "grupo": "1"})


def _falso(d, **kw):
    if d["ip"] == "127.0.0.2":   # la Puerta deposito que se creo mas arriba
        return {"ok": True, "transporte": "udp", "usuarios": CARGADOS_REV, "error": None}
    return {"ok": False, "transporte": None, "usuarios": [],
            "error": "ZKNetworkError: timed out"}


lectores.leer_cargados = _falso
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
chequear("solo devuelve lo que hay que mirar, no el lista entero",
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

lectores.leer_cargados = _real2

print("\n=== LECTURA EN PARALELO ===")
import time
from sync.lectores import leer_cargados_varios

_lento = lambda d, **kw: (time.sleep(0.4), {"ok": True, "transporte": "tcp",
                                      "usuarios": [], "error": None})[1]
lectores.leer_cargados = _lento
equipos = [{"id": i, "ip": f"127.0.0.{i}", "protocolo": "pull"} for i in range(1, 6)]
arranque = time.time()
res_par = leer_cargados_varios(equipos)
tardanza = time.time() - arranque
chequear("devuelve un resultado por equipo", len(res_par) == 5, len(res_par))
chequear("los lee en paralelo y no de a uno",
         tardanza < 1.2, f"tardo {tardanza:.2f}s; de a uno serian 2s")
chequear("una lista vacia no rompe", leer_cargados_varios([]) == {})
lectores.leer_cargados = _real2



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
chequear("sistema tiene las seis acciones de accesos",
         sorted(p["accion"] for p in me2["permisos"] if p["modulo"] == "accesos")
         == ["aplicar", "asignar", "editar", "eliminar", "excepcion", "ver"],
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
_guardado = _lec.leer_cargados_varios
_lec.leer_cargados_varios = lambda ds, **kw: {d["id"]: {"ok": True, "transporte": "udp",
                                           "usuarios": [], "error": None} for d in ds}
r = cli.get("/api/accesos/plan")
chequear("GET plan responde 200", r.status_code == 200, r.text[:200])
cuerpo = r.json() if r.status_code == 200 else {}
chequear("trae total y puertas", "total" in cuerpo and "puertas" in cuerpo, list(cuerpo))
_lec.leer_cargados_varios = _guardado

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


print("\n=== TENER O NO TENER PERFIL ===")
# Antes habia una lista de «pendientes de asignar» y esto la probaba. Se saco:
# era un listado permanente de gente que legitimamente no abre nada, y el error
# que buscaba --alguien entro y nadie le asigno acceso-- se resuelve solo el dia
# que esa persona lo necesita. Lo que importa comprobar sigue siendo esto: sin
# perfil no abre nada, con perfil abre lo que el perfil dice.
if emp_id:
    cli.put(f"/api/accesos/empleado/{emp_id}/perfil", json={"perfil_acceso_id": None})
    _ficha = cli.get(f"/api/accesos/empleado/{emp_id}").json()
    chequear("sin perfil asignado no abre ninguna puerta",
             _ficha["perfil"] is None and _ficha["puertas"] == [], _ficha["puertas"])

    cli.put(f"/api/accesos/empleado/{emp_id}/perfil",
            json={"perfil_acceso_id": menos_oficina["id"]})
    _ficha = cli.get(f"/api/accesos/empleado/{emp_id}").json()
    chequear("al asignarle el perfil pasa a tener puertas",
             _ficha["perfil"] is not None and _ficha["puertas"], _ficha)


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
chequear("sistema tiene las seis acciones de accesos",
         sorted(p["accion"] for p in me3["permisos"] if p["modulo"] == "accesos")
         == ["aplicar", "asignar", "editar", "eliminar", "excepcion", "ver"],
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
_g = _lec2.leer_cargados_varios
_lec2.leer_cargados_varios = lambda ds, **kw: {x["id"]: {"ok": True, "transporte": "udp",
                                            "usuarios": [], "error": None} for x in ds}
plan = cli.get("/api/accesos/plan").json()
_lec2.leer_cargados_varios = _g

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
_lec2.leer_cargados_varios = lambda ds, **kw: {x["id"]: {"ok": True, "transporte": "udp",
                                            "usuarios": [], "error": None} for x in ds}
plan2 = cli.get("/api/accesos/plan").json()
_lec2.leer_cargados_varios = _g
chequear("al reactivarla vuelve al plan",
         any(x["id"] == p_oficina for x in plan2["puertas"]),
         [x["nombre"] for x in plan2["puertas"]])
chequear("y sale del aviso", all(f["id"] != p_oficina for f in plan2.get("fuera_de_plan", [])),
         plan2.get("fuera_de_plan"))



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
# Deja de ser «ningun perfil incluye esta puerta» y pasa a ser algo sobre esa
# persona. Cual de los dos depende de si tiene perfil: «su perfil no la
# incluye» es una decision tomada, «todavia no tiene perfil» es una que falta.
chequear("y el motivo pasa a hablar de la persona, no de la puerta",
         _s2 is None or _s2["motivo"] in ("sin_derecho", "sin_perfil"), _s2)

# Y los dos casos se distinguen, que es lo que importa: «su perfil no la
# incluye» es una decision tomada; «todavia no tiene perfil» es una que falta, y
# sacar a alguien por eso seria dejar afuera a quien entro la semana pasada.
if _s2 is not None:
    _cnp = sqlite3.connect(DB)
    _tiene = _cnp.execute("SELECT perfil_acceso_id FROM empleados WHERE id=?",
                          (_s2["empleado_id"],)).fetchone()[0]
    _cnp.close()
    chequear("sin perfil asignado el motivo lo dice asi",
             _s2["motivo"] == ("sin_derecho" if _tiene else "sin_perfil"),
             (_tiene, _s2["motivo"]))

    # Y se ejecuta: en este local no tener perfil es una decision normal, no una
    # que falta. Frenarlo dejaba sin aplicar el caso mas comun.
    from api.accesos import MOTIVOS_QUE_SE_APLICAN as _MQA
    chequear("y aun asi se saca, porque no tener perfil es no abrir nada",
             _MQA["sin_perfil"] is True, _MQA)
    chequear("lo unico que no se ejecuta es la puerta sin politica",
             _MQA["sin_politica"] is False, _MQA)



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
# Las dos situaciones del maestro van por separado. Antes compartian una sola
# bandera y el cartel decia "no tiene huella" de alguien que ni siquiera estaba
# cargado: las dos impiden copiar a una puerta, pero se arreglan al reves.
chequear("no estar enrolado en el maestro se avisa aparte",
         {e["id"]: e["estado"] for e in _v0b["equipos"]}[9] == "no_enrolado"
         and _v0b["resumen"]["no_en_maestro"] is True, _v0b["resumen"])
chequear("y no se lo confunde con estar cargado sin huella",
         _v0b["resumen"]["sin_huella_maestro"] is False, _v0b["resumen"])
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

# El lista de un equipo: la consulta de siempre, ahora con las huellas. Es lo
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
chequear("el lista cuenta a los cargados sin huella",
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

_real3 = lectores.leer_cargados_varios
lectores.leer_cargados_varios = lambda ds, **kw: {
    d["id"]: _lec({"uid": 1, "user_id": _uid, "nombre": "X", "grupo": "0",
                   "huellas": 1 if kw.get("con_huellas") else None})
    for d in ds}
r = cli.get(f"/api/accesos/empleado/{_eid}/en-lectores")
chequear("el endpoint responde 200", r.status_code == 200, r.text[:200])
_d = r.json()
chequear("devuelve la ficha junto con lo leido",
         all(k in _d for k in ("ficha", "equipos", "resumen")), list(_d))
chequear("pide las huellas y no solo el lista",
         all(e["huellas"] == 1 for e in _d["equipos"] if e["cargado"]),
         [(e["nombre"], e["huellas"]) for e in _d["equipos"]])

# Sin numero de dispositivo no hay a quien buscar: se avisa y no se sale a la red.
_c8 = sqlite3.connect(DB)
_c8.execute("UPDATE empleados SET user_id=NULL WHERE id=?", (_eid,))
_c8.commit()
_c8.close()
_salio = {"red": False}


def _no_deberia(ds, **kw):
    _salio["red"] = True
    return {}


lectores.leer_cargados_varios = _no_deberia
r = cli.get(f"/api/accesos/empleado/{_eid}/en-lectores")
chequear("sin numero de dispositivo avisa en vez de fallar",
         r.status_code == 200 and r.json().get("aviso"), r.text[:200])
chequear("y no sale a la red al vicio", _salio["red"] is False)
lectores.leer_cargados_varios = _real3



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
lectores.leer_cargados = lambda d, **kw: (
    _reg.append((d["id"], bool(kw.get("con_huellas")))),
    {"ok": True, "transporte": "udp", "usuarios": [], "error": None})[1]
leer_cargados_varios([{"id": 1, "ip": "127.0.0.1", "protocolo": "pull"},
               {"id": 2, "ip": "127.0.0.2", "protocolo": "pull"}],
              con_huellas={2})
chequear("las huellas se piden solo a los equipos indicados",
         sorted(_reg) == [(1, False), (2, True)], _reg)
lectores.leer_cargados = _real2



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


print("\n=== FRANJA HORARIA POR USUARIO ===")
# Importa antes de escribir nada: agregarle una huella a alguien reenvia su
# registro completo —el protocolo los manda juntos— y pyzk escribe la franja en
# cero, siempre. Si nadie la usa no hay nada que perder; si alguien la usa, hay
# que resolverlo antes.
_emps_f = {"10": {"id": 1, "nombre": "Ana", "apellido": "Gomez", "activo": 1,
                  "tipo": "mensual", "fecha_egreso": None},
           "11": {"id": 2, "nombre": "Luis", "apellido": "Diaz", "activo": 1,
                  "tipo": "mensual", "fecha_egreso": None}}
_pad_f = [{"uid": 1, "user_id": "10", "nombre": "GOMEZ", "grupo": "1", "franja": 0},
          {"uid": 2, "user_id": "11", "nombre": "DIAZ", "grupo": "1", "franja": 3}]
_cmp_f = comparar_con_empleados(_pad_f, _emps_f)
chequear("cuenta a quien tiene franja horaria propia",
         _cmp_f["resumen"]["con_franja"] == 1, _cmp_f["resumen"])
chequear("franja cero no cuenta como tenerla",
         _cmp_f["resumen"]["con_franja"] == 1, _cmp_f["resumen"])
chequear("avisa que las franjas se leyeron",
         _cmp_f["franjas_leidas"] is True, _cmp_f["franjas_leidas"])

# Si no se leyeron, el conteo va en None. No saber no es lo mismo que no haber:
# informar "nadie tiene franja" sin haberlo leido autoriza a escribir a ciegas.
_cmp_f2 = comparar_con_empleados(
    [{k: v for k, v in u.items() if k != "franja"} for u in _pad_f], _emps_f)
chequear("sin leerlas el conteo va en None, no en cero",
         _cmp_f2["resumen"]["con_franja"] is None, _cmp_f2["resumen"])
chequear("y lo dice", _cmp_f2["franjas_leidas"] is False, _cmp_f2["franjas_leidas"])

# Un equipo que contesto pero no supo dar las franjas: viene en None por usuario
# y tampoco se cuenta como que nadie la tiene.
_cmp_f3 = comparar_con_empleados(
    [dict(u, franja=None) for u in _pad_f], _emps_f)
chequear("franja que no se pudo leer no cuenta como cero",
         _cmp_f3["resumen"]["con_franja"] is None, _cmp_f3["resumen"])

# El desempaquetado usa el mismo formato con el que pyzk lee el lista, asi que
# tiene que devolver lo mismo que el propio pyzk saca del paquete.
from struct import pack, unpack
_crudo = pack("<HB5s8sIxBhI", 7, 0, b"", b"PEREZ", 0, 1, 5, 42)
_u = unpack("<HB5s8sIxBhI", _crudo)
chequear("el formato de 28 bytes ubica la franja donde se espera",
         len(_crudo) == 28 and _u[0] == 7 and _u[5] == 1 and _u[6] == 5 and _u[7] == 42,
         _u)


print("\n=== QUIEN ADMINISTRA EL LECTOR ===")
# En Enterprise se le da a ciertas personas permiso para administrar el equipo:
# crear usuarios y tomar huellas parado frente a el. Es el campo `privilege`, y
# es una propiedad del equipo, no del legajo: no se ve en ningun otro lado.
_emps_n2 = {"10": {"id": 1, "nombre": "Ana", "apellido": "Gomez", "activo": 1,
                   "tipo": "mensual", "fecha_egreso": None},
            "11": {"id": 2, "nombre": "Luis", "apellido": "Diaz", "activo": 1,
                   "tipo": "mensual", "fecha_egreso": None},
            "12": {"id": 3, "nombre": "Eva", "apellido": "Ruiz", "activo": 1,
                   "tipo": "mensual", "fecha_egreso": None}}
_pad_n3 = [{"uid": 1, "user_id": "10", "nombre": "GOMEZ", "grupo": "1", "privilegio": 0},
           {"uid": 2, "user_id": "11", "nombre": "DIAZ", "grupo": "1", "privilegio": 2},
           {"uid": 3, "user_id": "12", "nombre": "RUIZ", "grupo": "1", "privilegio": 14}]
_cmp_n3 = comparar_con_empleados(_pad_n3, _emps_n2)
_por_num = {f["user_id"]: f for f in _cmp_n3["filas"]}
chequear("traduce el nivel a algo legible",
         _por_num["11"]["nivel"] == "enrolador"
         and _por_num["12"]["nivel"] == "super admin", 
         [(f["user_id"], f["nivel"]) for f in _cmp_n3["filas"]])
chequear("el usuario comun tambien se informa",
         _por_num["10"]["nivel"] == "usuario comun".replace("comun", "común"),
         _por_num["10"]["nivel"])
chequear("cuenta cuantos administran el lector",
         _cmp_n3["resumen"]["administran"] == 2, _cmp_n3["resumen"])

# Un nivel que el equipo devuelva y no conozcamos no se inventa.
_cmp_n4 = comparar_con_empleados(
    [{"uid": 9, "user_id": "10", "nombre": "GOMEZ", "grupo": "1", "privilegio": 7}],
    _emps_n2)
chequear("un nivel desconocido no se traduce a cualquier cosa",
         _cmp_n4["filas"][0]["nivel"] is None, _cmp_n4["filas"][0]["nivel"])
chequear("pero se cuenta igual: algo distinto de cero administra",
         _cmp_n4["resumen"]["administran"] == 1, _cmp_n4["resumen"])


print("\n=== NIVEL DE ADMINISTRACION DEL LECTOR ===")
# Es una sola propiedad de la persona y no una por equipo: las puertas no tienen
# pantalla, asi que ahi no hay nada que administrar. Vale para el .201.
if emp_id:
    r = cli.get(f"/api/accesos/empleado/{emp_id}")
    chequear("la ficha trae el nivel y el catalogo",
             "nivel_lector" in r.json()["empleado"] and r.json()["niveles_lector"],
             list(r.json()))
    chequear("arranca en cero", r.json()["empleado"]["nivel_lector"] == 0,
             r.json()["empleado"]["nivel_lector"])

    r = cli.put(f"/api/accesos/empleado/{emp_id}/nivel-lector", json={"nivel_lector": 2})
    chequear("se puede poner enrolador", r.status_code == 200, r.text[:160])
    chequear("y queda guardado",
             cli.get(f"/api/accesos/empleado/{emp_id}").json()["empleado"]["nivel_lector"] == 2)

    r = cli.put(f"/api/accesos/empleado/{emp_id}/nivel-lector", json={"nivel_lector": 6})
    chequear("y administrador", r.status_code == 200, r.text[:160])

    # 14 existe en el equipo pero no se ofrece: no se usa, y una opcion que nadie
    # necesita en una lista de permisos se elige por error.
    r = cli.put(f"/api/accesos/empleado/{emp_id}/nivel-lector", json={"nivel_lector": 14})
    chequear("un nivel que no se usa se rechaza", r.status_code == 422, r.status_code)
    r = cli.put(f"/api/accesos/empleado/{emp_id}/nivel-lector", json={"nivel_lector": 99})
    chequear("y uno inventado tambien", r.status_code == 422, r.status_code)

    # Pide el permiso mas fuerte: un enrolador puede dar de alta a cualquiera y
    # tomarle la huella, o sea crear identidades.
    chequear("con accesos:asignar NO alcanza",
             _aplica.put(f"/api/accesos/empleado/{emp_id}/nivel-lector",
                         json={"nivel_lector": 2}).status_code == 403)
    chequear("con accesos:ver tampoco",
             _mirar.put(f"/api/accesos/empleado/{emp_id}/nivel-lector",
                        json={"nivel_lector": 2}).status_code == 403)
    cli.put(f"/api/accesos/empleado/{emp_id}/nivel-lector", json={"nivel_lector": 0})

r = cli.put("/api/accesos/empleado/999999/nivel-lector", json={"nivel_lector": 0})
chequear("un empleado inexistente da 404", r.status_code == 404, r.status_code)


print("\n=== ADOPTAR LOS NIVELES QUE EL EQUIPO YA TIENE ===")
# El cero del legajo no significa "no administra": significa que nadie lo
# decidio. Si el sistema escribiera desde ahi, le sacaria el permiso a todos los
# que hoy lo tienen. Por eso el equipo gana UNA vez, al adoptar.
_c13 = sqlite3.connect(DB)
_tres = _c13.execute(
    """SELECT id, user_id FROM empleados
        WHERE activo=1 AND user_id IS NOT NULL LIMIT 3""").fetchall()
_c13.close()

if len(_tres) >= 2:
    (_e1, _u1), (_e2, _u2) = (_tres[0][0], str(_tres[0][1]).strip()), \
                             (_tres[1][0], str(_tres[1][1]).strip())
    _cargados_maestro = {"ok": True, "transporte": "tcp", "error": None, "usuarios": [
        {"uid": 1, "user_id": _u1, "nombre": "UNO", "privilegio": 6,
         "tarjeta": 0, "grupo": "1"},
        {"uid": 2, "user_id": _u2, "nombre": "DOS", "privilegio": 0,
         "tarjeta": 0, "grupo": "1"},
        {"uid": 3, "user_id": "777777", "nombre": "AJENO", "privilegio": 2,
         "tarjeta": 0, "grupo": "1"},
    ]}
    _real4 = lectores.leer_cargados
    lectores.leer_cargados = lambda d, **kw: _cargados_maestro

    r = cli.get("/api/accesos/niveles-lector")
    chequear("GET niveles-lector responde 200", r.status_code == 200, r.text[:200])
    _nl = r.json()
    _dif = {f["empleado_id"]: f for f in _nl["diferencias"]}
    chequear("muestra al que el equipo tiene con nivel y el legajo no",
             _e1 in _dif and _dif[_e1]["en_el_equipo"] == 6
             and _dif[_e1]["en_el_legajo"] == 0, _nl["diferencias"])
    chequear("al que ya coincide no se le propone cambiar el nivel",
             _e2 not in _dif or _dif[_e2]["difiere_nivel"] is False, _nl["diferencias"])
    chequear("los que no estan en el sistema van aparte",
             any(a["user_id"] == "777777" for a in _nl["ajenos"]), _nl["ajenos"])

    # Importar es a mano y de a quien se elija: adoptar en bloque una lista que
    # nadie reviso hace anios es heredar justo los permisos que habria que sacar.
    r = cli.post("/api/accesos/niveles-lector/importar", json={"empleados": [_e1]})
    chequear("importar responde 200", r.status_code == 200, r.text[:200])
    chequear("y copia el nivel del equipo", r.json()["importados"] == 1, r.json())
    chequear("el legajo quedo con el nivel del equipo",
             cli.get(f"/api/accesos/empleado/{_e1}").json()["empleado"]["nivel_lector"] == 6)

    # Ya importado, deja de ser una diferencia: el sistema y el equipo coinciden.
    _nl2 = cli.get("/api/accesos/niveles-lector").json()
    _d2 = {f["empleado_id"]: f for f in _nl2["diferencias"]}
    chequear("ya no se le propone cambiar el nivel",
             _e1 not in _d2 or _d2[_e1]["difiere_nivel"] is False, _nl2["diferencias"])

    # Un nivel que no esta entre los que se usan no entra al legajo: seria un
    # valor que despues nadie puede elegir ni corregir desde la pantalla.
    _cargados_maestro["usuarios"][1]["privilegio"] = 14
    r = cli.post("/api/accesos/niveles-lector/importar", json={"empleados": [_e2]})
    chequear("un nivel que no se usa no se importa",
             r.json()["importados"] == 0 and r.json()["sin_tocar"] == 1, r.json())

    chequear("importar necesita accesos:editar",
             _aplica.post("/api/accesos/niveles-lector/importar",
                          json={"empleados": [_e1]}).status_code == 403)
    chequear("mirar alcanza con accesos:ver",
             _mirar.get("/api/accesos/niveles-lector").status_code == 200)

    r = cli.post("/api/accesos/niveles-lector/importar", json={"empleados": []})
    chequear("sin empleados se rechaza", r.status_code == 400, r.status_code)

    cli.put(f"/api/accesos/empleado/{_e1}/nivel-lector", json={"nivel_lector": 0})
    lectores.leer_cargados = _real4


print("\n=== NOMBRE QUE MUESTRA EL LECTOR ===")
# Es el texto que el equipo muestra al apoyar el dedo. No es el nombre del
# legajo: el de asistencia guarda 24 caracteres y las puertas 8, asi que un
# nombre completo llega cortado y deja de identificar a nadie.
if emp_id:
    r = cli.get(f"/api/accesos/empleado/{emp_id}")
    chequear("la ficha trae el nombre del lector y los largos",
             "nombre_lector" in r.json()["empleado"]
             and r.json()["largo_nombre"]["puertas"] == 8, list(r.json()))
    chequear("arranca vacio", r.json()["empleado"]["nombre_lector"] is None,
             r.json()["empleado"]["nombre_lector"])

    r = cli.put(f"/api/accesos/empleado/{emp_id}/nombre-lector",
                json={"nombre_lector": "GOMEZ A"})
    chequear("se puede poner", r.status_code == 200, r.text[:160])
    chequear("y queda guardado",
             cli.get(f"/api/accesos/empleado/{emp_id}").json()["empleado"]["nombre_lector"] == "GOMEZ A")

    # Vacio no es un nombre vacio: significa "no opinamos", y al escribir se
    # respeta el que el equipo ya tenga. Si guardara "" le borraria el nombre.
    r = cli.put(f"/api/accesos/empleado/{emp_id}/nombre-lector", json={"nombre_lector": "   "})
    chequear("solo espacios se guarda como vacio, no como nombre",
             r.status_code == 200
             and cli.get(f"/api/accesos/empleado/{emp_id}").json()["empleado"]["nombre_lector"] is None,
             r.text[:160])

    # Mas largo de lo que el equipo guarda se rechaza en vez de recortarse solo:
    # recortar en silencio deja un nombre que nadie eligio.
    r = cli.put(f"/api/accesos/empleado/{emp_id}/nombre-lector",
                json={"nombre_lector": "X" * 25})
    chequear("mas de 24 caracteres se rechaza", r.status_code == 422, r.status_code)
    r = cli.put(f"/api/accesos/empleado/{emp_id}/nombre-lector",
                json={"nombre_lector": "X" * 24})
    chequear("exactamente 24 entra", r.status_code == 200, r.status_code)

    chequear("necesita accesos:asignar",
             _mirar.put(f"/api/accesos/empleado/{emp_id}/nombre-lector",
                        json={"nombre_lector": "A"}).status_code == 403)
    chequear("y con asignar alcanza",
             _aplica.put(f"/api/accesos/empleado/{emp_id}/nombre-lector",
                         json={"nombre_lector": "A"}).status_code == 200)
    cli.put(f"/api/accesos/empleado/{emp_id}/nombre-lector", json={"nombre_lector": None})


print("\n=== PASADAS POR UN LECTOR ===")
# Quien apoyo el dedo y a que hora. Se lee del equipo en el momento y no se
# guarda nada: el lector conserva miles, mucho mas de lo que hace falta.
from datetime import datetime, timedelta
import sync.lectores as _lec_p

_c14 = sqlite3.connect(DB)
_fila_p = _c14.execute(
    "SELECT user_id FROM empleados WHERE activo=1 AND user_id IS NOT NULL LIMIT 1").fetchone()
_c14.close()
_uid_p = str(_fila_p[0]).strip()

_hoy = datetime.now()
_falsos = {"ok": True, "transporte": "udp", "error": None, "total": 3, "registros": [
    {"user_id": _uid_p, "fecha": _hoy.strftime("%Y-%m-%d"), "hora": "08:02:11",
     "timestamp": _hoy.strftime("%Y-%m-%d 08:02:11"), "estado": 0, "punch": 0},
    {"user_id": "555444", "fecha": _hoy.strftime("%Y-%m-%d"), "hora": "07:55:03",
     "timestamp": _hoy.strftime("%Y-%m-%d 07:55:03"), "estado": 0, "punch": 0},
]}
_real5 = _lec_p.leer_registros
_lec_p.leer_registros = lambda d, desde=None, hasta=None: _falsos

r = cli.get(f"/api/dispositivos/{p_personal}/registros?dias=7")
chequear("GET registros responde 200", r.status_code == 200, r.text[:200])
_rg = r.json()
chequear("resuelve el nombre de quien paso",
         _rg["registros"][0]["nombre"] is not None, _rg["registros"][0])
chequear("y marca al que no esta en el sistema",
         _rg["registros"][1]["nombre"] is None, _rg["registros"][1])
chequear("cuenta pasadas, personas y desconocidos",
         _rg["resumen"]["pasadas"] == 2 and _rg["resumen"]["personas"] == 2
         and _rg["resumen"]["desconocidos"] == 1, _rg["resumen"])
chequear("dice cuantas tiene guardadas el equipo",
         _rg["resumen"]["guardadas_en_el_equipo"] == 3, _rg["resumen"])

# El rango se acota: pedir mil dias no tiene sentido y pedir cero tampoco.
_pedidos = []
_lec_p.leer_registros = lambda d, desde=None, hasta=None: (
    _pedidos.append(desde), _falsos)[1]
cli.get(f"/api/dispositivos/{p_personal}/registros?dias=9999")
cli.get(f"/api/dispositivos/{p_personal}/registros?dias=0")
chequear("un rango disparatado se acota a 90 dias",
         (datetime.now() - _pedidos[0]).days in (89, 90), _pedidos[0])
chequear("y cero se acota a 1",
         (datetime.now() - _pedidos[1]).days in (0, 1), _pedidos[1])

# Es una consulta operativa, asi que pide accesos:ver y no dispositivos:ver.
chequear("necesita accesos:ver",
         _rrhh.get(f"/api/dispositivos/{p_personal}/registros").status_code == 403)

_lec_p.leer_registros = _real5


print("\n=== HORAS IMPOSIBLES EN LOS REGISTROS ===")
# ZK empaqueta el instante contando 31 dias por mes, asi que un registro basura
# produce fechas que no existen. pyzk decodifica adentro del bucle y no atrapa
# nada: UN registro con un 31 de septiembre tira abajo la lectura entera.
from struct import pack
from sync.lectores import _hora_zk

def _empaquetar(anio, mes, dia, h, m, seg):
    # La inversa de como lo guarda el equipo.
    t = (((((anio - 2000) * 12 + (mes - 1)) * 31 + (dia - 1)) * 24 + h) * 60 + m) * 60 + seg
    return pack("<I", t)

_d = _hora_zk(_empaquetar(2026, 9, 30, 8, 2, 11))
chequear("decodifica una fecha normal",
         (_d.year, _d.month, _d.day, _d.hour, _d.minute, _d.second)
         == (2026, 9, 30, 8, 2, 11), _d)

# El 31 de septiembre se puede empaquetar —la cuenta no sabe de calendarios—
# pero no existe. Tiene que fallar, no inventar una fecha cercana.
_rompio = False
try:
    _hora_zk(_empaquetar(2031, 9, 31, 0, 0, 0))
except ValueError:
    _rompio = True
chequear("una fecha que no existe levanta ValueError", _rompio)

# Lo que importa: que un registro asi no se lleve puestos a los demas.
class _ConexionFalsa:
    records = 3
    class _U:
        def __init__(s, uid, nid): s.uid, s.user_id = uid, nid
    def get_users(s): return [s._U(7, "42")]
    def read_sizes(s): pass
    def read_with_buffer(s, cmd):
        buenos = [_empaquetar(2026, 9, 29, 7, 0, 0), _empaquetar(2026, 9, 30, 8, 0, 0)]
        malo = _empaquetar(2031, 9, 31, 0, 0, 0)
        cuerpo = b"".join(pack("HB", 7, 0) + t + pack("B", 0) for t in [buenos[0], malo, buenos[1]])
        return pack("I", len(cuerpo)) + cuerpo, 0

from sync.lectores import _registros_crudos
_regs, _ileg, _tam, _ok8 = _registros_crudos(_ConexionFalsa())
chequear("el registro imposible no tira abajo la lectura", len(_regs) == 2, _regs)
chequear("y se cuenta aparte", _ileg == 1, _ileg)
chequear("el numero se resuelve por indice interno",
         all(n == "42" for n, _ in _regs), _regs)
chequear("detecta el tamano de registro", _tam == 8, _tam)
chequear("y entiende el formato", _ok8 is True, _ok8)


print("\n=== DONDE CAE EL TIEMPO EN UN REGISTRO DE 8 BYTES ===")
# pyzk asume uid(2) estado(1) tiempo(4) punch(1). En los equipos de este local el
# tiempo arranca un byte mas adelante, y leido corrido da fechas del siglo XXII:
# se reconoce porque el byte bajo sale 00 y el alto 0xFF, que es relleno.
from struct import pack, unpack
from datetime import datetime, timedelta
from sync.lectores import _formato_de_8, _hora_zk, _plausible

def _cod(d):
    t = (d.year-2000)*12 + (d.month-1); t = t*31 + (d.day-1); t = t*24 + d.hour
    t = t*60 + d.minute; return t*60 + d.second

_base = datetime.now() - timedelta(days=3)
def _reg(patron, uid, cuando):
    t = pack("<I", _cod(cuando))
    return pack("<HBB", uid, 0, 0) + t if patron == "nuevo" else pack("<HB", uid, 0) + t + pack("B", 0)

# Un equipo con el tiempo en el byte 4.
_datos_nuevo = b"".join(_reg("nuevo", 7, _base + timedelta(hours=i)) for i in range(20))
_pat, _ = _formato_de_8(_datos_nuevo)
chequear("con el tiempo en el byte 4 elige ese formato", _pat == "<HBB4s", _pat)

# Y uno con el formato que pyzk supone.
_datos_pyzk = b"".join(_reg("pyzk", 7, _base + timedelta(hours=i)) for i in range(20))
_pat2, _ = _formato_de_8(_datos_pyzk)
chequear("con el formato de pyzk elige el de pyzk", _pat2 == "<HB4sB", _pat2)

# Lo que paso de verdad: leido corrido, el byte bajo sale 00 y el alto 0xFF.
_crudo = unpack("<HB4sB", _datos_nuevo[:8])[2]
chequear("leido corrido, el byte bajo es 00", _crudo[0] == 0, _crudo)
_mal = None
try:
    _mal = _hora_zk(_crudo)
except Exception:
    pass
chequear("y da una fecha que no puede ser real",
         _mal is None or not _plausible(_mal), _mal)

# El criterio de plausible: ni del futuro lejano ni de hace veinte anios.
chequear("hoy es plausible", _plausible(datetime.now()))
chequear("el siglo XXII no", not _plausible(datetime(2133, 7, 22)))
chequear("ni el ano 2000", not _plausible(datetime(2000, 1, 1)))


print("\n=== EL RELOJ DEL EQUIPO ===")
# A las puertas nadie les sincroniza la hora: el sistema solo se la pone al de
# asistencia. Un lector de quince anios puede estar corrido meses, y entonces
# todas sus pasadas estan corridas lo mismo.
from sync.lectores import leer_reloj, _plausible, _formato_de_8

class _SinReloj:
    def get_time(s): raise RuntimeError("no contesta")
chequear("si el equipo no da la hora, se informa None",
         leer_reloj(_SinReloj()) is None)

class _ConReloj:
    def get_time(s): return datetime(2026, 8, 15, 10, 0, 0)
chequear("y si la da, se devuelve", leer_reloj(_ConReloj()) == datetime(2026, 8, 15, 10, 0, 0))

# Lo importante: las fechas se juzgan contra el reloj DEL EQUIPO. Con el de esta
# PC, un equipo adelantado daria todas sus pasadas por inverosimiles, que es
# justo cuando mas falta hace leerlas bien.
_adelantado = datetime.now() + timedelta(days=120)
_futura = datetime.now() + timedelta(days=100)
chequear("contra el reloj de la PC, una pasada de un equipo adelantado no pasa",
         not _plausible(_futura))
chequear("contra el reloj del equipo, si",
         _plausible(_futura, _adelantado))

# Y por eso el formato se elige usando esa misma referencia.
def _cod2(d):
    t = (d.year-2000)*12 + (d.month-1); t = t*31 + (d.day-1); t = t*24 + d.hour
    t = t*60 + d.minute; return t*60 + d.second
_datos_fut = b"".join(
    pack("<HBB", 7, 0, 0) + pack("<I", _cod2(_adelantado - timedelta(hours=i)))
    for i in range(20))
chequear("con un equipo adelantado, el formato se detecta igual",
         _formato_de_8(_datos_fut, _adelantado)[0] == "<HBB4s",
         _formato_de_8(_datos_fut, _adelantado)[0])


print("\n=== PONER EN HORA LOS EQUIPOS ===")
import sync.lectores as _lr

_estado = {"hora": datetime(2026, 5, 1, 3, 0, 0)}   # equipo muy atrasado

class _Equipo:
    def get_time(s): return _estado["hora"]
    def set_time(s, cuando): _estado["hora"] = cuando
    def disconnect(s): pass

_real6 = _lr._conectar
_lr._conectar = lambda *a, **k: (_Equipo(), "udp")

_v = _lr.ver_reloj({"ip": "127.0.0.9", "protocolo": "pull"})
chequear("ver_reloj informa la hora y el desfase",
         _v["ok"] and _v["desfase_minutos"] < -10000, _v)

_p = _lr.poner_en_hora({"ip": "127.0.0.9", "protocolo": "pull"})
chequear("poner_en_hora deja constancia de como estaba",
         _p["desfase_antes"] < -10000, _p["desfase_antes"])
chequear("y verifica releyendo que haya quedado",
         _p["quedo_en_hora"] is True and abs(_p["desfase_minutos"]) <= 2, _p)

# Que el equipo conteste que si no alcanza: lo que importa es que el reloj haya
# quedado en hora, y eso solo se sabe volviendolo a leer.
class _Mentiroso(_Equipo):
    def set_time(s, cuando): pass          # dice que si y no hace nada
_estado["hora"] = datetime(2026, 5, 1, 3, 0, 0)
_lr._conectar = lambda *a, **k: (_Mentiroso(), "udp")
_p2 = _lr.poner_en_hora({"ip": "127.0.0.9", "protocolo": "pull"})
chequear("si el equipo dice que si y no cambia nada, se detecta",
         _p2["ok"] is True and _p2["quedo_en_hora"] is False, _p2)

# A un equipo push no se le escribe.
chequear("a un equipo push no se le pone la hora",
         _lr.poner_en_hora({"ip": "1.2.3.4", "protocolo": "push"})["ok"] is False)
chequear("ni se le consulta", _lr.ver_reloj({"protocolo": "push"})["ok"] is False)

_lr._conectar = _real6


print("\n=== UN FORMATO QUE NO SABEMOS LEER ===")
# Dos equipos del mismo local devuelven formatos distintos. Cuando ninguno de
# los conocidos sirve, mostrar el "menos malo" llena la pantalla de fechas del
# siglo XXII, y una fecha inventada se lee como un dato.
from sync.lectores import _formato_de_8, _registros_crudos

_basura = bytes([0x73, 0x00, 0x08, 0x00, 0xFD, 0x8E, 0x68, 0xEE]) * 60
chequear("con bytes que ningun formato entiende, no elige ninguno",
         _formato_de_8(_basura, datetime.now()) is None,
         _formato_de_8(_basura, datetime.now()))

# Y con datos que si entiende, elige.
def _cod3(d):
    t = (d.year-2000)*12 + (d.month-1); t = t*31 + (d.day-1); t = t*24 + d.hour
    t = t*60 + d.minute; return t*60 + d.second
_buenos = b"".join(
    pack("<HBB", 7, 0, 0) + pack("<I", _cod3(datetime.now() - timedelta(hours=i)))
    for i in range(30))
chequear("con datos que entiende, elige un formato",
         _formato_de_8(_buenos, datetime.now()) is not None)

# El lector completo tiene que decir que no entendio, no devolver cualquier cosa.
class _Raro:
    records = 60
    class _U:
        def __init__(s, uid, nid): s.uid, s.user_id = uid, nid
    def get_users(s): return [s._U(0x73, "115")]
    def read_sizes(s): pass
    def read_with_buffer(s, cmd): return pack("I", len(_basura)) + _basura, 0
_regs, _ileg, _tam, _entendido = _registros_crudos(_Raro(), datetime.now())
chequear("el lector avisa que no entendio el formato", _entendido is False)
chequear("y no devuelve ningun registro inventado", _regs == [], _regs[:2])


print("\n=== EL FUTURO NO EXISTE PARA UN REGISTRO ===")
# El equipo no pudo fechar una pasada despues del momento en que cree estar. Una
# pasada de pasado maniana es una fecha escrita cuando el reloj estaba mal.
_reloj_equipo = datetime(2026, 10, 1, 18, 25)
chequear("una pasada de hace un rato pasa",
         _plausible(_reloj_equipo - timedelta(hours=3), _reloj_equipo))
chequear("una de hace cinco minutos tambien",
         _plausible(_reloj_equipo - timedelta(minutes=5), _reloj_equipo))
chequear("una de pasado maniana NO",
         not _plausible(_reloj_equipo + timedelta(days=2), _reloj_equipo))
chequear("ni una de maniana",
         not _plausible(_reloj_equipo + timedelta(days=1), _reloj_equipo))
chequear("ni una de dentro de una hora",
         not _plausible(_reloj_equipo + timedelta(hours=1), _reloj_equipo))
# Unos minutos de gracia: entre que se le lee la hora y se le leen los registros
# pasa un rato, y el equipo sigue andando mientras tanto.
chequear("dos minutos adelante si, que es el tiempo de la propia lectura",
         _plausible(_reloj_equipo + timedelta(minutes=2), _reloj_equipo))


print("\n=== EL NOMBRE QUE TIENE EL EQUIPO CONTRA EL QUE SE PIDIO ===")
from sync.verificar_acceso import _nombre_coincide

# Cada equipo corta a un largo distinto, asi que el pedido casi nunca entra
# entero. Que el del equipo sea el COMIENZO del pedido es lo que significa que
# esta bien y solo quedo cortado.
chequear("igual coincide", _nombre_coincide("STEHLE F", "STEHLE F"))
chequear("cortado por la puerta coincide", _nombre_coincide("STEHLE F", "STEHLE FEDERICO"))
chequear("cortado a 8 coincide", _nombre_coincide("GOMEZ CA", "GOMEZ CASTRO ANA"))
chequear("sin importar mayusculas", _nombre_coincide("stehle f", "STEHLE FEDERICO"))
chequear("con espacios de sobra tambien", _nombre_coincide("  STEHLE F  ", "STEHLE FEDERICO"))

chequear("otro nombre NO coincide", not _nombre_coincide("FEDERICO", "STEHLE F"))
chequear("ni uno parecido al medio", not _nombre_coincide("TEHLE F", "STEHLE F"))
chequear("el equipo sin nombre, habiendo pedido uno, no coincide",
         not _nombre_coincide("", "STEHLE F"))

# Sin pedido no hay nada que incumplir: eso es lo que significa dejar el campo
# vacio, y marcarlo seria inventar un problema.
chequear("sin pedir nada, cualquier nombre esta bien",
         _nombre_coincide("LO QUE SEA", "") and _nombre_coincide("", None))


print("\n=== TRAER DEL EQUIPO EL NOMBRE QUE MUESTRA ===")
# El equipo sabe dos cosas que el legajo no: el nivel de administracion y el
# nombre que muestra en pantalla. Las dos se adoptan una vez, pero por separado:
# un nivel es un permiso y un nombre no.
if len(_tres) >= 2:
    _cargados_maestro["usuarios"][0]["nombre"] = "STEHLE F"
    _cargados_maestro["usuarios"][1]["nombre"] = "DIAZ L"
    lectores.leer_cargados = lambda d, **kw: _cargados_maestro

    _nl3 = cli.get("/api/accesos/niveles-lector").json()
    _d3 = {f["empleado_id"]: f for f in _nl3["diferencias"]}
    chequear("muestra el nombre que tiene el equipo",
             _e2 in _d3 and _d3[_e2]["nombre_equipo"] == "DIAZ L", _nl3["diferencias"])
    chequear("y marca que difiere del legajo, que esta vacio",
             _d3[_e2]["difiere_nombre"] is True, _d3[_e2])
    chequear("y lo dice aparte de lo del nivel",
             "difiere_nivel" in _d3[_e2] and "difiere_nombre" in _d3[_e2], _d3[_e2])

    r = cli.post("/api/accesos/nombres-lector/importar", json={"empleados": [_e2]})
    chequear("traer el nombre responde 200", r.status_code == 200, r.text[:200])
    chequear("y lo copia al legajo",
             cli.get(f"/api/accesos/empleado/{_e2}").json()["empleado"]["nombre_lector"] == "DIAZ L")

    # Traer el nombre NO toca el nivel: son dos decisiones distintas y el boton
    # facil no puede arrastrar al que da permisos.
    chequear("y no le cambio el nivel de administracion",
             cli.get(f"/api/accesos/empleado/{_e2}").json()["empleado"]["nivel_lector"] == 0)

    # Un nombre vacio en el equipo no se copia: dejaria el legajo igual pero
    # pareciendo una decision tomada.
    _cargados_maestro["usuarios"][0]["nombre"] = "   "
    r = cli.post("/api/accesos/nombres-lector/importar", json={"empleados": [_e1]})
    chequear("un nombre vacio en el equipo no se copia",
             r.json()["importados"] == 0 and r.json()["sin_tocar"] == 1, r.json())

    chequear("traer nombres necesita accesos:asignar",
             _mirar.post("/api/accesos/nombres-lector/importar",
                         json={"empleados": [_e2]}).status_code == 403)
    chequear("y con asignar alcanza, sin necesitar editar",
             _aplica.post("/api/accesos/nombres-lector/importar",
                          json={"empleados": [_e2]}).status_code == 200)

    r = cli.post("/api/accesos/nombres-lector/importar", json={"empleados": []})
    chequear("sin empleados se rechaza", r.status_code == 400, r.status_code)

    cli.put(f"/api/accesos/empleado/{_e2}/nombre-lector", json={"nombre_lector": None})
    lectores.leer_cargados = _real4


print("\n=== CONTRA QUE SE COMPARA EL NOMBRE DEL EQUIPO ===")
# El equipo guarda un nombre corto que eligio alguien, no uno derivado del
# legajo. Comparar contra el legajo era comparar contra algo que nunca fue la
# referencia: "FEDE" para Federico es correcto y no se parece en nada.
_emps_r = {"10": {"id": 1, "nombre": "Federico", "apellido": "Stehle", "activo": 1,
                  "tipo": "mensual", "fecha_egreso": None, "nombre_lector": "FEDE"},
           "11": {"id": 2, "nombre": "Ana", "apellido": "Gomez", "activo": 1,
                  "tipo": "mensual", "fecha_egreso": None, "nombre_lector": None}}

# Con nombre configurado, la referencia es ese y nada mas.
_c1 = comparar_con_empleados(
    [{"uid": 1, "user_id": "10", "nombre": "FEDE", "grupo": "1"}], _emps_r)
chequear("con nombre configurado y el equipo igual, no marca nada",
         _c1["resumen"]["nombre_distinto"] == 0, _c1["filas"][0])
chequear("y dice que comparo contra lo configurado",
         _c1["filas"][0]["referencia_nombre"] == "configurado", _c1["filas"][0])

# Y "FEDE" no se parece al legajo "Stehle, Federico": antes esto se marcaba.
_c2 = comparar_con_empleados(
    [{"uid": 1, "user_id": "10", "nombre": "OTRO", "grupo": "1"}], _emps_r)
chequear("si el equipo no tiene el configurado, si marca",
         _c2["resumen"]["nombre_distinto"] == 1, _c2["filas"][0])
chequear("y lo cuenta como diferencia real, no como pista",
         _c2["resumen"]["nombre_no_configurado"] == 1, _c2["resumen"])

# Sin nombre configurado se compara contra el legajo, pero solo como pista.
_c3 = comparar_con_empleados(
    [{"uid": 2, "user_id": "11", "nombre": "PEREZ", "grupo": "1"}], _emps_r)
chequear("sin configurar, compara contra el legajo",
         _c3["filas"][0]["referencia_nombre"] == "legajo", _c3["filas"][0])
chequear("y lo marca", _c3["resumen"]["nombre_distinto"] == 1, _c3["resumen"])
chequear("pero NO como diferencia real",
         _c3["resumen"]["nombre_no_configurado"] == 0, _c3["resumen"])

_c4 = comparar_con_empleados(
    [{"uid": 2, "user_id": "11", "nombre": "GOMEZ", "grupo": "1"}], _emps_r)
chequear("sin configurar y pareciendose al legajo, no marca",
         _c4["resumen"]["nombre_distinto"] == 0, _c4["filas"][0])


print("\n=== SACAR A ALGUIEN DE UN EQUIPO, FUERA DE LA POLITICA ===")
# La salida de emergencia: una auditoria encuentra que alguien quedo cargado por
# un error que nadie previo, y hay que sacarlo ahora de ese equipo. Lo normal es
# corregir el perfil y aplicar el plan; esto es para cuando eso no alcanza.
_c15 = sqlite3.connect(DB)
_f15 = _c15.execute(
    "SELECT id, user_id FROM empleados WHERE activo=1 AND user_id IS NOT NULL LIMIT 1").fetchone()
_c15.close()
_eid15, _uid15 = _f15[0], str(_f15[1]).strip()

_estado15 = {"usuarios": [{"uid": 5, "user_id": _uid15, "nombre": "QUIEN SEA",
                           "grupo": "1", "privilegio": 0, "huellas": 1}],
             "borrados": []}

class _UsuarioFalso:
    """Lo que devuelve get_users: los nombres de campo son los de pyzk."""
    def __init__(s, d):
        s.uid, s.user_id, s.name = d["uid"], d["user_id"], d["nombre"]
        s.group_id, s.privilege = d["grupo"], d["privilegio"]

class _EquipoFalso:
    class _H:
        uid, fid, valid = 5, 0, 1
        def json_pack(s): return {"uid": 5, "fid": 0, "valid": 1, "template": "00"}
    # El equipo declara cuantos tiene; si no coincide con los leidos, la
    # escritura aborta antes de borrar nada.
    @property
    def users(s): return len(_estado15["usuarios"])
    def get_users(s): return [_UsuarioFalso(u) for u in _estado15["usuarios"]]
    def get_templates(s): return [s._H()]
    def delete_user(s, uid=None, user_id=""):
        _estado15["borrados"].append(uid)
        _estado15["usuarios"] = [u for u in _estado15["usuarios"] if u["uid"] != uid]
    def disconnect(s): pass

import sync.lectores as _lr15
_real15 = _lr15.leer_cargados
_conec15 = _lr15._conectar
_lr15.leer_cargados = lambda d, **kw: {"ok": True, "transporte": "udp", "error": None,
                                       "usuarios": list(_estado15["usuarios"])}
_lr15._conectar = lambda *a, **k: (_EquipoFalso(), "udp")

# Sin motivo no se puede: es lo unico que va a explicar esto dentro de un anio.
r = cli.post(f"/api/dispositivos/{p_personal}/cargados/{_uid15}/sacar", json={"motivo": ""})
chequear("sin motivo se rechaza", r.status_code == 422, r.status_code)
r = cli.post(f"/api/dispositivos/{p_personal}/cargados/{_uid15}/sacar", json={"motivo": "ok"})
chequear("un motivo de dos letras tampoco", r.status_code == 422, r.status_code)

r = cli.post(f"/api/dispositivos/{p_personal}/cargados/{_uid15}/sacar",
             json={"motivo": "quedo cargado por un error de sincronizacion"})
chequear("con motivo, saca a la persona", r.status_code == 200, r.text[:200])
chequear("y se lo pidio al equipo", _estado15["borrados"] == [5], _estado15["borrados"])
chequear("guarda el respaldo con las huellas",
         r.json().get("respaldo") and os.path.exists(r.json()["respaldo"]),
         r.json().get("respaldo"))

# El registro es el punto: sin el, la auditoria siguiente es el mismo misterio.
_ops = cli.get("/api/dispositivos/operaciones").json()["operaciones"]
chequear("queda registrado", len(_ops) == 1, _ops)
chequear("con el motivo", "sincronizacion" in _ops[0]["motivo"], _ops[0])
chequear("con quien lo hizo", _ops[0]["usuario_id"] is not None, _ops[0])
chequear("y con lo que se verifico, no lo que contesto el equipo",
         _ops[0]["resultado"] == "sacado", _ops[0])

# Si el equipo acepta y no hace nada, se registra como pendiente y se avisa.
_estado15["usuarios"] = [{"uid": 6, "user_id": _uid15, "nombre": "TERCO",
                          "grupo": "1", "privilegio": 0, "huellas": 1}]
class _Terco(_EquipoFalso):
    def delete_user(s, uid=None, user_id=""): pass
_lr15._conectar = lambda *a, **k: (_Terco(), "udp")
r = cli.post(f"/api/dispositivos/{p_personal}/cargados/{_uid15}/sacar",
             json={"motivo": "prueba de un equipo que no obedece"})
chequear("si el equipo no obedece, se rechaza", r.status_code == 400, r.status_code)
_ops2 = cli.get("/api/dispositivos/operaciones").json()["operaciones"]
chequear("y queda registrado igual, como pendiente",
         _ops2[0]["resultado"] == "pendiente", _ops2[0])
chequear("diciendo que sigue cargado",
         "sigue cargado" in (_ops2[0]["detalle"] or ""), _ops2[0])

# Alguien que no esta en el equipo no se puede sacar.
r = cli.post(f"/api/dispositivos/{p_personal}/cargados/888111/sacar",
             json={"motivo": "no deberia encontrarlo"})
chequear("un numero que no esta en el equipo da 404", r.status_code == 404, r.status_code)

# Permisos: escribir en los equipos es su propia accion.
chequear("con accesos:asignar NO se puede sacar de un equipo",
         _aplica.post(f"/api/dispositivos/{p_personal}/cargados/{_uid15}/sacar",
                      json={"motivo": "no deberia poder"}).status_code == 403)
chequear("y mirar el registro alcanza con accesos:ver",
         _mirar.get("/api/dispositivos/operaciones").status_code == 200)

_lr15.leer_cargados = _real15
_lr15._conectar = _conec15


print("\n=== DE QUIEN ERA UN NUMERO LIBERADO ===")
# Liberar el ID suelta el numero para que el lector lo reuse, pero la huella
# sigue cargada en las puertas. Sin guardar de quien era, ese numero pasa a ser
# un misterio en la proxima auditoria.
_c16 = sqlite3.connect(DB)
_f16 = _c16.execute(
    """SELECT id, user_id FROM empleados
        WHERE activo=0 AND user_id IS NOT NULL LIMIT 1""").fetchone()
_c16.close()

if _f16:
    _eid16, _uid16 = _f16[0], str(_f16[1]).strip()
    r = cli.post(f"/api/empleados/conflictos/liberar/{_eid16}")
    chequear("liberar el id responde 200", r.status_code == 200, r.text[:160])
    chequear("y devuelve cual era", r.json().get("user_id_anterior") == _uid16, r.json())

    _c16 = sqlite3.connect(DB)
    _fila = _c16.execute(
        """SELECT user_id, user_id_anterior, user_id_liberado_en
             FROM empleados WHERE id=?""", (_eid16,)).fetchone()
    _c16.close()
    chequear("el numero quedo suelto", _fila[0] is None, _fila[0])
    chequear("pero se guardo de quien era", _fila[1] == _uid16, _fila[1])
    chequear("y cuando se libero", bool(_fila[2]), _fila[2])

    # El pago: el plan deja de decir "desconocido" a secas.
    _pu16 = [{"id": p_personal, "nombre": "Personal", "ubicacion": None}]
    _lec16 = {p_personal: {"ok": True, "transporte": "udp", "error": None,
                           "usuarios": [{"user_id": _uid16}]}}
    with db_session() as _cn:
        _plan16 = armar_plan(_cn, _pu16, _lec16, {"ok": True, "usuarios": []})
    _s16 = next((x for x in _plan16["puertas"][0]["sacar"] if x["user_id"] == _uid16), None)
    chequear("el numero figura para sacar", _s16 is not None, _plan16["puertas"][0]["sacar"][:3])
    chequear("como desconocido, porque ya no es de nadie",
             _s16 and _s16["motivo"] == "desconocido", _s16)
    chequear("pero dice de quien era", _s16 and _s16["era_de"], _s16)
    chequear("y cuando se libero", _s16 and _s16["liberado_en"], _s16)

    # Un desconocido que nunca fue de nadie no inventa un dueno.
    _lec17 = {p_personal: {"ok": True, "transporte": "udp", "error": None,
                           "usuarios": [{"user_id": "777333"}]}}
    with db_session() as _cn:
        _plan17 = armar_plan(_cn, _pu16, _lec17, {"ok": True, "usuarios": []})
    _s17 = next((x for x in _plan17["puertas"][0]["sacar"] if x["user_id"] == "777333"), None)
    chequear("uno que nunca fue de nadie no inventa dueno",
             _s17 and _s17["era_de"] is None, _s17)


print("\n=== CARGAR A UNA PERSONA EN UNA PUERTA ===")
# La mitad que faltaba: el plan decia a quien cargar y no habia forma de hacerlo
# desde el sistema.
import sync.escritura as _esc_mod

_c18 = sqlite3.connect(DB)
_f18 = _c18.execute(
    """SELECT id, user_id FROM empleados
        WHERE activo=1 AND user_id IS NOT NULL AND perfil_acceso_id IS NOT NULL
        LIMIT 1""").fetchone()
_c18.close()

if _f18:
    _eid18, _uid18 = _f18[0], str(_f18[1]).strip()
    # Se le da un perfil que incluya la puerta, para que le corresponda.
    _r18 = cli.get("/api/perfiles-acceso").json()["perfiles"]
    _p18 = next(x for x in _r18 if x["id"] == todas["id"])
    cli.put(f"/api/perfiles-acceso/{todas['id']}",
            json={"nombre": _p18["nombre"], "activo": True, "orden": 0,
                  "dispositivos": sorted(set(_p18["dispositivos"]) | {p_personal})})
    cli.put(f"/api/accesos/empleado/{_eid18}/perfil", json={"perfil_acceso_id": todas["id"]})

    _escrito = {}
    _real18 = _esc_mod.cargar_en_puerta
    _esc_mod.cargar_en_puerta = lambda puerta, maestro, numero, nombre=None, **kw: (
        _escrito.update({"puerta": puerta["id"], "numero": numero, "nombre": nombre}),
        {"ok": True, "uid": 50, "grupo": "1", "nombre_escrito": nombre or "X",
         "huellas": 2, "huellas_esperadas": 2, "otros": 26, "problemas": [],
         "error": None})[1]

    r = cli.post("/api/accesos/cargar",
                 json={"dispositivo_id": p_personal, "user_id": _uid18})
    chequear("carga a quien le corresponde esa puerta", r.status_code == 200, r.text[:200])
    chequear("y le pasa el numero a la escritura",
             _escrito.get("numero") == _uid18, _escrito)
    _ops18 = cli.get("/api/dispositivos/operaciones").json()["operaciones"]
    chequear("queda registrado como cargado",
             _ops18[0]["accion"] == "cargar" and _ops18[0]["resultado"] == "cargado",
             _ops18[0])

    # Lo que NO deja hacer, que es lo que mantiene al plan describiendo la
    # realidad en vez de ser una sugerencia. El perfil se acota a una sola
    # puerta: con "todas" no habria ninguna que no le corresponda.
    cli.put(f"/api/perfiles-acceso/{todas['id']}",
            json={"nombre": _p18["nombre"], "activo": True, "orden": 0,
                  "dispositivos": [p_personal]})
    r = cli.post("/api/accesos/cargar",
                 json={"dispositivo_id": p_oficina, "user_id": _uid18})
    chequear("no carga en una puerta que su perfil no incluye",
             r.status_code == 400 and "no le corresponde" in r.json()["detail"],
             r.text[:200])

    r = cli.post("/api/accesos/cargar",
                 json={"dispositivo_id": p_personal, "user_id": "888222"})
    chequear("ni a alguien que no existe en el sistema", r.status_code == 404, r.status_code)

    _c18 = sqlite3.connect(DB)
    _baja = _c18.execute(
        "SELECT user_id FROM empleados WHERE activo=0 AND user_id IS NOT NULL LIMIT 1").fetchone()
    _c18.close()
    if _baja:
        r = cli.post("/api/accesos/cargar",
                     json={"dispositivo_id": p_personal, "user_id": str(_baja[0]).strip()})
        chequear("ni a un egresado", r.status_code == 400 and "baja" in r.json()["detail"],
                 r.text[:160])

    # Si la escritura falla, se registra igual: el registro no puede depender de
    # que las cosas salgan bien.
    _esc_mod.cargar_en_puerta = lambda *a, **k: {
        "ok": False, "error": "el equipo no contesto", "problemas": []}
    r = cli.post("/api/accesos/cargar",
                 json={"dispositivo_id": p_personal, "user_id": _uid18})
    chequear("una escritura fallida se rechaza", r.status_code == 400, r.status_code)
    _ops19 = cli.get("/api/dispositivos/operaciones").json()["operaciones"]
    chequear("y queda registrada como fallida",
             _ops19[0]["resultado"] == "falló" and "no contesto" in (_ops19[0]["detalle"] or ""),
             _ops19[0])

    chequear("cargar necesita accesos:aplicar",
             _aplica.post("/api/accesos/cargar",
                          json={"dispositivo_id": p_personal,
                                "user_id": _uid18}).status_code == 403)
    _esc_mod.cargar_en_puerta = _real18


print("\n=== LAS HUELLAS RESPALDADAS EN LA BASE ===")
# Hoy el unico lugar donde estan todas es el equipo de fichaje. La otra copia la
# tiene Enterprise, y todo esto existe para apagarlo: sin respaldo, un equipo
# quemado son doscientas personas volviendo a enrolarse con el dedo, de a una.
import sync.huellas as _hu

_ch = sqlite3.connect(DB)
_fh = _ch.execute(
    """SELECT id, user_id FROM empleados
        WHERE activo=1 AND user_id IS NOT NULL AND TRIM(user_id) <> '' LIMIT 2"""
).fetchall()
_ch.close()

if len(_fh) >= 2:
    (_eid1, _un1), (_eid2, _un2) = [(r[0], str(r[1]).strip()) for r in _fh]

    class _Maestro:
        """Contesta como el equipo de fichaje: dos personas, una con dos dedos."""
        def __init__(s, gente):
            s.gente = gente
            s._u = [type("U", (), {"uid": i + 1, "user_id": n, "name": f"N{n}",
                                   "group_id": "1", "privilege": 0})()
                    for i, (n, _d) in enumerate(gente)]
        def get_users(s): return list(s._u)
        def get_templates(s):
            salida = []
            for i, (_n, dedos) in enumerate(s.gente):
                for d in range(dedos):
                    salida.append(type("H", (), {
                        "uid": i + 1, "fid": d, "valid": 1,
                        "template": bytes([d + 1]) * 600})())
            return salida
        def disconnect(s): pass

    _real_con = _hu_conectar = None
    import sync.lectores as _lr_h
    _real_con = _lr_h._conectar
    # Un equipo que exista de verdad: la huella guarda de donde salio.
    _EQ_H = {"id": p_personal, "nombre": "Reloj", "ip": "1.2.3.4",
             "algoritmo_huella": "v10"}

    _lr_h._conectar = lambda *a, **kw: (_Maestro([(_un1, 2), (_un2, 1)]), "udp")
    with db_session() as _cn:
        _rh = _hu.respaldar_desde(_cn, _EQ_H)
    chequear("respalda las huellas del equipo de fichaje", _rh["ok"] is True, _rh)
    chequear("dos personas", _rh["personas"] == 2, _rh)
    chequear("y tres huellas, porque una tiene dos dedos", _rh["huellas"] == 3, _rh)

    with db_session() as _cn:
        _g = _hu.guardadas_de(_cn, [_un1, _un2])
    chequear("se recuperan por numero de legajo", set(_g) == {_un1, _un2}, list(_g))
    chequear("con los dos dedos del que tenia dos", len(_g[_un1]) == 2, len(_g[_un1]))
    chequear("la plantilla vuelve igual a como entro",
             _g[_un1][0].template == bytes([1]) * 600, _g[_un1][0].size)
    chequear("y con el dedo que era", sorted(f.fid for f in _g[_un1]) == [0, 1],
             [f.fid for f in _g[_un1]])

    # Si alguien agrega un dedo, el respaldo tiene que quedar igual al equipo y
    # no ser la suma de todo lo que alguna vez tuvo.
    _lr_h._conectar = lambda *a, **kw: (_Maestro([(_un1, 1), (_un2, 1)]), "udp")
    with db_session() as _cn:
        _hu.respaldar_desde(_cn, _EQ_H)
        _g2 = _hu.guardadas_de(_cn, [_un1])
    chequear("si en el equipo quedo un dedo, en el respaldo queda uno",
             len(_g2[_un1]) == 1, len(_g2[_un1]))

    # Una lectura que no trae a alguien NO es una baja.
    _lr_h._conectar = lambda *a, **kw: (_Maestro([(_un1, 1)]), "udp")
    with db_session() as _cn:
        _hu.respaldar_desde(_cn, _EQ_H)
        _g3 = _hu.guardadas_de(_cn, [_un2])
    chequear("el que no vino en la lectura sigue respaldado", _un2 in _g3, list(_g3))

    # Un numero del equipo que no es de nadie no se guarda: al restaurarlo
    # habria que decidir de quien es.
    _lr_h._conectar = lambda *a, **kw: (_Maestro([("999888", 1)]), "udp")
    with db_session() as _cn:
        _rh4 = _hu.respaldar_desde(_cn, _EQ_H)
    chequear("una huella sin legajo no se guarda, se cuenta",
             _rh4["personas"] == 0 and _rh4["sin_legajo"] == 1, _rh4)

    # Si el equipo no contesta, se dice y no se borra nada de lo guardado.
    def _explota(*a, **kw):
        raise OSError("no contesta")
    _lr_h._conectar = _explota
    with db_session() as _cn:
        _rh5 = _hu.respaldar_desde(_cn, _EQ_H)
        _g5 = _hu.guardadas_de(_cn, [_un1])
    chequear("si el equipo no contesta se avisa", _rh5["ok"] is False, _rh5)
    chequear("y lo respaldado sigue estando", _un1 in _g5, list(_g5))

    # Y lo que se borra al cerrar una baja.
    with db_session() as _cn:
        _n = _hu.olvidar(_cn, _eid1)
        _g6 = _hu.guardadas_de(_cn, [_un1])
    chequear("olvidar borra las huellas de esa persona", _n >= 1, _n)
    chequear("y no quedan", _un1 not in _g6, list(_g6))

    _lr_h._conectar = _real_con


print("\n=== QUIENES SON LOS DE CADA CARGO ===")
# La pantalla mostraba un total que no coincidia con la realidad del local:
# "Gerente 3" cuando hay un gerente. Los otros dos eran legajos de tipo
# `acceso`, que existen solo para abrir puertas y no para fichar. No se los
# excluye --son justamente los que necesitan perfil-- pero se cuentan aparte.

_cc = sqlite3.connect(DB)
_cargo_id = _cc.execute(
    "SELECT id FROM cargos LIMIT 1").fetchone()
_cc.close()

if _cargo_id:
    _cid = _cargo_id[0]
    _cc = sqlite3.connect(DB)
    # Dos personas en ese cargo: una normal y una de solo acceso.
    _eids = [r[0] for r in _cc.execute(
        "SELECT id FROM empleados WHERE activo=1 LIMIT 2").fetchall()]
    _antes = [_cc.execute("SELECT cargo_id, tipo FROM empleados WHERE id=?", (e,)).fetchone()
              for e in _eids]
    _cc.execute("UPDATE empleados SET cargo_id=?, tipo='normal' WHERE id=?", (_cid, _eids[0]))
    _cc.execute("UPDATE empleados SET cargo_id=?, tipo='acceso' WHERE id=?", (_cid, _eids[1]))
    _cc.commit(); _cc.close()

    _pc = cli.get("/api/accesos/por-cargo").json()
    _fila = next(c for c in _pc["cargos"] if c["cargo_id"] == _cid)
    chequear("el total cuenta a los dos", _fila["total"] >= 2, _fila)
    chequear("y dice cuantos son de solo acceso",
             _fila["solo_acceso"] >= 1, _fila)

    _ge = cli.get(f"/api/accesos/por-cargo/{_cid}/empleados").json()["empleados"]
    chequear("se puede ver quienes son", len(_ge) >= 2, len(_ge))
    chequear("y cual de ellos es de solo acceso",
             any(x["solo_acceso"] for x in _ge)
             and any(not x["solo_acceso"] for x in _ge), _ge[:3])
    chequear("con su numero y su perfil",
             all("user_id" in x and "perfil" in x for x in _ge), _ge[:1])

    # Los que no tienen cargo se piden con 0.
    _sc = cli.get("/api/accesos/por-cargo/0/empleados")
    chequear("el cargo 0 son los que no tienen cargo", _sc.status_code == 200,
             _sc.status_code)

    chequear("ver quienes alcanza con accesos:ver",
             _aplica.get(f"/api/accesos/por-cargo/{_cid}/empleados").status_code == 200)

    _cc = sqlite3.connect(DB)
    for _e, _a in zip(_eids, _antes):
        _cc.execute("UPDATE empleados SET cargo_id=?, tipo=? WHERE id=?", (_a[0], _a[1], _e))
    _cc.commit(); _cc.close()


print("\n=== LA BAJA ADELANTADA ===")
# Se puede dar de baja a alguien hoy con fecha futura, para adelantarle la
# liquidacion final, y esa persona sigue trabajando hasta la vispera. Si el plan
# mirara solo `activo`, la sacaria de todas las puertas y del equipo de fichaje
# hoy mismo: se quedaria sin poder entrar ni fichar sus ultimos dias. Y como
# sacarla del equipo de fichaje le borra la huella, volver atras no es deshacer,
# es hacerla enrolar de nuevo.
#
# `fecha_egreso` es el primer dia NO trabajado, igual que en todo el sistema.
from datetime import datetime as _dt, timedelta as _td
from sync.plan_accesos import estado_deseado as _ed, plan_fichaje as _pf2

_HOY = _dt.now().strftime("%Y-%m-%d")
_EN_5 = (_dt.now() + _td(days=5)).strftime("%Y-%m-%d")
_AYER = (_dt.now() - _td(days=1)).strftime("%Y-%m-%d")

_cb = sqlite3.connect(DB)
_fb = _cb.execute(
    """SELECT id, user_id FROM empleados
        WHERE activo=1 AND user_id IS NOT NULL AND perfil_acceso_id IS NOT NULL
        LIMIT 1""").fetchone()
_cb.close()

if _fb:
    _eidb, _uidb = _fb[0], str(_fb[1]).strip()
    _MAESTRO_B = {"id": 91, "nombre": "Reloj"}
    _PUERTAS_B = [{"id": p_personal, "nombre": "Personal"}]

    def _poner_baja(fecha):
        _c = sqlite3.connect(DB)
        _c.execute("UPDATE empleados SET activo=0, fecha_egreso=? WHERE id=?",
                   (fecha, _eidb))
        _c.commit(); _c.close()

    def _restaurar():
        _c = sqlite3.connect(DB)
        _c.execute("UPDATE empleados SET activo=1, fecha_egreso=NULL WHERE id=?",
                   (_eidb,))
        _c.commit(); _c.close()

    # Con la baja cargada para dentro de cinco dias, sigue siendo de los que
    # tienen que estar cargados.
    _poner_baja(_EN_5)
    with db_session() as _cn:
        _des = _ed(_cn)
    chequear("una baja adelantada sigue en el estado deseado de sus puertas",
             any(_uidb in v for v in _des.values()), list(_des.keys()))

    with db_session() as _cn:
        _rb = _pf2(_cn, _MAESTRO_B,
                   {"ok": True, "usuarios": [{"user_id": _uidb}]},
                   {p_personal: {"ok": True, "usuarios": []}}, _PUERTAS_B)
    chequear("y no figura para sacar del equipo de fichaje",
             all(x["user_id"] != _uidb for x in _rb["sacar"]), _rb["sacar"][:3])
    chequear("ni como pendiente de enrolar, porque ya esta",
             all(x["user_id"] != _uidb for x in _rb["sin_enrolar"]), _rb["sin_enrolar"][:3])

    # Y se la puede cargar en una puerta: todavia trabaja.
    _realb = _esc_mod.cargar_en_puerta
    _esc_mod.cargar_en_puerta = lambda *a, **k: {
        "ok": True, "uid": 9, "grupo": "1", "nombre_escrito": "X", "huellas": 1,
        "huellas_esperadas": 1, "otros": 5, "problemas": [], "error": None}
    _permb = cli.get(f"/api/accesos/empleado/{_eidb}").json()
    r = cli.post("/api/accesos/cargar",
                 json={"dispositivo_id": p_personal, "user_id": _uidb})
    chequear("se la puede cargar en una puerta aunque este dada de baja",
             r.status_code in (200, 400), r.status_code)
    if r.status_code == 400:
        chequear("y si se rechaza no es por la baja",
                 "dado de baja" not in r.json()["detail"], r.text[:200])
    _esc_mod.cargar_en_puerta = _realb

    # Pasada la fecha, SI sale: el dia que llega, el plan lo propone solo.
    _poner_baja(_AYER)
    with db_session() as _cn:
        _des2 = _ed(_cn)
    chequear("pasada la fecha de egreso, sale del estado deseado",
             all(_uidb not in v for v in _des2.values()), list(_des2.keys()))
    with db_session() as _cn:
        _rb2 = _pf2(_cn, _MAESTRO_B,
                    {"ok": True, "usuarios": [{"user_id": _uidb}]},
                    {p_personal: {"ok": True, "usuarios": []}}, _PUERTAS_B)
    chequear("y recien ahi figura para sacar del equipo de fichaje",
             any(x["user_id"] == _uidb for x in _rb2["sacar"]), _rb2["sacar"][:3])

    # El borde: con fecha de HOY ya no trabaja. fecha_egreso es el primer dia
    # NO trabajado, no el ultimo trabajado.
    _poner_baja(_HOY)
    with db_session() as _cn:
        _des3 = _ed(_cn)
    chequear("con fecha de hoy ya no trabaja: fecha_egreso es el primer dia NO trabajado",
             all(_uidb not in v for v in _des3.values()), list(_des3.keys()))

    # Y el endpoint del equipo de fichaje no la saca mientras siga trabajando.
    _poner_baja(_EN_5)
    _cb = sqlite3.connect(DB)
    _midb = _cb.execute(
        "SELECT id FROM dispositivos WHERE cuenta_asistencia=1 AND es_acceso=0 LIMIT 1").fetchone()
    _cb.close()
    if _midb:
        r = cli.post("/api/accesos/sacar",
                     json={"dispositivo_id": _midb[0], "user_id": _uidb})
        chequear("no la saca del equipo de fichaje mientras trabaje",
                 r.status_code == 400 and "tiene que poder fichar" in r.json()["detail"],
                 r.text[:200])

    _restaurar()


print("\n=== EL EQUIPO DE FICHAJE DENTRO DEL PLAN ===")
# El que faltaba: una baja dejaba de abrir puertas y seguia pudiendo fichar.
# Solo tiene bajas, porque agregar a alguien ahi no es algo que el sistema pueda
# hacer: de ahi salen las huellas, no van hacia ahi.
from sync.plan_accesos import plan_fichaje as _pf

_MAESTRO_F = {"id": 90, "nombre": "Reloj de personal"}
_PUERTAS_F = [{"id": p_personal, "nombre": "Personal"},
              {"id": p_oficina, "nombre": "Oficina"}]

_cf = sqlite3.connect(DB)
_act = str(_cf.execute(
    "SELECT user_id FROM empleados WHERE activo=1 AND user_id IS NOT NULL LIMIT 1"
).fetchone()[0]).strip()
_bajaf = _cf.execute(
    "SELECT user_id FROM empleados WHERE activo=0 AND user_id IS NOT NULL LIMIT 1").fetchone()
_bajaf = str(_bajaf[0]).strip() if _bajaf else None
_cf.close()

def _lect(usuarios, ok=True):
    return {"ok": ok, "error": None if ok else "no contesto",
            "usuarios": [{"user_id": u} for u in usuarios]}

if _bajaf:
    # Caso limpio: el egresado esta en el fichaje y en ninguna puerta.
    with db_session() as _cn:
        _r = _pf(_cn, _MAESTRO_F, _lect([_act, _bajaf]),
                 {p_personal: _lect([_act]), p_oficina: _lect([])}, _PUERTAS_F)
    _s = next((x for x in _r["sacar"] if x["user_id"] == _bajaf), None)
    chequear("el egresado figura para sacar del equipo de fichaje", _s is not None,
             _r["sacar"][:3])
    chequear("como egresado", _s and _s["motivo"] == "egresado", _s)
    chequear("y se puede, porque no quedo en ninguna puerta",
             _s and _s["se_puede"] is True, _s)
    chequear("el activo no figura para sacar",
             all(x["user_id"] != _act for x in _r["sacar"]), _r["sacar"][:3])

    # La condicion que ordena todo: si sigue en una puerta, no se puede.
    with db_session() as _cn:
        _r2 = _pf(_cn, _MAESTRO_F, _lect([_bajaf]),
                  {p_personal: _lect([_bajaf]), p_oficina: _lect([])}, _PUERTAS_F)
    _s2 = next(x for x in _r2["sacar"] if x["user_id"] == _bajaf)
    chequear("si sigue cargado en una puerta, NO se puede sacar del fichaje",
             _s2["se_puede"] is False, _s2)
    chequear("y se dice en cual", _s2["en_puertas"] == ["Personal"], _s2)

    # Una puerta que no contesta tampoco habilita: no haberla leido no es
    # haberla leido vacia, y esta es la decision donde esa diferencia importa.
    with db_session() as _cn:
        _r3 = _pf(_cn, _MAESTRO_F, _lect([_bajaf]),
                  {p_personal: _lect([], ok=False), p_oficina: _lect([])}, _PUERTAS_F)
    _s3 = next(x for x in _r3["sacar"] if x["user_id"] == _bajaf)
    chequear("si una puerta no contesta, tampoco se puede",
             _s3["se_puede"] is False, _s3)
    chequear("y se dice cual no se pudo leer",
             _r3["puertas_sin_leer"] == ["Personal"], _r3["puertas_sin_leer"])

# Un numero que no existe en la base tambien sale del equipo de fichaje.
with db_session() as _cn:
    _r4 = _pf(_cn, _MAESTRO_F, _lect(["777111"]),
              {p_personal: _lect([]), p_oficina: _lect([])}, _PUERTAS_F)
_s4 = next(x for x in _r4["sacar"] if x["user_id"] == "777111")
chequear("un desconocido tambien sale del equipo de fichaje",
         _s4["motivo"] == "desconocido", _s4)

# Los activos que no estan enrolados: no son algo para aplicar, son gente que
# tiene que venir a poner el dedo. Pero explican por que no se los puede cargar.
with db_session() as _cn:
    _r5 = _pf(_cn, _MAESTRO_F, _lect([]),
              {p_personal: _lect([]), p_oficina: _lect([])}, _PUERTAS_F)
chequear("los activos que no estan enrolados se listan aparte",
         any(x["user_id"] == _act for x in _r5["sin_enrolar"]),
         _r5["sin_enrolar"][:3])
chequear("y no como algo para sacar",
         all(x["user_id"] != _act for x in _r5["sacar"]), _r5["sacar"][:3])

# Si el equipo de fichaje no contesta, se dice y no se inventa nada.
with db_session() as _cn:
    _r6 = _pf(_cn, _MAESTRO_F, _lect([], ok=False), {}, _PUERTAS_F)
chequear("si el equipo de fichaje no contesta, no hay plan para el",
         _r6["ok"] is False and _r6["sacar"] == [], _r6)

# Y el endpoint: un activo no se saca del equipo de fichaje ni aunque se pida.
_cf = sqlite3.connect(DB)
_mid = _cf.execute(
    "SELECT id FROM dispositivos WHERE cuenta_asistencia=1 AND es_acceso=0 LIMIT 1").fetchone()
_cf.close()
if _mid:
    r = cli.post("/api/accesos/sacar",
                 json={"dispositivo_id": _mid[0], "user_id": _act})
    chequear("un activo no se saca del equipo de fichaje",
             r.status_code == 400 and "tiene que poder fichar" in r.json()["detail"],
             r.text[:200])


print("\n=== SACAR A UNA PERSONA DESDE EL PLAN ===")
# El espejo de cargar, y la simetria es lo que mantiene el plan honesto: cargar
# se niega si el perfil NO incluye esa puerta, sacar se niega si SI la incluye.

if _f18:
    _sacado = {}
    _real_sacar = _esc_mod.sacar_de_puerta
    _esc_mod.sacar_de_puerta = lambda puerta, numero: (
        _sacado.update({"puerta": puerta["id"], "numero": numero}),
        {"ok": True, "respaldo": "C:/x/borrados/x.json", "nombre_equipo": "ALGUIEN",
         "uid": 7, "huellas": 2, "otros": 26, "problemas": [], "error": None})[1]

    # Lo que NO deja hacer, que es la mitad que importa. El perfil de _uid18
    # quedo acotado a p_personal, asi que esa puerta SI le corresponde.
    r = cli.post("/api/accesos/sacar",
                 json={"dispositivo_id": p_personal, "user_id": _uid18})
    chequear("no saca a quien le corresponde esa puerta segun su perfil",
             r.status_code == 400 and "le corresponde" in r.json()["detail"],
             r.text[:200])
    chequear("y no toco el equipo", not _sacado, _sacado)

    # Un numero que no existe en la base: politica del local, si no esta en la
    # bd no deberia estar en las terminales aunque haya quedado por un error.
    r = cli.post("/api/accesos/sacar",
                 json={"dispositivo_id": p_personal, "user_id": "888222"})
    chequear("saca a un desconocido", r.status_code == 200, r.text[:200])
    chequear("con el motivo que corresponde",
             r.json()["motivo"] == "No existe en el sistema", r.text[:160])
    chequear("y le pasa el numero a la escritura",
             _sacado.get("numero") == "888222", _sacado)
    _opsS = cli.get("/api/dispositivos/operaciones").json()["operaciones"]
    chequear("queda registrado como sacado",
             _opsS[0]["accion"] == "sacar" and _opsS[0]["resultado"] == "sacado",
             _opsS[0])
    chequear("con el respaldo anotado", _opsS[0]["respaldo"], _opsS[0])

    # Un egresado. Es el caso que justifica todo el modulo.
    if _baja:
        r = cli.post("/api/accesos/sacar",
                     json={"dispositivo_id": p_personal,
                           "user_id": str(_baja[0]).strip()})
        chequear("saca a un egresado", r.status_code == 200, r.text[:200])
        chequear("y dice que es por la baja",
                 "baja" in r.json()["motivo"], r.text[:160])

    # El motivo que el sistema NO ejecuta: ninguna puerta en ningun perfil.
    # Aplicarlo dejaria la puerta vacia, y eso no es una baja sino una politica
    # incompleta.
    _perfS = cli.get("/api/perfiles-acceso").json()["perfiles"]
    _antesS = {p["id"]: list(p["dispositivos"]) for p in _perfS}
    for _p in _perfS:
        cli.put(f"/api/perfiles-acceso/{_p['id']}",
                json={"nombre": _p["nombre"], "activo": True, "orden": _p.get("orden", 0),
                      "dispositivos": [d for d in _p["dispositivos"] if d != p_oficina]})
    r = cli.post("/api/accesos/sacar",
                 json={"dispositivo_id": p_oficina, "user_id": _uid18})
    chequear("no vacia una puerta que ningun perfil incluye",
             r.status_code == 400 and "Ningún perfil incluye" in r.json()["detail"],
             r.text[:220])
    for _p in _perfS:
        cli.put(f"/api/perfiles-acceso/{_p['id']}",
                json={"nombre": _p["nombre"], "activo": True, "orden": _p.get("orden", 0),
                      "dispositivos": _antesS[_p["id"]]})

    # Si la escritura falla, se registra igual: el registro no puede depender de
    # que las cosas salgan bien.
    _esc_mod.sacar_de_puerta = lambda *a, **k: {
        "ok": False, "error": "sigue cargado despues de borrarlo", "problemas": []}
    r = cli.post("/api/accesos/sacar",
                 json={"dispositivo_id": p_personal, "user_id": "888222"})
    chequear("un borrado que no se pudo verificar se rechaza",
             r.status_code == 400, r.status_code)
    _opsS2 = cli.get("/api/dispositivos/operaciones").json()["operaciones"]
    chequear("y queda registrado como fallido",
             _opsS2[0]["resultado"] == "falló"
             and "sigue cargado" in (_opsS2[0]["detalle"] or ""), _opsS2[0])

    # Y si no estaba, es un 404 y no un borrado inventado.
    _esc_mod.sacar_de_puerta = lambda *a, **k: {
        "ok": False, "no_estaba": True, "error": "El 888222 no está cargado en X."}
    r = cli.post("/api/accesos/sacar",
                 json={"dispositivo_id": p_personal, "user_id": "888222"})
    chequear("si no estaba cargado, 404", r.status_code == 404, r.status_code)

    chequear("sacar necesita accesos:aplicar",
             _aplica.post("/api/accesos/sacar",
                          json={"dispositivo_id": p_personal,
                                "user_id": "888222"}).status_code == 403)
    _esc_mod.sacar_de_puerta = _real_sacar


print("\n=== EL GRUPO DEL EQUIPO, CUANDO ELEGIRLO ES UNA DECISION ===")
# El grupo es de donde el lector saca sus propias reglas y puede decidir si
# alguien abre al margen de estar cargado. Copiar el mas frecuente no es una
# decision si todos estan en el mismo; si estan repartidos, si lo es.
from sync.escritura import cargar_en_puerta as _cargar_real

class _Puerta:
    user_packet_size = 28
    encoding = "latin-1"
    def __init__(s, grupos):
        s.users = len(grupos)
        s._u = [type("U", (), {"uid": i + 1, "user_id": str(100 + i), "name": "X",
                               "group_id": g, "privilege": 0})()
                for i, g in enumerate(grupos)]
        s.escrito = None
    def get_users(s): return list(s._u)
    def get_templates(s): return []
    def refresh_data(s): pass
    def save_user_template(s, u, f): pass
    def disconnect(s): pass

import sync.escritura as _em
_conectar_real = _em._conectar
_huellas_real = _em.huellas_de_varios
_escribir_real = _em._escribir_usuario

def _probar(grupos):
    equipo = _Puerta(grupos)
    _em._conectar = lambda *a, **kw: (equipo, "udp")
    _em.huellas_de_varios = lambda d, nums, *a, **kw: (
        {str(n).strip(): (type("Q", (), {"name": "ALGUIEN"})(), ["h"]) for n in nums}, {})
    def _fake_escribir(conexion, uid, nombre, privilegio, grupo, numero, franja=0):
        conexion.escrito = grupo
        conexion.users += 1
        conexion._u.append(type("U", (), {"uid": uid, "user_id": str(numero),
                                          "name": nombre, "group_id": grupo,
                                          "privilege": privilegio})())
    _em._escribir_usuario = _fake_escribir
    return _cargar_real({"nombre": "P", "ip": "1.1.1.1"}, {"nombre": "M", "ip": "2"}, "9988")

_r1 = _probar(["1", "1", "1"])
chequear("con todos en el mismo grupo, no es ambiguo",
         _r1["grupo_ambiguo"] is False, _r1)
chequear("y copia ese grupo", _r1["grupo"] == "1", _r1)

_r2 = _probar(["1", "1", "0"])
chequear("con la gente repartida, se marca como ambiguo",
         _r2["grupo_ambiguo"] is True, _r2)
chequear("elige el mas frecuente igual", _r2["grupo"] == "1", _r2)
chequear("y dice como esta repartida la puerta",
         _r2["reparto_grupos"] == {"1": 2, "0": 1}, _r2["reparto_grupos"])

# --- Los horarios que guarda el lector --------------------------------------
# Un grupo apunta a franjas, y una franja trae los horarios de los siete dias.
# La cuenta es lo unico que puede estar mal sin que se note: la primera version
# leia cada dia como un entero de dos bytes, que los da vuelta, y una franja
# abierta de 00:00 a 23:59 aparecia como "?3B17" -- parecia que el equipo
# contestaba basura. Estos son los bytes que contesto la .203 de verdad.
from sync import franjas as _fr

_FRANJA_203 = bytes.fromhex("00 00 17 3b" * 7)
_GRUPO_0 = bytes.fromhex("00 00 00 00")
_GRUPO_1 = bytes.fromhex("03 00 00 00 01 00 00 00 01 00 00 00 01 00 00 00")

_semana = _fr.semana(_FRANJA_203)
chequear("un dia son cuatro bytes: hora y minuto de inicio y de fin",
         _fr.dia(bytes([8, 30, 17, 45])) == (8, 30, 17, 45))
chequear("00 00 17 3b es de 00:00 a 23:59, no un entero dado vuelta",
         _semana[0] == (0, 0, 23, 59), _semana[0])
chequear("y los siete dias se leen igual", len(set(_semana)) == 1, _semana)
chequear("la franja de la .203 esta abierta", _fr.abierta(_semana) is True)
chequear("no esta vacia, que es otra cosa", _fr.vacia(_semana) is False)
chequear("una franja en cero si esta vacia",
         _fr.vacia(_fr.semana(bytes(28))) is True)
chequear("una franja en cero no cuenta como abierta",
         _fr.abierta(_fr.semana(bytes(28))) is False)
chequear("una hora imposible devuelve None en vez de inventar",
         _fr.dia(bytes([25, 0, 10, 0])) is None)
chequear("un minuto imposible tambien", _fr.dia(bytes([10, 61, 11, 0])) is None)
chequear("un bloque cortado tampoco se adivina",
         _fr.dia(bytes([1, 2])) is None)
chequear("un horario se muestra legible",
         _fr.texto_dia((8, 5, 23, 59)) == "08:05 a 23:59",
         _fr.texto_dia((8, 5, 23, 59)))
chequear("y un dia ilegible lo dice", _fr.texto_dia(None) == "ilegible")
chequear("un dia todo en cero es un dia cerrado, no de 00:00 a 00:00",
         _fr.texto_dia((0, 0, 0, 0)) == "cerrado", _fr.texto_dia((0, 0, 0, 0)))

_suyas_1, _otro_1 = _fr.franjas_del_grupo(_GRUPO_1)
chequear("el grupo 1 de la .203 usa la franja 1", _suyas_1 == [1, 1, 1], _suyas_1)
chequear("y el primer campo se devuelve aparte, sin nombre inventado",
         _otro_1 == 3, _otro_1)
_suyas_0, _otro_0 = _fr.franjas_del_grupo(_GRUPO_0)
chequear("el grupo 0 no tiene franja asignada", _suyas_0 == [], _suyas_0)
chequear("sin respuesta no revienta", _fr.franjas_del_grupo(b"") == ([], None))

chequear("ocho franjas iguales se nombran como un rango",
         _fr.rango([1, 2, 3, 4, 5, 6, 7, 8]) == "franjas 1 a 8",
         _fr.rango([1, 2, 3, 4, 5, 6, 7, 8]))
chequear("una sola se nombra en singular", _fr.rango([3]) == "franja 3")
chequear("y las salteadas se listan",
         _fr.rango([1, 4, 9]) == "franjas 1, 4, 9", _fr.rango([1, 4, 9]))
chequear("ninguna tambien tiene nombre", _fr.rango([]) == "ninguna franja")

# --- Que nombre se le escribe a una persona en una puerta -------------------
# El nombre corto que el lector muestra al apoyar el dedo. Sale del legajo
# --pestana Accesos-- y si ahi no hay nada se copia el que el equipo de fichaje
# ya le muestra. El numero es el ultimo recurso y se avisa, porque pasa solo
# cuando faltan los dos y conviene que alguien lo arregle.
_nom = _em._nombre_para_el_lector
_DEL_EQUIPO = type("Q", (), {"name": "JPEREZ"})()

chequear("manda el configurado en el legajo",
         _nom("J PEREZ", _DEL_EQUIPO, "42") == ("J PEREZ", False),
         _nom("J PEREZ", _DEL_EQUIPO, "42"))
chequear("si no hay, se copia el que el equipo ya le muestra",
         _nom(None, _DEL_EQUIPO, "42") == ("JPEREZ", False),
         _nom(None, _DEL_EQUIPO, "42"))
chequear("un configurado en blanco no cuenta como configurado",
         _nom("   ", _DEL_EQUIPO, "42") == ("JPEREZ", False),
         _nom("   ", _DEL_EQUIPO, "42"))
chequear("sin ninguno de los dos queda el numero, y se avisa",
         _nom(None, None, "42") == ("42", True), _nom(None, None, "42"))
chequear("un nombre vacio en el equipo tampoco cuenta",
         _nom(None, type("Q", (), {"name": "  "})(), "42") == ("42", True))
# No se usa el apellido del legajo como ultimo recurso aunque exista: quedaria
# escrito en el equipo pareciendo una decision que alguien tomo.
chequear("y no se inventa un nombre a partir del legajo",
         _nom(None, None, "42")[0] == "42", _nom(None, None, "42"))


# --- Aplicar todo el plan de una puerta, en una sola pasada ------------------
# Fila por fila serian treinta lecturas completas del mismo equipo. Aca se lee
# una vez, se hacen todos los cambios y se lee una vez. La verificacion no se
# afloja por eso: al final se comprueba cada linea Y que nadie mas haya cambiado.


class _PuertaPasada:
    """Un equipo que acepta altas y bajas. `rompe` simula lo que puede salir mal."""

    user_packet_size = 28
    encoding = "latin-1"

    def __init__(s, cargados=("100", "101", "102"), rompe=None):
        s.rompe = rompe        # None | "pisa_a_otro" | "no_borra" | "sin_huellas"
        s._u = [type("U", (), {"uid": i + 1, "user_id": n, "name": f"N{n}",
                               "group_id": "1", "privilege": 0})()
                for i, n in enumerate(cargados)]
        s._h = {u.uid: 1 for u in s._u}
        s.borrados, s.escritos = [], []

    @property
    def users(s): return len(s._u)
    def get_users(s): return list(s._u)
    def get_templates(s):
        return [type("H", (), {"uid": uid, "valid": 1,
                               "json_pack": lambda s2: {"t": "x"}})()
                for uid, n in s._h.items() for _ in range(n)]
    def refresh_data(s): pass
    def delete_user(s, uid=None, user_id=""):
        s.borrados.append(uid)
        if s.rompe == "no_borra":
            return
        s._u = [u for u in s._u if u.uid != uid]
        s._h.pop(uid, None)
        if s.rompe == "pisa_a_otro" and s._u:
            s._u[0].name = "PISADO"
    def save_user_template(s, u, huellas):
        s._h[u.uid] = 0 if s.rompe == "sin_huellas" else len(huellas)
    def disconnect(s): pass


def _preparar_pasada(equipo):
    _em._conectar = lambda *a, **kw: (equipo, "udp")
    _em._respaldar = lambda *a, **kw: "C:/x/r.json"
    _em.huellas_de_varios = lambda disp, numeros, *a, **kw: (
        {str(n): (type("Q", (), {"name": f"MAESTRO{n}"})(), ["h1", "h2"])
         for n in numeros if str(n) != "sinhuella"},
        {"sinhuella": "está en el equipo de asistencia pero sin ninguna huella"})
    def _esc_fake(conexion, uid, nombre, privilegio, grupo, numero, franja=0):
        conexion.escritos.append((uid, numero, nombre, grupo))
        conexion._u.append(type("U", (), {"uid": uid, "user_id": str(numero),
                                          "name": nombre, "group_id": grupo,
                                          "privilege": privilegio})())
        conexion._h[uid] = 0
    _em._escribir_usuario = _esc_fake

_real_huellas_varias = _em.huellas_de_varios
_real_respaldar = _em._respaldar
_PUERTA_P = {"nombre": "Puerta P", "ip": "10.0.0.8"}
_MAESTRO_P = {"nombre": "Reloj", "ip": "10.0.0.1"}

_eq = _PuertaPasada()
_preparar_pasada(_eq)
_rp = _em.aplicar_en_puerta(_PUERTA_P, _MAESTRO_P,
                            [{"user_id": "500", "nombre": "NUEVO"}],
                            [{"user_id": "100", "motivo": "Dado de baja"}])
chequear("la pasada sale bien", _rp["ok"] is True, _rp.get("error"))
chequear("carga y saca en la misma conexion", len(_rp["resultados"]) == 2, _rp)
_alta = next(x for x in _rp["resultados"] if x["accion"] == "cargar")
_baja = next(x for x in _rp["resultados"] if x["accion"] == "sacar")
chequear("el alta quedo con sus dos huellas",
         _alta["ok"] and _alta["huellas"] == 2, _alta)
chequear("y con el nombre que se le paso", _alta["nombre_escrito"] == "NUEVO", _alta)
chequear("la baja se hizo", _baja["ok"] is True, _baja)
chequear("y guardo respaldo antes de borrar", _baja["respaldo"], _baja)
chequear("las bajas van primero, para que una alta que falle no las postergue",
         _eq.borrados and _eq.escritos and True, (_eq.borrados, _eq.escritos))
chequear("informa cuantos no se tocaron", _rp["otros"] == 2, _rp)

# Una sin huella no se carga, y no detiene a las demas.
_eq = _PuertaPasada()
_preparar_pasada(_eq)
_rp = _em.aplicar_en_puerta(_PUERTA_P, _MAESTRO_P,
                            [{"user_id": "sinhuella"}, {"user_id": "501"}], [])
_sin = next(x for x in _rp["resultados"] if x["user_id"] == "sinhuella")
_ok2 = next(x for x in _rp["resultados"] if x["user_id"] == "501")
chequear("sin huella no se carga", _sin["ok"] is False, _sin)
chequear("y se dice por que", "sin ninguna huella" in _sin["error"], _sin)
chequear("pero la siguiente se carga igual", _ok2["ok"] is True, _ok2)
chequear("la pasada se marca como no del todo bien", _rp["ok"] is False, _rp)

# Lo que esta verificacion existe para encontrar: alguien que nadie pidio tocar.
_eq = _PuertaPasada(rompe="pisa_a_otro")
_preparar_pasada(_eq)
_rp = _em.aplicar_en_puerta(_PUERTA_P, _MAESTRO_P, [],
                            [{"user_id": "100", "motivo": "Dado de baja"}])
chequear("si la pasada le cambio algo a otro, no esta ok", _rp["ok"] is False, _rp)
chequear("y se dice a quien y que le cambio",
         _rp["problemas"] and "101" in _rp["problemas"][0]
         and "PISADO" in _rp["problemas"][0], _rp["problemas"])

# Un equipo que acepta el borrado y no borra: queda pendiente, no fallado.
_eq = _PuertaPasada(rompe="no_borra")
_preparar_pasada(_eq)
_rp = _em.aplicar_en_puerta(_PUERTA_P, _MAESTRO_P, [],
                            [{"user_id": "100", "motivo": "Dado de baja"}])
_b = _rp["resultados"][0]
chequear("si el equipo no borra, no se da por hecho", _b["ok"] is False, _b)
chequear("y se marca como que sigue cargado", _b.get("sigue") is True, _b)

# Una huella que no quedo: el alta figura hecha y la persona no abre.
_eq = _PuertaPasada(rompe="sin_huellas")
_preparar_pasada(_eq)
_rp = _em.aplicar_en_puerta(_PUERTA_P, _MAESTRO_P, [{"user_id": "502"}], [])
_a = _rp["resultados"][0]
chequear("un alta sin huellas no se da por buena", _a["ok"] is False, _a)
chequear("diciendo cuantas quedaron de cuantas",
         "0 de 2" in (_a["error"] or ""), _a)

# Lo que ya estaba no se vuelve a hacer, y se dice.
_eq = _PuertaPasada()
_preparar_pasada(_eq)
_rp = _em.aplicar_en_puerta(_PUERTA_P, _MAESTRO_P,
                            [{"user_id": "101"}], [{"user_id": "999"}])
chequear("no recarga a quien ya estaba",
         any(x.get("ya_estaba") for x in _rp["resultados"]), _rp["resultados"])
chequear("ni saca a quien ya no estaba",
         any(x.get("no_estaba") for x in _rp["resultados"]), _rp["resultados"])
chequear("y no le pidio nada al equipo",
         _eq.borrados == [] and _eq.escritos == [], (_eq.borrados, _eq.escritos))

# Con la lista incompleta no se toca nada: el indice sale de esa lista.
class _PuertaCorta(_PuertaPasada):
    @property
    def users(s): return len(s._u) + 1
_eq = _PuertaCorta()
_preparar_pasada(_eq)
_rp = _em.aplicar_en_puerta(_PUERTA_P, _MAESTRO_P, [{"user_id": "503"}],
                            [{"user_id": "100", "motivo": "x"}])
chequear("con la lectura incompleta no se aplica nada", _rp["ok"] is False, _rp)
chequear("y no se escribio ni se borro",
         _eq.borrados == [] and _eq.escritos == [], (_eq.borrados, _eq.escritos))

_em.huellas_de_varios = _real_huellas_varias
_em._respaldar = _real_respaldar
_em._conectar = _conectar_real
_em._escribir_usuario = _escribir_real


# --- Cambiarle el nombre a alguien que ya esta cargado ----------------------
# El nombre corto se decide en el legajo y hay que poder llevarlo al equipo. Lo
# delicado es que reescribir el registro de un usuario PUEDE borrarle las
# huellas: el protocolo manda usuario y huellas en el mismo paquete y nunca se
# probo sobre alguien que ya las tenia. Se hace a prueba de las dos respuestas.


class _PuertaParaActualizar:
    """`borra_huellas` simula el firmware que las pierde al reescribir."""

    user_packet_size = 28
    encoding = "latin-1"

    def __init__(s, borra_huellas=False, nombre="VIEJO", huellas=2):
        s.borra_huellas = borra_huellas
        s._u = [type("U", (), {"uid": 1, "user_id": "100", "name": nombre,
                               "group_id": "1", "privilege": 0})(),
                type("U", (), {"uid": 2, "user_id": "101", "name": "OTRO",
                               "group_id": "1", "privilege": 0})()]
        s._h = {1: huellas, 2: 1}
        s.escrituras, s.templates_grabados = [], 0

    @property
    def users(s): return len(s._u)
    def get_users(s): return list(s._u)
    def get_templates(s):
        return [type("H", (), {"uid": uid, "valid": 1,
                               "json_pack": lambda s2: {"t": "x"}})()
                for uid, n in s._h.items() for _ in range(n)]
    def refresh_data(s): pass
    def save_user_template(s, u, huellas):
        s.templates_grabados += 1
        s._h[u.uid] = len(huellas)
    def disconnect(s): pass


def _preparar_act(equipo):
    _em._conectar = lambda *a, **kw: (equipo, "udp")
    def _esc(conexion, uid, nombre, privilegio, grupo, numero, franja=0):
        conexion.escrituras.append((uid, numero, nombre, privilegio, grupo))
        for u in conexion._u:
            if u.uid == uid:
                u.name, u.privilege, u.group_id = nombre, privilegio, grupo
        if conexion.borra_huellas:
            conexion._h[uid] = 0
    _em._escribir_usuario = _esc

_PUERTA_A = {"nombre": "Puerta A", "ip": "10.0.0.7"}

# Caso normal: el equipo conserva las huellas al reescribir.
_eq = _PuertaParaActualizar()
_preparar_act(_eq)
_ra = _em.actualizar_en_puerta(_PUERTA_A, "100", nombre="NUEVO")
chequear("cambia el nombre", _ra["ok"] is True, _ra)
chequear("y lo dice", _ra["nombre_escrito"] == "NUEVO", _ra)
chequear("diciendo cual era antes", _ra["nombre_anterior"] == "VIEJO", _ra)
chequear("las huellas quedaron", _ra["huellas"] == 2, _ra)
chequear("y no hizo falta reponerlas", _ra["huellas_repuestas"] is False, _ra)
chequear("el grupo se reescribe igual, no se decide de nuevo",
         _eq.escrituras[0][4] == "1", _eq.escrituras)
chequear("y el indice interno es el que ya tenia", _eq.escrituras[0][0] == 1,
         _eq.escrituras)

# El caso que justifica la funcion: el equipo le borra las huellas.
_eq = _PuertaParaActualizar(borra_huellas=True)
_preparar_act(_eq)
_ra = _em.actualizar_en_puerta(_PUERTA_A, "100", nombre="NUEVO")
chequear("si el equipo borra las huellas al reescribir, se reponen",
         _ra["huellas_repuestas"] is True, _ra)
chequear("y la persona termina con las que tenia", _ra["huellas"] == 2, _ra)
chequear("la operacion se da por buena", _ra["ok"] is True, _ra)
chequear("se grabaron los templates una vez", _eq.templates_grabados == 1,
         _eq.templates_grabados)

# Sin cambios no se escribe nada: reescribir por las dudas es arriesgar las
# huellas de alguien a cambio de nada.
_eq = _PuertaParaActualizar(nombre="IGUAL")
_preparar_act(_eq)
_ra = _em.actualizar_en_puerta(_PUERTA_A, "100", nombre="IGUAL")
chequear("si el nombre ya es ese, no se escribe", _ra.get("sin_cambios") is True, _ra)
chequear("y no se le pidio nada al equipo", _eq.escrituras == [], _eq.escrituras)

# A quien no esta cargado no se lo carga: eso es otra operacion.
_eq = _PuertaParaActualizar()
_preparar_act(_eq)
_ra = _em.actualizar_en_puerta(_PUERTA_A, "999777", nombre="X")
chequear("a quien no esta cargado no lo crea", _ra.get("no_estaba") is True, _ra)
chequear("y no escribio nada", _eq.escrituras == [], _eq.escrituras)

# Y los demas tienen que quedar enteros, como en todo lo que escribe.
_eq = _PuertaParaActualizar()
_preparar_act(_eq)
_ra = _em.actualizar_en_puerta(_PUERTA_A, "100", nombre="NUEVO")
chequear("los demas quedan intactos", _ra["problemas"] == [], _ra["problemas"])
chequear("e informa cuantos son", _ra["otros"] == 1, _ra)

_em._conectar = _conectar_real
_em._escribir_usuario = _escribir_real


# --- Sacar a alguien, y verificar que los demas quedaron enteros -------------
# Que la persona ya no este es la parte facil. Lo que hay que probar es que el
# borrado no se llevo puesto a nadie mas: estos equipos borran por indice, y un
# indice corrido no se nota hasta que alguien se queda afuera.


class _PuertaParaBorrar:
    """Un equipo del que se puede sacar gente. `rompe` simula los desastres."""

    def __init__(s, cuantos=3, rompe=None):
        s.rompe = rompe            # None | "no_borra" | "pisa_a_otro" | "lista_corta"
        s._u = [type("U", (), {"uid": i + 1, "user_id": str(100 + i),
                               "name": f"N{i}", "group_id": "1", "privilege": 0})()
                for i in range(cuantos)]
        s.borrados = []

    @property
    def users(s):
        return len(s._u) + (1 if s.rompe == "lista_corta" else 0)

    def get_users(s): return list(s._u)
    def get_templates(s):
        return [type("H", (), {"uid": 1, "valid": 1,
                               "json_pack": lambda s2: {"t": "x"}})()]
    def delete_user(s, uid=None, user_id=""):
        s.borrados.append(uid)
        if s.rompe == "no_borra":
            return
        s._u = [u for u in s._u if u.uid != uid]
        if s.rompe == "pisa_a_otro" and s._u:
            s._u[0].name = "SE ME CAMBIO EL NOMBRE"
    def disconnect(s): pass


_PUERTA_X = {"nombre": "Puerta X", "ip": "10.0.0.9"}

def _probar_sacar(**kw):
    equipo = _PuertaParaBorrar(**kw)
    _em._conectar = lambda *a, **kw: (equipo, "udp")
    _em._respaldar = lambda *a, **kw: "C:/x/respaldo.json"
    return equipo, _em.sacar_de_puerta(_PUERTA_X, "100")

_real_respaldar = _em._respaldar

_eq, _rs = _probar_sacar()
chequear("saca a la persona pedida", _rs["ok"] is True, _rs)
chequear("y se lo pidio al equipo por su indice interno",
         _eq.borrados == [1], _eq.borrados)
chequear("informa cuantos quedaron", _rs["otros"] == 2, _rs)
chequear("y que guardo respaldo antes de borrar", _rs["respaldo"], _rs)

# Un equipo que acepta el comando y no hace nada. Queda pendiente, no fallado:
# el borrado sigue debiendose y la proxima pasada lo retoma.
_eq, _rs = _probar_sacar(rompe="no_borra")
chequear("si el equipo no borra, no se da por hecho", _rs["ok"] is False, _rs)
chequear("se dice que sigue cargado", "sigue cargado" in _rs["error"], _rs)
chequear("y se registra como pendiente, no como falla",
         _em.resultado_de_sacar(_rs) == "pendiente", _rs)

# Lo que esta verificacion existe para encontrar.
_eq, _rs = _probar_sacar(rompe="pisa_a_otro")
chequear("si el borrado le cambio algo a otro, no esta ok", _rs["ok"] is False, _rs)
chequear("y se dice a quien y que le cambio",
         _rs["problemas"] and "nombre" in _rs["problemas"][0], _rs["problemas"])
chequear("eso si es una falla, no algo pendiente",
         _em.resultado_de_sacar(_rs) == "falló", _rs)

# Si la lista vino corta no se borra NADA: el indice se calcula de esa lista.
_eq, _rs = _probar_sacar(rompe="lista_corta")
chequear("con la lista incompleta no se borra nada", _rs["ok"] is False, _rs)
chequear("y no se le pidio nada al equipo", _eq.borrados == [], _eq.borrados)
chequear("diciendo que la lectura no cierra",
         "no confiable" in _rs["error"], _rs["error"])

_eqv = _PuertaParaBorrar()
_em._conectar = lambda d: (_eqv, "udp")
_rs = _em.sacar_de_puerta(_PUERTA_X, "999777")
chequear("un numero que no esta en el equipo se avisa aparte",
         _rs.get("no_estaba") is True, _rs)
chequear("y no se borro nada", _eqv.borrados == [], _eqv.borrados)

_em._respaldar = _real_respaldar
_em._conectar = _conectar_real


# --- El horario que le da un grupo ------------------------------------------
# Los bytes son los que contesto el .209: la franja 2 de 08:00 a 19:30 y la 3 de
# 17:00 a 03:00, que cruza la medianoche. Una franja abierta puede salir bien de
# casualidad; estas no.
import sync.lectores as _lecf

_F209_DIA = bytes.fromhex("08 00 13 1e" * 7)
_F209_NOCHE = bytes.fromhex("11 00 03 00" * 7)
_F_ABIERTA = bytes.fromhex("00 00 17 3b" * 7)
_F_MIXTA = bytes.fromhex("08 00 12 00" * 5 + "09 00 0d 00" + "00 00 00 00")

chequear("la franja de dia del .209 es de 08:00 a 19:30",
         _fr.describir(_fr.semana(_F209_DIA)) == "todos los días de 08:00 a 19:30",
         _fr.describir(_fr.semana(_F209_DIA)))
chequear("la de noche cruza la medianoche y se muestra tal cual",
         _fr.describir(_fr.semana(_F209_NOCHE)) == "todos los días de 17:00 a 03:00",
         _fr.describir(_fr.semana(_F209_NOCHE)))
chequear("una abierta se dice en palabras, no con horarios",
         _fr.describir(_fr.semana(_F_ABIERTA)) == "todo el día, los siete días",
         _fr.describir(_fr.semana(_F_ABIERTA)))
chequear("una en cero no es un horario",
         _fr.describir(_fr.semana(bytes(28))) == "sin horarios cargados")
chequear("los dias iguales se agrupan y el cerrado se nombra",
         _fr.describir(_fr.semana(_F_MIXTA))
         == "lun a vie 08:00 a 18:00, sáb 09:00 a 13:00, dom cerrado",
         _fr.describir(_fr.semana(_F_MIXTA)))

_DEFS = {1: _fr.semana(_F_ABIERTA), 2: _fr.semana(_F209_DIA),
         3: _fr.semana(_F209_NOCHE)}

_t, _r = _fr.describir_grupo([], _DEFS)
chequear("un grupo sin franja no restringe", _r is False, (_t, _r))
_t, _r = _fr.describir_grupo([1, 1, 1], _DEFS)
chequear("un grupo con franja abierta tampoco", _r is False, (_t, _r))
_t, _r = _fr.describir_grupo([2, 2, 2], _DEFS)
chequear("un grupo con horario si restringe", _r is True, (_t, _r))
chequear("y lo dice una vez, no tres veces", _t.count("franja 2") == 1, _t)
_t, _r = _fr.describir_grupo([2, 3, 0], _DEFS)
chequear("con dos franjas distintas se describen las dos",
         "franja 2" in _t and "franja 3" in _t, _t)
_t, _r = _fr.describir_grupo([9, 0, 0], _DEFS)
chequear("una franja que no se pudo leer se dice", "no se pudo leer" in _t, _t)

# Lo importante: no se pudo leer NO es lo mismo que no tener horario. Decir
# "abre a cualquier hora" sobre algo que nadie leyo es lo que deja a alguien
# afuera sin que nada lo avise.
_t, _r = _fr.describir_grupo(None, _DEFS)
chequear("si no se pudo leer el grupo, restringe queda sin saber",
         _r is None, (_t, _r))
chequear("y el texto lo dice en vez de tranquilizar",
         "no se pudo leer" in _t, _t)


class _EquipoConFranjas:
    """Contesta como el .209: grupo 1 con la franja 2, grupo 0 sin ninguna."""

    def __init__(self, contesta=True):
        self.contesta = contesta
        self._ZK__data = b""

    def _ZK__send_command(self, comando, datos, respuesta):
        from zk import const
        if not self.contesta:
            self._ZK__data = b""
            return {"status": False}
        n = datos[0]
        if comando == const.CMD_GRPTZ_RRQ:
            self._ZK__data = (
                bytes.fromhex("03 00 00 00 02 00 00 00 02 00 00 00 02 00 00 00")
                if n == 1 else bytes.fromhex("00 00 00 00"))
        elif comando == const.CMD_TZ_RRQ:
            self._ZK__data = _F209_DIA if n == 2 else _F_ABIERTA
        return {"status": True}


_v = _lecf.ventana_del_grupo(_EquipoConFranjas(), "1")
chequear("se lee el horario del grupo desde el equipo",
         _v["restringe"] is True and _v["franjas"] == [2], _v)
chequear("y viene en palabras para mostrar", "08:00 a 19:30" in _v["texto"], _v)
_v0 = _lecf.ventana_del_grupo(_EquipoConFranjas(), "0")
chequear("un grupo sin franja se informa como sin horario",
         _v0["restringe"] is False and _v0["franjas"] == [], _v0)
_vm = _lecf.ventana_del_grupo(_EquipoConFranjas(contesta=False), "1")
chequear("si el equipo no contesta, no se afirma que abra siempre",
         _vm["restringe"] is None, _vm)


_em._conectar = _conectar_real
_em.huellas_de_varios = _huellas_real
_em._escribir_usuario = _escribir_real

# --- En que grupo va la persona que se carga --------------------------------
# El criterio era "el mas usado de esta puerta". Sigue valiendo, salvo cuando el
# mas usado impone horario: eso nunca es lo que se quiso, porque el unico equipo
# donde alguien asigno horarios a proposito es el .209 y en los demas las
# franjas abiertas son el estado de fabrica.


class _PuertaConGrupos:
    """Un equipo que contesta por sus grupos. `horario` son los que restringen."""

    def __init__(self, horario=(), muda=False):
        self.horario = {str(g) for g in horario}
        self.muda = muda
        self._ZK__data = b""

    def _ZK__send_command(self, comando, datos, respuesta):
        from zk import const
        if self.muda:
            self._ZK__data = b""
            return {"status": False}
        n = datos[0]
        if comando == const.CMD_GRPTZ_RRQ:
            # Los que restringen apuntan a la franja 2; los demas, a la 1.
            cual = 2 if str(n) in self.horario else 1
            self._ZK__data = pack("<IIII", 3, cual, cual, cual)
        elif comando == const.CMD_TZ_RRQ:
            self._ZK__data = (bytes.fromhex("08 00 13 1e" * 7) if n == 2
                              else bytes.fromhex("00 00 17 3b" * 7))
        return {"status": True}


from collections import Counter as _Cnt
from struct import pack

_g, _v, _av = _em._elegir_grupo(_PuertaConGrupos(), _Cnt({"1": 18, "0": 9}))
chequear("sin horarios en juego, se copia el mas usado", _g == "1", (_g, _av))
chequear("y no se avisa nada, porque la eleccion no tuvo consecuencias",
         _av is None, _av)
chequear("igual se informa el horario de ese grupo",
         _v["restringe"] is False, _v)

# El caso que importa: el mas usado tiene horario. Copiarlo le pondria un
# horario a alguien sin que nadie lo haya decidido.
_g, _v, _av = _em._elegir_grupo(_PuertaConGrupos(horario=["1"]),
                                _Cnt({"1": 18, "0": 9}))
chequear("si el mas usado tiene horario, se elige otro sin horario",
         _g == "0", (_g, _av))
chequear("y se explica por que se desvio del mas usado",
         _av and "tiene horario" in _av and "08:00 a 19:30" in _av, _av)
chequear("el grupo elegido no restringe", _v["restringe"] is False, _v)

# Si TODOS tienen horario no se bloquea: dejaria la puerta inutilizable hasta
# que alguien camine hasta el equipo, y el horario ya se informa en rojo.
_g, _v, _av = _em._elegir_grupo(_PuertaConGrupos(horario=["1", "0"]),
                                _Cnt({"1": 18, "0": 9}))
chequear("si todos tienen horario, se copia el mas usado igual", _g == "1", _g)
chequear("el horario elegido se informa como restrictivo",
         _v["restringe"] is True, _v)
chequear("y se dice que no habia ninguno sin horario",
         _av and "todos los grupos" in _av, _av)

# No poder leer no es lo mismo que no tener horario.
_g, _v, _av = _em._elegir_grupo(_PuertaConGrupos(muda=True),
                                _Cnt({"1": 18, "0": 9}))
chequear("si el equipo no contesta, se copia el mas usado", _g == "1", _g)
chequear("sin afirmar que no tiene horario", _v["restringe"] is None, _v)
chequear("y diciendo que no se pudo comprobar",
         _av and "no se pudo leer" in _av, _av)

_g, _v, _av = _em._elegir_grupo(_PuertaConGrupos(), _Cnt())
chequear("en una puerta vacia no hay de donde copiar: va el 1", _g == "1", _g)
chequear("y no se inventa un horario", _v is None, _v)


print(f"\n{'='*52}\n  {ok} pasaron, {fallos} fallaron\n{'='*52}")
raise SystemExit(1 if fallos else 0)
