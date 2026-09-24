"""A qué puertas accede cada persona.

Tres capas, y al final una sola lista de puertas:

    cargo    propone un perfil        (comodidad, no obligación)
    persona  tiene un perfil          (se puede cambiar)
             + excepciones puntuales  (quitar o agregar una puerta suelta)

Las excepciones existen para no tener que inventar un perfil nuevo cada vez que
alguien necesita algo distinto: así se llega a tener diez perfiles y usar tres.
Y como son un dato explícito, la pantalla de verificación las conoce y no las
marca como error — si no, cualquier restricción deliberada aparecería para
siempre como un problema, y una pantalla que muestra errores que no son errores
deja de mirarse.

El permiso para asignar está separado del de editar perfiles: poner un perfil
es parte de dar de alta a alguien, redefinir qué significa ese perfil cambia el
acceso de todos los que lo tienen.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from auth.core import get_current_user, require_permiso
from db.database import db_session

router = APIRouter(prefix="/api/accesos", tags=["accesos"])


class AsignacionIn(BaseModel):
    perfil_acceso_id: int | None = None


class ExcepcionIn(BaseModel):
    dispositivo_id: int
    modo: str
    motivo: str | None = None

    @field_validator("modo")
    @classmethod
    def _modo(cls, v):
        if v not in ("agregar", "quitar"):
            raise ValueError("El modo tiene que ser 'agregar' o 'quitar'")
        return v


def _empleado(conn, eid):
    fila = conn.execute(
        """SELECT e.id, e.nombre, e.apellido, e.user_id, e.activo, e.perfil_acceso_id,
                  e.cargo_id, c.nombre AS cargo, c.perfil_acceso_id AS perfil_del_cargo
             FROM empleados e
             LEFT JOIN cargos c ON c.id = e.cargo_id
            WHERE e.id = ?""",
        (eid,),
    ).fetchone()
    if not fila:
        raise HTTPException(404, "Empleado no encontrado")
    return dict(fila)


def puertas_de(conn, eid) -> dict:
    """
    Resuelve las tres capas en la lista final de puertas de una persona.

    Es lo único que el lector entiende: estar cargado o no estarlo. Las capas
    son para que se entienda de dónde sale cada puerta, no para el equipo.
    """
    emp = _empleado(conn, eid)
    perfil_id = emp["perfil_acceso_id"] or emp["perfil_del_cargo"]
    heredado = emp["perfil_acceso_id"] is None and emp["perfil_del_cargo"] is not None

    perfil = None
    del_perfil = set()
    if perfil_id:
        fila = conn.execute(
            "SELECT id, nombre FROM perfiles_acceso WHERE id=?", (perfil_id,)
        ).fetchone()
        if fila:
            perfil = dict(fila)
            del_perfil = {
                r["dispositivo_id"] for r in conn.execute(
                    "SELECT dispositivo_id FROM perfiles_dispositivos WHERE perfil_id=?",
                    (perfil_id,),
                )
            }

    excepciones = [
        dict(r) for r in conn.execute(
            """SELECT x.id, x.dispositivo_id, x.modo, x.motivo, x.creado_en,
                      d.nombre AS dispositivo
                 FROM accesos_excepciones x
                 JOIN dispositivos d ON d.id = x.dispositivo_id
                WHERE x.empleado_id = ?
             ORDER BY d.orden, d.id""",
            (eid,),
        )
    ]

    final = set(del_perfil)
    for x in excepciones:
        if x["modo"] == "quitar":
            final.discard(x["dispositivo_id"])
        else:
            final.add(x["dispositivo_id"])

    return {
        "empleado": emp,
        "perfil": perfil,
        "perfil_heredado_del_cargo": heredado,
        "puertas_del_perfil": sorted(del_perfil),
        "excepciones": excepciones,
        "puertas": sorted(final),
    }


@router.get("/empleado/{eid}")
def ver_empleado(eid: int, _user=Depends(require_permiso("accesos", "ver"))):
    """De dónde sale cada puerta de esta persona: del perfil o de una excepción."""
    with db_session() as conn:
        return puertas_de(conn, eid)


@router.put("/empleado/{eid}/perfil")
def asignar_perfil(eid: int, data: AsignacionIn,
                   _user=Depends(require_permiso("accesos", "asignar"))):
    """
    Le pone un perfil a una persona, o se lo saca para que vuelva a heredar el
    del cargo. Sin perfil y sin cargo, no abre ninguna puerta — que es un caso
    válido, no un dato faltante.
    """
    with db_session() as conn:
        _empleado(conn, eid)
        if data.perfil_acceso_id is not None:
            existe = conn.execute(
                "SELECT 1 FROM perfiles_acceso WHERE id=?", (data.perfil_acceso_id,)
            ).fetchone()
            if not existe:
                raise HTTPException(400, "Ese perfil no existe")
        conn.execute(
            "UPDATE empleados SET perfil_acceso_id=? WHERE id=?",
            (data.perfil_acceso_id, eid),
        )
        return puertas_de(conn, eid)


@router.post("/empleado/{eid}/excepcion", status_code=201)
def crear_excepcion(eid: int, data: ExcepcionIn,
                    usuario=Depends(require_permiso("accesos", "asignar"))):
    """
    Le saca o le da una puerta suelta a una persona, sin tocar el perfil.

    Una excepción que repite lo que el perfil ya dice se rechaza: no cambia
    nada y solo ensucia la ficha con ruido que después nadie sabe si borrar.
    """
    with db_session() as conn:
        _empleado(conn, eid)
        disp = conn.execute(
            "SELECT id, nombre FROM dispositivos WHERE id=?", (data.dispositivo_id,)
        ).fetchone()
        if not disp:
            raise HTTPException(400, "Ese equipo no existe")

        actual = puertas_de(conn, eid)
        ya_tiene = data.dispositivo_id in actual["puertas_del_perfil"]
        if data.modo == "agregar" and ya_tiene:
            raise HTTPException(
                409, f"El perfil ya incluye «{disp['nombre']}»: la excepción no cambiaría nada")
        if data.modo == "quitar" and not ya_tiene:
            raise HTTPException(
                409, f"El perfil no incluye «{disp['nombre']}»: la excepción no cambiaría nada")

        conn.execute(
            """INSERT INTO accesos_excepciones
                   (empleado_id, dispositivo_id, modo, motivo, creado_por)
               VALUES (?,?,?,?,?)
               ON CONFLICT (empleado_id, dispositivo_id) DO UPDATE SET
                   modo = excluded.modo, motivo = excluded.motivo,
                   creado_por = excluded.creado_por,
                   creado_en = datetime('now','localtime')""",
            # El id del usuario viaja como "sub" en el token, y es texto.
            (eid, data.dispositivo_id, data.modo,
             (data.motivo or "").strip() or None, int(usuario["sub"])),
        )
        return puertas_de(conn, eid)


@router.delete("/empleado/{eid}/excepcion/{did}")
def borrar_excepcion(eid: int, did: int,
                     _user=Depends(require_permiso("accesos", "asignar"))):
    """Saca la excepción: la persona vuelve a lo que diga su perfil."""
    with db_session() as conn:
        _empleado(conn, eid)
        cur = conn.execute(
            "DELETE FROM accesos_excepciones WHERE empleado_id=? AND dispositivo_id=?",
            (eid, did),
        )
        if not cur.rowcount:
            raise HTTPException(404, "Esa persona no tiene una excepción para ese equipo")
        return puertas_de(conn, eid)


@router.put("/cargo/{cid}/perfil")
def perfil_del_cargo(cid: int, data: AsignacionIn,
                     _user=Depends(require_permiso("accesos", "asignar"))):
    """
    El perfil que propone un cargo. Es solo un valor por defecto: no cambia a
    quien ya tenga uno propio, ni le saca el suyo a nadie.
    """
    with db_session() as conn:
        if not conn.execute("SELECT 1 FROM cargos WHERE id=?", (cid,)).fetchone():
            raise HTTPException(404, "Cargo no encontrado")
        if data.perfil_acceso_id is not None:
            if not conn.execute("SELECT 1 FROM perfiles_acceso WHERE id=?",
                                (data.perfil_acceso_id,)).fetchone():
                raise HTTPException(400, "Ese perfil no existe")
        conn.execute("UPDATE cargos SET perfil_acceso_id=? WHERE id=?",
                     (data.perfil_acceso_id, cid))
        heredan = conn.execute(
            """SELECT COUNT(*) FROM empleados
                WHERE cargo_id=? AND activo=1 AND perfil_acceso_id IS NULL""",
            (cid,),
        ).fetchone()[0]
    return {"ok": True, "heredan": heredan}


@router.get("/sin-perfil")
def sin_perfil(_user=Depends(require_permiso("accesos", "ver"))):
    """
    Los activos que no abren ninguna puerta: ni perfil propio ni del cargo.

    Puede ser correcto —administración no necesita abrir nada— o puede ser
    alguien que entró y a quien nadie le asignó el acceso todavía. El sistema
    no puede distinguirlos, pero sí ponerlos en una lista en vez de que
    aparezcan el día que la persona se queda afuera.
    """
    with db_session() as conn:
        filas = conn.execute(
            """SELECT e.id, e.user_id, e.nombre, e.apellido, e.fecha_ingreso,
                      c.nombre AS cargo
                 FROM empleados e
                 LEFT JOIN cargos c ON c.id = e.cargo_id
                WHERE e.activo = 1
                  AND e.perfil_acceso_id IS NULL
                  AND (c.perfil_acceso_id IS NULL OR e.cargo_id IS NULL)
             ORDER BY e.apellido, e.nombre"""
        ).fetchall()
    return [dict(f) for f in filas]
