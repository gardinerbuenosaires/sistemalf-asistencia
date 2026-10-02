"""El JavaScript de las plantillas: que compile y que no llame a lo que no existe.

Existe porque dos errores seguidos se escaparon de toda la suite, y los dos eran
del mismo tipo: código que el navegador no puede ejecutar, en un proyecto donde
las pruebas corren Python y nunca abren una página.

  · un `data-perm` dentro de una plantilla dinámica, que deja un botón invisible
    para siempre sin ningún error;
  · una llamada a `_esc()` en un archivo donde esa función no existía, que tiraba
    un ReferenceError mientras se armaba el HTML y dejaba una sección entera en
    blanco.

Ninguno de los dos rompe nada que Python pueda notar, y los dos se descubren
abriendo la pantalla y mirando. Esto cubre la parte que se puede automatizar.

Necesita node, que viene con el entorno de desarrollo. Si no está, las pruebas
se saltean en vez de fallar: no tiene sentido que la suite dependa de algo que
producción no usa.
"""
import os
import re
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(errors="replace")
AQUI = os.path.dirname(os.path.abspath(__file__))
PLANTILLAS = os.path.join(os.path.dirname(AQUI), "web", "templates")

ok = fallos = 0


def chequear(descripcion, condicion, extra=""):
    global ok, fallos
    if condicion:
        ok += 1
        print(f"  OK   {descripcion}")
    else:
        fallos += 1
        print(f"  FALLA {descripcion}  {extra}")


def hay_node():
    try:
        return subprocess.run(["node", "--version"], capture_output=True).returncode == 0
    except FileNotFoundError:
        return False


def script_de(ruta):
    """El JavaScript embebido, sin los <script src=...> que apuntan a archivos."""
    with open(ruta, encoding="utf-8") as f:
        html = f.read()
    return "\n".join(re.findall(
        r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S))


print("=== EL JAVASCRIPT DE LAS PLANTILLAS COMPILA ===")

if not hay_node():
    print("  (no hay node: se saltea)")
    print(f"\n{'='*52}\n  0 pasaron, 0 fallaron\n{'='*52}")
    raise SystemExit(0)

archivos = sorted(f for f in os.listdir(PLANTILLAS) if f.endswith(".html"))
for nombre in archivos:
    js = script_de(os.path.join(PLANTILLAS, nombre))
    if not js.strip():
        continue
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                     encoding="utf-8") as t:
        t.write(js)
        ruta = t.name
    try:
        r = subprocess.run(["node", "--check", ruta], capture_output=True, text=True)
    finally:
        os.unlink(ruta)
    detalle = (r.stderr or "").strip().splitlines()
    chequear(f"{nombre} compila", r.returncode == 0,
             " / ".join(detalle[:3]) if detalle else "")


print("\n=== NO SE LLAMA A HELPERS QUE NO EXISTEN ===")
# Solo los que empiezan con guion bajo. Es una convención del proyecto para los
# ayudantes propios de un archivo, así que si uno se llama y no está definido en
# ese mismo archivo, es un error seguro. Con nombres comunes habría que saber
# qué trae el navegador y qué otro archivo, y el chequeo se volvería ruidoso.
for nombre in archivos:
    js = script_de(os.path.join(PLANTILLAS, nombre))
    if not js.strip():
        continue
    # Sin limpiar comentarios ni textos, a propósito. Intentarlo con expresiones
    # regulares rompe las expresiones regulares del propio JavaScript —una barra
    # dentro de /[&<>"]/ parece el principio de un comentario— y se pierden
    # definiciones que sí existen. Un chequeo que grita en falso se ignora a la
    # segunda vez, y entonces no sirve para nada.
    #
    # Lo que se pierde al no limpiar son llamadas escritas dentro de un
    # comentario, que solo importarían si ese nombre no estuviera definido en
    # ninguna parte — que es justamente el caso que se busca.
    limpio = js

    llamados = set(re.findall(r"(?<![\w.])(_\w+)\s*\(", limpio))
    definidos = set(re.findall(r"function\s+(_\w+)", limpio))
    definidos |= set(re.findall(r"(?:const|let|var)\s+(_\w+)\s*=", limpio))
    # Los parámetros de una función también valen como definidos.
    definidos |= set(re.findall(r"\(\s*(_\w+)\s*[,)]", limpio))
    definidos |= set(re.findall(r",\s*(_\w+)\s*[,)=]", limpio))

    faltan = sorted(llamados - definidos)
    chequear(f"{nombre} no llama a helpers inexistentes", not faltan,
             ", ".join(faltan))


print(f"\n{'='*52}\n  {ok} pasaron, {fallos} fallaron\n{'='*52}")
raise SystemExit(1 if fallos else 0)
