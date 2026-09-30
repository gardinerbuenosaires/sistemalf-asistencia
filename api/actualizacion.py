"""Actualizar el sistema desde Configuración, sin entrar a la mini PC.

Hace lo mismo que scripts/update.bat salvo el reinicio: el .bat no se puede
lanzar desde acá porque al reiniciar el servicio Windows cierra también todo lo
que la aplicación lanzó, y el .bat moriría a mitad de camino dejando el servicio
parado. En cambio, la aplicación hace los pasos ella misma y al final se cierra;
NSSM la vuelve a abrir (AppRestartDelay 3000 en install.ps1) con el código nuevo.

Pasos: respaldo de la base → descarga (fast-forward a origin/main) → librerías →
prueba de arranque sobre una copia de la base → cierre. Si algo falla antes del
cierre se vuelve al commit anterior y el sistema sigue como estaba.
"""
import logging
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from auth.actividad import conectados
from auth.core import require_permiso
from config import DATA_DIR, DB_PATH
from db.database import db_session, get_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/actualizacion", tags=["actualizacion"])

REPO = Path(__file__).resolve().parent.parent
RESPALDOS_DIR = DATA_DIR / "respaldos_actualizacion"
RESPALDOS_A_GUARDAR = 5
RAMA = "main"

# Lo mismo que hace el arranque (main.lifespan) antes de levantar el servidor.
# Corre sobre una copia de la base, así que las migraciones nuevas se prueban
# contra datos reales sin tocarlos.
_PRUEBA_ARRANQUE = (
    "import main; "
    "from db.database import init_db; init_db(); "
    "from auth.core import ensure_admin; ensure_admin()"
)

_lock = threading.Lock()


# ── Git ───────────────────────────────────────────────────────────────────────

def _git_exe() -> str | None:
    """El servicio corre como LocalSystem y puede no tener git en su PATH."""
    encontrado = shutil.which("git")
    if encontrado:
        return encontrado
    for p in (r"C:\Program Files\Git\cmd\git.exe", r"C:\Program Files (x86)\Git\cmd\git.exe"):
        if os.path.exists(p):
            return p
    return None


def _git(*args, timeout=60) -> subprocess.CompletedProcess:
    exe = _git_exe()
    if not exe:
        raise RuntimeError("No se encontró git en esta máquina")
    # safe.directory: la carpeta la clonó un Administrador y el servicio corre
    # como otro usuario (LocalSystem); sin esto git se niega a trabajar en ella.
    return subprocess.run(
        [exe, "-c", f"safe.directory={REPO.as_posix()}", *args],
        cwd=REPO, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=timeout,
    )


def _git_ok(*args, timeout=60) -> str:
    r = _git(*args, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip() or f"git {' '.join(args)} falló")
    return r.stdout.strip()


def _commits(rango: str) -> list[dict]:
    salida = _git_ok("log", rango, "--format=%h%x1f%cI%x1f%s")
    commits = []
    for linea in salida.splitlines():
        partes = linea.split("\x1f")
        if len(partes) == 3:
            commits.append({"hash": partes[0], "fecha": partes[1][:16].replace("T", " "),
                            "mensaje": partes[2]})
    return commits


# ── Entorno ───────────────────────────────────────────────────────────────────

def _corre_como_servicio() -> bool:
    """Solo bajo NSSM hay quien reabra la aplicación después de cerrarla.

    Corriendo a mano (python main.py, start.bat) el sistema quedaría apagado.
    ACTUALIZACION_FORZAR=1 lo saltea, solo para las pruebas.
    """
    if os.environ.get("ACTUALIZACION_FORZAR") == "1":
        return True
    if os.name != "nt":
        return False
    try:
        r = subprocess.run(
            ["tasklist", "/FI", f"PID eq {os.getppid()}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, errors="replace", timeout=10,
        )
        return "nssm.exe" in r.stdout.lower()
    except Exception:
        return False


def _cerrar_para_reiniciar():
    """NSSM reabre la aplicación al verla cerrarse. os._exit y no sys.exit: hay
    hilos (scheduler, uvicorn) que no terminarían solos."""
    logger.warning("Actualización lista: cerrando para que el servicio reabra con la versión nueva")
    logging.shutdown()
    os._exit(0)


# ── Registro ──────────────────────────────────────────────────────────────────

def _registrar(aid: int, **campos):
    sets = ", ".join(f"{k}=?" for k in campos)
    with db_session() as conn:
        conn.execute(f"UPDATE actualizaciones SET {sets} WHERE id=?", (*campos.values(), aid))


def _terminar(aid: int, estado: str, detalle: str | None = None, **campos):
    _registrar(aid, estado=estado, detalle=detalle,
               finalizada_en=datetime.now().strftime("%Y-%m-%d %H:%M:%S"), **campos)


def cerrar_pendientes_al_arrancar():
    """Lo llama main.lifespan. Una fila 'reiniciando' significa que la versión
    nueva arrancó; una 'en_curso' que el proceso murió a mitad de camino."""
    try:
        actual = _git_ok("rev-parse", "--short", "HEAD")
    except Exception:
        actual = None
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with db_session() as conn:
        for fila in conn.execute(
            "SELECT id, estado, version_hasta FROM actualizaciones WHERE estado IN ('reiniciando','en_curso')"
        ).fetchall():
            if fila["estado"] == "reiniciando" and (actual is None or actual == fila["version_hasta"]):
                conn.execute("UPDATE actualizaciones SET estado='ok', finalizada_en=? WHERE id=?",
                             (ahora, fila["id"]))
            else:
                conn.execute(
                    "UPDATE actualizaciones SET estado='error', finalizada_en=?, "
                    "detalle=COALESCE(detalle, 'Se interrumpió antes de terminar. Revisar la versión instalada.') "
                    "WHERE id=?", (ahora, fila["id"]))


# ── Pasos ─────────────────────────────────────────────────────────────────────

def _respaldar_base() -> Path:
    RESPALDOS_DIR.mkdir(parents=True, exist_ok=True)
    destino = RESPALDOS_DIR / f"fichajes_{datetime.now():%Y%m%d_%H%M%S}.db"
    src = sqlite3.connect(f"file:{Path(DB_PATH).resolve().as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(destino)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    viejos = sorted(RESPALDOS_DIR.glob("fichajes_*.db"))[:-RESPALDOS_A_GUARDAR]
    for v in viejos:
        try:
            v.unlink()
        except OSError:
            pass
    return destino


def _instalar_librerias():
    r = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", "requirements.txt",
         "--quiet", "--disable-pip-version-check"],
        cwd=REPO, capture_output=True, text=True, errors="replace", timeout=900,
    )
    if r.returncode != 0:
        raise RuntimeError("No se pudieron instalar las librerías:\n" + _ultimas_lineas(r.stderr or r.stdout))


def _probar_arranque(respaldo: Path):
    with tempfile.TemporaryDirectory(prefix="sistemalf_prueba_") as tmp:
        copia = Path(tmp) / "fichajes.db"
        shutil.copy2(respaldo, copia)
        env = dict(os.environ, DB_PATH=str(copia), PYTHONIOENCODING="utf-8")
        r = subprocess.run(
            [sys.executable, "-c", _PRUEBA_ARRANQUE],
            cwd=REPO, env=env, capture_output=True, text=True, errors="replace", timeout=600,
        )
    if r.returncode != 0:
        raise RuntimeError("La versión nueva no arranca:\n" + _ultimas_lineas(r.stderr or r.stdout))


def _ultimas_lineas(texto: str, n: int = 12) -> str:
    return "\n".join((texto or "").strip().splitlines()[-n:])


def _actualizar(aid: int, version_desde_completa: str):
    movido = False
    try:
        _registrar(aid, paso="respaldo")
        respaldo = _respaldar_base()

        _registrar(aid, paso="descarga")
        _git_ok("fetch", "--quiet", "origin", RAMA, timeout=120)
        _git_ok("merge", "--ff-only", f"origin/{RAMA}")
        movido = True
        _registrar(aid, version_hasta=_git_ok("rev-parse", "--short", "HEAD"))

        _registrar(aid, paso="librerias")
        _instalar_librerias()

        _registrar(aid, paso="prueba")
        _probar_arranque(respaldo)

        _registrar(aid, paso="reinicio", estado="reiniciando")
    except Exception as e:
        detalle = str(e)
        if movido:
            try:
                _git_ok("reset", "--hard", version_desde_completa)
                detalle += "\n\nSe volvió a la versión anterior. El sistema sigue funcionando como antes."
            except Exception as e2:
                detalle += f"\n\nNO se pudo volver a la versión anterior: {e2}"
        logger.error("Actualización %s falló: %s", aid, detalle)
        _terminar(aid, "error", detalle)
        _lock.release()
        return
    # Un respiro para que la pantalla alcance a leer 'reiniciando'.
    threading.Timer(2.0, _cerrar_para_reiniciar).start()


# ── API ───────────────────────────────────────────────────────────────────────

@router.get("/estado")
def estado(buscar: bool = True, _user=Depends(require_permiso("actualizacion", "procesar"))):
    """Versión instalada, cambios pendientes en GitHub e historial.

    buscar=False no consulta GitHub: es lo que usa la pantalla mientras sigue
    una actualización en curso.
    """
    res = {
        "equipo": socket.gethostname(),
        "servicio": _corre_como_servicio(),
        "git": bool(_git_exe()),
        "en_curso": _lock.locked(),
        "instalada": None,
        "pendientes": [],
        "error_busqueda": None,
    }
    if res["git"]:
        try:
            res["instalada"] = (_commits("-1") or [None])[0]
            if buscar:
                _git_ok("fetch", "--quiet", "origin", RAMA, timeout=60)
            res["pendientes"] = _commits(f"HEAD..origin/{RAMA}")
        except Exception as e:
            res["error_busqueda"] = str(e)
    with db_session() as conn:
        res["historial"] = [dict(r) for r in conn.execute(
            "SELECT * FROM actualizaciones ORDER BY id DESC LIMIT 10").fetchall()]
        res["conectados"] = _conectados(conn, _user)
    return res


def _conectados(conn, user: dict) -> list[dict]:
    """Quién tiene el sistema abierto ahora (auth/actividad.py), con nombre."""
    try:
        minutos = int(get_config(conn, "inactividad_minutos", "10"))
    except (TypeError, ValueError):
        minutos = 10
    lista = conectados(max(1, minutos) * 60)
    if not lista:
        return []
    ids = [c["usuario_id"] for c in lista]
    nombres = {r["id"]: r["nombre"] for r in conn.execute(
        f"SELECT id, nombre FROM usuarios WHERE id IN ({','.join('?' * len(ids))})", ids)}
    yo = int(user.get("sub") or 0)
    return [{**c, "nombre": nombres.get(c["usuario_id"], f"usuario {c['usuario_id']}"),
             "soy_yo": c["usuario_id"] == yo} for c in lista]


@router.post("/actualizar")
def actualizar(user=Depends(require_permiso("actualizacion", "procesar"))):
    if not _corre_como_servicio():
        raise HTTPException(409, "Este sistema no está corriendo como servicio de Windows: "
                                 "nadie lo volvería a abrir después de actualizar. Usá update.bat.")
    if not _lock.acquire(blocking=False):
        raise HTTPException(409, "Ya hay una actualización en curso")
    try:
        desde = _git_ok("rev-parse", "HEAD")
        _git_ok("fetch", "--quiet", "origin", RAMA, timeout=60)
        if not _commits(f"HEAD..origin/{RAMA}"):
            raise HTTPException(409, "Esta máquina ya tiene la última versión")
        uid = int(user.get("sub") or 0) or None
        with db_session() as conn:
            fila = conn.execute("SELECT nombre FROM usuarios WHERE id=?", (uid,)).fetchone()
            aid = conn.execute(
                "INSERT INTO actualizaciones (usuario_id, usuario_nombre, version_desde, estado, paso) "
                "VALUES (?,?,?,'en_curso','inicio')",
                (uid, fila["nombre"] if fila else None, desde[:7]),
            ).lastrowid
    except HTTPException:
        _lock.release()
        raise
    except Exception as e:
        _lock.release()
        raise HTTPException(500, f"No se pudo iniciar la actualización: {e}")
    # El lock lo suelta el hilo si falla; si sale bien, el proceso se cierra.
    threading.Thread(target=_actualizar, args=(aid, desde), daemon=True).start()
    return {"ok": True, "id": aid}
