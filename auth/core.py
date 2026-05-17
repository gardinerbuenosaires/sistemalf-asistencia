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

SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
ALGORITHM  = "HS256"
TOKEN_TTL  = 8  # horas

# Módulos y acciones disponibles en el sistema
MODULOS = [
    "dashboard", "empleados", "horarios", "planificacion",
    "calendarios", "asistencia", "resultados", "usuarios", "roles", "sync", "premios", "vacaciones",
    "periodos",
]
ACCIONES = ["ver", "editar", "eliminar", "procesar", "corregir", "cerrar", "reabrir", "carga_inicial"]
# corregir → asistencia:corregir (novedades en planilla)

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
         "rol_id": rol_id, "rol": rol_nombre, "exp": exp},
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
    "sistema":        {m: list(ACCIONES) for m in MODULOS},
    "rrhh":           {
        "dashboard":     ["ver"],
        "empleados":     ["ver","editar"],
        "horarios":      ["ver","editar","eliminar"],
        "planificacion": ["ver","editar"],
        "calendarios":   ["ver","editar","eliminar"],
        "asistencia":    ["ver","editar","corregir"],
        "resultados":    ["ver","procesar"],
        "sync":          ["procesar"],
        "premios":       ["ver"],
        "vacaciones":    ["ver"],
        "periodos":      ["ver","cerrar","reabrir"],
    },
    "administracion": {
        "dashboard":     ["ver"],
        "empleados":     ["ver"],
        "asistencia":    ["ver"],
        "planificacion": ["ver"],
        "resultados":    ["ver"],
    },
    "gerencia": {
        "dashboard":     ["ver"],
        "asistencia":    ["ver"],
        "resultados":    ["ver"],
        "empleados":     ["ver"],
    },
    "encargado": {
        "dashboard":     ["ver"],
        "planificacion": ["ver","editar"],
        "asistencia":    ["ver"],
    },
}


def ensure_admin():
    """Crea roles, permisos y usuario sistema si no existen."""
    import logging
    logger = logging.getLogger(__name__)

    with db_session() as conn:
        # Crear roles default si no existen
        for nombre, desc, nivel in ROLES_DEFAULT:
            conn.execute(
                "INSERT OR IGNORE INTO roles (nombre, descripcion, nivel) VALUES (?,?,?)",
                (nombre, desc, nivel)
            )

        # Crear permisos default para cada rol
        for rol_nombre, modulos in PERMISOS_DEFAULT.items():
            rol = conn.execute("SELECT id FROM roles WHERE nombre=?", (rol_nombre,)).fetchone()
            if not rol:
                continue
            rid = rol["id"]
            for modulo, acciones in modulos.items():
                for accion in acciones:
                    conn.execute(
                        "INSERT OR IGNORE INTO permisos (rol_id, modulo, accion) VALUES (?,?,?)",
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
