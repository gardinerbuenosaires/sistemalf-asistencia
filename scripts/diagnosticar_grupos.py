"""Qué horarios tiene un lector y quién está en cada grupo. SOLO LECTURA.

Veníamos copiando el grupo de cada usuario sin saber qué significa, y en dos
puertas la gente está repartida entre el 0 y el 1 — ahí elegir uno es una
decisión sobre algo que nadie mira.

La estructura del equipo, según el protocolo:

    FRANJAS   hasta 50, cada una con los horarios de los siete días
    GRUPOS    hasta 99, cada uno con hasta tres franjas asignadas
    USUARIO   pertenece a un grupo, y hereda sus reglas

O sea que el grupo es «en qué horario abrís esta puerta», no «qué puertas
abrís». Esa segunda parte la decide el perfil del sistema. Son cosas distintas y
por eso conviven.

Lo que hace este script es pedirle al equipo las definiciones, que es lo único
que convierte la suposición en dato. pyzk declara los comandos y no implementa
ninguno, así que se muestran los bytes crudos además del desarmado: si el
formato no es el esperado, se ve y se decide con eso.

Además lista quién está en cada grupo. Ese es el control que no depende de
ninguna interpretación: si en los dos grupos hay gente que sabés que abre esa
puerta todos los días, el grupo no está filtrando nada.

Son comandos de LECTURA —RRQ, read request— y no modifican nada. Igual es la
primera vez que se los mandamos a estos equipos, así que conviene correrlo
cuando el local esté tranquilo.

Uso:  python scripts/diagnosticar_grupos.py IP
"""
import os
import sys

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

from sync.franjas import (DIAS, abierta, franjas_del_grupo, rango, semana,
                          texto_dia, vacia)

CUANTAS_FRANJAS = 8


def clave_del_equipo():
    """La contraseña del lector, si el sistema la tiene guardada."""
    try:
        from db.database import db_solo_lectura
        with db_solo_lectura() as conn:
            fila = conn.execute(
                "SELECT password FROM dispositivos WHERE ip=?", (IP,)).fetchone()
            if fila and fila["password"]:
                return int(fila["password"])
    except Exception:
        pass
    return 0


def conectar():
    from zk import ZK

    clave, ultimo = clave_del_equipo(), None
    for udp in (False, True):
        try:
            conexion = ZK(IP, port=4370, timeout=20, password=clave,
                          force_udp=udp, ommit_ping=True,
                          encoding="latin-1").connect()
            print(f"\n  Conectado a {IP} por {'UDP' if udp else 'TCP'}")
            return conexion
        except Exception as exc:
            ultimo = exc
    raise SystemExit(f"  No contestó: {ultimo}")


def quien_en_cada_grupo(conexion):
    """Lo que se puede confrontar con lo que ya sabés del local."""
    gente = {}
    for u in conexion.get_users():
        g = str(u.group_id).strip() or "(vacío)"
        gente.setdefault(g, []).append(
            (str(u.user_id).strip(), (u.name or "").strip()))
    print("\n  QUIÉN ESTÁ EN CADA GRUPO")
    for g in sorted(gente):
        print(f"\n     grupo {g}: {len(gente[g])} persona(s)")
        for numero, nombre in sorted(gente[g], key=lambda x: x[0].rjust(10)):
            print(f"        {numero:>8}  {nombre}")
    if len(gente) == 1:
        print("\n     uno solo, así que copiarlo al cargar gente no decide nada")
    return gente


def mostrar_franjas(enviar, datos_de, const):
    """Las primeras franjas, agrupadas por definición: casi siempre son iguales."""
    print("\n  FRANJAS DEFINIDAS EN EL EQUIPO")
    print(f"  (el equipo guarda hasta 50; se preguntan las primeras {CUANTAS_FRANJAS})")

    franjas, iguales = {}, {}
    for n in range(1, CUANTAS_FRANJAS + 1):
        try:
            respuesta = enviar(const.CMD_TZ_RRQ, bytes([n, 0, 0, 0]), 1032)
            crudo = datos_de()
            if not respuesta.get("status") or not crudo:
                franjas[n] = None
                continue
        except Exception as exc:
            print(f"     franja {n}: {type(exc).__name__}: {exc}")
            franjas[n] = None
            continue
        leida = semana(crudo)
        franjas[n] = {"semana": leida, "hex": crudo[:28].hex(" ")}
        iguales.setdefault(leida, []).append(n)

    for leida, numeros in iguales.items():
        print(f"\n     {rango(numeros)}:")
        if abierta(leida):
            print("        los siete días de 00:00 a 23:59 — no restringe nada")
        elif vacia(leida):
            print("        todo en cero — sin horarios cargados")
        else:
            for nombre_dia, valores in zip(DIAS, leida):
                print(f"        {nombre_dia:<10} {texto_dia(valores)}")
        print(f"        bytes: {franjas[numeros[0]]['hex']}")

    sin_respuesta = [n for n, f in franjas.items() if f is None]
    if sin_respuesta:
        print(f"\n     {rango(sin_respuesta)}: sin respuesta, probablemente "
              f"no definidas")
    return franjas


def mostrar_grupos(enviar, datos_de, const, gente):
    print("\n  QUÉ FRANJAS USA CADA GRUPO")
    usa = {}
    for g in sorted(gente, key=lambda x: (not x.isdigit(), x)):
        if not g.isdigit():
            continue
        try:
            respuesta = enviar(const.CMD_GRPTZ_RRQ, bytes([int(g), 0, 0, 0]), 1032)
            crudo = datos_de()
            if not respuesta.get("status") or not crudo:
                print(f"     grupo {g}: sin respuesta")
                usa[g] = []
                continue
        except Exception as exc:
            print(f"     grupo {g}: {type(exc).__name__}: {exc}")
            continue
        suyas, sin_identificar = franjas_del_grupo(crudo)
        usa[g] = suyas
        print(f"     grupo {g}: {crudo[:16].hex(' ')}")
        if suyas:
            cuales = ", ".join(str(n) for n in sorted(set(suyas)))
            print(f"        usa la franja {cuales}")
        else:
            print("        sin franja asignada")
        if sin_identificar is not None:
            print(f"        (primer campo: {sin_identificar}, sin identificar)")
    return usa


def conclusion(franjas, usa):
    """Lo que sale de lo leído, no de lo que suponíamos antes de leer."""
    print("\n  QUÉ SIGNIFICA ESTO")
    restringe = [(g, n) for g, suyas in usa.items() for n in suyas
                 if franjas.get(n) and not abierta(franjas[n]["semana"])]
    if not usa:
        print("     El equipo no contestó por los grupos. Si tampoco contestó")
        print("     por las franjas, lo más probable es que este modelo no las")
        print("     guarde y el grupo sea solo una etiqueta.")
    elif restringe:
        cuales = ", ".join(f"grupo {g} por la franja {n}" for g, n in restringe)
        print(f"     Hay grupos con horario: {cuales}")
        print("     Acá el grupo SÍ decide cuándo abre cada uno, así que elegirlo")
        print("     al cargar gente importa, y conviene que salga del perfil.")
    else:
        print("     Todas las franjas en juego están abiertas de 00:00 a 23:59 los")
        print("     siete días, y los grupos sin franja asignada no restringen nada.")
        print("     O sea que HOY el grupo no cambia quién abre esta puerta: es una")
        print("     etiqueta heredada de cómo se fue cargando la gente.")
        print("     Sigue siendo un botón que alguien puede apretar desde el menú")
        print("     del equipo, y por eso se copia el que ya está en uso ahí.")
    print("\n     Para confirmarlo sin creerle a los bytes: mirá los grupos de")
    print("     arriba. Si en los dos hay gente que abre esta puerta todos los")
    print("     días, el grupo no está filtrando nada.")


def main():
    from zk import const

    conexion = conectar()
    enviar = getattr(conexion, "_ZK__send_command")
    datos_de = lambda: getattr(conexion, "_ZK__data", b"")
    try:
        gente = quien_en_cada_grupo(conexion)
        franjas = mostrar_franjas(enviar, datos_de, const)
        usa = mostrar_grupos(enviar, datos_de, const, gente)
        conclusion(franjas, usa)
    finally:
        try:
            conexion.disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    main()
