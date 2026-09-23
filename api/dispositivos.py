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
        _traer(conn, did)
        _verificar_queda_uno_de_asistencia(conn, did, sigue=bool(data.cuenta_asistencia and data.activo))
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
        _traer(conn, did)
        _verificar_queda_uno_de_asistencia(conn, did, sigue=False)
        conn.execute("DELETE FROM dispositivos WHERE id=?", (did,))
    return {"ok": True}


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


def _leer(funcion):
    """Un dato que el equipo no sepa contestar no tiene que tirar abajo la prueba."""
    try:
        valor = funcion()
        return str(valor) if valor not in (None, "") else None
    except Exception:
        return None
