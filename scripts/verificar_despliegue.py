"""Qué hay que mirar antes y después de desplegar el módulo de accesos. SOLO LECTURA.

Se corre dos veces: **antes** de actualizar, para saber si algo va a salir mal,
y **después**, para confirmar que no salió.

Abre la base de producción en modo solo lectura. No escribe nada, en ningún
lado, ni en la base ni en los equipos.

Lo que revisa, en orden de lo que más duele si está mal:

  1. La IP del lector de asistencia. Es el único punto donde este módulo puede
     romper algo que hoy funciona: la bajada de fichajes pasa a leerla de la
     tabla `dispositivos`, y si esa tabla queda con la IP equivocada el local
     deja de registrar asistencia sin dar ningún error.

  2. Que el lector de asistencia no esté marcado como puerta. Si lo estuviera,
     sus pasadas entrarían a `fichajes` y darían vuelta entradas y salidas de
     todo el día.

  3. Que los fichajes sigan llegando. Es la prueba de que lo anterior está bien.

  4. Quién ve el módulo nuevo. Tiene que ser solo el rol `sistema`.

  5. Si las huellas ya están respaldadas.

Uso:
    scripts\\verificar_despliegue.bat
    scripts\\verificar_despliegue.bat --base C:\\otra\\ruta\\fichajes.db
"""
import os
import sqlite3
import sys
from datetime import datetime, timedelta

sys.stdout.reconfigure(errors="replace")
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(RAIZ)

BASE = r"C:\ProgramData\SistemAlf\fichajes.db"
if "--base" in sys.argv:
    BASE = sys.argv[sys.argv.index("--base") + 1]

BIEN, MAL, OJO = "  [OK]   ", "  [MAL]  ", "  [OJO]  "
problemas = []


def decir(estado, texto, detalle=""):
    print(f"{estado}{texto}")
    if detalle:
        for linea in detalle.splitlines():
            print(f"           {linea}")
    if estado == MAL:
        problemas.append(texto)


def hay_tabla(conn, nombre):
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (nombre,)).fetchone())


def main():
    if not os.path.exists(BASE):
        raise SystemExit(f"\n  No está la base:\n    {BASE}\n")

    conn = sqlite3.connect(f"file:{BASE}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    print(f"\n  Base: {BASE}")
    print(f"  Solo lectura. No se escribe nada.\n")

    # ── 1. La IP del lector de asistencia ────────────────────────────────────
    print("  1. EL LECTOR DE ASISTENCIA")
    cfg = {r["clave"]: (r["valor"] or "").strip() for r in conn.execute(
        "SELECT clave, valor FROM configuracion WHERE clave LIKE 'device_%'")}
    ip_cfg = cfg.get("device_ip", "")

    if not hay_tabla(conn, "dispositivos"):
        # Todavía no se desplegó: se mira si la migración va a poder sembrar.
        if ip_cfg:
            decir(BIEN, f"Todavía no se desplegó, y hay IP configurada: {ip_cfg}",
                  "Al actualizar, el equipo se va a cargar solo con esa IP.")
        else:
            decir(OJO, "Todavía no se desplegó, y NO hay device_ip en configuracion",
                  "No se va a cargar ningún equipo, y la asistencia va a seguir\n"
                  "funcionando por el camino de siempre. Pero vas a tener que\n"
                  "cargar el lector a mano en Configuracion > Dispositivos para\n"
                  "que el módulo de accesos sirva.")
    else:
        equipos = [dict(r) for r in conn.execute(
            """SELECT id, nombre, ip, puerto, activo, cuenta_asistencia, es_acceso,
                      protocolo FROM dispositivos ORDER BY orden, id""")]
        asistencia = [e for e in equipos if e["cuenta_asistencia"] and e["activo"]]

        if not asistencia:
            decir(MAL, "NINGÚN equipo está marcado como de asistencia",
                  "La bajada de fichajes no sabe a qué lector preguntarle.\n"
                  "Configuracion > Dispositivos: marca «Cuenta asistencia».")
        elif len(asistencia) > 1:
            decir(OJO, f"Hay {len(asistencia)} equipos marcados como de asistencia",
                  "Se usa el primero por orden: " + asistencia[0]["nombre"])
        else:
            eq = asistencia[0]
            decir(BIEN, f"Equipo de asistencia: {eq['nombre']} — {eq['ip']}:{eq['puerto']}")
            if ip_cfg and eq["ip"] != ip_cfg:
                decir(MAL, f"La IP NO coincide con la que había configurada ({ip_cfg})",
                      "Si la de la tabla está mal, el local deja de registrar\n"
                      "asistencia sin dar ningún error. Corregila antes de seguir.")
            elif not ip_cfg:
                decir(OJO, "No hay device_ip viejo contra qué comparar",
                      "Verificá a ojo que esa IP sea la del lector de este local.")

            # ── 2. Que no esté marcado como puerta ───────────────────────────
            if eq["es_acceso"]:
                decir(MAL, "El equipo de asistencia TAMBIÉN figura como puerta",
                      "Sus pasadas entrarían a fichajes y darían vuelta entradas\n"
                      "y salidas de todo el día. Destildá «Abre una puerta».")
            else:
                decir(BIEN, "Y no está marcado como puerta, que es lo que corresponde")

        puertas = [e for e in equipos if e["es_acceso"] and e["activo"]]
        print(f"           {len(puertas)} puerta(s) cargada(s), "
              f"{len(equipos)} equipo(s) en total")
        caidos = [e["nombre"] for e in equipos if not e["activo"]]
        if caidos:
            print(f"           desactivados: {', '.join(caidos)}")

    # ── 3. Que los fichajes sigan llegando ───────────────────────────────────
    print("\n  2. LA ASISTENCIA SIGUE FUNCIONANDO")
    try:
        ultimo = conn.execute(
            "SELECT MAX(fecha_hora) FROM fichajes").fetchone()[0]
    except sqlite3.OperationalError:
        ultimo = None
    if not ultimo:
        decir(OJO, "No hay ningún fichaje en la base")
    else:
        try:
            cuando = datetime.fromisoformat(str(ultimo)[:19])
            horas = (datetime.now() - cuando).total_seconds() / 3600
        except ValueError:
            cuando, horas = None, None
        legible = cuando.strftime("%d-%m-%Y %H:%M") if cuando else str(ultimo)
        if horas is None:
            decir(OJO, f"Último fichaje: {legible}")
        elif horas <= 24:
            decir(BIEN, f"Último fichaje: {legible} (hace {horas:.1f} h)")
        else:
            decir(MAL, f"El último fichaje es de hace {horas/24:.1f} día(s): {legible}",
                  "Si acabás de desplegar, revisá la IP del punto 1.")

    # ── 4. Quién ve el módulo nuevo ──────────────────────────────────────────
    print("\n  3. QUIÉN VE EL MÓDULO NUEVO")
    if not hay_tabla(conn, "permisos"):
        decir(OJO, "No hay tabla de permisos todavía")
    else:
        filas = conn.execute(
            """SELECT r.nombre, COUNT(*) AS n
                 FROM permisos p JOIN roles r ON r.id = p.rol_id
                WHERE p.modulo IN ('accesos','dispositivos')
                GROUP BY r.nombre ORDER BY r.nombre""").fetchall()
        if not filas:
            decir(OJO, "Ningún rol tiene permisos de accesos todavía",
                  "Se asignan solos al reiniciar, al rol «sistema».")
        for f in filas:
            otros = f["nombre"].lower() != "sistema"
            decir(OJO if otros else BIEN,
                  f"{f['nombre']}: {f['n']} permiso(s) de accesos/dispositivos",
                  "Este rol NO debería tenerlos si querés que el módulo sea\n"
                  "invisible para todos menos vos." if otros else "")

    # ── 5. Las huellas ───────────────────────────────────────────────────────
    print("\n  4. EL RESPALDO DE HUELLAS")
    if not hay_tabla(conn, "huellas"):
        decir(OJO, "Todavía no existe la tabla (se crea al desplegar)")
    else:
        f = conn.execute(
            """SELECT COUNT(DISTINCT empleado_id) AS personas, COUNT(*) AS n,
                      MAX(leida_en) AS ultima FROM huellas""").fetchone()
        if not f["n"]:
            decir(OJO, "No hay ninguna huella respaldada",
                  "Configuracion > Dispositivos > Huellas > Respaldar ahora.\n"
                  "Conviene hacerlo antes de tocar cualquier equipo.")
        else:
            faltan = conn.execute(
                """SELECT COUNT(*) FROM empleados e
                    WHERE (e.activo = 1 OR e.fecha_egreso > date('now','localtime'))
                      AND e.user_id IS NOT NULL AND TRIM(e.user_id) <> ''
                      AND NOT EXISTS (SELECT 1 FROM huellas h
                                       WHERE h.empleado_id = e.id)""").fetchone()[0]
            decir(BIEN if not faltan else OJO,
                  f"{f['n']} huella(s) de {f['personas']} persona(s), "
                  f"última copia {f['ultima']}",
                  f"{faltan} persona(s) que trabajan hoy no están respaldadas."
                  if faltan else "")

    conn.close()
    print("\n" + "  " + "-" * 60)
    if problemas:
        print(f"  HAY {len(problemas)} COSA(S) PARA CORREGIR:")
        for p in problemas:
            print(f"    · {p}")
        print()
        raise SystemExit(1)
    print("  Sin problemas que frenen el funcionamiento normal.\n")


if __name__ == "__main__":
    main()
