"""Entrega de uniformes y EPP — catálogos.

Tanda 1: rubros, tipos de talle, valores de talle y elementos. Los movimientos
(constancias) y los talles por empleado vienen en las tandas siguientes.

Todo el router está detrás de la bandera `uniformes_activo`: con la bandera en 0
cada ruta responde 404, igual que si el módulo no existiera.
"""

import json
import re
import sqlite3
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from db.database import db_session
from db.uniformes_schema import (uniformes_activo, MAX_ITEMS_POR_MOVIMIENTO,
                                MESES_PENDIENTES_DEFECTO)
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
            # Lo que se le entregó es la mejor evidencia de qué talle usa, así que
            # el talle se carga solo y no hay que mantenerlo a mano en dos lados.
            # Se guarda en la escala a la que pertenece el valor, no en la del
            # elemento: entregar un pantalón «L» actualiza el talle en letra de la
            # persona, y le deja intacto el que tenga en números.
            #
            # Una entrega del sistema pisa el talle anterior: se está cargando hoy.
            # Una carga histórica no: la hoja puede ser de hace dos años y la
            # persona pudo cambiar de talle desde entonces. Solo completa el que
            # falte, que es justamente lo que se busca al digitalizar el papel.
            # Una devolución tampoco toca nada: devolver no es probarse ropa.
            if tipo_del_talle and data.tipo == "entrega":
                pisa = data.origen == "sistema"
                conn.execute(
                    f"""INSERT INTO uniformes_talles_empleado (empleado_id, tipo_talle_id, valor)
                        VALUES (?,?,?)
                        ON CONFLICT (empleado_id, tipo_talle_id) DO {
                            "UPDATE SET valor=excluded.valor, modificado_en=datetime('now','localtime')"
                            if pisa else "NOTHING"}""",
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


# ══════════════════════════════════════════════════════════════════════════════
# REPORTES
# ══════════════════════════════════════════════════════════════════════════════
# Dicen lo que SÍ se entregó, nunca lo que falta: no hay registro de qué le
# corresponde a cada puesto. Cuentan solo entregas emitidas —las anuladas no
# existen para los reportes, las devoluciones no son entregas— e incluyen las
# históricas, que son entregas reales cargadas desde el papel.

_CONTABLE = "m.tipo = 'entrega' AND m.estado = 'emitida'"

_FROM_ENTREGAS = """
    FROM uniformes_items i
    JOIN uniformes_movimientos m          ON m.id = i.movimiento_id
    JOIN empleados e                      ON e.id = m.empleado_id
    LEFT JOIN cargos c                    ON c.id = e.cargo_id
    LEFT JOIN uniformes_elementos el      ON el.id = i.elemento_id
    LEFT JOIN uniformes_categorias r      ON r.id = el.categoria_id
"""

_ORDEN_LETRAS = ["XXS", "XS", "S", "M", "L", "XL", "XXL", "XXXL"]


def _clave_talle(talle):
    """42 antes que 44, y S antes que M antes que L — no alfabético."""
    try:
        return (0, float(talle), "")
    except (TypeError, ValueError):
        u = (talle or "").upper()
        return (1, _ORDEN_LETRAS.index(u) if u in _ORDEN_LETRAS else 99, u)


def _dmy(iso):
    iso = (iso or "")[:10]
    return f"{iso[8:10]}/{iso[5:7]}/{iso[:4]}" if len(iso) == 10 else ""


def _filtros_entregas(desde, hasta, empleado_id, cargo_id, departamento_id, rubro_id):
    """El cargo y el rubro se filtran por id —el cargo actual del empleado y el
    rubro actual del elemento—, no por el texto que quedó copiado en cada
    constancia: así un cambio de nombre no parte el reporte en dos."""
    where, params = [_CONTABLE], []
    if desde:
        where.append("m.fecha >= ?"); params.append(_fecha_valida(desde))
    if hasta:
        where.append("m.fecha <= ?"); params.append(_fecha_valida(hasta))
    if empleado_id:
        where.append("m.empleado_id = ?"); params.append(empleado_id)
    if cargo_id:
        where.append("e.cargo_id = ?"); params.append(cargo_id)
    if departamento_id:
        where.append("c.departamento_id = ?"); params.append(departamento_id)
    if rubro_id:
        where.append("el.categoria_id = ?"); params.append(rubro_id)
    return " AND ".join(where), params


def _consulta_entregas(vista, desde, hasta, empleado_id, cargo_id, departamento_id, rubro_id):
    if vista not in ("detalle", "resumen"):
        raise HTTPException(400, "La vista tiene que ser detalle o resumen")
    where, params = _filtros_entregas(desde, hasta, empleado_id, cargo_id, departamento_id, rubro_id)

    with db_session() as conn:
        if vista == "detalle":
            # El detalle muestra lo que se imprimió: los datos copiados en la
            # constancia, no los del catálogo de hoy.
            filas = [dict(f) for f in conn.execute(f"""
                SELECT m.id AS constancia_id, m.numero, m.fecha, m.origen,
                       m.empleado_apellido_nombre AS empleado, m.cargo_nombre AS cargo,
                       i.elemento_nombre AS elemento, i.categoria_nombre AS rubro,
                       i.talle, i.cantidad
                {_FROM_ENTREGAS}
                WHERE {where}
                ORDER BY m.fecha DESC, m.id DESC, i.orden
            """, params).fetchall()]
        else:
            # El resumen agrupa por elemento (el id), con el nombre de hoy.
            filas = [dict(f) for f in conn.execute(f"""
                SELECT i.elemento_id,
                       COALESCE(el.nombre, MAX(i.elemento_nombre))  AS elemento,
                       COALESCE(r.nombre, MAX(i.categoria_nombre))  AS rubro,
                       SUM(i.cantidad)                              AS unidades,
                       COUNT(DISTINCT m.id)                         AS constancias,
                       COUNT(DISTINCT m.empleado_id)                AS personas
                {_FROM_ENTREGAS}
                WHERE {where}
                GROUP BY i.elemento_id
                ORDER BY unidades DESC, elemento
            """, params).fetchall()]
            por_elemento = {}
            for x in conn.execute(f"""
                SELECT i.elemento_id, i.talle, SUM(i.cantidad) AS unidades
                {_FROM_ENTREGAS}
                WHERE {where} AND i.talle IS NOT NULL AND i.talle != ''
                GROUP BY i.elemento_id, i.talle
            """, params).fetchall():
                por_elemento.setdefault(x["elemento_id"], []).append(
                    {"talle": x["talle"], "unidades": x["unidades"]})
            for f in filas:
                f["talles"] = sorted(por_elemento.get(f["elemento_id"], []),
                                     key=lambda z: _clave_talle(z["talle"]))

    unidades = sum(f.get("unidades", f.get("cantidad", 0)) or 0 for f in filas)
    return {"vista": vista, "filas": filas,
            "totales": {"filas": len(filas), "unidades": unidades}}


def _antiguedad(fecha_iso, hoy):
    d = date.fromisoformat(fecha_iso[:10])
    meses = (hoy.year - d.year) * 12 + (hoy.month - d.month) - (1 if hoy.day < d.day else 0)
    return {"fecha": fecha_iso[:10], "meses": max(meses, 0), "dias": (hoy - d).days}


def _consulta_antiguedad(cargo_id, departamento_id, rubro_id, incluir_inactivos,
                         incluir_sin_uniforme=False):
    """Última entrega por persona y por rubro.

    Se arma desde la lista de empleados y no desde las entregas: los que nunca
    recibieron nada no tienen ninguna fila de entrega, y son justamente los que
    tienen que aparecer primero.
    """
    hoy = date.today()
    with db_session() as conn:
        rubros = [dict(r) for r in conn.execute(
            "SELECT id, nombre, meses_alerta FROM uniformes_categorias WHERE activo=1 ORDER BY nombre")]

        sql = f"""SELECT e.id, e.apellido, e.nombre, e.activo, c.nombre AS cargo,
                         (s.cargo_id IS NOT NULL) AS sin_uniforme
                  FROM empleados e LEFT JOIN cargos c ON c.id = e.cargo_id
                  LEFT JOIN uniformes_cargos_sin_uniforme s ON s.cargo_id = e.cargo_id
                  WHERE {EXCLUIR_NO_PERSONAL}"""
        params = []
        if not incluir_inactivos:
            sql += " AND e.activo = 1"
        if cargo_id:
            sql += " AND e.cargo_id = ?"; params.append(cargo_id)
        if departamento_id:
            sql += " AND c.departamento_id = ?"; params.append(departamento_id)
        empleados = conn.execute(sql, params).fetchall()

        ultimas = {}
        for x in conn.execute(f"""
            SELECT m.empleado_id, el.categoria_id AS rubro_id, MAX(m.fecha) AS fecha
            FROM uniformes_items i
            JOIN uniformes_movimientos m     ON m.id = i.movimiento_id
            LEFT JOIN uniformes_elementos el ON el.id = i.elemento_id
            WHERE {_CONTABLE}
            GROUP BY m.empleado_id, el.categoria_id
        """).fetchall():
            ultimas.setdefault(x["empleado_id"], {})[x["rubro_id"]] = x["fecha"]

    umbral = {r["id"]: r["meses_alerta"] for r in rubros}
    filas, ocultos = [], 0
    for e in empleados:
        suyas = ultimas.get(e["id"], {})
        # Un puesto que no recibe uniforme oculta solo el «nunca»: si esa
        # persona alguna vez recibió algo, aparece igual, con su fecha.
        if e["sin_uniforme"] and not incluir_sin_uniforme and not any(suyas.values()):
            ocultos += 1
            continue
        por_rubro = {}
        for r in rubros:
            f = suyas.get(r["id"])
            if f is None:
                por_rubro[str(r["id"])] = None
                continue
            a = _antiguedad(f, hoy)
            # El aviso pinta, nunca bloquea: es solo una marca en el reporte.
            a["alerta"] = umbral[r["id"]] is not None and a["meses"] >= umbral[r["id"]]
            por_rubro[str(r["id"])] = a
        todas = [f for f in suyas.values() if f]
        filas.append({
            "id": e["id"],
            "empleado": f'{e["apellido"]}, {e["nombre"]}',
            "cargo": e["cargo"],
            "activo": e["activo"],
            "sin_uniforme": bool(e["sin_uniforme"]),
            "ultima": _antiguedad(max(todas), hoy) if todas else None,
            "rubros": por_rubro,
        })

    # Arriba quien nunca recibió; después, del más viejo al más reciente.
    def clave(f):
        ref = f["rubros"].get(str(rubro_id)) if rubro_id else f["ultima"]
        return (0, "", f["empleado"]) if ref is None else (1, ref["fecha"], f["empleado"])
    filas.sort(key=clave)
    return {"rubros": rubros, "filas": filas, "ordenado_por": rubro_id,
            "ocultos_sin_uniforme": ocultos}


def _describir_filtros(desde=None, hasta=None, empleado_id=None, cargo_id=None,
                       departamento_id=None, rubro_id=None):
    partes = []
    with db_session() as conn:
        for valor, sql, etiqueta in (
            (empleado_id, "SELECT apellido || ', ' || nombre FROM empleados WHERE id=?", "Empleado"),
            (cargo_id, "SELECT nombre FROM cargos WHERE id=?", "Cargo"),
            (departamento_id, "SELECT nombre FROM departamentos WHERE id=?", "Departamento"),
            (rubro_id, "SELECT nombre FROM uniformes_categorias WHERE id=?", "Rubro"),
        ):
            if valor:
                fila = conn.execute(sql, (valor,)).fetchone()
                partes.append(f"{etiqueta}: {fila[0] if fila else valor}")
        emp = conn.execute(
            "SELECT clave, valor FROM configuracion WHERE clave IN ('empresa_razon_social','nombre_empresa')"
        ).fetchall()
    if desde or hasta:
        partes.append(f"Período: {_dmy(desde) or 'desde el inicio'} a {_dmy(hasta) or 'hoy'}")
    c = {r["clave"]: (r["valor"] or "").strip() for r in emp}
    empresa = c.get("empresa_razon_social") or c.get("nombre_empresa") or ""
    return empresa, " · ".join(partes) or "Sin filtros"


def _excel(titulo, subtitulo, encabezados, filas, anchos, archivo, alertas=None):
    """Arma el .xlsx. `alertas` es un conjunto de (fila, columna) a pintar."""
    import openpyxl
    from io import BytesIO
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter
    from fastapi.responses import StreamingResponse

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = titulo[:31]
    ws.append([titulo])
    ws["A1"].font = Font(bold=True, size=13)
    ws.append([subtitulo])
    ws["A2"].font = Font(italic=True, color="666666")
    ws.append([])
    ws.append(encabezados)
    for celda in ws[4]:
        celda.font = Font(bold=True)
        celda.fill = PatternFill("solid", fgColor="EDEDED")
    rojo = PatternFill("solid", fgColor="F8D7D7")
    for n, fila in enumerate(filas):
        ws.append(fila)
        for col, valor in enumerate(fila, 1):
            celda = ws.cell(row=5 + n, column=col)
            if isinstance(valor, date):
                celda.number_format = "DD/MM/YYYY"
            if alertas and (n, col - 1) in alertas:
                celda.fill = rojo
    for i, ancho in enumerate(anchos, 1):
        ws.column_dimensions[get_column_letter(i)].width = ancho
    ws.freeze_panes = "A5"

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{archivo}"'},
    )


@router.get("/api/uniformes/reportes/entregas")
def reporte_entregas(vista: str = "detalle", desde: str | None = None, hasta: str | None = None,
                     empleado_id: int | None = None, cargo_id: int | None = None,
                     departamento_id: int | None = None, rubro_id: int | None = None,
                     _u=Depends(ver)):
    return _consulta_entregas(vista, desde, hasta, empleado_id, cargo_id, departamento_id, rubro_id)


@router.get("/api/uniformes/reportes/entregas.xlsx")
def reporte_entregas_excel(vista: str = "detalle", desde: str | None = None, hasta: str | None = None,
                           empleado_id: int | None = None, cargo_id: int | None = None,
                           departamento_id: int | None = None, rubro_id: int | None = None,
                           _u=Depends(ver)):
    datos = _consulta_entregas(vista, desde, hasta, empleado_id, cargo_id, departamento_id, rubro_id)
    empresa, filtros = _describir_filtros(desde, hasta, empleado_id, cargo_id, departamento_id, rubro_id)
    sub = f"{empresa} · {filtros} · Generado el {date.today().strftime('%d/%m/%Y')}".lstrip(" ·")
    if vista == "detalle":
        filas = [[date.fromisoformat(f["fecha"][:10]),
                  str(f["numero"]).zfill(6) if f["numero"] else "papel",
                  "Histórica" if f["origen"] == "historico" else "Sistema",
                  f["empleado"], f["cargo"] or "", f["elemento"], f["rubro"] or "",
                  f["talle"] or "", f["cantidad"]] for f in datos["filas"]]
        return _excel("Entregas — detalle", sub,
                      ["Fecha", "N°", "Origen", "Empleado", "Puesto", "Elemento", "Rubro", "Talle", "Cantidad"],
                      filas, [12, 9, 11, 32, 20, 30, 18, 8, 10],
                      f"uniformes_entregas_detalle_{date.today().isoformat()}.xlsx")
    filas = [[f["elemento"], f["rubro"] or "", f["unidades"], f["constancias"], f["personas"],
              " · ".join(f'{x["talle"]}: {x["unidades"]}' for x in f["talles"])] for f in datos["filas"]]
    return _excel("Entregas — resumen", sub,
                  ["Elemento", "Rubro", "Unidades", "Constancias", "Personas", "Por talle"],
                  filas, [32, 18, 10, 12, 10, 50],
                  f"uniformes_entregas_resumen_{date.today().isoformat()}.xlsx")


@router.get("/api/uniformes/reportes/antiguedad")
def reporte_antiguedad(cargo_id: int | None = None, departamento_id: int | None = None,
                       rubro_id: int | None = None, incluir_inactivos: bool = False,
                       incluir_sin_uniforme: bool = False, _u=Depends(ver)):
    return _consulta_antiguedad(cargo_id, departamento_id, rubro_id, incluir_inactivos,
                                incluir_sin_uniforme)


@router.get("/api/uniformes/reportes/antiguedad.xlsx")
def reporte_antiguedad_excel(cargo_id: int | None = None, departamento_id: int | None = None,
                             rubro_id: int | None = None, incluir_inactivos: bool = False,
                             incluir_sin_uniforme: bool = False, _u=Depends(ver)):
    datos = _consulta_antiguedad(cargo_id, departamento_id, rubro_id, incluir_inactivos,
                                 incluir_sin_uniforme)
    empresa, filtros = _describir_filtros(cargo_id=cargo_id, departamento_id=departamento_id,
                                          rubro_id=rubro_id)
    if datos["ocultos_sin_uniforme"]:
        filtros += (f" · Sin las {datos['ocultos_sin_uniforme']} personas de puestos que no "
                    f"reciben uniforme y nunca recibieron nada")
    sub = f"{empresa} · {filtros} · Generado el {date.today().strftime('%d/%m/%Y')}".lstrip(" ·")
    encabezados = ["Empleado", "Cargo", "Estado", "Última entrega", "Meses"]
    for r in datos["rubros"]:
        encabezados += [f"{r['nombre']} — última", f"{r['nombre']} — meses"]
    filas, alertas = [], set()
    for n, f in enumerate(datos["filas"]):
        u = f["ultima"]
        fila = [f["empleado"], f["cargo"] or "", "Activo" if f["activo"] else "Egresado",
                date.fromisoformat(u["fecha"]) if u else "nunca", u["meses"] if u else ""]
        for r in datos["rubros"]:
            a = f["rubros"].get(str(r["id"]))
            if a and a.get("alerta"):
                alertas.update({(n, len(fila)), (n, len(fila) + 1)})
            fila += [date.fromisoformat(a["fecha"]) if a else "nunca", a["meses"] if a else ""]
        filas.append(fila)
    anchos = [32, 20, 10, 14, 8] + [18, 10] * len(datos["rubros"])
    return _excel("Última entrega por persona", sub, encabezados, filas, anchos,
                  f"uniformes_ultima_entrega_{date.today().isoformat()}.xlsx", alertas)


# ══════════════════════════════════════════════════════════════════════════════
# PUESTOS QUE NO RECIBEN UNIFORME
# ══════════════════════════════════════════════════════════════════════════════

class PuestoIn(BaseModel):
    recibe: bool


@router.get("/api/uniformes/puestos")
def list_puestos(_u=Depends(ver)):
    with db_session() as conn:
        rows = conn.execute(f"""
            SELECT c.id, c.nombre, d.nombre AS departamento,
                   (SELECT COUNT(*) FROM empleados e
                     WHERE e.cargo_id = c.id AND e.activo = 1 AND {EXCLUIR_NO_PERSONAL}) AS empleados,
                   (s.cargo_id IS NULL) AS recibe,
                   s.marcado_por, s.marcado_en
            FROM cargos c
            LEFT JOIN departamentos d                ON d.id = c.departamento_id
            LEFT JOIN uniformes_cargos_sin_uniforme s ON s.cargo_id = c.id
            ORDER BY c.nombre
        """).fetchall()
    return [{**dict(r), "recibe": bool(r["recibe"])} for r in rows]


@router.put("/api/uniformes/puestos/{cargo_id}")
def set_puesto(cargo_id: int, data: PuestoIn, user=Depends(editar)):
    with db_session() as conn:
        if not conn.execute("SELECT id FROM cargos WHERE id=?", (cargo_id,)).fetchone():
            raise HTTPException(404, "Cargo no encontrado")
        if data.recibe:
            conn.execute("DELETE FROM uniformes_cargos_sin_uniforme WHERE cargo_id=?", (cargo_id,))
        else:
            conn.execute(
                """INSERT INTO uniformes_cargos_sin_uniforme (cargo_id, marcado_por) VALUES (?,?)
                   ON CONFLICT (cargo_id) DO NOTHING""",
                (cargo_id, _nombre_usuario(conn, user)),
            )
    return {"ok": True, "cargo_id": cargo_id, "recibe": data.recibe}


# ══════════════════════════════════════════════════════════════════════════════
# FICHA POR PERSONA
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/api/uniformes/resumen/{empleado_id}")
def resumen_empleado(empleado_id: int, _u=Depends(ver)):
    """El resumen de una persona: lo usan el botón de la ficha del empleado y la
    ficha dentro del módulo.

    Sale del mismo cálculo que el reporte de última entrega, así el aviso del
    botón coincide exactamente con lo que muestra el reporte.
    """
    datos = _consulta_antiguedad(None, None, None, incluir_inactivos=True,
                                 incluir_sin_uniforme=True)
    fila = next((f for f in datos["filas"] if f["id"] == empleado_id), None)
    if fila is None:
        raise HTTPException(404, "Empleado no encontrado")
    with db_session() as conn:
        constancias = conn.execute(
            f"SELECT COUNT(*) FROM uniformes_movimientos m WHERE m.empleado_id = ? AND {_CONTABLE}",
            (empleado_id,),
        ).fetchone()[0]
    rubros = []
    for r in datos["rubros"]:
        a = fila["rubros"].get(str(r["id"]))
        rubros.append({"id": r["id"], "nombre": r["nombre"], "meses_alerta": r["meses_alerta"],
                       "ultima": a, "alerta": bool(a and a.get("alerta"))})
    return {
        "empleado_id": empleado_id,
        "empleado": fila["empleado"],
        "cargo": fila["cargo"],
        "sin_uniforme": fila["sin_uniforme"],
        "constancias": constancias,
        "ultima": fila["ultima"],
        "rubros": rubros,
        "alertas": [{"rubro": x["nombre"], "meses": x["ultima"]["meses"]} for x in rubros if x["alerta"]],
    }


# ══════════════════════════════════════════════════════════════════════════════
# ROPA PENDIENTE Y CIERRE DEL CIRCUITO
# ══════════════════════════════════════════════════════════════════════════════
# El sistema no sabe qué ropa tiene alguien puesta: sabe qué se le entregó y
# cuándo. Por eso acá no hay una deuda calculada sino una foto corta y fechada
# —lo entregado en los últimos N meses, agrupado por prenda—, y lo anterior a esa
# ventana queda igual a la vista, aparte y con su fecha, para que lo juzgue una
# persona. El total de toda la vida no sirve: nadie devuelve las doce chaquetas
# que recibió en seis años, porque cada una reemplazó a la anterior.
#
# El cierre es lo que le da fin al circuito. Sin él la bandeja de egresados solo
# crece y en unos meses no la mira nadie.

RESULTADOS_CIERRE = {
    "devolvio_todo":     "Devolvió todo",
    "devolvio_parcial":  "Devolvió parte",
    "no_devolvio":       "No devolvió",
    "previo_al_sistema": "Anterior al sistema",
}

CLAVE_MESES = "uniformes_meses_pendientes"


class CierreIn(BaseModel):
    empleado_id: int
    fecha: str | None = None
    resultado: str
    observacion: str | None = None


class ReaperturaIn(BaseModel):
    motivo: str


class CierreMasivoIn(BaseModel):
    hasta: str
    observacion: str | None = None
    simular: bool = True


class ParametrosIn(BaseModel):
    meses_pendientes: int


def _meses_pendientes(conn) -> int:
    """La ventana, configurable. Si el valor guardado es basura se usa el de
    fábrica: este número decide qué ve el que liquida, no puede quedar en 0."""
    row = conn.execute("SELECT valor FROM configuracion WHERE clave=?", (CLAVE_MESES,)).fetchone()
    try:
        n = int(str(row["valor"]).strip()) if row else MESES_PENDIENTES_DEFECTO
    except (TypeError, ValueError):
        return MESES_PENDIENTES_DEFECTO
    return n if 1 <= n <= 120 else MESES_PENDIENTES_DEFECTO


def _restar_meses(hoy: date, meses: int) -> date:
    """Fecha de inicio de la ventana. Sin dateutil: es la única cuenta de
    calendario del módulo y no justifica una dependencia."""
    total = hoy.year * 12 + (hoy.month - 1) - meses
    anio, mes = divmod(total, 12)
    mes += 1
    bisiesto = anio % 4 == 0 and (anio % 100 != 0 or anio % 400 == 0)
    largo = [31, 29 if bisiesto else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][mes - 1]
    return date(anio, mes, min(hoy.day, largo))


def _cubierto_por_cierre(fila, cierre):
    """¿Ese movimiento ya quedó resuelto por el cierre?

    Por fecha, salvo el empate. Si el cierre y la entrega son del mismo día
    —alguien que vuelve a entrar y recibe ropa esa misma tarde— la fecha sola no
    alcanza y desempata el momento en que se cargó cada cosa: lo anterior al
    cierre queda resuelto, lo posterior vuelve a contar.
    """
    if not cierre:
        return False
    if fila["fecha"] != cierre["fecha"]:
        return fila["fecha"] < cierre["fecha"]
    return (fila["creado_en"] or "") <= (cierre["cerrado_en"] or "")


def _pendientes(conn, empleados, meses, hoy=None):
    """Qué ropa puede tener cada una de esas personas, por prenda.

    Devuelve un diccionario por empleado_id. Tres reglas:

    - Se cuenta desde el último cierre vigente: lo anterior ya se resolvió, con
      desempate por hora si caen el mismo día. Eso hace que una recontratación
      funcione sola — el que vuelve arranca en cero y suma lo nuevo.
    - Dentro de la ventana, las entregas se suman por prenda; lo de antes va
      aparte, con su fecha, sin sumarse al total.
    - Las devoluciones restan, con piso en cero.
    """
    hoy = hoy or date.today()
    desde = _restar_meses(hoy, meses).isoformat()
    ids = [e["id"] for e in empleados]
    salida = {}
    if not ids:
        return salida
    marcas = ",".join("?" * len(ids))

    cierres = {r["empleado_id"]: dict(r) for r in conn.execute(
        f"""SELECT * FROM uniformes_cierres
            WHERE estado='vigente' AND empleado_id IN ({marcas})""", ids).fetchall()}

    filas = conn.execute(f"""
        SELECT m.empleado_id, m.id AS constancia_id, m.numero, m.fecha, m.tipo, m.origen,
               m.creado_en, i.elemento_id, i.cantidad, i.talle,
               COALESCE(el.nombre, i.elemento_nombre)  AS elemento,
               COALESCE(r.nombre,  i.categoria_nombre) AS rubro
        FROM uniformes_items i
        JOIN uniformes_movimientos m     ON m.id = i.movimiento_id
        LEFT JOIN uniformes_elementos el ON el.id = i.elemento_id
        LEFT JOIN uniformes_categorias r ON r.id = el.categoria_id
        WHERE m.estado = 'emitida' AND m.empleado_id IN ({marcas})
        ORDER BY m.fecha, m.id, i.orden
    """, ids).fetchall()

    acum = {i: {"dentro": {}, "antes": {}, "devuelto": {}, "constancias": {}} for i in ids}
    for f in filas:
        a = acum[f["empleado_id"]]
        if _cubierto_por_cierre(f, cierres.get(f["empleado_id"])):
            continue
        # Un elemento borrado del catálogo deja el id en NULL: se agrupa por el
        # nombre copiado en la constancia para que no se junten prendas distintas.
        clave = f["elemento_id"] if f["elemento_id"] is not None else f'n:{f["elemento"]}'

        if f["fecha"] >= desde:
            c = a["constancias"].setdefault(f["constancia_id"], {
                "id": f["constancia_id"], "numero": f["numero"], "fecha": f["fecha"],
                "tipo": f["tipo"], "origen": f["origen"], "renglones": 0, "unidades": 0})
            c["renglones"] += 1
            c["unidades"] += f["cantidad"] or 0

        if f["tipo"] == "devolucion":
            d = a["devuelto"].setdefault(clave, {
                "elemento_id": f["elemento_id"], "elemento": f["elemento"],
                "rubro": f["rubro"], "unidades": 0})
            d["unidades"] += f["cantidad"] or 0
            continue

        destino = a["dentro"] if f["fecha"] >= desde else a["antes"]
        p = destino.setdefault(clave, {
            "elemento_id": f["elemento_id"], "elemento": f["elemento"], "rubro": f["rubro"],
            "entregado": 0, "ultima": None, "fechas": []})
        p["entregado"] += f["cantidad"] or 0
        p["ultima"] = max(p["ultima"] or "", f["fecha"])
        p["fechas"].append({"fecha": f["fecha"], "cantidad": f["cantidad"], "talle": f["talle"]})

    for e in empleados:
        a = acum[e["id"]]
        prendas = []
        for clave, p in a["dentro"].items():
            dev = a["devuelto"].pop(clave, None)
            devuelto = dev["unidades"] if dev else 0
            prendas.append({**p, "devuelto": devuelto,
                            "pendiente": max(p["entregado"] - devuelto, 0),
                            "antiguedad": _antiguedad(p["ultima"], hoy)})
        anteriores = []
        for clave, p in a["antes"].items():
            dev = a["devuelto"].pop(clave, None)
            anteriores.append({
                "elemento_id": p["elemento_id"], "elemento": p["elemento"], "rubro": p["rubro"],
                "entregado": p["entregado"], "devuelto": dev["unidades"] if dev else 0,
                "ultima": p["ultima"], "antiguedad": _antiguedad(p["ultima"], hoy)})
        # Devoluciones de prendas que no figuran entregadas en este tramo. Se
        # muestran igual: si no, parecería que la persona no devolvió nada.
        sueltas = [{"elemento_id": d["elemento_id"], "elemento": d["elemento"], "rubro": d["rubro"],
                    "entregado": 0, "devuelto": d["unidades"], "pendiente": 0,
                    "ultima": None, "fechas": [], "antiguedad": None}
                   for d in a["devuelto"].values()]

        prendas.sort(key=lambda x: ((x["rubro"] or ""), (x["elemento"] or "")))
        anteriores.sort(key=lambda x: x["ultima"], reverse=True)
        salida[e["id"]] = {
            "meses": meses,
            "desde": desde,
            "prendas": prendas + sueltas,
            "anteriores": anteriores,
            "constancias": sorted(a["constancias"].values(),
                                  key=lambda c: (c["fecha"], c["id"]), reverse=True),
            "total_pendiente": sum(p["pendiente"] for p in prendas),
            "cierre": cierres.get(e["id"]),
        }
    return salida


def _fila_empleado(conn, empleado_id):
    return conn.execute(
        f"""SELECT e.id, e.apellido, e.nombre, e.dni, e.activo, e.fecha_egreso,
                   c.nombre AS cargo
            FROM empleados e LEFT JOIN cargos c ON c.id = e.cargo_id
            WHERE e.id = ? AND {EXCLUIR_NO_PERSONAL}""", (empleado_id,)).fetchone()


@router.get("/api/uniformes/parametros")
def get_parametros(_u=Depends(ver)):
    with db_session() as conn:
        return {"meses_pendientes": _meses_pendientes(conn)}


@router.put("/api/uniformes/parametros")
def put_parametros(data: ParametrosIn, _u=Depends(editar)):
    if not 1 <= data.meses_pendientes <= 120:
        raise HTTPException(400, "La ventana tiene que estar entre 1 y 120 meses")
    valor = str(data.meses_pendientes)
    with db_session() as conn:
        cur = conn.execute("UPDATE configuracion SET valor=? WHERE clave=?", (valor, CLAVE_MESES))
        if cur.rowcount == 0:
            conn.execute(
                "INSERT INTO configuracion (clave, valor, descripcion) VALUES (?,?,?)",
                (CLAVE_MESES, valor,
                 "Meses hacia atrás que se consideran «lo que la persona todavía tiene»"))
    return {"meses_pendientes": data.meses_pendientes}


@router.get("/api/uniformes/pendientes/{empleado_id}")
def pendientes_empleado(empleado_id: int, _u=Depends(ver)):
    """El panel de una persona: qué puede tener, qué es viejo, qué papeles hay."""
    with db_session() as conn:
        emp = _fila_empleado(conn, empleado_id)
        if not emp:
            raise HTTPException(404, "Empleado no encontrado")
        datos = _pendientes(conn, [emp], _meses_pendientes(conn))[emp["id"]]
        historia = [dict(r) for r in conn.execute(
            "SELECT * FROM uniformes_cierres WHERE empleado_id=? ORDER BY id DESC",
            (empleado_id,)).fetchall()]
    return {
        "empleado_id": emp["id"],
        "empleado": f'{emp["apellido"]}, {emp["nombre"]}',
        "cargo": emp["cargo"],
        "activo": emp["activo"],
        "fecha_egreso": emp["fecha_egreso"],
        **datos,
        "cierres": historia,
    }


def _consulta_egresados(desde, hasta, incluir_cerrados, solo_con_pendientes):
    """La bandeja: quién se fue y todavía figura con ropa.

    Se arma sobre `fecha_egreso`, que es el dato que tiene el que liquida.
    """
    with db_session() as conn:
        meses = _meses_pendientes(conn)
        sql = f"""SELECT e.id, e.apellido, e.nombre, e.dni, e.activo, e.fecha_egreso,
                         c.nombre AS cargo
                  FROM empleados e LEFT JOIN cargos c ON c.id = e.cargo_id
                  WHERE e.activo = 0 AND e.fecha_egreso IS NOT NULL
                    AND {EXCLUIR_NO_PERSONAL}"""
        params = []
        if desde:
            sql += " AND e.fecha_egreso >= ?"; params.append(_fecha_valida(desde))
        if hasta:
            sql += " AND e.fecha_egreso <= ?"; params.append(_fecha_valida(hasta))
        sql += " ORDER BY e.fecha_egreso DESC, e.apellido"
        empleados = conn.execute(sql, params).fetchall()
        datos = _pendientes(conn, empleados, meses)

    filas, cerrados = [], 0
    for e in empleados:
        d = datos[e["id"]]
        if d["cierre"]:
            cerrados += 1
            if not incluir_cerrados:
                continue
        elif solo_con_pendientes and not d["total_pendiente"] and not d["anteriores"]:
            continue
        filas.append({
            "id": e["id"],
            "empleado": f'{e["apellido"]}, {e["nombre"]}',
            "cargo": e["cargo"],
            "fecha_egreso": e["fecha_egreso"],
            "total_pendiente": d["total_pendiente"],
            "anteriores": len(d["anteriores"]),
            "prendas": [{"elemento": p["elemento"], "pendiente": p["pendiente"]}
                        for p in d["prendas"] if p["pendiente"]],
            "cierre": d["cierre"],
        })
    return {"meses": meses, "filas": filas,
            "totales": {"filas": len(filas), "cerrados": cerrados,
                        "unidades": sum(f["total_pendiente"] for f in filas)}}


@router.get("/api/uniformes/egresados")
def listado_egresados(desde: str | None = None, hasta: str | None = None,
                      incluir_cerrados: bool = False, solo_con_pendientes: bool = True,
                      _u=Depends(ver)):
    return _consulta_egresados(desde, hasta, incluir_cerrados, solo_con_pendientes)


@router.get("/api/uniformes/egresados.xlsx")
def listado_egresados_excel(desde: str | None = None, hasta: str | None = None,
                            incluir_cerrados: bool = False, solo_con_pendientes: bool = True,
                            _u=Depends(ver)):
    datos = _consulta_egresados(desde, hasta, incluir_cerrados, solo_con_pendientes)
    empresa, filtros = _describir_filtros(desde, hasta)
    sub = (f"{empresa} · Egreso — {filtros} · Ventana de {datos['meses']} meses · "
           f"Generado el {date.today().strftime('%d/%m/%Y')}").lstrip(" ·")
    filas = [[f["empleado"], f["cargo"] or "",
              date.fromisoformat(f["fecha_egreso"][:10]) if f["fecha_egreso"] else "",
              f["total_pendiente"],
              " · ".join(f'{p["elemento"]} ({p["pendiente"]})' for p in f["prendas"]),
              RESULTADOS_CIERRE.get((f["cierre"] or {}).get("resultado"), "")]
             for f in datos["filas"]]
    return _excel("Egresados", sub,
                  ["Empleado", "Cargo", "Egreso", "Pendiente", "Prendas", "Cierre"],
                  filas, [34, 22, 12, 11, 52, 20], "uniformes-egresados.xlsx")


@router.post("/api/uniformes/cierres", status_code=201)
def crear_cierre(data: CierreIn, user=Depends(editar)):
    """Cierra el circuito de una persona: deja de figurar como pendiente.

    No exige que haya una constancia de devolución. Si la exigiera, el día que
    alguien se va sin devolver nada habría que emitir un papel vacío para poder
    cerrar, o sea inventar un documento. Lo que devolvió se puede escribir en la
    observación, o registrarse aparte como devolución si hay algo que firmar.
    """
    if data.resultado not in RESULTADOS_CIERRE:
        raise HTTPException(400, "Resultado inválido")
    with db_session() as conn:
        emp = _fila_empleado(conn, data.empleado_id)
        if not emp:
            raise HTTPException(404, "Empleado no encontrado")
        fecha = _fecha_valida(data.fecha) if data.fecha else date.today().isoformat()
        datos = _pendientes(conn, [emp], _meses_pendientes(conn))[emp["id"]]
        try:
            cur = conn.execute(
                """INSERT INTO uniformes_cierres
                       (empleado_id, fecha, resultado, observacion, pendiente_json, cerrado_por)
                   VALUES (?,?,?,?,?,?)""",
                (emp["id"], fecha, data.resultado,
                 (data.observacion or "").strip() or None,
                 json.dumps(datos["prendas"], ensure_ascii=False),
                 _nombre_usuario(conn, user)))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Esta persona ya tiene un cierre vigente")
        row = conn.execute("SELECT * FROM uniformes_cierres WHERE id=?", (cur.lastrowid,)).fetchone()
    return dict(row)


@router.post("/api/uniformes/cierres/{cid}/reabrir")
def reabrir_cierre(cid: int, data: ReaperturaIn,
                   user=Depends(require_permiso("uniformes", "eliminar"))):
    """Reabrir no borra: el cierre queda como historia, con quién y por qué.

    Va con el permiso de eliminar —el mismo que anular una constancia— porque
    deshacer una decisión ya asentada no es lo mismo que tomarla.
    """
    motivo = (data.motivo or "").strip()
    if not motivo:
        raise HTTPException(400, "El motivo es obligatorio")
    with db_session() as conn:
        row = conn.execute("SELECT * FROM uniformes_cierres WHERE id=?", (cid,)).fetchone()
        if not row:
            raise HTTPException(404, "Cierre no encontrado")
        if row["estado"] != "vigente":
            raise HTTPException(409, "Ese cierre ya estaba reabierto")
        conn.execute(
            """UPDATE uniformes_cierres
               SET estado='reabierto', reabierto_en=datetime('now','localtime'),
                   reabierto_por=?, motivo_reapertura=?
               WHERE id=?""", (_nombre_usuario(conn, user), motivo, cid))
        row = conn.execute("SELECT * FROM uniformes_cierres WHERE id=?", (cid,)).fetchone()
    return dict(row)


@router.post("/api/uniformes/cierres/masivo")
def cierre_masivo(data: CierreMasivoIn, user=Depends(editar)):
    """Cierra de una vez a los egresados anteriores a la puesta en marcha.

    Sin esto la bandeja nace con cientos de personas que se fueron hace años y
    el reporte es inservible el primer día. Va con el permiso de carga inicial,
    el mismo de la digitalización del papel: es una acción de puesta en marcha.

    Simula por defecto: dice a cuántos alcanzaría sin tocar nada.
    """
    rol_id = user.get("rol_id")
    if not rol_id or not tiene_permiso(rol_id, "uniformes", "carga_inicial"):
        raise HTTPException(403, "Sin permiso: uniformes.carga_inicial")
    hasta = _fecha_valida(data.hasta)
    with db_session() as conn:
        pendientes = conn.execute(
            f"""SELECT e.id, e.apellido, e.nombre, e.fecha_egreso
                FROM empleados e
                WHERE e.activo = 0 AND e.fecha_egreso IS NOT NULL AND e.fecha_egreso <= ?
                  AND {EXCLUIR_NO_PERSONAL}
                  AND e.id NOT IN (SELECT empleado_id FROM uniformes_cierres WHERE estado='vigente')
                ORDER BY e.fecha_egreso DESC""", (hasta,)).fetchall()
        muestra = [{"id": p["id"], "empleado": f'{p["apellido"]}, {p["nombre"]}',
                    "fecha_egreso": p["fecha_egreso"]} for p in pendientes[:20]]
        if data.simular:
            return {"simulacion": True, "cantidad": len(pendientes), "muestra": muestra}
        quien = _nombre_usuario(conn, user)
        obs = (data.observacion or "").strip() or "Cierre masivo de puesta en marcha"
        conn.executemany(
            """INSERT INTO uniformes_cierres
                   (empleado_id, fecha, resultado, observacion, cerrado_por)
               VALUES (?,?,'previo_al_sistema',?,?)""",
            [(p["id"], p["fecha_egreso"], obs, quien) for p in pendientes])
    return {"simulacion": False, "cantidad": len(pendientes), "muestra": muestra}
