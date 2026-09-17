"""
Cierra de una vez el circuito de ropa de las bajas anteriores a la puesta en
marcha del módulo.

Para qué sirve. La bandeja de bajas —Reportes → Bajas— muestra a quien se
fue y todavía figura con ropa sin devolver. El día del deploy arranca vacía,
porque no hay ninguna entrega cargada. Pero a medida que RRHH digitaliza el papel,
cada constancia vieja de alguien que ya se fue lo hace aparecer ahí, y esa ropa no
se va a devolver nunca: la persona se fue hace años y su liquidación ya se pagó.
Este script marca esas bajas como resueltas para que la bandeja quede con los que
se van de verdad a partir de ahora.

Se corre UNA vez, cuando termina la carga histórica. Los que se vayan después se
cierran de a uno desde la pantalla, que es donde se decide si devolvió o no.

El cierre no borra nada: deja asentado que esa baja quedó resuelta, con el
resultado «anterior al sistema», y se puede reabrir desde la ficha de la persona.
Además funciona como fecha de corte, así que si alguno vuelve a ser contratado,
lo que se le entregue después cuenta normalmente.

Es idempotente: a quien ya tiene un cierre vigente no se lo toca.

Uso:  python scripts/cerrar_bajas.py [--hasta AAAA-MM-DD] [--aplicar]

      --hasta   cierra a los que egresaron en esa fecha o antes.
                Por defecto, hoy: todas las bajas de la base.
      --aplicar sin esto hace una simulación y no escribe nada.
"""
import sys, os

# La raiz del proyecto, y pararse ahi: DB_PATH sale de config.py como ruta
# relativa, asi que se resuelve contra el directorio actual. Sin esto, correr el
# script parado en scripts\ busca la base en scripts\data\ y, si ahi existiera
# alguna, escribiria en la que no es. Va ANTES de importar config.
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.chdir(RAIZ)

from datetime import date, datetime
from pathlib import Path

from config import DB_PATH
from db.database import db_session

APLICAR = "--aplicar" in sys.argv


def _argumento(nombre, defecto=None):
    if nombre in sys.argv:
        i = sys.argv.index(nombre) + 1
        if i >= len(sys.argv):
            sys.exit(f"Falta el valor de {nombre}")
        return sys.argv[i]
    return defecto


HASTA = _argumento("--hasta", date.today().isoformat())
try:
    datetime.strptime(HASTA, "%Y-%m-%d")
except ValueError:
    sys.exit(f"Fecha inválida: {HASTA!r}. Se espera AAAA-MM-DD.")

OBSERVACION = _argumento("--observacion", "Baja anterior a la puesta en marcha del módulo")

# Sin DB_PATH el default es relativo al directorio actual, así que es fácil
# terminar escribiendo en una base que no es. Mostrarla antes de tocar nada.
if not Path(DB_PATH).exists():
    sys.exit(f"No encuentro la base en {Path(DB_PATH).resolve()}\n"
             f"Revisa que el sistema este instalado ahi, o defini DB_PATH.")

print(f"Base: {Path(DB_PATH).resolve()}")
print("MODO: " + ("APLICAR (escribe)" if APLICAR else "simulación (no escribe nada)"))
print(f"Cierra las bajas hasta el {HASTA[8:10]}/{HASTA[5:7]}/{HASTA[0:4]} inclusive")
print()

with db_session() as conn:
    existe = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='uniformes_cierres'").fetchone()
    if not existe:
        sys.exit("Esta base no tiene el módulo de uniformes. Actualizá el sistema primero.")

    # Los 'acceso' no son personal: existen solo para abrir puertas con la huella.
    pendientes = conn.execute(
        """SELECT e.id, e.apellido, e.nombre, e.fecha_egreso,
                  (SELECT COUNT(*) FROM uniformes_movimientos m
                    WHERE m.empleado_id = e.id AND m.tipo='entrega' AND m.estado='emitida') AS entregas
           FROM empleados e
           WHERE e.activo = 0 AND e.fecha_egreso IS NOT NULL AND e.fecha_egreso <= ?
             AND e.tipo != 'acceso'
             AND e.id NOT IN (SELECT empleado_id FROM uniformes_cierres WHERE estado='vigente')
           ORDER BY e.fecha_egreso DESC, e.apellido""", (HASTA,)).fetchall()

    ya_cerrados = conn.execute(
        "SELECT COUNT(*) FROM uniformes_cierres WHERE estado='vigente'").fetchone()[0]
    con_ropa = [p for p in pendientes if p["entregas"]]

    print(f"  Bajas sin cerrar     .................. {len(pendientes)}")
    print(f"    de ellos, con entregas registradas .. {len(con_ropa)}")
    print(f"  Ya tenían cierre (no se tocan) ........ {ya_cerrados}")
    print()

    if con_ropa:
        print("  Los que hoy figuran con ropa:")
        for p in con_ropa[:15]:
            f = p["fecha_egreso"][:10]
            print(f"    {f[8:10]}/{f[5:7]}/{f[0:4]}  {p['apellido']}, {p['nombre']}"
                  f"   ({p['entregas']} constancia{'s' if p['entregas'] != 1 else ''})")
        if len(con_ropa) > 15:
            print(f"    … y {len(con_ropa) - 15} más")
        print()

    if not pendientes:
        print("  No hay nada para cerrar.")
    elif not APLICAR:
        print(f"  Simulación: se cerrarían {len(pendientes)} bajas.")
        print("  Volvé a correrlo con --aplicar para escribir.")
    else:
        # La fecha del cierre es la del egreso, no la de hoy: así el corte queda
        # donde corresponde y, si la persona vuelve, lo nuevo cuenta desde ahí.
        conn.executemany(
            """INSERT INTO uniformes_cierres
                   (empleado_id, fecha, resultado, observacion, cerrado_por)
               VALUES (?,?,'previo_al_sistema',?,'Cierre masivo (script)')""",
            [(p["id"], p["fecha_egreso"][:10], OBSERVACION) for p in pendientes])
        print(f"  Listo: {len(pendientes)} bajas cerradas.")
        print("  La bandeja queda con los que se vayan a partir de ahora.")
