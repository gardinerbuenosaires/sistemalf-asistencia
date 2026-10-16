"""Cambiar el nivel de administración de alguien en un lector. ESCRIBE.

El nivel es quién puede administrar el equipo parado frente a él: entrar al
menú, dar de alta gente, tomarle la huella. No tiene nada que ver con abrir
puertas ni con los permisos del sistema.

Por qué un script y no un botón. El sistema ofrece tres niveles —0, 2 y 6— y no
ofrece el 14, super admin, a propósito: no se usa acá, y una opción que nadie
necesita en una lista de permisos es una invitación a elegirla por error. Pero
un equipo puede traer un 14 de antes, y entonces hace falta poder bajarlo.

**Solo puede poner 0 o 14**, y eso no es una decisión nuestra. Para el formato
del equipo de fichaje hay que usar `set_user` de la librería, que deja en
usuario común a cualquier nivel que no sea uno de esos dos. Poner un 2 o un 6
desde acá escribiría otra cosa que la pedida, así que no se intenta: eso se
hace en el menú del equipo.

Lo que cuida:

  · Avisa si el cambio deja al equipo **sin ningún administrador**. En estos
    lectores, sin administradores el menú queda abierto para cualquiera que
    pase: es lo contrario de lo que uno busca al sacar permisos.
  · Relee para verificar, y si el equipo le borró las huellas al reescribir el
    registro, las repone en el acto.
  · No toca el nombre, ni el grupo, ni el índice interno.

Uso:
    scripts\\cambiar_nivel_lector.bat 8 0          baja al 8 a usuario común
    scripts\\cambiar_nivel_lector.bat 8 0 --si     sin preguntar
    scripts\\cambiar_nivel_lector.bat 8 0 --equipo 192.168.1.209
"""
import os
import sys

sys.stdout.reconfigure(errors="replace")
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.chdir(RAIZ)

BASE_PRODUCCION = r"C:\ProgramData\SistemAlf\fichajes.db"
if "--base" in sys.argv:
    os.environ["DB_PATH"] = sys.argv[sys.argv.index("--base") + 1]
elif not os.getenv("DB_PATH") and os.path.exists(BASE_PRODUCCION):
    os.environ["DB_PATH"] = BASE_PRODUCCION

SUELTOS = [a for a in sys.argv[1:] if not a.startswith("--")]
if len(SUELTOS) < 2:
    print(__doc__)
    raise SystemExit(1)
NUMERO, NIVEL = SUELTOS[0].strip(), SUELTOS[1].strip()


def argumento(nombre):
    if nombre in sys.argv:
        i = sys.argv.index(nombre)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return None


def salir(mensaje):
    print(f"\n  {mensaje}\n")
    raise SystemExit(1)


def main():
    from sync.escritura import NIVELES, actualizar_en_puerta
    from sync.lectores import leer_cargados
    from db.database import db_solo_lectura

    try:
        nivel = int(NIVEL)
    except ValueError:
        salir(f"«{NIVEL}» no es un nivel. Los que se pueden poner desde acá son "
              f"0 (usuario común) y 14 (super admin).")
    if nivel not in (0, 14):
        salir(f"Desde acá solo se puede poner 0 o 14.\n"
              f"     El {nivel} —{NIVELES.get(nivel, 'desconocido')}— hay que "
              f"ponerlo en el menú del equipo: la librería que sabe escribir el\n"
              f"     formato del equipo de fichaje lo dejaría en usuario común "
              f"sin avisar, o sea otra cosa que la pedida.")

    ip = argumento("--equipo")
    with db_solo_lectura() as conn:
        if ip:
            fila = conn.execute(
                """SELECT id, nombre, ip, puerto, password, timeout
                     FROM dispositivos WHERE ip = ? AND activo = 1""", (ip,)).fetchone()
        else:
            fila = conn.execute(
                """SELECT id, nombre, ip, puerto, password, timeout
                     FROM dispositivos
                    WHERE activo=1 AND cuenta_asistencia=1 AND protocolo='pull'
                      AND ip IS NOT NULL ORDER BY orden, id LIMIT 1""").fetchone()
        if not fila:
            salir("No encuentro ese equipo en el sistema.")
        quien = conn.execute(
            "SELECT nombre, apellido FROM empleados WHERE TRIM(user_id)=?",
            (NUMERO,)).fetchone()
    equipo = dict(fila)

    print(f"\n  Equipo: {equipo['nombre']} ({equipo['ip']})")
    lectura = leer_cargados(equipo)
    if not lectura["ok"]:
        salir(f"No contestó: {lectura['error']}")

    actual = next((u for u in lectura["usuarios"] if u["user_id"] == NUMERO), None)
    if actual is None:
        salir(f"El {NUMERO} no está cargado en ese equipo.")

    tenia = actual.get("privilegio") or 0
    nombre = (f"{quien['apellido']}, {quien['nombre']}".strip(", ")
              if quien else "(no está en el sistema)")
    print(f"  Persona: {NUMERO} · {nombre}")
    print(f"     en el equipo figura como «{actual['nombre']}»")
    print(f"     nivel actual: {tenia} — {NIVELES.get(tenia, 'desconocido')}")
    print(f"     nivel pedido: {nivel} — {NIVELES.get(nivel, 'desconocido')}")

    if tenia == nivel:
        salir("Ya tiene ese nivel. No hay nada que cambiar.")

    # Quedarse sin administradores no es "mas seguro": en estos lectores el
    # menu queda abierto para cualquiera que pase.
    administran = [u for u in lectura["usuarios"] if (u.get("privilegio") or 0) >= 2]
    if nivel == 0 and tenia >= 2 and len(administran) <= 1:
        print(f"\n  OJO: es el único que administra este equipo.")
        print(f"  Sin ningún administrador, el menú del lector queda abierto "
              f"para cualquiera")
        print(f"  que pase. Si querés sacarle el permiso a esta persona, dale "
              f"el nivel a otra primero.")
    elif nivel == 0 and tenia >= 2:
        otros = [f"{u['user_id']} «{u['nombre']}»" for u in administran
                 if u["user_id"] != NUMERO]
        print(f"\n  Después de esto siguen administrando: {', '.join(otros)}")

    if "--si" not in sys.argv:
        if input("\n  ¿Lo cambio? (s/n) ").strip().lower() != "s":
            salir("Cancelado. No se escribió nada.")

    # Se reusa lo del sistema: relee para verificar, comprueba que los demás
    # quedaron enteros, y si el equipo le borró las huellas al reescribir el
    # registro las repone en el acto. En este equipo son las originales.
    r = actualizar_en_puerta(equipo, NUMERO, nombre=None, privilegio=nivel)

    if r.get("aviso_lista"):
        print(f"\n  {r['aviso_lista']}")
    if not r["ok"]:
        salir(f"No se pudo: {r.get('error')}")

    print(f"\n  Hecho. Quedó en nivel {nivel} con {r.get('huellas')} huella(s)"
          + (", que hubo que reponer" if r.get("huellas_repuestas") else "")
          + f", y los otros {r.get('otros')} intactos.")
    print(f"  Comprobalo en el menú del equipo cuando pases.\n")


if __name__ == "__main__":
    main()
