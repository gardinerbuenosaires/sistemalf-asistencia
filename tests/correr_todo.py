"""Corre todas las pruebas. Cada una trabaja sobre su propia COPIA de la base.

Uso:  python tests/correr_todo.py [--origen ruta/a/fichajes.db]

Sin --origen copia data/fichajes.db. La base de origen nunca se modifica: se
abre en solo lectura. Antes de pasar a producción conviene correrlo contra una
copia de la base de cada instancia.
"""
import glob
import os
import re
import subprocess
import sys
import time

AQUI = os.path.dirname(os.path.abspath(__file__))


def main():
    args = sys.argv[1:]
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    if "--origen" in args:
        env["DB_ORIGEN"] = os.path.abspath(args[args.index("--origen") + 1])
    origen = env.get("DB_ORIGEN") or os.path.join(os.path.dirname(AQUI), "data", "fichajes.db")
    print(f"Base de origen (solo lectura): {origen}\n")

    archivos = sorted(f for f in glob.glob(os.path.join(AQUI, "*.py"))
                      if not os.path.basename(f).startswith(("_", "correr_")))
    total_ok = total_mal = 0
    rotas = []
    for f in archivos:
        t0 = time.time()
        r = subprocess.run([sys.executable, f], capture_output=True, text=True,
                           env=env, encoding="utf-8", errors="replace")
        m = re.search(r"(\d+) pasaron, (\d+) fallaron", r.stdout)
        ok, mal = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
        total_ok += ok
        total_mal += mal
        estado = "ok" if r.returncode == 0 and m else "FALLA"
        print(f"  {estado:5} {os.path.basename(f):30} {ok:4} pasaron {mal:3} fallaron  ({time.time() - t0:4.1f} s)")
        if estado != "ok":
            rotas.append(f)
            salida = (r.stdout + r.stderr).splitlines()
            lineas = [l for l in salida if "FALLA" in l or "Error" in l or "Traceback" in l] or salida[-8:]
            for l in lineas[:12]:
                print("          " + l)
    print(f"\n  {total_ok} pasaron, {total_mal} fallaron, en {len(archivos)} archivos")
    sys.exit(1 if rotas else 0)


if __name__ == "__main__":
    main()
