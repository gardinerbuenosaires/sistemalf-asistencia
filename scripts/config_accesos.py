"""Guardar y restaurar la configuración de accesos: los equipos y los perfiles.

Existe porque esa configuración se pierde cada vez que se reemplaza la base.
La copia de pruebas se refresca desde producción —que todavía no tiene estas
tablas— y los seis lectores hay que volver a cargarlos a mano. Va a pasar otra
vez el día que se configure producción de cero.

Qué guarda:
  · los equipos, con su IP, puerto, clave y para qué se usa cada uno
  · los perfiles, y qué puertas incluye cada uno

Qué NO guarda, a propósito:
  · el perfil de cada empleado, que se rehace en un minuto con «Asignar por
    cargo» y que además depende de qué empleados tenga la base de destino
  · las excepciones, que son decisiones sobre personas concretas y copiarlas a
    otra base sin mirar sería arrastrar sin pensar lo más difícil de auditar
  · lo que el equipo informó de sí mismo —modelo, firmware, número de serie—
    porque eso se vuelve a leer apretando «Probar» y copiarlo sería afirmar
    algo que no se verificó en esa instalación

Al importar no se duplica nada: los equipos se reconocen por IP y los perfiles
por nombre. Un equipo que ya existe se deja como está y se informa, en vez de
pisarle una configuración que alguien pudo haber ajustado.

Uso:  python scripts/config_accesos.py exportar [--salida ARCHIVO]
      python scripts/config_accesos.py importar ARCHIVO
"""
import json
import os
import sys
from datetime import datetime

sys.stdout.reconfigure(errors="replace")
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.chdir(RAIZ)

# Qué base usar, en orden. A diferencia de los scripts que escriben en los
# equipos, acá el default NO es producción: esto trata de la configuración DE
# ESTA INSTALACIÓN, así que la suya es la que corresponde. En producción esa
# carpeta no existe y cae en la de producción, que ahí sí es la propia.
BASE_PRODUCCION = r"C:\ProgramData\SistemAlf\fichajes.db"
BASE_LOCAL = os.path.join(RAIZ, "data", "pruebas.db")
if "--base" in sys.argv:
    os.environ["DB_PATH"] = sys.argv[sys.argv.index("--base") + 1]
elif not os.getenv("DB_PATH"):
    if os.path.exists(BASE_LOCAL):
        os.environ["DB_PATH"] = BASE_LOCAL
    elif os.path.exists(BASE_PRODUCCION):
        os.environ["DB_PATH"] = BASE_PRODUCCION

CAMPOS = ("nombre", "ubicacion", "protocolo", "ip", "puerto", "password",
          "timeout", "cuenta_asistencia", "es_acceso", "activo", "orden")


def salir(mensaje):
    print(f"\n  {mensaje}\n")
    raise SystemExit(1)


def argumento(nombre, defecto=None):
    if nombre in sys.argv:
        i = sys.argv.index(nombre)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return defecto


def _sin_tablas():
    """
    El mensaje cuando falta la tabla. Dice qué base se miró y cuál sería la
    otra: la primera vez que esto pasó, la pregunta fue justamente "¿no estará
    leyendo otra base?", y el mensaje no daba manera de saberlo.
    """
    usada = os.environ.get("DB_PATH", "")
    hay_prod = (os.path.exists(BASE_PRODUCCION)
                and os.path.abspath(BASE_PRODUCCION) != os.path.abspath(usada or ""))
    otra = BASE_PRODUCCION if hay_prod else BASE_LOCAL
    texto = (f"La base {usada} todavia no tiene la tabla de equipos. "
             f"Se crea al arrancar el sistema con la version nueva.")
    if os.path.exists(otra) and os.path.abspath(otra) != os.path.abspath(usada or ""):
        texto += f"  Si la que buscabas es otra: --base {otra}"
    return texto


def _hay_tablas(conn):
    return bool(conn.execute(
        """SELECT 1 FROM sqlite_master
            WHERE type='table' AND name='dispositivos'""").fetchone())


def exportar():
    from db.database import db_session

    destino = argumento("--salida") or os.path.join(
        RAIZ, f"accesos-{datetime.now():%Y%m%d-%H%M%S}.json")

    print("  Base: " + os.environ.get("DB_PATH", "(ninguna)"))
    with db_session() as conn:
        if not _hay_tablas(conn):
            salir(_sin_tablas())
        equipos = [dict(r) for r in conn.execute(
            f"SELECT {', '.join(CAMPOS)} FROM dispositivos ORDER BY orden, id")]
        perfiles = []
        for p in conn.execute(
            "SELECT id, nombre, descripcion, activo, orden FROM perfiles_acceso "
            "ORDER BY orden, id"
        ):
            puertas = [r["nombre"] for r in conn.execute(
                """SELECT d.nombre FROM perfiles_dispositivos pd
                     JOIN dispositivos d ON d.id = pd.dispositivo_id
                    WHERE pd.perfil_id = ? ORDER BY d.orden, d.id""", (p["id"],))]
            # Las puertas se guardan por NOMBRE y no por id: los ids son de esta
            # base y en otra significan otra cosa.
            perfiles.append({"nombre": p["nombre"], "descripcion": p["descripcion"],
                             "activo": p["activo"], "orden": p["orden"],
                             "puertas": puertas})

    contenido = {"exportado": datetime.now().strftime("%d-%m-%Y %H:%M:%S"),
                 "base": os.environ.get("DB_PATH", ""),
                 "equipos": equipos, "perfiles": perfiles}
    with open(destino, "w", encoding="utf-8") as f:
        json.dump(contenido, f, ensure_ascii=False, indent=1)

    # Se relee: un archivo que no se comprobó no sirve de respaldo.
    with open(destino, encoding="utf-8") as f:
        releido = json.load(f)
    if len(releido["equipos"]) != len(equipos):
        salir(f"El archivo quedó incompleto: {destino}")

    print(f"\n  {len(equipos)} equipo(s) y {len(perfiles)} perfil(es) guardados en:")
    print(f"     {destino}\n")
    for e in equipos:
        usa = []
        if e["cuenta_asistencia"]:
            usa.append("fichaje")
        if e["es_acceso"]:
            usa.append("puerta")
        print(f"     {e['nombre']:22} {str(e['ip']):16} {' + '.join(usa) or '—'}")
    for p in perfiles:
        print(f"     perfil «{p['nombre']}»: {', '.join(p['puertas']) or 'sin puertas'}")
    print()


def importar(archivo):
    from db.database import db_session

    if not os.path.exists(archivo):
        salir(f"No encuentro el archivo: {archivo}")
    with open(archivo, encoding="utf-8") as f:
        datos = json.load(f)

    print()
    print("  Base: " + os.environ.get("DB_PATH", "(ninguna)"))
    print("  Archivo del " + str(datos.get("exportado", "?")))
    print(f"  {len(datos['equipos'])} equipo(s), {len(datos['perfiles'])} perfil(es)")

    with db_session() as conn:
        if not _hay_tablas(conn):
            salir(_sin_tablas())

        existentes = {r["ip"]: r["nombre"] for r in conn.execute(
            "SELECT ip, nombre FROM dispositivos WHERE ip IS NOT NULL")}
        nuevos, ya_estaban = 0, []
        for e in datos["equipos"]:
            if e["ip"] in existentes:
                ya_estaban.append(f"{e['nombre']} ({e['ip']}) — ya existe como "
                                  f"«{existentes[e['ip']]}»")
                continue
            conn.execute(
                f"""INSERT INTO dispositivos ({', '.join(CAMPOS)})
                     VALUES ({', '.join('?' * len(CAMPOS))})""",
                [e[c] for c in CAMPOS])
            nuevos += 1

        perfiles_nuevos, perfiles_ya = 0, []
        for p in datos["perfiles"]:
            fila = conn.execute("SELECT id FROM perfiles_acceso WHERE nombre=?",
                                (p["nombre"],)).fetchone()
            if fila:
                perfiles_ya.append(p["nombre"])
                continue
            cur = conn.execute(
                """INSERT INTO perfiles_acceso (nombre, descripcion, activo, orden)
                     VALUES (?,?,?,?)""",
                (p["nombre"], p["descripcion"], p["activo"], p["orden"]))
            pid = cur.lastrowid
            perfiles_nuevos += 1
            for nombre_puerta in p["puertas"]:
                d = conn.execute("SELECT id FROM dispositivos WHERE nombre=?",
                                 (nombre_puerta,)).fetchone()
                if d:
                    conn.execute(
                        """INSERT OR IGNORE INTO perfiles_dispositivos
                             (perfil_id, dispositivo_id) VALUES (?,?)""", (pid, d["id"]))
                else:
                    print(f"     OJO: el perfil «{p['nombre']}» menciona la puerta "
                          f"«{nombre_puerta}», que no está en esta base")

    print(f"\n  {nuevos} equipo(s) y {perfiles_nuevos} perfil(es) agregados")
    for linea in ya_estaban:
        print(f"     sin tocar: {linea}")
    if perfiles_ya:
        print(f"     perfiles que ya existían: {', '.join(perfiles_ya)}")
    print()


def main():
    modo = sys.argv[1] if len(sys.argv) > 1 else ""
    if modo == "exportar":
        exportar()
    elif modo == "importar" and len(sys.argv) >= 3:
        importar(sys.argv[2])
    else:
        print(__doc__)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
