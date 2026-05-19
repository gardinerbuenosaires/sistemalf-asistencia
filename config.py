import os
from pathlib import Path

# Dispositivo ZKTeco
DEVICE_IP = os.getenv("DEVICE_IP", "192.168.1.201")
DEVICE_PORT = int(os.getenv("DEVICE_PORT", 4370))
DEVICE_PASSWORD = int(os.getenv("DEVICE_PASSWORD", 0))
DEVICE_TIMEOUT = int(os.getenv("DEVICE_TIMEOUT", 10))

# Base de datos
DB_PATH = os.getenv("DB_PATH", "fichajes.db")

# Directorio de datos (fotos, etc.) — mismo directorio que la DB por defecto
DATA_DIR = Path(os.getenv("DB_PATH", "fichajes.db")).parent if os.getenv("DB_PATH") else Path("data")

# Sincronización automática (minutos)
SYNC_INTERVAL_MINUTES = int(os.getenv("SYNC_INTERVAL_MINUTES", 5))

# API
API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", 8000))
