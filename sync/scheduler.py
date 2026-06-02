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
_fecha_catchall: str | None = None
_fecha_barrido_manana: str | None = None
_mes_ultimo_sync_feriados: str | None = None
_fecha_ultimo_sync_hora: str | None = None
_mes_ultimo_cierre_auto: str | None = None
_fecha_ultimo_liberar_ids: str | None = None


def _run_sync():
    from sync.downloader import sync_attendances
    from sync.processor import process_pending
    logger.info("Iniciando ciclo de sincronización...")
    result = sync_attendances()
    process_pending()
    logger.info("Sincronización completada: %s", result)


def _check_y_evaluar():
    """
    Corre cada 5 minutos. Evalúa hoy en dos momentos por bloque:
      1. Al inicio: cuando hora_entrada ya pasó (estado provisorio durante el turno).
      2. Al cierre: ventana de 30 minutos después de hora_salida (resultado definitivo).
    """
    global _fecha_ultimo_reset

    ahora = datetime.now()
    hoy   = str(ahora.date())
    ahora_hhmm = ahora.strftime("%H:%M")

    # Resetear registro al cambiar de día
    if _fecha_ultimo_reset != hoy:
        _bloques_evaluados_hoy.clear()
        _fecha_ultimo_reset = hoy

    try:
        from db.database import db_session
        with db_session() as conn:
            bloques = conn.execute(
                "SELECT h.id AS horario_id, h.nombre, hb.bloque, hb.hora_entrada, hb.hora_salida "
                "FROM horarios_bloques hb JOIN horarios h ON h.id=hb.horario_id "
                "WHERE h.activo=1 AND hb.aplica=1"
            ).fetchall()
    except Exception as e:
        logger.warning("Error leyendo bloques para evaluación automática: %s", e)
        return

    evaluado_hoy = False

    for b in bloques:
        nombre_bloque = f"{b['nombre']}_b{b['bloque']}"

        # ── Evaluación al cierre (definitiva) ────────────────────────────────
        clave_cierre = f"{hoy}_{nombre_bloque}_cierre"
        if clave_cierre not in _bloques_evaluados_hoy:
            hh, mm = map(int, b["hora_salida"].split(":"))
            t_salida = ahora.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if t_salida <= ahora <= t_salida + timedelta(minutes=30):
                try:
                    from sync.evaluador import evaluar_fecha
                    resumen = evaluar_fecha(hoy)
                    logger.info("Evaluación cierre %s: %s", hoy, resumen)
                except Exception as e:
                    logger.error("Error en evaluación de cierre: %s", e)
                _bloques_evaluados_hoy.add(clave_cierre)

        # ── Evaluación al inicio (provisoria, 30 min después de hora_entrada) ─
        clave_inicio = f"{hoy}_{nombre_bloque}_inicio"
        if clave_inicio not in _bloques_evaluados_hoy and b["hora_entrada"]:
            hh_e, mm_e = map(int, b["hora_entrada"].split(":"))
            t_entrada = ahora.replace(hour=hh_e, minute=mm_e, second=0, microsecond=0)
            ventana_inicio_i = t_entrada + timedelta(minutes=30)
            ventana_fin_i    = t_entrada + timedelta(minutes=60)
            if ventana_inicio_i <= ahora <= ventana_fin_i:
                try:
                    from sync.evaluador import evaluar_fecha
                    resumen = evaluar_fecha(hoy, solo_horario_id=b["horario_id"])
                    logger.info("Evaluación inicio turno %s bloque %s/%s: %s",
                                hoy, b["nombre"], b["bloque"], resumen)
                except Exception as e:
                    logger.error("Error en evaluación de inicio: %s", e)
                _bloques_evaluados_hoy.add(clave_inicio)


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


def _get_dia_cierre_auto() -> int:
    """Lee el día de cierre automático de períodos desde la DB."""
    try:
        from db.database import db_session, get_config
        with db_session() as conn:
            return max(1, min(28, int(get_config(conn, "dia_cierre_periodo", "6"))))
    except Exception:
        return 6


def _cerrar_periodo_auto():
    """
    Cierra automáticamente el período del mes anterior el día configurado de cada mes a la madrugada.
    """
    global _mes_ultimo_cierre_auto
    ahora = datetime.now()

    if ahora.day != _get_dia_cierre_auto() or ahora.hour not in (1, 2):
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


def _horarios_por_tipo(hora_corte: str = "22:00") -> tuple[list, list]:
    """
    Devuelve (ids_diurnos, ids_nocturnos).
    Diurnos: todos los bloques tienen hora_salida <= hora_corte.
    Nocturnos: algún bloque cruza medianoche o tiene hora_salida > hora_corte.
    """
    try:
        from db.database import db_session
        with db_session() as conn:
            rows = conn.execute(
                "SELECT horario_id, MAX(hora_salida) AS max_salida, MAX(cruza_medianoche) AS cruza "
                "FROM horarios_bloques hb JOIN horarios h ON h.id=hb.horario_id "
                "WHERE h.activo=1 AND hb.aplica=1 "
                "GROUP BY horario_id"
            ).fetchall()
        diurnos   = [r["horario_id"] for r in rows if r["max_salida"] <= hora_corte and not r["cruza"]]
        nocturnos = [r["horario_id"] for r in rows if r["max_salida"] >  hora_corte or  r["cruza"]]
        return diurnos, nocturnos
    except Exception as e:
        logger.warning("Error clasificando horarios: %s", e)
        return [], []


def _evaluar_catchall():
    """
    Barrido nocturno (23:00): evalúa solo horarios diurnos del día actual.
    Los nocturnos aún están en curso — los cubre el barrido matutino del día siguiente.
    """
    global _fecha_catchall
    ahora = datetime.now()
    hoy   = str(ahora.date())

    if _fecha_catchall == hoy or ahora.hour != 23:
        return

    diurnos, _ = _horarios_por_tipo()
    if not diurnos:
        return

    try:
        from sync.evaluador import evaluar_fecha
        resumen = evaluar_fecha(hoy, horario_ids=diurnos)
        _fecha_catchall = hoy
        logger.info("Barrido nocturno (diurnos) %s: %s", hoy, resumen)
    except Exception as e:
        logger.error("Error en barrido nocturno: %s", e)


def _evaluar_barrido_manana():
    """
    Barrido matutino (06:00): evalúa horarios nocturnos del día anterior.
    Cubre turnos noche que cerraron pasada la medianoche.
    """
    global _fecha_barrido_manana
    ahora = datetime.now()
    hoy   = str(ahora.date())

    if _fecha_barrido_manana == hoy or ahora.hour != 6:
        return

    _, nocturnos = _horarios_por_tipo()
    if not nocturnos:
        _fecha_barrido_manana = hoy
        return

    ayer = str((ahora - timedelta(days=1)).date())
    try:
        from sync.evaluador import evaluar_fecha
        resumen = evaluar_fecha(ayer, horario_ids=nocturnos)
        _fecha_barrido_manana = hoy
        logger.info("Barrido matutino (nocturnos, ayer=%s): %s", ayer, resumen)
    except Exception as e:
        logger.error("Error en barrido matutino: %s", e)


def _evaluar_al_arranque():
    """
    Corre una vez al iniciar el servidor. Evalúa hoy y ayer (si es antes de las 06:00)
    para recuperar ventanas que se hayan perdido mientras el servidor estaba caído.
    """
    ahora = datetime.now()
    hoy   = str(ahora.date())
    try:
        from sync.evaluador import evaluar_fecha
        resumen = evaluar_fecha(hoy)
        logger.info("Evaluación al arranque (%s): %s", hoy, resumen)
        if ahora.hour < 6:
            ayer = str((ahora - timedelta(days=1)).date())
            resumen_ayer = evaluar_fecha(ayer)
            logger.info("Evaluación al arranque — ayer (%s): %s", ayer, resumen_ayer)
    except Exception as e:
        logger.error("Error en evaluación al arranque: %s", e)


def start_scheduler():
    def sync_loop():
        _run_sync()
        while True:
            time.sleep(_get_sync_interval())
            _run_sync()

    def eval_loop():
        # Esperar 2 min al inicio para que la DB esté lista, luego evaluar al arranque
        time.sleep(120)
        _evaluar_al_arranque()
        while True:
            _check_y_evaluar()
            _evaluar_catchall()
            time.sleep(300)  # cada 5 minutos

def _liberar_ids_dispositivo():
    """
    Corre una vez por día a la madrugada.
    Libera el user_id de empleados dados de baja hace más de 48 horas,
    para que el ID pueda ser reutilizado en el dispositivo.
    """
    global _fecha_ultimo_liberar_ids
    ahora = datetime.now()
    hoy   = str(ahora.date())

    if _fecha_ultimo_liberar_ids == hoy:
        return
    if ahora.hour not in (1, 2, 3):
        return

    try:
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
            if rows:
                for r in rows:
                    conn.execute("UPDATE empleados SET user_id=NULL, en_dispositivo=0 WHERE id=?", (r["id"],))
                    logger.info("ID dispositivo liberado: %s %s (id=%s, user_id=%s)",
                                r["apellido"], r["nombre"], r["id"], r["user_id"])
        _fecha_ultimo_liberar_ids = hoy
    except Exception as e:
        logger.error("Error en _liberar_ids_dispositivo: %s", e)


def start_scheduler():
    def sync_loop():
        _run_sync()
        while True:
            time.sleep(_get_sync_interval())
            _run_sync()

    def eval_loop():
        time.sleep(120)
        _evaluar_al_arranque()
        while True:
            _check_y_evaluar()
            _evaluar_catchall()
            time.sleep(300)

    def plan_loop():
        time.sleep(120)
        while True:
            _generar_planificacion_auto()
            _sync_feriados_auto()
            _sincronizar_hora_auto()
            _cerrar_periodo_auto()
            _evaluar_barrido_manana()
            _liberar_ids_dispositivo()
            time.sleep(3600)

    threading.Thread(target=sync_loop, daemon=True, name="sync-scheduler").start()
    threading.Thread(target=eval_loop, daemon=True, name="eval-scheduler").start()
    threading.Thread(target=plan_loop, daemon=True, name="plan-scheduler").start()

    logger.info(
        "Scheduler iniciado: sync cada %d min, evaluación automática cada 5 min, planificación diaria a la 01:00",
        SYNC_INTERVAL_MINUTES
    )
