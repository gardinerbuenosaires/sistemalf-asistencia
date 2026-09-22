"""
Relevamiento de los lectores ZKTeco de un local, antes de encarar el control de
accesos de puertas. Es la Fase 0 (inventario) y la Fase 2 (backup) del protocolo
de verificación, en un solo paso.

Para qué sirve. Antes de escribir una sola línea del módulo de puertas hay que
saber con qué equipos se cuenta: si son de la misma familia que el .201, si las
huellas que se enrolan ahí pueden viajar a las puertas, cuánta gente entra en
cada uno y qué hay cargado hoy. Y antes de escribir nada en ningún lector hay
que tener una copia de lo que tiene, porque sin copia la única vuelta atrás es
re-enrolar a la gente parada frente a cada puerta.

Qué hace, por cada lector:
  - Identidad: nombre, plataforma, firmware, serie, MAC.
  - Versión del algoritmo de huella y de rostro. Es la que decide si una
    huella enrolada en el maestro sirve en la puerta.
  - Capacidad y ocupación: usuarios, huellas y registros contra el máximo.
  - Algunas opciones de red y de servidor (DHCP, ADMS). Es de mejor esfuerzo:
    los nombres cambian según el firmware, y que no aparezca un valor NO
    significa que esté apagado. El menú del equipo es la fuente de verdad.
  - El padrón completo y todas las huellas, a un archivo por lector.

Prueba TCP y, si no contesta, UDP: los equipos viejos a veces solo hablan UDP,
y probarlos solo por TCP los da por muertos sin serlo.

Y después compara cada puerta contra el maestro: quién está en la puerta y no
en el maestro, qué números están cargados con otro nombre (señal de un número
reutilizado que en la puerta sigue siendo el empleado anterior), quiénes
figuran de baja en la base y siguen cargados, y si las huellas de la puerta son
byte a byte las del maestro. Si lo son, Enterprise ya las está copiando y eso
prueba en la práctica que viajan.

Es SOLO LECTURA. No llama a nada que modifique un equipo: ni disable_device,
ni set_*, ni clear_*, ni restart, ni unlock, ni reg_event. Se revisó el código
de pyzk 0.9 para confirmar que las lecturas que usa no escriben por debajo.
Puede correr con Enterprise andando.

Conecta con encoding latin-1 a propósito. pyzk decodifica los nombres con
errors='ignore', así que con el UTF-8 que trae por defecto un «Muñoz» grabado
en latin-1 pierde la ñ sin avisar, y un backup así no restaura igual. Latin-1
va y vuelve exacto byte a byte.

Los archivos que genera tienen huellas y claves numéricas de los empleados.
Quedan en la PC; la carpeta relevamiento/ está en .gitignore.

Uso:  python scripts/relevar_lectores.py IP [IP ...] [opciones]

      IP             cada lector de puerta. Si alguno tiene clave de
                     comunicación, IP@clave (por ejemplo 192.168.1.205@1234).
      --maestro IP   el lector donde se enrola. Por defecto el que está
                     configurado en el sistema (el .201).
      --clave N      clave de comunicación para los que no la indican.
                     Por defecto, la del maestro.
      --timeout N    segundos por operación. Por defecto 30, porque leer las
                     huellas de un equipo lleno tarda; 5 con --solo-conexion.
      --salida DIR   dónde dejar el informe y los backups.
      --sin-backup   solo el inventario, sin bajar huellas.
      --solo-conexion  solo ver qué equipos responden y por qué transporte.
                     Es lo primero que conviene correr: no baja padrones ni
                     huellas, así que tarda segundos.

La base la encuentra sola: primero DB_PATH si está definida, si no la ruta de
producción C:\\ProgramData\\SistemAlf\\fichajes.db. De ahí sale cuál es el maestro
de ese local y contra qué empleados cruzar el padrón de las puertas. Si no
aparece ninguna igual funciona, pasando --maestro, pero sin ese cruce.
"""
import sys, os

# La raiz del proyecto, y pararse ahi: DB_PATH sale de config.py como ruta
# relativa, asi que se resuelve contra el directorio actual. Va ANTES de
# importar config.
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.chdir(RAIZ)

# En produccion la base no esta al lado del codigo sino en C:\ProgramData, y el
# servicio se entera por DB_PATH, que corriendo a mano no esta definida. Sin
# esto hay que exportarla cada vez, y olvidarse no avisa: config.py cae en la
# ruta relativa y sqlite crea una base vacia ahi. Va ANTES de importar config,
# que lee la variable al importarse. Misma ruta que usa scripts\backup.ps1.
BASE_PRODUCCION = r"C:\ProgramData\SistemAlf\fichajes.db"
if not os.getenv("DB_PATH") and os.path.exists(BASE_PRODUCCION):
    os.environ["DB_PATH"] = BASE_PRODUCCION

import base64
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from zk import ZK, const

from config import DB_PATH

# La consola de Windows suele estar en cp1252: un carácter que no exista ahí
# no tiene que tirar abajo el relevamiento a mitad del backup.
try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass


def _argumento(nombre, defecto=None):
    if nombre in sys.argv:
        i = sys.argv.index(nombre) + 1
        if i >= len(sys.argv) or sys.argv[i].startswith("--"):
            sys.exit(f"Falta el valor de {nombre}")
        return sys.argv[i]
    return defecto


def _entero(nombre, defecto):
    valor = _argumento(nombre, defecto)
    try:
        return int(valor)
    except (TypeError, ValueError):
        sys.exit(f"{nombre} tiene que ser un número entero, no {valor!r}")


CON_VALOR = {"--maestro", "--clave", "--timeout", "--salida"}
SIN_VALOR = {"--sin-backup", "--solo-conexion"}


def _posicionales():
    """Los argumentos que no son opciones ni valores de opciones: las IPs."""
    salida, saltar = [], False
    for a in sys.argv[1:]:
        if saltar:
            saltar = False
        elif a in CON_VALOR:
            saltar = True
        elif a in SIN_VALOR:
            pass
        elif a.startswith("--"):
            sys.exit(f"Opción desconocida: {a}")
        else:
            salida.append(a)
    return salida


HAY_BASE = Path(DB_PATH).exists()
SIN_BACKUP = "--sin-backup" in sys.argv
SOLO_CONEXION = "--solo-conexion" in sys.argv
# En modo solo conexión la espera es corta: son dos intentos por equipo (TCP y
# UDP) y con varios lectores muertos la espera larga se hace eterna.
TIMEOUT = _entero("--timeout", 5 if SOLO_CONEXION else 30)

# El maestro y su clave: lo que diga la línea de comandos, si no lo configurado
# en el sistema. La configuración vive en la base, con config.py de respaldo.
if HAY_BASE:
    from sync.downloader import _get_device_config
    _cfg = _get_device_config()
else:
    from config import DEVICE_IP, DEVICE_PASSWORD
    _cfg = {"ip": DEVICE_IP, "password": DEVICE_PASSWORD}

MAESTRO = _argumento("--maestro")
if not MAESTRO and not HAY_BASE:
    # Para ver quien responde el maestro no hace falta: las IP de las puertas
    # las da el usuario. Se lo incluye igual cuando se sabe cual es, como
    # control: si el contesta y las puertas no, el problema son las puertas y
    # no el script ni la red. El relevamiento completo si lo necesita, porque
    # todo se compara contra el.
    if not SOLO_CONEXION:
        sys.exit(f"No encuentro la base en {Path(DB_PATH).resolve()}, así que no sé "
                 f"cuál es el maestro.\nDefiní DB_PATH o pasá --maestro IP.")
MAESTRO = MAESTRO or (_cfg["ip"] if HAY_BASE else None)
CLAVE_DEFECTO = _entero("--clave", _cfg["password"])


def _equipo(texto):
    """'IP' o 'IP@clave', devuelve (ip, clave)."""
    ip, _, clave = texto.partition("@")
    if not clave:
        return ip.strip(), CLAVE_DEFECTO
    try:
        return ip.strip(), int(clave)
    except ValueError:
        sys.exit(f"Clave inválida en {texto!r}: tiene que ser numérica.")


maestro_ip, maestro_clave = _equipo(MAESTRO) if MAESTRO else (None, CLAVE_DEFECTO)
PUERTAS = []
for texto in _posicionales():
    ip, clave = _equipo(texto)
    if ip != maestro_ip and ip not in (p[0] for p in PUERTAS):
        PUERTAS.append((ip, clave))

if not PUERTAS:
    sys.exit("Indicá al menos un lector de puerta.\n"
             "Uso: python scripts/relevar_lectores.py IP [IP ...] [--maestro IP]")

SELLO = datetime.now().strftime("%Y%m%d-%H%M%S")
if _argumento("--salida"):
    SALIDA = Path(_argumento("--salida"))
elif HAY_BASE:
    SALIDA = Path(DB_PATH).resolve().parent / "relevamiento" / SELLO
else:
    SALIDA = Path(RAIZ) / "relevamiento" / SELLO


# ---------------------------------------------------------------------------
# Lectura de un equipo
# ---------------------------------------------------------------------------

# Opciones que se piden por nombre. Los nombres dependen del firmware: si el
# equipo no conoce uno, devuelve error y se informa como «sin dato».
OPCIONES = ["DHCP", "WebServerIP", "WebServerPort", "ICLOCKSVRURL", "IclockSvrFun"]


def _opcion(zk, nombre):
    """
    Lee una opción del equipo por nombre (CMD_OPTIONS_RRQ, solo lectura).

    pyzk no expone una lectura genérica de opciones, pero la usa por dentro
    para get_fp_version y compañía. Se replica acá lo mismo que hacen esas
    funciones, incluido el _clear_error cuando el equipo no la reconoce.
    """
    comando = nombre.encode("ascii") + b"\x00"
    try:
        respuesta = zk._ZK__send_command(const.CMD_OPTIONS_RRQ, comando, 1024)
    except Exception:
        return None
    if respuesta.get("status"):
        valor = zk._ZK__data.split(b"=", 1)[-1].split(b"\x00")[0]
        return valor.decode("latin-1").strip()
    try:
        zk._clear_error(comando)
    except Exception:
        pass
    return None


def _seguro(funcion):
    """Una lectura que falla no corta el relevamiento: queda como None."""
    try:
        return funcion()
    except Exception:
        return None


def _conectar(ip, clave):
    """
    Primero TCP y, si no contesta, UDP. Los equipos de hace diez anios o mas a
    veces solo hablan UDP, y probar solo TCP los da por muertos sin serlo.
    Devuelve (conexion, transporte).
    """
    ultimo = None
    for udp in (False, True):
        zk = ZK(ip, port=4370, timeout=TIMEOUT, password=clave,
                force_udp=udp, ommit_ping=True, encoding="latin-1")
        try:
            return zk.connect(), ("UDP" if udp else "TCP")
        except Exception as exc:
            ultimo = exc
    raise ultimo


def leer_equipo(ip, clave, bajar_huellas):
    datos = {"ip": ip, "error": None, "transporte": None}
    conexion = None
    try:
        conexion, datos["transporte"] = _conectar(ip, clave)

        datos["nombre"]      = _seguro(conexion.get_device_name)
        datos["plataforma"]  = _seguro(conexion.get_platform)
        datos["firmware"]    = _seguro(conexion.get_firmware_version)
        datos["serie"]       = _seguro(conexion.get_serialnumber)
        datos["mac"]         = _seguro(conexion.get_mac)
        datos["fp_version"]  = _seguro(conexion.get_fp_version)
        datos["face_version"] = _seguro(conexion.get_face_version)
        datos["red"]         = _seguro(conexion.get_network_params)
        datos["opciones"]    = {n: _opcion(conexion, n) for n in OPCIONES}

        if SOLO_CONEXION:
            return datos

        conexion.read_sizes()
        datos["capacidad"] = {
            "usuarios": conexion.users,  "usuarios_max": conexion.users_cap,
            "huellas":  conexion.fingers, "huellas_max": conexion.fingers_cap,
            "registros": conexion.records, "registros_max": conexion.rec_cap,
            "rostros": getattr(conexion, "faces", 0),
            "rostros_max": getattr(conexion, "faces_cap", 0),
        }

        usuarios = conexion.get_users()
        # El tamaño de paquete lo ajusta get_users según lo que responde el
        # equipo: 28 es el formato viejo, 72 el nuevo. Si el maestro y una
        # puerta difieren, copiar usuarios entre ellos necesita cuidado.
        datos["paquete_usuario"] = conexion.user_packet_size
        datos["usuarios"] = [{
            "uid": u.uid,
            "user_id": str(u.user_id).strip(),
            "nombre": u.name,
            "privilegio": u.privilege,
            "clave": u.password,
            "grupo": u.group_id,
            "tarjeta": u.card,
        } for u in usuarios]

        if bajar_huellas:
            # get_templates identifica la huella por el uid interno del
            # equipo, no por el user_id. Se traduce acá.
            por_uid = {u.uid: str(u.user_id).strip() for u in usuarios}
            datos["huellas"] = [{
                "uid": h.uid,
                "user_id": por_uid.get(h.uid),
                "dedo": h.fid,
                "valida": h.valid,
                "template": base64.b64encode(h.template).decode("ascii"),
            } for h in conexion.get_templates()]

    except Exception as exc:
        datos["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if conexion:
            try:
                conexion.disconnect()
            except Exception:
                pass
    return datos


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------

def guardar_backup(datos):
    """
    Escribe el backup y lo vuelve a leer desde el disco para confirmar que
    está completo. Un backup que no se probó no es un backup.
    Devuelve (ruta, lista de problemas).
    """
    ruta = SALIDA / f"lector_{datos['ip'].replace('.', '_')}.json"
    copia = dict(datos, leido_en=SELLO)
    ruta.write_text(json.dumps(copia, ensure_ascii=False, indent=1), encoding="utf-8")

    problemas = []
    leido = json.loads(ruta.read_text(encoding="utf-8"))
    cap = datos["capacidad"]
    if len(leido["usuarios"]) != cap["usuarios"]:
        problemas.append(f"el equipo informa {cap['usuarios']} usuarios y se "
                         f"guardaron {len(leido['usuarios'])}")
    if len(leido.get("huellas", [])) != cap["huellas"]:
        problemas.append(f"el equipo informa {cap['huellas']} huellas y se "
                         f"guardaron {len(leido.get('huellas', []))}")
    originales = {(h["uid"], h["dedo"]): h["template"] for h in datos.get("huellas", [])}
    for h in leido.get("huellas", []):
        if originales.get((h["uid"], h["dedo"])) != h["template"]:
            problemas.append(f"la huella {h['dedo']} del uid {h['uid']} no "
                             f"se lee igual que la bajada")
            break
    sin_dueno = sum(1 for h in leido.get("huellas", []) if not h["user_id"])
    if sin_dueno:
        problemas.append(f"{sin_dueno} huellas no tienen usuario asociado")
    return ruta, problemas


# ---------------------------------------------------------------------------
# Informe
# ---------------------------------------------------------------------------

LINEAS = []


def p(texto=""):
    print(texto)
    LINEAS.append(texto)


def titulo(texto):
    p()
    p(texto)
    p("-" * len(texto))


def _v(valor):
    return "sin dato" if valor in (None, "") else str(valor)


def _lista(valores, tope=15):
    valores = sorted(valores, key=lambda x: (len(x), x))
    texto = ", ".join(valores[:tope])
    return texto + (f" … y {len(valores) - tope} más" if len(valores) > tope else "")


def empleados_de_la_base():
    if not HAY_BASE:
        return None
    from db.database import db_session
    with db_session() as conn:
        return {
            str(r["user_id"]).strip(): dict(r)
            for r in conn.execute(
                "SELECT user_id, nombre, apellido, activo, tipo, fecha_egreso "
                "FROM empleados WHERE user_id IS NOT NULL")
        }


def informar_equipo(rol, d):
    titulo(f"{rol} · {d['ip']}")
    if d["error"]:
        p(f"  NO SE PUDO LEER: {d['error']}")
        p("  Si Enterprise estaba conectado en ese momento, reintentar. Si es")
        p("  «Unauthenticated», el equipo tiene clave de comunicación: pasarla")
        p("  como IP@clave.")
        return
    p(f"  Responde por ..... {_v(d.get('transporte'))}")
    p(f"  Nombre ........... {_v(d['nombre'])}")
    p(f"  Plataforma ....... {_v(d['plataforma'])}")
    p(f"  Firmware ......... {_v(d['firmware'])}")
    p(f"  Serie / MAC ...... {_v(d['serie'])} / {_v(d['mac'])}")
    p(f"  Algoritmo huella . {_v(d['fp_version'])}")
    p(f"  Algoritmo rostro . {_v(d['face_version'])}")
    if SOLO_CONEXION:
        return
    paquete = d['paquete_usuario']
    p(f"  Paquete usuario .. {_v(int(paquete) if paquete else paquete)} bytes")
    red = d["red"] or {}
    p(f"  Red .............. {_v(red.get('ip'))} / {_v(red.get('mask'))} "
      f"gw {_v(red.get('gateway'))}")
    c = d["capacidad"]
    p(f"  Usuarios ......... {c['usuarios']} de {c['usuarios_max']}")
    p(f"  Huellas .......... {c['huellas']} de {c['huellas_max']}")
    p(f"  Registros ........ {c['registros']} de {c['registros_max']}")
    if c["rostros_max"]:
        p(f"  Rostros .......... {c['rostros']} de {c['rostros_max']}")
    p("  Opciones (mejor esfuerzo; «sin dato» no quiere decir apagado):")
    for nombre, valor in d["opciones"].items():
        p(f"    {nombre:<14} {_v(valor)}")

    usuarios = d["usuarios"]
    privilegios = Counter(u["privilegio"] for u in usuarios)
    grupos = Counter(u["grupo"] or "(vacío)" for u in usuarios)
    con_tarjeta = sum(1 for u in usuarios if u["tarjeta"])
    con_clave = sum(1 for u in usuarios if u["clave"])
    p(f"  Privilegios ...... " + ", ".join(
        f"{_PRIV.get(k, k)}: {n}" for k, n in sorted(privilegios.items())))
    p(f"  Grupos ........... " + ", ".join(
        f"{k}: {n}" for k, n in sorted(grupos.items(), key=str)))
    p(f"  Con tarjeta ...... {con_tarjeta}")
    p(f"  Con clave numérica {con_clave}")
    admins = [u["user_id"] for u in usuarios if u["privilegio"] == const.USER_ADMIN]
    if admins:
        p(f"  Administradores .. {_lista(admins)}")


_PRIV = {const.USER_DEFAULT: "común", const.USER_ENROLLER: "enrolador",
         const.USER_MANAGER: "gerente", const.USER_ADMIN: "admin"}


def comparar(maestro, puerta, empleados):
    titulo(f"Puerta {puerta['ip']} contra el maestro")
    if puerta["error"] or maestro["error"]:
        p("  No se puede comparar: falta leer uno de los dos equipos.")
        return

    # Compatibilidad
    fp_m, fp_p = maestro["fp_version"], puerta["fp_version"]
    if fp_m in (None, "") or fp_p in (None, ""):
        p("  Huellas: SIN DATO. Algún equipo no informó su algoritmo.")
    elif str(fp_m) == str(fp_p):
        p(f"  Huellas: MISMO ALGORITMO ({fp_p}). Una huella del maestro debería")
        p(f"  servir acá. Lo confirma la prueba física de la Fase 3.")
    else:
        p(f"  Huellas: ALGORITMO DISTINTO (maestro {fp_m}, puerta {fp_p}).")
        p(f"  Las huellas del maestro NO sirven en esta puerta: habría que")
        p(f"  enrolar aparte en esta familia de equipos.")
    if maestro["paquete_usuario"] != puerta["paquete_usuario"]:
        p(f"  Paquete de usuario distinto (maestro {int(maestro['paquete_usuario'])}, "
          f"puerta {int(puerta['paquete_usuario'])}): copiar usuarios necesita cuidado.")
    if (maestro["plataforma"], maestro["firmware"]) != (puerta["plataforma"], puerta["firmware"]):
        p(f"  Plataforma o firmware distintos al maestro.")

    # Padrón
    m = {u["user_id"]: u for u in maestro["usuarios"]}
    q = {u["user_id"]: u for u in puerta["usuarios"]}
    solo_puerta = set(q) - set(m)
    solo_maestro = set(m) - set(q)
    en_ambos = set(q) & set(m)
    p(f"  Padrón: {len(q)} en la puerta, {len(en_ambos)} también en el maestro.")
    p(f"    Faltan en la puerta (están en el maestro): {len(solo_maestro)}")
    if solo_puerta:
        p(f"    Solo en la puerta, no en el maestro: {len(solo_puerta)}")
        p(f"      {_lista(solo_puerta)}")
        p(f"      Alguien los cargó directo en la puerta, o el maestro los borró")
        p(f"      y la puerta no se enteró.")
    # Los equipos viejos guardan el nombre en un campo mas corto que el maestro
    # y lo cortan al grabarlo. Eso no es un numero reutilizado, y confundirlo
    # manda a investigar decenas de casos que no son. Se toma como corte el
    # nombre mas largo que hay en esa puerta: si el de la puerta llega a ese
    # largo y ademas es el principio del nombre del maestro, esta cortado.
    limite = max((len(u["nombre"].strip()) for u in puerta["usuarios"]), default=0)
    distintos, cortados = [], []
    for uid in en_ambos:
        en_puerta, en_maestro = q[uid]["nombre"].strip(), m[uid]["nombre"].strip()
        if en_puerta == en_maestro:
            continue
        if (limite and len(en_puerta) == limite
                and en_maestro.startswith(en_puerta)):
            cortados.append(uid)
        else:
            distintos.append(uid)

    if cortados:
        p(f"    Nombres cortados por el equipo a {limite} caracteres: {len(cortados)}."
          f" No es un problema.")
    if distintos:
        distintos.sort(key=lambda x: (len(x), x))
        p(f"    Mismo número, distinto nombre que en el maestro: {len(distintos)}")
        for uid in distintos[:15]:
            p(f"      {uid}: puerta «{q[uid]['nombre']}» · maestro «{m[uid]['nombre']}»")
        p(f"      Si el número se reutilizó en el maestro, en esta puerta sigue")
        p(f"      cargado el empleado anterior, con su huella.")

    # Cruce con la base
    if empleados is not None:
        de_baja = [uid for uid in q if uid in empleados and not empleados[uid]["activo"]]
        desconocidos = [uid for uid in q if uid not in empleados]
        if de_baja:
            p(f"  De baja en el sistema y cargados en esta puerta: {len(de_baja)}")
            for uid in sorted(de_baja, key=lambda x: (len(x), x))[:15]:
                e = empleados[uid]
                p(f"    {uid}: {e['apellido']}, {e['nombre']} · egreso {_v(e['fecha_egreso'])}")
            p(f"    Si esta puerta está en servicio, estas huellas abren.")
        if desconocidos:
            p(f"  Números que no existen en la base: {len(desconocidos)}")
            p(f"    {_lista(desconocidos)}")

    # Huellas byte a byte
    if "huellas" in maestro and "huellas" in puerta:
        hm = {(h["user_id"], h["dedo"]): h["template"] for h in maestro["huellas"]}
        hp = {(h["user_id"], h["dedo"]): h["template"] for h in puerta["huellas"]
              if h["user_id"] in en_ambos}
        comunes = [k for k in hp if k in hm]
        iguales = sum(1 for k in comunes if hp[k] == hm[k])
        if comunes:
            p(f"  Huellas idénticas byte a byte al maestro: {iguales} de {len(comunes)}")
            if iguales == len(comunes):
                p(f"    Todas iguales: Enterprise las copia del maestro. Es la prueba")
                p(f"    práctica de que las huellas viajan a este equipo.")
            elif iguales == 0:
                p(f"    Ninguna igual: se enrolaron aparte en la puerta, o el equipo")
                p(f"    las recodifica al guardarlas. La Fase 3 lo resuelve.")
            else:
                p(f"    Mezcla: parte copiadas y parte enroladas aparte.")
        else:
            p(f"  Huellas: no hay ninguna del mismo empleado y dedo en los dos equipos.")


# ---------------------------------------------------------------------------

def main():
    SALIDA.mkdir(parents=True, exist_ok=True)

    p(f"Relevamiento de lectores · {datetime.now():%d/%m/%Y %H:%M}")
    p(f"Base: {Path(DB_PATH).resolve() if HAY_BASE else 'no encontrada (no se cruza con empleados)'}")
    p(f"Maestro: {maestro_ip or 'no sé cuál es, se relevan solo las puertas'} · "
      f"Puertas: {', '.join(ip for ip, _ in PUERTAS)}")
    p(f"Salida: {SALIDA.resolve()}")
    p("Solo lectura: no se modifica ningún equipo.")
    if SOLO_CONEXION:
        p("Modo solo conexión: nada más que ver quién responde y por dónde.")

    bajar = not (SIN_BACKUP or SOLO_CONEXION)
    print("\nLeyendo equipos…", flush=True)
    leidos = []
    cola = ([("Maestro", (maestro_ip, maestro_clave))] if maestro_ip else [])          + [("Puerta", x) for x in PUERTAS]
    for rol, (ip, clave) in cola:
        print(f"  {ip} …", end=" ", flush=True)
        d = leer_equipo(ip, clave, bajar)
        if d["error"]:
            print("no responde", flush=True)
        elif SOLO_CONEXION:
            print(f"responde por {d['transporte']}", flush=True)
        else:
            print(f"responde por {d['transporte']}, {len(d['usuarios'])} usuarios"
                  + (f", {len(d['huellas'])} huellas" if bajar else ""), flush=True)
        leidos.append((rol, d))

    for rol, d in leidos:
        informar_equipo(rol, d)

    if not SOLO_CONEXION:
        maestro = leidos[0][1]
        empleados = empleados_de_la_base()
        for rol, d in leidos[1:]:
            comparar(maestro, d, empleados)

    if bajar:
        titulo("Backup")
        for rol, d in leidos:
            if d["error"]:
                p(f"  {d['ip']}: SIN BACKUP, no se pudo leer el equipo.")
                continue
            ruta, problemas = guardar_backup(d)
            if problemas:
                p(f"  {d['ip']}: INCOMPLETO, en {ruta.name}")
                for problema in problemas:
                    p(f"    - {problema}")
            else:
                p(f"  {d['ip']}: verificado, en {ruta.name} "
                  f"({len(d['usuarios'])} usuarios, {len(d['huellas'])} huellas)")
        p("  Contienen huellas y claves de empleados: no copiarlos fuera de la PC.")

    p()
    p("Queda por verificar en el menú de cada equipo (no se lee por software):")
    p("  - Comm > ADMS / Cloud Server: si apunta a Enterprise, el equipo se")
    p("    sincroniza solo y va a pisar lo que se escriba.")
    p("  - Si la IP es fija o por DHCP, cuando la opción DHCP salió «sin dato».")

    informe = SALIDA / "informe.txt"
    informe.write_text("\n".join(LINEAS) + "\n", encoding="utf-8")
    print(f"\nInforme guardado en {informe.resolve()}")

    if any(d["error"] for _, d in leidos):
        sys.exit(1)


if __name__ == "__main__":
    main()
