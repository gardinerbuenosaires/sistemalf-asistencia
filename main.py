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
    Empleados actualmente en su turno: tienen b*_entrada asignado pero no b*_salida,
    y la ventana del bloque aún no cerró (hora_salida + tolerancia_salida_despues).
    Usa el mismo greedy del evaluador — no depende del campo tipo del ZKTeco.
    """
    from db.database import db_session
    from datetime import datetime, timedelta
    from collections import defaultdict
    from sync.evaluador import _clasificar_fichajes, _dt

    ahora  = datetime.now()
    hoy    = str(ahora.date())
    manana = str((ahora + timedelta(days=1)).date())

    with db_session() as conn:
        planes = conn.execute(
            """SELECT p.empleado_id, p.horario_id,
                      e.user_id, e.nombre, e.apellido, e.cargo
               FROM planificacion p
               JOIN empleados e ON e.id = p.empleado_id
               WHERE p.fecha = ? AND p.es_franco = 0 AND p.horario_id IS NOT NULL""",
            (hoy,)
        ).fetchall()

        eids_con_plan = {p["empleado_id"] for p in planes}

        hids = list({p["horario_id"] for p in planes}) if planes else []
        bloques_map: dict = defaultdict(list)
        if hids:
            ph = ",".join("?" * len(hids))
            for b in conn.execute(
                f"SELECT * FROM horarios_bloques WHERE horario_id IN ({ph}) ORDER BY horario_id, bloque",
                hids
            ).fetchall():
                bloques_map[b["horario_id"]].append(dict(b))

        # Fichajes de hoy de todos los empleados (con y sin plan)
        todos_fichajes = conn.execute(
            """SELECT f.empleado_id, f.timestamp,
                      e.user_id, e.nombre, e.apellido, e.cargo
               FROM fichajes f
               JOIN empleados e ON e.id = f.empleado_id
               WHERE date(f.timestamp) IN (?,?)
               ORDER BY f.empleado_id, f.timestamp""",
            (hoy, manana)
        ).fetchall()

    fich_map: dict = defaultdict(list)
    for f in todos_fichajes:
        fich_map[f["empleado_id"]].append(dict(f))

    con_plan   = []
    sin_plan   = []
    ausentes   = []
    fecha      = ahora.date()

    # — Empleados con planificación: greedy completo —
    for plan in planes:
        eid     = plan["empleado_id"]
        bloques = bloques_map.get(plan["horario_id"], [])
        if not bloques:
            continue

        fichajes_emp = [f for f in fich_map[eid] if f["timestamp"][:10] in (hoy, manana)]
        slots = _clasificar_fichajes(fichajes_emp, bloques, fecha)

        for b in bloques:
            bn        = b["bloque"]
            f_entrada = slots.get(f"b{bn}_entrada")
            f_salida  = slots.get(f"b{bn}_salida")

            t_entrada = _dt(fecha, b["hora_entrada"])
            cruza     = bool(b["cruza_medianoche"])
            t_salida  = _dt(fecha, b["hora_salida"], dia_sig=cruza)

            if not f_entrada:
                # Ausente: la ventana de entrada ya cerró (pasó la tolerancia)
                tol_entrada = timedelta(minutes=b.get("tolerancia_entrada_despues", 60))
                if ahora > t_entrada + tol_entrada and ahora < t_salida + timedelta(hours=2):
                    ausentes.append({
                        "user_id":           plan["user_id"],
                        "nombre":            plan["nombre"],
                        "apellido":          plan["apellido"],
                        "cargo":             plan["cargo"],
                        "hora_entrada_plan": b["hora_entrada"],
                        "bloque":            bn,
                    })
                continue

            if f_salida:
                continue

            margen = timedelta(minutes=b.get("tolerancia_salida_despues", 60))
            if ahora > t_salida + margen:
                continue

            con_plan.append({
                "user_id":      plan["user_id"],
                "nombre":       plan["nombre"],
                "apellido":     plan["apellido"],
                "cargo":        plan["cargo"],
                "hora_entrada": f_entrada["timestamp"],
                "bloque":       bn,
            })

    # — Empleados sin planificación: heurística de paridad —
    vistos = {p["user_id"] for p in con_plan}
    for eid, fichajes in fich_map.items():
        if eid in eids_con_plan:
            continue
        fichajes_hoy = [f for f in fichajes if f["timestamp"][:10] == hoy]
        if not fichajes_hoy or len(fichajes_hoy) % 2 == 0:
            continue  # par = probablemente salió
        ultimo = fichajes_hoy[-1]
        if ultimo["user_id"] in vistos:
            continue
        sin_plan.append({
            "user_id":       ultimo["user_id"],
            "nombre":        ultimo["nombre"],
            "apellido":      ultimo["apellido"],
            "cargo":         ultimo["cargo"],
            "hora_entrada":  fichajes_hoy[0]["timestamp"],
            "ultimo_fichaje": ultimo["timestamp"],
            "cant_fichajes": len(fichajes_hoy),
        })

    con_plan.sort(key=lambda p: (p["cargo"] or "", p["hora_entrada"]))
    sin_plan.sort(key=lambda p: (p["cargo"] or "", p["hora_entrada"]))
    ausentes.sort(key=lambda p: (p["cargo"] or "", p["hora_entrada_plan"]))
    return {"con_planificacion": con_plan, "sin_planificacion": sin_plan, "ausentes": ausentes}


if __name__ == "__main__":
    uvicorn.run("main:app", host=API_HOST, port=API_PORT, reload=False)
