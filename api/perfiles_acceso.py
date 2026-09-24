"""Perfiles de acceso: un nombre y el conjunto de puertas que abre.

Es el modelo con el que el usuario ya piensa el problema — "todas las puertas",
"todas menos oficina", "solo oficina", "solo cámaras" — y el mismo que usa
Enterprise. Lo que cambia es que acá el perfil sirve además para comparar: con
él, el sistema puede decir no solo quién está cargado de más en un lector, sino
también a quién le falta estar.

El cargo propone un perfil y la persona puede tener una excepción. Ninguno de
los dos es obligatorio: alguien que no abre ninguna puerta es un caso válido.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from auth.core import require_permiso
from db.database import db_session

router = APIRouter(prefix="/api/perfiles-acceso", tags=["perfiles-acceso"])


class PerfilIn(BaseModel):
    nombre: str
    descripcion: str | None = None
    activo: bool = True
    orden: int = 0
    dispositivos: list[int] = []

    @field_validator("nombre")
    @classmethod
    def _nombre(cls, v):
        if not v or not v.strip():
            raise ValueError("El nombre no puede estar vacío")
        return v.strip()


def _traer(conn, pid):
    fila = conn.execute("SELECT * FROM perfiles_acceso WHERE id=?", (pid,)).fetchone()
    if not fila:
        raise HTTPException(404, "Perfil no encontrado")
    d = dict(fila)
    d["dispositivos"] = [
        r["dispositivo_id"] for r in conn.execute(
            "SELECT dispositivo_id FROM perfiles_dispositivos WHERE perfil_id=?", (pid,)
        )
    ]
    d["empleados"] = conn.execute(
        "SELECT COUNT(*) FROM empleados WHERE perfil_acceso_id=? AND activo=1", (pid,)
    ).fetchone()[0]
    return d


def _guardar_puertas(conn, pid, dispositivos):
    """
    Reemplaza el conjunto de puertas del perfil.

    Se valida que cada id exista: una puerta borrada dejaría un perfil apuntando
    al vacío y el sistema calcularía mal a quién le falta acceso.
    """
    ids = sorted(set(int(x) for x in dispositivos))
    if ids:
        existen = {
            r["id"] for r in conn.execute(
                f"SELECT id FROM dispositivos WHERE id IN ({','.join('?' * len(ids))})", ids
            )
        }
        faltan = [i for i in ids if i not in existen]
        if faltan:
            raise HTTPException(400, f"No existen estos equipos: {faltan}")

    conn.execute("DELETE FROM perfiles_dispositivos WHERE perfil_id=?", (pid,))
    conn.executemany(
        "INSERT INTO perfiles_dispositivos (perfil_id, dispositivo_id) VALUES (?,?)",
        [(pid, i) for i in ids],
    )


@router.get("")
def listar(_user=Depends(require_permiso("accesos", "ver"))):
    """
    Los perfiles con sus puertas, y las puertas disponibles para la matriz.
    """
    with db_session() as conn:
        perfiles = [
            _traer(conn, r["id"]) for r in conn.execute(
                "SELECT id FROM perfiles_acceso ORDER BY orden, nombre"
            )
        ]
        puertas = [
            dict(r) for r in conn.execute(
                """SELECT id, nombre, ubicacion, activo FROM dispositivos
                    WHERE es_acceso = 1 ORDER BY orden, id"""
            )
        ]
    return {"perfiles": perfiles, "puertas": puertas}


@router.post("", status_code=201)
def crear(data: PerfilIn, _user=Depends(require_permiso("accesos", "editar"))):
    with db_session() as conn:
        try:
            cur = conn.execute(
                """INSERT INTO perfiles_acceso (nombre, descripcion, activo, orden)
                   VALUES (?,?,?,?)""",
                (data.nombre, (data.descripcion or "").strip() or None,
                 int(data.activo), data.orden),
            )
        except Exception:
            raise HTTPException(409, "Ya hay un perfil con ese nombre")
        _guardar_puertas(conn, cur.lastrowid, data.dispositivos)
        return _traer(conn, cur.lastrowid)


@router.put("/{pid}")
def actualizar(pid: int, data: PerfilIn,
               _user=Depends(require_permiso("accesos", "editar"))):
    with db_session() as conn:
        _traer(conn, pid)
        try:
            conn.execute(
                """UPDATE perfiles_acceso
                      SET nombre=?, descripcion=?, activo=?, orden=? WHERE id=?""",
                (data.nombre, (data.descripcion or "").strip() or None,
                 int(data.activo), data.orden, pid),
            )
        except Exception:
            raise HTTPException(409, "Ya hay un perfil con ese nombre")
        _guardar_puertas(conn, pid, data.dispositivos)
        return _traer(conn, pid)


@router.delete("/{pid}")
def eliminar(pid: int, _user=Depends(require_permiso("accesos", "eliminar"))):
    """
    Borra un perfil, salvo que haya gente usándolo.

    Sin este freno, los empleados quedarían sin perfil de golpe y el sistema
    diría que a todos ellos les falta acceso a todas las puertas.
    """
    with db_session() as conn:
        p = _traer(conn, pid)
        en_uso = conn.execute(
            "SELECT COUNT(*) FROM empleados WHERE perfil_acceso_id=?", (pid,)
        ).fetchone()[0]
        if en_uso:
            raise HTTPException(
                409,
                f"Hay {en_uso} empleado(s) con el perfil «{p['nombre']}». "
                f"Cambiales el perfil antes de borrarlo.",
            )
        conn.execute("DELETE FROM perfiles_dispositivos WHERE perfil_id=?", (pid,))
        conn.execute("DELETE FROM perfiles_acceso WHERE id=?", (pid,))
    return {"ok": True}
