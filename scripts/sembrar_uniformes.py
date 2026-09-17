"""
Carga un catálogo inicial de tipos de talle y elementos para el módulo de
uniformes y EPP, pensado para un restaurante en Argentina.

Es un punto de partida para no cargar quince artículos a mano, NO un default del
sistema: por eso es un script y no parte de la migración. Se corre una vez, se
borra lo que no aplique y se corrige el resto desde la pantalla.

Dos campos quedan a propósito para completar a mano, porque salen impresos en un
documento que alguien firma y no se pueden inventar:

  · MARCA              queda vacía — depende de a quién le compren
  · POSEE CERTIFICADO  viene marcado solo donde la certificación es la razón de
                       ser del artículo (calzado de seguridad, antiparras,
                       guantes anticorte). Hay que confirmarlo contra la ficha
                       real del producto: es lo primero que mira una auditoría.

Es idempotente: lo que ya existe con el mismo nombre no se toca ni se duplica.

Uso:  python scripts/sembrar_uniformes.py [--aplicar]
      (sin --aplicar hace una simulación y no escribe nada)
"""
import sys, os

# La raiz del proyecto, y pararse ahi: DB_PATH sale de config.py como ruta
# relativa, asi que se resuelve contra el directorio actual. Sin esto, correr el
# script parado en scripts\ busca la base en scripts\data\ y, si ahi existiera
# alguna, escribiria en la que no es. Va ANTES de importar config.
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.chdir(RAIZ)

from pathlib import Path

from config import DB_PATH
from db.database import db_session

APLICAR = "--aplicar" in sys.argv

# Sin DB_PATH el default es relativo al directorio actual, así que es fácil
# terminar escribiendo en una base que no es. Mostrarla antes de tocar nada.
if not Path(DB_PATH).exists():
    sys.exit(f"No encuentro la base en {Path(DB_PATH).resolve()}\n"
             f"Revisa que el sistema este instalado ahi, o defini DB_PATH.")

print(f"Base: {Path(DB_PATH).resolve()}")
print("MODO: " + ("APLICAR (escribe)" if APLICAR else "simulación (no escribe nada)"))
print()


# ── Tipos de talle ────────────────────────────────────────────────────────────
# Un tipo por medida del cuerpo, no por prenda: chaqueta, camisa, chaleco, remera
# y campera comparten "Ropa superior" porque la misma persona usa el mismo talle
# en todas.
#
# Y un tipo por ESCALA, porque un tipo guarda un solo valor por persona: la misma
# persona es 42 en la marca que numera y L en la que usa letras, y las dos cosas
# tienen que poder convivir. Por eso cada prenda tiene su par (número) / (letra).
# El calzado no lo necesita: siempre viene numerado.
LETRAS = ["S", "M", "L", "XL", "XXL", "XXXL"]
NUMEROS = ["38", "40", "42", "44", "46", "48", "50", "52", "54", "56"]

TIPOS_TALLE = [
    ("Calzado",                ["36", "37", "38", "39", "40", "41",
                                "42", "43", "44", "45", "46"]),
    ("Pantalón (número)",      NUMEROS),
    ("Pantalón (letra)",       LETRAS),
    ("Ropa superior (número)", NUMEROS),
    ("Ropa superior (letra)",  LETRAS),
]

# ── Elementos ─────────────────────────────────────────────────────────────────
# (nombre, tipo_modelo, rubro, tipo_talle, posee_certificado)
# tipo_talle None = el artículo no lleva talle.
#
# La escala de cada prenda es un default razonable, no una verdad: en Argentina
# las prendas de arriba suelen venir en letras y los pantalones numerados, pero
# depende de la marca. Al cargar la marca real, revisar que la escala coincida.
ELEMENTOS = [
    # Cocina
    ("Chaqueta de cocina",           "Gabardina",                    "Ropa de trabajo", "Ropa superior (letra)",  False),
    ("Pantalón de cocina",           "Gabardina",                    "Ropa de trabajo", "Pantalón (número)",      False),
    ("Delantal de cocina con peto",  "Tela antifluido",              "Ropa de trabajo", None,                     False),
    ("Gorro de cocina",              "Ajustable",                    "Ropa de trabajo", None,                     False),
    # Salón
    ("Camisa de salón",              "Oxford",                       "Ropa de trabajo", "Ropa superior (letra)",  False),
    ("Pantalón de salón",            "Gabardina",                    "Ropa de trabajo", "Pantalón (número)",      False),
    ("Chaleco de salón",             "Gabardina",                    "Ropa de trabajo", "Ropa superior (letra)",  False),
    ("Delantal de mozo a la cintura", "Tela",                        "Ropa de trabajo", None,                     False),
    # Generales
    ("Remera de trabajo",            "Algodón",                      "Ropa de trabajo", "Ropa superior (letra)",  False),
    ("Campera de abrigo",            "Polar térmico",                "Ropa de trabajo", "Ropa superior (letra)",  False),
    # Calzado
    ("Calzado de seguridad",         "Puntera composite",            "EPP",             "Calzado",       True),
    ("Zueco antideslizante",         "Suela EVA",                    "Ropa de trabajo", "Calzado",       False),
    ("Botas de goma",                "Caña alta, suela antideslizante", "EPP",          "Calzado",       False),
    # Protección
    ("Guantes anticorte",            "Nivel 5",                      "EPP",             None,            True),
    ("Guantes térmicos para horno",  "Resistente hasta 250 °C",      "EPP",             None,            True),
    ("Antiparras de seguridad",      "Policarbonato incoloro",       "EPP",             None,            True),
    ("Delantal impermeable",         "PVC, para lavado",             "EPP",             None,            False),
    ("Faja lumbar",                  "Elástica con tiradores",       "EPP",             None,            True),
]


with db_session() as conn:
    if not conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='uniformes_elementos'"
    ).fetchone():
        sys.exit("ERROR: el módulo de uniformes no está migrado en esta base.")

    # ── Tipos de talle y sus escalas ─────────────────────────────────────────
    tipos_ids = {}
    nuevos_tipos = nuevos_valores = 0
    for nombre, escala in TIPOS_TALLE:
        row = conn.execute(
            "SELECT id FROM uniformes_tipos_talle WHERE nombre=?", (nombre,)
        ).fetchone()
        if row:
            tipos_ids[nombre] = row["id"]
            print(f"  = tipo de talle ya existe: {nombre}")
        else:
            nuevos_tipos += 1
            print(f"  + tipo de talle: {nombre}  ({len(escala)} valores)")
            if APLICAR:
                orden = conn.execute(
                    "SELECT COALESCE(MAX(orden),0)+1 FROM uniformes_tipos_talle"
                ).fetchone()[0]
                cur = conn.execute(
                    "INSERT INTO uniformes_tipos_talle (nombre, orden) VALUES (?,?)",
                    (nombre, orden),
                )
                tipos_ids[nombre] = cur.lastrowid

        tid = tipos_ids.get(nombre)
        if tid is None:
            # Tipo nuevo en simulación: todavía no existe, así que faltan todos.
            nuevos_valores += len(escala)
            continue
        for i, valor in enumerate(escala):
            ya = conn.execute(
                "SELECT id FROM uniformes_talle_valores WHERE tipo_talle_id=? AND valor=?",
                (tid, valor),
            ).fetchone()
            if ya:
                continue
            nuevos_valores += 1
            if APLICAR:
                conn.execute(
                    "INSERT INTO uniformes_talle_valores (tipo_talle_id, valor, orden) "
                    "VALUES (?,?,?)",
                    (tid, valor, i),
                )

    # ── Rubros ───────────────────────────────────────────────────────────────
    rubros = {r["nombre"]: r["id"]
              for r in conn.execute("SELECT id, nombre FROM uniformes_categorias")}
    faltan = {r for _, _, r, _, _ in ELEMENTOS} - set(rubros)
    if faltan:
        sys.exit(f"ERROR: faltan estos rubros en la base: {', '.join(sorted(faltan))}")

    # ── Elementos ────────────────────────────────────────────────────────────
    print()
    nuevos_el = 0
    for nombre, tipo_modelo, rubro, tipo_talle, cert in ELEMENTOS:
        ya = conn.execute(
            "SELECT id FROM uniformes_elementos WHERE nombre=?", (nombre,)
        ).fetchone()
        if ya:
            print(f"  = ya existe: {nombre}")
            continue
        nuevos_el += 1
        talle_txt = tipo_talle or "sin talle"
        print(f"  + {nombre:32} {rubro:16} {talle_txt:14} {'CERT' if cert else ''}")
        if APLICAR:
            conn.execute(
                """INSERT INTO uniformes_elementos
                       (nombre, tipo_modelo, marca, posee_certificado,
                        categoria_id, tipo_talle_id)
                   VALUES (?,?,NULL,?,?,?)""",
                (nombre, tipo_modelo, int(cert), rubros[rubro],
                 tipos_ids.get(tipo_talle) if tipo_talle else None),
            )

    print()
    print(f"  tipos de talle nuevos : {nuevos_tipos}")
    print(f"  valores de escala     : {nuevos_valores}")
    print(f"  elementos nuevos      : {nuevos_el}")
    if not APLICAR:
        print()
        print("  Simulación: no se escribió nada. Repetir con --aplicar.")
        conn.rollback()
