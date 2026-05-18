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
_mes_ultimo_sync_feriados: str | None = None
_fecha_ultimo_sync_hora: str | None = None
_mes_ultimo_cierre_auto: str | None = None


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


def _sync_feriados_auto():
    """
    Sincroniza feriados nacionales del año en curso y del siguiente.
    Corre una vez al mes (día 1, madrugada). Tan liviano como un ping.
    """
    global _mes_ultimo_sync_feriados
    ahora = datetime.now()
    mes   = ahora.strftime("%Y-%m")

    if _mes_ultimo_sync_feriados == mes:
        return
    if ahora.hour not in (1, 2) or ahora.day != 1:
        return

    try:
        import httpx
        from api.feriados import importar_feriados
        for anio in (ahora.year, ahora.year + 1):
            r = importar_feriados(anio)
            logger.info(
                "Sync feriados %d: %d nuevos, %d eliminados (API: %d)",
                anio, r["nuevos"], r["eliminados"], r["total_api"]
            )
        _mes_ultimo_sync_feriados = mes
    except Exception as e:
        logger.error("Error en sync automático de feriados: %s", e)


def _sincronizar_hora_auto():
    """Sincroniza la hora del dispositivo una vez por día a la madrugada."""
    global _fecha_ultimo_sync_hora
    ahora = datetime.now()
    hoy   = str(ahora.date())

    if _fecha_ultimo_sync_hora == hoy:
        return
    if ahora.hour not in (1, 2):
        return

    try:
        from sync.downloader import sync_time
        result = sync_time()
        if result["ok"]:
            _fecha_ultimo_sync_hora = hoy
            logger.info("Hora del dispositivo sincronizada automáticamente")
        else:
            logger.warning("Fallo en sync automático de hora: %s", result["error"])
    except Exception as e:
        logger.error("Error en sync automático de hora: %s", e)


def _cerrar_periodo_auto():
    """
    Cierra automáticamente el período del mes anterior el día 6 de cada mes a la madrugada.
    Los sueldos se pagan el 5, por lo que a partir del 6 no deben hacerse correcciones libres.
    """
    global _mes_ultimo_cierre_auto
    ahora = datetime.now()

    if ahora.day != 6 or ahora.hour not in (1, 2):
        return

    mes_actual = ahora.strftime("%Y-%m")
    if _mes_ultimo_cierre_auto == mes_actual:
        return

    # Mes a cerrar: el anterior al actual
    primer_dia_mes = ahora.replace(day=1)
    mes_anterior = primer_dia_mes - timedelta(days=1)
    anio, mes = mes_anterior.year, mes_anterior.month

    try:
        from db.database import db_session
        from api.periodos_cerrados import es_periodo_cerrado
        with db_session() as conn:
            if es_periodo_cerrado(conn, anio, mes):
                logger.info("Cierre automático: período %d-%02d ya estaba cerrado", anio, mes)
                _mes_ultimo_cierre_auto = mes_actual
                return
            conn.execute(
                "INSERT INTO periodos_cerrados (anio, mes, cerrado_por) VALUES (?,?,NULL)",
                (anio, mes)
            )
        _mes_ultimo_cierre_auto = mes_actual
        logger.info("Cierre automático: período %d-%02d cerrado correctamente", anio, mes)
    except Exception as e:
        logger.error("Error en cierre automático de período: %s", e)


def _get_sync_interval() -> int:
    """Lee el intervalo de sync de la DB; cae al valor de config.py si falla."""
    try:
        from db.database import db_session, get_config
        with db_session() as conn:
            val = get_config(conn, "sync_interval_minutos", str(SYNC_INTERVAL_MINUTES))
            return max(1, int(val)) * 60
    except Exception:
        return SYNC_INTERVAL_MINUTES * 60


def start_scheduler():
    def sync_loop():
        _run_sync()
        while True:
            time.sleep(_get_sync_interval())
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
            _sync_feriados_auto()
            _sincronizar_hora_auto()
            _cerrar_periodo_auto()
            time.sleep(3600)  # revisar cada hora; cada función ejecuta solo en su ventana horaria

    threading.Thread(target=sync_loop, daemon=True, name="sync-scheduler").start()
    threading.Thread(target=eval_loop, daemon=True, name="eval-scheduler").start()
    threading.Thread(target=plan_loop, daemon=True, name="plan-scheduler").start()

    logger.info(
        "Scheduler iniciado: sync cada %d min, evaluación automática cada 5 min, planificación diaria a la 01:00",
        SYNC_INTERVAL_MINUTES
    )
