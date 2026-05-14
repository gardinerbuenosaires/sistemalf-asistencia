from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from db.database import db_session
from auth.core import require_permiso

router = APIRouter(prefix="/api/turnos", tags=["turnos"])


class TurnoIn(BaseModel):
    nombre: str
    hora_desde: str  # "HH:MM"


@router.get("")
def list_turnos():
    with db_session() as conn:
        rows = conn.execute("SELECT * FROM turnos ORDER BY hora_desde").fetchall()
    return [dict(r) for r in rows]


@router.post("", status_code=201)
def create_turno(data: TurnoIn, _user=Depends(require_permiso("horarios", "editar"))):
    with db_session() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO turnos (nombre, hora_desde) VALUES (?, ?)",
                (data.nombre.strip(), data.hora_desde),
            )
            return dict(conn.execute("SELECT * FROM turnos WHERE id=?", (cur.lastrowid,)).fetchone())
        except Exception:
            raise HTTPException(status_code=409, detail="Ya existe un turno con ese nombre")


@router.put("/{turno_id}")
def update_turno(turno_id: int, data: TurnoIn, _user=Depends(require_permiso("horarios", "editar"))):
    with db_session() as conn:
        if not conn.execute("SELECT id FROM turnos WHERE id=?", (turno_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Turno no encontrado")
        try:
            conn.execute(
                "UPDATE turnos SET nombre=?, hora_desde=? WHERE id=?",
                (data.nombre.strip(), data.hora_desde, turno_id),
            )
        except Exception:
            raise HTTPException(status_code=409, detail="Ya existe un turno con ese nombre")
        return dict(conn.execute("SELECT * FROM turnos WHERE id=?", (turno_id,)).fetchone())


@router.delete("/{turno_id}")
def delete_turno(turno_id: int, _user=Depends(require_permiso("horarios", "eliminar"))):
    with db_session() as conn:
        if not conn.execute("SELECT id FROM turnos WHERE id=?", (turno_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Turno no encontrado")
        en_uso = conn.execute(
            "SELECT COUNT(*) FROM horarios WHERE turno_id=?", (turno_id,)
        ).fetchone()[0]
        if en_uso:
            raise HTTPException(
                status_code=409,
                detail=f"No se puede eliminar: {en_uso} horario(s) usan este turno",
            )
        conn.execute("DELETE FROM turnos WHERE id=?", (turno_id,))
    return {"ok": True}
