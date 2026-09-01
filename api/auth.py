from fastapi import APIRouter, HTTPException, Response, Depends
from pydantic import BaseModel
from typing import Optional
from db.database import db_session
from auth.core import (verify_password, create_token, get_current_user,
                       hash_password, require_permiso, invalidar_cache,
                       MODULOS, ACCIONES, MODULO_ACCIONES, INACTIVITY_TTL)

router = APIRouter(tags=["auth"])


class LoginIn(BaseModel):
    email: str
    password: str


class CambiarClaveIn(BaseModel):
    clave_actual: str
    clave_nueva: str


class UsuarioIn(BaseModel):
    nombre: str
    email: str
    rol_id: int
    password: str = ""
    activo: int = 1
    pagina_inicio: str = ""
    turno_dist: str = ""
    departamento_dist: Optional[int] = None


class RolIn(BaseModel):
    nombre: str
    descripcion: str = ""
    nivel: int = 1
    pagina_inicio: str = "/"


class PermisosIn(BaseModel):
    permisos: list[dict]  # [{modulo, accion}]


# ── Auth ──────────────────────────────────────────────────────────────────────

@router.post("/api/auth/login")
def login(data: LoginIn, response: Response):
    with db_session() as conn:
        row = conn.execute(
            """SELECT u.*, r.nombre as rol_nombre,
                      COALESCE(u.pagina_inicio, r.pagina_inicio, '/') AS pagina_inicio_eff
               FROM usuarios u LEFT JOIN roles r ON r.id=u.rol_id
               WHERE u.email=? AND u.activo=1""",
            (data.email.strip(),)
        ).fetchone()
    if not row or not verify_password(data.password, row["password_hash"]):
        raise HTTPException(401, "Email o contraseña incorrectos")
    token = create_token(row["id"], row["email"], row["rol_id"], row["rol_nombre"] or "")
    response.set_cookie("session", token, httponly=True, samesite="lax", max_age=INACTIVITY_TTL)
    return {"ok": True, "rol": row["rol_nombre"], "nombre": row["nombre"],
            "pagina_inicio": row["pagina_inicio_eff"] or "/"}


@router.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie("session")
    return {"ok": True}


@router.get("/api/auth/me")
def me(user=Depends(get_current_user)):
    with db_session() as conn:
        row = conn.execute(
            """SELECT u.id, u.nombre, u.email, u.rol_id, u.turno_dist, u.departamento_dist, r.nombre as rol
               FROM usuarios u LEFT JOIN roles r ON r.id=u.rol_id
               WHERE u.id=?""",
            (user["sub"],)
        ).fetchone()
    if not row:
        raise HTTPException(404)
    from auth.core import get_permisos_rol
    perms = list(get_permisos_rol(row["rol_id"] or 0))
    return {**dict(row), "permisos": [{"modulo": m, "accion": a} for m, a in perms]}


@router.post("/api/auth/cambiar-clave")
def cambiar_clave(data: CambiarClaveIn, user=Depends(get_current_user)):
    with db_session() as conn:
        row = conn.execute("SELECT password_hash FROM usuarios WHERE id=?", (user["sub"],)).fetchone()
        if not row or not verify_password(data.clave_actual, row["password_hash"]):
            raise HTTPException(400, "Contraseña actual incorrecta")
        if len(data.clave_nueva) < 6:
            raise HTTPException(400, "Mínimo 6 caracteres")
        conn.execute("UPDATE usuarios SET password_hash=? WHERE id=?",
                     (hash_password(data.clave_nueva), user["sub"]))
    return {"ok": True}


# ── Usuarios ──────────────────────────────────────────────────────────────────

@router.get("/api/usuarios")
def list_usuarios(user=Depends(require_permiso("usuarios", "ver"))):
    with db_session() as conn:
        rows = conn.execute(
            """SELECT u.id, u.nombre, u.email, u.rol_id, u.activo, u.creado_en,
                      u.pagina_inicio, u.turno_dist, u.departamento_dist, r.nombre as rol_nombre
               FROM usuarios u LEFT JOIN roles r ON r.id=u.rol_id
               ORDER BY u.nombre"""
        ).fetchall()
    return [dict(r) for r in rows]


@router.post("/api/usuarios", status_code=201)
def create_usuario(data: UsuarioIn, user=Depends(require_permiso("usuarios", "editar"))):
    if not data.password or len(data.password) < 6:
        raise HTTPException(400, "Contraseña de al menos 6 caracteres")
    with db_session() as conn:
        if conn.execute("SELECT id FROM usuarios WHERE email=?", (data.email,)).fetchone():
            raise HTTPException(409, "Email ya registrado")
        turno_dist = data.turno_dist.strip() or None
        cur = conn.execute(
            "INSERT INTO usuarios (nombre, email, password_hash, rol_id, activo, turno_dist, departamento_dist) VALUES (?,?,?,?,?,?,?)",
            (data.nombre.strip(), data.email.strip(), hash_password(data.password),
             data.rol_id, data.activo, turno_dist, data.departamento_dist)
        )
        return {"id": cur.lastrowid, "ok": True}


@router.put("/api/usuarios/{uid}")
def update_usuario(uid: int, data: UsuarioIn, user=Depends(require_permiso("usuarios", "editar"))):
    with db_session() as conn:
        if not conn.execute("SELECT id FROM usuarios WHERE id=?", (uid,)).fetchone():
            raise HTTPException(404)
        pagina = data.pagina_inicio.strip() or None
        turno_dist = data.turno_dist.strip() or None
        if data.password:
            if len(data.password) < 6:
                raise HTTPException(400, "Mínimo 6 caracteres")
            conn.execute(
                "UPDATE usuarios SET nombre=?,email=?,rol_id=?,activo=?,password_hash=?,pagina_inicio=?,turno_dist=?,departamento_dist=? WHERE id=?",
                (data.nombre.strip(), data.email.strip(), data.rol_id,
                 data.activo, hash_password(data.password), pagina, turno_dist, data.departamento_dist, uid)
            )
        else:
            conn.execute(
                "UPDATE usuarios SET nombre=?,email=?,rol_id=?,activo=?,pagina_inicio=?,turno_dist=?,departamento_dist=? WHERE id=?",
                (data.nombre.strip(), data.email.strip(), data.rol_id, data.activo, pagina, turno_dist, data.departamento_dist, uid)
            )
    return {"ok": True}


@router.delete("/api/usuarios/{uid}")
def delete_usuario(uid: int, user=Depends(require_permiso("usuarios", "eliminar"))):
    if str(uid) == str(user["sub"]):
        raise HTTPException(400, "No podés eliminarte a vos mismo")
    with db_session() as conn:
        conn.execute("DELETE FROM usuarios WHERE id=?", (uid,))
    return {"ok": True}


# ── Roles ─────────────────────────────────────────────────────────────────────

@router.get("/api/roles")
def list_roles(user=Depends(get_current_user)):
    with db_session() as conn:
        roles = conn.execute(
            "SELECT * FROM roles WHERE activo=1 ORDER BY nombre"
        ).fetchall()
        result = []
        for r in roles:
            perms = conn.execute(
                "SELECT modulo, accion FROM permisos WHERE rol_id=? ORDER BY modulo, accion",
                (r["id"],)
            ).fetchall()
            result.append({**dict(r), "permisos": [dict(p) for p in perms]})
    return result


@router.post("/api/roles", status_code=201)
def create_rol(data: RolIn, user=Depends(require_permiso("roles", "editar"))):
    with db_session() as conn:
        if conn.execute("SELECT id FROM roles WHERE lower(nombre)=lower(?)", (data.nombre,)).fetchone():
            raise HTTPException(409, "Ya existe un rol con ese nombre")
        cur = conn.execute(
            "INSERT INTO roles (nombre, descripcion, nivel, pagina_inicio) VALUES (?,?,?,?)",
            (data.nombre.strip(), data.descripcion.strip(), data.nivel, data.pagina_inicio.strip() or "/")
        )
        return {"id": cur.lastrowid, "ok": True}


@router.put("/api/roles/{rid}")
def update_rol(rid: int, data: RolIn, user=Depends(require_permiso("roles", "editar"))):
    with db_session() as conn:
        if not conn.execute("SELECT id FROM roles WHERE id=?", (rid,)).fetchone():
            raise HTTPException(404)
        if conn.execute("SELECT id FROM roles WHERE lower(nombre)=lower(?) AND id!=?", (data.nombre, rid)).fetchone():
            raise HTTPException(409, "Ya existe un rol con ese nombre")
        conn.execute(
            "UPDATE roles SET nombre=?,descripcion=?,nivel=?,pagina_inicio=? WHERE id=?",
            (data.nombre.strip(), data.descripcion.strip(), data.nivel,
             data.pagina_inicio.strip() or "/", rid)
        )
    invalidar_cache(rid)
    return {"ok": True}


@router.put("/api/roles/{rid}/permisos")
def set_permisos(rid: int, data: PermisosIn, user=Depends(require_permiso("roles", "editar"))):
    with db_session() as conn:
        if not conn.execute("SELECT id FROM roles WHERE id=?", (rid,)).fetchone():
            raise HTTPException(404)
        conn.execute("DELETE FROM permisos WHERE rol_id=?", (rid,))
        for p in data.permisos:
            m, a = p.get("modulo"), p.get("accion")
            if a in MODULO_ACCIONES.get(m, ()):
                conn.execute(
                    "INSERT OR IGNORE INTO permisos (rol_id,modulo,accion) VALUES (?,?,?)",
                    (rid, m, a)
                )
    invalidar_cache(rid)
    return {"ok": True}


@router.delete("/api/roles/{rid}")
def delete_rol(rid: int, user=Depends(require_permiso("roles", "eliminar"))):
    with db_session() as conn:
        en_uso = conn.execute("SELECT COUNT(*) FROM usuarios WHERE rol_id=?", (rid,)).fetchone()[0]
        if en_uso:
            raise HTTPException(409, f"El rol está asignado a {en_uso} usuario(s)")
        conn.execute("DELETE FROM roles WHERE id=?", (rid,))
    invalidar_cache(rid)
    return {"ok": True}


@router.get("/api/roles/modulos")
def get_modulos(user=Depends(get_current_user)):
    return {"modulos": MODULOS, "acciones": ACCIONES,
            "modulo_acciones": MODULO_ACCIONES}
