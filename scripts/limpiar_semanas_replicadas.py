"""
Borra las semanas FUTURAS que quedaron pre-llenadas por la replicación automática.

Hasta que se quitó, confirmar una semana creaba borradores para las 4 siguientes
copiando las asignaciones. Eso dejaba cocina y peones planificados un mes adelante
sin que nadie lo decidiera, y arrastraba errores (bajas, vacaciones cargadas
después) semana tras semana. El mecanismo previsto es el botón "copiar semana
anterior", que trabaja sobre una semana vacía y sí valida al copiar.

Se reconocen porque la replicación escribía todas sus filas en una sola
transacción: comparten un único `creado_en`. Una semana armada a mano tiene
decenas de timestamps distintos, uno por clic.

Criterios (los tres a la vez):
  - estado = 'borrador'      → nunca toca una semana confirmada
  - semana_inicio >= corte   → deja afuera la semana en curso y la que arranca el
                               lunes, que el encargado ya puede estar usando
  - un solo `creado_en` distinto entre sus filas, y más de MIN_FILAS

Borra la fila de `*_semana`; los detalles, francos, vacaciones y licencias caen
por ON DELETE CASCADE (foreign_keys está en ON). Se borran igual de forma
explícita por si alguna vez corre con las FK apagadas.

Uso:  python scripts/limpiar_semanas_replicadas.py [--desde AAAA-MM-DD] [--aplicar]
      (sin --aplicar hace una simulación y no escribe nada)

El corte por defecto es el lunes de la semana subsiguiente. Con --desde se fija
otro, por ejemplo:  --desde 2026-09-07
"""
import sys, os
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.database import db_session

APLICAR = "--aplicar" in sys.argv
MIN_FILAS = 20   # por debajo de esto no se asume replicación


def _corte() -> str:
    """Lunes desde el cual se borra. Por defecto el de la semana subsiguiente, para
    no tocar la semana en curso ni la que arranca el lunes.

    La fecha se valida siempre: el corte se compara como texto contra
    `semana_inicio`, asi que un valor invalido ordenaria antes que cualquier fecha
    y alcanzaria a TODAS las semanas. En un script destructivo eso no puede pasar.
    """
    if "--desde" not in sys.argv:
        hoy_d = date.today()
        lunes_esta = hoy_d - timedelta(days=hoy_d.weekday())
        return str(lunes_esta + timedelta(days=14))

    i = sys.argv.index("--desde") + 1
    if i >= len(sys.argv):
        sys.exit("ERROR: --desde necesita una fecha. Ej: --desde 2026-09-07")
    valor = sys.argv[i]
    try:
        d = date.fromisoformat(valor)
    except ValueError:
        sys.exit(f"ERROR: '{valor}' no es una fecha AAAA-MM-DD. Ej: --desde 2026-09-07")
    if d.weekday() != 0:
        sys.exit(f"ERROR: {valor} es {['lunes','martes','miercoles','jueves','viernes','sabado','domingo'][d.weekday()]}; "
                 "el corte tiene que ser un lunes (las semanas arrancan el lunes).")
    if d <= date.today():
        sys.exit(f"ERROR: {valor} no es futuro. El script no toca semanas en curso ni pasadas.")
    return valor


MODULOS = [
    ("COCINA", "distribucion_semana", "distribucion_detalle",
     ["distribucion_detalle", "distribucion_franco", "distribucion_vacacion", "distribucion_licencia"]),
    ("PEONES", "peones_semana", "peones_detalle",
     ["peones_detalle", "peones_franco", "peones_vacacion"]),
]

corte = _corte()

with db_session() as conn:
    tot_semanas = tot_filas = 0
    calendario = set()

    for nombre, tabla_sem, tabla_det, hijas in MODULOS:
        print(f"\n=== {nombre} ===")
        candidatas = conn.execute(
            f"""SELECT s.id, s.turno, s.semana_inicio,
                       (SELECT COUNT(*) FROM {tabla_det} d WHERE d.distribucion_id = s.id) AS filas,
                       (SELECT COUNT(DISTINCT d.creado_en) FROM {tabla_det} d WHERE d.distribucion_id = s.id) AS lotes,
                       (SELECT MIN(d.creado_en) FROM {tabla_det} d WHERE d.distribucion_id = s.id) AS creada
                FROM {tabla_sem} s
                WHERE s.estado != 'confirmado' AND s.semana_inicio >= ?
                ORDER BY s.semana_inicio, s.turno""",
            (corte,)
        ).fetchall()

        for s in candidatas:
            replicada = s["lotes"] == 1 and s["filas"] > MIN_FILAS
            marca = "BORRAR " if replicada else "conserva"
            print(f"  [{marca}] {s['turno']} {s['semana_inicio']}  "
                  f"filas={s['filas']:>4}  lotes={s['lotes']:>3}  creada={s['creada']}")
            if not replicada:
                continue

            tot_semanas += 1
            calendario.add(s["semana_inicio"])
            tot_filas += s["filas"]
            if APLICAR:
                for hija in hijas:
                    conn.execute(f"DELETE FROM {hija} WHERE distribucion_id=?", (s["id"],))
                conn.execute(f"DELETE FROM {tabla_sem} WHERE id=?", (s["id"],))

    print(f"\ncorte: solo semanas que arrancan el {corte} o después")
    print(f"{'APLICADO' if APLICAR else 'SIMULACION (nada escrito)'} — "
          f"{len(calendario)} semanas de calendario "
          f"({tot_semanas} filas semana/turno), {tot_filas} asignaciones")
    if not APLICAR:
        print("Volvé a correrlo con --aplicar para ejecutar el borrado.")
    else:
        print("Las semanas quedan sin crear: se rearman con 'copiar semana anterior'.")
