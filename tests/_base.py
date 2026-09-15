"""Arranque común de las pruebas: cada una trabaja sobre una COPIA de la base.

Nunca sobre la base real. La base de origen se abre en solo lectura y se copia
con la API de backup de SQLite —que sale consistente aunque haya transacciones
en el WAL— a una carpeta temporal que se borra al terminar. DB_PATH se fija
antes de importar la app, así el sistema entero apunta a la copia.

Origen: la variable DB_ORIGEN, o si no está, data/fichajes.db.
"""
import atexit
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def preparar(migrar=True):
    """Copia la base y deja todo apuntando a la copia. Devuelve su ruta.

    Con migrar=True además corre lo mismo que el arranque del sistema
    (init_db y ensure_admin) y, si la base no tiene catálogo de uniformes, lo
    siembra: así las pruebas funcionan también sobre una base que todavía no
    tiene el módulo, como la de producción antes de desplegarlo.
    """
    origen = os.environ.get("DB_ORIGEN") or os.path.join(RAIZ, "data", "fichajes.db")
    if not os.path.exists(origen):
        sys.exit(f"No existe la base de origen: {origen}")

    carpeta = tempfile.mkdtemp(prefix="prueba_sistemalf_")
    atexit.register(shutil.rmtree, carpeta, True)
    destino = os.path.join(carpeta, "fichajes.db")
    src = sqlite3.connect(f"file:{origen}?mode=ro", uri=True)
    dst = sqlite3.connect(destino)
    src.backup(dst)
    src.close()
    # La copia nunca habla con el reloj real.
    dst.execute("UPDATE configuracion SET valor='127.0.0.1' WHERE clave='device_ip'")
    dst.execute("UPDATE configuracion SET valor='0' WHERE clave='limpiar_dispositivo_auto'")
    dst.commit()
    dst.close()

    os.environ["DB_PATH"] = destino
    os.environ.setdefault("SECRET_KEY", "pruebas-locales")
    if RAIZ not in sys.path:
        sys.path.insert(0, RAIZ)
    os.chdir(RAIZ)
    logging.disable(logging.INFO)

    if migrar:
        from db.database import init_db
        from auth.core import ensure_admin
        init_db()
        ensure_admin()
        c = sqlite3.connect(destino)
        vacio = c.execute("SELECT COUNT(*) FROM uniformes_elementos").fetchone()[0] == 0
        # En una base que no tenía el módulo la migración deja la bandera
        # apagada, que es lo correcto para producción. Las pruebas del módulo
        # lo ejercitan, así que en la copia se prende. uniformes_migracion.py
        # arranca sin migrar justamente para verificar que nazca apagada.
        c.execute("UPDATE configuracion SET valor='1' WHERE clave='uniformes_activo'")
        c.commit()
        c.close()
        if vacio:
            subprocess.run(
                [sys.executable, os.path.join(RAIZ, "scripts", "sembrar_uniformes.py"), "--aplicar"],
                env=dict(os.environ, PYTHONIOENCODING="utf-8"), capture_output=True, check=True,
            )
    return destino


def token_sistema():
    """(token, rol_id) de un usuario activo con rol Sistema de la copia.

    No se asume que sea el usuario 1: en otra instalación puede no existir.
    """
    from auth.core import create_token
    c = sqlite3.connect(os.environ["DB_PATH"])
    c.row_factory = sqlite3.Row
    u = c.execute("""SELECT u.id, u.email, u.rol_id FROM usuarios u
                     JOIN roles r ON r.id = u.rol_id
                     WHERE lower(r.nombre) = 'sistema' AND u.activo = 1
                     ORDER BY u.id LIMIT 1""").fetchone()
    c.close()
    if not u:
        sys.exit("La base no tiene ningún usuario activo con rol Sistema")
    return create_token(u["id"], u["email"], u["rol_id"], "sistema"), u["rol_id"]
