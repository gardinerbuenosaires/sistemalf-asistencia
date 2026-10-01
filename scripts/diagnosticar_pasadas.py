"""Qué bytes manda un lector cuando se le piden las pasadas, y cómo leerlos.

Existe porque dos equipos del mismo local devolvieron formatos distintos y
adivinar no alcanzó: uno daba fechas del siglo XXII con las horas bien, que es
la firma de que los bits bajos del tiempo se leen correctos y los altos no.

SOLO LECTURA. No le escribe nada a ningún equipo.

Lo que hace: baja el bloque crudo, muestra los primeros registros en hexadecimal
y prueba cada formato conocido contra el reloj del propio equipo, diciendo
cuántas fechas creíbles produce cada uno. El que gana es el que hay que usar.

Uso:  python scripts/diagnosticar_pasadas.py IP [--cuantos 8]
"""
import os
import sys
from datetime import datetime, timedelta
from struct import unpack

sys.stdout.reconfigure(errors="replace")
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.chdir(RAIZ)

BASE_PRODUCCION = r"C:\ProgramData\SistemAlf\fichajes.db"
if not os.getenv("DB_PATH") and os.path.exists(BASE_PRODUCCION):
    os.environ["DB_PATH"] = BASE_PRODUCCION

if len(sys.argv) < 2:
    print(__doc__)
    raise SystemExit(1)
IP = sys.argv[1]
CUANTOS = int(sys.argv[sys.argv.index("--cuantos") + 1]) if "--cuantos" in sys.argv else 8


def hora_zk(crudo):
    t = unpack("<I", crudo)[0]
    seg = t % 60;  t //= 60
    mi  = t % 60;  t //= 60
    h   = t % 24;  t //= 24
    d   = t % 31 + 1;  t //= 31
    m   = t % 12 + 1;  t //= 12
    return datetime(t + 2000, m, d, h, mi, seg)


def hora_hex(crudo):
    """El otro formato de ZK: los seis valores como bytes sueltos."""
    a, m, d, h, mi, s = unpack("6B", crudo[:6])
    return datetime(a + 2000, m, d, h, mi, s)


# Unix, por si el equipo guarda segundos desde 1970 en vez del empaquetado de
# ZK. Se prueban varias composiciones de los cuatro bytes porque las lecturas
# directas dan fechas del siglo XXI tardio, y en un equipo los bytes del tiempo
# aparecieron en un orden que no es ni little ni big endian puro.
def unix_le(c):  return datetime.fromtimestamp(int.from_bytes(c[:4], "little"))
def unix_be(c):  return datetime.fromtimestamp(int.from_bytes(c[:4], "big"))
def unix_mix(c): return datetime.fromtimestamp(
    (c[2] << 24) | (c[3] << 16) | (c[1] << 8) | c[0])
def unix_mix2(c): return datetime.fromtimestamp(
    (c[3] << 24) | (c[2] << 16) | (c[0] << 8) | c[1])
def unix_le_utc(c): return datetime.utcfromtimestamp(int.from_bytes(c[:4], "little"))
def unix_mix_utc(c): return datetime.utcfromtimestamp(
    (c[2] << 24) | (c[3] << 16) | (c[1] << 8) | c[0])


def main():
    from zk import ZK, const

    clave = 0
    try:
        from db.database import db_session
        with db_session() as conn:
            fila = conn.execute(
                "SELECT password FROM dispositivos WHERE ip=?", (IP,)).fetchone()
            if fila and fila["password"]:
                clave = fila["password"]
    except Exception:
        pass

    conexion = None
    for udp in (False, True):
        try:
            conexion = ZK(IP, port=4370, timeout=30, password=int(clave),
                          force_udp=udp, ommit_ping=True, encoding="latin-1").connect()
            print(f"\n  Conectado a {IP} por {'UDP' if udp else 'TCP'}")
            break
        except Exception as exc:
            ultimo = exc
    if conexion is None:
        raise SystemExit(f"  No contestó: {ultimo}")

    try:
        reloj = conexion.get_time()
        print(f"  El equipo marca {reloj}   (esta PC: {datetime.now():%Y-%m-%d %H:%M:%S})")

        usuarios = {u.uid: str(u.user_id).strip() for u in conexion.get_users()}
        conexion.read_sizes()
        print(f"  Dice tener {conexion.records} pasadas y {conexion.users} usuarios")

        datos, _ = conexion.read_with_buffer(const.CMD_ATTLOG_RRQ)
        total = unpack("I", datos[:4])[0]
        datos = datos[4:]
        print(f"  Bloque: {total} bytes declarados, {len(datos)} recibidos")
        if conexion.records:
            print(f"  Tamaño por registro: {total / conexion.records:.3f}"
                  f"   (entero: {total // conexion.records})")

        print(f"\n  PRIMEROS {CUANTOS} registros, de a 8 bytes:")
        for i in range(CUANTOS):
            trozo = datos[i * 8:(i + 1) * 8]
            if len(trozo) < 8:
                break
            print(f"      {' '.join(f'{b:02X}' for b in trozo)}")

        # El dato que decide. Media docena de ordenes de bytes dan fechas
        # plausibles; solo uno pone la pasada mas nueva a horas del reloj del
        # propio equipo, que es donde tiene que estar si la puerta se usa.
        print(f"\n  ULTIMOS {CUANTOS} registros (tienen que ser de hace horas):")
        for i in range(max(0, len(datos) // 8 - CUANTOS), len(datos) // 8):
            trozo = datos[i * 8:(i + 1) * 8]
            print(f"      {' '.join(f'{b:02X}' for b in trozo)}")

        # La forma del bloque entero. Las puntas engañaron: los dos extremos
        # eran el mismo usuario y el par alto bajaba del primero al último, así
        # que esto no es una lista ordenada por tiempo y mirar ocho registros de
        # cada lado no alcanza.
        referencia = reloj or datetime.now()
        print("\n  FORMA DEL BLOQUE")
        cuantos = len(datos) // 8
        uids, altos, orden_uids, campos23 = {}, {}, [], set()
        for i in range(cuantos):
            r = datos[i * 8:(i + 1) * 8]
            u = r[0] | r[1] << 8
            if u not in uids:
                orden_uids.append((i, u))
            uids[u] = uids.get(u, 0) + 1
            campos23.add(r[2] | r[3] << 8)
            altos[r[6] << 8 | r[7]] = altos.get(r[6] << 8 | r[7], 0) + 1

        print(f"    {len(uids)} usuarios distintos en {cuantos} registros")
        print(f"    bytes 2-3: {len(campos23)} valor(es) distinto(s) -> "
              + ", ".join(f"0x{v:04X}" for v in sorted(campos23)[:6]))
        print(f"    dónde aparece cada usuario por primera vez (indice: uid):")
        print("      " + "  ".join(f"{i}:{u}" for i, u in orden_uids[:14]))
        cambios = sum(1 for i in range(1, cuantos)
                      if (datos[i * 8] | datos[i * 8 + 1] << 8)
                      != (datos[(i - 1) * 8] | datos[(i - 1) * 8 + 1] << 8))
        print(f"      el usuario cambia {cambios} veces en {cuantos} registros"
              f" -> {'agrupados' if cambios < len(uids) * 3 else 'mezclados'}")

        en_orden = sorted(altos)
        print(f"\n    par alto (bytes 6-7 en BE): {len(altos)} valores distintos")
        print(f"      del 0x{en_orden[0]:04X} al 0x{en_orden[-1]:04X}")
        print(f"      los 8 más repetidos: " + "  ".join(
            f"0x{k:04X}({v})" for k, v in sorted(altos.items(), key=lambda x: -x[1])[:8]))

        # Si el tiempo fuesen segundos armados como par_alto*65536 + par_bajo,
        # el máximo tiene que caer sobre el reloj del equipo. La fecha desde la
        # que habría que contar para que eso pase identifica la época, que es el
        # único dato que falta.
        print("\n    si el tiempo fuesen segundos = par_alto*65536 + par_bajo:")
        for nombre, leer in (("alto BE", lambda r: r[6] << 8 | r[7]),
                             ("alto LE", lambda r: r[7] << 8 | r[6])):
            vals = [leer(datos[i * 8:(i + 1) * 8]) * 65536
                    + (datos[i * 8 + 4] | datos[i * 8 + 5] << 8)
                    for i in range(cuantos)]
            span = (max(vals) - min(vals)) / 86400
            desde = referencia - timedelta(seconds=max(vals))
            print(f"      {nombre}: abarca {span:.1f} días; para que el más nuevo"
                  f" sea ahora habría que contar desde {desde:%d-%m-%Y}")

        # Cada candidato: donde cae el numero y donde el tiempo.
        candidatos = [
            ("8  ZK empaquetado en el byte 4",   8, "<HBB4s", 0, 3, hora_zk),
            ("8  ZK empaquetado en el byte 3",   8, "<HB4sB", 0, 2, hora_zk),
            ("8  Unix little endian",            8, "<HBB4s", 0, 3, unix_le),
            ("8  Unix big endian",               8, "<HBB4s", 0, 3, unix_be),
            ("8  Unix mezclado (alto BE)",       8, "<HBB4s", 0, 3, unix_mix),
            ("8  Unix mezclado (bajo BE)",       8, "<HBB4s", 0, 3, unix_mix2),
            ("8  Unix little endian en UTC",     8, "<HBB4s", 0, 3, unix_le_utc),
            ("8  Unix mezclado en UTC",          8, "<HBB4s", 0, 3, unix_mix_utc),
            ("16 legajo(4) + ZK empaquetado",    16, "<I4sBB2sI", 0, 1, hora_zk),
            ("16 legajo(4) + tiempohex(6)",      16, "<I6s6s", 0, 1, hora_hex),
        ]
        limite = datetime.now() + timedelta(days=2)
        piso = datetime(2015, 1, 1)
        referencia = reloj or datetime.now()

        print(f"\n  Que produce cada formato sobre {min(200, len(datos) // 8)} registros:")
        for nombre, tam, patron, i_num, i_t, decodificar in candidatos:
            buenas = malas = 0
            muestra = []
            resto = datos[:tam * 200]
            while len(resto) >= tam:
                try:
                    campos = unpack(patron, resto[:tam])
                    cuando = decodificar(campos[i_t])
                    if piso <= cuando <= max(limite, referencia + timedelta(days=2)):
                        buenas += 1
                        if len(muestra) < 3:
                            num = campos[i_num]
                            muestra.append(f"{usuarios.get(num, num)} {cuando:%d-%m-%Y %H:%M:%S}")
                    else:
                        malas += 1
                        if len(muestra) < 3:
                            muestra.append(f"? {cuando:%d-%m-%Y %H:%M:%S}")
                except Exception:
                    malas += 1
                resto = resto[tam:]
            # El ultimo registro del bloque es el que decide entre candidatos
            # que parecen todos creibles.
            ultimo = None
            try:
                fin = (len(datos) // tam - 1) * tam
                ultimo = decodificar(unpack(patron, datos[fin:fin + tam])[i_t])
            except Exception:
                pass
            marca = "  <<< ESTE" if buenas and buenas >= malas else ""
            print(f"    {nombre:34} creibles {buenas:>4}  raras {malas:>4}{marca}")
            for m in muestra:
                print(f"        {m}")
            if ultimo:
                horas = abs((referencia - ultimo).total_seconds()) / 3600
                print(f"        ultimo: {ultimo:%d-%m-%Y %H:%M:%S}"
                      f"   a {horas:.1f} h del reloj del equipo")
    finally:
        try:
            conexion.disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    main()
