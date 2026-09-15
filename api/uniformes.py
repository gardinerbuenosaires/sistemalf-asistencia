"""Entrega de uniformes y EPP — catálogos.

Tanda 1: rubros, tipos de talle, valores de talle y elementos. Los movimientos
(constancias) y los talles por empleado vienen en las tandas siguientes.

Todo el router está detrás de la bandera `uniformes_activo`: con la bandera en 0
cada ruta responde 404, igual que si el módulo no existiera.
"""

import re
import sqlite3
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from db.database import db_session
from db.uniformes_schema import uniformes_activo, MAX_ITEMS_POR_MOVIMIENTO
from auth.core import require_permiso, tiene_permiso


def modulo_activo():
    """Bandera del módulo. Se aplica a todo el router."""
    with db_session() as conn:
        if not uniformes_activo(conn):
            raise HTTPException(404)


router = APIRouter(tags=["uniformes"], dependencies=[Depends(modulo_activo)])

# Los empleados tipo 'acceso' existen solo para abrir puertas con la huella: no
# son personal y no reciben ropa. Se los excluye siempre, igual que en
# distribucion.py y barmans.py.
EXCLUIR_NO_PERSONAL = "e.tipo != 'acceso'"

ver = require_permiso("uniformes", "ver")
editar = require_permiso("uniformes", "editar")


# ── Modelos ───────────────────────────────────────────────────────────────────

class RubroIn(BaseModel):
    nombre: str | None = None
    meses_alerta: int | None = None
    activo: bool | None = None


class TipoTalleIn(BaseModel):
    nombre: str | None = None
    orden: int | None = None
    activo: bool | None = None


class TalleValorIn(BaseModel):
    valor: str | None = None
    orden: int | None = None
    activo: bool | None = None


class ElementoIn(BaseModel):
    nombre: str | None = None
    tipo_modelo: str | None = None
    marca: str | None = None
    posee_certificado: bool | None = None
    categoria_id: int | None = None
    tipo_talle_id: int | None = None
    activo: bool | None = None


def _texto(valor, campo):
    """Normaliza un texto obligatorio."""
    v = (valor or "").strip()
    if not v:
        raise HTTPException(400, f"{campo} es obligatorio")
    return v


# ══════════════════════════════════════════════════════════════════════════════
# RUBROS  (Ropa de trabajo / EPP)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/api/uniformes/rubros")
def list_rubros(_u=Depends(ver)):
    with db_session() as conn:
        rows = conn.execute(
            """SELECT r.id, r.nombre, r.meses_alerta, r.activo,
                      (SELECT COUNT(*) FROM uniformes_elementos e WHERE e.categoria_id = r.id) AS elementos
               FROM uniformes_categorias r
               ORDER BY r.nombre"""
        ).fetchall()
    return [dict(r) for r in rows]


@router.post("/api/uniformes/rubros", status_code=201)
def create_rubro(data: RubroIn, _u=Depends(editar)):
    nombre = _texto(data.nombre, "El nombre")
    with db_session() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO uniformes_categorias (nombre, meses_alerta) VALUES (?,?)",
                (nombre, data.meses_alerta),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Ya existe un rubro con ese nombre")
        row = conn.execute(
            "SELECT id, nombre, meses_alerta, activo FROM uniformes_categorias WHERE id=?",
            (cur.lastrowid,),
        ).fetchone()
    return dict(row)


@router.patch("/api/uniformes/rubros/{rid}")
def update_rubro(rid: int, data: RubroIn, _u=Depends(editar)):
    with db_session() as conn:
        if not conn.execute("SELECT id FROM uniformes_categorias WHERE id=?", (rid,)).fetchone():
            raise HTTPException(404, "Rubro no encontrado")
        if data.nombre is not None:
            try:
                conn.execute(
                    "UPDATE uniformes_categorias SET nombre=? WHERE id=?",
                    (_texto(data.nombre, "El nombre"), rid),
                )
            except sqlite3.IntegrityError:
                raise HTTPException(409, "Ya existe un rubro con ese nombre")
        # meses_alerta se manda explícitamente en null para decir "no avisar nunca",
        # así que hay que distinguir "vino null" de "no vino".
        if "meses_alerta" in data.model_fields_set:
            if data.meses_alerta is not None and data.meses_alerta < 1:
                raise HTTPException(400, "Los meses de alerta tienen que ser 1 o más")
            conn.execute(
                "UPDATE uniformes_categorias SET meses_alerta=? WHERE id=?",
                (data.meses_alerta, rid),
            )
        if data.activo is not None:
            conn.execute(
                "UPDATE uniformes_categorias SET activo=? WHERE id=?", (int(data.activo), rid)
            )
        row = conn.execute(
            "SELECT id, nombre, meses_alerta, activo FROM uniformes_categorias WHERE id=?", (rid,)
        ).fetchone()
    return dict(row)


@router.delete("/api/uniformes/rubros/{rid}")
def delete_rubro(rid: int, _u=Depends(editar)):
    with db_session() as conn:
        if not conn.execute("SELECT id FROM uniformes_categorias WHERE id=?", (rid,)).fetchone():
            raise HTTPException(404, "Rubro no encontrado")
        en_uso = conn.execute(
            "SELECT COUNT(*) FROM uniformes_elementos WHERE categoria_id=?", (rid,)
        ).fetchone()[0]
        if en_uso:
            raise HTTPException(
                409, f"No se puede eliminar: {en_uso} elemento{'s' if en_uso > 1 else ''} "
                     f"de este rubro. Se puede desactivar."
            )
        conn.execute("DELETE FROM uniformes_categorias WHERE id=?", (rid,))
    return {"ok": True}


# ══════════════════════════════════════════════════════════════════════════════
# TIPOS DE TALLE
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/api/uniformes/tipos-talle")
def list_tipos_talle(_u=Depends(ver)):
    with db_session() as conn:
        rows = conn.execute(
            """SELECT t.id, t.nombre, t.orden, t.activo,
                      (SELECT COUNT(*) FROM uniformes_talle_valores v WHERE v.tipo_talle_id = t.id) AS valores,
                      (SELECT COUNT(*) FROM uniformes_elementos e WHERE e.tipo_talle_id = t.id) AS elementos
               FROM uniformes_tipos_talle t
               ORDER BY t.orden, t.nombre"""
        ).fetchall()
    return [dict(r) for r in rows]


@router.post("/api/uniformes/tipos-talle", status_code=201)
def create_tipo_talle(data: TipoTalleIn, _u=Depends(editar)):
    nombre = _texto(data.nombre, "El nombre")
    with db_session() as conn:
        orden = data.orden
        if orden is None:
            orden = (conn.execute(
                "SELECT COALESCE(MAX(orden), 0) + 1 FROM uniformes_tipos_talle"
            ).fetchone()[0])
        try:
            cur = conn.execute(
                "INSERT INTO uniformes_tipos_talle (nombre, orden) VALUES (?,?)", (nombre, orden)
            )
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Ya existe un tipo de talle con ese nombre")
        row = conn.execute(
            "SELECT id, nombre, orden, activo FROM uniformes_tipos_talle WHERE id=?",
            (cur.lastrowid,),
        ).fetchone()
    return dict(row)


@router.patch("/api/uniformes/tipos-talle/{tid}")
def update_tipo_talle(tid: int, data: TipoTalleIn, _u=Depends(editar)):
    with db_session() as conn:
        if not conn.execute("SELECT id FROM uniformes_tipos_talle WHERE id=?", (tid,)).fetchone():
            raise HTTPException(404, "Tipo de talle no encontrado")
        if data.nombre is not None:
            try:
                conn.execute(
                    "UPDATE uniformes_tipos_talle SET nombre=? WHERE id=?",
                    (_texto(data.nombre, "El nombre"), tid),
                )
            except sqlite3.IntegrityError:
                raise HTTPException(409, "Ya existe un tipo de talle con ese nombre")
        if data.orden is not None:
            conn.execute("UPDATE uniformes_tipos_talle SET orden=? WHERE id=?", (data.orden, tid))
        if data.activo is not None:
            conn.execute(
                "UPDATE uniformes_tipos_talle SET activo=? WHERE id=?", (int(data.activo), tid)
            )
        row = conn.execute(
            "SELECT id, nombre, orden, activo FROM uniformes_tipos_talle WHERE id=?", (tid,)
        ).fetchone()
    return dict(row)


@router.delete("/api/uniformes/tipos-talle/{tid}")
def delete_tipo_talle(tid: int, _u=Depends(editar)):
    with db_session() as conn:
        if not conn.execute("SELECT id FROM uniformes_tipos_talle WHERE id=?", (tid,)).fetchone():
            raise HTTPException(404, "Tipo de talle no encontrado")
        elementos = conn.execute(
            "SELECT COUNT(*) FROM uniformes_elementos WHERE tipo_talle_id=?", (tid,)
        ).fetchone()[0]
        if elementos:
            raise HTTPException(
                409, f"No se puede eliminar: {elementos} elemento"
                     f"{'s lo usan' if elementos > 1 else ' lo usa'}. Se puede desactivar."
            )
        cargados = conn.execute(
            "SELECT COUNT(*) FROM uniformes_talles_empleado WHERE tipo_talle_id=?", (tid,)
        ).fetchone()[0]
        if cargados:
            raise HTTPException(
                409, f"No se puede eliminar: {cargados} empleado"
                     f"{'s tienen' if cargados > 1 else ' tiene'} este talle cargado. "
                     f"Se puede desactivar."
            )
        # Los valores caen por ON DELETE CASCADE.
        conn.execute("DELETE FROM uniformes_tipos_talle WHERE id=?", (tid,))
    return {"ok": True}


# ══════════════════════════════════════════════════════════════════════════════
# VALORES DE TALLE  (la escala de cada tipo: 38/40/42… o S/M/L/XL)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/api/uniformes/tipos-talle/{tid}/valores")
def list_valores(tid: int, _u=Depends(ver)):
    with db_session() as conn:
        if not conn.execute("SELECT id FROM uniformes_tipos_talle WHERE id=?", (tid,)).fetchone():
            raise HTTPException(404, "Tipo de talle no encontrado")
        rows = conn.execute(
            """SELECT id, tipo_talle_id, valor, orden, activo
               FROM uniformes_talle_valores WHERE tipo_talle_id=?
               ORDER BY orden, valor""",
            (tid,),
        ).fetchall()
    return [dict(r) for r in rows]


@router.post("/api/uniformes/tipos-talle/{tid}/valores", status_code=201)
def create_valor(tid: int, data: TalleValorIn, _u=Depends(editar)):
    valor = _texto(data.valor, "El valor")
    with db_session() as conn:
        if not conn.execute("SELECT id FROM uniformes_tipos_talle WHERE id=?", (tid,)).fetchone():
            raise HTTPException(404, "Tipo de talle no encontrado")
        orden = data.orden
        if orden is None:
            orden = conn.execute(
                "SELECT COALESCE(MAX(orden), 0) + 1 FROM uniformes_talle_valores WHERE tipo_talle_id=?",
                (tid,),
            ).fetchone()[0]
        try:
            cur = conn.execute(
                "INSERT INTO uniformes_talle_valores (tipo_talle_id, valor, orden) VALUES (?,?,?)",
                (tid, valor, orden),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Ese valor ya existe en este tipo de talle")
        row = conn.execute(
            "SELECT id, tipo_talle_id, valor, orden, activo FROM uniformes_talle_valores WHERE id=?",
            (cur.lastrowid,),
        ).fetchone()
    return dict(row)


@router.patch("/api/uniformes/talle-valores/{vid}")
def update_valor(vid: int, data: TalleValorIn, _u=Depends(editar)):
    with db_session() as conn:
        if not conn.execute("SELECT id FROM uniformes_talle_valores WHERE id=?", (vid,)).fetchone():
            raise HTTPException(404, "Valor no encontrado")
        if data.valor is not None:
            try:
                conn.execute(
                    "UPDATE uniformes_talle_valores SET valor=? WHERE id=?",
                    (_texto(data.valor, "El valor"), vid),
                )
            except sqlite3.IntegrityError:
                raise HTTPException(409, "Ese valor ya existe en este tipo de talle")
        if data.orden is not None:
            conn.execute("UPDATE uniformes_talle_valores SET orden=? WHERE id=?", (data.orden, vid))
        if data.activo is not None:
            conn.execute(
                "UPDATE uniformes_talle_valores SET activo=? WHERE id=?", (int(data.activo), vid)
            )
        row = conn.execute(
            "SELECT id, tipo_talle_id, valor, orden, activo FROM uniformes_talle_valores WHERE id=?",
            (vid,),
        ).fetchone()
    return dict(row)


@router.delete("/api/uniformes/talle-valores/{vid}")
def delete_valor(vid: int, _u=Depends(editar)):
    with db_session() as conn:
        if not conn.execute("SELECT id FROM uniformes_talle_valores WHERE id=?", (vid,)).fetchone():
            raise HTTPException(404, "Valor no encontrado")
        conn.execute("DELETE FROM uniformes_talle_valores WHERE id=?", (vid,))
    return {"ok": True}


# ══════════════════════════════════════════════════════════════════════════════
# ELEMENTOS  (el catálogo de artículos)
# ══════════════════════════════════════════════════════════════════════════════

_SELECT_ELEMENTO = """
    SELECT e.id, e.nombre, e.tipo_modelo, e.marca, e.posee_certificado,
           e.categoria_id, r.nombre AS categoria_nombre,
           e.tipo_talle_id, t.nombre AS tipo_talle_nombre,
           e.activo,
           (SELECT COUNT(*) FROM uniformes_items i WHERE i.elemento_id = e.id) AS entregado_veces
    FROM uniformes_elementos e
    LEFT JOIN uniformes_categorias  r ON r.id = e.categoria_id
    LEFT JOIN uniformes_tipos_talle t ON t.id = e.tipo_talle_id
"""


def _validar_referencias(conn, categoria_id, tipo_talle_id):
    if categoria_id is not None and not conn.execute(
        "SELECT id FROM uniformes_categorias WHERE id=?", (categoria_id,)
    ).fetchone():
        raise HTTPException(400, "El rubro indicado no existe")
    if tipo_talle_id is not None and not conn.execute(
        "SELECT id FROM uniformes_tipos_talle WHERE id=?", (tipo_talle_id,)
    ).fetchone():
        raise HTTPException(400, "El tipo de talle indicado no existe")


@router.get("/api/uniformes/elementos")
def list_elementos(solo_activos: bool = False, _u=Depends(ver)):
    with db_session() as conn:
        sql = _SELECT_ELEMENTO + (" WHERE e.activo=1" if solo_activos else "")
        rows = conn.execute(sql + " ORDER BY e.activo DESC, e.nombre").fetchall()
    return [dict(r) for r in rows]


@router.post("/api/uniformes/elementos", status_code=201)
def create_elemento(data: ElementoIn, _u=Depends(editar)):
    nombre = _texto(data.nombre, "El nombre")
    with db_session() as conn:
        _validar_referencias(conn, data.categoria_id, data.tipo_talle_id)
        cur = conn.execute(
            """INSERT INTO uniformes_elementos
                   (nombre, tipo_modelo, marca, posee_certificado, categoria_id, tipo_talle_id)
               VALUES (?,?,?,?,?,?)""",
            (nombre,
             (data.tipo_modelo or "").strip() or None,
             (data.marca or "").strip() or None,
             int(bool(data.posee_certificado)),
             data.categoria_id,
             data.tipo_talle_id),
        )
        row = conn.execute(_SELECT_ELEMENTO + " WHERE e.id=?", (cur.lastrowid,)).fetchone()
    return dict(row)


@router.patch("/api/uniformes/elementos/{eid}")
def update_elemento(eid: int, data: ElementoIn, _u=Depends(editar)):
    with db_session() as conn:
        if not conn.execute("SELECT id FROM uniformes_elementos WHERE id=?", (eid,)).fetchone():
            raise HTTPException(404, "Elemento no encontrado")
        _validar_referencias(conn, data.categoria_id, data.tipo_talle_id)

        # Editar el catálogo cambia lo que se entrega de acá en adelante y nunca
        # lo que ya se firmó: cada renglón guardó su propia copia al emitirse.
        if data.nombre is not None:
            conn.execute("UPDATE uniformes_elementos SET nombre=? WHERE id=?",
                         (_texto(data.nombre, "El nombre"), eid))
        for campo in ("tipo_modelo", "marca"):
            if campo in data.model_fields_set:
                valor = (getattr(data, campo) or "").strip() or None
                conn.execute(f"UPDATE uniformes_elementos SET {campo}=? WHERE id=?", (valor, eid))
        if data.posee_certificado is not None:
            conn.execute("UPDATE uniformes_elementos SET posee_certificado=? WHERE id=?",
                         (int(data.posee_certificado), eid))
        if "categoria_id" in data.model_fields_set:
            conn.execute("UPDATE uniformes_elementos SET categoria_id=? WHERE id=?",
                         (data.categoria_id, eid))
        if "tipo_talle_id" in data.model_fields_set:
            conn.execute("UPDATE uniformes_elementos SET tipo_talle_id=? WHERE id=?",
                         (data.tipo_talle_id, eid))
        if data.activo is not None:
            conn.execute("UPDATE uniformes_elementos SET activo=? WHERE id=?",
                         (int(data.activo), eid))
        row = conn.execute(_SELECT_ELEMENTO + " WHERE e.id=?", (eid,)).fetchone()
    return dict(row)


@router.delete("/api/uniformes/elementos/{eid}")
def delete_elemento(eid: int, _u=Depends(editar)):
    """Solo borra elementos que nunca se entregaron — es para corregir un alta
    equivocada. Lo que ya figura en una constancia firmada se desactiva, no se
    borra: el renglón guarda su copia, pero el id sostiene los reportes."""
    with db_session() as conn:
        if not conn.execute("SELECT id FROM uniformes_elementos WHERE id=?", (eid,)).fetchone():
            raise HTTPException(404, "Elemento no encontrado")
        en_uso = conn.execute(
            "SELECT COUNT(*) FROM uniformes_items WHERE elemento_id=?", (eid,)
        ).fetchone()[0]
        if en_uso:
            raise HTTPException(
                409, f"No se puede eliminar: figura en {en_uso} entrega"
                     f"{'s' if en_uso > 1 else ''}. Se puede desactivar."
            )
        conn.execute("DELETE FROM uniformes_elementos WHERE id=?", (eid,))
    return {"ok": True}


# ══════════════════════════════════════════════════════════════════════════════
# TALLES POR EMPLEADO
# ══════════════════════════════════════════════════════════════════════════════
# Se registran aparte del historial de entregas porque para comprar hacen falta
# los talles de TODO el personal, incluida la gente que nunca recibió nada por el
# sistema. El historial solo conoce a los que ya recibieron; no alcanza.


class TalleEmpleadoIn(BaseModel):
    empleado_id: int
    tipo_talle_id: int
    valor: str | None = None   # None o "" borra el talle cargado


@router.get("/api/uniformes/talles")
def grilla_talles(
    cargo_id: int | None = None,
    sin_cargo: bool = False,
    departamento_id: int | None = None,
    incluir_inactivos: bool = False,
    _u=Depends(ver),
):
    """Todo lo que necesita la grilla en una sola llamada: las columnas (tipos de
    talle con su escala) y las filas (empleados con lo que tengan cargado)."""
    with db_session() as conn:
        tipos = conn.execute(
            """SELECT id, nombre FROM uniformes_tipos_talle
               WHERE activo=1 ORDER BY orden, nombre"""
        ).fetchall()

        valores = conn.execute(
            """SELECT tipo_talle_id, valor FROM uniformes_talle_valores
               WHERE activo=1 ORDER BY tipo_talle_id, orden, valor"""
        ).fetchall()
        escala = {}
        for v in valores:
            escala.setdefault(v["tipo_talle_id"], []).append(v["valor"])

        # El empleado no tiene departamento propio: se deriva del cargo.
        sql = """
            SELECT e.id, e.apellido, e.nombre, e.activo,
                   c.nombre AS cargo, d.nombre AS departamento
            FROM empleados e
            LEFT JOIN cargos        c ON c.id = e.cargo_id
            LEFT JOIN departamentos d ON d.id = c.departamento_id
            WHERE """ + EXCLUIR_NO_PERSONAL + """
        """
        params = []
        if not incluir_inactivos:
            sql += " AND e.activo=1"
        if sin_cargo:
            # Sirve para encontrar a quien le falta el cargo: la constancia
            # imprime PUESTO tomandolo de ahi, asi que sin cargo sale en blanco.
            sql += " AND e.cargo_id IS NULL"
        elif cargo_id:
            sql += " AND e.cargo_id=?"
            params.append(cargo_id)
        if departamento_id:
            sql += " AND c.departamento_id=?"
            params.append(departamento_id)
        sql += " ORDER BY e.apellido, e.nombre"
        empleados = conn.execute(sql, params).fetchall()

        cargados = conn.execute(
            "SELECT empleado_id, tipo_talle_id, valor FROM uniformes_talles_empleado"
        ).fetchall()

    por_empleado = {}
    for t in cargados:
        por_empleado.setdefault(t["empleado_id"], {})[str(t["tipo_talle_id"])] = t["valor"]

    return {
        "tipos": [{"id": t["id"], "nombre": t["nombre"], "escala": escala.get(t["id"], [])}
                  for t in tipos],
        "empleados": [{**dict(e), "talles": por_empleado.get(e["id"], {})} for e in empleados],
    }


@router.put("/api/uniformes/talles")
def set_talle(data: TalleEmpleadoIn, _u=Depends(editar)):
    """Carga, cambia o borra el talle de una persona para un tipo.

    Valor vacío borra la fila: no se guarda "sin talle", simplemente no hay fila.
    Así el recuento de "cuántos faltan" sale de contar filas y no de distinguir
    entre vacío, nulo y cadena en blanco.
    """
    valor = (data.valor or "").strip()
    with db_session() as conn:
        if not conn.execute("SELECT id FROM empleados WHERE id=?", (data.empleado_id,)).fetchone():
            raise HTTPException(404, "Empleado no encontrado")
        if not conn.execute("SELECT id FROM uniformes_tipos_talle WHERE id=?",
                            (data.tipo_talle_id,)).fetchone():
            raise HTTPException(404, "Tipo de talle no encontrado")

        if not valor:
            conn.execute(
                "DELETE FROM uniformes_talles_empleado WHERE empleado_id=? AND tipo_talle_id=?",
                (data.empleado_id, data.tipo_talle_id),
            )
            return {"ok": True, "valor": None}

        # El desplegable solo puede mandar valores de la escala, pero se valida
        # igual: es lo que mantiene limpio el recuento para comprar.
        existe = conn.execute(
            "SELECT id FROM uniformes_talle_valores WHERE tipo_talle_id=? AND valor=?",
            (data.tipo_talle_id, valor),
        ).fetchone()
        if not existe:
            raise HTTPException(400, f"«{valor}» no figura en la escala de ese tipo de talle")

        conn.execute(
            """INSERT INTO uniformes_talles_empleado (empleado_id, tipo_talle_id, valor)
               VALUES (?,?,?)
               ON CONFLICT (empleado_id, tipo_talle_id)
               DO UPDATE SET valor=excluded.valor,
                             modificado_en=datetime('now','localtime')""",
            (data.empleado_id, data.tipo_talle_id, valor),
        )
    return {"ok": True, "valor": valor}


# ══════════════════════════════════════════════════════════════════════════════
# CONSTANCIAS
# ══════════════════════════════════════════════════════════════════════════════
# Guardar es emitir: no hay borrador. El acto real es instantáneo — el empleado
# está enfrente, se le dan tres cosas, se imprime y firma. Por eso al guardar se
# asigna el número y se sella la copia de los datos, todo en el mismo momento.
#
# Una constancia emitida es de solo lectura. Si algo salió mal: anular con motivo
# y hacer una nueva. Nunca se edita un documento ya firmado.


class ItemIn(BaseModel):
    elemento_id: int
    cantidad: int = 1
    talle: str | None = None
    observacion: str | None = None


class ConstanciaIn(BaseModel):
    empleado_id: int
    fecha: str
    tipo: str = "entrega"
    origen: str = "sistema"
    observaciones: str | None = None
    items: list[ItemIn]


class AnulacionIn(BaseModel):
    motivo: str


def _nombre_usuario(conn, user) -> str:
    """Se guarda el nombre, no el id: la constancia es un documento y tiene que
    seguir diciendo quién la emitió aunque después borren el usuario."""
    row = conn.execute("SELECT nombre FROM usuarios WHERE id=?", (user.get("sub"),)).fetchone()
    return (row["nombre"] if row else None) or user.get("email") or "?"


def _base_prenda(nombre: str) -> str:
    """«Pantalón (letra)» -> «Pantalón». Es la misma regla con la que la grilla
    de talles agrupa las columnas."""
    return re.sub(r"\s*\([^)]*\)\s*$", "", nombre or "").strip()


def _tipo_del_valor(conn, tipo_talle_id, valor):
    """Qué tipo de talle, dentro de la misma prenda, contiene ese valor.

    Devuelve el id, o None si el valor no pertenece a ninguna escala de esa
    prenda. Permite entregar un pantalón «L» cuando el elemento está configurado
    en la escala numérica: es la misma prenda en otra escala.
    """
    fila = conn.execute(
        "SELECT nombre FROM uniformes_tipos_talle WHERE id=?", (tipo_talle_id,)
    ).fetchone()
    if not fila:
        return None
    base = _base_prenda(fila["nombre"])
    for t in conn.execute("SELECT id, nombre FROM uniformes_tipos_talle").fetchall():
        if _base_prenda(t["nombre"]) != base:
            continue
        if conn.execute(
            "SELECT 1 FROM uniformes_talle_valores WHERE tipo_talle_id=? AND valor=?",
            (t["id"], valor),
        ).fetchone():
            return t["id"]
    return None


def _fecha_valida(fecha: str) -> str:
    f = (fecha or "").strip()[:10]
    try:
        datetime.strptime(f, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(400, "Fecha inválida (se espera AAAA-MM-DD)")
    return f


@router.get("/api/uniformes/empleados")
def empleados_para_constancia(incluir_inactivos: bool = True, _u=Depends(ver)):
    """El buscador de empleados de la constancia.

    Trae los egresados por defecto: una devolución llega siempre después de la
    baja, y reimprimir una constancia vieja también tiene que ser posible.
    """
    with db_session() as conn:
        sql = f"""
            SELECT e.id, e.apellido, e.nombre, e.dni, e.activo,
                   c.nombre AS cargo
            FROM empleados e
            LEFT JOIN cargos c ON c.id = e.cargo_id
            WHERE {EXCLUIR_NO_PERSONAL}
        """
        if not incluir_inactivos:
            sql += " AND e.activo=1"
        sql += " ORDER BY e.activo DESC, e.apellido, e.nombre"
        empleados = conn.execute(sql).fetchall()
        talles = conn.execute(
            "SELECT empleado_id, tipo_talle_id, valor FROM uniformes_talles_empleado"
        ).fetchall()

    por_empleado = {}
    for t in talles:
        por_empleado.setdefault(t["empleado_id"], {})[str(t["tipo_talle_id"])] = t["valor"]
    return [{**dict(e), "talles": por_empleado.get(e["id"], {})} for e in empleados]


@router.get("/api/uniformes/constancias")
def list_constancias(
    empleado_id: int | None = None,
    desde: str | None = None,
    hasta: str | None = None,
    tipo: str | None = None,
    estado: str | None = None,
    origen: str | None = None,
    _u=Depends(ver),
):
    sql = """
        SELECT m.id, m.numero, m.fecha, m.tipo, m.origen, m.estado,
               m.empleado_id, m.empleado_apellido_nombre, m.empleado_dni,
               m.cargo_nombre, m.observaciones, m.creado_por, m.creado_en,
               m.anulada_en, m.anulada_por, m.motivo_anulacion,
               (SELECT COUNT(*)          FROM uniformes_items i WHERE i.movimiento_id = m.id) AS renglones,
               (SELECT COALESCE(SUM(i.cantidad),0) FROM uniformes_items i WHERE i.movimiento_id = m.id) AS unidades
        FROM uniformes_movimientos m
        WHERE 1=1
    """
    params = []
    for campo, valor in (("m.empleado_id", empleado_id), ("m.tipo", tipo),
                         ("m.estado", estado), ("m.origen", origen)):
        if valor:
            sql += f" AND {campo}=?"
            params.append(valor)
    if desde:
        sql += " AND m.fecha >= ?"
        params.append(_fecha_valida(desde))
    if hasta:
        sql += " AND m.fecha <= ?"
        params.append(_fecha_valida(hasta))
    sql += " ORDER BY m.fecha DESC, m.id DESC"

    with db_session() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


@router.get("/api/uniformes/constancias/{cid}")
def get_constancia(cid: int, _u=Depends(ver)):
    with db_session() as conn:
        mov = conn.execute(
            "SELECT * FROM uniformes_movimientos WHERE id=?", (cid,)
        ).fetchone()
        if not mov:
            raise HTTPException(404, "Constancia no encontrada")
        items = conn.execute(
            """SELECT i.*, t.nombre AS tipo_talle_nombre
               FROM uniformes_items i
               LEFT JOIN uniformes_elementos e ON e.id = i.elemento_id
               LEFT JOIN uniformes_tipos_talle t ON t.id = e.tipo_talle_id
               WHERE i.movimiento_id=? ORDER BY i.orden, i.id""",
            (cid,),
        ).fetchall()
    return {**dict(mov), "items": [dict(i) for i in items]}


@router.post("/api/uniformes/constancias", status_code=201)
def crear_constancia(data: ConstanciaIn, user=Depends(editar)):
    if data.tipo not in ("entrega", "devolucion"):
        raise HTTPException(400, "Tipo inválido")
    if data.origen not in ("sistema", "historico"):
        raise HTTPException(400, "Origen inválido")
    if not data.items:
        raise HTTPException(400, "La constancia necesita al menos un renglón")
    if len(data.items) > MAX_ITEMS_POR_MOVIMIENTO:
        raise HTTPException(
            400,
            f"La constancia entra en una sola hoja: máximo {MAX_ITEMS_POR_MOVIMIENTO} "
            f"renglones. Si hacen falta más, son dos constancias."
        )
    fecha = _fecha_valida(data.fecha)

    with db_session() as conn:
        # La carga histórica es un permiso aparte: se le da a RRHH mientras dure
        # la digitalización del papel y se le saca después.
        if data.origen == "historico":
            rol_id = user.get("rol_id")
            if not rol_id or not tiene_permiso(rol_id, "uniformes", "carga_inicial"):
                raise HTTPException(403, "Sin permiso: uniformes.carga_inicial")

        emp = conn.execute(
            f"""SELECT e.id, e.apellido, e.nombre, e.dni, c.nombre AS cargo
                FROM empleados e LEFT JOIN cargos c ON c.id = e.cargo_id
                WHERE e.id=? AND {EXCLUIR_NO_PERSONAL}""",
            (data.empleado_id,),
        ).fetchone()
        if not emp:
            raise HTTPException(404, "Empleado no encontrado")

        # ── Sellar la copia de los datos del empleado ───────────────────────
        # Van impresos en un papel que se firma: si mañana corrigen un DNI mal
        # cargado, la hoja archivada no puede cambiar.
        cur = conn.execute(
            """INSERT INTO uniformes_movimientos
                   (empleado_id, fecha, tipo, origen, estado, observaciones,
                    empleado_apellido_nombre, empleado_dni, cargo_nombre, creado_por)
               VALUES (?,?,?,?,'emitida',?,?,?,?,?)""",
            (emp["id"], fecha, data.tipo, data.origen,
             (data.observaciones or "").strip() or None,
             f'{emp["apellido"]}, {emp["nombre"]}', emp["dni"], emp["cargo"],
             _nombre_usuario(conn, user)),
        )
        mid = cur.lastrowid

        # ── Número correlativo ──────────────────────────────────────────────
        # Los históricos no llevan: el correlativo identifica hojas que imprimió
        # el sistema, y un histórico no tiene hoja propia — la hoja es el papel
        # firmado que ya está en el legajo.
        if data.origen == "sistema":
            numero = conn.execute(
                "SELECT COALESCE(MAX(numero),0)+1 FROM uniformes_movimientos WHERE tipo=?",
                (data.tipo,),
            ).fetchone()[0]
            try:
                conn.execute("UPDATE uniformes_movimientos SET numero=? WHERE id=?", (numero, mid))
            except sqlite3.IntegrityError:
                # El índice único (tipo, numero) es la red: si dos usuarios emiten
                # a la vez, uno reintenta en vez de repetir número en un documento
                # firmado.
                raise HTTPException(409, "Otro usuario emitió al mismo tiempo. Probá de nuevo.")

        # ── Renglones, con su copia ─────────────────────────────────────────
        for orden, it in enumerate(data.items):
            el = conn.execute(
                """SELECT e.id, e.nombre, e.tipo_modelo, e.marca, e.posee_certificado,
                          e.tipo_talle_id, cat.nombre AS categoria_nombre
                   FROM uniformes_elementos e
                   LEFT JOIN uniformes_categorias cat ON cat.id = e.categoria_id
                   WHERE e.id=?""",
                (it.elemento_id,),
            ).fetchone()
            if not el:
                raise HTTPException(400, f"El elemento {it.elemento_id} no existe")
            if it.cantidad is None or it.cantidad < 1:
                raise HTTPException(400, f"«{el['nombre']}»: la cantidad tiene que ser 1 o más")

            talle = (it.talle or "").strip() or None
            tipo_del_talle = None
            if talle and el["tipo_talle_id"]:
                tipo_del_talle = _tipo_del_valor(conn, el["tipo_talle_id"], talle)
                if tipo_del_talle is None and data.origen == "sistema":
                    raise HTTPException(
                        400,
                        f"«{el['nombre']}»: el talle {talle} no figura en ninguna "
                        f"escala de esa prenda"
                    )

            conn.execute(
                """INSERT INTO uniformes_items
                       (movimiento_id, elemento_id, cantidad, talle, orden, observacion,
                        elemento_nombre, tipo_modelo, marca, posee_certificado, categoria_nombre)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (mid, el["id"], it.cantidad, talle, orden,
                 (it.observacion or "").strip() or None,
                 el["nombre"], el["tipo_modelo"], el["marca"],
                 el["posee_certificado"], el["categoria_nombre"]),
            )

            # ── El talle entregado actualiza el registro de la persona ──────
            # La entrega más reciente es la mejor evidencia de qué talle usa.
            # Solo si el valor pertenece a la escala, para no ensuciar el
            # recuento de compra con lo que venga de una carga histórica.
            # Se guarda en la escala a la que pertenece el valor, no en la del
            # elemento: entregar un pantalón «L» actualiza el talle en letra de la
            # persona, y le deja intacto el que tenga en números.
            if tipo_del_talle and data.tipo == "entrega":
                conn.execute(
                    """INSERT INTO uniformes_talles_empleado (empleado_id, tipo_talle_id, valor)
                       VALUES (?,?,?)
                       ON CONFLICT (empleado_id, tipo_talle_id)
                       DO UPDATE SET valor=excluded.valor,
                                     modificado_en=datetime('now','localtime')""",
                    (emp["id"], tipo_del_talle, talle),
                )

        row = conn.execute("SELECT * FROM uniformes_movimientos WHERE id=?", (mid,)).fetchone()
    return dict(row)


@router.post("/api/uniformes/constancias/{cid}/anular")
def anular_constancia(cid: int, data: AnulacionIn, user=Depends(require_permiso("uniformes", "eliminar"))):
    """Anular no borra: deja el documento con su motivo, quién y cuándo.

    Es lo que hace que el permiso pueda estar en RRHH sin riesgo — el que se
    equivoca emitiendo es RRHH y a los diez minutos quiere corregirlo.
    """
    motivo = (data.motivo or "").strip()
    if not motivo:
        raise HTTPException(400, "El motivo de anulación es obligatorio")

    with db_session() as conn:
        mov = conn.execute(
            "SELECT id, estado FROM uniformes_movimientos WHERE id=?", (cid,)
        ).fetchone()
        if not mov:
            raise HTTPException(404, "Constancia no encontrada")
        if mov["estado"] == "anulada":
            raise HTTPException(409, "La constancia ya estaba anulada")

        conn.execute(
            """UPDATE uniformes_movimientos
               SET estado='anulada', motivo_anulacion=?, anulada_por=?,
                   anulada_en=datetime('now','localtime')
               WHERE id=?""",
            (motivo, _nombre_usuario(conn, user), cid),
        )
        row = conn.execute("SELECT * FROM uniformes_movimientos WHERE id=?", (cid,)).fetchone()
    return dict(row)


# ══════════════════════════════════════════════════════════════════════════════
# IMPRESIÓN
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/api/uniformes/empresa")
def datos_empresa(_u=Depends(ver)):
    """El encabezado de la constancia impresa.

    Endpoint propio, y no /api/configuracion, porque ése exige usuarios:ver, que
    RRHH no tiene: sin esto, a quien emite la constancia le fallaría al
    imprimirla.

    Si la razón social está vacía se usa el nombre comercial, para que la hoja
    nunca salga con el membrete en blanco.
    """
    claves = ("empresa_razon_social", "empresa_cuit", "empresa_direccion",
              "empresa_localidad", "empresa_cp", "empresa_provincia",
              "nombre_empresa", "logo_empresa")
    with db_session() as conn:
        rows = conn.execute(
            f"SELECT clave, valor FROM configuracion "
            f"WHERE clave IN ({','.join('?' * len(claves))})",
            claves,
        ).fetchall()
    c = {r["clave"]: (r["valor"] or "").strip() for r in rows}
    return {
        "razon_social": c.get("empresa_razon_social") or c.get("nombre_empresa") or "",
        "cuit":         c.get("empresa_cuit", ""),
        "direccion":    c.get("empresa_direccion", ""),
        "localidad":    c.get("empresa_localidad", ""),
        "cp":           c.get("empresa_cp", ""),
        "provincia":    c.get("empresa_provincia", ""),
        "logo":         c.get("logo_empresa") or None,
    }
