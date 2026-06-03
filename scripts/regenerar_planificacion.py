"""
Regenera planificacion automatica para un rango de fechas.
Uso: python scripts/regenerar_planificacion.py 2026-06-01 2026-07-31
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.database import init_db
init_db()
from api.calendarios import generar_semana
from datetime import date, timedelta

fecha_desde = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date(2026, 6, 1)
fecha_hasta = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else date(2026, 7, 31)

lunes = fecha_desde - timedelta(days=fecha_desde.weekday())
totales = {"generados": 0, "omitidos": 0}

class _FakeUser: pass

while lunes <= fecha_hasta:
    r = generar_semana(str(lunes), _user=_FakeUser())
    totales["generados"] += r.get("generados", 0)
    totales["omitidos"]  += r.get("omitidos", 0)
    print(f"Semana {lunes}: +{r['generados']} generados, {r['omitidos']} omitidos")
    lunes += timedelta(weeks=1)

print(f"\nTotal: {totales}")
