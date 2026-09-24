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
    # El acceso sale SOLO del perfil propio. El cargo propone, no decide: si
    # decidiera, cambiarle el cargo a alguien le cambiaria las puertas, y eso lo
    # puede hacer quien edita empleados aunque no tenga permiso de accesos.
    perfil_id = emp["perfil_acceso_id"]

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
        # Una excepción puede quedar obsoleta sin que nadie la toque: si a la
        # persona le cambian el perfil, "agregar oficina" deja de hacer nada
        # cuando el perfil nuevo ya incluye oficina. No se borra sola —eso sería
        # decidir por el usuario— pero se marca, porque una excepción que no
        # hace nada y parece que hace algo es peor que no tenerla.
        en_perfil = x["dispositivo_id"] in del_perfil
        x["sin_efecto"] = ((x["modo"] == "agregar" and en_perfil)
                           or (x["modo"] == "quitar" and not en_perfil))
        if x["modo"] == "quitar":
            final.discard(x["dispositivo_id"])
        else:
            final.add(x["dispositivo_id"])

    # Lo que el cargo propone, solo si la persona no tiene perfil propio. Es una
    # sugerencia para que alguien con permiso la aplique de un click, no algo
    # que ya esté pasando.
    sugerencia = None
    if perfil_id is None and emp["perfil_del_cargo"]:
        fila = conn.execute(
            "SELECT id, nombre FROM perfiles_acceso WHERE id=?", (emp["perfil_del_cargo"],)
        ).fetchone()
        if fila:
            sugerencia = dict(fila)

    return {
        "empleado": emp,
        "perfil": perfil,
        "sugerencia_del_cargo": sugerencia,
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
    Le pone un perfil a una persona, o se lo saca y entonces no abre nada.

    Sacarle el perfil **borra sus excepciones**, porque una excepción modifica
    un perfil: dice "como su perfil, pero sin Oficina". Sin perfil no hay nada
    que modificar, y dejarlas tiene dos problemas. Quedan como residuo que
    parece decir algo y no dice nada; y si más adelante se le vuelve a poner un
    perfil, esa excepción vieja vuelve a actuar sin que nadie lo haya decidido.

    Cambiar de un perfil a otro NO las borra: "esta persona no entra a Oficina"
    es una decisión sobre la persona, no sobre el perfil, y sigue valiendo.
    Cuando queda sin efecto, se marca.

    La confirmación la hace la pantalla antes de llamar acá, mostrando cuáles
    se van a borrar con su motivo.
    """
    with db_session() as conn:
        _empleado(conn, eid)
        if data.perfil_acceso_id is not None:
            existe = conn.execute(
                "SELECT 1 FROM perfiles_acceso WHERE id=?", (data.perfil_acceso_id,)
            ).fetchone()
            if not existe:
                raise HTTPException(400, "Ese perfil no existe")

        borradas = 0
        if data.perfil_acceso_id is None:
            borradas = conn.execute(
                "DELETE FROM accesos_excepciones WHERE empleado_id=?", (eid,)
            ).rowcount

        conn.execute(
            "UPDATE empleados SET perfil_acceso_id=? WHERE id=?",
            (data.perfil_acceso_id, eid),
        )
        return {**puertas_de(conn, eid), "excepciones_borradas": borradas}


@router.post("/empleado/{eid}/excepcion", status_code=201)
def crear_excepcion(eid: int, data: ExcepcionIn,
                    usuario=Depends(require_permiso("accesos", "asignar"))):
    """
    Le saca o le da una puerta suelta a una persona, sin tocar el perfil.

    Necesita que la persona tenga un perfil: una excepción lo modifica, y sin
    perfil no hay nada que modificar. Quien necesita una combinación que no
    existe necesita un perfil nuevo, no una excepción sobre la nada — un perfil
    llamado "Solo depósito" se entiende leyéndolo.

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
        if actual["perfil"] is None:
            raise HTTPException(
                409,
                "Esta persona no tiene perfil de acceso, y una excepción modifica "
                "un perfil. Asignale uno primero, o creá un perfil nuevo si "
                "necesita una combinación de puertas que todavía no existe.",
            )
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


@router.get("/plan")
def plan(_user=Depends(require_permiso("accesos", "ver"))):
    """
    Qué habría que cambiar en cada lector para que refleje la política.

    Lee los equipos y compara contra lo que deberían tener. No escribe nada:
    esto arma el plan, aplicarlo es otra cosa y todavía no está habilitado.

    Lee además el equipo de asistencia, porque de ahí sale la huella que habría
    que copiar: sin eso no se puede saber a quién falta enrolar, y cargar a
    alguien sin su huella lo deja sin poder abrir igual.
    """
    from sync.lectores import leer_padrones
    from sync.plan_accesos import armar_plan

    with db_session() as conn:
        puertas = [
            dict(r) for r in conn.execute(
                """SELECT id, nombre, ubicacion, ip, puerto, password, timeout, protocolo
                     FROM dispositivos
                    WHERE activo = 1 AND es_acceso = 1 AND protocolo = 'pull'
                      AND ip IS NOT NULL
                 ORDER BY orden, id"""
            )
        ]
        maestros = [
            dict(r) for r in conn.execute(
                """SELECT id, nombre, ip, puerto, password, timeout, protocolo
                     FROM dispositivos
                    WHERE activo = 1 AND cuenta_asistencia = 1 AND protocolo = 'pull'
                      AND ip IS NOT NULL
                 ORDER BY orden, id LIMIT 1"""
            )
        ]

    if not puertas:
        return {"total": {"agregar": 0, "sacar": 0, "sin_huella": 0,
                          "sin_leer": 0, "puertas": 0},
                "puertas": [], "huellas_verificadas": False,
                "aviso": "No hay ningún equipo marcado como «Abre una puerta»."}

    # Todo junto en una sola tanda: el maestro también, que es solo uno más.
    lecturas = leer_padrones(puertas + maestros)
    maestro = lecturas.get(maestros[0]["id"]) if maestros else None

    with db_session() as conn:
        return armar_plan(conn, puertas, lecturas, maestro)


@router.get("/sin-perfil")
def sin_perfil(_user=Depends(require_permiso("accesos", "ver"))):
    """
    Los activos sin perfil propio: la lista de pendientes de asignar.

    El cargo no les da acceso solo —propone— así que todos estos hoy no abren
    ninguna puerta. Puede estar bien (administración no necesita abrir nada) o
    puede ser alguien que entró y a quien nadie le asignó el acceso todavía. El
    sistema no puede distinguirlos, pero sí ponerlos en una lista en vez de que
    aparezcan el día que la persona se queda afuera.

    Los que tienen un cargo que propone algo vienen con esa sugerencia, y van
    al final: resolverlos es aceptar lo que ya está escrito. Arriba quedan los
    que no tienen ninguna, que son los que piden una decisión y por eso los que
    más fácil se pasan por alto.
    """
    with db_session() as conn:
        filas = conn.execute(
            """SELECT e.id, e.user_id, e.nombre, e.apellido, e.fecha_ingreso,
                      c.nombre AS cargo, c.id AS cargo_id,
                      c.perfil_acceso_id AS sugerencia_id,
                      p.nombre AS sugerencia_nombre
                 FROM empleados e
                 LEFT JOIN cargos c ON c.id = e.cargo_id
                 LEFT JOIN perfiles_acceso p ON p.id = c.perfil_acceso_id
                WHERE e.activo = 1 AND e.perfil_acceso_id IS NULL
             ORDER BY (c.perfil_acceso_id IS NOT NULL), e.apellido, e.nombre"""
        ).fetchall()
    return [dict(f) for f in filas]
