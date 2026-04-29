from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from db.database import db_session, _sugerir_turno_id

router = APIRouter(prefix="/api/horarios", tags=["horarios"])


class BloqueIn(BaseModel):
    bloque: int
    hora_entrada: str
    hora_salida: str
    cruza_medianoche: bool = False
    tolerancia_entrada_antes: int = 15
    tolerancia_entrada_despues: int = 60
    tolerancia_tarde: int = 10
    tolerancia_salida_antes: int = 30
    tolerancia_salida_despues: int = 60


class HorarioIn(BaseModel):
    nombre: str
    tipo: str
    bloques: list[BloqueIn]


def _get_horario(conn, horario_id: int):
    h = conn.execute(
        """SELECT h.*, t.nombre as turno_nombre
           FROM horarios h LEFT JOIN turnos t ON t.id = h.turno_id
           WHERE h.id = ?""",
        (horario_id,),
    ).fetchone()
    if not h:
        raise HTTPException(status_code=404, detail="Horario no encontrado")
    bloques = conn.execute(
        "SELECT * FROM horarios_bloques WHERE horario_id = ? ORDER BY bloque",
        (horario_id,),
    ).fetchall()
    return {**dict(h), "bloques": [dict(b) for b in bloques]}


@router.get("")
def list_horarios():
    with db_session() as conn:
        rows = conn.execute(
            """SELECT h.*, t.nombre as turno_nombre
               FROM horarios h LEFT JOIN turnos t ON t.id = h.turno_id
               ORDER BY h.tipo, h.nombre"""
        ).fetchall()
        result = []
        for h in rows:
            bloques = conn.execute(
                "SELECT * FROM horarios_bloques WHERE horario_id = ? ORDER BY bloque",
                (h["id"],),
            ).fetchall()
            result.append({**dict(h), "bloques": [dict(b) for b in bloques]})
    return result


@router.get("/{horario_id}")
def get_horario(horario_id: int):
    with db_session() as conn:
        return _get_horario(conn, horario_id)


@router.post("", status_code=201)
def create_horario(data: HorarioIn):
    if data.tipo not in ("simple", "cortado"):
        raise HTTPException(status_code=422, detail="tipo debe ser 'simple' o 'cortado'")
    n_bloques = 1 if data.tipo == "simple" else 2
    if len(data.bloques) != n_bloques:
        raise HTTPException(
            status_code=422,
            detail=f"Tipo '{data.tipo}' requiere exactamente {n_bloques} bloque(s)"
        )
    with db_session() as conn:
        cur = conn.execute(
            "INSERT INTO horarios (nombre, tipo) VALUES (?, ?)",
            (data.nombre.strip(), data.tipo),
        )
        horario_id = cur.lastrowid
        for b in data.bloques:
            conn.execute(
                """
                INSERT INTO horarios_bloques
                    (horario_id, bloque, hora_entrada, hora_salida, cruza_medianoche,
                     tolerancia_entrada_antes, tolerancia_entrada_despues, tolerancia_tarde,
                     tolerancia_salida_antes, tolerancia_salida_despues)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (horario_id, b.bloque, b.hora_entrada, b.hora_salida,
                 int(b.cruza_medianoche), b.tolerancia_entrada_antes,
                 b.tolerancia_entrada_despues, b.tolerancia_tarde,
                 b.tolerancia_salida_antes, b.tolerancia_salida_despues),
            )
        # Auto-asignar turno según hora de entrada del primer bloque
        turno_id = _sugerir_turno_id(conn, data.bloques[0].hora_entrada)
        if turno_id:
            conn.execute("UPDATE horarios SET turno_id=? WHERE id=?", (turno_id, horario_id))
        return _get_horario(conn, horario_id)


class TurnoAsignacionIn(BaseModel):
    turno_id: Optional[int] = None


@router.patch("/{horario_id}/turno")
def set_turno(horario_id: int, data: TurnoAsignacionIn):
    with db_session() as conn:
        if not conn.execute("SELECT id FROM horarios WHERE id=?", (horario_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Horario no encontrado")
        conn.execute("UPDATE horarios SET turno_id=? WHERE id=?", (data.turno_id, horario_id))
    return {"ok": True}


@router.put("/{horario_id}")
def update_horario(horario_id: int, data: HorarioIn):
    if data.tipo not in ("simple", "cortado"):
        raise HTTPException(status_code=422, detail="tipo debe ser 'simple' o 'cortado'")
    n_bloques = 1 if data.tipo == "simple" else 2
    if len(data.bloques) != n_bloques:
        raise HTTPException(
            status_code=422,
            detail=f"Tipo '{data.tipo}' requiere exactamente {n_bloques} bloque(s)"
        )
    with db_session() as conn:
        h = conn.execute("SELECT id FROM horarios WHERE id = ?", (horario_id,)).fetchone()
        if not h:
            raise HTTPException(status_code=404, detail="Horario no encontrado")
        conn.execute(
            "UPDATE horarios SET nombre = ?, tipo = ? WHERE id = ?",
            (data.nombre.strip(), data.tipo, horario_id),
        )
        conn.execute("DELETE FROM horarios_bloques WHERE horario_id = ?", (horario_id,))
        for b in data.bloques:
            conn.execute(
                """
                INSERT INTO horarios_bloques
                    (horario_id, bloque, hora_entrada, hora_salida, cruza_medianoche,
                     tolerancia_entrada_antes, tolerancia_entrada_despues, tolerancia_tarde,
                     tolerancia_salida_antes, tolerancia_salida_despues)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (horario_id, b.bloque, b.hora_entrada, b.hora_salida,
                 int(b.cruza_medianoche), b.tolerancia_entrada_antes,
                 b.tolerancia_entrada_despues, b.tolerancia_tarde,
                 b.tolerancia_salida_antes, b.tolerancia_salida_despues),
            )
        return _get_horario(conn, horario_id)


@router.delete("/{horario_id}")
def delete_horario(horario_id: int):
    with db_session() as conn:
        h = conn.execute("SELECT id FROM horarios WHERE id = ?", (horario_id,)).fetchone()
        if not h:
            raise HTTPException(status_code=404, detail="Horario no encontrado")
        en_uso = conn.execute(
            "SELECT COUNT(*) FROM asignaciones WHERE horario_id = ?", (horario_id,)
        ).fetchone()[0]
        if en_uso:
            raise HTTPException(
                status_code=409,
                detail="No se puede eliminar: el horario está asignado a empleados"
            )
        conn.execute("DELETE FROM horarios_bloques WHERE horario_id = ?", (horario_id,))
        conn.execute("DELETE FROM horarios WHERE id = ?", (horario_id,))
    return {"ok": True}
