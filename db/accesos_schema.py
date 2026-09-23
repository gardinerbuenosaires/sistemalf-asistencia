"""Esquema del módulo de control de accesos.

Vive en un archivo aparte, por el mismo motivo que `uniformes_schema.py`: el
módulo se desarrolla en una rama larga y un bloque grande dentro de `_migrate()`
garantiza un merge conflictivo con main. Acá el punto de contacto es una línea.

Todo es aditivo: crea la tabla `dispositivos` y no modifica ninguna existente.

Qué resuelve. Hasta ahora el lector biométrico eran cuatro claves sueltas en
`configuracion` (`device_ip`, `device_port`, `device_password`, `device_timeout`),
que solo alcanzan para un equipo. El relevamiento mostró que un local tiene un
maestro de asistencia y varios lectores de puerta, así que la configuración pasa
a ser una tabla con una fila por equipo.

Por qué estas columnas y no menos. Hay tres que hoy no usa nadie y están a
propósito, porque agregarlas después de tener equipos cargados es una migración:

  · `protocolo`    pull  = el sistema llama al equipo por IP (lo que hacemos hoy)
                   push  = el equipo llama al sistema, y se identifica por número
                           de serie, no por IP. Los equipos nuevos vienen así.
  · `numero_serie` la identidad de un equipo push. Un equipo pull se direcciona
                   por IP; uno push puede ni tenerla fija.
  · `algoritmo_huella` v10 y v12 no son intercambiables y no hay conversión
                   posible. Una huella solo se le puede mandar a un equipo del
                   mismo algoritmo, así que el dato tiene que viajar con el equipo.

`cuenta_asistencia` es la que evita el accidente más caro: el procesador alterna
entrada/salida por orden cronológico, así que si las pasadas por una puerta
entraran a `fichajes` darían vuelta todo el día y romperían planilla, evaluador y
premios. Solo los equipos con esta marca alimentan la asistencia.
"""

import logging

logger = logging.getLogger(__name__)


def migrar_accesos(conn):
    """Crea la tabla de dispositivos y siembra el equipo ya configurado."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dispositivos (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre            TEXT NOT NULL,
            ubicacion         TEXT,

            -- Cómo se le habla
            protocolo         TEXT    NOT NULL DEFAULT 'pull',
            ip                TEXT,
            puerto            INTEGER NOT NULL DEFAULT 4370,
            password          INTEGER NOT NULL DEFAULT 0,
            timeout           INTEGER NOT NULL DEFAULT 10,
            transporte        TEXT,

            -- Qué es, según lo que informa el propio equipo
            numero_serie      TEXT,
            modelo            TEXT,
            firmware          TEXT,
            plataforma        TEXT,
            algoritmo_huella  TEXT,

            -- Para qué se usa
            cuenta_asistencia INTEGER NOT NULL DEFAULT 0,
            es_acceso         INTEGER NOT NULL DEFAULT 0,

            activo            INTEGER NOT NULL DEFAULT 1,
            orden             INTEGER NOT NULL DEFAULT 0,
            visto_en          TEXT,
            creado_en         TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            modificado_en     TEXT,

            CHECK (protocolo IN ('pull','push')),
            CHECK (transporte IS NULL OR transporte IN ('tcp','udp'))
        )
    """)
    # Un equipo pull se identifica por dónde está; uno push, por quién es. Los
    # índices son parciales para que las filas sin ese dato no choquen entre sí.
    conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS ix_dispositivos_ip
                    ON dispositivos (ip, puerto) WHERE ip IS NOT NULL""")
    conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS ix_dispositivos_serie
                    ON dispositivos (numero_serie) WHERE numero_serie IS NOT NULL""")

    _sembrar_equipo_actual(conn)


def _sembrar_equipo_actual(conn):
    """
    Pasa el lector ya configurado a la tabla, una sola vez.

    Las claves `device_*` de `configuracion` NO se borran, y es deliberado: si
    alguna vez hay que volver a una versión anterior del código, son el único
    lugar donde está la IP real de ese local. Cada instancia tiene la suya
    (Gardiner .1.201, Happening .0.201) y el default de `config.py` sirve para
    una sola de las dos. Mientras la tabla tenga filas, nadie las lee.
    """
    if conn.execute("SELECT 1 FROM dispositivos LIMIT 1").fetchone():
        return

    cfg = {
        r["clave"]: r["valor"]
        for r in conn.execute(
            "SELECT clave, valor FROM configuracion WHERE clave LIKE 'device_%'"
        )
    }

    def _entero(clave, defecto):
        try:
            return int(str(cfg.get(clave, defecto)).strip())
        except (TypeError, ValueError):
            return defecto

    conn.execute(
        """INSERT INTO dispositivos
               (nombre, protocolo, ip, puerto, password, timeout,
                cuenta_asistencia, es_acceso, activo, orden)
           VALUES (?, 'pull', ?, ?, ?, ?, 1, 0, 1, 0)""",
        (
            "Lector principal",
            (cfg.get("device_ip") or "").strip() or "192.168.1.201",
            _entero("device_port", 4370),
            _entero("device_password", 0),
            _entero("device_timeout", 10),
        ),
    )
    logger.info("Migración: dispositivos sembrado con el lector ya configurado")
