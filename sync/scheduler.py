"""
Scheduler de sincronización y evaluación automática.

Dos ciclos independientes:
  1. Sincronización con ZKTeco (cada SYNC_INTERVAL_MINUTES)
  2. Evaluación automática: cada 5 minutos revisa si algún bloque de horario
     cerró su salida en los últimos 30 minutos → evalúa el día actual
"""
import logging
import threading
import time
from datetime import datetime, timedelta
from config import SYNC_INTERVAL_MINUTES

logger = logging.getLogger(__name__)

# Registro de bloques ya evaluados hoy para no re-ejecutar innecesariamente
_bloques_evaluados_hoy: set[str] = set()
_fecha_ultimo_reset = None
_fecha_ultima_generacion: str | None = None


def _run_sync():
    from sync.downloader import sync_attendances
    from sync.processor import process_pending
    logger.info("Iniciando ciclo de sincronización...")
    result = sync_attendances()
    process_pending()
    logger.info("Sincronización completada: %s", result)


def _check_y_evaluar():
    """
    Corre cada 5 minutos. Si la hora actual está dentro de los 30 minutos
    posteriores a la hora de salida de algún bloque activo, evalúa hoy.
    """
    global _fecha_ultimo_reset

    ahora = datetime.now()
    hoy   = str(ahora.date())

    # Resetear registro al cambiar de día
    if _fecha_ultimo_reset != hoy:
        _bloques_evaluados_hoy.clear()
        _fecha_ultimo_reset = hoy

    try:
        from db.database import db_session
        with db_session() as conn:
            bloques = conn.execute(
                "SELECT h.nombre, hb.bloque, hb.hora_salida "
                "FROM horarios_bloques hb JOIN horarios h ON h.id=hb.horario_id "
                "WHERE h.activo=1"
            ).fetchall()
    except Exception as e:
        logger.warning("Error leyendo bloques para evaluación automática: %s", e)
        return

    for b in bloques:
        clave = f"{hoy}_{b['nombre']}_b{b['bloque']}"
        if clave in _bloques_evaluados_hoy:
            continue

        hh, mm = map(int, b["hora_salida"].split(":"))
        t_salida = ahora.replace(hour=hh, minute=mm, second=0, microsecond=0)
        ventana_inicio = t_salida
        ventana_fin    = t_salida + timedelta(minutes=30)

        if ventana_inicio <= ahora <= ventana_fin:
            logger.info(
                "Evaluación automática: bloque %s/%s cerró a las %s",
                b["nombre"], b["bloque"], b["hora_salida"]
            )
            try:
                from sync.evaluador import evaluar_fecha
                resumen = evaluar_fecha(hoy)
                logger.info("Evaluación automática %s: %s", hoy, resumen)
                _bloques_evaluados_hoy.add(clave)
            except Exception as e:
                logger.error("Error en evaluación automática: %s", e)


def _generar_planificacion_auto():
    """
    Genera planificación automática para la semana actual y la siguiente.
    Corre una vez por día a la madrugada. Respeta entradas manuales.
    """
    global _fecha_ultima_generacion
    ahora = datetime.now()
    hoy   = str(ahora.date())

    if _fecha_ultima_generacion == hoy:
        return
    if ahora.hour not in (1, 2):
        return

    try:
        from api.calendarios import generar_semana
        totales = {"generados": 0, "omitidos": 0}
        for w in range(5):  # semana actual + 4 semanas más (~1 mes)
            fecha = str((ahora + timedelta(weeks=w)).date())
            r = generar_semana(fecha)
            totales["generados"] += r.get("generados", 0)
            totales["omitidos"]  += r.get("omitidos", 0)
        _fecha_ultima_generacion = hoy
        logger.info("Planificación automática desde %s: %s", hoy, totales)
    except Exception as e:
        logger.error("Error en generación automática de planificación: %s", e)


def start_scheduler():
    sync_interval = SYNC_INTERVAL_MINUTES * 60

    def sync_loop():
        _run_sync()
        while True:
            time.sleep(sync_interval)
            _run_sync()

    def eval_loop():
        # Esperar 2 min al inicio para que la DB esté lista
        time.sleep(120)
        while True:
            _check_y_evaluar()
            time.sleep(300)  # cada 5 minutos

    def plan_loop():
        time.sleep(120)
        while True:
            _generar_planificacion_auto()
            time.sleep(3600)  # revisar cada hora, ejecuta solo entre 01:00 y 02:59

    threading.Thread(target=sync_loop, daemon=True, name="sync-scheduler").start()
    threading.Thread(target=eval_loop, daemon=True, name="eval-scheduler").start()
    threading.Thread(target=plan_loop, daemon=True, name="plan-scheduler").start()

    logger.info(
        "Scheduler iniciado: sync cada %d min, evaluación automática cada 5 min, planificación diaria a la 01:00",
        SYNC_INTERVAL_MINUTES
    )
