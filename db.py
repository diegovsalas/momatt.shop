# db.py — Capa de base de datos (Postgres)
# -------------------------------------------------------------------
# Diseño minimalista: psycopg3 con connection pool global. Sin ORM.
# Las migraciones (CREATE TABLE IF NOT EXISTS) se corren al arrancar.
#
# Si DATABASE_URL no está configurada, el app sigue arrancando pero
# las funcionalidades que requieren BD (login, registro, pedidos
# persistentes, /perfil) quedan deshabilitadas con warning.
# -------------------------------------------------------------------
import json
import os
from typing import Optional

try:
    from psycopg_pool import ConnectionPool
    from psycopg.rows import dict_row
    _PSYCOPG_OK = True
except ImportError:
    _PSYCOPG_OK = False


DATABASE_URL = os.getenv("DATABASE_URL", "")
_pool: Optional["ConnectionPool"] = None


def disponible() -> bool:
    """¿La BD está configurada y operativa?"""
    return _pool is not None


def init():
    """Inicializa el pool y corre migraciones. Llamar al arrancar el app."""
    global _pool
    if not DATABASE_URL or not _PSYCOPG_OK:
        print("⚠  DATABASE_URL no configurada o psycopg ausente. "
              "Auth y persistencia de pedidos deshabilitados.")
        return

    # Render exige sslmode=require en producción
    url = DATABASE_URL
    if "sslmode=" not in url and "render.com" in url:
        url += ("&" if "?" in url else "?") + "sslmode=require"

    _pool = ConnectionPool(conninfo=url, min_size=1, max_size=5, open=True)
    _migrate()
    print("✓ Postgres conectado y migraciones aplicadas.")


def _migrate():
    """CREATE TABLE IF NOT EXISTS — idempotente, se corre en cada arranque."""
    with _pool.connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                id            SERIAL PRIMARY KEY,
                email         TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                nombre        TEXT NOT NULL,
                telefono      TEXT,
                created_at    TIMESTAMPTZ DEFAULT NOW()
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pedidos (
                id            TEXT PRIMARY KEY,
                user_id       INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
                email         TEXT NOT NULL,
                nombre        TEXT NOT NULL,
                telefono      TEXT,
                items         JSONB NOT NULL,
                subtotal_prod INTEGER NOT NULL,
                envio         INTEGER NOT NULL,
                iva           INTEGER NOT NULL,
                total         INTEGER NOT NULL,
                unidades      INTEGER NOT NULL,
                paqueteria    TEXT NOT NULL,
                metodo        TEXT NOT NULL,
                estado        TEXT NOT NULL DEFAULT 'pendiente_pago',
                created_at    TIMESTAMPTZ DEFAULT NOW()
            );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pedidos_email   ON pedidos(email);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pedidos_user_id ON pedidos(user_id);")
        # Migración aditiva: ciudad/estado de entrega y datos fiscales
        # opcionales. ADD COLUMN IF NOT EXISTS es seguro de re-ejecutar.
        conn.execute("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS ciudad_entrega TEXT;")
        conn.execute("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS estado_entrega TEXT;")
        conn.execute("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS sucursal       TEXT;")
        conn.execute("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS rfc            TEXT;")
        conn.execute("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS razon_social   TEXT;")
        conn.execute("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS cp_fiscal      TEXT;")
        conn.commit()


# ------------------- USUARIOS -------------------

def crear_usuario(email: str, password_hash: str, nombre: str, telefono: str = ""):
    """Inserta usuario y devuelve el row creado. Lanza si el email ya existe."""
    with _pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "INSERT INTO usuarios (email, password_hash, nombre, telefono) "
                "VALUES (%s, %s, %s, %s) RETURNING *;",
                (email.lower().strip(), password_hash, nombre.strip(), telefono.strip() or None),
            )
            return cur.fetchone()


def buscar_usuario_por_email(email: str):
    with _pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM usuarios WHERE email = %s;", (email.lower().strip(),))
            return cur.fetchone()


def buscar_usuario_por_id(user_id: int):
    with _pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM usuarios WHERE id = %s;", (user_id,))
            return cur.fetchone()


# ------------------- PEDIDOS -------------------

def crear_pedido(pedido_id: str, user_id, email: str, nombre: str, telefono: str,
                 items: list, subtotal_prod: int, envio: int, iva: int, total: int,
                 unidades: int, paqueteria: str, metodo: str,
                 ciudad_entrega: str = "", estado_entrega: str = "",
                 sucursal: str = "",
                 rfc: str = "", razon_social: str = "", cp_fiscal: str = ""):
    """Persiste un pedido. user_id puede ser None (guest checkout)."""
    with _pool.connection() as conn:
        conn.execute(
            "INSERT INTO pedidos (id, user_id, email, nombre, telefono, items, "
            "subtotal_prod, envio, iva, total, unidades, paqueteria, metodo, "
            "ciudad_entrega, estado_entrega, sucursal, rfc, razon_social, cp_fiscal) "
            "VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, "
            "%s, %s, %s, %s, %s, %s);",
            (pedido_id, user_id, email.lower().strip(), nombre, telefono or None,
             json.dumps(items), subtotal_prod, envio, iva, total, unidades, paqueteria, metodo,
             ciudad_entrega or None, estado_entrega or None, sucursal or None,
             rfc or None, razon_social or None, cp_fiscal or None),
        )
        conn.commit()


def buscar_pedido(pedido_id: str):
    with _pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM pedidos WHERE id = %s;", (pedido_id,))
            return cur.fetchone()


def listar_pedidos_de_usuario(user_id: int):
    with _pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT * FROM pedidos WHERE user_id = %s ORDER BY created_at DESC;",
                (user_id,),
            )
            return cur.fetchall()


def adoptar_pedidos_huerfanos(user_id: int, email: str):
    """Cuando un usuario se registra, asocia sus pedidos previos hechos
    como guest (user_id NULL) con el mismo email."""
    with _pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE pedidos SET user_id = %s "
                "WHERE user_id IS NULL AND email = %s;",
                (user_id, email.lower().strip()),
            )
            n = cur.rowcount
            conn.commit()
            return n
