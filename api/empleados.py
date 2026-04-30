from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from db.database import db_session
from auth.core import require_permiso, get_current_user

router = APIRouter(prefix="/api/empleados", tags=["empleados"])


class EmpleadoIn(BaseModel):
    nombre: str
    apellido: str
    dni: Optional[str] = None
    cuil: Optional[str] = None
    fecha_nacimiento: Optional[str] = None
    fecha_ingreso: Optional[str] = None
    fecha_egreso: Optional[str] = None
    cargo: Optional[str] = None
    departamento: Optional[str] = None
    categoria: Optional[str] = None
    telefono: Optional[str] = None
    email: Optional[str] = None
    domicilio: Optional[str] = None
    observaciones: Optional[str] = None
    activo: int = 1


@router.get("")
def list_empleados(todos: bool = False, _user=Depends(require_permiso("empleados", "ver"))):
    with db_session() as conn:
        where = "" if todos else "WHERE activo = 1"
        rows = conn.execute(
            f"SELECT * FROM empleados {where} ORDER BY apellido, nombre"
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/{eid}")
def get_empleado(eid: int, _user=Depends(require_permiso("empleados", "ver"))):
    with db_session() as conn:
        row = conn.execute("SELECT * FROM empleados WHERE id=?", (eid,)).fetchone()
        if not row:
            raise HTTPException(404, "Empleado no encontrado")
        return dict(row)


@router.post("", status_code=201)
def create_empleado(data: EmpleadoIn, _user=Depends(require_permiso("empleados", "editar"))):
    with db_session() as conn:
        cur = conn.execute(
            """INSERT INTO empleados
               (nombre, apellido, dni, cuil, fecha_nacimiento, fecha_ingreso, fecha_egreso,
                cargo, departamento, categoria, telefono, email, domicilio, observaciones, activo)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (data.nombre.strip(), data.apellido.strip(), data.dni, data.cuil,
             data.fecha_nacimiento, data.fecha_ingreso, data.fecha_egreso,
             data.cargo, data.departamento, data.categoria,
             data.telefono, data.email, data.domicilio, data.observaciones, data.activo)
        )
        eid = cur.lastrowid
        return dict(conn.execute("SELECT * FROM empleados WHERE id=?", (eid,)).fetchone())


@router.put("/{eid}")
def update_empleado(eid: int, data: EmpleadoIn, _user=Depends(require_permiso("empleados", "editar"))):
    with db_session() as conn:
        row = conn.execute("SELECT id FROM empleados WHERE id=?", (eid,)).fetchone()
        if not row:
            raise HTTPException(404, "Empleado no encontrado")
        anterior = conn.execute("SELECT activo FROM empleados WHERE id=?", (eid,)).fetchone()
        conn.execute(
            """UPDATE empleados SET
               nombre=?, apellido=?, dni=?, cuil=?, fecha_nacimiento=?, fecha_ingreso=?,
               fecha_egreso=?, cargo=?, departamento=?, categoria=?, telefono=?, email=?,
               domicilio=?, observaciones=?, activo=?,
               modificado_en=datetime('now','localtime')
               WHERE id=?""",
            (data.nombre.strip(), data.apellido.strip(), data.dni, data.cuil,
             data.fecha_nacimiento, data.fecha_ingreso, data.fecha_egreso,
             data.cargo, data.departamento, data.categoria,
             data.telefono, data.email, data.domicilio, data.observaciones, data.activo, eid)
        )
        if anterior["activo"] == 1 and data.activo == 0:
            # Usar fecha_egreso como corte; si no viene, tomar hoy
            corte = data.fecha_egreso or conn.execute(
                "SELECT date('now','localtime')"
            ).fetchone()[0]
            # Asegurar que fecha_egreso quede guardada
            if not data.fecha_egreso:
                conn.execute(
                    "UPDATE empleados SET fecha_egreso=? WHERE id=?", (corte, eid)
                )
            # Eliminar planificacion y resultados DESPUÉS del corte
            conn.execute(
                "DELETE FROM planificacion WHERE empleado_id=? AND fecha > ? AND auto_generado=1",
                (eid, corte)
            )
            conn.execute(
                "DELETE FROM resultados_dia WHERE empleado_id=? AND fecha > ? AND corregido_manualmente=0",
                (eid, corte)
            )
        return dict(conn.execute("SELECT * FROM empleados WHERE id=?", (eid,)).fetchone())
