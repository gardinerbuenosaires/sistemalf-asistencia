import logging
import uvicorn
from fastapi import FastAPI, Cookie, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse
from contextlib import asynccontextmanager
from typing import Optional

from config import API_HOST, API_PORT
from db.database import init_db
from sync.scheduler import start_scheduler
from api.horarios import router as horarios_router
from api.planificacion import router as planificacion_router
from api.calendarios import router as calendarios_router
from api.empleados import router as empleados_router
from api.resultados import router as resultados_router
from api.auth import router as auth_router
from auth.core import decode_token, ensure_admin, check_page_auth

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)




@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    ensure_admin()
    start_scheduler()
    yield


app = FastAPI(
    title="Sistema de Fichaje Biométrico",
    version="0.1.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory="web/static"), name="static")
app.include_router(horarios_router)
app.include_router(planificacion_router)
app.include_router(calendarios_router)
app.include_router(empleados_router)
app.include_router(resultados_router)
app.include_router(auth_router)


def _auth(request: Request, modulo: str, accion: str = "ver"):
    return check_page_auth(request.cookies.get("session"), modulo, accion)


@app.get("/login", include_in_schema=False)
def page_login(): return FileResponse("web/templates/login.html")

@app.get("/", include_in_schema=False)
def root(request: Request):
    return FileResponse("web/templates/index.html") if _auth(request, "dashboard") else RedirectResponse("/login")

@app.get("/horarios", include_in_schema=False)
def page_horarios(request: Request):
    return FileResponse("web/templates/horarios.html") if _auth(request, "horarios") else RedirectResponse("/login")

@app.get("/planificacion", include_in_schema=False)
def page_planificacion(request: Request):
    return FileResponse("web/templates/planificacion.html") if _auth(request, "planificacion") else RedirectResponse("/login")

@app.get("/calendarios", include_in_schema=False)
def page_calendarios(request: Request):
    return FileResponse("web/templates/calendarios.html") if _auth(request, "calendarios") else RedirectResponse("/login")

@app.get("/empleados", include_in_schema=False)
def page_empleados(request: Request):
    return FileResponse("web/templates/empleados.html") if _auth(request, "empleados") else RedirectResponse("/login")

@app.get("/asistencia", include_in_schema=False)
def page_asistencia(request: Request):
    return FileResponse("web/templates/asistencia.html") if _auth(request, "asistencia") else RedirectResponse("/login")

@app.get("/usuarios", include_in_schema=False)
def page_usuarios(request: Request):
    return FileResponse("web/templates/usuarios.html") if _auth(request, "usuarios") else RedirectResponse("/login")

@app.get("/roles", include_in_schema=False)
def page_roles(request: Request):
    return FileResponse("web/templates/roles.html") if _auth(request, "roles") else RedirectResponse("/login")


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
def get_fichajes(user_id: str | None = None, fecha: str | None = None,
                 fecha_desde: str | None = None, fecha_hasta: str | None = None,
                 limit: int = 100):
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
    if fecha_desde:
        clauses.append("date(timestamp) >= ?")
        params.append(fecha_desde)
    if fecha_hasta:
        clauses.append("date(timestamp) <= ?")
        params.append(fecha_hasta)
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
            "SELECT * FROM empleados WHERE activo = 1 ORDER BY apellido, nombre"
        ).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/empleados/sync-dispositivo", tags=["empleados"])
def sync_empleados_dispositivo():
    """Importa desde el ZKTeco los usuarios que aún no existen como empleados."""
    from sync.downloader import sync_users
    return sync_users()


@app.get("/api/presencia/hoy", tags=["presencia"])
def presencia_hoy():
    """
    Empleados con entrada activa hoy (entrada sin salida posterior).
    Agrupa por cargo para la vista del encargado.
    """
    from db.database import db_session
    from datetime import date
    hoy = date.today().isoformat()
    with db_session() as conn:
        rows = conn.execute(
            """
            SELECT
                e.user_id,
                e.nombre,
                e.apellido,
                e.cargo,
                f.timestamp AS hora_entrada
            FROM fichajes f
            JOIN empleados e ON e.id = f.empleado_id
            WHERE date(f.timestamp) = ?
              AND f.tipo = 'entrada'
              AND NOT EXISTS (
                SELECT 1 FROM fichajes f2
                WHERE f2.empleado_id = f.empleado_id
                  AND f2.tipo = 'salida'
                  AND date(f2.timestamp) = ?
                  AND f2.timestamp > f.timestamp
              )
            ORDER BY e.cargo, f.timestamp
            """,
            (hoy, hoy),
        ).fetchall()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    uvicorn.run("main:app", host=API_HOST, port=API_PORT, reload=False)
