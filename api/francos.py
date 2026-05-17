from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from db.database import db_session
from auth.core import require_permiso

router = APIRouter(prefix="/api/francos", tags=["francos"])


class SaldoInicialIn(BaseModel):
    empleado_id: int
    mes: str    # YYYY-MM
    saldo: float


@router.get("/saldo-inicial")
def get_saldo_inicial(mes: str, _user=Depends(require_permiso("asistencia", "carga_inicial"))):
    """Devuelve empleados activos con su saldo inicial de francos para el mes dado."""
    with db_session() as conn:
        empleados = conn.execute(
            """SELECT e.id, e.nombre, e.apellido, c.nombre AS cargo
               FROM empleados e
               LEFT JOIN cargos c ON c.id = e.cargo_id
               WHERE e.activo = 1 AND e.tipo != 'acceso'
               ORDER BY e.apellido, e.nombre"""
        ).fetchall()
        saldos = {
            r["empleado_id"]: r["saldo"]
            for r in conn.execute(
                "SELECT empleado_id, saldo FROM saldo_francos WHERE mes=?", (mes,)
            ).fetchall()
        }

    return [
        {
            "empleado_id": e["id"],
            "apellido":    e["apellido"],
            "nombre":      e["nombre"],
            "cargo":       e["cargo"],
            "saldo":       saldos.get(e["id"]),
            "tiene_saldo": e["id"] in saldos,
        }
        for e in empleados
    ]


@router.post("/saldo-inicial", status_code=200)
def upsert_saldo_inicial(data: SaldoInicialIn, _user=Depends(require_permiso("asistencia", "carga_inicial"))):
    """Inserta o actualiza el saldo inicial de francos para un empleado en un mes."""
    with db_session() as conn:
        if not conn.execute("SELECT id FROM empleados WHERE id=?", (data.empleado_id,)).fetchone():
            raise HTTPException(404, "Empleado no encontrado")
        conn.execute("""
            INSERT INTO saldo_francos (empleado_id, mes, saldo)
            VALUES (?, ?, ?)
            ON CONFLICT(empleado_id, mes) DO UPDATE SET saldo = excluded.saldo
        """, (data.empleado_id, data.mes, data.saldo))
    return {"ok": True}
