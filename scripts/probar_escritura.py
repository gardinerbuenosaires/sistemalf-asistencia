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

Uso:  python scripts/probar_escritura.py alta    IP NUMERO [--grupo N]
      python scripts/probar_escritura.py huella  IP NUMERO NUMERO_EN_EL_MAESTRO
      python scripts/probar_escritura.py borrado IP NUMERO [--nombre X]
      python scripts/probar_escritura.py restaurar IP NUMERO
      python scripts/probar_escritura.py limpiar IP
      python scripts/probar_escritura.py desconocidos [IP] [--incluir-fichaje]

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

      borrado     borra el usuario de prueba y verifica que no se haya
                  llevado a nadie por delante. Es la única operación
                  irreversible en el equipo, y la que más importa verificar:
                  los lectores viejos reordenan índices internos al borrar.

      restaurar   vuelve a poner a esa persona desde el respaldo que dejó el
                  borrado. Es la vuelta atrás, y de paso un anticipo de lo que
                  va a hacer el trabajo real al cargar a alguien en una puerta.

      --nombre X  al borrar a alguien que no sea PRUEBA, hay que escribir el
                  nombre que el equipo tiene para ese número. Si no coincide,
                  el número está equivocado y no se borra nada. Un "¿estás
                  seguro?" no sirve de guardia: a eso se le dice que sí.

      limpiar     borra las pasadas guardadas en una puerta, para empezar de
                  cero con el reloj ya en hora. No toca usuarios ni huellas, y
                  se niega contra el equipo de asistencia: sus registros son los
                  fichajes de la planilla.

      desconocidos  borra de TODAS las puertas a los que no existen en el
                  sistema. La lista sale de la unión de los equipos y no del
                  maestro: un egresado al que le dieron la baja desapareció del
                  maestro y quedó en las puertas, que es el caso más común.
                  Con una IP, solo ese equipo. Guarda el registro completo de
                  cada uno antes de tocarlo: son los únicos cuya huella puede no
                  estar en ningún otro lado.
      --incluir-fichaje  también limpia el equipo de asistencia. Aparte porque
                  sacar a alguien de ahí le quita la posibilidad de fichar.

      --solo-ver  lista quiénes se borrarían y sale sin tocar nada. La lista
                  queda en un archivo para poder mirarla con calma.

      --base RUTA  qué base consultar. Por defecto la de producción, que es
                  donde está la lista real de empleados. Sirve cuando producción
                  todavía no tiene las tablas nuevas: ahí va una copia reciente.

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
if "--base" in sys.argv:
    os.environ["DB_PATH"] = sys.argv[sys.argv.index("--base") + 1]
elif not os.getenv("DB_PATH") and os.path.exists(BASE_PRODUCCION):
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
    crudas = {}
    for h in conexion.get_templates():
        if getattr(h, "valid", 1):
            huellas[h.uid] += 1
            crudas.setdefault(h.uid, []).append(h)
    return {
        "declarados": declarados,
        # Las huellas tal como vinieron. Hacen falta para que un borrado pueda
        # guardarse su propio respaldo: sin los templates, deshacerlo depende de
        # que la persona siga enrolada en el maestro, y el caso que más importa
        # —los que no están en la base— es justamente el que no lo está.
        "huellas_crudas": crudas,
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


# Los cuatro niveles que maneja el equipo. Los dos del medio son los que sirven
# para que alguien pueda administrar el lector desde el lector: dar de alta gente
# y tomarle la huella parado ahí.
NIVELES = {0: "usuario común", 2: "enrolador", 6: "administrador", 14: "super admin"}


def escribir_usuario(conexion, uid, nombre, privilegio, grupo, numero,
                     tarjeta=0, franja=0):
    """
    Crea o reescribe un usuario SIN degradarle el nivel.

    Existe porque `set_user` de pyzk hace esto antes de empaquetar:

        if privilege not in [USER_DEFAULT, USER_ADMIN]:
            privilege = USER_DEFAULT

    O sea que a un enrolador (2) o a un administrador (6) los deja como usuario
    común, sin avisar. Son justamente los niveles que se usan para que alguien
    pueda administrar el lector, así que perderlos no es un detalle: esa persona
    deja de poder enrolar a nadie, y nadie se entera hasta que lo necesita.

    Acá se arma el mismo paquete que arma pyzk y se manda con el mismo comando;
    lo único que no se hace es recortar el nivel. Y de paso la franja horaria
    viaja como parámetro en vez de ir clavada en cero.

    Usa `__send_command`, que es interno de pyzk. Es el precio de no poder pasar
    por `set_user`, y queda acotado a esta función.
    """
    from struct import pack
    from zk import const

    if conexion.user_packet_size != 28:
        salir("Este equipo usa el formato de 72 bytes. Esta función es para las "
              "puertas, que usan el de 28.")
    codificacion = getattr(conexion, "encoding", "latin-1")
    paquete = pack(
        "HB5s8sIxBHI", int(uid), int(privilegio), b"",
        (nombre or "").encode(codificacion, errors="ignore"),
        int(tarjeta), int(grupo or 0), int(franja), int(numero))
    enviar = getattr(conexion, "_ZK__send_command")
    respuesta = enviar(const.CMD_USER_WRQ, paquete, 1024)
    if not respuesta.get("status"):
        raise RuntimeError("El equipo rechazó la escritura del usuario")
    conexion.refresh_data()


def carpeta_respaldos():
    """
    Al lado de la base, como los backups del relevamiento. Tiene huellas
    adentro: no sale de esta PC.
    """
    from pathlib import Path
    base = os.environ.get("DB_PATH")
    raiz = Path(base).resolve().parent if base else Path(RAIZ)
    destino = raiz / "borrados"
    destino.mkdir(parents=True, exist_ok=True)
    return destino


def guardar_respaldo(ip, numero, datos, fingers):
    """
    Guarda todo lo necesario para volver a poner a esta persona tal como estaba:
    sus campos y sus huellas completas.

    Guardar las huellas y no solo el número es la diferencia entre poder
    deshacer y depender de que la persona siga enrolada en el maestro. Y el caso
    que más importa —los que están en una puerta y no existen en la base— es
    justamente el que no está en el maestro.

    Se vuelve a leer del disco después de escribirlo: un respaldo que no se
    comprobó no es un respaldo.
    """
    import json

    ruta = carpeta_respaldos() / f"{ip.replace('.', '-')}-{numero}.json"
    contenido = {
        "ip": ip, "numero": numero,
        "uid": datos["uid"], "nombre": datos["nombre"],
        "grupo": datos["grupo"], "privilegio": datos["privilegio"],
        "huellas": [h.json_pack() for h in fingers],
    }
    ruta.write_text(json.dumps(contenido, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    releido = json.loads(ruta.read_text(encoding="utf-8"))
    if len(releido["huellas"]) != len(fingers):
        salir(f"El respaldo quedó incompleto en {ruta}. No se borra nada.")
    for a, b in zip(contenido["huellas"], releido["huellas"]):
        if a["template"] != b["template"]:
            salir(f"El respaldo no coincide con lo leído. No se borra nada.")
    return ruta


def modo_restaurar(ip, numero):
    """
    Vuelve a poner en una puerta a alguien que se borró, desde su respaldo.

    Es la vuelta atrás del borrado, y también un anticipo de lo que va a hacer
    el trabajo real cuando tenga que cargar a alguien en una puerta: crear el
    usuario y escribirle sus huellas.
    """
    import json
    from zk.finger import Finger

    maestro_ip, clave, puerta = datos_del_sistema(ip, numero, False)
    ruta = carpeta_respaldos() / f"{ip.replace('.', '-')}-{numero}.json"
    if not ruta.exists():
        salir(f"No encuentro el respaldo:\n     {ruta}")
    guardado = json.loads(ruta.read_text(encoding="utf-8"))

    cabecera("restaurar un usuario", ip, numero, puerta, maestro_ip)
    print(f"\n  Respaldo: {ruta}")
    nivel = int(guardado["privilegio"])
    print(f"     nombre «{guardado['nombre']}», grupo {guardado['grupo']}, "
          f"{len(guardado['huellas'])} huella(s)")
    print(f"     nivel {nivel} — {NIVELES.get(nivel, 'desconocido')}")
    if nivel not in (0, 14):
        print(f"     se restaura tal cual: set_user de pyzk lo habría dejado "
              f"en usuario común")

    conexion = abrir(ip, clave)
    try:
        antes = foto(conexion)
        ok, detalle = lectura_confiable(antes)
        print(f"  Lectura previa: {detalle}")
        if not ok:
            salir("Lectura no confiable. NO se escribe nada.")
        if numero in antes["usuarios"]:
            salir(f"El equipo ya tiene un usuario {numero} "
                  f"(«{antes['usuarios'][numero]['nombre']}»). No hay que restaurar nada.")

        usados = {u["uid"] for u in antes["usuarios"].values()}
        # El índice original si sigue libre —así queda igual que antes— y si no,
        # uno nuevo: el número de legajo es lo que identifica a la persona, el
        # índice es solo su posición adentro del equipo.
        uid = guardado["uid"] if guardado["uid"] not in usados else max(usados) + 1
        if uid != guardado["uid"]:
            print(f"  El índice original {guardado['uid']} está ocupado; "
                  f"se usa el {uid}.")

        if "--si" not in sys.argv:
            if input("\n  ¿Restaurar? (s/n) ").strip().lower() != "s":
                salir("Cancelado.")

        escribir_usuario(conexion, uid=uid, nombre=guardado["nombre"],
                         privilegio=int(guardado["privilegio"]),
                         grupo=guardado["grupo"], numero=numero)
        fingers = [Finger.json_unpack(h) for h in guardado["huellas"]]
        if fingers:
            recien = next((u for u in conexion.get_users()
                           if str(u.user_id).strip() == numero), None)
            if recien is None:
                salir("Se creó el usuario pero no aparece al releer. Parate acá.")
            conexion.save_user_template(recien, fingers)
        print("\n  Restaurado. Volviendo a leer para verificar…")

        despues = foto(conexion)
        vuelto = despues["usuarios"].get(numero)
        problemas = comparar(antes, despues, numero)
        bien = vuelto and vuelto["huellas"] == len(guardado["huellas"])
        print(f"\n  {'OK   ' if bien else 'FALLA'} el {numero} volvió con "
              f"{vuelto['huellas'] if vuelto else 0} de "
              f"{len(guardado['huellas'])} huella(s)")
        print(f"  {'OK   ' if not problemas else 'FALLA'} los demás "
              f"{'quedaron intactos' if not problemas else 'NO quedaron intactos'}")
        for p in problemas:
            print(f"        {p}")
        if bien and not problemas:
            print(f"\n  Quedó como estaba. El borrado se puede deshacer.")
    finally:
        try:
            conexion.disconnect()
        except Exception:
            pass


def modo_borrado(ip, numero):
    """
    Borra el usuario de prueba y verifica que no se haya llevado a nadie por
    delante. Es la única de las tres operaciones que es irreversible en ese
    equipo, y la que más importa verificar: los lectores viejos reordenan
    índices internos, y un borrado mal hecho se nota recién cuando alguien no
    puede entrar.

    `delete_user` manda el índice interno y nada más (`pack('h', uid)`), así que
    un índice equivocado borra a otra persona. Por eso se usa el de la foto ya
    verificada y no el que resolvería pyzk por su cuenta.
    """
    maestro_ip, clave, puerta = datos_del_sistema(ip, numero, False)
    cabecera("borrar un usuario", ip, numero, puerta, maestro_ip)
    conexion = abrir(ip, clave)

    try:
        antes = foto(conexion)
        ok, detalle = lectura_confiable(antes)
        print(f"  Lectura previa: {detalle}")
        if not ok:
            salir("Lectura no confiable. NO se borra nada.")

        objetivo = antes["usuarios"].get(numero)
        if objetivo is None:
            salir(f"El equipo no tiene ningún usuario {numero}. Nada que borrar.")

        # Borrar al usuario de prueba no necesita ceremonia: lo creamos nosotros.
        # Borrar a una persona real sí, y el guardia útil no es un "¿estás
        # seguro?" —a eso se le dice que sí sin leer— sino tener que escribir el
        # nombre que el equipo tiene. Si no coincide, el número está equivocado.
        if objetivo["nombre"].upper() != "PRUEBA":
            esperado = argumento("--nombre")
            if not esperado:
                salir(f"El usuario {numero} de este equipo se llama "
                      f"«{objetivo['nombre']}», no PRUEBA.\n"
                      f"  Si es a propósito, confirmá el nombre:\n"
                      f"      ... borrado {ip} {numero} --nombre \"{objetivo['nombre']}\"")
            if esperado.strip().upper() != objetivo["nombre"].upper():
                salir(f"Pusiste --nombre «{esperado}» y el equipo dice "
                      f"«{objetivo['nombre']}».\n"
                      f"  No coinciden, así que el número puede estar equivocado. "
                      f"No se borra nada.")

        respaldo = guardar_respaldo(ip, numero, objetivo,
                                    antes["huellas_crudas"].get(objetivo["uid"], []))
        print(f"\n  Respaldo de este usuario guardado en:")
        print(f"     {respaldo}")
        print(f"  Con eso se lo puede volver a poner tal cual:")
        print(f"     scripts\\restaurar_usuario.bat {ip} {numero}")

        nivel = int(objetivo["privilegio"])
        print(f"\n  Se va a borrar:")
        print(f"     número {numero}, nombre «{objetivo['nombre']}», "
              f"índice interno {objetivo['uid']}, {objetivo['huellas']} huella(s)")
        print(f"     nivel {nivel} — {NIVELES.get(nivel, 'desconocido')}"
              + ("   OJO: esta persona administra este lector" if nivel else ""))
        print(f"  Quedan {len(antes['usuarios']) - 1} usuarios, que tienen que "
              f"seguir igual.")

        if "--si" not in sys.argv:
            if input("\n  ¿Borrar? (s/n) ").strip().lower() != "s":
                salir("Cancelado. No se borró nada.")

        conexion.delete_user(uid=objetivo["uid"])
        print("\n  Borrado. Volviendo a leer para verificar…")

        despues = foto(conexion)
        ok2, detalle2 = lectura_confiable(despues)
        sigue = despues["usuarios"].get(numero)
        problemas = comparar(antes, despues, numero)

        print(f"\n  {'OK   ' if not sigue else 'FALLA'} el usuario {numero} "
              f"{'ya no está en el equipo' if not sigue else 'TODAVÍA ESTÁ'}")
        print(f"  {'OK   ' if not problemas else 'FALLA'} los otros "
              f"{len(antes['usuarios']) - 1} usuarios "
              f"{'quedaron intactos, con sus huellas' if not problemas else 'NO quedaron intactos'}")
        for p in problemas:
            print(f"        {p}")
        if not ok2:
            print(f"  OJO   la lectura posterior no es confiable: {detalle2}")

        if not sigue and not problemas and ok2:
            print(f"\n  El borrado funciona y no se lleva a nadie por delante.")
            print(f"  La puerta quedó como estaba antes de todas estas pruebas.")
            print(f"\n  Las tres operaciones estan probadas contra este equipo.")
        else:
            print(f"\n  Algo no salió como se esperaba. Tenés el backup de esta")
            print(f"  puerta de antes de empezar; no toques nada y contámelo.")
    finally:
        try:
            conexion.disconnect()
        except Exception:
            pass


def modo_limpiar(ip):
    """
    Borra las pasadas guardadas en una puerta y empieza de cero.

    Para qué. Un lector que estuvo años con el reloj roto tiene miles de pasadas
    fechadas en el siglo XXII. Esas fechas no se pueden arreglar: el equipo
    guardó lo que creía que era, y no hay forma de saber cuánto estaba corrido en
    cada momento. Con el reloj ya en hora, empezar de nuevo deja un registro que
    sirve, en vez de uno que hay que explicar cada vez que se mira.

    Lo que NO se borra: usuarios ni huellas. `clear_attendance` toca solamente
    el área de registros, y acá se verifica releyendo que las dos cosas hayan
    quedado como estaban.

    Y nunca el equipo de asistencia. Sus registros se convierten en los fichajes
    de la planilla, y los que todavía no se descargaron no están en ningún otro
    lado. De ese se encarga el sistema los días 1 y 15, después de bajarlos.
    """
    import json
    from datetime import datetime

    maestro_ip, clave, puerta = datos_del_sistema(ip, "", False)
    cabecera("borrar las pasadas", ip, "—", puerta, maestro_ip)
    conexion = abrir(ip, clave)

    try:
        reloj = None
        try:
            reloj = conexion.get_time()
        except Exception:
            pass
        antes = foto(conexion)
        ok, detalle = lectura_confiable(antes)
        print(f"  Lectura previa: {detalle}")
        if not ok:
            salir("Lectura no confiable. NO se borra nada.")
        huellas_antes = sum(u["huellas"] or 0 for u in antes["usuarios"].values())

        from zk import const
        conexion.read_sizes()
        cuantas = getattr(conexion, "records", 0)
        print(f"  Reloj del equipo: {reloj}")
        print(f"  Tiene {cuantas} pasadas guardadas")
        print(f"  Y {len(antes['usuarios'])} usuarios con {huellas_antes} huellas,"
              f" que NO se tocan")

        if not cuantas:
            salir("No hay pasadas que borrar.")

        # Respaldo del bloque crudo. Son unos pocos KB y cuesta nada; sin él,
        # «empezar de nuevo» sería irreversible por completo.
        crudo, _ = conexion.read_with_buffer(const.CMD_ATTLOG_RRQ)
        ruta = carpeta_respaldos() / f"pasadas-{ip.replace('.', '-')}-{datetime.now():%Y%m%d-%H%M%S}.json"
        ruta.write_text(json.dumps({
            "ip": ip, "cuando": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "reloj_del_equipo": reloj.strftime("%Y-%m-%d %H:%M:%S") if reloj else None,
            "registros": cuantas, "bytes": crudo.hex(),
        }, indent=1), encoding="utf-8")
        if len(bytes.fromhex(json.loads(ruta.read_text(encoding="utf-8"))["bytes"])) != len(crudo):
            salir(f"El respaldo quedó incompleto en {ruta}. No se borra nada.")
        print(f"\n  Respaldo del bloque crudo ({len(crudo)} bytes):")
        print(f"     {ruta}")

        print(f"\n  Se van a borrar las {cuantas} pasadas de esta puerta.")
        print(f"  No se puede deshacer en el equipo: el respaldo son los bytes,")
        print(f"  no una copia que se pueda volver a cargar.")

        if "--si" not in sys.argv:
            if input("\n  ¿Borrar las pasadas? (s/n) ").strip().lower() != "s":
                salir("Cancelado. No se borró nada.")

        conexion.clear_attendance()
        print("\n  Borradas. Volviendo a leer para verificar…")

        conexion.read_sizes()
        quedan = getattr(conexion, "records", None)
        despues = foto(conexion)
        huellas_despues = sum(u["huellas"] or 0 for u in despues["usuarios"].values())
        problemas = comparar(antes, despues)

        print(f"\n  {'OK   ' if quedan == 0 else 'FALLA'} el equipo quedó con "
              f"{quedan} pasadas")
        print(f"  {'OK   ' if not problemas else 'FALLA'} los {len(antes['usuarios'])} "
              f"usuarios y sus {huellas_antes} huellas "
              f"{'siguen enteros' if not problemas else 'NO siguen enteros'}")
        for p in problemas:
            print(f"        {p}")
        if huellas_despues != huellas_antes:
            print(f"        OJO: las huellas pasaron de {huellas_antes} a {huellas_despues}")

        if quedan == 0 and not problemas and huellas_despues == huellas_antes:
            print(f"\n  Listo. Esta puerta empieza a registrar de cero, con la hora")
            print(f"  en la que está ahora. Lo que anote de acá en más va a servir.")
        else:
            print(f"\n  Algo no salió como se esperaba. Contámelo antes de seguir.")
    finally:
        try:
            conexion.disconnect()
        except Exception:
            pass


def _equipos_a_limpiar(solo_ip=None, incluir_fichaje=False):
    """
    Los equipos que se van a revisar, y el de asistencia aparte.

    El de fichaje no entra salvo que se pida: borrar a alguien de ahí le saca la
    posibilidad de fichar, que es más grave que perder una puerta, y si ese
    número resultara ser de una persona real con el legajo mal borrado, el error
    se descubre el lunes a las siete de la mañana.
    """
    from db.database import db_session

    with db_session() as conn:
        # Sin esa tabla no hay forma de saber cuál equipo es puerta y cuál es el
        # de fichaje, y esa distinción es la que evita borrarle a alguien la
        # posibilidad de fichar. Mejor decirlo que tirar un error de SQL.
        if not conn.execute(
            """SELECT 1 FROM sqlite_master
                WHERE type='table' AND name='dispositivos'""").fetchone():
            otra = os.path.join(RAIZ, "data", "pruebas.db")
            sugerencia = ""
            if (os.path.exists(otra) and os.path.abspath(otra)
                    != os.path.abspath(os.environ.get("DB_PATH", ""))):
                sugerencia = (
                    "  Esta instalación tiene una base propia que sí la tiene. "
                    "Si es una copia reciente de producción, sirve: "
                    f'desconocidos --base "{otra}"')
            if sugerencia:
                print(sugerencia)
            salir("Esta base todavía no tiene la tabla de equipos. Se crea al "
                  "arrancar el sistema con la versión nueva; hasta entonces no "
                  "hay forma de saber qué equipo es qué.")
        filas = [dict(r) for r in conn.execute(
            """SELECT id, nombre, ip, puerto, password, timeout, protocolo,
                      es_acceso, cuenta_asistencia
                 FROM dispositivos
                WHERE activo=1 AND protocolo='pull' AND ip IS NOT NULL
             ORDER BY orden, id""")]
        del_sistema = {str(r[0]).strip() for r in conn.execute(
            "SELECT user_id FROM empleados WHERE user_id IS NOT NULL")}

    puertas = [d for d in filas if d["es_acceso"]]
    fichaje = [d for d in filas if d["cuenta_asistencia"] and not d["es_acceso"]]
    if solo_ip:
        puertas = [d for d in puertas if d["ip"] == solo_ip]
        fichaje = [d for d in fichaje if d["ip"] == solo_ip]
    return (puertas + (fichaje if incluir_fichaje or solo_ip else []),
            fichaje, del_sistema)


def modo_desconocidos(solo_ip=None):
    """
    Borra de los equipos a los que no existen en el sistema.

    El criterio es del usuario y es el correcto: la base decide quién existe.
    Pero la lista no se saca del equipo de asistencia, se saca de la UNIÓN de
    todos. Un egresado al que le dieron la baja en Enterprise desapareció del
    maestro y quedó en las puertas: es el caso más común, y es justamente el que
    no aparecería mirando solo el maestro.

    De cada uno se guarda el registro completo con sus huellas, por equipo,
    antes de borrarlo. Son los únicos cuya huella puede no estar en ningún otro
    lado: no tienen legajo, y si tampoco están en el maestro ese respaldo es la
    única copia que existe.

    Un equipo que no contesta no frena a los demás, pero se informa: la limpieza
    queda incompleta y hay que volver cuando ese equipo esté.
    """
    incluir_fichaje = "--incluir-fichaje" in sys.argv
    equipos, fichaje, del_sistema = _equipos_a_limpiar(solo_ip, incluir_fichaje)
    if not equipos:
        salir("No hay equipos para revisar." if not solo_ip
              else f"{solo_ip} no está cargado como equipo activo.")

    print(f"\n  Borrar a los que no existen en el sistema")
    print(f"  -----------------------------------------")
    ruta_base = os.environ.get("DB_PATH", "")
    print(f"  Base    : {ruta_base or '(la que resuelva config)'}")
    linea = f"            conoce {len(del_sistema)} números"
    horas = None
    if ruta_base and os.path.exists(ruta_base):
        from datetime import datetime
        horas = (datetime.now()
                 - datetime.fromtimestamp(os.path.getmtime(ruta_base))).total_seconds() / 3600
        linea += (f", y se escribió hace {int(horas)} h" if horas < 48
                  else f", y se escribió hace {int(horas / 24)} días")
    print(linea)
    # Una base vieja no es un detalle de prolijidad: alguien que entró después
    # de la copia figura como desconocido y se iría con el resto.
    if horas is not None and horas > 24:
        print()
        print("  OJO: esta base tiene más de un día. Si es una copia, el que haya")
        print(f"  entrado desde entonces figura como desconocido y se borraría con")
        print(f"  los demás. Conviene copiarla de nuevo antes de seguir.")
    print(f"  Equipos : " + ", ".join(f"{d['nombre']} ({d['ip']})" for d in equipos))
    if fichaje and not incluir_fichaje and not solo_ip:
        print(f"  El equipo de fichaje NO se toca. Para incluirlo: --incluir-fichaje")

    # Primero se lee todo. Decidir con una foto parcial es lo que lleva a borrar
    # de una puerta a alguien que en otra era la única copia de su huella.
    lecturas, sin_responder = {}, []
    for d in equipos:
        print(f"\n  Leyendo {d['nombre']} ({d['ip']})…", end=" ", flush=True)
        conexion = None
        try:
            conexion, _t = conectar(d["ip"], d.get("password") or 0)
            f = foto(conexion)
            ok, detalle = lectura_confiable(f)
            if not ok:
                print(f"lectura no confiable: {detalle}")
                sin_responder.append((d, detalle))
            else:
                lecturas[d["id"]] = f
                print(f"{len(f['usuarios'])} usuarios")
        except Exception as exc:
            print(f"no contestó ({type(exc).__name__})")
            sin_responder.append((d, str(exc)))
        finally:
            if conexion:
                try:
                    conexion.disconnect()
                except Exception:
                    pass

    if not lecturas:
        salir("Ningún equipo contestó. No se borra nada.")

    # La unión de todos, menos la base.
    fantasmas = {}
    for d in equipos:
        f = lecturas.get(d["id"])
        if not f:
            continue
        for numero, datos in f["usuarios"].items():
            if numero in del_sistema:
                continue
            fantasmas.setdefault(numero, []).append((d, datos))

    if not fantasmas:
        salir("No hay ningún número que el sistema no conozca. Nada que borrar.")

    print(f"\n  {len(fantasmas)} número(s) que no existen en el sistema:\n")
    total_borrados = 0
    for numero in sorted(fantasmas, key=lambda x: (len(x), x)):
        donde = fantasmas[numero]
        nombres = {datos["nombre"] for _d, datos in donde if datos["nombre"]}
        huellas = sum(datos["huellas"] or 0 for _d, datos in donde)
        total_borrados += len(donde)
        print(f"     {numero:>8}  «{' / '.join(sorted(nombres)) or 'sin nombre'}»"
              f"  {huellas} huella(s) en total")
        print(f"               en: " + ", ".join(d["nombre"] for d, _ in donde))

    # La lista queda en un archivo. Revisar veintitantas personas con el cursor
    # esperando una respuesta no es revisar; y despues de borrar, ese archivo es
    # el unico registro de a quien se saco y de donde.
    from datetime import datetime
    ruta_lista = carpeta_respaldos() / f"desconocidos-{datetime.now():%Y%m%d-%H%M%S}.txt"
    with open(ruta_lista, "w", encoding="utf-8") as arch:
        arch.write(f"Desconocidos al {datetime.now():%d-%m-%Y %H:%M}" + chr(10))
        arch.write(f"Base: {ruta_base}  ({len(del_sistema)} numeros conocidos)" + chr(10) * 2)
        for numero in sorted(fantasmas, key=lambda x: (len(x), x)):
            donde = fantasmas[numero]
            nombres = {d["nombre"] for _e, d in donde if d["nombre"]}
            arch.write(f"{numero:>8}  {' / '.join(sorted(nombres)) or 'sin nombre'}" + chr(10))
            for equipo, datos in donde:
                arch.write(f"          {equipo['nombre']} ({equipo['ip']})"
                           f"  {datos['huellas'] or 0} huella(s)"
                           f"  nivel {datos['privilegio']}" + chr(10))
        for equipo, por_que in sin_responder:
            arch.write(chr(10) + f"SIN LEER: {equipo['nombre']} ({equipo['ip']}): {por_que}"
                       + chr(10))

    print(f"\n  Son {total_borrados} borrado(s) en {len(lecturas)} equipo(s).")
    print(f"  La lista completa quedó en:")
    print(f"     {ruta_lista}")
    if sin_responder:
        print(f"\n  OJO: {len(sin_responder)} equipo(s) no se pudieron leer, así que la")
        print(f"  limpieza va a quedar incompleta. Hay que volver cuando estén:")
        for d, por_que in sin_responder:
            print(f"     {d['nombre']} ({d['ip']}): {por_que}")

    if "--solo-ver" in sys.argv:
        print()
        print("  Solo se miró: no se borró nada. Para aplicarlo, lo mismo sin --solo-ver.")
        return

    if "--si" not in sys.argv:
        escrito = input(f"\n  Escribí cuántos borrados vas a hacer ({total_borrados})"
                        f" o Enter para salir: ")
        if escrito.strip() != str(total_borrados):
            salir("Cancelado. No se borró nada.")

    print(f"\n  Respaldos en {carpeta_respaldos()}")
    for d in equipos:
        f = lecturas.get(d["id"])
        if not f:
            continue
        # Los fantasmas que viven en ESTE equipo, con sus datos de acá: el uid
        # y las huellas son propios de cada lector, no se pueden reusar.
        suyos = {}
        for numero, lista in fantasmas.items():
            for equipo, datos in lista:
                if equipo["id"] == d["id"]:
                    suyos[numero] = datos
        if not suyos:
            continue
        print(f"\n  {d['nombre']} ({d['ip']})")
        conexion = None
        try:
            conexion, _t = conectar(d["ip"], d.get("password") or 0)
            for numero in sorted(suyos, key=lambda x: (len(x), x)):
                datos = suyos[numero]
                guardar_respaldo(d["ip"], numero, datos,
                                 f["huellas_crudas"].get(datos["uid"], []))
                try:
                    conexion.delete_user(uid=datos["uid"])
                    print(f"     borrado {numero}  «{datos['nombre']}»")
                except Exception as exc:
                    print(f"     FALLO   {numero}: {exc}")

            despues = foto(conexion)
            ok2, detalle2 = lectura_confiable(despues)
            quedaron = set(despues["usuarios"]) & set(suyos)
            problemas = [p for p in comparar(f, despues)
                         if not any(n in p for n in suyos)]
            print(f"     {'OK   ' if not quedaron else 'FALLA'} quedan {len(quedaron)}"
                  f" de los que había que sacar")
            print(f"     {'OK   ' if not problemas else 'FALLA'} los otros "
                  f"{len(f['usuarios']) - len(suyos)} siguen enteros con sus huellas")
            for p in problemas:
                print(f"           {p}")
            if not ok2:
                print(f"     OJO   la lectura posterior no es confiable: {detalle2}")
        except Exception as exc:
            print(f"     No se pudo: {exc}")
        finally:
            if conexion:
                try:
                    conexion.disconnect()
                except Exception:
                    pass

    print(f"\n  Listo." + (" Falta volver por los equipos que no contestaron."
                           if sin_responder else ""))


def main():
    modo = sys.argv[1] if len(sys.argv) > 1 else ""
    if modo == "alta" and len(sys.argv) >= 4:
        modo_alta(sys.argv[2], str(sys.argv[3]).strip())
    elif modo == "huella" and len(sys.argv) >= 5:
        modo_huella(sys.argv[2], str(sys.argv[3]).strip(), str(sys.argv[4]).strip())
    elif modo == "borrado" and len(sys.argv) >= 4:
        modo_borrado(sys.argv[2], str(sys.argv[3]).strip())
    elif modo == "restaurar" and len(sys.argv) >= 4:
        modo_restaurar(sys.argv[2], str(sys.argv[3]).strip())
    elif modo == "limpiar" and len(sys.argv) >= 3:
        modo_limpiar(sys.argv[2])
    elif modo == "desconocidos":
        # Sin IP: todas las puertas. Con IP: solo esa.
        ip = sys.argv[2] if len(sys.argv) >= 3 and not sys.argv[2].startswith("--") else None
        modo_desconocidos(ip)
    else:
        print(__doc__)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
