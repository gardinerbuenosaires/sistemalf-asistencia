"""Qué son los grupos y las franjas dentro de un lector. SOLO LECTURA.

Veníamos copiando el grupo de cada usuario sin saber qué significa, y en dos
puertas la gente está repartida entre el 0 y el 1 — ahí elegir uno es una
decisión sobre algo que nadie entiende.

La estructura del equipo, según el protocolo:

    FRANJAS   hasta 50, cada una con los horarios de los siete días
    GRUPOS    hasta 99, cada uno con hasta tres franjas asignadas
    USUARIO   pertenece a un grupo, y hereda sus reglas

O sea que el grupo es «qué reglas te aplica esta puerta», no «en qué puertas
estás». Esa segunda parte la decide el perfil del sistema. Son cosas distintas y
por eso conviven.

Lo que hace este script es pedirle al equipo las definiciones, que es lo único
que convierte la suposición en dato. pyzk declara los comandos y no implementa
ninguno, así que la respuesta se muestra cruda y se intenta un desarmado: si el
formato no es el esperado, se ven los bytes y se decide con eso.

Son comandos de LECTURA —RRQ, read request— y no modifican nada. Igual es la
primera vez que se los mandamos a estos equipos, así que conviene correrlo
cuando el local esté tranquilo.

Uso:  python scripts/diagnosticar_grupos.py IP
"""
import os
import sys
from struct import unpack

sys.stdout.reconfigure(errors="replace")
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.chdir(RAIZ)

BASE_PRODUCCION = r"C:\ProgramData\SistemAlf\fichajes.db"
BASE_LOCAL = os.path.join(RAIZ, "data", "pruebas.db")
if "--base" in sys.argv:
    os.environ["DB_PATH"] = sys.argv[sys.argv.index("--base") + 1]
elif not os.getenv("DB_PATH"):
    if os.path.exists(BASE_LOCAL):
        os.environ["DB_PATH"] = BASE_LOCAL
    elif os.path.exists(BASE_PRODUCCION):
        os.environ["DB_PATH"] = BASE_PRODUCCION

if len(sys.argv) < 2 or sys.argv[1].startswith("--"):
    print(__doc__)
    raise SystemExit(1)
IP = sys.argv[1]

DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")


def hhmm(valor):
    """Los horarios vienen como dos bytes: hora y minuto."""
    h, m = valor >> 8, valor & 0xFF
    return f"{h:02d}:{m:02d}" if h < 24 and m < 60 else f"?{valor:04X}"


def main():
    from zk import ZK, const

    clave = 0
    try:
        from db.database import db_solo_lectura
        with db_solo_lectura() as conn:
            fila = conn.execute(
                "SELECT password FROM dispositivos WHERE ip=?", (IP,)).fetchone()
            if fila and fila["password"]:
                clave = fila["password"]
    except Exception:
        pass

    conexion = ultimo = None
    for udp in (False, True):
        try:
            conexion = ZK(IP, port=4370, timeout=20, password=int(clave),
                          force_udp=udp, ommit_ping=True, encoding="latin-1").connect()
            print(f"\n  Conectado a {IP} por {'UDP' if udp else 'TCP'}")
            break
        except Exception as exc:
            ultimo = exc
    if conexion is None:
        raise SystemExit(f"  No contestó: {ultimo}")

    enviar = getattr(conexion, "_ZK__send_command")
    datos_de = lambda: getattr(conexion, "_ZK__data", b"")

    try:
        # Qué grupos se usan de verdad, que es lo que ya sabíamos mirar.
        usados = {}
        for u in conexion.get_users():
            g = str(u.group_id).strip() or "(vacío)"
            usados[g] = usados.get(g, 0) + 1
        print(f"\n  GRUPOS EN USO (de los usuarios cargados)")
        for g, n in sorted(usados.items()):
            print(f"     grupo {g:>3}: {n} persona(s)")
        if len(usados) == 1:
            print("     uno solo, así que copiarlo al cargar gente no decide nada")

        # Las definiciones. Acá es donde empieza lo que no sabemos.
        print(f"\n  FRANJAS DEFINIDAS EN EL EQUIPO")
        print(f"  (el equipo guarda hasta 50; se preguntan las primeras 8)")
        for n in range(1, 9):
            try:
                r = enviar(const.CMD_TZ_RRQ, bytes([n, 0, 0, 0]), 1032)
                crudo = datos_de()
                if not r.get("status") or not crudo:
                    print(f"     franja {n}: sin respuesta")
                    continue
                print(f"     franja {n}: {crudo[:28].hex(' ')}")
                if len(crudo) >= 28:
                    # Siete días, cuatro bytes cada uno: dos valores hora/minuto.
                    for i, dia in enumerate(DIAS):
                        desde, hasta = unpack("<HH", crudo[i * 4:i * 4 + 4])
                        if desde or hasta:
                            print(f"         {dia:<10} {hhmm(desde)} a {hhmm(hasta)}")
            except Exception as exc:
                print(f"     franja {n}: {type(exc).__name__}: {exc}")

        print(f"\n  QUÉ FRANJAS TIENE CADA GRUPO")
        for g in sorted(usados, key=lambda x: (not x.isdigit(), x)):
            if not g.isdigit():
                continue
            try:
                r = enviar(const.CMD_GRPTZ_RRQ, bytes([int(g), 0, 0, 0]), 1032)
                crudo = datos_de()
                if not r.get("status") or not crudo:
                    print(f"     grupo {g}: sin respuesta")
                    continue
                print(f"     grupo {g}: {crudo[:16].hex(' ')}")
            except Exception as exc:
                print(f"     grupo {g}: {type(exc).__name__}: {exc}")

        print(f"\n  Si los bytes salen todos en cero o el equipo no contesta, lo más")
        print(f"  probable es que este modelo no guarde franjas y el grupo sea solo")
        print(f"  una etiqueta. Eso también es una respuesta.")
    finally:
        try:
            conexion.disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    main()
