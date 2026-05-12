import sqlite3
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from db.database import db_session
from auth.core import require_permiso

router = APIRouter(tags=["catalogos"])


class ItemIn(BaseModel):
    nombre: str


class CargoUpdate(BaseModel):
    aplica_premio: bool | None = None
    nombre: str | None = None


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
        if data.nombre is not None:
            nombre = data.nombre.strip()
            if not nombre:
                raise HTTPException(400, "Nombre requerido")
            try:
                conn.execute("UPDATE cargos SET nombre=? WHERE id=?", (nombre, cid))
            except sqlite3.IntegrityError:
                raise HTTPException(409, "Ya existe un cargo con ese nombre")
        if data.aplica_premio is not None:
            conn.execute("UPDATE cargos SET aplica_premio=? WHERE id=?", (int(data.aplica_premio), cid))
        row = conn.execute("SELECT id, nombre, aplica_premio FROM cargos WHERE id=?", (cid,)).fetchone()
    return dict(row)


@router.delete("/api/cargos/{cid}", status_code=200)
def delete_cargo(cid: int, _user=Depends(require_permiso("empleados", "editar"))):
    with db_session() as conn:
        if not conn.execute("SELECT id FROM cargos WHERE id=?", (cid,)).fetchone():
            raise HTTPException(404, "Cargo no encontrado")
        en_uso = conn.execute("SELECT COUNT(*) FROM empleados WHERE cargo_id=?", (cid,)).fetchone()[0]
        if en_uso:
            raise HTTPException(409, f"No se puede eliminar: {en_uso} empleado{'s' if en_uso > 1 else ''} tiene{'n' if en_uso > 1 else ''} este cargo asignado")
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


@router.patch("/api/departamentos/{did}", status_code=200)
def update_departamento(did: int, data: ItemIn, _user=Depends(require_permiso("empleados", "editar"))):
    nombre = data.nombre.strip()
    if not nombre:
        raise HTTPException(400, "Nombre requerido")
    with db_session() as conn:
        if not conn.execute("SELECT id FROM departamentos WHERE id=?", (did,)).fetchone():
            raise HTTPException(404, "Departamento no encontrado")
        try:
            conn.execute("UPDATE departamentos SET nombre=? WHERE id=?", (nombre, did))
            row = conn.execute("SELECT id, nombre FROM departamentos WHERE id=?", (did,)).fetchone()
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Ya existe un departamento con ese nombre")
    return dict(row)


@router.delete("/api/departamentos/{did}", status_code=200)
def delete_departamento(did: int, _user=Depends(require_permiso("empleados", "editar"))):
    with db_session() as conn:
        if not conn.execute("SELECT id FROM departamentos WHERE id=?", (did,)).fetchone():
            raise HTTPException(404, "Departamento no encontrado")
        en_uso = conn.execute("SELECT COUNT(*) FROM empleados WHERE departamento_id=?", (did,)).fetchone()[0]
        if en_uso:
            raise HTTPException(409, f"No se puede eliminar: {en_uso} empleado{'s' if en_uso > 1 else ''} tiene{'n' if en_uso > 1 else ''} este departamento asignado")
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


@router.patch("/api/categorias/{cid}", status_code=200)
def update_categoria(cid: int, data: ItemIn, _user=Depends(require_permiso("empleados", "editar"))):
    nombre = data.nombre.strip()
    if not nombre:
        raise HTTPException(400, "Nombre requerido")
    with db_session() as conn:
        if not conn.execute("SELECT id FROM categorias WHERE id=?", (cid,)).fetchone():
            raise HTTPException(404, "Categoría no encontrada")
        try:
            conn.execute("UPDATE categorias SET nombre=? WHERE id=?", (nombre, cid))
            row = conn.execute("SELECT id, nombre FROM categorias WHERE id=?", (cid,)).fetchone()
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Ya existe una categoría con ese nombre")
    return dict(row)


@router.delete("/api/categorias/{cid}", status_code=200)
def delete_categoria(cid: int, _user=Depends(require_permiso("empleados", "editar"))):
    with db_session() as conn:
        if not conn.execute("SELECT id FROM categorias WHERE id=?", (cid,)).fetchone():
            raise HTTPException(404, "Categoría no encontrada")
        en_uso = conn.execute("SELECT COUNT(*) FROM empleados WHERE categoria_id=?", (cid,)).fetchone()[0]
        if en_uso:
            raise HTTPException(409, f"No se puede eliminar: {en_uso} empleado{'s' if en_uso > 1 else ''} tiene{'n' if en_uso > 1 else ''} esta categoría asignada")
        conn.execute("DELETE FROM categorias WHERE id=?", (cid,))
    return {"ok": True}


# ── Turnos del legajo ──────────────────────────────────────────────────────────

@router.get("/api/turnos-legajo")
def list_turnos_legajo():
    with db_session() as conn:
        rows = conn.execute("SELECT id, nombre, orden FROM turnos_legajo ORDER BY orden, nombre").fetchall()
    return [dict(r) for r in rows]


@router.post("/api/turnos-legajo", status_code=201)
def create_turno_legajo(data: ItemIn, _user=Depends(require_permiso("empleados", "editar"))):
    nombre = data.nombre.strip()
    if not nombre:
        raise HTTPException(400, "Nombre requerido")
    with db_session() as conn:
        try:
            orden = (conn.execute("SELECT COALESCE(MAX(orden),0)+1 FROM turnos_legajo").fetchone()[0])
            conn.execute("INSERT INTO turnos_legajo (nombre, orden) VALUES (?, ?)", (nombre, orden))
            row = conn.execute("SELECT id, nombre, orden FROM turnos_legajo WHERE nombre=?", (nombre,)).fetchone()
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Ya existe ese turno")
    return dict(row)


@router.patch("/api/turnos-legajo/{tid}", status_code=200)
def update_turno_legajo(tid: int, data: ItemIn, _user=Depends(require_permiso("empleados", "editar"))):
    nombre = data.nombre.strip()
    if not nombre:
        raise HTTPException(400, "Nombre requerido")
    with db_session() as conn:
        if not conn.execute("SELECT id FROM turnos_legajo WHERE id=?", (tid,)).fetchone():
            raise HTTPException(404, "Turno no encontrado")
        try:
            conn.execute("UPDATE turnos_legajo SET nombre=? WHERE id=?", (nombre, tid))
            row = conn.execute("SELECT id, nombre, orden FROM turnos_legajo WHERE id=?", (tid,)).fetchone()
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Ya existe un turno con ese nombre")
    return dict(row)


@router.delete("/api/turnos-legajo/{tid}", status_code=200)
def delete_turno_legajo(tid: int, _user=Depends(require_permiso("empleados", "editar"))):
    with db_session() as conn:
        if not conn.execute("SELECT id FROM turnos_legajo WHERE id=?", (tid,)).fetchone():
            raise HTTPException(404, "Turno no encontrado")
        en_uso = conn.execute("SELECT COUNT(*) FROM empleados WHERE turno_id=?", (tid,)).fetchone()[0]
        if en_uso:
            raise HTTPException(409, f"No se puede eliminar: {en_uso} empleado{'s' if en_uso > 1 else ''} tiene{'n' if en_uso > 1 else ''} este turno asignado")
        conn.execute("DELETE FROM turnos_legajo WHERE id=?", (tid,))
    return {"ok": True}


# ── Sectores del legajo ────────────────────────────────────────────────────────

@router.get("/api/sectores-legajo")
def list_sectores_legajo():
    with db_session() as conn:
        rows = conn.execute("SELECT id, nombre, orden FROM sectores_legajo ORDER BY orden, nombre").fetchall()
    return [dict(r) for r in rows]


@router.post("/api/sectores-legajo", status_code=201)
def create_sector_legajo(data: ItemIn, _user=Depends(require_permiso("empleados", "editar"))):
    nombre = data.nombre.strip()
    if not nombre:
        raise HTTPException(400, "Nombre requerido")
    with db_session() as conn:
        try:
            orden = (conn.execute("SELECT COALESCE(MAX(orden),0)+1 FROM sectores_legajo").fetchone()[0])
            conn.execute("INSERT INTO sectores_legajo (nombre, orden) VALUES (?, ?)", (nombre, orden))
            row = conn.execute("SELECT id, nombre, orden FROM sectores_legajo WHERE nombre=?", (nombre,)).fetchone()
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Ya existe ese sector")
    return dict(row)


@router.patch("/api/sectores-legajo/{sid}", status_code=200)
def update_sector_legajo(sid: int, data: ItemIn, _user=Depends(require_permiso("empleados", "editar"))):
    nombre = data.nombre.strip()
    if not nombre:
        raise HTTPException(400, "Nombre requerido")
    with db_session() as conn:
        if not conn.execute("SELECT id FROM sectores_legajo WHERE id=?", (sid,)).fetchone():
            raise HTTPException(404, "Sector no encontrado")
        try:
            conn.execute("UPDATE sectores_legajo SET nombre=? WHERE id=?", (nombre, sid))
            row = conn.execute("SELECT id, nombre, orden FROM sectores_legajo WHERE id=?", (sid,)).fetchone()
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Ya existe un sector con ese nombre")
    return dict(row)


@router.delete("/api/sectores-legajo/{sid}", status_code=200)
def delete_sector_legajo(sid: int, _user=Depends(require_permiso("empleados", "editar"))):
    with db_session() as conn:
        if not conn.execute("SELECT id FROM sectores_legajo WHERE id=?", (sid,)).fetchone():
            raise HTTPException(404, "Sector no encontrado")
        en_uso = conn.execute("SELECT COUNT(*) FROM empleados WHERE sector_id=?", (sid,)).fetchone()[0]
        if en_uso:
            raise HTTPException(409, f"No se puede eliminar: {en_uso} empleado{'s' if en_uso > 1 else ''} tiene{'n' if en_uso > 1 else ''} este sector asignado")
        conn.execute("DELETE FROM sectores_legajo WHERE id=?", (sid,))
    return {"ok": True}


# ── Niveles de estudio ─────────────────────────────────────────────────────────

@router.get("/api/niveles-estudio")
def list_niveles_estudio():
    with db_session() as conn:
        rows = conn.execute("SELECT id, nombre, orden FROM niveles_estudio ORDER BY orden, nombre").fetchall()
    return [dict(r) for r in rows]


@router.post("/api/niveles-estudio", status_code=201)
def create_nivel_estudio(data: ItemIn, _user=Depends(require_permiso("empleados", "editar"))):
    nombre = data.nombre.strip()
    if not nombre:
        raise HTTPException(400, "Nombre requerido")
    with db_session() as conn:
        try:
            orden = (conn.execute("SELECT COALESCE(MAX(orden),0)+1 FROM niveles_estudio").fetchone()[0])
            conn.execute("INSERT INTO niveles_estudio (nombre, orden) VALUES (?, ?)", (nombre, orden))
            row = conn.execute("SELECT id, nombre, orden FROM niveles_estudio WHERE nombre=?", (nombre,)).fetchone()
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Ya existe ese nivel")
    return dict(row)


@router.patch("/api/niveles-estudio/{nid}", status_code=200)
def update_nivel_estudio(nid: int, data: ItemIn, _user=Depends(require_permiso("empleados", "editar"))):
    nombre = data.nombre.strip()
    if not nombre:
        raise HTTPException(400, "Nombre requerido")
    with db_session() as conn:
        if not conn.execute("SELECT id FROM niveles_estudio WHERE id=?", (nid,)).fetchone():
            raise HTTPException(404, "Nivel no encontrado")
        try:
            conn.execute("UPDATE niveles_estudio SET nombre=? WHERE id=?", (nombre, nid))
            row = conn.execute("SELECT id, nombre, orden FROM niveles_estudio WHERE id=?", (nid,)).fetchone()
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Ya existe un nivel con ese nombre")
    return dict(row)


@router.delete("/api/niveles-estudio/{nid}", status_code=200)
def delete_nivel_estudio(nid: int, _user=Depends(require_permiso("empleados", "editar"))):
    with db_session() as conn:
        if not conn.execute("SELECT id FROM niveles_estudio WHERE id=?", (nid,)).fetchone():
            raise HTTPException(404, "Nivel no encontrado")
        en_uso = conn.execute("SELECT COUNT(*) FROM empleados WHERE nivel_estudio_id=?", (nid,)).fetchone()[0]
        if en_uso:
            raise HTTPException(409, f"No se puede eliminar: {en_uso} empleado{'s' if en_uso > 1 else ''} tiene{'n' if en_uso > 1 else ''} este nivel asignado")
        conn.execute("DELETE FROM niveles_estudio WHERE id=?", (nid,))
    return {"ok": True}
