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

        print(f"\n  Primeros {CUANTOS} registros, en bloques de 8 y de 16 bytes:")
        for tam in (8, 16):
            print(f"\n    --- de a {tam} bytes ---")
            for i in range(CUANTOS):
                trozo = datos[i * tam:(i + 1) * tam]
                if len(trozo) < tam:
                    break
                print(f"      {' '.join(f'{b:02X}' for b in trozo)}")

        # Cada candidato: donde cae el numero y donde el tiempo.
        candidatos = [
            ("8  uid(2) est(1) punch(1) tiempo(4)", 8, "<HBB4s", 0, 3, hora_zk),
            ("8  uid(2) est(1) tiempo(4) punch(1)", 8, "<HB4sB", 0, 2, hora_zk),
            ("16 legajo(4) tiempo(4) ...",          16, "<I4sBB2sI", 0, 1, hora_zk),
            ("16 legajo(4) tiempohex(6) ...",       16, "<I6s6s", 0, 1, hora_hex),
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
            marca = "  <<< ESTE" if buenas and buenas >= malas else ""
            print(f"    {nombre:38} creibles {buenas:>4}  raras {malas:>4}{marca}")
            for m in muestra:
                print(f"        {m}")
    finally:
        try:
            conexion.disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    main()
