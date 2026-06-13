"""
Ejecutar en la PC local (con acceso al ZKTeco).
Genera usuarios_zk.json en la misma carpeta.
Copiar ese archivo al proyecto en la otra máquina.
"""
import json
from zk import ZK

DEVICE_IP = "192.168.0.201"
DEVICE_PORT = 4370
DEVICE_TIMEOUT = 10
OUTPUT_FILE = "usuarios_zk.json"

zk = ZK(DEVICE_IP, port=DEVICE_PORT, timeout=DEVICE_TIMEOUT, password=0, force_udp=False, ommit_ping=False)
conn = None
try:
    print(f"Conectando a {DEVICE_IP}:{DEVICE_PORT}...")
    conn = zk.connect()
    users = conn.get_users()
    print(f"Usuarios leídos: {len(users)}")
    def fix_encoding(s):
        if not s:
            return ""
        try:
            return s.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return s

    data = [
        {
            "user_id": str(u.user_id).strip(),
            "name": fix_encoding((u.name or "").strip()),
            "privilege": u.privilege,
        }
        for u in users
        if str(u.user_id).strip()
    ]
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"Guardado en {OUTPUT_FILE} ({len(data)} registros)")
except Exception as e:
    print(f"Error: {e}")
finally:
    if conn:
        try:
            conn.disconnect()
        except Exception:
            pass
