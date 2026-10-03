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

Cada cosa tiene su permiso, y la escala va de mayor a menor alcance:

    editar    redefinir un perfil — cambia el acceso de todos los que lo tengan
    excepcion desviarse de la política para una persona
    asignar   aplicar la política: a esta persona le corresponde este perfil

Asignar es parte de dar de alta a alguien y puede vivir en RRHH. Las excepciones
van aparte porque son lo más difícil de auditar: un perfil se ve de un vistazo
en la matriz, una excepción solo abriendo la ficha de esa persona.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from auth.core import get_current_user, require_permiso, tiene_permiso
from db.database import db_session

router = APIRouter(prefix="/api/accesos", tags=["accesos"])


# Los niveles que se usan. El equipo también entiende 14 (super admin), pero no
# se ofrece: no se usa acá, y una opción que nadie necesita en una lista de
# permisos es una invitación a elegirla por error. Si un equipo trae un 14, se
# lee y se informa; otra cosa es ponerlo desde acá.
NIVELES_LECTOR = {0: "No administra el lector",
                  2: "Enrolador — puede dar de alta y tomar huellas",
                  6: "Administrador del lector"}


class AsignacionIn(BaseModel):
    perfil_acceso_id: int | None = None


class NombreLectorIn(BaseModel):
    nombre_lector: str | None = None

    @field_validator("nombre_lector")
    @classmethod
    def _nombre(cls, v):
        v = (v or "").strip()
        if len(v) > 24:
            raise ValueError("El equipo de asistencia guarda hasta 24 caracteres")
        return v or None


class NivelLectorIn(BaseModel):
    nivel_lector: int

    @field_validator("nivel_lector")
    @classmethod
    def _nivel(cls, v):
        if v not in NIVELES_LECTOR:
            raise ValueError(f"Nivel inválido. Los que se usan son "
                             f"{', '.join(str(k) for k in NIVELES_LECTOR)}")
        return v


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
                  e.nivel_lector, e.nombre_lector, e.cargo_id, c.nombre AS cargo
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

    return {
        "empleado": emp,
        "niveles_lector": NIVELES_LECTOR,
        "largo_nombre": {"asistencia": 24, "puertas": 8},
        "perfil": perfil,
        "puertas_del_perfil": sorted(del_perfil),
        "excepciones": excepciones,
        "puertas": sorted(final),
    }


@router.get("/empleado/{eid}")
def ver_empleado(eid: int, _user=Depends(require_permiso("accesos", "ver"))):
    """De dónde sale cada puerta de esta persona: del perfil o de una excepción."""
    with db_session() as conn:
        return puertas_de(conn, eid)


@router.put("/empleado/{eid}/nivel-lector")
def nivel_lector(eid: int, data: NivelLectorIn,
                 _user=Depends(require_permiso("accesos", "editar"))):
    """
    Quién puede administrar el equipo de asistencia parado frente a él.

    Pide el permiso más fuerte de accesos, y no el de asignar, porque no es
    aplicar una política: un enrolador puede dar de alta a cualquiera y tomarle
    la huella. Eso es crear identidades, que es de donde sale todo lo demás —el
    fichaje y las puertas. Quien puede hacer eso puede fabricarse un acceso sin
    pasar por ninguna de las pantallas que controlamos acá.

    Vale para el equipo de asistencia y no para las puertas: no tienen pantalla,
    así que en ellas no hay nada que administrar.
    """
    with db_session() as conn:
        _empleado(conn, eid)     # 404 si no existe
        conn.execute("UPDATE empleados SET nivel_lector=? WHERE id=?",
                     (data.nivel_lector, eid))
        return puertas_de(conn, eid)


@router.put("/empleado/{eid}/nombre-lector")
def nombre_lector(eid: int, data: NombreLectorIn,
                  _user=Depends(require_permiso("accesos", "asignar"))):
    """
    Cómo se llama esta persona dentro de los equipos.

    Es el texto que el lector muestra en pantalla al apoyar el dedo, y lo elige
    alguien para que sea reconocible ahí. No es el nombre del legajo: el equipo
    de asistencia guarda 24 caracteres y las puertas 8, así que un nombre
    completo llega cortado y deja de identificar a nadie.

    Vacío significa que no opinamos: al escribir se conserva el que el equipo ya
    tenga. Así una columna vacía no le borra el nombre a nadie.
    """
    with db_session() as conn:
        _empleado(conn, eid)
        conn.execute("UPDATE empleados SET nombre_lector=? WHERE id=?",
                     (data.nombre_lector, eid))
        return puertas_de(conn, eid)


@router.get("/empleado/{eid}/en-lectores")
def empleado_en_lectores(eid: int, _user=Depends(require_permiso("accesos", "ver"))):
    """
    Lee los equipos y dice si esta persona está realmente cargada en cada uno.

    Es la contracara de la ficha: la ficha dice qué *debería* abrir, esto dice
    qué tiene el equipo. Cuando alguien se queda afuera, la pregunta es esta.

    Lee también la huella, no solo si figura en la lista: alguien cargado sin su
    huella aparece en el lista y no abre igual, y ese caso mirando el lista
    parece resuelto.

    No escribe nada.
    """
    from sync.lectores import leer_cargados_varios
    from sync.verificar_acceso import verificar

    with db_session() as conn:
        ficha = puertas_de(conn, eid)
        equipos = [
            dict(r) for r in conn.execute(
                """SELECT id, nombre, ubicacion, ip, puerto, password, timeout,
                          protocolo, es_acceso, cuenta_asistencia
                     FROM dispositivos
                    WHERE activo = 1 AND protocolo = 'pull' AND ip IS NOT NULL
                      AND (es_acceso = 1 OR cuenta_asistencia = 1)
                 ORDER BY orden, id"""
            )
        ]

    user_id = (ficha["empleado"]["user_id"] or "").strip()
    if not user_id:
        # Sin número de dispositivo no hay a quién buscar en el equipo. No es un
        # error: es alguien que todavía no se enroló.
        return {"ficha": ficha, "equipos": [], "resumen": None, "user_id": None,
                "aviso": "Esta persona no tiene número de dispositivo, así que "
                         "no puede estar cargada en ningún lector."}
    if not equipos:
        return {"ficha": ficha, "equipos": [], "resumen": None, "user_id": user_id,
                "aviso": "No hay equipos que se puedan consultar."}

    lecturas = leer_cargados_varios(equipos, con_huellas=True)
    return {"ficha": ficha,
            **verificar(user_id, equipos, lecturas, set(ficha["puertas"]),
                        ficha["empleado"].get("nombre_lector"))}


@router.put("/empleado/{eid}/perfil")
def asignar_perfil(eid: int, data: AsignacionIn,
                   usuario=Depends(require_permiso("accesos", "asignar"))):
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
            # Una operación exige los permisos de todos sus efectos. Sacar el
            # perfil borra las excepciones, así que sin permiso de excepciones
            # esto sería un camino indirecto para deshacerlas: alcanzaba con
            # sacar el perfil y volver a ponerlo para devolverle a alguien una
            # puerta que le habían quitado a propósito.
            cuantas = conn.execute(
                "SELECT COUNT(*) FROM accesos_excepciones WHERE empleado_id=?", (eid,)
            ).fetchone()[0]
            if cuantas and not tiene_permiso(usuario.get("rol_id"), "accesos", "excepcion"):
                raise HTTPException(
                    403,
                    f"Esta persona tiene {cuantas} excepción(es) y sacarle el perfil "
                    f"las borraría. Para eso hace falta permiso de excepciones: "
                    f"pedí que las saquen primero, o que hagan este cambio.",
                )
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
                    usuario=Depends(require_permiso("accesos", "excepcion"))):
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
                     _user=Depends(require_permiso("accesos", "excepcion"))):
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
    from sync.lectores import leer_cargados_varios
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
        # Puertas que algún perfil todavía da, pero que el sistema no está
        # administrando: desactivadas, sin IP, o marcadas como que no abren
        # puerta. Callarlo seria lo peor: la gente sigue cargada ahí y nadie se
        # entera, egresados incluidos.
        fuera = [
            dict(r) for r in conn.execute(
                """SELECT DISTINCT d.id, d.nombre, d.activo, d.es_acceso, d.protocolo,
                          (d.ip IS NULL) AS sin_ip
                     FROM dispositivos d
                     JOIN perfiles_dispositivos pd ON pd.dispositivo_id = d.id
                    WHERE NOT (d.activo = 1 AND d.es_acceso = 1
                               AND d.protocolo = 'pull' AND d.ip IS NOT NULL)
                 ORDER BY d.orden, d.id"""
            )
        ]
        for f in fuera:
            f["motivo"] = ("está desactivada" if not f["activo"]
                           else "ya no figura como puerta" if not f["es_acceso"]
                           else "es un equipo push" if f["protocolo"] == "push"
                           else "no tiene IP cargada")
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
                "puertas": [], "huellas_verificadas": False, "fuera_de_plan": fuera,
                "aviso": "No hay ningún equipo marcado como «Abre una puerta»."}

    # Todo en una sola tanda, pero las huellas solo al maestro: de ahí sale la
    # huella que habría que copiar, así que sin leerla no se puede distinguir
    # "le falta esta puerta" de "no hay nada que copiarle". A las puertas no
    # hacen falta para saber a quién agregar o sacar, y leerles los templates
    # multiplicaría el tiempo de una pantalla que ya consulta todos los equipos.
    lecturas = leer_cargados_varios(puertas + maestros,
                             con_huellas={m["id"] for m in maestros})
    maestro = lecturas.get(maestros[0]["id"]) if maestros else None

    with db_session() as conn:
        return {**armar_plan(conn, puertas, lecturas, maestro), "fuera_de_plan": fuera}


@router.get("/descubrir")
def descubrir(_user=Depends(require_permiso("accesos", "ver"))):
    """
    Propone perfiles a partir de lo que los lectores ya tienen cargado.

    Es la carga inicial: nadie tiene perfil, son cientos de personas, y
    asignarlas de a una no es una opción. Pero los perfiles ya existen dentro
    de los equipos — solo hay que leerlos y ponerles nombre.

    Solo lectura. Lo que se elija se aplica con el endpoint de al lado.
    """
    from sync.lectores import leer_cargados_varios
    from sync.descubrir_perfiles import agrupar_por_puertas, emparejar_con_perfiles

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
    if not puertas:
        return {"grupos": [], "sin_leer": [], "ignorados": [], "completo": False,
                "aviso": "No hay ningún equipo marcado como «Abre una puerta»."}

    lecturas = leer_cargados_varios(puertas)

    with db_session() as conn:
        empleados = {
            str(r["user_id"]).strip(): dict(r)
            for r in conn.execute(
                """SELECT id, user_id, nombre, apellido, activo, perfil_acceso_id
                     FROM empleados WHERE user_id IS NOT NULL"""
            )
        }
        perfiles = [
            {"id": r["id"], "nombre": r["nombre"],
             "dispositivos": [
                 x["dispositivo_id"] for x in conn.execute(
                     "SELECT dispositivo_id FROM perfiles_dispositivos WHERE perfil_id=?",
                     (r["id"],))
             ]}
            for r in conn.execute("SELECT id, nombre FROM perfiles_acceso ORDER BY nombre")
        ]

    resultado = agrupar_por_puertas(lecturas, puertas, empleados)
    emparejar_con_perfiles(resultado["grupos"], perfiles)
    return resultado


class AplicarGrupoIn(BaseModel):
    empleados: list[int]
    perfil_acceso_id: int


@router.post("/descubrir/aplicar")
def aplicar_grupo(data: AplicarGrupoIn,
                  _user=Depends(require_permiso("accesos", "asignar"))):
    """
    Le pone un perfil a los empleados de un grupo descubierto.

    No toca a quien ya tiene perfil propio: esa persona es una decisión tomada,
    a veces con excepciones encima, y pisarla en masa borraría justo lo que
    alguien se tomó el trabajo de definir.
    """
    if not data.empleados:
        raise HTTPException(400, "No viene ningún empleado")
    with db_session() as conn:
        if not conn.execute("SELECT 1 FROM perfiles_acceso WHERE id=?",
                            (data.perfil_acceso_id,)).fetchone():
            raise HTTPException(400, "Ese perfil no existe")
        marcas = ",".join("?" * len(data.empleados))
        cur = conn.execute(
            f"""UPDATE empleados SET perfil_acceso_id = ?
                 WHERE id IN ({marcas}) AND activo = 1 AND perfil_acceso_id IS NULL""",
            [data.perfil_acceso_id, *data.empleados],
        )
        return {"ok": True, "asignados": cur.rowcount,
                "sin_tocar": len(data.empleados) - cur.rowcount}


class AplicarCargoIn(BaseModel):
    cargo_id: int | None          # null = los que no tienen cargo
    perfil_acceso_id: int
    pisar: bool = False


@router.get("/por-cargo")
def por_cargo(_user=Depends(require_permiso("accesos", "ver"))):
    """
    Cuánta gente hay por cargo y qué perfil tiene hoy.

    Es la foto para el arranque. Al implementar esto nadie tiene perfil y son
    cientos de personas: asignarlas de a una no es una opción, y el cargo es la
    forma en que el usuario ya piensa quién entra a dónde.

    Ojo con lo que esto NO es: no guarda ninguna relación cargo → perfil. El
    cargo se usa para elegir a quién, y después el perfil es de cada persona.
    Cambiarle el cargo a alguien no le cambia las puertas, que es justo lo que
    se quiso evitar cuando el cargo daba acceso: eso lo puede hacer quien edita
    empleados, sin permiso de accesos.
    """
    with db_session() as conn:
        filas = conn.execute(
            """SELECT c.id AS cargo_id, c.nombre AS cargo,
                      COUNT(*)                                        AS total,
                      SUM(e.perfil_acceso_id IS NULL)                 AS sin_perfil
                 FROM empleados e
                 LEFT JOIN cargos c ON c.id = e.cargo_id
                WHERE e.activo = 1
             GROUP BY c.id, c.nombre
             ORDER BY (c.id IS NULL), c.nombre"""
        ).fetchall()

        # Qué perfiles ya tiene la gente de cada cargo. Importa antes de asignar
        # en masa: si el cargo ya tiene gente con perfil, hay una decisión previa
        # ahí y conviene verla antes de pisarla.
        actuales: dict = {}
        for r in conn.execute(
            """SELECT e.cargo_id, p.id AS perfil_id, p.nombre AS perfil,
                      COUNT(*) AS cuantos
                 FROM empleados e
                 JOIN perfiles_acceso p ON p.id = e.perfil_acceso_id
                WHERE e.activo = 1
             GROUP BY e.cargo_id, p.id, p.nombre
             ORDER BY COUNT(*) DESC"""
        ):
            actuales.setdefault(r["cargo_id"], []).append(
                {"id": r["perfil_id"], "nombre": r["perfil"], "cuantos": r["cuantos"]})

        # Con excepciones: cambiarles el perfil no las borra, pero puede dejarlas
        # sin efecto. Avisarlo antes es más útil que descubrirlo después.
        con_exc = {
            r["cargo_id"]: r["cuantos"] for r in conn.execute(
                """SELECT e.cargo_id, COUNT(DISTINCT e.id) AS cuantos
                     FROM empleados e
                     JOIN accesos_excepciones x ON x.empleado_id = e.id
                    WHERE e.activo = 1
                 GROUP BY e.cargo_id"""
            )
        }

        perfiles = [
            dict(r) for r in conn.execute(
                """SELECT id, nombre FROM perfiles_acceso
                    WHERE activo = 1 ORDER BY orden, nombre"""
            )
        ]

    cargos = []
    for f in filas:
        d = dict(f)
        d["con_perfil"] = d["total"] - d["sin_perfil"]
        d["perfiles_actuales"] = actuales.get(d["cargo_id"], [])
        d["con_excepciones"] = con_exc.get(d["cargo_id"], 0)
        cargos.append(d)

    return {"cargos": cargos, "perfiles": perfiles,
            "activos": sum(c["total"] for c in cargos),
            "sin_perfil": sum(c["sin_perfil"] for c in cargos)}


@router.post("/por-cargo/aplicar")
def aplicar_cargo(data: AplicarCargoIn,
                  _user=Depends(require_permiso("accesos", "asignar"))):
    """
    Le pone un perfil a todos los activos de un cargo.

    Solo asigna: nunca deja a nadie sin perfil. Quitarlo en masa borraría las
    excepciones de cada uno —una excepción modifica un perfil, sin perfil no
    tiene qué modificar— y eso necesita permiso de excepciones. Un borrado así,
    en masa y con un solo clic, no tiene por qué existir: si hay que sacarle el
    acceso a alguien, se hace en su legajo y se ve a quién.

    Por defecto no toca a quien ya tiene perfil: esa persona es una decisión
    tomada. Con `pisar` sí, porque el caso real existe —asignar el perfil
    equivocado a un cargo entero y tener que corregirlo— y sin eso el único
    camino serían 30 legajos de a uno.
    """
    with db_session() as conn:
        if not conn.execute("SELECT 1 FROM perfiles_acceso WHERE id=? AND activo=1",
                            (data.perfil_acceso_id,)).fetchone():
            raise HTTPException(400, "Ese perfil no existe o está desactivado")
        if data.cargo_id is not None and not conn.execute(
                "SELECT 1 FROM cargos WHERE id=?", (data.cargo_id,)).fetchone():
            raise HTTPException(400, "Ese cargo no existe")

        donde = "e.cargo_id IS NULL" if data.cargo_id is None else "e.cargo_id = ?"
        args = [] if data.cargo_id is None else [data.cargo_id]

        # A quiénes le va a cambiar el perfil que ya tenían: es el dato que hay
        # que poder mostrar después, porque es lo que no se puede deshacer solo.
        pisados = conn.execute(
            f"""SELECT COUNT(*) FROM empleados e
                 WHERE e.activo = 1 AND {donde}
                   AND e.perfil_acceso_id IS NOT NULL
                   AND e.perfil_acceso_id <> ?""",
            [*args, data.perfil_acceso_id],
        ).fetchone()[0]

        # Las excepciones no se borran, pero un perfil nuevo puede dejarlas sin
        # efecto: "agregar oficina" no hace nada si el perfil nuevo ya la da.
        con_exc = conn.execute(
            f"""SELECT COUNT(DISTINCT e.id) FROM empleados e
                 JOIN accesos_excepciones x ON x.empleado_id = e.id
                WHERE e.activo = 1 AND {donde}""", args
        ).fetchone()[0]

        filtro = "" if data.pisar else " AND e.perfil_acceso_id IS NULL"
        cur = conn.execute(
            f"""UPDATE empleados SET perfil_acceso_id = ?
                 WHERE id IN (SELECT e.id FROM empleados e
                               WHERE e.activo = 1 AND {donde}{filtro})""",
            [data.perfil_acceso_id, *args],
        )
        total = conn.execute(
            f"SELECT COUNT(*) FROM empleados e WHERE e.activo = 1 AND {donde}", args
        ).fetchone()[0]

    return {"ok": True, "asignados": cur.rowcount,
            "sin_tocar": total - cur.rowcount,
            "pisados": pisados if data.pisar else 0,
            "con_excepciones": con_exc}


class ImportarNivelesIn(BaseModel):
    empleados: list[int]


def _leer_niveles_del_maestro():
    """
    Lee el equipo de asistencia y cruza con los legajos lo que él sabe y el
    sistema no: el nivel de administración y el nombre que muestra en pantalla.

    Del equipo de asistencia y no de las puertas, a propósito: el maestro guarda
    24 caracteres y las puertas 8. Adoptar el de una puerta congelaría en el
    legajo un nombre que ya venía cortado.
    """
    from sync.lectores import leer_cargados

    with db_session() as conn:
        maestro = conn.execute(
            """SELECT id, nombre, ip, puerto, password, timeout, protocolo
                 FROM dispositivos
                WHERE activo=1 AND cuenta_asistencia=1 AND protocolo='pull'
                  AND ip IS NOT NULL
             ORDER BY orden, id LIMIT 1""").fetchone()
        if not maestro:
            return None, None, {}
        empleados = {
            str(r["user_id"]).strip(): dict(r) for r in conn.execute(
                """SELECT id, user_id, nombre, apellido, activo, nivel_lector,
                          nombre_lector
                     FROM empleados WHERE user_id IS NOT NULL""")
        }
    return dict(maestro), leer_cargados(dict(maestro)), empleados


@router.get("/niveles-lector")
def niveles_lector(_user=Depends(require_permiso("accesos", "ver"))):
    """
    Qué nivel tiene cada uno en el equipo de fichaje, contra lo que dice su legajo.

    Existe para el momento de adopción. La columna del legajo arranca en cero
    para todos, y ese cero no significa "no administra": significa que nadie lo
    decidió todavía. Si el sistema empezara a escribir desde ahí, le sacaría el
    permiso a todos los que hoy lo tienen. Así que primero se trae lo que el
    equipo ya tiene, se mira, y recién después el sistema pasa a mandar.

    Solo lectura.
    """
    maestro, lectura, empleados = _leer_niveles_del_maestro()
    if maestro is None:
        return {"ok": False, "aviso": "No hay ningún equipo de asistencia cargado."}
    if not lectura["ok"]:
        return {"ok": False, "equipo": maestro["nombre"], "error": lectura["error"]}

    filas, ajenos = [], []
    for u in lectura["usuarios"]:
        nivel = u.get("privilegio") or 0
        emp = empleados.get(u["user_id"])
        if emp is None:
            if nivel:
                ajenos.append({"user_id": u["user_id"], "nombre": u["nombre"],
                               "nivel": nivel})
            continue
        nombre_equipo = (u.get("nombre") or "").strip()
        nombre_legajo = (emp["nombre_lector"] or "").strip()
        difiere_nivel = nivel != emp["nivel_lector"]
        # Vacío en el legajo significa "no opinamos", así que traer el del equipo
        # es exactamente lo que esta pantalla sirve para hacer.
        difiere_nombre = bool(nombre_equipo) and nombre_equipo != nombre_legajo
        if not difiere_nivel and not difiere_nombre:
            continue
        filas.append({
            "empleado_id": emp["id"], "user_id": u["user_id"],
            "nombre": f"{emp['apellido']}, {emp['nombre']}".strip(", "),
            "activo": bool(emp["activo"]),
            "en_el_equipo": nivel, "en_el_legajo": emp["nivel_lector"],
            "conocido": nivel in NIVELES_LECTOR,
            "difiere_nivel": difiere_nivel,
            "nombre_equipo": nombre_equipo, "nombre_legajo": nombre_legajo,
            "difiere_nombre": difiere_nombre,
        })
    filas.sort(key=lambda f: (-f["en_el_equipo"], f["nombre"]))
    return {"ok": True, "equipo": maestro["nombre"], "diferencias": filas,
            "ajenos": ajenos, "largo_nombre": 24,
            "iguales": len(lectura["usuarios"]) - len(filas) - len(ajenos)}


@router.post("/niveles-lector/importar")
def importar_niveles(data: ImportarNivelesIn,
                     _user=Depends(require_permiso("accesos", "editar"))):
    """
    Copia al legajo el nivel que el equipo tiene, para los que se elijan.

    Es la única vez que el equipo le gana al sistema, y es a mano: adoptar en
    bloque una lista de permisos que nadie revisó hace años es la forma de
    heredar exactamente los que habría que sacar.
    """
    if not data.empleados:
        raise HTTPException(400, "No viene ningún empleado")
    maestro, lectura, empleados = _leer_niveles_del_maestro()
    if maestro is None or not lectura["ok"]:
        raise HTTPException(400, "No se pudo leer el equipo de asistencia")

    por_id = {e["id"]: e for e in empleados.values()}
    niveles = {u["user_id"]: (u.get("privilegio") or 0) for u in lectura["usuarios"]}
    cambiados = 0
    with db_session() as conn:
        for eid in data.empleados:
            emp = por_id.get(eid)
            if emp is None:
                continue
            nivel = niveles.get(str(emp["user_id"]).strip())
            # Un nivel que no está entre los que se usan no se copia: entraría al
            # legajo un valor que después nadie puede elegir ni corregir.
            if nivel is None or nivel not in NIVELES_LECTOR:
                continue
            conn.execute("UPDATE empleados SET nivel_lector=? WHERE id=?", (nivel, eid))
            cambiados += 1
    return {"ok": True, "importados": cambiados,
            "sin_tocar": len(data.empleados) - cambiados}


@router.post("/nombres-lector/importar")
def importar_nombres(data: ImportarNivelesIn,
                     _user=Depends(require_permiso("accesos", "asignar"))):
    """
    Copia al legajo el nombre que el equipo de asistencia muestra en pantalla.

    Va aparte de importar los niveles aunque salga de la misma lectura, y no por
    comodidad: un nivel es un permiso. Traer nombres es inofensivo y uno lo
    quiere para todos; traer niveles le da a alguien la posibilidad de enrolar
    gente. Juntarlos en un botón haría que la decisión fácil arrastre la otra.

    Pide `asignar`, el mismo permiso que escribir ese nombre a mano.
    """
    if not data.empleados:
        raise HTTPException(400, "No viene ningún empleado")
    maestro, lectura, empleados = _leer_niveles_del_maestro()
    if maestro is None or not lectura["ok"]:
        raise HTTPException(400, "No se pudo leer el equipo de asistencia")

    por_id = {e["id"]: e for e in empleados.values()}
    nombres = {u["user_id"]: (u.get("nombre") or "").strip()
               for u in lectura["usuarios"]}
    cambiados = 0
    with db_session() as conn:
        for eid in data.empleados:
            emp = por_id.get(eid)
            if emp is None:
                continue
            nombre = nombres.get(str(emp["user_id"]).strip())
            # Un nombre vacío en el equipo no se copia: dejaría el legajo igual
            # que antes pero pareciendo una decisión tomada.
            if not nombre:
                continue
            conn.execute("UPDATE empleados SET nombre_lector=? WHERE id=?",
                         (nombre[:24], eid))
            cambiados += 1
    return {"ok": True, "importados": cambiados,
            "sin_tocar": len(data.empleados) - cambiados}


class CargarEnPuertaIn(BaseModel):
    dispositivo_id: int
    user_id: str


def _registrar_operacion(conn, **datos):
    """Deja constancia de lo que se le escribió a un equipo. Lo que no se registra
    no se puede auditar, y auditar es la mitad del sentido de este módulo."""
    conn.execute(
        """INSERT INTO accesos_operaciones
             (dispositivo_id, equipo, user_id, empleado_id, nombre_equipo,
              accion, motivo, resultado, detalle, respaldo, usuario_id)
           VALUES (:dispositivo_id, :equipo, :user_id, :empleado_id, :nombre_equipo,
                   :accion, :motivo, :resultado, :detalle, :respaldo, :usuario_id)""",
        {"dispositivo_id": None, "equipo": None, "user_id": None, "empleado_id": None,
         "nombre_equipo": None, "accion": None, "motivo": None, "resultado": None,
         "detalle": None, "respaldo": None, "usuario_id": None, **datos})


@router.post("/cargar")
def cargar_en_puerta(data: CargarEnPuertaIn,
                     usuario=Depends(require_permiso("accesos", "aplicar"))):
    """
    Carga a una persona en una puerta, copiándole la huella del equipo de fichaje.

    Es la mitad que faltaba: hasta ahora el plan decía a quién había que cargar y
    no había forma de hacerlo desde el sistema.

    Solo carga a quien le corresponde esa puerta según su perfil. No es una
    restricción técnica —el equipo aceptaría a cualquiera— sino la que mantiene
    el sentido de todo esto: si se pudiera cargar a alguien salteando la
    política, el plan dejaría de describir la realidad y volveríamos a tener dos
    fuentes de verdad, que es el problema que vinimos a resolver.

    Sin huella en el maestro no se carga, y se dice por qué. Cargar a alguien sin
    huella lo deja figurando en la lista del equipo sin poder abrir: parece
    hecho y no lo está, y nadie vuelve a mirar algo que ya figura resuelto.
    """
    from sync.escritura import cargar_en_puerta as escribir

    numero = str(data.user_id).strip()
    with db_session() as conn:
        puerta = conn.execute(
            """SELECT id, nombre, ip, puerto, password, timeout, protocolo,
                      es_acceso, cuenta_asistencia
                 FROM dispositivos WHERE id = ? AND activo = 1""",
            (data.dispositivo_id,)).fetchone()
        if not puerta:
            raise HTTPException(404, "Esa puerta no existe o está desactivada")
        if not puerta["es_acceso"]:
            raise HTTPException(400, f"{puerta['nombre']} no es una puerta.")
        if puerta["protocolo"] != "pull":
            raise HTTPException(400, f"{puerta['nombre']} es un equipo push: no "
                                     f"atiende llamadas.")
        maestro = conn.execute(
            """SELECT id, nombre, ip, puerto, password, timeout, protocolo
                 FROM dispositivos
                WHERE activo=1 AND cuenta_asistencia=1 AND protocolo='pull'
                  AND ip IS NOT NULL ORDER BY orden, id LIMIT 1""").fetchone()
        if not maestro:
            raise HTTPException(400, "No hay equipo de fichaje cargado: de ahí "
                                     "sale la huella que se copia.")

        emp = conn.execute(
            """SELECT id, nombre, apellido, activo, nombre_lector
                 FROM empleados WHERE TRIM(user_id) = ?""", (numero,)).fetchone()
        if not emp:
            raise HTTPException(404, f"El {numero} no existe en el sistema.")
        if not emp["activo"]:
            raise HTTPException(400, f"{emp['apellido']}, {emp['nombre']} está dado "
                                     f"de baja. El sistema no carga egresados.")
        # Que le corresponda esa puerta: es lo que mantiene al plan describiendo
        # la realidad en vez de ser una sugerencia.
        if data.dispositivo_id not in set(puertas_de(conn, emp["id"])["puertas"]):
            raise HTTPException(
                400, f"A {emp['apellido']}, {emp['nombre']} no le corresponde "
                     f"{puerta['nombre']} según su perfil. Cambiale el perfil o "
                     f"ponele una excepción, y después aplicá.")
        puerta, maestro, emp = dict(puerta), dict(maestro), dict(emp)

    r = escribir(puerta, maestro, numero, nombre=emp["nombre_lector"])

    with db_session() as conn:
        _registrar_operacion(
            conn, dispositivo_id=puerta["id"], equipo=puerta["nombre"],
            user_id=numero, empleado_id=emp["id"],
            nombre_equipo=r.get("nombre_escrito"), accion="cargar",
            motivo=None, resultado="cargado" if r["ok"] else "falló",
            detalle=r.get("error"), respaldo=None,
            usuario_id=int(usuario.get("sub") or 0) or None)

    if not r["ok"]:
        raise HTTPException(400, f"No se pudo cargar a {emp['apellido']}, "
                                 f"{emp['nombre']} en {puerta['nombre']}: "
                                 f"{r['error']}")
    return {"ok": True, "equipo": puerta["nombre"],
            "empleado": f"{emp['apellido']}, {emp['nombre']}".strip(", "),
            "huellas": r["huellas"], "grupo": r["grupo"],
            "nombre_escrito": r["nombre_escrito"], "otros_intactos": r["otros"]}


@router.get("/sin-perfil")
def sin_perfil(_user=Depends(require_permiso("accesos", "ver"))):
    """
    Los activos sin perfil propio: la lista de pendientes de asignar.

    El cargo no les da acceso solo —propone— así que todos estos hoy no abren
    ninguna puerta. Puede estar bien (administración no necesita abrir nada) o
    puede ser alguien que entró y a quien nadie le asignó el acceso todavía. El
    sistema no puede distinguirlos, pero sí ponerlos en una lista en vez de que
    aparezcan el día que la persona se queda afuera.

    """
    with db_session() as conn:
        filas = conn.execute(
            """SELECT e.id, e.user_id, e.nombre, e.apellido, e.fecha_ingreso,
                      c.nombre AS cargo, c.id AS cargo_id
                 FROM empleados e
                 LEFT JOIN cargos c ON c.id = e.cargo_id
                WHERE e.activo = 1 AND e.perfil_acceso_id IS NULL
             ORDER BY e.apellido, e.nombre"""
        ).fetchall()
    return [dict(f) for f in filas]
