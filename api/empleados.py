import shutil
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from db.database import db_session
from auth.core import require_permiso, get_current_user
from config import DATA_DIR

FOTOS_DIR            = DATA_DIR / "fotos"
FOTOS_PENDIENTES_DIR = DATA_DIR / "fotos_pendientes"

router = APIRouter(prefix="/api/empleados", tags=["empleados"])


class EmpleadoIn(BaseModel):
    nombre: str
    apellido: str
    dni: Optional[str] = None
    cuil: Optional[str] = None
    fecha_nacimiento: Optional[str] = None
    fecha_ingreso: Optional[str] = None
    fecha_egreso: Optional[str] = None
    cargo_id: Optional[int] = None
    categoria_id: Optional[int] = None
    telefono: Optional[str] = None
    email: Optional[str] = None
    domicilio: Optional[str] = None
    observaciones: Optional[str] = None
    activo: int = 1
    tipo: str = "normal"
    nacionalidad: Optional[str] = None
    estado_civil: Optional[str] = None
    turno_id: Optional[int] = None
    sector_id: Optional[int] = None
    contacto_emergencia_nombre: Optional[str] = None
    contacto_emergencia_telefono: Optional[str] = None
    contacto_emergencia_parentesco: Optional[str] = None
    nivel_estudio_id: Optional[int] = None
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
            conds.append("e.activo = 1")
        if sin_acceso:
            conds.append("e.tipo != 'acceso'")
        where = ("WHERE " + " AND ".join(conds)) if conds else ""
        rows = conn.execute(f"""
            SELECT c.departamento_id AS departamento_id,
                   e.*,
                   c.nombre   AS cargo,
                   d.nombre   AS departamento,
                   cat.nombre AS categoria,
                   tl.nombre  AS turno,
                   sl.nombre  AS sector,
                   ne.nombre  AS nivel_estudio
            FROM empleados e
            LEFT JOIN cargos c           ON c.id  = e.cargo_id
            LEFT JOIN departamentos d    ON d.id  = c.departamento_id
            LEFT JOIN categorias cat     ON cat.id = e.categoria_id
            LEFT JOIN turnos_legajo tl   ON tl.id = e.turno_id
            LEFT JOIN sectores_legajo sl ON sl.id = e.sector_id
            LEFT JOIN niveles_estudio ne ON ne.id = e.nivel_estudio_id
            {where} ORDER BY e.apellido, e.nombre
        """).fetchall()
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


@router.get("/fotos-pendientes")
def list_fotos_pendientes(_user=Depends(require_permiso("empleados", "editar"))):
    if not FOTOS_PENDIENTES_DIR.exists():
        return []
    files = sorted(
        (f for f in FOTOS_PENDIENTES_DIR.iterdir() if f.is_file()),
        key=lambda f: f.stat().st_mtime,
        reverse=True
    )
    return [{"filename": f.name, "url": f"/fotos/{f.name}"} for f in files]


@router.get("/{eid}")
def get_empleado(eid: int, _user=Depends(require_permiso("empleados", "ver"))):
    with db_session() as conn:
        row = conn.execute("""
            SELECT c.departamento_id AS departamento_id,
                   e.*,
                   c.nombre   AS cargo,
                   d.nombre   AS departamento,
                   cat.nombre AS categoria,
                   tl.nombre  AS turno,
                   sl.nombre  AS sector,
                   ne.nombre  AS nivel_estudio
            FROM empleados e
            LEFT JOIN cargos c           ON c.id  = e.cargo_id
            LEFT JOIN departamentos d    ON d.id  = c.departamento_id
            LEFT JOIN categorias cat     ON cat.id = e.categoria_id
            LEFT JOIN turnos_legajo tl   ON tl.id = e.turno_id
            LEFT JOIN sectores_legajo sl ON sl.id = e.sector_id
            LEFT JOIN niveles_estudio ne ON ne.id = e.nivel_estudio_id
            WHERE e.id=?
        """, (eid,)).fetchone()
        if not row:
            raise HTTPException(404, "Empleado no encontrado")
        return dict(row)


@router.put("/{eid}/horario-habitual")
def set_horario_habitual(eid: int, body: dict, _user=Depends(require_permiso("empleados", "editar"))):
    from fastapi import HTTPException
    horario_id = body.get("horario_id")        # None = sin horario
    fecha_desde = body.get("fecha_desde")      # Optional: limpiar planif desde esta fecha
    with db_session() as conn:
        if not conn.execute("SELECT id FROM empleados WHERE id=?", (eid,)).fetchone():
            raise HTTPException(404, "Empleado no encontrado")
        conn.execute(
            "UPDATE empleados SET horario_habitual_id=? WHERE id=?",
            (horario_id, eid)
        )
        if fecha_desde:
            # Verificar si fecha_desde cae dentro de una semana confirmada de este empleado
            conflicto = conn.execute(
                """SELECT ds.semana_inicio
                   FROM distribucion_detalle dd
                   JOIN distribucion_semana ds ON ds.id = dd.distribucion_id
                   WHERE dd.empleado_id = ?
                     AND ds.estado = 'confirmado'
                     AND ds.semana_inicio <= ?
                     AND DATE(ds.semana_inicio, '+6 days') >= ?
                   ORDER BY ds.semana_inicio
                   LIMIT 1""",
                (eid, fecha_desde, fecha_desde)
            ).fetchone()
            if conflicto:
                from datetime import date, timedelta
                lunes = date.fromisoformat(conflicto["semana_inicio"])
                domingo = lunes + timedelta(days=6)
                raise HTTPException(
                    409,
                    f"El horario está confirmado en la semana del {lunes.strftime('%d/%m')} "
                    f"al {domingo.strftime('%d/%m')}. "
                    f"Para modificarlo desde esa fecha es necesario desconfirmar esa semana en Distribución."
                )
            conn.execute(
                "DELETE FROM planificacion WHERE empleado_id=? AND fecha >= ? AND origen='distribucion'",
                (eid, fecha_desde)
            )
            conn.execute(
                "DELETE FROM resultados_dia WHERE empleado_id=? AND fecha >= ? AND corregido_manualmente=0",
                (eid, fecha_desde)
            )
    return {"ok": True, "horario_id": horario_id}


@router.put("/{eid}")
def update_empleado(eid: int, data: EmpleadoIn, _user=Depends(require_permiso("empleados", "editar"))):
    with db_session() as conn:
        row = conn.execute("SELECT id FROM empleados WHERE id=?", (eid,)).fetchone()
        if not row:
            raise HTTPException(404, "Empleado no encontrado")
        anterior = conn.execute("SELECT activo, cargo_id FROM empleados WHERE id=?", (eid,)).fetchone()
        cargo_id_anterior = anterior["cargo_id"]
        if data.tipo not in ("normal", "jerarquico", "acceso"):
            raise HTTPException(400, "tipo inválido")
        conn.execute(
            """UPDATE empleados SET
               nombre=?, apellido=?, dni=?, cuil=?, fecha_nacimiento=?, fecha_ingreso=?,
               cargo_id=?, categoria_id=?, telefono=?, email=?,
               domicilio=?, observaciones=?, activo=?, tipo=?,
               nacionalidad=?, estado_civil=?, turno_id=?, sector_id=?,
               contacto_emergencia_nombre=?, contacto_emergencia_telefono=?,
               contacto_emergencia_parentesco=?, nivel_estudio_id=?,
               dom_calle=?, dom_numero=?, dom_piso=?, dom_entre_calle1=?, dom_entre_calle2=?,
               dom_barrio=?, dom_localidad=?, dom_provincia=?, dom_cp=?,
               dom_lat=?, dom_lng=?, dom_mapa=?,
               modificado_en=datetime('now','localtime')
               WHERE id=?""",
            (data.nombre.strip(), data.apellido.strip(), data.dni, data.cuil,
             data.fecha_nacimiento, data.fecha_ingreso,
             data.cargo_id, data.categoria_id,
             data.telefono, data.email, data.domicilio, data.observaciones, data.activo, data.tipo,
             data.nacionalidad, data.estado_civil, data.turno_id, data.sector_id,
             data.contacto_emergencia_nombre, data.contacto_emergencia_telefono,
             data.contacto_emergencia_parentesco, data.nivel_estudio_id,
             data.dom_calle, data.dom_numero, data.dom_piso, data.dom_entre_calle1, data.dom_entre_calle2,
             data.dom_barrio, data.dom_localidad, data.dom_provincia, data.dom_cp,
             data.dom_lat, data.dom_lng, data.dom_mapa,
             eid)
        )
        if anterior["activo"] == 0 and data.activo == 1:
            conn.execute("UPDATE empleados SET fecha_egreso=NULL WHERE id=?", (eid,))
        # Si el cargo cambió, manejar transiciones entre sistemas de planificación
        if data.cargo_id and data.cargo_id != cargo_id_anterior:
            hoy = conn.execute("SELECT date('now','localtime')").fetchone()[0]

            def _usa_dist(cargo_id):
                if not cargo_id:
                    return False
                r = conn.execute(
                    """SELECT d.usa_distribucion, d.escribe_planificacion FROM cargos c
                       JOIN departamentos d ON d.id = c.departamento_id
                       WHERE c.id = ?""", (cargo_id,)
                ).fetchone()
                return bool(r and r["usa_distribucion"] and r["escribe_planificacion"])

            nuevo_usa_dist   = _usa_dist(data.cargo_id)
            anterior_usa_dist = _usa_dist(cargo_id_anterior)

            corte = hoy

            if nuevo_usa_dist and not anterior_usa_dist:
                # Regular → Distribución: limpiar planif auto y cerrar calendario activo
                conn.execute(
                    "DELETE FROM planificacion WHERE empleado_id=? AND fecha >= ? AND auto_generado=1",
                    (eid, corte)
                )
                conn.execute(
                    """UPDATE asignaciones SET fecha_hasta=?
                       WHERE empleado_id=? AND (fecha_hasta IS NULL OR fecha_hasta > ?)""",
                    (corte, eid, corte)
                )
            elif anterior_usa_dist and not nuevo_usa_dist:
                # Distribución → Regular: limpiar planif de distribución futura
                conn.execute(
                    "DELETE FROM planificacion WHERE empleado_id=? AND fecha >= ? AND origen='distribucion'",
                    (eid, corte)
                )

        baja = anterior["activo"] == 1 and data.activo == 0
        if baja:
            corte = data.fecha_egreso or conn.execute(
                "SELECT date('now','localtime')"
            ).fetchone()[0]
            conn.execute(
                "UPDATE empleados SET activo=0, fecha_egreso=? WHERE id=?", (corte, eid)
            )
            conn.execute(
                "DELETE FROM planificacion WHERE empleado_id=? AND fecha >= ? AND auto_generado=1",
                (eid, corte)
            )
            conn.execute(
                "DELETE FROM resultados_dia WHERE empleado_id=? AND fecha >= ? AND corregido_manualmente=0",
                (eid, corte)
            )
        return dict(conn.execute("""
            SELECT c.departamento_id AS departamento_id,
                   e.*,
                   c.nombre   AS cargo,
                   d.nombre   AS departamento,
                   cat.nombre AS categoria,
                   tl.nombre  AS turno,
                   sl.nombre  AS sector,
                   ne.nombre  AS nivel_estudio
            FROM empleados e
            LEFT JOIN cargos c           ON c.id  = e.cargo_id
            LEFT JOIN departamentos d    ON d.id  = c.departamento_id
            LEFT JOIN categorias cat     ON cat.id = e.categoria_id
            LEFT JOIN turnos_legajo tl   ON tl.id = e.turno_id
            LEFT JOIN sectores_legajo sl ON sl.id = e.sector_id
            LEFT JOIN niveles_estudio ne ON ne.id = e.nivel_estudio_id
            WHERE e.id=?
        """, (eid,)).fetchone())


@router.post("/{eid}/foto")
def asignar_foto(eid: int, body: dict, _user=Depends(require_permiso("empleados", "editar"))):
    filename = body.get("filename")
    if not filename:
        raise HTTPException(400, "filename requerido")
    src = FOTOS_PENDIENTES_DIR / filename
    if not src.exists():
        raise HTTPException(404, "Foto pendiente no encontrada")
    FOTOS_DIR.mkdir(parents=True, exist_ok=True)
    dest = FOTOS_DIR / filename
    with db_session() as conn:
        old = conn.execute("SELECT foto_path FROM empleados WHERE id=?", (eid,)).fetchone()
        if not old:
            raise HTTPException(404, "Empleado no encontrado")
        if old["foto_path"]:
            old_file = FOTOS_DIR / Path(old["foto_path"]).name
            old_file.unlink(missing_ok=True)
        shutil.move(str(src), str(dest))
        conn.execute("UPDATE empleados SET foto_path=? WHERE id=?", (f"/fotos/{filename}", eid))
    return {"ok": True, "foto_path": f"/fotos/{filename}"}


@router.delete("/{eid}/foto")
def eliminar_foto(eid: int, _user=Depends(require_permiso("empleados", "editar"))):
    with db_session() as conn:
        row = conn.execute("SELECT foto_path FROM empleados WHERE id=?", (eid,)).fetchone()
        if not row:
            raise HTTPException(404, "Empleado no encontrado")
        if row["foto_path"]:
            (FOTOS_DIR / Path(row["foto_path"]).name).unlink(missing_ok=True)
        conn.execute("UPDATE empleados SET foto_path=NULL WHERE id=?", (eid,))
    return {"ok": True}
