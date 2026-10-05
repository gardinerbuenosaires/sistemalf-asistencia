"""
Autenticación JWT + permisos dinámicos por módulo/acción desde la DB.

Módulos:  dashboard, empleados, horarios, planificacion, calendarios,
          asistencia, resultados, usuarios, roles, sync
Acciones: ver, editar, eliminar, procesar
"""
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt as _bcrypt
from fastapi import Cookie, Depends, HTTPException, status
from jose import JWTError, jwt

from db.database import db_session

SECRET_KEY      = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
ALGORITHM       = "HS256"
TOKEN_TTL       = 8    # horas — expiración absoluta
INACTIVITY_TTL  = 600  # segundos — 10 minutos sin actividad desloguea

# Módulos y acciones disponibles en el sistema
MODULOS = [
    "dashboard", "empleados", "horarios", "planificacion",
    "calendarios", "asistencia", "resultados", "usuarios", "roles", "sync", "premios", "vacaciones",
    "periodos", "distribucion", "mozos", "barmans", "peones", "uniformes",
    "actualizacion", "dispositivos", "accesos",
]
ACCIONES = ["ver", "editar", "eliminar", "procesar", "corregir", "cerrar", "reabrir", "carga_inicial", "ver_todos", "confirmar", "jubilacion", "fichaje_manual", "propia", "fichaje_propio", "asignar", "correccion_propia", "excepcion", "aplicar"]
# corregir       → asistencia:corregir (novedades en planilla)
# fichaje_manual → asistencia:fichaje_manual (crear y borrar fichadas a mano,
#                  individuales o por fuerza mayor). Separado de "editar" porque
#                  inventa una marca que el reloj nunca registró.
# propia         → planificacion:propia (tocar la planificación o el calendario
#                  del empleado vinculado al propio usuario). Sin él, "editar"
#                  alcanza para todos menos para uno mismo: cambiarse el horario
#                  del día después de llegar borra la llegada tarde.
#                  vacaciones:propia → vacaciones pagadas y saldo inicial propios.
#                  empleados:propia → en el propio legajo, lo que mueve asistencia,
#                  vacaciones o premios: ingreso, tipo (un jerárquico sin fichar
#                  queda presente), cargo, categoría, activo y jubilación.
# fichaje_propio → asistencia:fichaje_propio (crear y borrar fichadas manuales
#                  del empleado vinculado al propio usuario). Igual que "propia":
#                  una entrada manual anterior a la marca real tapa la tardanza.
# correccion_propia → asistencia:correccion_propia (cargar, cambiar o borrar
#                  novedades del propio legajo, salvo la observación, y editar
#                  su saldo de francos). Un CP o un NF tapan una tardanza o una
#                  falta igual que cambiarse el horario.
# asignar        → vacaciones:asignar (poner o sacar una V, desde la planilla o
#                  desde las grillas de distribución). Separado de
#                  asistencia:corregir y de editar las grillas, que cubren el
#                  resto de las novedades y los horarios.

# Acciones que cada módulo realmente usa. Es la fuente única: la matriz de roles
# se dibuja con esto y set_permisos rechaza lo que no figure acá. Al agregar un
# require_permiso() con una acción nueva, sumarla al módulo correspondiente.
MODULO_ACCIONES = {
    "dashboard":     ["ver"],
    "empleados":     ["ver", "editar", "jubilacion", "propia"],
    "horarios":      ["ver", "editar", "eliminar"],
    "planificacion": ["ver", "editar", "propia"],
    "calendarios":   ["ver", "editar", "eliminar"],
    "asistencia":    ["ver", "editar", "corregir", "carga_inicial", "ver_todos", "fichaje_manual", "fichaje_propio",
                     "correccion_propia"],
    "resultados":    ["ver", "procesar"],
    "usuarios":      ["ver", "editar", "eliminar"],
    "roles":         ["ver", "editar", "eliminar"],
    "sync":          ["procesar"],
    "premios":       ["ver", "editar", "corregir", "cerrar", "reabrir"],
    "vacaciones":    ["ver", "editar", "carga_inicial", "asignar", "propia"],
    "periodos":      ["ver", "cerrar", "reabrir"],
    "distribucion":  ["ver", "editar", "confirmar"],
    "mozos":         ["ver", "editar", "confirmar"],
    "barmans":       ["ver", "editar", "confirmar"],
    "peones":        ["ver", "editar", "confirmar"],
    # uniformes:carga_inicial → cargar constancias anteriores al sistema desde el
    #   papel. Separado de "editar" para poder dárselo a RRHH mientras dure la
    #   digitalización y sacárselo después.
    # uniformes:eliminar      → anular una constancia emitida, con motivo obligatorio.
    "uniformes":      ["ver", "editar", "carga_inicial", "eliminar"],
    # actualizacion:procesar → bajar la versión nueva de GitHub y reiniciar el
    #   sistema desde Configuración. De entrada, solo Sistema.
    "actualizacion":  ["procesar"],
    # dispositivos → los lectores biométricos: el maestro de asistencia y los de
    #   puerta. Es configuración técnica, por eso arranca solo en Sistema: tocar
    #   una IP mal deja al restaurante sin fichaje.
    "dispositivos":   ["ver", "editar", "eliminar"],
    # accesos → la politica de quien abre que puerta, separada de los equipos.
    #   editar    → redefinir que puertas incluye un perfil. Cambia el acceso de
    #               todos los que lo tengan.
    #   asignar   → ponerle un perfil a una persona. Es aplicar la politica, se
    #               hace al dar de alta a alguien, y puede vivir en RRHH.
    #   excepcion → sacarle o darle una puerta suelta a alguien. Es desviarse de
    #               la politica, y es lo mas dificil de auditar: un perfil se ve
    #               en la matriz, una excepcion solo abriendo esa ficha. Por eso
    #               va aparte de asignar.
    #   aplicar   → escribirle a los equipos: llevar la politica a los lectores
    #               y sacar a mano lo que quedo por un error. Va aparte de las
    #               demas porque las otras deciden y esta ejecuta: una politica
    #               equivocada se corrige antes de aplicarla, un borrado en un
    #               lector deja a alguien afuera a las siete de la maniana.
    "accesos":        ["ver", "editar", "eliminar", "asignar", "excepcion", "aplicar"],
}

# Cómo se agrupan los módulos en la pantalla de Roles. Un módulo que no figure
# acá cae en "Otros", así que agregarlo a un grupo es opcional pero conviene.
MODULO_GRUPOS = [
    ("Asistencia",   ["asistencia", "resultados", "periodos", "sync"]),
    ("Programación", ["horarios", "planificacion", "calendarios"]),
    ("Distribución", ["distribucion", "mozos", "barmans", "peones"]),
    ("Personal",     ["empleados", "vacaciones", "premios", "uniformes"]),
    ("Sistema",      ["dashboard", "usuarios", "roles", "actualizacion",
                      "dispositivos", "accesos"]),
]

# Cache simple de permisos: {rol_id: (timestamp, set{(modulo,accion)})}
_cache: dict[int, tuple[float, set]] = {}
_CACHE_TTL = 30  # segundos


# ── Passwords ─────────────────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    return _bcrypt.hashpw(plain.encode(), _bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


# ── JWT ───────────────────────────────────────────────────────────────────────

def create_token(user_id: int, email: str, rol_id: int, rol_nombre: str) -> str:
    exp = datetime.now(timezone.utc) + timedelta(hours=TOKEN_TTL)
    return jwt.encode(
        {"sub": str(user_id), "email": email,
         "rol_id": rol_id, "rol": rol_nombre, "exp": exp, "la": time.time()},
        SECRET_KEY, algorithm=ALGORITHM
    )


def refresh_token(payload: dict) -> str:
    """Renueva last_active manteniendo la expiración absoluta original."""
    return jwt.encode(
        {**payload, "la": time.time()},
        SECRET_KEY, algorithm=ALGORITHM
    )


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return {}


# ── Permisos ──────────────────────────────────────────────────────────────────

def invalidar_cache(rol_id: int | None = None):
    if rol_id:
        _cache.pop(rol_id, None)
    else:
        _cache.clear()


def get_permisos_rol(rol_id: int) -> set:
    """Devuelve set de (modulo, accion) para el rol. Usa cache con TTL."""
    cached = _cache.get(rol_id)
    if cached and time.time() - cached[0] < _CACHE_TTL:
        return cached[1]
    with db_session() as conn:
        rows = conn.execute(
            "SELECT modulo, accion FROM permisos WHERE rol_id=?", (rol_id,)
        ).fetchall()
    perms = {(r["modulo"], r["accion"]) for r in rows}
    _cache[rol_id] = (time.time(), perms)
    return perms


def tiene_permiso(rol_id: int, modulo: str, accion: str) -> bool:
    return (modulo, accion) in get_permisos_rol(rol_id)


# ── Dependencias FastAPI ──────────────────────────────────────────────────────

def get_current_user(session: Optional[str] = Cookie(default=None)):
    if not session:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "No autenticado")
    payload = decode_token(session)
    if not payload:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sesión inválida o expirada")
    return payload


def require_permiso(modulo: str, accion: str):
    """Dependencia: exige permiso específico. Uso: Depends(require_permiso('empleados','editar'))"""
    def _check(user=Depends(get_current_user)):
        rol_id = user.get("rol_id")
        if not rol_id or not tiene_permiso(rol_id, modulo, accion):
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                f"Sin permiso: {modulo}.{accion}")
        return user
    return _check


# Acciones que habilitan a un usuario a tocarse a sí mismo, y qué se le dice si no
# la tiene. Cambiarse el propio vínculo con un empleado exige todas. Los mismos
# textos están en web/static/js/legajo-propio.js, que avisa antes de intentar.
PERMISOS_PROPIOS = {
    ("planificacion", "propia"):
        "No podés modificar tu propia planificación. Si necesitás un cambio, pedíselo a otro encargado.",
    ("asistencia",    "fichaje_propio"):
        "No podés cargar ni borrar tus propias fichadas. Si necesitás una corrección, pedísela a otro encargado.",
    ("asistencia",    "correccion_propia"):
        "No podés cargar, cambiar ni borrar novedades en tu propio legajo. Si necesitás una, pedísela a otro encargado.",
    ("vacaciones",    "propia"):
        "No podés modificar tus propias vacaciones pagadas ni tu saldo inicial de vacaciones. Si necesitás un cambio, pedíselo a otro encargado.",
    ("empleados",     "propia"):
        "No podés cambiar en tu propio legajo el ingreso, el tipo, el cargo, la categoría, el estado ni la jubilación. Si hace falta, pedíselo a otro encargado.",
}


def _check_no_es_propio(conn, user: dict, empleado_ids, modulo: str, accion: str) -> None:
    """Rechaza actuar sobre el empleado vinculado al usuario si el rol no tiene
    modulo:accion. Un usuario sin empleado vinculado no se frena."""
    if tiene_permiso(user.get("rol_id") or 0, modulo, accion):
        return
    row = conn.execute(
        "SELECT empleado_id FROM usuarios WHERE id=?", (user.get("sub"),)
    ).fetchone()
    propio = row["empleado_id"] if row else None
    if propio and propio in {int(e) for e in empleado_ids if e is not None}:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            PERMISOS_PROPIOS[(modulo, accion)])


def check_no_es_propia(conn, user: dict, empleado_ids) -> None:
    _check_no_es_propio(conn, user, empleado_ids, "planificacion", "propia")


def check_no_es_fichaje_propio(conn, user: dict, empleado_ids) -> None:
    _check_no_es_propio(conn, user, empleado_ids, "asistencia", "fichaje_propio")


def check_no_es_correccion_propia(conn, user: dict, empleado_ids) -> None:
    _check_no_es_propio(conn, user, empleado_ids, "asistencia", "correccion_propia")


def check_no_es_vacacion_propia(conn, user: dict, empleado_ids) -> None:
    _check_no_es_propio(conn, user, empleado_ids, "vacaciones", "propia")


def check_no_es_legajo_propio(conn, user: dict, empleado_ids) -> None:
    _check_no_es_propio(conn, user, empleado_ids, "empleados", "propia")


def check_asignar_vacaciones(user: dict) -> None:
    """Para los caminos que ponen o sacan una V dentro de un endpoint que ya
    exige otro permiso (novedades de la planilla, celdas de las grillas)."""
    if not tiene_permiso(user.get("rol_id") or 0, "vacaciones", "asignar"):
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "No tenés permiso para asignar ni quitar vacaciones.")


def check_page_auth(token: str | None, modulo: str, accion: str = "ver") -> bool:
    """Para rutas de página (no API): verifica cookie sin lanzar excepción."""
    if not token:
        return False
    payload = decode_token(token)
    if not payload:
        return False
    rol_id = payload.get("rol_id")
    return bool(rol_id and tiene_permiso(rol_id, modulo, accion))


# ── Bootstrap ─────────────────────────────────────────────────────────────────

ROLES_DEFAULT = [
    ("sistema",        "Acceso total al sistema", 100),
    ("rrhh",           "Gestión de personal, horarios y asistencia", 80),
    ("administracion", "Consulta de empleados y asistencia", 60),
    ("gerencia",       "Reportes y dashboard. Solo lectura", 40),
    ("encargado",      "Presencia y planificación operativa", 20),
]

PERMISOS_DEFAULT = {
    "sistema":        {m: list(MODULO_ACCIONES[m]) for m in MODULOS},
    "rrhh":           {
        "dashboard":     ["ver"],
        "empleados":     ["ver","editar"],
        "horarios":      ["ver","editar","eliminar"],
        "planificacion": ["ver","editar"],
        "calendarios":   ["ver","editar","eliminar"],
        "asistencia":    ["ver","editar","corregir","fichaje_manual"],
        "resultados":    ["ver","procesar"],
        "sync":          ["procesar"],
        "premios":       ["ver"],
        "vacaciones":    ["ver","asignar"],
        "periodos":      ["ver","cerrar","reabrir"],
        "distribucion":  ["ver","editar","confirmar"],
        "mozos":         ["ver","editar","confirmar"],
        "barmans":       ["ver","editar","confirmar"],
        "peones":        ["ver","editar","confirmar"],
        # La anulación queda en RRHH y no solo en sistema: el que se equivoca
        # emitiendo es RRHH y a los diez minutos quiere corregirlo. Lo que cubre
        # el riesgo es la trazabilidad (motivo obligatorio + quién + cuándo).
        "uniformes":      ["ver","editar","carga_inicial","eliminar"],
    },
    "administracion": {
        "dashboard":     ["ver"],
        "empleados":     ["ver"],
        "asistencia":    ["ver"],
        "planificacion": ["ver"],
        "resultados":    ["ver"],
        "uniformes":      ["ver"],
    },
    "gerencia": {
        "dashboard":     ["ver"],
        "asistencia":    ["ver"],
        "resultados":    ["ver"],
        "empleados":     ["ver"],
        "uniformes":      ["ver"],
    },
    "encargado": {
        "dashboard":     ["ver"],
        "planificacion": ["ver","editar"],
        "asistencia":    ["ver"],
        "vacaciones":    ["asignar"],
        "distribucion":  ["ver","editar","confirmar"],
        "mozos":         ["ver","editar","confirmar"],
        "barmans":       ["ver","editar","confirmar"],
        "peones":        ["ver","editar","confirmar"],
    },
}


def ensure_admin():
    """Crea roles, permisos y usuario sistema si no existen."""
    import logging
    logger = logging.getLogger(__name__)

    with db_session() as conn:
        # Crear roles default si no existen (comparación case-insensitive)
        for nombre, desc, nivel in ROLES_DEFAULT:
            if not conn.execute("SELECT id FROM roles WHERE lower(nombre)=lower(?)", (nombre,)).fetchone():
                conn.execute(
                    "INSERT INTO roles (nombre, descripcion, nivel) VALUES (?,?,?)",
                    (nombre, desc, nivel)
                )

        # Permisos default: cada uno se aplica UNA sola vez por rol.
        #
        # Reinsertarlos en cada arranque no distingue un permiso que nunca
        # existió de uno que alguien sacó a propósito desde Roles: la decisión
        # se deshacía sola en el reinicio siguiente. Con el registro de lo ya
        # aplicado, lo que se saca queda sacado, y un módulo o acción nueva
        # —que nunca se aplicó— sigue llegando sola. La primera vez el registro
        # está vacío, así que se comporta como antes y no cambia nada.
        #
        # Sistema es la excepción: recibe todo en cada arranque, para que nunca
        # quede el sistema sin un rol que lo administre.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS permisos_default_aplicados (
                rol_id      INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
                modulo      TEXT NOT NULL,
                accion      TEXT NOT NULL,
                aplicado_en TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                PRIMARY KEY (rol_id, modulo, accion)
            )
        """)
        for rol_nombre, modulos in PERMISOS_DEFAULT.items():
            rol = conn.execute("SELECT id FROM roles WHERE lower(nombre)=lower(?)", (rol_nombre,)).fetchone()
            if not rol:
                continue
            rid = rol["id"]
            siempre = rol_nombre.lower() == "sistema"
            for modulo, acciones in modulos.items():
                for accion in acciones:
                    ya_aplicado = conn.execute(
                        "SELECT 1 FROM permisos_default_aplicados WHERE rol_id=? AND modulo=? AND accion=?",
                        (rid, modulo, accion)
                    ).fetchone()
                    if ya_aplicado and not siempre:
                        continue
                    conn.execute(
                        "INSERT OR IGNORE INTO permisos (rol_id, modulo, accion) VALUES (?,?,?)",
                        (rid, modulo, accion)
                    )
                    conn.execute(
                        "INSERT OR IGNORE INTO permisos_default_aplicados (rol_id, modulo, accion) VALUES (?,?,?)",
                        (rid, modulo, accion)
                    )

        # Migrar usuarios que tengan columna rol (texto) a rol_id
        try:
            conn.execute("SELECT rol FROM usuarios LIMIT 1")
            # Columna vieja existe — migrar
            rows = conn.execute("SELECT id, rol FROM usuarios WHERE rol_id IS NULL").fetchall()
            for row in rows:
                rol = conn.execute("SELECT id FROM roles WHERE nombre=?", (row["rol"],)).fetchone()
                if rol:
                    conn.execute("UPDATE usuarios SET rol_id=? WHERE id=?", (rol["id"], row["id"]))
        except Exception:
            pass  # columna 'rol' ya no existe

        # Crear usuario admin si no hay ninguno
        count = conn.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0]
        if count == 0:
            rol_sistema = conn.execute("SELECT id FROM roles WHERE nombre='sistema'").fetchone()
            if rol_sistema:
                pwd = "admin1234"
                conn.execute(
                    "INSERT INTO usuarios (nombre, email, password_hash, rol_id) VALUES (?,?,?,?)",
                    ("Administrador", "admin@sistema.local", hash_password(pwd), rol_sistema["id"])
                )
                logger.warning(
                    "Usuario inicial creado — email: admin@sistema.local | clave: %s  "
                    "¡Cambiala después del primer login!", pwd
                )
