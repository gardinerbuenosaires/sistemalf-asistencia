"""
Aplica migraciones faltantes directamente sobre la DB.
Ejecutar desde C:\SistemAlf con:
    set DB_PATH=C:\ProgramData\SistemAlf\fichajes.db && python scripts/fix_migration.py
"""
import sqlite3
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


def add_col(tabla, columna, tipo):
    global cambios
    if not col_exists(tabla, columna):
        conn.execute(f"ALTER TABLE {tabla} ADD COLUMN {columna} {tipo}")
        print(f"  + {tabla}.{columna}")
        cambios += 1


def create_table(nombre, ddl):
    global cambios
    if not tabla_existe(nombre):
        conn.execute(ddl)
        print(f"  + tabla {nombre}")
        cambios += 1


# ── Tablas de catálogo requeridas por list_empleados ─────────────────────────
print("\n[Tablas de catálogo]")
create_table("turnos_legajo", """
    CREATE TABLE turnos_legajo (
        id     INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT UNIQUE NOT NULL,
        orden  INTEGER NOT NULL DEFAULT 0
    )
""")
create_table("sectores_legajo", """
    CREATE TABLE sectores_legajo (
        id     INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT UNIQUE NOT NULL,
        orden  INTEGER NOT NULL DEFAULT 0
    )
""")
create_table("niveles_estudio", """
    CREATE TABLE niveles_estudio (
        id     INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT UNIQUE NOT NULL,
        orden  INTEGER NOT NULL DEFAULT 0
    )
""")

# Datos iniciales si las tablas estaban vacías
if not conn.execute("SELECT 1 FROM turnos_legajo LIMIT 1").fetchone():
    for t in [("TM", 1), ("TN", 2), ("CORTADO", 3)]:
        conn.execute("INSERT OR IGNORE INTO turnos_legajo (nombre, orden) VALUES (?, ?)", t)
    print("  + datos iniciales turnos_legajo")
if not conn.execute("SELECT 1 FROM sectores_legajo LIMIT 1").fetchone():
    for s in [("SALÓN", 1), ("COCINA", 2)]:
        conn.execute("INSERT OR IGNORE INTO sectores_legajo (nombre, orden) VALUES (?, ?)", s)
    print("  + datos iniciales sectores_legajo")
if not conn.execute("SELECT 1 FROM niveles_estudio LIMIT 1").fetchone():
    for n in [("Primario", 1), ("Secundario", 2), ("Terciario", 3), ("Universitario", 4)]:
        conn.execute("INSERT OR IGNORE INTO niveles_estudio (nombre, orden) VALUES (?, ?)", n)
    print("  + datos iniciales niveles_estudio")

# ── Columnas en empleados ─────────────────────────────────────────────────────
print("\n[Columnas en empleados]")
add_col("empleados", "turno_id",         "INTEGER REFERENCES turnos_legajo(id)")
add_col("empleados", "sector_id",        "INTEGER REFERENCES sectores_legajo(id)")
add_col("empleados", "nivel_estudio_id", "INTEGER REFERENCES niveles_estudio(id)")
add_col("empleados", "nacionalidad",     "TEXT")
add_col("empleados", "estado_civil",     "TEXT")
add_col("empleados", "contacto_emergencia_nombre",     "TEXT")
add_col("empleados", "contacto_emergencia_telefono",   "TEXT")
add_col("empleados", "contacto_emergencia_parentesco", "TEXT")
add_col("empleados", "dom_calle",        "TEXT")
add_col("empleados", "dom_numero",       "TEXT")
add_col("empleados", "dom_piso",         "TEXT")
add_col("empleados", "dom_entre_calle1", "TEXT")
add_col("empleados", "dom_entre_calle2", "TEXT")
add_col("empleados", "dom_barrio",       "TEXT")
add_col("empleados", "dom_localidad",    "TEXT")
add_col("empleados", "dom_provincia",    "TEXT")
add_col("empleados", "dom_cp",           "TEXT")
add_col("empleados", "dom_lat",          "REAL")
add_col("empleados", "dom_lng",          "REAL")
add_col("empleados", "dom_mapa",         "TEXT")
add_col("empleados", "foto_path",        "TEXT")
add_col("empleados", "pagina_inicio",    "TEXT")

# ── cargos ────────────────────────────────────────────────────────────────────
print("\n[Columnas en cargos]")
add_col("cargos", "departamento_id", "INTEGER REFERENCES departamentos(id)")
add_col("cargos", "aplica_premio",   "INTEGER NOT NULL DEFAULT 0")

# ── departamentos ─────────────────────────────────────────────────────────────
print("\n[Columnas en departamentos]")
add_col("departamentos", "sector_id", "INTEGER REFERENCES sectores_legajo(id)")
add_col("departamentos", "activo",    "INTEGER NOT NULL DEFAULT 1")

# ── usuarios ──────────────────────────────────────────────────────────────────
print("\n[Columnas en usuarios]")
add_col("usuarios", "pagina_inicio", "TEXT")

# ── roles ─────────────────────────────────────────────────────────────────────
print("\n[Columnas en roles]")
add_col("roles", "pagina_inicio", "TEXT DEFAULT '/'")

# ── distribucion ──────────────────────────────────────────────────────────────
print("\n[Distribución]")
if tabla_existe("distribucion_detalle"):
    add_col("distribucion_detalle", "horario_id", "INTEGER REFERENCES horarios(id)")
create_table("distribucion_franco", """
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

conn.commit()
conn.close()

print(f"\n{cambios} cambio(s) aplicado(s).")
if cambios > 0:
    print("Reiniciar el servicio para que tome efecto.")
else:
    print("La DB ya estaba al dia.")
