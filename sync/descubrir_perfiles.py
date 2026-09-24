"""Deducir los perfiles de lo que los lectores ya tienen cargado.

Para qué sirve. Al empezar, nadie tiene perfil: son cientos de personas y
asignarlas de a una no es una opción. Pero los perfiles ya existen — están
materializados en los equipos. Si alguien está cargado en Personal y Cámaras
pero no en Oficina, su perfil *es* "todas menos oficina", aunque nadie lo haya
escrito nunca.

Entonces se agrupa a la gente por el conjunto exacto de puertas donde está, y
cada grupo es un perfil candidato. Tres decisiones en vez de trescientas.

Y de paso salta lo que hay que mirar: un grupo de una sola persona con una
combinación que no es ninguna de las demás suele ser un error de carga, o una
excepción que alguien hizo a mano y nadie anotó.

Es SOLO LECTURA sobre los equipos. Lo único que escribe, y en otra función, es
el perfil de los empleados en la base.
"""
import logging

logger = logging.getLogger(__name__)


def agrupar_por_puertas(lecturas: dict, puertas: list, empleados: dict) -> dict:
    """
    Agrupa a la gente por el conjunto exacto de puertas donde está cargada.

    `lecturas` es {dispositivo_id: resultado de leer_padron}, `puertas` las
    filas de los equipos, y `empleados` {user_id: fila}.

    Devuelve {grupos, sin_leer, ignorados}. Cada grupo trae el conjunto de
    puertas, quiénes lo componen, y cuántos de ellos ya tienen perfil propio
    —a esos no hay que tocarlos, ya son una decisión tomada—.
    """
    leidas = [d for d in puertas if lecturas.get(d["id"], {}).get("ok")]
    sin_leer = [
        {"id": d["id"], "nombre": d["nombre"],
         "error": lecturas.get(d["id"], {}).get("error", "sin resultado")}
        for d in puertas if not lecturas.get(d["id"], {}).get("ok")
    ]

    # Con una puerta sin leer, el conjunto de cada persona estaría incompleto y
    # los grupos saldrían mal. Mejor no proponer nada que proponer algo falso.
    if sin_leer:
        return {"grupos": [], "sin_leer": sin_leer, "ignorados": [],
                "completo": False}

    nombre_puerta = {d["id"]: d["nombre"] for d in puertas}
    donde: dict[str, set] = {}
    for d in leidas:
        for u in lecturas[d["id"]]["usuarios"]:
            donde.setdefault(u["user_id"], set()).add(d["id"])

    grupos: dict[frozenset, dict] = {}
    ignorados = []
    for user_id, puertas_de_esa in donde.items():
        emp = empleados.get(user_id)
        # Quien no está en el sistema, o está de baja, no entra: no hay a quién
        # asignarle un perfil, y un egresado no debería abrir nada.
        if emp is None:
            ignorados.append({"user_id": user_id, "motivo": "no existe en el sistema"})
            continue
        if not emp["activo"]:
            ignorados.append({"user_id": user_id, "nombre": _nombre(emp),
                              "motivo": "está dado de baja"})
            continue

        clave = frozenset(puertas_de_esa)
        g = grupos.setdefault(clave, {"puertas": sorted(puertas_de_esa),
                                      "personas": [], "con_perfil": 0})
        g["personas"].append({
            "empleado_id": emp["id"], "user_id": user_id, "nombre": _nombre(emp),
            "tiene_perfil": bool(emp["perfil_acceso_id"]),
        })
        if emp["perfil_acceso_id"]:
            g["con_perfil"] += 1

    salida = []
    for g in grupos.values():
        g["personas"].sort(key=lambda p: p["nombre"])
        g["nombres_puertas"] = [nombre_puerta.get(i, f"#{i}") for i in g["puertas"]]
        g["total"] = len(g["personas"])
        g["sin_perfil"] = g["total"] - g["con_perfil"]
        salida.append(g)

    # Los grupos grandes primero: son los perfiles de verdad. Los de una o dos
    # personas suelen ser el error de carga o la excepción que nadie anotó, y
    # conviene mirarlos con la cabeza fresca después de resolver lo obvio.
    salida.sort(key=lambda g: (-g["total"], g["puertas"]))
    ignorados.sort(key=lambda x: (len(x["user_id"]), x["user_id"]))
    return {"grupos": salida, "sin_leer": [], "ignorados": ignorados, "completo": True}


def _nombre(emp) -> str:
    return f"{emp['apellido']}, {emp['nombre']}".strip(", ")


def emparejar_con_perfiles(grupos: list, perfiles: list) -> None:
    """
    Marca en cada grupo si ya existe un perfil con exactamente esas puertas.

    Si existe, resolver el grupo es elegirlo; si no, hay que crear uno y
    ponerle nombre. La coincidencia tiene que ser exacta: un perfil que incluye
    esas puertas *y alguna más* le daría a esa gente acceso que hoy no tiene.
    """
    por_conjunto = {frozenset(p["dispositivos"]): p for p in perfiles}
    for g in grupos:
        p = por_conjunto.get(frozenset(g["puertas"]))
        g["perfil_existente"] = {"id": p["id"], "nombre": p["nombre"]} if p else None
