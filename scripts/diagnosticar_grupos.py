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

# Las 50 que guarda el equipo, no una muestra: una franja cargada a mano puede
# estar en cualquier posición, y preguntar solo las primeras es justo la forma
# de no encontrarla. La salida no se alarga porque las iguales se agrupan.
CUANTAS_FRANJAS = 50

# Los grupos se preguntan más allá de los que tienen gente: una franja con
# horario asignada a un grupo vacío explica para qué se cargó, y si mañana
# alguien queda en ese grupo, importa.
HASTA_GRUPO = 15


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
    """
    Las franjas del equipo, agrupadas por definición.

    Se agrupan porque en general son todas iguales: mostrar cincuenta veces
    «abierta» esconde justo a la que no lo está, que es la única que interesa.
    """
    print("\n  FRANJAS DEFINIDAS EN EL EQUIPO")
    print(f"  (se preguntan las {CUANTAS_FRANJAS}; las iguales se agrupan)")

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

    # Primero las que tienen horario de verdad. Son las que cambian algo, y si
    # van al final quedan abajo de cuarenta líneas que dicen lo mismo.
    def interesa(par):
        leida = par[0]
        return (abierta(leida) or vacia(leida), par[1][0])

    for leida, numeros in sorted(iguales.items(), key=interesa):
        # En ASCII a propósito: una flecha linda se convierte en «?» si la
        # consola no está en UTF-8, y justo en la línea que hay que ver.
        marca = "" if abierta(leida) or vacia(leida) else "   *** CON HORARIO ***"
        print(f"\n     {rango(numeros)}:{marca}")
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
    """
    Qué franja usa cada grupo.

    Se preguntan los grupos con gente y además los primeros vacíos: una franja
    con horario asignada a un grupo donde hoy no hay nadie explica para qué se
    cargó, y queda escrito antes de que alguien caiga ahí sin saberlo.

    Los grupos con gente se muestran siempre; los vacíos, solo si tienen algo
    asignado. Un listado de quince «sin franja» no informa.
    """
    print("\n  QUÉ FRANJAS USA CADA GRUPO")
    con_gente = {g for g in gente if g.isdigit()}
    a_preguntar = sorted(con_gente | {str(n) for n in range(HASTA_GRUPO + 1)},
                         key=int)
    usa, vacios = {}, {}
    for g in a_preguntar:
        try:
            respuesta = enviar(const.CMD_GRPTZ_RRQ, bytes([int(g), 0, 0, 0]), 1032)
            crudo = datos_de()
            if not respuesta.get("status") or not crudo:
                if g in con_gente:
                    print(f"     grupo {g}: sin respuesta")
                    usa[g] = []
                continue
        except Exception as exc:
            if g in con_gente:
                print(f"     grupo {g}: {type(exc).__name__}: {exc}")
            continue
        suyas, sin_identificar = franjas_del_grupo(crudo)
        if g in con_gente:
            usa[g] = suyas
        elif suyas:
            vacios[g] = suyas
        else:
            continue
        cuantos = (f"{len(gente[g])} persona(s)" if g in con_gente
                   else "sin gente hoy")
        print(f"     grupo {g} ({cuantos}): {crudo[:16].hex(' ')}")
        if suyas:
            cuales = ", ".join(str(n) for n in sorted(set(suyas)))
            print(f"        usa la franja {cuales}")
        else:
            print("        sin franja asignada")
        if sin_identificar is not None:
            print(f"        (primer campo: {sin_identificar}, sin identificar)")
    if not vacios:
        print(f"     (los grupos del 0 al {HASTA_GRUPO} sin gente no tienen "
              f"franja asignada)")
    return usa, vacios


def conclusion(franjas, usa, vacios, gente):
    """Lo que sale de lo leído, no de lo que suponíamos antes de leer."""
    print("\n  QUÉ SIGNIFICA ESTO")

    def con_horario(suyas):
        # Sin repetir: un grupo tiene tres lugares para franjas y suele poner
        # la misma en los tres, así que la lista cruda la nombra tres veces.
        return sorted({n for n in suyas
                       if franjas.get(n) and not abierta(franjas[n]["semana"])
                       and not vacia(franjas[n]["semana"])})

    restringe = [(g, n) for g, suyas in usa.items() for n in con_horario(suyas)]
    guardadas = [(g, n) for g, suyas in vacios.items() for n in con_horario(suyas)]

    if not usa:
        print("     El equipo no contestó por los grupos. Si tampoco contestó")
        print("     por las franjas, lo más probable es que este modelo no las")
        print("     guarde y el grupo sea solo una etiqueta.")
    elif restringe:
        cuales = ", ".join(f"grupo {g} por la franja {n}" for g, n in restringe)
        print(f"     Este equipo SÍ tiene horarios en uso: {cuales}.")
        print("     La gente de esos grupos abre solo dentro de ese horario, aunque")
        print("     esté cargada. Dos cosas que salen de acá:")
        print("       · cargar a alguien en el grupo equivocado le da un horario que")
        print("         no le corresponde, y eso no se ve: simplemente no abre un día")
        print("         a una hora y nadie sabe por qué")
        print("       · el grupo deja de ser algo que se copia de la puerta y pasa a")
        print("         ser algo que tiene que salir del perfil, como las puertas")
        for g in sorted({g for g, _n in restringe}, key=int):
            cuantos = len(gente.get(g, []))
            print(f"     El grupo {g} tiene {cuantos} persona(s) hoy.")
    else:
        print("     Todas las franjas en uso están abiertas de 00:00 a 23:59 los")
        print("     siete días, y los grupos sin franja asignada no restringen nada.")
        print("     O sea que HOY el grupo no cambia quién abre esta puerta: es una")
        print("     etiqueta heredada de cómo se fue cargando la gente.")
        print("     Sigue siendo un botón que alguien puede apretar desde el menú")
        print("     del equipo, y por eso se copia el que ya está en uso ahí.")

    if guardadas:
        cuales = ", ".join(f"grupo {g} con la franja {n}" for g, n in guardadas)
        print(f"\n     Ojo: hay horario cargado en grupos donde hoy no hay nadie")
        print(f"     ({cuales}). No afecta a nadie ahora, pero si alguien cae ahí")
        print(f"     empieza a tener horario. Conviene saber para qué se cargó.")

    if len(usa) > 1:
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
        usa, vacios = mostrar_grupos(enviar, datos_de, const, gente)
        conclusion(franjas, usa, vacios, gente)
    finally:
        try:
            conexion.disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    main()
