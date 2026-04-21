"""
Ejecuta la sincronización y el procesamiento en intervalos regulares.
Corre en un hilo daemon para no bloquear el servidor FastAPI.
"""
import logging
import threading
import time
from config import SYNC_INTERVAL_MINUTES
from sync.downloader import sync_attendances
from sync.processor import process_pending

logger = logging.getLogger(__name__)


def _run_cycle():
    logger.info("Iniciando ciclo de sincronización...")
    sync_result = sync_attendances()
    if sync_result.get("nuevos", 0) > 0 or True:
        process_pending()
    logger.info("Ciclo completado: %s", sync_result)


def start_scheduler():
    interval_seconds = SYNC_INTERVAL_MINUTES * 60

    def loop():
        # Primera ejecución inmediata al iniciar
        _run_cycle()
        while True:
            time.sleep(interval_seconds)
            _run_cycle()

    thread = threading.Thread(target=loop, daemon=True, name="sync-scheduler")
    thread.start()
    logger.info(
        "Scheduler iniciado: sincronización cada %d minutos", SYNC_INTERVAL_MINUTES
    )
