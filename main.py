import logging
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager

from config import API_HOST, API_PORT
from db.database import init_db
from sync.scheduler import start_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_scheduler()
    yield


app = FastAPI(
    title="Sistema de Fichaje Biométrico",
    version="0.1.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory="web/static"), name="static")


@app.get("/", include_in_schema=False)
def root():
    return FileResponse("web/templates/index.html")


# --- Rutas de la API (se expanden en etapas siguientes) ---

@app.get("/api/sync/now", tags=["sync"])
def sync_now():
    """Fuerza una sincronización inmediata con el dispositivo."""
    from sync.downloader import sync_attendances
    from sync.processor import process_pending
    result = sync_attendances()
    result["procesamiento"] = process_pending()
    return result


@app.get("/api/sync/log", tags=["sync"])
def sync_log(limit: int = 20):
    """Últimas N entradas del log de sincronización."""
    from db.database import db_session
    with db_session() as conn:
        rows = conn.execute(
            """
            SELECT * FROM sync_log
            ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/fichajes", tags=["fichajes"])
def get_fichajes(user_id: str | None = None, fecha: str | None = None, limit: int = 100):
    """Consulta fichajes con filtros opcionales."""
    from db.database import db_session
    clauses = []
    params = []
    if user_id:
        clauses.append("user_id = ?")
        params.append(user_id)
    if fecha:
        clauses.append("date(timestamp) = ?")
        params.append(fecha)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    with db_session() as conn:
        rows = conn.execute(
            f"SELECT * FROM fichajes {where} ORDER BY timestamp DESC LIMIT ?",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/inconsistencias", tags=["fichajes"])
def get_inconsistencias(resuelta: int = 0):
    """Lista de inconsistencias detectadas."""
    from db.database import db_session
    with db_session() as conn:
        rows = conn.execute(
            """
            SELECT i.*, e.nombre
            FROM inconsistencias i
            LEFT JOIN empleados e ON e.user_id = i.user_id
            WHERE i.resuelta = ?
            ORDER BY i.creado_en DESC
            """,
            (resuelta,),
        ).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/empleados", tags=["empleados"])
def get_empleados():
    from db.database import db_session
    with db_session() as conn:
        rows = conn.execute(
            "SELECT * FROM empleados WHERE activo = 1 ORDER BY nombre"
        ).fetchall()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    uvicorn.run("main:app", host=API_HOST, port=API_PORT, reload=False)
