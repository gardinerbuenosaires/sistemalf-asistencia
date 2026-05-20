import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from db.database import db_session
from auth.core import require_permiso, get_current_user

router = APIRouter(prefix="/api/feriados", tags=["feriados"])

_API_URL = "https://api.argentinadatos.com/v1/feriados/{año}"


class FeriadoIn(BaseModel):
    fecha:  str   # YYYY-MM-DD
    nombre: str
    tipo:   str = "nacional"


@router.get("")
def listar_feriados(año: int | None = None, _user=Depends(require_permiso("calendarios", "ver"))):
    with db_session() as conn:
        if año:
            rows = conn.execute(
                "SELECT * FROM feriados WHERE fecha LIKE ? ORDER BY fecha",
                (f"{año}-%",)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM feriados ORDER BY fecha").fetchall()
    return [dict(r) for r in rows]


@router.post("", status_code=201)
def crear_feriado(data: FeriadoIn, _user=Depends(require_permiso("calendarios", "editar"))):
    with db_session() as conn:
        try:
            conn.execute(
                "INSERT INTO feriados (fecha, nombre, tipo) VALUES (?,?,?)",
                (data.fecha, data.nombre, data.tipo),
            )
            row = conn.execute(
                "SELECT * FROM feriados WHERE fecha=?", (data.fecha,)
            ).fetchone()
        except Exception:
            raise HTTPException(409, "Ya existe un feriado en esa fecha")
    return dict(row)


@router.delete("/{fid}")
def eliminar_feriado(fid: int, _user=Depends(require_permiso("calendarios", "eliminar"))):
    with db_session() as conn:
        if not conn.execute("SELECT id FROM feriados WHERE id=?", (fid,)).fetchone():
            raise HTTPException(404, "Feriado no encontrado")
        conn.execute("DELETE FROM feriados WHERE id=?", (fid,))
    return {"ok": True}


@router.post("/importar/{anio}")
def importar_feriados(anio: int, _user=Depends(require_permiso("calendarios", "editar"))):
    """
    Sincroniza los feriados nacionales del año contra api.argentinadatos.com.
    - Agrega los que no están en la DB.
    - Elimina los nacionales que ya no aparecen en la API (fechas movidas).
    - No toca los feriados de tipo provincial o local (cargados a mano).
    """
    try:
        resp = httpx.get(_API_URL.format(año=anio), timeout=10)
        resp.raise_for_status()
        datos = resp.json()
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Error al consultar la API: {e}")
    except Exception as e:
        raise HTTPException(500, f"Error inesperado: {e}")

    api_fechas = {}
    for f in datos:
        fecha  = (f.get("fecha") or "").strip()
        nombre = (f.get("nombre") or "Feriado").strip()
        tipo_raw = (f.get("tipo") or "").lower()
        tipo = "provincial" if "provincial" in tipo_raw else "nacional"
        if fecha:
            api_fechas[fecha] = (nombre, tipo)

    prefijo = f"{anio}-"
    nuevos = eliminados = 0

    with db_session() as conn:
        # Fechas nacionales que ya están en la DB para este año
        db_rows = conn.execute(
            "SELECT id, fecha FROM feriados WHERE fecha LIKE ? AND tipo = 'nacional'",
            (prefijo + "%",)
        ).fetchall()
        db_fechas = {r["fecha"]: r["id"] for r in db_rows}

        # Eliminar los que ya no están en la API
        for fecha, fid in db_fechas.items():
            if fecha not in api_fechas:
                conn.execute("DELETE FROM feriados WHERE id=?", (fid,))
                eliminados += 1

        # Agregar los nuevos
        for fecha, (nombre, tipo) in api_fechas.items():
            if fecha not in db_fechas:
                conn.execute(
                    "INSERT INTO feriados (fecha, nombre, tipo) VALUES (?,?,?)",
                    (fecha, nombre, tipo),
                )
                nuevos += 1

    return {"anio": anio, "total_api": len(api_fechas), "nuevos": nuevos, "eliminados": eliminados}
