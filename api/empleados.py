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
    tipo: str = "normal"
    nacionalidad: Optional[str] = None
    estado_civil: Optional[str] = None
    turno: Optional[str] = None
    sector: Optional[str] = None
    contacto_emergencia_nombre: Optional[str] = None
    contacto_emergencia_telefono: Optional[str] = None
    contacto_emergencia_parentesco: Optional[str] = None
    nivel_estudio: Optional[str] = None
    dom_calle: Optional[str] = None
    dom_numero: Optional[str] = None
    dom_piso: Optional[str] = None
    dom_entre_calle1: Optional[str] = None
    dom_entre_calle2: Optional[str] = None
    dom_barrio: Optional[str] = None
    dom_localidad: Optional[str] = None
    dom_provincia: Optional[str] = None
    dom_cp: Optional[str] = None
    dom_lat: Optional[float] = None
    dom_lng: Optional[float] = None
    dom_mapa: Optional[str] = None


@router.get("")
def list_empleados(todos: bool = False, sin_acceso: bool = False,
                   _user=Depends(require_permiso("empleados", "ver"))):
    with db_session() as conn:
        conds = []
        if not todos:
            conds.append("activo = 1")
        if sin_acceso:
            conds.append("tipo != 'acceso'")
        where = ("WHERE " + " AND ".join(conds)) if conds else ""
        rows = conn.execute(
            f"SELECT * FROM empleados {where} ORDER BY apellido, nombre"
        ).fetchall()
    return [dict(r) for r in rows]


@router.post("/conflictos/liberar/{eid}", status_code=200)
def liberar_id_dispositivo(eid: int, _user=Depends(require_permiso("empleados", "editar"))):
    with db_session() as conn:
        emp = conn.execute(
            "SELECT id, activo, user_id FROM empleados WHERE id=?", (eid,)
        ).fetchone()
        if not emp:
            raise HTTPException(404, "Empleado no encontrado")
        if emp["activo"]:
            raise HTTPException(400, "Solo se puede liberar el ID de un empleado inactivo")
        if not emp["user_id"]:
            raise HTTPException(400, "Este empleado no tiene ID de dispositivo asignado")
        conn.execute("UPDATE empleados SET user_id=NULL WHERE id=?", (eid,))
    return {"ok": True}


@router.get("/{eid}")
def get_empleado(eid: int, _user=Depends(require_permiso("empleados", "ver"))):
    with db_session() as conn:
        row = conn.execute("SELECT * FROM empleados WHERE id=?", (eid,)).fetchone()
        if not row:
            raise HTTPException(404, "Empleado no encontrado")
        return dict(row)


@router.put("/{eid}")
def update_empleado(eid: int, data: EmpleadoIn, _user=Depends(require_permiso("empleados", "editar"))):
    with db_session() as conn:
        row = conn.execute("SELECT id FROM empleados WHERE id=?", (eid,)).fetchone()
        if not row:
            raise HTTPException(404, "Empleado no encontrado")
        anterior = conn.execute("SELECT activo FROM empleados WHERE id=?", (eid,)).fetchone()
        if data.tipo not in ("normal", "jerarquico", "acceso"):
            raise HTTPException(400, "tipo inválido")
        conn.execute(
            """UPDATE empleados SET
               nombre=?, apellido=?, dni=?, cuil=?, fecha_nacimiento=?, fecha_ingreso=?,
               fecha_egreso=?, cargo=?, departamento=?, categoria=?, telefono=?, email=?,
               domicilio=?, observaciones=?, activo=?, tipo=?,
               nacionalidad=?, estado_civil=?, turno=?, sector=?,
               contacto_emergencia_nombre=?, contacto_emergencia_telefono=?,
               contacto_emergencia_parentesco=?, nivel_estudio=?,
               dom_calle=?, dom_numero=?, dom_piso=?, dom_entre_calle1=?, dom_entre_calle2=?,
               dom_barrio=?, dom_localidad=?, dom_provincia=?, dom_cp=?,
               dom_lat=?, dom_lng=?, dom_mapa=?,
               modificado_en=datetime('now','localtime')
               WHERE id=?""",
            (data.nombre.strip(), data.apellido.strip(), data.dni, data.cuil,
             data.fecha_nacimiento, data.fecha_ingreso, data.fecha_egreso,
             data.cargo, data.departamento, data.categoria,
             data.telefono, data.email, data.domicilio, data.observaciones, data.activo, data.tipo,
             data.nacionalidad, data.estado_civil, data.turno, data.sector,
             data.contacto_emergencia_nombre, data.contacto_emergencia_telefono,
             data.contacto_emergencia_parentesco, data.nivel_estudio,
             data.dom_calle, data.dom_numero, data.dom_piso, data.dom_entre_calle1, data.dom_entre_calle2,
             data.dom_barrio, data.dom_localidad, data.dom_provincia, data.dom_cp,
             data.dom_lat, data.dom_lng, data.dom_mapa,
             eid)
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
