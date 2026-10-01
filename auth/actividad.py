"""Quién está usando el sistema ahora, para elegir cuándo actualizar.

Las sesiones no se guardan en ningún lado (van en la cookie), así que la única
forma de saberlo es mirar los pedidos que llegan. SlidingSessionMiddleware
anota cada uno acá; se guarda en memoria porque solo importa el presente y un
reinicio cierra todas las sesiones de todos modos.

Dos horas por usuario:
- contacto: el último pedido de cualquier tipo, incluido el ping de cada minuto
  de session-guard.js → tiene una página del sistema abierta.
- actividad: la última acción real. Es el "la" del token, que los pedidos con
  X-Poll no renuevan → está haciendo algo.
"""
import threading
import time

_lock = threading.Lock()
_usuarios: dict[int, dict] = {}

# session-guard.js pinguea cada 60 s: sin contacto en 2,5 min, la página se cerró.
CONTACTO_VIGENTE = 150


def registrar(payload: dict) -> None:
    try:
        uid = int(payload.get("sub") or 0)
    except (TypeError, ValueError):
        return
    if not uid:
        return
    with _lock:
        _usuarios[uid] = {"contacto": time.time(), "actividad": payload.get("la") or 0}


def conectados(inactividad_segundos: int) -> list[dict]:
    """Usuarios con una página abierta y sesión todavía vigente, el más activo primero."""
    ahora = time.time()
    with _lock:
        copia = dict(_usuarios)
    res = []
    for uid, u in copia.items():
        if ahora - u["contacto"] > CONTACTO_VIGENTE:
            continue
        inactivo = ahora - u["actividad"]
        if inactivo > inactividad_segundos:
            continue        # la sesión ya venció: el próximo pedido lo desloguea
        res.append({"usuario_id": uid, "inactivo_seg": int(inactivo)})
    return sorted(res, key=lambda r: r["inactivo_seg"])
