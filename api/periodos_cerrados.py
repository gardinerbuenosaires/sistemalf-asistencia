from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth.core import require_permiso, get_current_user
from db.database import db_session

router = APIRouter(prefix="/api/periodos-cerrados", tags=["periodos"])


# ── Helpers importables desde otros módulos ───────────────────────────────────

def es_periodo_cerrado(conn, anio: int, mes: int) -> bool:
    return conn.execute(
        "SELECT 1 FROM periodos_cerrados WHERE anio=? AND mes=?", (anio, mes)
    ).fetchone() is not None


def check_periodo_abierto(conn, fecha_str: str):
    """Lanza HTTPException 409 si el período que contiene fecha_str está cerrado."""
    d = date.fromisoformat(fecha_str)
    if es_periodo_cerrado(conn, d.year, d.month):
        raise HTTPException(
            409,
            f"El período {d.year}-{d.month:02d} está cerrado y no puede modificarse"
        )


def check_rango_abierto(conn, fecha_desde: str, fecha_hasta: str):
    """Verifica todos los meses en un rango de fechas."""
    d = date.fromisoformat(fecha_desde)
    d_end = date.fromisoformat(fecha_hasta)
    cur = date(d.year, d.month, 1)
    while cur <= d_end:
        if es_periodo_cerrado(conn, cur.year, cur.month):
            raise HTTPException(
                409,
                f"El período {cur.year}-{cur.month:02d} está cerrado y no puede modificarse"
            )
        cur = date(cur.year + (cur.month // 12), (cur.month % 12) + 1, 1)


# ── Endpoints ─────────────────────────────────────────────────────────────────

class PeriodoIn(BaseModel):
    anio: int
    mes:  int


@router.get("")
def listar(_user=Depends(require_permiso("periodos", "ver"))):
    with db_session() as conn:
        rows = conn.execute(
            "SELECT pc.*, u.nombre as cerrado_por_nombre "
            "FROM periodos_cerrados pc "
            "LEFT JOIN usuarios u ON u.id = pc.cerrado_por "
            "ORDER BY pc.anio DESC, pc.mes DESC"
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/{anio}/{mes}")
def get_periodo(anio: int, mes: int, _user=Depends(require_permiso("periodos", "ver"))):
    with db_session() as conn:
        row = conn.execute(
            "SELECT pc.*, u.nombre as cerrado_por_nombre "
            "FROM periodos_cerrados pc "
            "LEFT JOIN usuarios u ON u.id = pc.cerrado_por "
            "WHERE pc.anio=? AND pc.mes=?",
            (anio, mes)
        ).fetchone()
    if not row:
        return {"cerrado": False}
    return {**dict(row), "cerrado": True}


@router.post("", status_code=201)
def cerrar_periodo(data: PeriodoIn, user=Depends(require_permiso("periodos", "cerrar"))):
    if data.mes < 1 or data.mes > 12:
        raise HTTPException(400, "Mes inválido")
    hoy = date.today()
    if data.anio > hoy.year or (data.anio == hoy.year and data.mes >= hoy.month):
        raise HTTPException(400, "Solo se pueden cerrar períodos anteriores al mes actual")
    with db_session() as conn:
        if es_periodo_cerrado(conn, data.anio, data.mes):
            raise HTTPException(409, "El período ya está cerrado")
        conn.execute(
            "INSERT INTO periodos_cerrados (anio, mes, cerrado_por) VALUES (?,?,?)",
            (data.anio, data.mes, user["id"])
        )
    return {"ok": True}


@router.delete("/{anio}/{mes}")
def reabrir_periodo(anio: int, mes: int, _user=Depends(require_permiso("periodos", "reabrir"))):
    with db_session() as conn:
        if not es_periodo_cerrado(conn, anio, mes):
            raise HTTPException(404, "El período no está cerrado")
        conn.execute(
            "DELETE FROM periodos_cerrados WHERE anio=? AND mes=?", (anio, mes)
        )
    return {"ok": True}
