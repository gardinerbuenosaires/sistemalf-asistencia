"""Esquema del módulo de entrega de uniformes y EPP.

Vive en un archivo aparte y no adentro de `_migrate()` a propósito: el módulo se
desarrolla en una rama larga, y un bloque de ochenta líneas dentro de `_migrate()`
garantiza un merge conflictivo con main. Acá el punto de contacto con el resto del
sistema es una sola línea.

Todo es aditivo: crea tablas nuevas con prefijo `uniformes_` y agrega claves a
`configuracion`. No modifica ninguna tabla existente.
"""

import logging

logger = logging.getLogger(__name__)


# ── Claves de configuración ───────────────────────────────────────────────────
# Arrancan vacías: cada instalación carga las suyas desde la pantalla de
# Configuración. `nombre_empresa` NO se toca — es el nombre comercial que se
# muestra en el login y en el nav, distinto de la razón social.

CLAVES_CONFIGURACION = [
    ('uniformes_activo', '0',
     'Si está en 1, habilita el módulo de entrega de uniformes y EPP'),
    ('empresa_razon_social', '',
     'Razón social de la empresa (encabezado de los documentos impresos)'),
    ('empresa_cuit', '',
     'C.U.I.T. de la empresa (encabezado de los documentos impresos)'),
    ('empresa_direccion', '',
     'Domicilio de la empresa (encabezado de los documentos impresos)'),
    ('empresa_localidad', '',
     'Localidad de la empresa (encabezado de los documentos impresos)'),
    ('empresa_cp', '',
     'Código postal de la empresa (encabezado de los documentos impresos)'),
    ('empresa_provincia', '',
     'Provincia de la empresa (encabezado de los documentos impresos)'),
]

# Rubros iniciales: (nombre, meses_alerta). El umbral vive por rubro y no global
# porque seis meses tiene sentido para la ropa y ninguno para los guantes, que se
# reponen cuando se rompen. NULL = nunca avisa, solo informa.
RUBROS_INICIALES = [
    ("Ropa de trabajo", 6),
    ("EPP", None),
]

# Máximo de renglones por constancia. La hoja no pagina: medido sobre la maqueta
# impresa entran 16 con textos de una línea, y el tope de 14 deja margen para los
# nombres largos que bajan de renglón. Si hicieran falta más, son dos constancias.
MAX_ITEMS_POR_MOVIMIENTO = 14


_SCHEMA = """
-- ══════════════════════════════════════════════════════════════════════════
-- CATÁLOGOS
-- ══════════════════════════════════════════════════════════════════════════

-- Rubros: "Ropa de trabajo" y "EPP". En la UI se llaman *rubro* y nunca
-- *categoría*, porque `categorias` ya existe y es la del empleado por convenio.
CREATE TABLE IF NOT EXISTS uniformes_categorias (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre       TEXT NOT NULL UNIQUE,
    meses_alerta INTEGER,
    activo       INTEGER NOT NULL DEFAULT 1
);

-- Tipos de talle: Calzado, Pantalón, Pantalón (letra), Chaqueta…
-- Que existan dos tipos para la misma prenda es intencional: una persona es 42 en
-- una marca y L en otra, y las dos cosas son ciertas al mismo tiempo.
CREATE TABLE IF NOT EXISTS uniformes_tipos_talle (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL UNIQUE,
    orden  INTEGER NOT NULL DEFAULT 0,
    activo INTEGER NOT NULL DEFAULT 1
);

-- Los valores válidos de cada tipo. Se elige de una lista, no se escribe: con
-- texto libre aparecen "L", "l" y "Large" como tres talles distintos y el
-- recuento para comprar sale mal. `orden` es lo que permite mostrar S, M, L, XL
-- en ese orden y no alfabético.
CREATE TABLE IF NOT EXISTS uniformes_talle_valores (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo_talle_id INTEGER NOT NULL REFERENCES uniformes_tipos_talle(id) ON DELETE CASCADE,
    valor         TEXT NOT NULL,
    orden         INTEGER NOT NULL DEFAULT 0,
    activo        INTEGER NOT NULL DEFAULT 1,
    UNIQUE (tipo_talle_id, valor)
);

-- El catálogo de artículos. Las cuatro primeras columnas son las del formulario
-- de papel. `tipo_talle_id` NULL = el elemento no lleva talle.
CREATE TABLE IF NOT EXISTS uniformes_elementos (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre            TEXT NOT NULL,
    tipo_modelo       TEXT,
    marca             TEXT,
    posee_certificado INTEGER NOT NULL DEFAULT 0,
    categoria_id      INTEGER REFERENCES uniformes_categorias(id),
    tipo_talle_id     INTEGER REFERENCES uniformes_tipos_talle(id),
    activo            INTEGER NOT NULL DEFAULT 1,
    creado_en         TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS ix_uniformes_elementos_activo
    ON uniformes_elementos (activo, nombre);


-- ══════════════════════════════════════════════════════════════════════════
-- TALLES POR EMPLEADO
-- ══════════════════════════════════════════════════════════════════════════

-- Se registra aparte del historial porque para comprar hacen falta los talles de
-- todo el personal, incluida la gente que nunca recibió nada por el sistema.
-- Solo hay filas para quien tenga talle cargado.
CREATE TABLE IF NOT EXISTS uniformes_talles_empleado (
    empleado_id   INTEGER NOT NULL REFERENCES empleados(id) ON DELETE CASCADE,
    tipo_talle_id INTEGER NOT NULL REFERENCES uniformes_tipos_talle(id) ON DELETE CASCADE,
    valor         TEXT NOT NULL,
    modificado_en TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    PRIMARY KEY (empleado_id, tipo_talle_id)
);


-- ══════════════════════════════════════════════════════════════════════════
-- MOVIMIENTOS
-- ══════════════════════════════════════════════════════════════════════════

-- El evento. `tipo` queda preparado para la devolución, que se implementa más
-- adelante. `origen='historico'` son las entregas cargadas desde el papel: no
-- llevan número y no se imprimen, porque la firma ya existe en el legajo físico.
--
-- Los campos de empleado son una copia, no un JOIN: van impresos en un papel
-- firmado, así que si mañana corrigen un DNI mal cargado la hoja no cambia.
-- `creado_por` y `anulada_por` guardan el nombre por el mismo motivo.
CREATE TABLE IF NOT EXISTS uniformes_movimientos (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    empleado_id              INTEGER NOT NULL REFERENCES empleados(id),
    fecha                    TEXT NOT NULL,
    tipo                     TEXT NOT NULL DEFAULT 'entrega'
                                  CHECK (tipo IN ('entrega','devolucion')),
    origen                   TEXT NOT NULL DEFAULT 'sistema'
                                  CHECK (origen IN ('sistema','historico')),
    numero                   INTEGER,
    estado                   TEXT NOT NULL DEFAULT 'emitida'
                                  CHECK (estado IN ('emitida','anulada')),
    observaciones            TEXT,
    empleado_apellido_nombre TEXT,
    empleado_dni             TEXT,
    cargo_nombre             TEXT,
    anulada_en               TEXT,
    anulada_por              TEXT,
    motivo_anulacion         TEXT,
    creado_por               TEXT,
    creado_en                TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS ix_uniformes_mov_empleado
    ON uniformes_movimientos (empleado_id, fecha);

-- Serie de numeración separada por tipo. El índice es parcial porque los
-- históricos no llevan número y pueden ser muchos.
CREATE UNIQUE INDEX IF NOT EXISTS ux_uniformes_mov_numero
    ON uniformes_movimientos (tipo, numero) WHERE numero IS NOT NULL;


-- Los renglones. Guardan `elemento_id` (la relación, para que los reportes
-- agrupen por id y sigan funcionando aunque después se corrija un nombre) y
-- además una copia de lo que se imprimió (para que un papel firmado no cambie
-- si mañana editan el catálogo).
CREATE TABLE IF NOT EXISTS uniformes_items (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    movimiento_id     INTEGER NOT NULL REFERENCES uniformes_movimientos(id) ON DELETE CASCADE,
    elemento_id       INTEGER REFERENCES uniformes_elementos(id),
    cantidad          INTEGER NOT NULL DEFAULT 1,
    talle             TEXT,
    orden             INTEGER NOT NULL DEFAULT 0,
    observacion       TEXT,
    elemento_nombre   TEXT,
    tipo_modelo       TEXT,
    marca             TEXT,
    posee_certificado INTEGER,
    categoria_nombre  TEXT
);

CREATE INDEX IF NOT EXISTS ix_uniformes_items_mov
    ON uniformes_items (movimiento_id, orden);

CREATE INDEX IF NOT EXISTS ix_uniformes_items_elemento
    ON uniformes_items (elemento_id);


-- ══════════════════════════════════════════════════════════════════════════
-- PUESTOS QUE NO RECIBEN UNIFORME
-- ══════════════════════════════════════════════════════════════════════════

-- Solo sirve para que el reporte de última entrega no llene su parte de arriba
-- con gente que nunca va a recibir nada. Se marca lo que NO recibe: un cargo
-- nuevo, sin marcar, sigue apareciendo hasta que alguien decida. Tabla propia
-- para no tocar `cargos`, que usan otros módulos.
CREATE TABLE IF NOT EXISTS uniformes_cargos_sin_uniforme (
    cargo_id    INTEGER PRIMARY KEY REFERENCES cargos(id) ON DELETE CASCADE,
    marcado_por TEXT,
    marcado_en  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
"""


def migrar_uniformes(conn):
    """Crea las tablas del módulo y siembra configuración y rubros iniciales.

    Idempotente: se ejecuta en cada arranque y no pisa nada existente.
    """
    conn.executescript(_SCHEMA)

    conn.executemany(
        "INSERT OR IGNORE INTO configuracion (clave, valor, descripcion) VALUES (?,?,?)",
        CLAVES_CONFIGURACION,
    )

    # Rubros iniciales solo en una base virgen. Si el usuario los renombró o
    # borró, no se los devolvemos en el próximo arranque.
    if conn.execute("SELECT COUNT(*) FROM uniformes_categorias").fetchone()[0] == 0:
        conn.executemany(
            "INSERT INTO uniformes_categorias (nombre, meses_alerta) VALUES (?,?)",
            RUBROS_INICIALES,
        )
        logger.info("Uniformes: rubros iniciales creados (%s)",
                    ", ".join(n for n, _ in RUBROS_INICIALES))


def uniformes_activo(conn) -> bool:
    """Bandera del módulo. Con la bandera apagada el módulo no existe para nadie:
    ni link en el nav, ni rutas, ni fila en la matriz de roles."""
    row = conn.execute(
        "SELECT valor FROM configuracion WHERE clave='uniformes_activo'"
    ).fetchone()
    return bool(row) and str(row["valor"]).strip() == "1"
