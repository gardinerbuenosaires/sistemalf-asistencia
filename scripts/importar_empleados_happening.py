"""
Importa empleados desde el Excel de ZKTime (Happening) a una BD nueva.

Uso:
    python scripts/importar_empleados_happening.py

Crea data_happening/ con una BD vacía e importa los empleados.
"""
import os
import sys

# Apuntar a la BD de Happening antes de importar módulos del proyecto
os.environ["DB_PATH"] = "data_happening/fichajes.db"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import xlrd
from db.database import db_session, init_db

XLS_PATH = os.path.join(os.path.dirname(__file__), "Empleados_Happening.xls")
COL_ID    = 1
COL_NOMBRE = 5
COL_DEPTO  = 7


def _parse_nombre(raw: str):
    raw = raw.strip()
    if not raw:
        return None, None
    # Descartar filas donde el "nombre" es solo un número
    try:
        int(float(raw))
        return None, None
    except ValueError:
        pass

    if ", " in raw:
        apellido, nombre = raw.split(", ", 1)
    elif " " in raw:
        nombre, apellido = raw.split(" ", 1)
    else:
        nombre, apellido = raw, ""

    return nombre.strip().title(), apellido.strip().title()


def _parse_cargo(raw: str) -> str:
    raw = raw.strip()
    if " - " in raw:
        return raw.split(" - ", 1)[1].strip().title()
    return raw.title()


def _parse_uid(raw) -> str | None:
    try:
        return str(int(str(raw).split(".")[0]))
    except (ValueError, TypeError):
        return None


def main():
    # Inicializar BD vacía
    os.makedirs("data_happening", exist_ok=True)
    init_db()
    print(f"BD inicializada en data_happening/")

    wb = xlrd.open_workbook(XLS_PATH, encoding_override="cp1252")
    ws = wb.sheet_by_index(0)

    cargos_cache: dict[str, int] = {}
    importados = 0
    omitidos   = 0

    with db_session() as conn:
        for i in range(1, ws.nrows):
            nombre_raw = ws.cell_value(i, COL_NOMBRE)
            if not nombre_raw:
                omitidos += 1
                continue

            nombre, apellido = _parse_nombre(str(nombre_raw))
            if not nombre:
                omitidos += 1
                continue

            user_id  = _parse_uid(ws.cell_value(i, COL_ID))
            depto_raw = str(ws.cell_value(i, COL_DEPTO)).strip()
            cargo_nombre = _parse_cargo(depto_raw) if depto_raw else None

            cargo_id = None
            if cargo_nombre:
                if cargo_nombre not in cargos_cache:
                    row = conn.execute(
                        "SELECT id FROM cargos WHERE nombre=?", (cargo_nombre,)
                    ).fetchone()
                    if row:
                        cargos_cache[cargo_nombre] = row["id"]
                    else:
                        cur = conn.execute(
                            "INSERT INTO cargos (nombre) VALUES (?)", (cargo_nombre,)
                        )
                        cargos_cache[cargo_nombre] = cur.lastrowid
                cargo_id = cargos_cache[cargo_nombre]

            conn.execute(
                "INSERT OR IGNORE INTO empleados (user_id, nombre, apellido, cargo_id, activo) VALUES (?,?,?,?,1)",
                (user_id, nombre, apellido, cargo_id),
            )
            if conn.execute("SELECT changes()").fetchone()[0]:
                importados += 1
                print(f"  + {apellido}, {nombre}  (ID: {user_id}  Cargo: {cargo_nombre})")
            else:
                omitidos += 1

    print(f"\nListo — Importados: {importados} | Omitidos: {omitidos}")
    print(f"Cargos creados: {list(cargos_cache.keys())}")


if __name__ == "__main__":
    main()
