import sqlite3
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from db.database import db_session
from auth.core import require_permiso

router = APIRouter(tags=["catalogos"])


class ItemIn(BaseModel):
    nombre: str


class CargoUpdate(BaseModel):
    aplica_premio: bool


# ── Cargos ────────────────────────────────────────────────────────────────────

@router.get("/api/cargos")
def list_cargos():
    with db_session() as conn:
        rows = conn.execute("SELECT id, nombre, aplica_premio FROM cargos ORDER BY nombre").fetchall()
    return [dict(r) for r in rows]


@router.post("/api/cargos", status_code=201)
def create_cargo(data: ItemIn, _user=Depends(require_permiso("empleados", "editar"))):
    nombre = data.nombre.strip()
    if not nombre:
        raise HTTPException(400, "Nombre requerido")
    with db_session() as conn:
        try:
            conn.execute("INSERT INTO cargos (nombre) VALUES (?)", (nombre,))
            row = conn.execute("SELECT id, nombre, aplica_premio FROM cargos WHERE nombre=?", (nombre,)).fetchone()
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Ya existe un cargo con ese nombre")
    return dict(row)


@router.patch("/api/cargos/{cid}", status_code=200)
def update_cargo(cid: int, data: CargoUpdate, _user=Depends(require_permiso("empleados", "editar"))):
    with db_session() as conn:
        if not conn.execute("SELECT id FROM cargos WHERE id=?", (cid,)).fetchone():
            raise HTTPException(404, "Cargo no encontrado")
        conn.execute("UPDATE cargos SET aplica_premio=? WHERE id=?", (int(data.aplica_premio), cid))
        row = conn.execute("SELECT id, nombre, aplica_premio FROM cargos WHERE id=?", (cid,)).fetchone()
    return dict(row)


@router.delete("/api/cargos/{cid}", status_code=200)
def delete_cargo(cid: int, _user=Depends(require_permiso("empleados", "editar"))):
    with db_session() as conn:
        if not conn.execute("SELECT id FROM cargos WHERE id=?", (cid,)).fetchone():
            raise HTTPException(404, "Cargo no encontrado")
        conn.execute("DELETE FROM cargos WHERE id=?", (cid,))
    return {"ok": True}


# ── Departamentos ─────────────────────────────────────────────────────────────

@router.get("/api/departamentos")
def list_departamentos():
    with db_session() as conn:
        rows = conn.execute("SELECT id, nombre FROM departamentos ORDER BY nombre").fetchall()
    return [dict(r) for r in rows]


@router.post("/api/departamentos", status_code=201)
def create_departamento(data: ItemIn, _user=Depends(require_permiso("empleados", "editar"))):
    nombre = data.nombre.strip()
    if not nombre:
        raise HTTPException(400, "Nombre requerido")
    with db_session() as conn:
        try:
            conn.execute("INSERT INTO departamentos (nombre) VALUES (?)", (nombre,))
            row = conn.execute("SELECT id, nombre FROM departamentos WHERE nombre=?", (nombre,)).fetchone()
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Ya existe un departamento con ese nombre")
    return dict(row)


@router.delete("/api/departamentos/{did}", status_code=200)
def delete_departamento(did: int, _user=Depends(require_permiso("empleados", "editar"))):
    with db_session() as conn:
        if not conn.execute("SELECT id FROM departamentos WHERE id=?", (did,)).fetchone():
            raise HTTPException(404, "Departamento no encontrado")
        conn.execute("DELETE FROM departamentos WHERE id=?", (did,))
    return {"ok": True}


# ── Categorías ────────────────────────────────────────────────────────────────

@router.get("/api/categorias")
def list_categorias():
    with db_session() as conn:
        rows = conn.execute("SELECT id, nombre FROM categorias ORDER BY nombre").fetchall()
    return [dict(r) for r in rows]


@router.post("/api/categorias", status_code=201)
def create_categoria(data: ItemIn, _user=Depends(require_permiso("empleados", "editar"))):
    nombre = data.nombre.strip()
    if not nombre:
        raise HTTPException(400, "Nombre requerido")
    with db_session() as conn:
        try:
            conn.execute("INSERT INTO categorias (nombre) VALUES (?)", (nombre,))
            row = conn.execute("SELECT id, nombre FROM categorias WHERE nombre=?", (nombre,)).fetchone()
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Ya existe una categoría con ese nombre")
    return dict(row)


@router.delete("/api/categorias/{cid}", status_code=200)
def delete_categoria(cid: int, _user=Depends(require_permiso("empleados", "editar"))):
    with db_session() as conn:
        if not conn.execute("SELECT id FROM categorias WHERE id=?", (cid,)).fetchone():
            raise HTTPException(404, "Categoría no encontrada")
        conn.execute("DELETE FROM categorias WHERE id=?", (cid,))
    return {"ok": True}
