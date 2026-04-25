"""
Enriquece los empleados ya existentes en la BD con datos del Excel de Enterprise.
Usa user_id como clave de match. No crea empleados nuevos ni borra nada.
"""
import xlrd
from db.database import init_db, db_session

EXCEL_FILE = "Listado de empleados.xls"
COL_CODIGO = 1
COL_NOMBRE = 5
COL_CATEGORIA = 10

def parse_codigo(val):
    if not val:
        return None
    return str(int(float(val))) if isinstance(val, float) else str(val).lstrip("0") or "0"

def parse_nombre(val):
    partes = val.strip().split()
    if len(partes) >= 2:
        return partes[-1], " ".join(partes[:-1])  # apellido=última, nombre=resto
    return partes[0] if partes else "", ""

def parse_categoria(val):
    if not val:
        return None
    partes = val.split(" - ", 1)
    return partes[1].strip() if len(partes) == 2 else val.strip()

def main():
    init_db()
    wb = xlrd.open_workbook(EXCEL_FILE)
    ws = wb.sheet_by_index(0)
    print(f"Filas en Excel: {ws.nrows - 2}")

    actualizados = 0
    sin_match = []

    with db_session() as conn:
        for r in range(2, ws.nrows):
            codigo_raw = ws.cell_value(r, COL_CODIGO)
            nombre_raw = ws.cell_value(r, COL_NOMBRE)
            categoria_raw = ws.cell_value(r, COL_CATEGORIA)

            user_id = parse_codigo(codigo_raw)
            if not user_id:
                continue

            nombre_raw = (nombre_raw or "").strip()
            categoria = parse_categoria(categoria_raw)

            if nombre_raw:
                apellido, nombre = parse_nombre(nombre_raw)
            else:
                apellido, nombre = "", ""

            cur = conn.execute(
                """
                UPDATE empleados
                SET apellido      = CASE WHEN ? != '' THEN ? ELSE apellido END,
                    nombre        = CASE WHEN ? != '' THEN ? ELSE nombre END,
                    cargo         = CASE WHEN ? IS NOT NULL THEN ? ELSE cargo END,
                    modificado_en = datetime('now','localtime')
                WHERE user_id = ?
                """,
                (apellido, apellido, nombre, nombre, categoria, categoria, user_id),
            )
            if cur.rowcount:
                actualizados += 1
                print(f"  + {user_id:>6}  {apellido}, {nombre}  [{categoria}]")
            else:
                sin_match.append(user_id)

    print(f"\nActualizados: {actualizados}")
    if sin_match:
        print(f"Sin match en BD ({len(sin_match)}): {', '.join(sin_match)}")

if __name__ == "__main__":
    main()
