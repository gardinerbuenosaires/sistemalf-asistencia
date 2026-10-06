"""Copia la base de producción a la de pruebas, sin las huellas.

Para qué. Trabajar con datos de verdad es lo que hace que las pruebas sirvan:
una base inventada no tiene los casos raros que rompen las cosas. Pero desde que
las huellas se guardan en la base, copiarla tal cual se lleva los datos
biométricos de casi doscientas personas a una máquina de desarrollo.

Así que el script hace las dos cosas en un paso. Que el camino seguro sea el
camino fácil es más confiable que acordarse de hacer la segunda parte: si fueran
dos comandos, algún día alguien hace el primero y se va a almorzar.

Lo que NO hace, a propósito:

  · No toca producción. La abre en modo solo lectura y, si eso fallara, no
    sigue. Acá el riesgo no es perder la copia, es romper la que funciona.

  · No borra nada de la copia que no sean las huellas. Lo demás es justamente lo
    que se viene a buscar.

Uso:
    scripts\\copiar_base_a_pruebas.bat
    scripts\\copiar_base_a_pruebas.bat --destino otra\\ruta.db
"""
import os
import shutil
import sqlite3
import sys
from datetime import datetime

sys.stdout.reconfigure(errors="replace")
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(RAIZ)

ORIGEN = r"C:\ProgramData\SistemAlf\fichajes.db"
DESTINO = os.path.join(RAIZ, "data", "pruebas.db")

if "--origen" in sys.argv:
    ORIGEN = sys.argv[sys.argv.index("--origen") + 1]
if "--destino" in sys.argv:
    DESTINO = sys.argv[sys.argv.index("--destino") + 1]


def main():
    if not os.path.exists(ORIGEN):
        raise SystemExit(f"\n  No está la base de producción:\n    {ORIGEN}\n")

    print(f"\n  Origen : {ORIGEN}")
    print(f"  Destino: {DESTINO}")

    # Solo lectura, y con la copia propia de SQLite en vez de copiar el archivo:
    # la base está en WAL y el servidor puede estar escribiendo justo ahora. Un
    # copy crudo puede tomar un archivo a mitad de una transacción.
    try:
        origen = sqlite3.connect(f"file:{ORIGEN}?mode=ro", uri=True)
    except sqlite3.OperationalError as exc:
        raise SystemExit(f"\n  No se pudo abrir producción para leer: {exc}\n")

    if os.path.exists(DESTINO):
        viejo = f"{DESTINO}.{datetime.now():%Y%m%d-%H%M%S}.bak"
        shutil.move(DESTINO, viejo)
        print(f"\n  La anterior se guardó como {os.path.basename(viejo)}")

    os.makedirs(os.path.dirname(DESTINO) or ".", exist_ok=True)
    destino = sqlite3.connect(DESTINO)
    with destino:
        origen.backup(destino)
    origen.close()
    print("\n  Copia hecha.")

    # Y ahora lo que justifica que esto sea un script y no un copy.
    hay = destino.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='huellas'"
    ).fetchone()
    if not hay:
        print("  La copia no tiene tabla de huellas: nada que vaciar.")
    else:
        cuantas = destino.execute("SELECT COUNT(*) FROM huellas").fetchone()[0]
        destino.execute("DELETE FROM huellas")
        destino.commit()
        destino.execute("VACUUM")
        print(f"  Se borraron {cuantas} huella(s) de la copia.")
        quedan = destino.execute("SELECT COUNT(*) FROM huellas").fetchone()[0]
        if quedan:
            raise SystemExit(f"\n  QUEDARON {quedan} huellas en la copia. "
                             f"No uses este archivo.\n")
    destino.close()

    mb = os.path.getsize(DESTINO) / 1024 / 1024
    print(f"\n  Listo: {os.path.basename(DESTINO)}, {mb:.1f} MB, sin huellas.")
    print("  Producción no se tocó: se abrió en modo solo lectura.\n")


if __name__ == "__main__":
    main()
