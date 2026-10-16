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
    # La copia nunca habla con el reloj real. Hay que apagar los dos lugares:
    # la clave vieja, que es de donde sale la siembra si la tabla todavía no
    # existe, y la tabla `dispositivos`, que es la que manda cuando ya está
    # migrada. Si la base de origen ya tiene equipos cargados, sin esto las
    # pruebas saldrían a buscar lectores de verdad en la red del restaurante.
    dst.execute("UPDATE configuracion SET valor='127.0.0.1' WHERE clave='device_ip'")
    dst.execute("UPDATE configuracion SET valor='0' WHERE clave='limpiar_dispositivo_auto'")
    # Las pruebas de accesos arman sus propios equipos y perfiles, asi que la
    # copia arranca sin los de produccion. Antes no hacia falta --esas tablas no
    # existian en la base de origen-- y dejo de ser cierto el dia que se
    # desplegó el modulo y se refresco la copia desde produccion: las pruebas
    # chocaban contra los ocho lectores reales.
    #
    # Borra tambien las huellas, y eso es aparte: son datos biometricos, y sin
    # esto cada corrida los copiaba a una carpeta temporal.
    for tabla in ("huellas", "accesos_operaciones", "accesos_excepciones",
                  "perfiles_dispositivos", "perfiles_acceso", "dispositivos"):
        try:
            dst.execute(f"DELETE FROM {tabla}")
        except sqlite3.OperationalError:
            pass           # copia anterior a esa tabla
    # Y los campos de accesos que viven en el legajo. Son del mismo modulo que
    # las tablas de arriba: las pruebas los escriben y verifican, y heredar los
    # de produccion hace fallar "arranca vacio" sobre algo que vino lleno.
    # `nivel_lector` vuelve a su default --cero, no administra-- y no a NULL,
    # que la columna no lo acepta.
    for columna, vacio in (("perfil_acceso_id", "NULL"), ("nivel_lector", "0"),
                           ("nombre_lector", "NULL"), ("user_id_anterior", "NULL"),
                           ("user_id_liberado_en", "NULL")):
        try:
            dst.execute(f"UPDATE empleados SET {columna} = {vacio}")
        except sqlite3.OperationalError:
            pass           # copia anterior a esa columna

    try:
        # Una IP distinta para cada uno, dentro del loopback. Ponerles la misma
        # a todos choca con el indice unico de (ip, puerto): con un solo equipo
        # cargado funcionaba, y dejo de funcionar el dia que la base de origen
        # vino de una produccion con las puertas ya cargadas. Todo 127.x.x.x es
        # loopback, asi que ninguna contesta igual.
        dst.execute("""UPDATE dispositivos
                          SET ip = '127.0.' || ((id / 256) % 256) || '.' || (id % 256)""")
    except sqlite3.OperationalError:
        pass  # copia anterior a la tabla; la crea init_db() y la siembra ya sale con la IP de arriba
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
