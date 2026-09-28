"""Prueba de ESCRITURA contra un lector de puerta: el alta de un usuario.

Es la primera vez que este sistema le escribe algo a un equipo. Por eso se hace
sobre un número descartable, en una sola puerta, a mano, y verificando.

Qué prueba y qué no. Prueba que se puede crear un usuario sin romper a los que
ya están. NO prueba que ese usuario abra la puerta: para eso falta escribirle una
huella, que es el paso siguiente y va aparte.

Lo que NUNCA hace:
  · escribirle al equipo de asistencia (se niega si la IP es la del maestro)
  · escribir si la lectura previa no es confiable
  · usar un número que ya exista en el equipo o en el sistema
  · tocar ninguna base de datos

Sobre la base. La lee, y solo para dos cosas: saber cuál es el equipo de
asistencia —para negarse a tocarlo— y qué números ya están usados. Son tres
SELECT; no llama a `init_db()`, así que no crea tablas ni corre migraciones.

Y lee la de PRODUCCIÓN a propósito, no una copia. Las dos preguntas que hace
solo valen contra la lista real: en una copia de hace días, un número puede
figurar libre y estar asignado desde ayer. Una protección que consulta datos
viejos es peor que no tenerla, porque da confianza sin darla.

Sobre el índice interno. Cada usuario tiene un `uid`, que es su posición dentro
del equipo, distinta de su número de legajo. `set_user` sin uid usa el que pyzk
calcula: el máximo de los usuarios que acaba de leer, más uno. Si esa lectura
viniera incompleta, el máximo saldría bajo y se sobrescribiría a alguien que ya
está. Ese es el modo de falla que destruye datos sin avisar, y es la razón de
que acá se verifique la lectura antes de escribir y se pase el uid explícito.

Uso:  python scripts/probar_escritura.py alta   IP NUMERO [--grupo N]
      python scripts/probar_escritura.py huella IP NUMERO NUMERO_EN_EL_MAESTRO

      IP          la puerta donde probar. NUNCA el maestro.
      NUMERO      el número descartable (por ejemplo 9990)

      alta        crea el usuario de prueba, sin huella. No abre la puerta.
      --grupo N   en qué grupo crearlo. Por defecto, el grupo más frecuente
                  entre los que ya están en ese equipo: si todos abren estando
                  en el grupo 1, crear el de prueba en el 1 reproduce lo que
                  pasa de verdad. Crearlo en otro probaría otra cosa.

      huella      copia al usuario de prueba las huellas que una persona tiene
                  en el maestro. El maestro se LEE, nunca se le escribe. El
                  destino tiene que llamarse PRUEBA: es la red contra el error
                  que más caro sale, que es equivocarse de número y pisarle la
                  huella a un empleado real.

      --si        no preguntar antes de escribir (para no tipear dos veces)
"""
import os
import sys
from collections import Counter

sys.stdout.reconfigure(errors="replace")

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.chdir(RAIZ)

BASE_PRODUCCION = r"C:\ProgramData\SistemAlf\fichajes.db"
if not os.getenv("DB_PATH") and os.path.exists(BASE_PRODUCCION):
    os.environ["DB_PATH"] = BASE_PRODUCCION


def salir(mensaje):
    print(f"\n  {mensaje}\n")
    raise SystemExit(1)


def argumento(nombre, defecto=None):
    if nombre in sys.argv:
        i = sys.argv.index(nombre)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return defecto


def conectar(ip, clave, timeout=30):
    """TCP y si no contesta UDP, igual que el relevamiento."""
    from zk import ZK

    ultimo = None
    for udp in (False, True):
        try:
            zk = ZK(ip, port=4370, timeout=timeout, password=int(clave),
                    force_udp=udp, ommit_ping=True, encoding="latin-1")
            return zk.connect(), ("udp" if udp else "tcp")
        except Exception as exc:
            ultimo = exc
    raise ultimo


def foto(conexion):
    """
    Cómo está el equipo ahora: cada usuario con su índice, nombre, grupo y
    cuántas huellas tiene. Es contra esto que se compara después, y es lo que
    permite afirmar que no se rompió nada en vez de suponerlo.
    """
    usuarios = conexion.get_users()
    declarados = getattr(conexion, "users", None)
    huellas = Counter()
    for h in conexion.get_templates():
        if getattr(h, "valid", 1):
            huellas[h.uid] += 1
    return {
        "declarados": declarados,
        # Los objetos tal como los devuelve pyzk. Hacen falta para escribir una
        # huella: `save_user_template` quiere el User del equipo DESTINO, y
        # armarlo a mano perdería el grupo y el privilegio que ya tiene.
        "crudos": {str(u.user_id).strip(): u for u in usuarios},
        "usuarios": {
            str(u.user_id).strip(): {
                "uid": u.uid,
                "nombre": (u.name or "").strip(),
                "grupo": str(u.group_id).strip(),
                "privilegio": u.privilege,
                "huellas": huellas.get(u.uid, 0),
            } for u in usuarios
        },
    }


def lectura_confiable(f):
    """
    El equipo dice cuántos usuarios tiene; si lo leído no coincide, la lectura
    vino cortada. Escribir sobre una lectura cortada es lo que sobrescribe a
    alguien: el índice libre se calcularía a partir de una lista incompleta.
    """
    leidos, dice = len(f["usuarios"]), f["declarados"]
    if dice is None:
        return False, "el equipo no dijo cuántos usuarios tiene"
    if leidos != dice:
        return False, f"el equipo dice tener {dice} usuarios y se leyeron {leidos}"
    if leidos == 0:
        return False, "el equipo contestó con cero usuarios"
    return True, f"{leidos} usuarios, coincide con lo que declara el equipo"


def comparar(antes, despues, excepto=None):
    """
    Que el cambio buscado haya ocurrido no alcanza: hay que probar que a los
    demás no les pasó nada. Un alta que corre índices, o una huella que se
    escribe encima de otra persona, se ven acá y en ningún otro lado.

    `excepto` es el número al que SÍ se le espera un cambio. Todos los demás
    tienen que quedar idénticos, y nadie más puede aparecer.
    """
    problemas = []
    for numero, a in antes["usuarios"].items():
        if numero == excepto:
            continue
        d = despues["usuarios"].get(numero)
        if d is None:
            problemas.append(f"DESAPARECIÓ el {numero} ({a['nombre']})")
            continue
        for campo in ("uid", "nombre", "grupo", "privilegio", "huellas"):
            if a[campo] != d[campo]:
                problemas.append(
                    f"al {numero} ({a['nombre']}) le cambió {campo}: "
                    f"{a[campo]} -> {d[campo]}")
    aparecidos = set(despues["usuarios"]) - set(antes["usuarios"])
    for numero in sorted(aparecidos - {excepto}):
        problemas.append(f"APARECIÓ un {numero} que nadie creó")
    return problemas


def leer_huellas_del_maestro(ip, clave, numero):
    """
    Trae las huellas de una persona del equipo donde se enrola. SOLO LECTURA.

    El maestro es el único equipo donde la huella es original y no una copia:
    una prueba que salga mal ahí no se repara con un backup, se repara volviendo
    a enrolar gente. Por eso acá solo se lee, nunca se escribe.
    """
    conexion = None
    try:
        conexion, transporte = conectar(ip, clave)
        usuarios = conexion.get_users()
        yo = next((u for u in usuarios if str(u.user_id).strip() == numero), None)
        if yo is None:
            salir(f"El número {numero} no está en el equipo de asistencia ({ip}).")
        mias = [h for h in conexion.get_templates()
                if h.uid == yo.uid and getattr(h, "valid", 1)]
        return transporte, (yo.name or "").strip(), mias
    finally:
        if conexion:
            try:
                conexion.disconnect()
            except Exception:
                pass


def datos_del_sistema(ip, numero, numero_debe_estar_libre):
    """
    Lo que hace falta saber antes de escribirle a un equipo. SOLO SELECT.

    Devuelve (maestro_ip, clave, puerta). El maestro no se toca nunca: es el
    único equipo donde la huella es original y no una copia, así que una prueba
    que salga mal ahí no se repara con un backup sino volviendo a enrolar gente.

    De dónde sale el maestro depende de qué base se esté mirando. La de
    producción todavía no tiene la tabla `dispositivos` —la crea la rama de
    accesos— y ahí la IP del lector vive en `configuracion.device_ip`. Las dos
    sirven: lo único que hace falta es saber cuál equipo NO tocar.
    """
    maestro_ip = clave = puerta = None
    try:
        from db.database import db_session
        with db_session() as conn:
            hay_tabla = conn.execute(
                """SELECT 1 FROM sqlite_master
                    WHERE type='table' AND name='dispositivos'""").fetchone()
            if hay_tabla:
                fila = conn.execute(
                    """SELECT ip, password FROM dispositivos
                        WHERE activo=1 AND cuenta_asistencia=1 AND ip IS NOT NULL
                     ORDER BY orden, id LIMIT 1""").fetchone()
                if fila:
                    maestro_ip, clave = fila["ip"], fila["password"]
                puerta = conn.execute(
                    "SELECT nombre, password FROM dispositivos WHERE ip=?", (ip,)).fetchone()
                if puerta:
                    clave = puerta["password"]
            else:
                cfg = {r["clave"]: r["valor"] for r in conn.execute(
                    "SELECT clave, valor FROM configuracion WHERE clave LIKE 'device_%'")}
                maestro_ip = (cfg.get("device_ip") or "").strip() or None
                clave = cfg.get("device_password") or 0

            ocupado = conn.execute(
                """SELECT apellido, nombre, activo FROM empleados
                    WHERE TRIM(user_id) = ?""", (numero,)).fetchone()
            # Para sugerir uno libre si el pedido está tomado. Pasa más de lo
            # que uno espera: los números redondos ya se usaron alguna vez, y
            # la importación desde el lector crea legajos con ese nombre.
            tomados = {str(r[0]).strip() for r in conn.execute(
                "SELECT user_id FROM empleados WHERE user_id IS NOT NULL")}
    except Exception as exc:
        salir(f"No se pudo leer la base del sistema: {exc}\n"
              f"  Sin eso no puedo verificar que la IP no sea el maestro.")

    # Sin saber cuál es el maestro no hay protección, y esa protección es el
    # motivo por el que este script consulta la base. Mejor no correr.
    if not maestro_ip:
        salir("La base no dice cuál es el equipo de asistencia.\n"
              "  Sin eso no puedo garantizar que esta IP no sea el maestro, que es\n"
              "  lo único que este script no puede tocar.")

    if ip == maestro_ip:
        salir(f"{ip} es el equipo de asistencia. Este script no le escribe.")
    if ocupado and numero_debe_estar_libre:
        libres = [str(n) for n in range(9990, 9000, -1) if str(n) not in tomados][:4]
        salir(f"El número {numero} es de {ocupado['apellido']}, {ocupado['nombre']} "
              f"({'activo' if ocupado['activo'] else 'dado de baja'}).\n"
              f"  Un legajo dado de baja sigue ocupando el número, así que no sirve.\n"
              + (f"  Libres en la base: {', '.join(libres)}"
                 if libres else "  No encontré ninguno libre entre 9001 y 9990."))
    return maestro_ip, clave, puerta


def cabecera(titulo, ip, numero, puerta, maestro_ip):
    print(f"\n  Prueba de ESCRITURA — {titulo}")
    print(f"  " + "-" * (23 + len(titulo)))
    print(f"  Puerta : {ip}"
          + (f"  ({puerta['nombre']})" if puerta else "  (no está en el sistema)"))
    print(f"  Número : {numero}")
    print(f"  Base   : {os.environ.get('DB_PATH', '(la que resuelva config)')}")
    print(f"           se lee, NO se escribe: de acá salen los números ya usados")
    print(f"  Maestro: {maestro_ip}  — no se toca")


def abrir(ip, clave):
    try:
        conexion, transporte = conectar(ip, clave or 0)
    except Exception as exc:
        salir(f"El equipo no contestó: {type(exc).__name__}: {exc}")
    print(f"  Conectado por {transporte.upper()}")
    return conexion


def modo_alta(ip, numero):
    maestro_ip, clave, puerta = datos_del_sistema(ip, numero, True)
    grupo_pedido = argumento("--grupo")
    cabecera("alta de un usuario", ip, numero, puerta, maestro_ip)
    conexion = abrir(ip, clave)

    try:
        antes = foto(conexion)
        ok, detalle = lectura_confiable(antes)
        print(f"  Lectura previa: {detalle}")
        if not ok:
            salir("Lectura no confiable. NO se escribe nada.\n"
                  "  Volvé a correrlo; si sigue igual, el equipo tiene un problema.")

        if numero in antes["usuarios"]:
            salir(f"El equipo ya tiene un usuario {numero}. Elegí otro número.")

        usados = {u["uid"] for u in antes["usuarios"].values()}
        nuevo_uid = max(usados) + 1
        if nuevo_uid in usados:
            salir("No pude calcular un índice libre.")

        if grupo_pedido is not None:
            grupo = str(grupo_pedido)
            por_que = "pedido con --grupo"
        else:
            cuenta = Counter(u["grupo"] for u in antes["usuarios"].values() if u["grupo"])
            grupo = cuenta.most_common(1)[0][0] if cuenta else "1"
            por_que = f"el más frecuente acá ({cuenta.most_common(1)[0][1]} de {len(antes['usuarios'])})"

        print(f"\n  Se va a crear:")
        print(f"     número {numero}, nombre PRUEBA, grupo {grupo}  ({por_que})")
        print(f"     índice interno {nuevo_uid}, que hoy está libre")
        print(f"\n  Sin huella todavía: este usuario NO va a abrir la puerta.")

        if "--si" not in sys.argv:
            if input("\n  ¿Escribir? (s/n) ").strip().lower() != "s":
                salir("Cancelado. No se escribió nada.")

        conexion.set_user(uid=nuevo_uid, name="PRUEBA", privilege=0,
                          password="", group_id=grupo, user_id=numero)
        print("\n  Escrito. Volviendo a leer para verificar…")

        despues = foto(conexion)
        ok2, detalle2 = lectura_confiable(despues)
        creado = despues["usuarios"].get(numero)
        problemas = comparar(antes, despues, numero)

        print(f"\n  {'OK   ' if creado else 'FALLA'} el usuario {numero} "
              f"{'existe en el equipo' if creado else 'NO aparece'}")
        if creado:
            print(f"        índice {creado['uid']}, nombre «{creado['nombre']}», "
                  f"grupo {creado['grupo']}, {creado['huellas']} huellas")
            if creado["uid"] != nuevo_uid:
                print(f"        OJO: pedí el índice {nuevo_uid} y quedó en {creado['uid']}")
        print(f"  {'OK   ' if not problemas else 'FALLA'} los otros "
              f"{len(antes['usuarios'])} usuarios "
              f"{'quedaron intactos' if not problemas else 'NO quedaron intactos'}")
        for p in problemas:
            print(f"        {p}")
        if not ok2:
            print(f"  OJO   la lectura posterior tampoco es confiable: {detalle2}")

        if creado and not problemas and ok2:
            print(f"\n  El alta funciona en este equipo.")
            print(f"  Siguiente paso: escribirle una huella y probar si abre.")
        else:
            print(f"\n  Algo no salió como se esperaba. NO sigas con la huella.")
            print(f"  El backup de esta puerta es de antes de esto.")
    finally:
        try:
            conexion.disconnect()
        except Exception:
            pass


def modo_huella(ip, numero, numero_maestro):
    """
    Copia la huella de una persona del maestro al usuario de prueba de una
    puerta. Es lo que prueba de verdad que las huellas son portables entre
    equipos: hasta acá solo sabíamos que Enterprise las copia, no que nosotros
    podamos.

    El usuario destino tiene que llamarse PRUEBA. Es la red contra el error que
    más caro sale: equivocarse de número y escribirle la huella de alguien
    encima de un empleado real.
    """
    maestro_ip, clave, puerta = datos_del_sistema(ip, numero, False)
    cabecera("escribir una huella", ip, numero, puerta, maestro_ip)

    print(f"\n  Leyendo la huella del {numero_maestro} en el maestro…")
    transporte_m, nombre_m, mias = leer_huellas_del_maestro(
        maestro_ip, clave, numero_maestro)
    if not mias:
        salir(f"El {numero_maestro} ({nombre_m}) no tiene ninguna huella "
              f"enrolada en el maestro. No hay nada que copiar.")
    dedos = ", ".join(str(h.fid) for h in mias)
    print(f"  {nombre_m}: {len(mias)} huella(s), dedo(s) {dedos}  "
          f"(leído por {transporte_m.upper()}, sin escribirle nada)")

    conexion = abrir(ip, clave)
    try:
        antes = foto(conexion)
        ok, detalle = lectura_confiable(antes)
        print(f"  Lectura previa: {detalle}")
        if not ok:
            salir("Lectura no confiable. NO se escribe nada.")

        destino = antes["crudos"].get(numero)
        if destino is None:
            salir(f"El equipo no tiene ningún usuario {numero}. "
                  f"Corré primero el alta.")
        actual = antes["usuarios"][numero]
        if actual["nombre"].upper() != "PRUEBA":
            salir(f"El usuario {numero} de este equipo se llama "
                  f"«{actual['nombre']}», no PRUEBA.\n"
                  f"  Este script solo le escribe al usuario de prueba: si le "
                  f"escribiera a\n  una persona real, le pisaría la huella.")

        print(f"\n  Se le va a escribir al usuario {numero} («{actual['nombre']}», "
              f"índice {destino.uid}):")
        print(f"     {len(mias)} huella(s) copiada(s) del {numero_maestro}")
        print(f"     hoy tiene {actual['huellas']}")
        print(f"\n  Después vas a poder apoyar el dedo en esa puerta.")

        if "--si" not in sys.argv:
            if input("\n  ¿Escribir? (s/n) ").strip().lower() != "s":
                salir("Cancelado. No se escribió nada.")

        conexion.save_user_template(destino, mias)
        print("\n  Escrito. Volviendo a leer para verificar…")

        despues = foto(conexion)
        ok2, detalle2 = lectura_confiable(despues)
        quedo = despues["usuarios"].get(numero)
        problemas = comparar(antes, despues, numero)

        bien = quedo and quedo["huellas"] == len(mias)
        print(f"\n  {'OK   ' if bien else 'FALLA'} el usuario {numero} "
              f"quedó con {quedo['huellas'] if quedo else 0} huella(s), "
              f"se escribieron {len(mias)}")
        if quedo and (quedo["grupo"] != actual["grupo"]
                      or quedo["nombre"] != actual["nombre"]):
            # Escribir una huella reenvía el registro del usuario entero, así
            # que el grupo y el nombre pueden cambiar sin que nadie lo pida.
            print(f"        OJO: le cambió algo del registro. "
                  f"grupo {actual['grupo']} -> {quedo['grupo']}, "
                  f"nombre «{actual['nombre']}» -> «{quedo['nombre']}»")
        print(f"  {'OK   ' if not problemas else 'FALLA'} los otros "
              f"{len(antes['usuarios']) - 1} usuarios "
              f"{'quedaron intactos' if not problemas else 'NO quedaron intactos'}")
        for p in problemas:
            print(f"        {p}")
        if not ok2:
            print(f"  OJO   la lectura posterior no es confiable: {detalle2}")

        if bien and not problemas and ok2:
            print(f"\n  La huella se escribió y nadie más se movió.")
            print(f"  Ahora andá a esa puerta y apoyá el dedo.")
            print(f"     Si abre: las huellas son portables y el camino sirve.")
            print(f"     Si no abre: el usuario existe con huella, así que el")
            print(f"     problema es el grupo {actual['grupo']} o la puerta misma,")
            print(f"     no la copia. Probá con --grupo distinto en el alta.")
        else:
            print(f"\n  Algo no salió como se esperaba. Contámelo antes de seguir.")
    finally:
        try:
            conexion.disconnect()
        except Exception:
            pass


def main():
    modo = sys.argv[1] if len(sys.argv) > 1 else ""
    if modo == "alta" and len(sys.argv) >= 4:
        modo_alta(sys.argv[2], str(sys.argv[3]).strip())
    elif modo == "huella" and len(sys.argv) >= 5:
        modo_huella(sys.argv[2], str(sys.argv[3]).strip(), str(sys.argv[4]).strip())
    else:
        print(__doc__)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
