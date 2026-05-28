"""
Aplica migraciones faltantes directamente sobre la DB.
Ejecutar desde C:\SistemAlf con:  python scripts/fix_migration.py
"""
import sqlite3
import sys
import os

DB_PATH = os.getenv("DB_PATH", "fichajes.db")

print(f"DB: {os.path.abspath(DB_PATH)}")

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
cambios = 0

def col_exists(tabla, columna):
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({tabla})").fetchall()}
    return columna in cols

def tabla_existe(tabla):
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (tabla,)
    ).fetchone())

# ── cargos.departamento_id ────────────────────────────────────────────────────
if not col_exists("cargos", "departamento_id"):
    conn.execute("ALTER TABLE cargos ADD COLUMN departamento_id INTEGER REFERENCES departamentos(id)")
    print("OK: columna departamento_id agregada a cargos")
    cambios += 1
else:
    print("OK: cargos.departamento_id ya existe")

# ── distribucion_detalle.horario_id ───────────────────────────────────────────
if tabla_existe("distribucion_detalle") and not col_exists("distribucion_detalle", "horario_id"):
    conn.execute("ALTER TABLE distribucion_detalle ADD COLUMN horario_id INTEGER REFERENCES horarios(id)")
    print("OK: columna horario_id agregada a distribucion_detalle")
    cambios += 1
else:
    if tabla_existe("distribucion_detalle"):
        print("OK: distribucion_detalle.horario_id ya existe")

# ── distribucion_franco ───────────────────────────────────────────────────────
if not tabla_existe("distribucion_franco"):
    conn.execute("""
        CREATE TABLE distribucion_franco (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            distribucion_id INTEGER NOT NULL REFERENCES distribucion_semana(id) ON DELETE CASCADE,
            empleado_id     INTEGER NOT NULL REFERENCES empleados(id),
            fecha           TEXT    NOT NULL,
            creado_por      INTEGER REFERENCES usuarios(id),
            creado_en       TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
            UNIQUE(distribucion_id, empleado_id, fecha)
        )
    """)
    print("OK: tabla distribucion_franco creada")
    cambios += 1
else:
    print("OK: distribucion_franco ya existe")

conn.commit()
conn.close()

print(f"\n{cambios} cambio(s) aplicado(s).")
if cambios > 0:
    print("Reiniciar el servicio para que tome efecto.")
