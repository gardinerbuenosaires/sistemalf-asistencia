"""ABM de los lectores biométricos del local.

Hasta acá el lector era un puñado de claves en `configuracion` y alcanzaba,
porque había uno. Ahora hay un maestro de asistencia y varios lectores de
puerta, así que cada uno es una fila.

La prueba de conexión es de solo lectura: se conecta, pregunta quién es y
completa los datos de identidad del equipo. No escribe nada en el lector. Eso
evita el error más molesto de cargar equipos a mano, que es tipear mal la IP y
enterarse tres días después porque no llegan fichajes.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from auth.core import require_permiso
from db.database import db_session

router = APIRouter(prefix="/api/dispositivos", tags=["dispositivos"])

CAMPOS = """id, nombre, ubicacion, protocolo, ip, puerto, password, timeout,
            transporte, numero_serie, modelo, firmware, plataforma,
            algoritmo_huella, cuenta_asistencia, es_acceso, activo, orden,
            visto_en, creado_en, modificado_en"""


class DispositivoIn(BaseModel):
    nombre: str
    ubicacion: str | None = None
    protocolo: str = "pull"
    ip: str | None = None
    puerto: int = 4370
    password: int = 0
    timeout: int = 10
    numero_serie: str | None = None
    cuenta_asistencia: bool = False
    es_acceso: bool = True
    activo: bool = True
    orden: int = 0

    @field_validator("nombre")
    @classmethod
    def _nombre(cls, v):
        if not v or not v.strip():
            raise ValueError("El nombre no puede estar vacío")
        return v.strip()

    @field_validator("protocolo")
    @classmethod
    def _protocolo(cls, v):
        if v not in ("pull", "push"):
            raise ValueError("El protocolo tiene que ser 'pull' o 'push'")
        return v

    @field_validator("puerto")
    @classmethod
    def _puerto(cls, v):
        if not 1 <= v <= 65535:
            raise ValueError("Puerto fuera de rango")
        return v

    @field_validator("timeout")
    @classmethod
    def _timeout(cls, v):
        if not 1 <= v <= 120:
            raise ValueError("El tiempo de espera tiene que estar entre 1 y 120 segundos")
        return v


def _validar_direccion(data: DispositivoIn):
    """
    Un equipo pull se direcciona por IP; uno push se identifica por su número
    de serie, y puede no tener IP fija. Sin el dato que corresponde, el equipo
    no es alcanzable de ninguna forma.
    """
    if data.protocolo == "pull" and not (data.ip or "").strip():
        raise HTTPException(400, "Un equipo pull necesita IP: es como se lo ubica")
    if data.protocolo == "push" and not (data.numero_serie or "").strip():
        raise HTTPException(
            400, "Un equipo push necesita número de serie: es como se identifica al llamar"
        )


def _traer(conn, did):
    fila = conn.execute(f"SELECT {CAMPOS} FROM dispositivos WHERE id=?", (did,)).fetchone()
    if not fila:
        raise HTTPException(404, "Dispositivo no encontrado")
    return dict(fila)


def _limpio(valor):
    valor = (valor or "").strip() if isinstance(valor, str) else valor
    return valor or None


@router.get("")
def listar(_user=Depends(require_permiso("dispositivos", "ver"))):
    with db_session() as conn:
        filas = conn.execute(
            f"SELECT {CAMPOS} FROM dispositivos ORDER BY orden, id"
        ).fetchall()
    return [dict(f) for f in filas]


@router.post("", status_code=201)
def crear(data: DispositivoIn, _user=Depends(require_permiso("dispositivos", "editar"))):
    _validar_direccion(data)
    with db_session() as conn:
        try:
            cur = conn.execute(
                """INSERT INTO dispositivos
                       (nombre, ubicacion, protocolo, ip, puerto, password, timeout,
                        numero_serie, cuenta_asistencia, es_acceso, activo, orden)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (data.nombre, _limpio(data.ubicacion), data.protocolo, _limpio(data.ip),
                 data.puerto, data.password, data.timeout, _limpio(data.numero_serie),
                 int(data.cuenta_asistencia), int(data.es_acceso), int(data.activo), data.orden),
            )
            return _traer(conn, cur.lastrowid)
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(409, "Ya hay un equipo con esa dirección o ese número de serie")


@router.put("/{did}")
def actualizar(did: int, data: DispositivoIn,
               _user=Depends(require_permiso("dispositivos", "editar"))):
    _validar_direccion(data)
    with db_session() as conn:
        antes = _traer(conn, did)
        _verificar_queda_uno_de_asistencia(conn, did, sigue=bool(data.cuenta_asistencia and data.activo))
        # Destildar "abre una puerta" la saca de la matriz y del plan, pero los
        # perfiles la siguen conteniendo: el sistema dejaria de administrarla en
        # silencio y quien este cargado ahi se queda para siempre, egresados
        # incluidos. Es deshacer politica de acceso desde la pantalla de
        # equipos, asi que se frena igual que el borrado.
        if antes["es_acceso"] and not data.es_acceso:
            _verificar_sin_referencias(conn, did, antes["nombre"],
                                       accion="sacarle «Abre una puerta» a")
        try:
            conn.execute(
                """UPDATE dispositivos
                      SET nombre=?, ubicacion=?, protocolo=?, ip=?, puerto=?, password=?,
                          timeout=?, numero_serie=?, cuenta_asistencia=?, es_acceso=?,
                          activo=?, orden=?, modificado_en=datetime('now','localtime')
                    WHERE id=?""",
                (data.nombre, _limpio(data.ubicacion), data.protocolo, _limpio(data.ip),
                 data.puerto, data.password, data.timeout, _limpio(data.numero_serie),
                 int(data.cuenta_asistencia), int(data.es_acceso), int(data.activo),
                 data.orden, did),
            )
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(409, "Ya hay un equipo con esa dirección o ese número de serie")
        return _traer(conn, did)


@router.delete("/{did}")
def eliminar(did: int, _user=Depends(require_permiso("dispositivos", "eliminar"))):
    with db_session() as conn:
        d = _traer(conn, did)
        _verificar_queda_uno_de_asistencia(conn, did, sigue=False)
        _verificar_sin_referencias(conn, did, d["nombre"])
        conn.execute("DELETE FROM dispositivos WHERE id=?", (did,))
    return {"ok": True}


def _verificar_sin_referencias(conn, did, nombre, accion="borrar"):
    """
    Frena lo que dejaría a la política de accesos apuntando al vacío.

    Vale para borrar la puerta y para sacarle «abre una puerta»: las dos cosas
    la sacan de la política sin tocar los perfiles, que la siguen conteniendo.
    Hacerlo en silencio cambia qué abre un perfil sin que nadie lo decida, y en
    el caso del borrado además rompe la clave foránea de las excepciones, que
    sale como un error 500 sin explicación. Mejor decir qué la está usando.
    """
    en_perfiles = conn.execute(
        """SELECT p.nombre FROM perfiles_dispositivos pd
             JOIN perfiles_acceso p ON p.id = pd.perfil_id
            WHERE pd.dispositivo_id = ? ORDER BY p.nombre""",
        (did,),
    ).fetchall()
    excepciones = conn.execute(
        "SELECT COUNT(*) FROM accesos_excepciones WHERE dispositivo_id=?", (did,)
    ).fetchone()[0]

    if not en_perfiles and not excepciones:
        return

    partes = []
    if en_perfiles:
        nombres = ", ".join(f"«{r['nombre']}»" for r in en_perfiles)
        partes.append(f"está en {len(en_perfiles)} perfil(es): {nombres}")
    if excepciones:
        partes.append(f"hay {excepciones} excepción(es) de empleados sobre ella")
    raise HTTPException(
        409,
        f"No se puede {accion} «{nombre}»: " + " y ".join(partes) +
        ". Sacala de ahí primero, así queda claro a quién le cambia el acceso.",
    )


def _verificar_queda_uno_de_asistencia(conn, did, sigue: bool):
    """
    Impide quedarse sin ningún equipo que alimente la asistencia.

    Sin eso el sync no tiene a quién preguntarle y el restaurante deja de
    registrar fichajes, que es de lo que cuelgan planilla, premios y
    liquidación. El error se vería recién cuando alguien note que faltan
    fichadas, o sea tarde.
    """
    if sigue:
        return
    otros = conn.execute(
        """SELECT COUNT(*) FROM dispositivos
            WHERE id <> ? AND activo = 1 AND cuenta_asistencia = 1""",
        (did,),
    ).fetchone()[0]
    era_de_asistencia = conn.execute(
        "SELECT cuenta_asistencia, activo FROM dispositivos WHERE id=?", (did,)
    ).fetchone()
    if era_de_asistencia and era_de_asistencia["cuenta_asistencia"] and era_de_asistencia["activo"] and not otros:
        raise HTTPException(
            409,
            "Es el único equipo que alimenta la asistencia. Marcá otro antes, "
            "o el sistema deja de recibir fichajes.",
        )


@router.get("/revision/todos")
def revision(_user=Depends(require_permiso("dispositivos", "ver"))):
    """
    Revisa todos los lectores de una y resume qué hay para corregir.

    Es la rutina de verificación completa en una pantalla, en vez de abrir
    equipo por equipo. Solo lectura. Un equipo que no contesta se informa como
    tal y no frena a los demás.

    Solo entran los equipos activos a los que se les puede preguntar: un equipo
    push no atiende llamadas, así que no tiene sentido incluirlo acá.
    """
    from sync.lectores import leer_padrones, comparar_con_empleados

    with db_session() as conn:
        equipos = [
            dict(f) for f in conn.execute(
                f"""SELECT {CAMPOS} FROM dispositivos
                     WHERE activo = 1 AND protocolo = 'pull' AND ip IS NOT NULL
                  ORDER BY orden, id"""
            )
        ]
        empleados = {
            str(r["user_id"]).strip(): dict(r)
            for r in conn.execute(
                """SELECT id, user_id, nombre, apellido, activo, tipo, fecha_egreso
                     FROM empleados WHERE user_id IS NOT NULL"""
            )
        }

    lecturas = leer_padrones(equipos)
    salida, total = [], {"equipos": len(equipos), "sin_responder": 0,
                         "de_baja": 0, "desconocidos": 0, "nombre_distinto": 0}

    for d in equipos:
        lectura = lecturas.get(d["id"], {"ok": False, "error": "sin resultado", "usuarios": []})
        fila = {"id": d["id"], "nombre": d["nombre"], "ubicacion": d["ubicacion"],
                "ip": d["ip"], "cuenta_asistencia": d["cuenta_asistencia"],
                "es_acceso": d["es_acceso"], "ok": lectura["ok"],
                "error": lectura.get("error"), "transporte": lectura.get("transporte")}
        if lectura["ok"]:
            comparacion = comparar_con_empleados(lectura["usuarios"], empleados)
            fila["resumen"] = comparacion["resumen"]
            # Solo lo que hay que mirar: el resto es ruido en esta pantalla.
            fila["problemas"] = [f for f in comparacion["filas"]
                                 if f["estado"] != "ok" or f["nombre_distinto"]]
            for clave in ("de_baja", "desconocidos", "nombre_distinto"):
                total[clave] += comparacion["resumen"][clave]
        else:
            total["sin_responder"] += 1
        salida.append(fila)

    if any(f["ok"] for f in salida):
        with db_session() as conn:
            for f in salida:
                if f["ok"]:
                    conn.execute(
                        """UPDATE dispositivos
                              SET transporte=?, visto_en=datetime('now','localtime')
                            WHERE id=?""",
                        (f["transporte"], f["id"]),
                    )

    return {"total": total, "equipos": salida}


@router.post("/{did}/probar")
def probar(did: int, _user=Depends(require_permiso("dispositivos", "editar"))):
    """
    Se conecta al equipo, le pregunta quién es y guarda esos datos.

    Solo lectura: no modifica nada en el lector. Prueba TCP y, si no contesta,
    UDP, porque los equipos viejos a veces solo hablan UDP y probarlos solo por
    TCP los daría por muertos sin serlo.
    """
    with db_session() as conn:
        d = _traer(conn, did)

    if d["protocolo"] != "pull":
        raise HTTPException(
            400,
            "A un equipo push no se lo puede probar desde acá: no atiende llamadas, "
            "es él quien llama al sistema.",
        )
    if not d["ip"]:
        raise HTTPException(400, "El equipo no tiene IP cargada")

    from zk import ZK

    info, transporte, error = None, None, None
    for udp in (False, True):
        conexion = None
        try:
            conexion = ZK(d["ip"], port=int(d["puerto"]), timeout=int(d["timeout"]),
                          password=int(d["password"]), force_udp=udp,
                          ommit_ping=True, encoding="latin-1").connect()
            info = {
                "modelo":           _leer(conexion.get_device_name),
                "plataforma":       _leer(conexion.get_platform),
                "firmware":         _leer(conexion.get_firmware_version),
                "numero_serie":     _leer(conexion.get_serialnumber),
                "algoritmo_huella": _leer(conexion.get_fp_version),
            }
            transporte = "udp" if udp else "tcp"
            break
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        finally:
            if conexion:
                try:
                    conexion.disconnect()
                except Exception:
                    pass

    if info is None:
        return {"ok": False, "error": error,
                "detalle": "Si dice «Unauthenticated», el equipo tiene contraseña de "
                           "comunicación y hay que cargarla acá."}

    with db_session() as conn:
        try:
            conn.execute(
                """UPDATE dispositivos
                      SET modelo=?, plataforma=?, firmware=?, numero_serie=?,
                          algoritmo_huella=?, transporte=?,
                          visto_en=?, modificado_en=datetime('now','localtime')
                    WHERE id=?""",
                (info["modelo"], info["plataforma"], info["firmware"],
                 info["numero_serie"], info["algoritmo_huella"], transporte,
                 datetime.now().strftime("%Y-%m-%d %H:%M:%S"), did),
            )
        except Exception:
            # El número de serie es único: si otro equipo ya lo tiene, lo más
            # probable es que sea la misma máquina cargada dos veces.
            raise HTTPException(
                409,
                f"El número de serie {info['numero_serie']} ya está cargado en otro "
                f"equipo. ¿Está el mismo lector dado de alta dos veces?",
            )
        return {"ok": True, "transporte": transporte, **_traer(conn, did)}


@router.get("/{did}/padron")
def padron(did: int, _user=Depends(require_permiso("dispositivos", "ver"))):
    """
    Qué tiene cargado este lector, cruzado contra los empleados del sistema.

    Es la pantalla que reemplaza la verificación a ojo: en vez de comparar dos
    listas en pantallas distintas, el sistema marca solo a los que hay que
    mirar. Solo lectura: no toca el equipo.
    """
    from sync.lectores import leer_padron, comparar_con_empleados

    with db_session() as conn:
        d = _traer(conn, did)

    # Con las huellas: es la mirada en profundidad a UN equipo, y sin ellas la
    # lista dice quien esta cargado pero no quien puede abrir. "Revisar todos" no
    # las pide, porque son bastantes mas datos por equipo y ahi se leen todos.
    lectura = leer_padron(d, con_huellas=True)
    if not lectura["ok"]:
        return {"ok": False, "error": lectura["error"], "dispositivo": d}

    with db_session() as conn:
        empleados = {
            str(r["user_id"]).strip(): dict(r)
            for r in conn.execute(
                """SELECT id, user_id, nombre, apellido, activo, tipo, fecha_egreso
                     FROM empleados WHERE user_id IS NOT NULL"""
            )
        }
        # Dejar constancia de que el equipo contestó, aunque no se haya probado.
        conn.execute(
            """UPDATE dispositivos SET transporte=?, visto_en=datetime('now','localtime')
                WHERE id=?""",
            (lectura["transporte"], did),
        )

    return {"ok": True, "transporte": lectura["transporte"], "dispositivo": d,
            **comparar_con_empleados(lectura["usuarios"], empleados)}


def _leer(funcion):
    """Un dato que el equipo no sepa contestar no tiene que tirar abajo la prueba."""
    try:
        valor = funcion()
        return str(valor) if valor not in (None, "") else None
    except Exception:
        return None
