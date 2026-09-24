"""El plan: qué habría que cambiar en cada lector para que refleje la política.

No encola cambios. Cuando alguien toca un perfil o una excepción, lo único que
cambia es la base — el **estado deseado**. Después esto lee cada equipo, lo
compara contra lo deseado y arma la diferencia.

Se eligió así y no una cola de órdenes porque una orden envejece mal: si en el
medio alguien tocó el equipo a mano, o restauraron un backup, "agregar a Juan en
Personal" puede haber dejado de corresponder. Comparar estados es idempotente,
se autocorrige venga el desvío de donde venga, y un equipo apagado no pierde
nada: la próxima vez que se lo alcance, se corrige.

Acá no se escribe nada. Esto arma el plan; aplicarlo es otra cosa, y no se
habilita hasta que escribir esté probado contra los equipos reales.
"""
import logging

logger = logging.getLogger(__name__)

# Por qué alguien sobra en un lector. El orden importa: es el que se usa para
# mostrar primero lo que más urge.
MOTIVOS_SACAR = {
    "egresado":     "Dado de baja en el sistema",
    "desconocido":  "No existe en el sistema",
    "sin_derecho":  "Su perfil ya no incluye esta puerta",
}


def estado_deseado(conn) -> dict:
    """
    Quién debería estar cargado en cada puerta, según perfiles y excepciones.

    Devuelve {dispositivo_id: {user_id: datos_del_empleado}}.

    Solo entran los activos con número de dispositivo: sin número no hay forma
    de cargarlos en un lector, y un egresado no debería abrir nada.
    """
    puertas_de_perfil: dict[int, set] = {}
    for r in conn.execute("SELECT perfil_id, dispositivo_id FROM perfiles_dispositivos"):
        puertas_de_perfil.setdefault(r["perfil_id"], set()).add(r["dispositivo_id"])

    excepciones: dict[int, list] = {}
    for r in conn.execute(
        "SELECT empleado_id, dispositivo_id, modo FROM accesos_excepciones"
    ):
        excepciones.setdefault(r["empleado_id"], []).append(dict(r))

    # El perfil propio y nada más. El cargo propone, no da acceso: si diera,
    # cambiarle el cargo a alguien le cambiaría las puertas, y eso lo puede
    # hacer quien edita empleados aunque no tenga permiso de accesos.
    deseado: dict[int, dict] = {}
    for e in conn.execute(
        """SELECT e.id, e.user_id, e.nombre, e.apellido,
                  e.perfil_acceso_id AS perfil_id
             FROM empleados e
            WHERE e.activo = 1 AND e.user_id IS NOT NULL AND TRIM(e.user_id) <> ''"""
    ):
        puertas = set(puertas_de_perfil.get(e["perfil_id"], ())) if e["perfil_id"] else set()
        for x in excepciones.get(e["id"], ()):
            if x["modo"] == "quitar":
                puertas.discard(x["dispositivo_id"])
            else:
                puertas.add(x["dispositivo_id"])

        if not puertas:
            continue
        datos = {"empleado_id": e["id"], "user_id": str(e["user_id"]).strip(),
                 "nombre": f"{e['apellido']}, {e['nombre']}".strip(", ")}
        for did in puertas:
            deseado.setdefault(did, {})[datos["user_id"]] = datos
    return deseado


def armar_plan(conn, puertas: list, lecturas: dict, maestro: dict | None) -> dict:
    """
    Compara lo leído de cada puerta contra lo que debería tener.

    `lecturas` es {dispositivo_id: resultado de leer_padron}.
    `maestro` es el resultado de leer el equipo de asistencia, o None si no se
    pudo: de ahí sale la huella que habría que copiar, así que sin él no se
    puede saber a quién falta enrolar.
    """
    deseado = estado_deseado(conn)
    empleados = {
        str(r["user_id"]).strip(): dict(r)
        for r in conn.execute(
            """SELECT id, user_id, nombre, apellido, activo, fecha_egreso
                 FROM empleados WHERE user_id IS NOT NULL"""
        )
    }

    # Quién tiene huella para copiar. Si el maestro no contestó no se puede
    # saber, y el plan lo dice en vez de suponer que todos la tienen.
    con_huella = None
    if maestro and maestro.get("ok"):
        con_huella = {u["user_id"] for u in maestro["usuarios"]}

    salida, total = [], {"agregar": 0, "sacar": 0, "sin_huella": 0,
                         "sin_leer": 0, "puertas": len(puertas)}

    for d in puertas:
        lectura = lecturas.get(d["id"], {"ok": False, "error": "sin resultado", "usuarios": []})
        fila = {"id": d["id"], "nombre": d["nombre"], "ubicacion": d["ubicacion"],
                "ok": lectura["ok"], "error": lectura.get("error"),
                "agregar": [], "sacar": []}

        if not lectura["ok"]:
            total["sin_leer"] += 1
            salida.append(fila)
            continue

        actual = {u["user_id"] for u in lectura["usuarios"]}
        debe = deseado.get(d["id"], {})

        for user_id in sorted(debe.keys() - actual, key=lambda x: (len(x), x)):
            persona = dict(debe[user_id])
            # Sin huella en el maestro no hay nada que copiar: cargar al usuario
            # sin su huella lo deja sin poder abrir igual, y encima parece hecho.
            persona["sin_huella"] = (con_huella is not None and user_id not in con_huella)
            if persona["sin_huella"]:
                total["sin_huella"] += 1
            fila["agregar"].append(persona)

        for user_id in sorted(actual - debe.keys(), key=lambda x: (len(x), x)):
            emp = empleados.get(user_id)
            if emp is None:
                motivo = "desconocido"
            elif not emp["activo"]:
                motivo = "egresado"
            else:
                motivo = "sin_derecho"
            fila["sacar"].append({
                "user_id": user_id,
                "nombre": (f"{emp['apellido']}, {emp['nombre']}".strip(", ")
                           if emp else None),
                "empleado_id": emp["id"] if emp else None,
                "fecha_egreso": emp["fecha_egreso"] if emp else None,
                "motivo": motivo,
                "motivo_texto": MOTIVOS_SACAR[motivo],
            })

        orden = {"egresado": 0, "desconocido": 1, "sin_derecho": 2}
        fila["sacar"].sort(key=lambda s: (orden[s["motivo"]], len(s["user_id"]), s["user_id"]))
        total["agregar"] += len(fila["agregar"])
        total["sacar"] += len(fila["sacar"])
        salida.append(fila)

    return {
        "total": total,
        "puertas": salida,
        "huellas_verificadas": con_huella is not None,
    }
