"""Saca de los backups del relevamiento un resumen que se pueda compartir.

Para qué sirve. Los archivos que deja `relevar_lectores.py` tienen las huellas
y las claves numéricas de los empleados: no salen de la PC. Pero para revisar
cómo agrupa el sistema a la gente por puertas alcanza con saber qué número está
en qué lector, sin nombre ni nada más.

Esto lee esos backups y escribe un archivo con eso solo. Lo que queda adentro:

  · el nombre y la IP de cada lector, su modelo y su algoritmo de huella
  · la lista de números de usuario cargados en cada uno
  · cuántos usuarios y cuántas huellas tiene, como control

Lo que NO queda: ninguna huella, ninguna clave, ningún nombre de persona. Los
números de usuario por sí solos no identifican a nadie fuera del sistema.

No toca ningún equipo: trabaja sobre archivos que ya existen.

Uso:  python scripts/exportar_padrones.py [CARPETA] [--salida ARCHIVO]

      CARPETA   la del relevamiento, con los lector_*.json adentro. Si no se
                indica, toma la más reciente de
                C:\\ProgramData\\SistemAlf\\relevamiento\\
"""
import sys, os

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.chdir(RAIZ)

import json
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

BASE_RELEVAMIENTOS = Path(r"C:\ProgramData\SistemAlf\relevamiento")


def _argumento(nombre, defecto=None):
    if nombre in sys.argv:
        i = sys.argv.index(nombre) + 1
        if i >= len(sys.argv):
            sys.exit(f"Falta el valor de {nombre}")
        return sys.argv[i]
    return defecto


def _carpeta():
    sueltos = [a for a in sys.argv[1:] if not a.startswith("--")]
    # El valor de --salida no es la carpeta.
    salida = _argumento("--salida")
    sueltos = [a for a in sueltos if a != salida]
    if sueltos:
        return Path(sueltos[0])
    if not BASE_RELEVAMIENTOS.exists():
        sys.exit(f"No encuentro {BASE_RELEVAMIENTOS}.\n"
                 f"Indicá la carpeta del relevamiento como primer argumento.")
    # La más reciente: las carpetas se llaman AAAAMMDD-HHMMSS, así que ordenar
    # por nombre alcanza y no depende de la fecha del sistema de archivos.
    carpetas = sorted((d for d in BASE_RELEVAMIENTOS.iterdir() if d.is_dir()),
                      key=lambda d: d.name)
    if not carpetas:
        sys.exit(f"No hay ningún relevamiento en {BASE_RELEVAMIENTOS}")
    return carpetas[-1]


def main():
    carpeta = _carpeta()
    archivos = sorted(carpeta.glob("lector_*.json"))
    if not archivos:
        sys.exit(f"No hay archivos lector_*.json en {carpeta.resolve()}")

    print(f"Relevamiento: {carpeta.resolve()}")
    equipos = []
    for ruta in archivos:
        try:
            d = json.loads(ruta.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"  {ruta.name}: no se pudo leer ({exc})")
            continue

        usuarios = sorted({str(u.get("user_id") or "").strip()
                           for u in d.get("usuarios", [])} - {""},
                          key=lambda x: (len(x), x))
        cap = d.get("capacidad") or {}
        equipos.append({
            "nombre":           d.get("nombre"),
            "ip":               d.get("ip"),
            "modelo":           d.get("plataforma"),
            "firmware":         d.get("firmware"),
            "algoritmo_huella": d.get("fp_version"),
            "transporte":       d.get("transporte"),
            "paquete_usuario":  cap.get("paquete_usuario") or d.get("paquete_usuario"),
            "usuarios_en_equipo": cap.get("usuarios"),
            "huellas_en_equipo":  cap.get("huellas"),
            "user_ids":         usuarios,
        })
        print(f"  {ruta.name}: {len(usuarios)} números")

    salida = Path(_argumento("--salida") or (carpeta / "padrones_para_compartir.json"))
    salida.write_text(json.dumps({
        "generado_en": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "origen": str(carpeta.resolve()),
        "contiene": "solo numeros de usuario por lector, sin nombres ni huellas ni claves",
        "equipos": equipos,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\nListo: {salida.resolve()}")
    print("Ese archivo no tiene huellas, ni claves, ni nombres de personas.")


if __name__ == "__main__":
    main()
