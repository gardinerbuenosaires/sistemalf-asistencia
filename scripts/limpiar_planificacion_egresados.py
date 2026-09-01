"""
Limpia la planificación y los resultados generados DESPUÉS de la fecha de egreso
de empleados desvinculados.

Hasta la corrección de generar_semana(), el generador automático no filtraba por
egreso (las asignaciones sobreviven a la baja), así que a los desvinculados se les
seguía generando planificación y el evaluador los marcaba ausentes todos los días.

Solo borra lo auto-generado y los resultados no corregidos a mano.

Uso:  python scripts/limpiar_planificacion_egresados.py [--aplicar]
      (sin --aplicar hace una simulación y no escribe nada)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path

from config import DB_PATH
from db.database import db_session

APLICAR = "--aplicar" in sys.argv

# Sin DB_PATH el default es relativo al directorio actual, así que es fácil
# terminar leyendo una base que no es. Mostrarla antes de tocar nada.
print(f"Base: {Path(DB_PATH).resolve()}")

with db_session() as conn:
    egresados = conn.execute(
        "SELECT id, apellido, nombre, fecha_egreso FROM empleados "
        "WHERE activo = 0 AND fecha_egreso IS NOT NULL AND fecha_egreso != '' "
        "ORDER BY fecha_egreso DESC"
    ).fetchall()

    tot_plan = tot_res = tot_emp = 0
    for e in egresados:
        eid, egr = e["id"], e["fecha_egreso"][:10]
        plan = conn.execute(
            "SELECT COUNT(*) FROM planificacion WHERE empleado_id=? AND fecha >= ? AND auto_generado=1",
            (eid, egr)).fetchone()[0]
        res = conn.execute(
            "SELECT COUNT(*) FROM resultados_dia WHERE empleado_id=? AND fecha >= ? AND corregido_manualmente=0",
            (eid, egr)).fetchone()[0]
        if not (plan or res):
            continue
        tot_emp += 1
        tot_plan += plan
        tot_res  += res
        print(f"  {eid:>4} {e['apellido'][:20]:<20} egreso={egr}  planificacion={plan:<4} resultados={res}")
        if APLICAR:
            conn.execute(
                "DELETE FROM planificacion WHERE empleado_id=? AND fecha >= ? AND auto_generado=1",
                (eid, egr))
            conn.execute(
                "DELETE FROM resultados_dia WHERE empleado_id=? AND fecha >= ? AND corregido_manualmente=0",
                (eid, egr))

    print(f"\n{'APLICADO' if APLICAR else 'SIMULACION (nada escrito)'} — "
          f"{tot_emp} empleados, {tot_plan} dias de planificacion, {tot_res} resultados")
    if not APLICAR:
        print("Volve a correrlo con --aplicar para ejecutar el borrado.")
