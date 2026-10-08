"""Volver a poner a una persona en el equipo de fichaje. ESCRIBE EN EL .201.

Para qué existe. El sistema sabe escribir en las puertas y **no** en el equipo
de fichaje: ahí la función de escritura se niega a propósito, porque ese equipo
usa otro formato de registro y es el que sostiene la asistencia de todos. Pero
pasa el caso de alguien que se borró de ahí por error y cuya huella no se puede
volver a conseguir sin que la persona venga.

Es de una sola vez y por eso es un script y no un botón. Un botón invita a
usarlo; esto hay que ir a buscarlo.

De dónde saca la huella, en este orden:

  1. **El respaldo del borrado**, si el sistema fue quien lo borró. Es lo que
     esa persona tenía en el equipo de fichaje: nombre, grupo, nivel y huellas.
  2. **Otra puerta donde siga cargada**, con `--desde-puerta IP`. Las huellas de
     las puertas son byte a byte las del maestro —se verificó en el
     relevamiento— así que sirven igual.

Qué cuida:

  · No escribe si la lista del equipo vino cortada. El índice libre se calcula
    de esa lista, y con una lista corta se le escribe encima a alguien.
  · No escribe si esa persona ya está. Reponer a quien está es pisarlo.
  · Verifica releyendo: que quedó con todas sus huellas y que los demás
    quedaron enteros.

Usa `set_user` de pyzk y no la escritura propia del sistema. La propia existe
para no degradarle el nivel a un enrolador o un administrador, y solo sabe el
formato de las puertas. Para un usuario común en el equipo de fichaje, `set_user`
es el camino probado de la librería. Si la persona tenía nivel de enrolador o
administrador, el script avisa y no sigue.

Uso:
    scripts\\reponer_en_fichaje.bat 57
    scripts\\reponer_en_fichaje.bat 57 --desde-puerta 192.168.1.208
    scripts\\reponer_en_fichaje.bat 57 --si        (sin preguntar)
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

if len(sys.argv) < 2 or sys.argv[1].startswith("--"):
    print(__doc__)
    raise SystemExit(1)
NUMERO = str(sys.argv[1]).strip()


def argumento(nombre):
    if nombre in sys.argv:
        i = sys.argv.index(nombre)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return None


def salir(mensaje):
    print(f"\n  {mensaje}\n")
    raise SystemExit(1)


def equipos():
    """El equipo de fichaje y, si se pidió, la puerta de donde copiar."""
    from db.database import db_solo_lectura

    ip_puerta = argumento("--desde-puerta")
    with db_solo_lectura() as conn:
        maestro = conn.execute(
            """SELECT id, nombre, ip, puerto, password, timeout
                 FROM dispositivos
                WHERE activo=1 AND cuenta_asistencia=1 AND protocolo='pull'
                  AND ip IS NOT NULL ORDER BY orden, id LIMIT 1""").fetchone()
        if not maestro:
            salir("No hay equipo de fichaje cargado en el sistema.")
        puerta = None
        if ip_puerta:
            puerta = conn.execute(
                """SELECT id, nombre, ip, puerto, password, timeout
                     FROM dispositivos WHERE ip = ? AND activo = 1""",
                (ip_puerta,)).fetchone()
            if not puerta:
                salir(f"No tengo cargada ninguna puerta con la IP {ip_puerta}.")
        quien = conn.execute(
            """SELECT nombre, apellido, activo, nombre_lector
                 FROM empleados WHERE TRIM(user_id)=?""", (NUMERO,)).fetchone()
    return (dict(maestro), dict(puerta) if puerta else None,
            dict(quien) if quien else None)


def del_respaldo():
    """Lo que el sistema guardó al borrarlo del equipo de fichaje."""
    import json
    from pathlib import Path
    from zk.finger import Finger

    base = os.environ.get("DB_PATH")
    carpeta = (Path(base).resolve().parent if base else Path(".")) / "borrados"
    if not carpeta.exists():
        return None
    # El más nuevo de esa persona. El nombre lleva la IP y la fecha.
    candidatos = sorted(carpeta.glob(f"*-{NUMERO}-*.json")) + \
                 sorted(carpeta.glob(f"*-{NUMERO}.json"))
    if not candidatos:
        return None
    ruta = candidatos[-1]
    d = json.loads(ruta.read_text(encoding="utf-8"))
    return {"origen": str(ruta), "equipo": d.get("equipo"),
            "nombre": d.get("nombre") or NUMERO, "grupo": d.get("grupo") or 0,
            "privilegio": int(d.get("privilegio") or 0),
            "huellas": [Finger.json_unpack(h) for h in d.get("huellas", [])]}


def de_la_puerta(puerta):
    """Lo que esa persona tiene cargado hoy en una puerta."""
    from sync.lectores import _conectar

    conexion = None
    try:
        conexion, _t = _conectar(puerta["ip"], puerta.get("puerto", 4370),
                                 puerta.get("password", 0), puerta.get("timeout", 10))
        quien = next((u for u in conexion.get_users()
                      if str(u.user_id).strip() == NUMERO), None)
        if quien is None:
            salir(f"El {NUMERO} no está cargado en {puerta['nombre']}.")
        suyas = [h for h in conexion.get_templates()
                 if h.uid == quien.uid and getattr(h, "valid", 1)]
        if not suyas:
            salir(f"El {NUMERO} está en {puerta['nombre']} pero sin ninguna huella.")
        return {"origen": f"{puerta['nombre']} ({puerta['ip']})",
                "equipo": puerta["nombre"],
                "nombre": (quien.name or "").strip() or NUMERO,
                "nombre_corto": True,
                "grupo": 0, "privilegio": 0, "huellas": suyas}
    finally:
        if conexion:
            try:
                conexion.disconnect()
            except Exception:
                pass


def main():
    from sync.lectores import _conectar

    maestro, puerta, quien = equipos()
    print(f"\n  Equipo de fichaje: {maestro['nombre']} ({maestro['ip']})")
    if quien:
        print(f"  Persona: {quien['apellido']}, {quien['nombre']}"
              + ("" if quien["activo"] else "  (DADA DE BAJA en el sistema)"))
    else:
        print(f"  El {NUMERO} no existe en el sistema. Pará y mirá eso primero.")

    datos = del_respaldo()
    if datos:
        print(f"\n  Fuente: el respaldo del borrado")
        print(f"     {datos['origen']}")
    elif puerta:
        datos = de_la_puerta(puerta)
        print(f"\n  Fuente: {datos['origen']}")
    else:
        salir("No encuentro respaldo de su borrado. Si sigue cargado en alguna "
              "puerta, pasame cuál:\n"
              "     scripts\\reponer_en_fichaje.bat " + NUMERO +
              " --desde-puerta 192.168.1.208")

    # Las puertas guardan 8 caracteres y el equipo de fichaje 24. Copiar el
    # nombre de una puerta lo dejaria recortado en el equipo que si tiene lugar
    # --«SEBA» en vez del nombre entero-- y eso es lo que la persona ve en
    # pantalla al fichar. El del legajo le gana.
    if datos.get("nombre_corto") and quien and (quien.get("nombre_lector") or "").strip():
        print(f"     el nombre de la puerta viene recortado a 8 caracteres "
              f"(«{datos['nombre']}»)")
        datos["nombre"] = quien["nombre_lector"].strip()[:24]
        print(f"     se usa el del legajo: «{datos['nombre']}»")
    elif datos.get("nombre_corto"):
        print(f"     OJO: el nombre viene de una puerta, recortado a 8 "
              f"caracteres. El equipo de fichaje guarda 24.")
        print(f"     Si querés el entero, ponéselo en el legajo "
              f"(Accesos -> nombre que muestra el lector) y volvé a correr esto.")
    print(f"     nombre «{datos['nombre']}», {len(datos['huellas'])} huella(s)")
    if datos["privilegio"] not in (0,):
        salir(f"Tenía nivel {datos['privilegio']} (no es usuario común). "
              f"set_user de pyzk lo dejaría en usuario común sin avisar, así que "
              f"este script no sigue: eso hay que hacerlo a mano en el equipo.")

    conexion = None
    try:
        conexion, transporte = _conectar(maestro["ip"], maestro.get("puerto", 4370),
                                         maestro.get("password", 0),
                                         maestro.get("timeout", 10))
        print(f"\n  Conectado por {transporte}")
        usuarios = conexion.get_users()
        dice = getattr(conexion, "users", None)
        print(f"  Tiene {len(usuarios)} usuario(s)"
              + (f", dice tener {dice}" if dice is not None else ""))
        if dice is not None and len(usuarios) != dice:
            salir("La lista vino cortada. NO se escribe nada: el índice libre "
                  "se calcula de esta lista y con una lista corta se le escribe "
                  "encima a alguien.")
        if any(str(u.user_id).strip() == NUMERO for u in usuarios):
            salir(f"El {NUMERO} YA está cargado. No hay nada que reponer.")

        antes = {str(u.user_id).strip(): (u.uid, (u.name or "").strip())
                 for u in usuarios}
        usados = {u.uid for u in usuarios}
        uid = max(usados) + 1 if usados else 1

        if "--si" not in sys.argv:
            print(f"\n  Se va a crear el {NUMERO} como «{datos['nombre']}» con "
                  f"{len(datos['huellas'])} huella(s), en el índice {uid}.")
            if input("  ¿Lo repongo? (s/n) ").strip().lower() != "s":
                salir("Cancelado. No se escribió nada.")

        conexion.set_user(uid=uid, name=datos["nombre"], privilege=0,
                          user_id=NUMERO)
        recien = next((u for u in conexion.get_users()
                       if str(u.user_id).strip() == NUMERO), None)
        if recien is None:
            salir("Se escribió el usuario y no aparece al releer. Pará acá.")
        conexion.save_user_template(recien, datos["huellas"])

        print("\n  Escrito. Releyendo para verificar...")
        despues = conexion.get_users()
        cuenta = {}
        for h in conexion.get_templates():
            if getattr(h, "valid", 1):
                cuenta[h.uid] = cuenta.get(h.uid, 0) + 1
        vuelto = next((u for u in despues if str(u.user_id).strip() == NUMERO), None)
        if vuelto is None:
            salir("No quedó cargado.")
        tiene = cuenta.get(vuelto.uid, 0)
        print(f"     {NUMERO} «{(vuelto.name or '').strip()}» con {tiene} "
              f"de {len(datos['huellas'])} huella(s)")

        # Y los demás, que es lo que hay que probar: que no se llevó puesto a nadie.
        ahora = {str(u.user_id).strip(): (u.uid, (u.name or "").strip())
                 for u in despues}
        problemas = [f"cambió el {n}: {antes[n]} -> {ahora.get(n)}"
                     for n in antes if ahora.get(n) != antes[n]]
        problemas += [f"desapareció el {n}" for n in antes if n not in ahora]
        if problemas:
            print("\n  OJO, los demás NO quedaron iguales:")
            for p in problemas:
                print(f"     {p}")
        else:
            print(f"     los otros {len(antes)} quedaron intactos")

        if tiene == len(datos["huellas"]) and not problemas:
            print("\n  Listo. Pedile que fiche para confirmar que el dedo le lee.\n")
        else:
            print("\n  Quedó a medias. Miralo antes de seguir.\n")
            raise SystemExit(1)
    finally:
        if conexion:
            try:
                conexion.disconnect()
            except Exception:
                pass


if __name__ == "__main__":
    main()
