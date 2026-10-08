"""Qué hay en la carpeta de respaldos de borrado. SOLO LECTURA.

Cada vez que el sistema borra a alguien de un equipo, guarda antes un archivo
con su registro completo y sus huellas. Esta carpeta es la vuelta atrás de
todos esos borrados.

Para qué este script. Buscar por nombre de archivo engaña: el archivo se llama
con el número **tal como lo tenía el equipo**, que no siempre es el del legajo
—un `057` o un número con espacios quedan con otro nombre— y además no se ve de
quién era sin abrirlo. Esto mira adentro.

No es el registro de lo que se hizo: eso está en «Operaciones», en el sistema, y
es la fuente autorizada. Esta carpeta son las huellas por si hay que volver
atrás. Si algo figura en Operaciones y no está acá, eso es un problema; si está
acá y no en Operaciones, lo borró un script viejo.

Uso:
    scripts\\ver_borrados.bat            todos, del más nuevo al más viejo
    scripts\\ver_borrados.bat 57         solo los que mencionen ese número
    scripts\\ver_borrados.bat lavega     o ese texto en el nombre
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(errors="replace")
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(RAIZ)

BASE_PRODUCCION = r"C:\ProgramData\SistemAlf\fichajes.db"
if "--base" in sys.argv:
    os.environ["DB_PATH"] = sys.argv[sys.argv.index("--base") + 1]
elif not os.getenv("DB_PATH") and os.path.exists(BASE_PRODUCCION):
    os.environ["DB_PATH"] = BASE_PRODUCCION

BUSCADO = next((a for a in sys.argv[1:] if not a.startswith("--")), "").lower()


def main():
    base = os.environ.get("DB_PATH")
    carpeta = (Path(base).resolve().parent if base else Path(".")) / "borrados"
    print(f"\n  Carpeta: {carpeta}")
    if not carpeta.exists():
        print("\n  No existe. Nunca se borro a nadie desde el sistema.\n")
        return

    archivos = sorted(carpeta.glob("*.json"),
                      key=lambda p: p.stat().st_mtime, reverse=True)
    if not archivos:
        print("\n  Esta vacia. Nunca se borro a nadie desde el sistema.\n")
        return

    print(f"  {len(archivos)} respaldo(s)"
          + (f", buscando «{BUSCADO}»" if BUSCADO else "") + "\n")

    mostrados = 0
    for ruta in archivos:
        try:
            d = json.loads(ruta.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"  {ruta.name}: no se pudo leer ({exc})")
            continue
        numero = str(d.get("user_id", "")).strip()
        nombre = (d.get("nombre") or "").strip()
        equipo = d.get("equipo") or d.get("ip") or "?"
        if BUSCADO and BUSCADO not in f"{numero} {nombre} {ruta.name}".lower():
            continue
        cuando = d.get("cuando") or datetime.fromtimestamp(
            ruta.stat().st_mtime).strftime("%d-%m-%Y %H:%M:%S")
        print(f"  {cuando}  {numero:>8}  «{nombre}»")
        print(f"      de {equipo} · {len(d.get('huellas', []))} huella(s) · "
              f"{ruta.name}")
        mostrados += 1

    if BUSCADO and not mostrados:
        print(f"  Ninguno menciona «{BUSCADO}».")
        print()
        print("  Si esa persona desaparecio igual del equipo, el sistema NO fue:")
        print("  nunca borra sin guardar esto antes, y si el respaldo falla el")
        print("  borrado tampoco se hace. Mira «Operaciones» en el sistema para")
        print("  confirmarlo, y si ahi tampoco esta, algo mas le esta escribiendo")
        print("  a ese equipo.")
    print()


if __name__ == "__main__":
    main()
