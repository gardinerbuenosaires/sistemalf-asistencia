import logging
import time
import uvicorn
import shutil
from pathlib import Path
from fastapi import Depends, FastAPI, Cookie, Request, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from typing import Optional
from starlette.middleware.base import BaseHTTPMiddleware

from config import API_HOST, API_PORT, DATA_DIR
from db.database import init_db
from sync.scheduler import start_scheduler
from api.horarios import router as horarios_router
from api.planificacion import router as planificacion_router
from api.calendarios import router as calendarios_router
from api.empleados import router as empleados_router
from api.resultados import router as resultados_router
from api.auth import router as auth_router
from api.turnos import router as turnos_router
from api.asistencia_mensual import router as asistencia_mensual_router
from api.feriados import router as feriados_router
from api.fichajes import router as fichajes_router
from api.catalogos import router as catalogos_router
from api.premios import router as premios_router
from api.vacaciones import router as vacaciones_router
from api.francos import router as francos_router
from api.periodos_cerrados import router as periodos_cerrados_router
from api.distribucion import router as distribucion_router
from auth.core import decode_token, ensure_admin, check_page_auth, require_permiso, get_current_user, refresh_token, INACTIVITY_TTL

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)




FOTOS_DIR            = DATA_DIR / "fotos"
FOTOS_PENDIENTES_DIR = DATA_DIR / "fotos_pendientes"


@asynccontextmanager
async def lifespan(app: FastAPI):
    FOTOS_DIR.mkdir(parents=True, exist_ok=True)
    FOTOS_PENDIENTES_DIR.mkdir(parents=True, exist_ok=True)
    init_db()
    ensure_admin()
    start_scheduler()
    yield


app = FastAPI(
    title="Sistema de Fichaje Biométrico",
    version="0.1.0",
    lifespan=lifespan,
)

_SKIP_INACTIVITY = {"/login", "/upload-foto", "/api/auth/login", "/api/auth/logout"}
_inactividad_cache: dict = {"ttl": 0, "segundos": INACTIVITY_TTL}

def _get_inactividad_segundos() -> int:
    """Lee el timeout de inactividad desde la DB, con cache de 60 segundos."""
    now = time.time()
    if now < _inactividad_cache["ttl"]:
        return _inactividad_cache["segundos"]
    from db.database import db_session, get_config
    with db_session() as conn:
        val = get_config(conn, "inactividad_minutos", "10")
    try:
        segundos = max(1, int(val)) * 60
    except (ValueError, TypeError):
        segundos = INACTIVITY_TTL
    _inactividad_cache.update({"ttl": now + 60, "segundos": segundos})
    return segundos


class SlidingSessionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith("/static") or path.startswith("/api/fotos") or path in _SKIP_INACTIVITY:
            return await call_next(request)

        token = request.cookies.get("session")
        if token:
            payload = decode_token(token)
            if payload:
                timeout = _get_inactividad_segundos()
                if time.time() - payload.get("la", 0) > timeout:
                    resp = (
                        JSONResponse({"detail": "Sesión expirada por inactividad"}, status_code=401)
                        if path.startswith("/api/") else RedirectResponse("/login")
                    )
                    resp.delete_cookie("session")
                    return resp
                response = await call_next(request)
                response.set_cookie(
                    "session", refresh_token(payload),
                    httponly=True, samesite="lax", max_age=timeout
                )
                return response

        return await call_next(request)

app.add_middleware(SlidingSessionMiddleware)

app.mount("/static", StaticFiles(directory="web/static"), name="static")
app.include_router(horarios_router)
app.include_router(planificacion_router)
app.include_router(calendarios_router)
app.include_router(empleados_router)
app.include_router(resultados_router)
app.include_router(auth_router)
app.include_router(turnos_router)
app.include_router(asistencia_mensual_router)
app.include_router(feriados_router)
app.include_router(fichajes_router)
app.include_router(catalogos_router)
app.include_router(premios_router)
app.include_router(vacaciones_router)
app.include_router(francos_router)
app.include_router(periodos_cerrados_router)
app.include_router(distribucion_router)


def _page(request: Request, template: str, modulo: str, accion: str = "ver"):
    token = request.cookies.get("session")
    if not token or not decode_token(token):
        return RedirectResponse("/login")
    if not check_page_auth(token, modulo, accion):
        return RedirectResponse("/")
    return FileResponse(template)


@app.get("/login", include_in_schema=False)
def page_login(): return FileResponse("web/templates/login.html")

@app.get("/", include_in_schema=False)
def root(request: Request):
    token = request.cookies.get("session")
    payload = decode_token(token) if token else None
    if not payload:
        return RedirectResponse("/login")
    if check_page_auth(token, "dashboard"):
        return FileResponse("web/templates/index.html")
    from db.database import db_session
    with db_session() as conn:
        rol = conn.execute("SELECT pagina_inicio FROM roles WHERE id=?",
                           (payload.get("rol_id"),)).fetchone()
    destino = (rol["pagina_inicio"] if rol and rol["pagina_inicio"] else None) or "/"
    if destino == "/":
        return FileResponse("web/templates/index.html")
    return RedirectResponse(destino)

@app.get("/horarios", include_in_schema=False)
def page_horarios(request: Request):
    return _page(request, "web/templates/horarios.html", "horarios")

@app.get("/planificacion", include_in_schema=False)
def page_planificacion(request: Request):
    return _page(request, "web/templates/planificacion.html", "planificacion")

@app.get("/calendarios", include_in_schema=False)
def page_calendarios(request: Request):
    return _page(request, "web/templates/calendarios.html", "calendarios")

@app.get("/empleados", include_in_schema=False)
def page_empleados(request: Request):
    return _page(request, "web/templates/empleados.html", "empleados")

@app.get("/asistencia", include_in_schema=False)
def page_asistencia(request: Request):
    return _page(request, "web/templates/asistencia.html", "asistencia")

@app.get("/usuarios", include_in_schema=False)
def page_usuarios(request: Request):
    return RedirectResponse("/configuracion")

@app.get("/roles", include_in_schema=False)
def page_roles(request: Request):
    return _page(request, "web/templates/roles.html", "roles")

@app.get("/configuracion", include_in_schema=False)
def page_configuracion(request: Request):
    return _page(request, "web/templates/configuracion.html", "usuarios")

@app.get("/fichajes", include_in_schema=False)
def page_fichajes(request: Request):
    return _page(request, "web/templates/fichajes.html", "asistencia")

@app.get("/asistencia-mensual", include_in_schema=False)
def page_asistencia_mensual(request: Request):
    return _page(request, "web/templates/asistencia_mensual.html", "asistencia")

@app.get("/premios", include_in_schema=False)
def page_premios(request: Request):
    return _page(request, "web/templates/premios.html", "premios")

@app.get("/vacaciones", include_in_schema=False)
def page_vacaciones(request: Request):
    return _page(request, "web/templates/vacaciones.html", "vacaciones")

@app.get("/empleados/listado", include_in_schema=False)
def page_empleados_listado(request: Request):
    return _page(request, "web/templates/empleados_listado.html", "empleados")

@app.get("/empleados/{eid}/legajo", include_in_schema=False)
def page_legajo_imprimible(eid: int, request: Request):
    return _page(request, "web/templates/legajo_imprimible.html", "empleados")

@app.get("/distribucion", include_in_schema=False)
def page_distribucion(request: Request):
    return _page(request, "web/templates/distribucion.html", "distribucion")

@app.get("/manual-encargado", include_in_schema=False)
def page_manual_encargado(request: Request):
    return _page(request, "web/templates/manual_encargado.html", "asistencia")

@app.get("/upload-foto", include_in_schema=False)
def page_upload_foto():
    return FileResponse("web/templates/upload_foto.html")

@app.post("/api/fotos/verificar-pin", tags=["fotos"])
async def verificar_pin_foto(body: dict):
    from db.database import db_session, get_config
    with db_session() as conn:
        pin_config = get_config(conn, "pin_upload_foto", "")
    if not pin_config:
        return {"ok": True}
    return {"ok": body.get("pin", "") == pin_config}

@app.post("/api/fotos/upload", tags=["fotos"])
async def upload_foto_pendiente(file: UploadFile = File(...)):
    import uuid
    from fastapi import HTTPException as _HTTPException
    ext = Path(file.filename).suffix.lower() if file.filename else ""
    if ext not in (".png", ".jpg", ".jpeg", ".webp", ".heic", ".heif"):
        ext = ".jpg"
    filename = f"{uuid.uuid4().hex}{ext}"
    dest = FOTOS_PENDIENTES_DIR / filename
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    return {"ok": True, "filename": filename}

@app.get("/fotos/{filename}", include_in_schema=False)
def serve_foto(filename: str):
    from fastapi import HTTPException as _HTTPException
    import re
    if not re.match(r'^[a-zA-Z0-9_\-\.]+$', filename):
        raise _HTTPException(400)
    for d in (FOTOS_DIR, FOTOS_PENDIENTES_DIR):
        p = d / filename
        if p.exists():
            return FileResponse(str(p))
    raise _HTTPException(404)


# --- Rutas de la API (se expanden en etapas siguientes) ---

@app.get("/api/sync/now", tags=["sync"])
def sync_now(_user=Depends(require_permiso("sync", "procesar"))):
    """Fuerza una sincronización inmediata con el dispositivo."""
    from sync.downloader import sync_attendances
    from sync.processor import process_pending
    result = sync_attendances()
    result["procesamiento"] = process_pending()
    return result


@app.get("/api/sync/log", tags=["sync"])
def sync_log(limit: int = 20, _user=Depends(get_current_user)):
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
                 limit: int = 100, _user=Depends(require_permiso("asistencia", "ver"))):
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
def get_inconsistencias(resuelta: int = 0, _user=Depends(require_permiso("asistencia", "ver"))):
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


@app.post("/api/empleados/sync-dispositivo", tags=["empleados"])
def sync_empleados_dispositivo(_user=Depends(require_permiso("sync", "procesar"))):
    """Importa desde el ZKTeco los usuarios que aún no existen como empleados."""
    from sync.downloader import sync_users
    return sync_users()


@app.get("/api/configuracion", tags=["configuracion"])
def get_configuracion(_user=Depends(require_permiso("usuarios", "ver"))):
    from db.database import db_session
    with db_session() as conn:
        rows = conn.execute("SELECT clave, valor, descripcion FROM configuracion ORDER BY clave").fetchall()
    return [dict(r) for r in rows]


@app.put("/api/configuracion/{clave}", tags=["configuracion"])
def set_configuracion(clave: str, body: dict, _user=Depends(require_permiso("usuarios", "editar"))):
    from db.database import db_session
    from fastapi import HTTPException
    valor = body.get("valor", "").strip()
    if not valor:
        raise HTTPException(status_code=422, detail="Valor requerido")
    with db_session() as conn:
        conn.execute(
            "INSERT INTO configuracion (clave, valor) VALUES (?,?) ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor",
            (clave, valor),
        )
    return {"ok": True}


@app.post("/api/config/logo", tags=["configuracion"])
def upload_logo(file: UploadFile = File(...), _user=Depends(require_permiso("usuarios", "editar"))):
    from fastapi import HTTPException
    ext = Path(file.filename).suffix.lower()
    if ext not in (".png", ".jpg", ".jpeg", ".svg", ".webp"):
        raise HTTPException(400, "Formato no soportado. Usar PNG, JPG o SVG.")
    dest = Path("web/static/uploads") / f"logo_empresa{ext}"
    for old in Path("web/static/uploads").glob("logo_empresa.*"):
        old.unlink(missing_ok=True)
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    url = f"/static/uploads/logo_empresa{ext}"
    from db.database import db_session
    with db_session() as conn:
        conn.execute(
            "INSERT INTO configuracion (clave, valor, descripcion) VALUES (?,?,?) "
            "ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor",
            ("logo_empresa", url, "URL del logo de la empresa para el legajo")
        )
    return {"ok": True, "url": url}


@app.get("/api/config/logo", tags=["configuracion"])
def get_logo():
    from db.database import db_session
    with db_session() as conn:
        row = conn.execute("SELECT valor FROM configuracion WHERE clave='logo_empresa'").fetchone()
    return {"url": row["valor"] if row else None}


@app.get("/api/config/nombre-empresa", tags=["configuracion"])
def get_nombre_empresa():
    from db.database import db_session
    with db_session() as conn:
        row = conn.execute("SELECT valor FROM configuracion WHERE clave='nombre_empresa'").fetchone()
    return {"nombre": row["valor"] if row else ""}


@app.post("/api/dispositivo/sincronizar-hora", tags=["dispositivo"])
def sincronizar_hora_dispositivo(_user=Depends(require_permiso("usuarios", "editar"))):
    from sync.downloader import sync_time
    result = sync_time()
    if not result["ok"]:
        from fastapi import HTTPException
        raise HTTPException(500, detail=result["error"] or "Error al sincronizar hora")
    return {"ok": True}


@app.post("/api/admin/liberar-ids-dispositivo", tags=["admin"])
def liberar_ids_dispositivo_manual(_user=Depends(require_permiso("usuarios", "editar"))):
    """Libera el user_id de empleados dados de baja hace más de 48 horas."""
    from db.database import db_session
    with db_session() as conn:
        rows = conn.execute("""
            SELECT id, user_id, nombre, apellido
            FROM empleados
            WHERE activo = 0
              AND user_id IS NOT NULL
              AND fecha_egreso IS NOT NULL
              AND (julianday('now','localtime') - julianday(fecha_egreso)) >= 2
        """).fetchall()
        liberados = []
        for r in rows:
            conn.execute("UPDATE empleados SET user_id=NULL, en_dispositivo=0 WHERE id=?", (r["id"],))
            liberados.append({"nombre": f"{r['apellido']} {r['nombre']}", "user_id": r["user_id"]})
    return {"liberados": len(liberados), "detalle": liberados}


@app.get("/api/presencia/hoy", tags=["presencia"])
def presencia_hoy(_user=Depends(require_permiso("dashboard", "ver"))):
    """
    Empleados actualmente en su turno: tienen b*_entrada asignado pero no b*_salida,
    y la ventana del bloque aún no cerró (hora_salida + tolerancia_salida_despues).
    Usa el mismo greedy del evaluador — no depende del campo tipo del ZKTeco.
    """
    from db.database import db_session, get_config
    from datetime import datetime, timedelta
    from collections import defaultdict
    from sync.evaluador import _clasificar_fichajes, _dt

    ahora  = datetime.now()
    hoy    = str(ahora.date())
    ayer   = str((ahora - timedelta(days=1)).date())
    manana = str((ahora + timedelta(days=1)).date())

    with db_session() as conn:
        planes = conn.execute(
            """SELECT p.empleado_id, p.horario_id,
                      e.user_id, e.nombre, e.apellido, c.nombre AS cargo
               FROM planificacion p
               JOIN empleados e ON e.id = p.empleado_id
               LEFT JOIN cargos c ON c.id = e.cargo_id
               WHERE p.fecha = ? AND p.es_franco = 0 AND p.horario_id IS NOT NULL
               AND e.activo = 1 AND e.tipo != 'jerarquico'""",
            (hoy,)
        ).fetchall()

        eids_con_plan = {p["empleado_id"] for p in planes}

        # Empleados con franco planificado hoy
        francos_hoy = conn.execute(
            """SELECT p.empleado_id,
                      e.user_id, e.nombre, e.apellido, c.nombre AS cargo
               FROM planificacion p
               JOIN empleados e ON e.id = p.empleado_id
               LEFT JOIN cargos c ON c.id = e.cargo_id
               WHERE p.fecha = ? AND p.es_franco = 1 AND e.activo = 1""",
            (hoy,)
        ).fetchall()
        eids_con_franco = {f["empleado_id"] for f in francos_hoy}

        # FT con horario ya asignado → situación atendida, no mostrar en "franco con fichaje"
        eids_ft_resuelto = {
            r["empleado_id"]
            for r in conn.execute(
                "SELECT empleado_id FROM resultados_dia WHERE fecha=? AND es_franco=1 AND horario_id IS NOT NULL",
                (hoy,)
            ).fetchall()
        }

        # Empleados con cualquier novedad hoy (situación ya atendida → no mostrar en "no ficharon")
        novedades_justificadas = conn.execute(
            "SELECT DISTINCT empleado_id FROM novedades WHERE fecha = ?",
            (hoy,)
        ).fetchall()
        eids_con_novedad = {r["empleado_id"] for r in novedades_justificadas}

        umbral_h = int(get_config(conn, "umbral_ciclo_abierto_horas", "4"))

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
                      e.user_id, e.nombre, e.apellido, c.nombre AS cargo
               FROM fichajes f
               JOIN empleados e ON e.id = f.empleado_id AND e.activo = 1
               LEFT JOIN cargos c ON c.id = e.cargo_id
               WHERE date(f.timestamp) IN (?,?,?)
               ORDER BY f.empleado_id, f.timestamp""",
            (ayer, hoy, manana)
        ).fetchall()

    fich_map: dict = defaultdict(list)
    for f in todos_fichajes:
        fich_map[f["empleado_id"]].append(dict(f))

    con_plan           = []
    sin_plan           = []
    ausentes           = []
    franco_con_fichaje = []
    ciclo_abierto      = []
    fecha              = ahora.date()

    # — Empleados con planificación: greedy completo —
    for plan in planes:
        eid     = plan["empleado_id"]
        bloques = bloques_map.get(plan["horario_id"], [])
        if not bloques:
            continue

        fichajes_emp = [f for f in fich_map[eid] if f["timestamp"][:10] in (ayer, hoy, manana)]
        slots = _clasificar_fichajes(fichajes_emp, bloques, fecha)

        for b in bloques:
            if not b.get("aplica", 1):
                continue  # bloque MT: no aplica, ignorar
            bn        = b["bloque"]
            f_entrada = slots.get(f"b{bn}_entrada")
            f_salida  = slots.get(f"b{bn}_salida")

            t_entrada = _dt(fecha, b["hora_entrada"])
            cruza     = bool(b["cruza_medianoche"])
            t_salida  = _dt(fecha, b["hora_salida"], dia_sig=cruza)
            # Compensar cruza_medianoche no seteado: si salida < entrada, sumar un día
            if t_salida <= t_entrada:
                t_salida += timedelta(days=1)

            if not f_entrada:
                tol_entrada = timedelta(minutes=b.get("tolerancia_entrada_despues", 60))
                if (ahora > t_entrada + tol_entrada
                        and ahora < t_salida + timedelta(hours=2)
                        and eid not in eids_con_novedad):
                    ausentes.append({
                        "empleado_id":       plan["empleado_id"],
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
                # Tiene entrada pero superó el turno → ciclo sin cerrar
                ciclo_abierto.append({
                    "empleado_id":      plan["empleado_id"],
                    "user_id":          plan["user_id"],
                    "nombre":           plan["nombre"],
                    "apellido":         plan["apellido"],
                    "cargo":            plan["cargo"],
                    "hora_entrada":     f_entrada["timestamp"],
                    "ultimo_fichaje":   f_entrada["timestamp"],
                    "hora_salida_plan": b["hora_salida"],
                    "motivo":           "turno_vencido",
                })
                continue

            con_plan.append({
                "empleado_id":  plan["empleado_id"],
                "user_id":      plan["user_id"],
                "nombre":       plan["nombre"],
                "apellido":     plan["apellido"],
                "cargo":        plan["cargo"],
                "hora_entrada": f_entrada["timestamp"],
                "bloque":       bn,
            })

    # — Empleados con franco planificado pero que ficharon hoy —
    for fila in francos_hoy:
        eid = fila["empleado_id"]
        if eid in eids_ft_resuelto:
            continue  # horario ya asignado → situación atendida
        fichajes_hoy_f = [f for f in fich_map.get(eid, []) if f["timestamp"][:10] == hoy]
        if not fichajes_hoy_f:
            continue
        # Salida de turno noche que cruza medianoche: todos los fichajes de hoy son antes de las
        # 06:00 y hay al menos una entrada ayer después de las 15:00 → par completo, ignorar.
        fichajes_ayer_f = [f for f in fich_map.get(eid, []) if f["timestamp"][:10] == ayer]
        if (all(f["timestamp"][11:16] < "06:00" for f in fichajes_hoy_f)
                and any(f["timestamp"][11:16] >= "15:00" for f in fichajes_ayer_f)):
            continue
        # Salida de turno 00:00-08:00: todos los fichajes de hoy son antes de las 09:00
        # y hay una entrada ayer después de las 23:00 → salida del turno de medianoche, ignorar.
        if (all(f["timestamp"][11:16] < "09:00" for f in fichajes_hoy_f)
                and any(f["timestamp"][11:16] >= "23:00" for f in fichajes_ayer_f)):
            continue
        ultimo_dt = datetime.strptime(fichajes_hoy_f[-1]["timestamp"], "%Y-%m-%d %H:%M:%S")
        entrada = {
            "empleado_id":    fila["empleado_id"],
            "user_id":        fila["user_id"],
            "nombre":         fila["nombre"],
            "apellido":       fila["apellido"],
            "cargo":          fila["cargo"],
            "hora_entrada":   fichajes_hoy_f[0]["timestamp"],
            "ultimo_fichaje": fichajes_hoy_f[-1]["timestamp"],
            "cant_fichajes":  len(fichajes_hoy_f),
        }
        if ahora - ultimo_dt > timedelta(hours=umbral_h):
            ciclo_abierto.append({**entrada, "motivo": "fichaje_antiguo"})
        else:
            franco_con_fichaje.append(entrada)

    # — Empleados sin planificación: heurística de paridad —
    vistos = {p["user_id"] for p in con_plan}
    for eid, fichajes in fich_map.items():
        if eid in eids_con_plan or eid in eids_con_franco:
            continue
        fichajes_hoy_s = [f for f in fichajes if f["timestamp"][:10] == hoy]
        if not fichajes_hoy_s:
            continue
        # Salida de turno noche que cruza medianoche: mismo criterio que en la sección franco
        fichajes_ayer_s = [f for f in fichajes if f["timestamp"][:10] == ayer]
        if (all(f["timestamp"][11:16] < "06:00" for f in fichajes_hoy_s)
                and any(f["timestamp"][11:16] >= "17:00" for f in fichajes_ayer_s)):
            continue
        ultimo = fichajes_hoy_s[-1]
        if ultimo["user_id"] in vistos:
            continue
        ultimo_dt = datetime.strptime(ultimo["timestamp"], "%Y-%m-%d %H:%M:%S")
        entrada = {
            "empleado_id":    eid,
            "user_id":        ultimo["user_id"],
            "nombre":         ultimo["nombre"],
            "apellido":       ultimo["apellido"],
            "cargo":          ultimo["cargo"],
            "hora_entrada":   fichajes_hoy_s[0]["timestamp"],
            "ultimo_fichaje": ultimo["timestamp"],
            "cant_fichajes":  len(fichajes_hoy_s),
        }
        if ahora - ultimo_dt > timedelta(hours=umbral_h):
            ciclo_abierto.append({**entrada, "motivo": "fichaje_antiguo"})
        else:
            sin_plan.append(entrada)

    con_plan.sort(key=lambda p: (p["cargo"] or "", p["hora_entrada"]))
    sin_plan.sort(key=lambda p: (p["cargo"] or "", p["hora_entrada"]))
    ausentes.sort(key=lambda p: (p["cargo"] or "", p["hora_entrada_plan"]))
    franco_con_fichaje.sort(key=lambda p: (p["cargo"] or "", p["hora_entrada"]))
    ciclo_abierto.sort(key=lambda p: (p["cargo"] or "", p["hora_entrada"]))
    return {
        "con_planificacion":  con_plan,
        "sin_planificacion":  sin_plan,
        "ausentes":           ausentes,
        "franco_con_fichaje": franco_con_fichaje,
        "ciclo_abierto":      ciclo_abierto,
    }


if __name__ == "__main__":
    uvicorn.run("main:app", host=API_HOST, port=API_PORT, reload=False)
