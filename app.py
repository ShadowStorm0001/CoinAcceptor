import os
import sqlite3
import threading
import time
from datetime import datetime, timezone

from flask import Flask, jsonify, request, render_template_string, session, redirect, url_for
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)

app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret-key")

KIOSK_ID = "KIOSK1"
COIN_VALUE_CENTS = 100

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin"

# ------------------------------------------------------------
# DATABASE
# ------------------------------------------------------------

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

if DATABASE_URL:
    import psycopg2
    import psycopg2.extras

    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

    DB_TYPE = "postgres"
else:
    DB_TYPE = "sqlite"

    # Local development database.
    # On Render, use PostgreSQL for persistence.
    DB_FILE = os.environ.get("DB_FILE", "kiosk.db")


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ------------------------------------------------------------
# DATABASE HELPERS
# ------------------------------------------------------------

db_lock = threading.RLock()


def get_db():
    if DB_TYPE == "postgres":
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = False
        return conn

    conn = sqlite3.connect(
        DB_FILE,
        timeout=30,
        check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    return conn


def close_db(conn):
    try:
        conn.close()
    except Exception:
        pass


def init_db():
    with db_lock:
        conn = get_db()

        try:
            cur = conn.cursor()

            if DB_TYPE == "postgres":
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS cards (
                        card_id TEXT PRIMARY KEY,
                        balance_cents INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                """)

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS sessions (
                        id SERIAL PRIMARY KEY,
                        kiosk_id TEXT NOT NULL,
                        card_id TEXT NOT NULL,
                        mode TEXT NOT NULL,
                        status TEXT NOT NULL,
                        coin_count INTEGER NOT NULL DEFAULT 0,
                        amount_cents INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                """)

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS transactions (
                        id SERIAL PRIMARY KEY,
                        kiosk_id TEXT NOT NULL,
                        card_id TEXT NOT NULL,
                        type TEXT NOT NULL,
                        amount_cents INTEGER NOT NULL DEFAULT 0,
                        coins INTEGER NOT NULL DEFAULT 0,
                        description TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                """)

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS coin_events (
                        id SERIAL PRIMARY KEY,
                        kiosk_id TEXT NOT NULL,
                        card_id TEXT,
                        accepted INTEGER NOT NULL,
                        coins INTEGER NOT NULL,
                        amount_cents INTEGER NOT NULL,
                        reason TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                """)

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS products (
                        id SERIAL PRIMARY KEY,
                        name TEXT NOT NULL,
                        price_cents INTEGER NOT NULL,
                        active INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                """)

            else:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS cards (
                        card_id TEXT PRIMARY KEY,
                        balance_cents INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                """)

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS sessions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        kiosk_id TEXT NOT NULL,
                        card_id TEXT NOT NULL,
                        mode TEXT NOT NULL,
                        status TEXT NOT NULL,
                        coin_count INTEGER NOT NULL DEFAULT 0,
                        amount_cents INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                """)

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS transactions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        kiosk_id TEXT NOT NULL,
                        card_id TEXT NOT NULL,
                        type TEXT NOT NULL,
                        amount_cents INTEGER NOT NULL DEFAULT 0,
                        coins INTEGER NOT NULL DEFAULT 0,
                        description TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                """)

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS coin_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        kiosk_id TEXT NOT NULL,
                        card_id TEXT,
                        accepted INTEGER NOT NULL,
                        coins INTEGER NOT NULL,
                        amount_cents INTEGER NOT NULL,
                        reason TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                """)

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS products (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        price_cents INTEGER NOT NULL,
                        active INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                """)

            conn.commit()

            # Add default products only if none exist.
            if DB_TYPE == "postgres":
                cur.execute("SELECT COUNT(*) FROM products")
            else:
                cur.execute("SELECT COUNT(*) AS count FROM products")

            count = cur.fetchone()[0]

            if count == 0:
                now = utc_now()

                defaults = [
                    ("Service 1", 200),
                    ("Service 2", 500),
                    ("Service 3", 1000)
                ]

                for name, price in defaults:
                    if DB_TYPE == "postgres":
                        cur.execute("""
                            INSERT INTO products
                            (name, price_cents, active, created_at, updated_at)
                            VALUES (%s, %s, 1, %s, %s)
                        """, (name, price, now, now))
                    else:
                        cur.execute("""
                            INSERT INTO products
                            (name, price_cents, active, created_at, updated_at)
                            VALUES (?, ?, 1, ?, ?)
                        """, (name, price, now, now))

                conn.commit()

        finally:
            close_db(conn)


init_db()


def query_one(sql, params=()):
    with db_lock:
        conn = get_db()

        try:
            cur = conn.cursor()

            if DB_TYPE == "postgres":
                cur.execute(sql, params)
                row = cur.fetchone()

                if row is None:
                    return None

                columns = [d[0] for d in cur.description]
                return dict(zip(columns, row))

            cur.execute(sql, params)
            row = cur.fetchone()

            return dict(row) if row else None

        finally:
            close_db(conn)


def query_all(sql, params=()):
    with db_lock:
        conn = get_db()

        try:
            cur = conn.cursor()

            if DB_TYPE == "postgres":
                cur.execute(sql, params)
                rows = cur.fetchall()
                columns = [d[0] for d in cur.description]
                return [dict(zip(columns, row)) for row in rows]

            cur.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]

        finally:
            close_db(conn)


def execute(sql, params=()):
    with db_lock:
        conn = get_db()

        try:
            cur = conn.cursor()
            cur.execute(sql, params)
            conn.commit()

            if DB_TYPE == "postgres":
                try:
                    return cur.fetchone()
                except Exception:
                    return None

            return cur.lastrowid

        finally:
            close_db(conn)


# ------------------------------------------------------------
# COMMON FUNCTIONS
# ------------------------------------------------------------

def money(cents):
    return f"${cents / 100:.2f}"


def ensure_card(card_id):
    card_id = str(card_id).strip()

    if not card_id:
        return None

    existing = query_one(
        "SELECT * FROM cards WHERE card_id = ?" if DB_TYPE == "sqlite"
        else "SELECT * FROM cards WHERE card_id = %s",
        (card_id,)
    )

    if existing:
        return existing

    now = utc_now()

    if DB_TYPE == "sqlite":
        execute("""
            INSERT INTO cards
            (card_id, balance_cents, created_at, updated_at)
            VALUES (?, 0, ?, ?)
        """, (card_id, now, now))
    else:
        execute("""
            INSERT INTO cards
            (card_id, balance_cents, created_at, updated_at)
            VALUES (%s, 0, %s, %s)
        """, (card_id, now, now))

    return query_one(
        "SELECT * FROM cards WHERE card_id = ?" if DB_TYPE == "sqlite"
        else "SELECT * FROM cards WHERE card_id = %s",
        (card_id,)
    )


def get_active_session():
    if DB_TYPE == "sqlite":
        return query_one("""
            SELECT *
            FROM sessions
            WHERE kiosk_id = ?
            AND status = 'active'
            ORDER BY id DESC
            LIMIT 1
        """, (KIOSK_ID,))

    return query_one("""
        SELECT *
        FROM sessions
        WHERE kiosk_id = %s
        AND status = 'active'
        ORDER BY id DESC
        LIMIT 1
    """, (KIOSK_ID,))


def close_active_session():
    now = utc_now()

    if DB_TYPE == "sqlite":
        execute("""
            UPDATE sessions
            SET status = 'cancelled',
                updated_at = ?
            WHERE kiosk_id = ?
            AND status = 'active'
        """, (now, KIOSK_ID))
    else:
        execute("""
            UPDATE sessions
            SET status = 'cancelled',
                updated_at = %s
            WHERE kiosk_id = %s
            AND status = 'active'
        """, (now, KIOSK_ID))


# ------------------------------------------------------------
# KIOSK
# ------------------------------------------------------------

@app.route("/")
def index():
    return redirect(url_for("kiosk"))


@app.route("/kiosk")
def kiosk():
    return render_template_string(
        KIOSK_HTML,
        KIOSK_ID=KIOSK_ID
    )


@app.route("/api/kiosk/scan", methods=["POST"])
def kiosk_scan():
    data = request.get_json(silent=True) or {}

    card_id = str(data.get("card_id", "")).strip()
    mode = str(data.get("mode", "")).strip().lower()

    if not card_id:
        return jsonify({
            "success": False,
            "error": "CARD_REQUIRED"
        }), 400

    if mode not in ("charge", "balance", "store"):
        return jsonify({
            "success": False,
            "error": "INVALID_MODE"
        }), 400

    card = ensure_card(card_id)

    if mode == "charge":

        # Do not leave an old session hanging around.
        close_active_session()

        now = utc_now()

        if DB_TYPE == "sqlite":
            execute("""
                INSERT INTO sessions
                (kiosk_id, card_id, mode, status,
                 coin_count, amount_cents, created_at, updated_at)
                VALUES (?, ?, 'charge', 'active', 0, 0, ?, ?)
            """, (KIOSK_ID, card_id, now, now))
        else:
            execute("""
                INSERT INTO sessions
                (kiosk_id, card_id, mode, status,
                 coin_count, amount_cents, created_at, updated_at)
                VALUES (%s, %s, 'charge', 'active', 0, 0, %s, %s)
            """, (KIOSK_ID, card_id, now, now))

        return jsonify({
            "success": True,
            "card_id": card_id,
            "balance_cents": card["balance_cents"],
            "balance": money(card["balance_cents"]),
            "mode": "charge"
        })

    return jsonify({
        "success": True,
        "card_id": card_id,
        "balance_cents": card["balance_cents"],
        "balance": money(card["balance_cents"]),
        "mode": mode
    })


@app.route("/api/kiosk/state")
def kiosk_state():
    active = get_active_session()

    if not active:
        return jsonify({
            "success": True,
            "active": False
        })

    return jsonify({
        "success": True,
        "active": True,
        "session": {
            "id": active["id"],
            "card_id": active["card_id"],
            "coin_count": active["coin_count"],
            "amount_cents": active["amount_cents"],
            "amount": money(active["amount_cents"])
        }
    })


@app.route("/api/kiosk/end", methods=["POST"])
def kiosk_end():
    close_active_session()

    return jsonify({
        "success": True
    })


# ------------------------------------------------------------
# COIN RECEIVER
# ------------------------------------------------------------

@app.route("/api/coin", methods=["POST"])
def coin():
    data = request.get_json(silent=True) or {}

    kiosk_id = str(data.get("kiosk_id", "")).strip()
    coins = int(data.get("coins", 1))

    if coins < 1:
        coins = 1

    # The Pi MUST identify itself as KIOSK1.
    if kiosk_id != KIOSK_ID:
        execute_coin_event(
            kiosk_id=kiosk_id or "UNKNOWN",
            card_id=None,
            accepted=0,
            coins=coins,
            amount_cents=coins * COIN_VALUE_CENTS,
            reason="INVALID_KIOSK_ID"
        )

        return jsonify({
            "success": False,
            "error": "INVALID_KIOSK_ID"
        }), 403

    active = get_active_session()

    # Always record the physical coin event.
    if not active:
        execute_coin_event(
            kiosk_id=KIOSK_ID,
            card_id=None,
            accepted=0,
            coins=coins,
            amount_cents=coins * COIN_VALUE_CENTS,
            reason="NO_ACTIVE_CARD_SESSION"
        )

        return jsonify({
            "success": False,
            "error": "NO_ACTIVE_CARD_SESSION",
            "message": "No active charge session."
        }), 409

    new_count = active["coin_count"] + coins
    new_amount = active["amount_cents"] + (
        coins * COIN_VALUE_CENTS
    )

    now = utc_now()

    with db_lock:
        conn = get_db()

        try:
            cur = conn.cursor()

            if DB_TYPE == "sqlite":
                cur.execute("""
                    UPDATE sessions
                    SET coin_count = ?,
                        amount_cents = ?,
                        updated_at = ?
                    WHERE id = ?
                    AND kiosk_id = ?
                    AND status = 'active'
                """, (
                    new_count,
                    new_amount,
                    now,
                    active["id"],
                    KIOSK_ID
                ))
            else:
                cur.execute("""
                    UPDATE sessions
                    SET coin_count = %s,
                        amount_cents = %s,
                        updated_at = %s
                    WHERE id = %s
                    AND kiosk_id = %s
                    AND status = 'active'
                """, (
                    new_count,
                    new_amount,
                    now,
                    active["id"],
                    KIOSK_ID
                ))

            if DB_TYPE == "sqlite":
                cur.execute("""
                    INSERT INTO coin_events
                    (kiosk_id, card_id, accepted, coins,
                     amount_cents, reason, created_at)
                    VALUES (?, ?, 1, ?, ?, 'ACCEPTED', ?)
                """, (
                    KIOSK_ID,
                    active["card_id"],
                    coins,
                    coins * COIN_VALUE_CENTS,
                    now
                ))
            else:
                cur.execute("""
                    INSERT INTO coin_events
                    (kiosk_id, card_id, accepted, coins,
                     amount_cents, reason, created_at)
                    VALUES (%s, %s, 1, %s, %s, 'ACCEPTED', %s)
                """, (
                    KIOSK_ID,
                    active["card_id"],
                    coins,
                    coins * COIN_VALUE_CENTS,
                    now
                ))

            conn.commit()

        except Exception:
            conn.rollback()
            raise

        finally:
            close_db(conn)

    return jsonify({
        "success": True,
        "card_id": active["card_id"],
        "coins": new_count,
        "amount_cents": new_amount,
        "amount": money(new_amount)
    })


def execute_coin_event(
    kiosk_id,
    card_id,
    accepted,
    coins,
    amount_cents,
    reason
):
    now = utc_now()

    if DB_TYPE == "sqlite":
        execute("""
            INSERT INTO coin_events
            (kiosk_id, card_id, accepted, coins,
             amount_cents, reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            kiosk_id,
            card_id,
            accepted,
            coins,
            amount_cents,
            reason,
            now
        ))
    else:
        execute("""
            INSERT INTO coin_events
            (kiosk_id, card_id, accepted, coins,
             amount_cents, reason, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            kiosk_id,
            card_id,
            accepted,
            coins,
            amount_cents,
            reason,
            now
        ))


# ------------------------------------------------------------
# CONFIRM CHARGE
# ------------------------------------------------------------

@app.route("/api/kiosk/confirm-charge", methods=["POST"])
def confirm_charge():
    data = request.get_json(silent=True) or {}

    scanned_card = str(
        data.get("card_id", "")
    ).strip()

    if not scanned_card:
        return jsonify({
            "success": False,
            "error": "CARD_REQUIRED"
        }), 400

    active = get_active_session()

    if not active:
        return jsonify({
            "success": False,
            "error": "NO_ACTIVE_CARD_SESSION"
        }), 409

    amount = active["amount_cents"]

    if amount <= 0:
        return jsonify({
            "success": False,
            "error": "NO_MONEY",
            "message": "No coins have been inserted."
        }), 400

    original_card = active["card_id"]

    # DIFFERENT CARD
    if scanned_card != original_card:
        ensure_card(scanned_card)

        return jsonify({
            "success": False,
            "different_card": True,
            "original_card": original_card,
            "scanned_card": scanned_card,
            "amount_cents": amount,
            "amount": money(amount)
        }), 409

    # SAME CARD
    return finalize_charge(
        active,
        scanned_card
    )


def finalize_charge(active, card_id):
    amount = active["amount_cents"]
    coins = active["coin_count"]
    now = utc_now()

    with db_lock:
        conn = get_db()

        try:
            cur = conn.cursor()

            if DB_TYPE == "sqlite":
                cur.execute(
                    "SELECT balance_cents FROM cards WHERE card_id = ?",
                    (card_id,)
                )
            else:
                cur.execute(
                    "SELECT balance_cents FROM cards WHERE card_id = %s FOR UPDATE",
                    (card_id,)
                )

            card_row = cur.fetchone()

            if not card_row:
                conn.rollback()

                return jsonify({
                    "success": False,
                    "error": "CARD_NOT_FOUND"
                }), 404

            balance = card_row[0]
            new_balance = balance + amount

            if DB_TYPE == "sqlite":
                cur.execute("""
                    UPDATE cards
                    SET balance_cents = ?,
                        updated_at = ?
                    WHERE card_id = ?
                """, (
                    new_balance,
                    now,
                    card_id
                ))

                cur.execute("""
                    INSERT INTO transactions
                    (kiosk_id, card_id, type,
                     amount_cents, coins, description, created_at)
                    VALUES (?, ?, 'CHARGE', ?, ?, ?, ?)
                """, (
                    KIOSK_ID,
                    card_id,
                    amount,
                    coins,
                    "Card charged",
                    now
                ))

                cur.execute("""
                    UPDATE sessions
                    SET status = 'completed',
                        updated_at = ?
                    WHERE id = ?
                """, (
                    now,
                    active["id"]
                ))

            else:
                cur.execute("""
                    UPDATE cards
                    SET balance_cents = %s,
                        updated_at = %s
                    WHERE card_id = %s
                """, (
                    new_balance,
                    now,
                    card_id
                ))

                cur.execute("""
                    INSERT INTO transactions
                    (kiosk_id, card_id, type,
                     amount_cents, coins, description, created_at)
                    VALUES (%s, %s, 'CHARGE', %s, %s, %s, %s)
                """, (
                    KIOSK_ID,
                    card_id,
                    amount,
                    coins,
                    "Card charged",
                    now
                ))

                cur.execute("""
                    UPDATE sessions
                    SET status = 'completed',
                        updated_at = %s
                    WHERE id = %s
                """, (
                    now,
                    active["id"]
                ))

            conn.commit()

        except Exception:
            conn.rollback()
            raise

        finally:
            close_db(conn)

    return jsonify({
        "success": True,
        "card_id": card_id,
        "amount_cents": amount,
        "amount": money(amount),
        "balance_cents": new_balance,
        "balance": money(new_balance)
    })


@app.route("/api/kiosk/confirm-different-card", methods=["POST"])
def confirm_different_card():
    data = request.get_json(silent=True) or {}

    scanned_card = str(
        data.get("card_id", "")
    ).strip()

    if not scanned_card:
        return jsonify({
            "success": False,
            "error": "CARD_REQUIRED"
        }), 400

    active = get_active_session()

    if not active:
        return jsonify({
            "success": False,
            "error": "NO_ACTIVE_CARD_SESSION"
        }), 409

    # The new card must be different from the original.
    if scanned_card == active["card_id"]:
        return finalize_charge(
            active,
            scanned_card
        )

    ensure_card(scanned_card)

    return finalize_charge(
        active,
        scanned_card
    )


# ------------------------------------------------------------
# STORE
# ------------------------------------------------------------

@app.route("/api/store/products")
def store_products():
    if DB_TYPE == "sqlite":
        products = query_all("""
            SELECT *
            FROM products
            WHERE active = 1
            ORDER BY id ASC
        """)
    else:
        products = query_all("""
            SELECT *
            FROM products
            WHERE active = 1
            ORDER BY id ASC
        """)

    return jsonify({
        "success": True,
        "products": [
            {
                "id": p["id"],
                "name": p["name"],
                "price_cents": p["price_cents"],
                "price": money(p["price_cents"])
            }
            for p in products
        ]
    })


@app.route("/api/store/buy", methods=["POST"])
def store_buy():
    data = request.get_json(silent=True) or {}

    card_id = str(data.get("card_id", "")).strip()

    try:
        product_id = int(data.get("product_id"))
    except Exception:
        return jsonify({
            "success": False,
            "error": "INVALID_PRODUCT"
        }), 400

    if not card_id:
        return jsonify({
            "success": False,
            "error": "CARD_REQUIRED"
        }), 400

    if DB_TYPE == "sqlite":
        product = query_one("""
            SELECT *
            FROM products
            WHERE id = ?
            AND active = 1
        """, (product_id,))
    else:
        product = query_one("""
            SELECT *
            FROM products
            WHERE id = %s
            AND active = 1
        """, (product_id,))

    if not product:
        return jsonify({
            "success": False,
            "error": "PRODUCT_NOT_FOUND"
        }), 404

    now = utc_now()

    with db_lock:
        conn = get_db()

        try:
            cur = conn.cursor()

            if DB_TYPE == "sqlite":
                cur.execute("""
                    SELECT balance_cents
                    FROM cards
                    WHERE card_id = ?
                """, (card_id,))
            else:
                cur.execute("""
                    SELECT balance_cents
                    FROM cards
                    WHERE card_id = %s
                    FOR UPDATE
                """, (card_id,))

            card = cur.fetchone()

            if not card:
                conn.rollback()

                return jsonify({
                    "success": False,
                    "error": "CARD_NOT_FOUND"
                }), 404

            balance = card[0]
            price = product["price_cents"]

            if balance < price:
                conn.rollback()

                return jsonify({
                    "success": False,
                    "error": "INSUFFICIENT_BALANCE",
                    "balance_cents": balance,
                    "balance": money(balance),
                    "price_cents": price,
                    "price": money(price)
                }), 409

            new_balance = balance - price

            if DB_TYPE == "sqlite":
                cur.execute("""
                    UPDATE cards
                    SET balance_cents = ?,
                        updated_at = ?
                    WHERE card_id = ?
                """, (
                    new_balance,
                    now,
                    card_id
                ))

                cur.execute("""
                    INSERT INTO transactions
                    (kiosk_id, card_id, type,
                     amount_cents, coins, description, created_at)
                    VALUES (?, ?, 'PURCHASE', ?, 0, ?, ?)
                """, (
                    KIOSK_ID,
                    card_id,
                    -price,
                    product["name"],
                    now
                ))

            else:
                cur.execute("""
                    UPDATE cards
                    SET balance_cents = %s,
                        updated_at = %s
                    WHERE card_id = %s
                """, (
                    new_balance,
                    now,
                    card_id
                ))

                cur.execute("""
                    INSERT INTO transactions
                    (kiosk_id, card_id, type,
                     amount_cents, coins, description, created_at)
                    VALUES (%s, %s, 'PURCHASE', %s, 0, %s, %s)
                """, (
                    KIOSK_ID,
                    card_id,
                    -price,
                    product["name"],
                    now
                ))

            conn.commit()

        except Exception:
            conn.rollback()
            raise

        finally:
            close_db(conn)

    return jsonify({
        "success": True,
        "product": product["name"],
        "price_cents": price,
        "price": money(price),
        "balance_cents": new_balance,
        "balance": money(new_balance)
    })


# ------------------------------------------------------------
# ADMIN LOGIN
# ------------------------------------------------------------

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():

    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")

        if (
            username == ADMIN_USERNAME
            and password == ADMIN_PASSWORD
        ):
            session["admin"] = True
            return redirect(url_for("admin"))

        return render_template_string(
            ADMIN_LOGIN_HTML,
            error="Incorrect username or password."
        )

    return render_template_string(
        ADMIN_LOGIN_HTML,
        error=""
    )


@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))


@app.route("/admin")
def admin():
    if not session.get("admin"):
        return redirect(url_for("admin_login"))

    return render_template_string(
        ADMIN_HTML,
        kiosk_id=KIOSK_ID
    )


# ------------------------------------------------------------
# ADMIN API
# ------------------------------------------------------------

def require_admin():
    if not session.get("admin"):
        return jsonify({
            "success": False,
            "error": "UNAUTHORIZED"
        }), 401

    return None


@app.route("/api/admin/live")
def admin_live():

    auth = require_admin()

    if auth:
        return auth

    active = get_active_session()

    transactions = query_all("""
        SELECT *
        FROM transactions
        ORDER BY id DESC
        LIMIT 100
    """)

    coin_events = query_all("""
        SELECT *
        FROM coin_events
        ORDER BY id DESC
        LIMIT 100
    """)

    cards = query_all("""
        SELECT *
        FROM cards
        ORDER BY updated_at DESC
        LIMIT 100
    """)

    products = query_all("""
        SELECT *
        FROM products
        ORDER BY id ASC
    """)

    return jsonify({
        "success": True,
        "active_session": active,
        "transactions": transactions,
        "coin_events": coin_events,
        "cards": cards,
        "products": products
    })


@app.route("/api/admin/product", methods=["POST"])
def admin_product_create():

    auth = require_admin()

    if auth:
        return auth

    data = request.get_json(silent=True) or {}

    name = str(data.get("name", "")).strip()

    try:
        price_cents = int(data.get("price_cents"))
    except Exception:
        return jsonify({
            "success": False,
            "error": "INVALID_PRICE"
        }), 400

    if not name:
        return jsonify({
            "success": False,
            "error": "NAME_REQUIRED"
        }), 400

    if price_cents < 0:
        return jsonify({
            "success": False,
            "error": "INVALID_PRICE"
        }), 400

    now = utc_now()

    if DB_TYPE == "sqlite":
        product_id = execute("""
            INSERT INTO products
            (name, price_cents, active, created_at, updated_at)
            VALUES (?, ?, 1, ?, ?)
        """, (
            name,
            price_cents,
            now,
            now
        ))
    else:
        row = query_one("""
            INSERT INTO products
            (name, price_cents, active, created_at, updated_at)
            VALUES (%s, %s, 1, %s, %s)
            RETURNING id
        """, (
            name,
            price_cents,
            now,
            now
        ))

        product_id = row["id"]

    return jsonify({
        "success": True,
        "id": product_id
    })


@app.route("/api/admin/product/<int:product_id>", methods=["POST"])
def admin_product_update(product_id):

    auth = require_admin()

    if auth:
        return auth

    data = request.get_json(silent=True) or {}

    name = str(data.get("name", "")).strip()

    try:
        price_cents = int(data.get("price_cents"))
    except Exception:
        return jsonify({
            "success": False,
            "error": "INVALID_PRICE"
        }), 400

    active = 1 if data.get("active", True) else 0

    if not name:
        return jsonify({
            "success": False,
            "error": "NAME_REQUIRED"
        }), 400

    now = utc_now()

    if DB_TYPE == "sqlite":
        execute("""
            UPDATE products
            SET name = ?,
                price_cents = ?,
                active = ?,
                updated_at = ?
            WHERE id = ?
        """, (
            name,
            price_cents,
            active,
            now,
            product_id
        ))
    else:
        execute("""
            UPDATE products
            SET name = %s,
                price_cents = %s,
                active = %s,
                updated_at = %s
            WHERE id = %s
        """, (
            name,
            price_cents,
            active,
            now,
            product_id
        ))

    return jsonify({
        "success": True
    })


@app.route("/api/admin/product/<int:product_id>/delete", methods=["POST"])
def admin_product_delete(product_id):

    auth = require_admin()

    if auth:
        return auth

    now = utc_now()

    # Do not actually delete products because old
    # transaction history should remain valid.
    if DB_TYPE == "sqlite":
        execute("""
            UPDATE products
            SET active = 0,
                updated_at = ?
            WHERE id = ?
        """, (
            now,
            product_id
        ))
    else:
        execute("""
            UPDATE products
            SET active = 0,
                updated_at = %s
            WHERE id = %s
        """, (
            now,
            product_id
        ))

    return jsonify({
        "success": True
    })


# ------------------------------------------------------------
# KIOSK HTML
# ------------------------------------------------------------

KIOSK_HTML = r"""
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport"
      content="width=device-width,
               initial-scale=1.0,
               maximum-scale=1.0,
               user-scalable=no">

<title>Kiosk</title>

<style>

* {
    box-sizing: border-box;
    -webkit-tap-highlight-color: transparent;
}

html,
body {
    margin: 0;
    width: 100%;
    height: 100%;
    overflow: hidden;
    font-family:
        Inter,
        Arial,
        Helvetica,
        sans-serif;
    background: #07111f;
    color: white;
}

body {
    user-select: none;
}

#app {
    width: 100%;
    height: 100%;
    position: relative;
    overflow: hidden;
    background:
        radial-gradient(
            circle at 20% 20%,
            rgba(55, 110, 180, 0.20),
            transparent 40%
        ),
        radial-gradient(
            circle at 80% 70%,
            rgba(0, 170, 255, 0.13),
            transparent 40%
        ),
        linear-gradient(
            135deg,
            #07111f,
            #0c1a2c 50%,
            #07111f
        );
}

#backgroundVideo {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    object-fit: cover;
    opacity: 0.10;
    pointer-events: none;
}

.overlay {
    position: absolute;
    inset: 0;
    background:
        linear-gradient(
            135deg,
            rgba(4, 11, 20, 0.91),
            rgba(7, 20, 35, 0.87)
        );
}

.screen {
    position: absolute;
    inset: 0;
    display: none;
    flex-direction: column;
    padding: 40px;
    z-index: 5;
}

.screen.active {
    display: flex;
    animation: screenIn 0.25s ease;
}

@keyframes screenIn {
    from {
        opacity: 0;
        transform: scale(0.985);
    }

    to {
        opacity: 1;
        transform: scale(1);
    }
}

.top {
    display: flex;
    align-items: center;
    justify-content: space-between;
}

.brand {
    font-size: 20px;
    font-weight: 800;
    letter-spacing: 3px;
}

.language {
    border: 1px solid rgba(255,255,255,.18);
    background: rgba(255,255,255,.06);
    color: white;
    border-radius: 14px;
    padding: 13px 20px;
    font-size: 17px;
}

.center {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
}

.title {
    font-size: clamp(36px, 5vw, 70px);
    font-weight: 800;
    margin-bottom: 15px;
    text-align: center;
}

.subtitle {
    font-size: clamp(18px, 2.2vw, 28px);
    color: rgba(255,255,255,.68);
    text-align: center;
    margin-bottom: 45px;
}

.menu {
    width: min(850px, 100%);
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 20px;
}

.big-button {
    min-height: 150px;
    border: 1px solid rgba(255,255,255,.12);
    border-radius: 28px;
    background:
        linear-gradient(
            145deg,
            rgba(255,255,255,.11),
            rgba(255,255,255,.035)
        );
    color: white;
    font-size: 25px;
    font-weight: 700;
    cursor: pointer;
    transition:
        transform .15s ease,
        background .15s ease,
        border .15s ease;
}

.big-button:active {
    transform: scale(.97);
}

.big-button:hover {
    border-color: rgba(255,255,255,.30);
    background:
        linear-gradient(
            145deg,
            rgba(255,255,255,.17),
            rgba(255,255,255,.06)
        );
}

.back-button {
    min-width: 190px;
    min-height: 72px;
    padding: 15px 25px;
    border: 1px solid rgba(255,255,255,.15);
    border-radius: 18px;
    background: rgba(255,255,255,.07);
    color: white;
    font-size: 22px;
    font-weight: 700;
}

.card-area {
    width: min(760px, 100%);
    min-height: 230px;
    border-radius: 30px;
    border: 2px solid rgba(100,190,255,.30);
    background: rgba(20,45,70,.55);
    display: flex;
    align-items: center;
    justify-content: center;
    position: relative;
    overflow: hidden;
    margin-bottom: 30px;
}

.card-area::after {
    content: "";
    position: absolute;
    left: 0;
    right: 0;
    height: 3px;
    background: rgba(100,200,255,.8);
    animation: scanner 2s ease-in-out infinite;
}

@keyframes scanner {
    0%,100% {
        top: 20%;
        opacity: .25;
    }

    50% {
        top: 80%;
        opacity: 1;
    }
}

.card-text {
    font-size: 31px;
    font-weight: 700;
    z-index: 2;
}

.scan-input {
    position: absolute;
    left: -1000px;
    opacity: 0;
}

.timer {
    font-size: 19px;
    color: rgba(255,255,255,.55);
    margin-top: 20px;
}

.charge-box {
    width: min(900px, 100%);
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 20px;
    margin-bottom: 30px;
}

.info-box {
    padding: 30px;
    border-radius: 25px;
    background: rgba(255,255,255,.06);
    border: 1px solid rgba(255,255,255,.10);
}

.info-label {
    color: rgba(255,255,255,.55);
    font-size: 17px;
    margin-bottom: 8px;
}

.info-value {
    font-size: 42px;
    font-weight: 800;
}

.amount {
    font-size: clamp(55px, 8vw, 100px);
    font-weight: 900;
    margin: 20px 0;
}

.button-row {
    display: flex;
    gap: 20px;
    justify-content: center;
    flex-wrap: wrap;
}

.action {
    min-width: 260px;
    min-height: 82px;
    border-radius: 20px;
    border: 0;
    background: #ffffff;
    color: #07111f;
    font-size: 23px;
    font-weight: 800;
    padding: 20px 35px;
}

.action.secondary {
    background: rgba(255,255,255,.09);
    color: white;
    border: 1px solid rgba(255,255,255,.15);
}

.warning {
    width: min(850px, 100%);
    padding: 35px;
    border-radius: 25px;
    background: rgba(160, 100, 20, .16);
    border: 1px solid rgba(255,180,70,.35);
    text-align: center;
    margin-bottom: 30px;
}

.warning-title {
    font-size: 34px;
    font-weight: 800;
    margin-bottom: 15px;
}

.warning-text {
    font-size: 21px;
    line-height: 1.5;
    color: rgba(255,255,255,.72);
}

.balance {
    font-size: clamp(65px, 10vw, 120px);
    font-weight: 900;
    margin: 30px 0;
}

.products {
    width: min(1000px, 100%);
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 20px;
    max-height: 60vh;
    overflow-y: auto;
    padding: 10px;
}

.product {
    min-height: 170px;
    border-radius: 25px;
    border: 1px solid rgba(255,255,255,.12);
    background: rgba(255,255,255,.07);
    color: white;
    padding: 25px;
    text-align: left;
    font-size: 21px;
}

.product-name {
    font-size: 26px;
    font-weight: 800;
    margin-bottom: 20px;
}

.product-price {
    font-size: 35px;
    font-weight: 900;
}

.success-title {
    font-size: 70px;
    font-weight: 900;
    animation: pulse 1s ease-in-out infinite alternate;
}

@keyframes pulse {
    from {
        transform: scale(1);
    }

    to {
        transform: scale(1.04);
    }
}

.error {
    color: #ff8f8f;
    font-size: 21px;
    margin: 20px;
    text-align: center;
}

.footer {
    text-align: center;
    color: rgba(255,255,255,.38);
    font-size: 15px;
}

@media (max-width: 850px) {

    .screen {
        padding: 25px;
    }

    .menu {
        grid-template-columns: 1fr;
    }

    .big-button {
        min-height: 110px;
    }

    .charge-box {
        grid-template-columns: 1fr;
    }

    .products {
        grid-template-columns: 1fr;
    }
}

</style>
</head>

<body>

<div id="app">

<video id="backgroundVideo"
       autoplay
       muted
       loop
       playsinline>
</video>

<div class="overlay"></div>

<!-- MAIN -->

<section id="homeScreen" class="screen active">

    <div class="top">
        <div class="brand">SELF SERVICE</div>

        <button
            class="language"
            onclick="toggleLanguage()"
            id="languageButton">
            中文
        </button>
    </div>

    <div class="center">

        <div class="title" data-en="Welcome" data-zh="欢迎">
            Welcome
        </div>

        <div
            class="subtitle"
            data-en="Please select a service"
            data-zh="请选择服务">
            Please select a service
        </div>

        <div class="menu">

            <button
                class="big-button"
                onclick="startCharge()"
                data-en="Charge Card"
                data-zh="充值卡片">
                Charge Card
            </button>

            <button
                class="big-button"
                onclick="startBalance()"
                data-en="Check Balance"
                data-zh="查询余额">
                Check Balance
            </button>

            <button
                class="big-button"
                onclick="startStore()"
                data-en="Store"
                data-zh="商店">
                Store
            </button>

        </div>

    </div>

    <div class="footer">
        KIOSK1
    </div>

</section>


<!-- SCAN -->

<section id="scanScreen" class="screen">

    <div class="top">

        <div class="brand" id="scanBrand">
            SCAN CARD
        </div>

    </div>

    <div class="center">

        <div class="title"
             id="scanTitle"
             data-en="Please scan your card"
             data-zh="请刷卡">
            Please scan your card
        </div>

        <div class="subtitle"
             id="scanSubtitle"
             data-en="Hold your card against the reader"
             data-zh="请将卡靠近读卡器">
            Hold your card against the reader
        </div>

        <div class="card-area">

            <div
                class="card-text"
                data-en="READY FOR CARD"
                data-zh="等待刷卡">
                READY FOR CARD
            </div>

            <input
                id="scanInput"
                class="scan-input"
                autocomplete="off"
                autocorrect="off"
                spellcheck="false">

        </div>

        <div class="timer">
            <span data-en="Timeout in" data-zh="将在">Timeout in</span>
            <span id="scanTimer">15</span>
            <span data-en="seconds" data-zh="秒后超时">seconds</span>
        </div>

        <div class="button-row">

            <button
                class="back-button"
                onclick="goHome()"
                data-en="Back"
                data-zh="返回">
                Back
            </button>

        </div>

    </div>

</section>


<!-- CHARGE -->

<section id="chargeScreen" class="screen">

    <div class="top">

        <div class="brand">
            CHARGE
        </div>

    </div>

    <div class="center">

        <div class="subtitle"
             data-en="Insert coins"
             data-zh="投入硬币">
            Insert coins
        </div>

        <div class="amount" id="chargeAmount">
            $0.00
        </div>

        <div class="charge-box">

            <div class="info-box">

                <div
                    class="info-label"
                    data-en="Card"
                    data-zh="卡片">
                    Card
                </div>

                <div
                    class="info-value"
                    id="chargeCard">
                    -
                </div>

            </div>

            <div class="info-box">

                <div
                    class="info-label"
                    data-en="Coins"
                    data-zh="硬币">
                    Coins
                </div>

                <div
                    class="info-value"
                    id="chargeCoins">
                    0
                </div>

            </div>

        </div>

        <div class="subtitle"
             data-en="When finished, scan the card again to update it."
             data-zh="完成后，请再次刷卡更新卡片余额。">
            When finished, scan the card again to update it.
        </div>

        <input
            id="chargeScanInput"
            class="scan-input"
            autocomplete="off"
            autocorrect="off"
            spellcheck="false">

        <div class="button-row">

            <button
                class="action"
                onclick="focusChargeScanner()"
                data-en="Tap Card to Finish"
                data-zh="刷卡完成">
                Tap Card to Finish
            </button>

            <button
                class="action secondary"
                onclick="cancelCharge()"
                data-en="Cancel"
                data-zh="取消">
                Cancel
            </button>

        </div>

        <div class="timer">
            <span data-en="Session timeout in"
                  data-zh="会话将在">
                Session timeout in
            </span>
            <span id="chargeTimer">60</span>
            <span data-en="seconds"
                  data-zh="秒后超时">
                seconds
            </span>
        </div>

    </div>

</section>


<!-- DIFFERENT CARD -->

<section id="differentScreen" class="screen">

    <div class="top">
        <div class="brand">CARD CHANGE</div>
    </div>

    <div class="center">

        <div class="warning">

            <div
                class="warning-title"
                data-en="Different card detected"
                data-zh="检测到不同卡片">
                Different card detected
            </div>

            <div class="warning-text">

                <div>
                    <span data-en="Original card:"
                          data-zh="原卡片：">
                        Original card:
                    </span>

                    <strong id="originalCard">-</strong>
                </div>

                <br>

                <div>
                    <span data-en="Scanned card:"
                          data-zh="扫描卡片：">
                        Scanned card:
                    </span>

                    <strong id="differentCard">-</strong>
                </div>

                <br>

                <div>
                    <span data-en="Amount:"
                          data-zh="金额：">
                        Amount:
                    </span>

                    <strong id="differentAmount">$0.00</strong>
                </div>

                <br>

                <span
                    data-en="Continue to charge the scanned card?"
                    data-zh="是否继续将金额充值到扫描的卡片？">
                    Continue to charge the scanned card?
                </span>

            </div>

        </div>

        <div class="button-row">

            <button
                class="action"
                onclick="continueDifferentCard()"
                data-en="Continue"
                data-zh="继续">
                Continue
            </button>

            <button
                class="action secondary"
                onclick="returnToCharge()"
                data-en="Back"
                data-zh="返回">
                Back
            </button>

        </div>

    </div>

</section>


<!-- DIFFERENT CONFIRM -->

<section id="differentConfirmScreen" class="screen">

    <div class="center">

        <div class="title"
             data-en="Scan the new card again"
             data-zh="请再次刷新卡片">
            Scan the new card again
        </div>

        <div class="subtitle"
             data-en="This confirms that the balance should be transferred to this card."
             data-zh="再次刷卡以确认余额充值到此卡。">
            This confirms that the balance should be transferred to this card.
        </div>

        <div class="card-area">

            <div
                class="card-text"
                data-en="SCAN NEW CARD"
                data-zh="再次刷卡">
                SCAN NEW CARD
            </div>

            <input
                id="differentScanInput"
                class="scan-input"
                autocomplete="off"
                autocorrect="off"
                spellcheck="false">

        </div>

        <div class="timer">
            <span data-en="Timeout in"
                  data-zh="将在">
                Timeout in
            </span>
            <span id="differentTimer">15</span>
            <span data-en="seconds"
                  data-zh="秒后超时">
                seconds
            </span>
        </div>

        <button
            class="back-button"
            onclick="returnToCharge()"
            data-en="Back"
            data-zh="返回">
            Back
        </button>

    </div>

</section>


<!-- BALANCE -->

<section id="balanceScreen" class="screen">

    <div class="top">
        <div class="brand">BALANCE</div>
    </div>

    <div class="center">

        <div
            class="subtitle"
            data-en="Card balance"
            data-zh="卡片余额">
            Card balance
        </div>

        <div
            class="balance"
            id="balanceAmount">
            $0.00
        </div>

        <div
            class="subtitle"
            id="balanceCard">
            -
        </div>

        <div class="timer">
            <span data-en="Returning in"
                  data-zh="将在">
                Returning in
            </span>

            <span id="balanceTimer">5</span>

            <span data-en="seconds"
                  data-zh="秒后返回">
                seconds
            </span>
        </div>

    </div>

</section>


<!-- STORE -->

<section id="storeScreen" class="screen">

    <div class="top">

        <div class="brand">
            STORE
        </div>

        <div
            class="language"
            id="storeBalance">
            $0.00
        </div>

    </div>

    <div class="center">

        <div
            class="title"
            data-en="Store"
            data-zh="商店">
            Store
        </div>

        <div
            class="subtitle"
            data-en="Select a product"
            data-zh="选择商品">
            Select a product
        </div>

        <div
            class="products"
            id="products">
        </div>

        <div class="button-row">

            <button
                class="back-button"
                onclick="goHome()"
                data-en="Back"
                data-zh="返回">
                Back
            </button>

        </div>

    </div>

</section>


<!-- SUCCESS -->

<section id="successScreen" class="screen">

    <div class="center">

        <div
            class="success-title"
            data-en="SUCCESS"
            data-zh="成功">
            SUCCESS
        </div>

        <div
            class="subtitle"
            id="successText">
        </div>

    </div>

</section>


<!-- ERROR -->

<section id="errorScreen" class="screen">

    <div class="center">

        <div
            class="title"
            data-en="Something went wrong"
            data-zh="发生错误">
            Something went wrong
        </div>

        <div
            class="error"
            id="errorText">
        </div>

        <button
            class="back-button"
            onclick="goHome()"
            data-en="Back"
            data-zh="返回">
            Back
        </button>

    </div>

</section>

</div>

<script>

const KIOSK_ID = "{{ KIOSK_ID }}";

let language = "en";

let currentMode = null;
let currentCard = null;

let differentCard = null;
let differentAmount = 0;

let timerInterval = null;
let stateInterval = null;

let scanTimeout = null;


// ------------------------------------------------------------
// LANGUAGE
// ------------------------------------------------------------

function toggleLanguage() {

    language = language === "en" ? "zh" : "en";

    document.querySelectorAll("[data-en]").forEach(el => {

        el.textContent =
            language === "en"
                ? el.dataset.en
                : el.dataset.zh;

    });

    document.getElementById("languageButton").textContent =
        language === "en"
            ? "中文"
            : "English";
}


// ------------------------------------------------------------
// SCREENS
// ------------------------------------------------------------

function showScreen(id) {

    document
        .querySelectorAll(".screen")
        .forEach(screen => {
            screen.classList.remove("active");
        });

    document
        .getElementById(id)
        .classList.add("active");
}


function goHome() {

    stopTimers();

    fetch("/api/kiosk/end", {
        method: "POST"
    }).catch(() => {});

    currentMode = null;
    currentCard = null;

    showScreen("homeScreen");
}


function stopTimers() {

    if (timerInterval) {
        clearInterval(timerInterval);
        timerInterval = null;
    }

    if (stateInterval) {
        clearInterval(stateInterval);
        stateInterval = null;
    }

    if (scanTimeout) {
        clearTimeout(scanTimeout);
        scanTimeout = null;
    }
}


function startCountdown(
    elementId,
    seconds,
    callback
) {

    stopTimers();

    let remaining = seconds;

    const element =
        document.getElementById(elementId);

    element.textContent = remaining;

    timerInterval = setInterval(() => {

        remaining--;

        element.textContent = remaining;

        if (remaining <= 0) {

            clearInterval(timerInterval);
            timerInterval = null;

            callback();
        }

    }, 1000);
}


// ------------------------------------------------------------
// SCANNER
// ------------------------------------------------------------

function focusInput(id) {

    const input =
        document.getElementById(id);

    if (!input) {
        return;
    }

    input.value = "";

    setTimeout(() => {
        input.focus();
    }, 100);
}


function setupScanner(inputId, handler) {

    const input =
        document.getElementById(inputId);

    input.addEventListener("keydown", event => {

        if (event.key === "Enter") {

            event.preventDefault();

            const value =
                input.value.trim();

            input.value = "";

            if (value) {
                handler(value);
            }
        }
    });

    input.addEventListener("blur", () => {

        setTimeout(() => {

            const active =
                document.activeElement;

            if (
                !active ||
                active === document.body
            ) {
                input.focus();
            }

        }, 100);
    });
}


setupScanner(
    "scanInput",
    handleInitialScan
);

setupScanner(
    "chargeScanInput",
    handleChargeFinishScan
);

setupScanner(
    "differentScanInput",
    handleDifferentConfirmScan
);


// ------------------------------------------------------------
// CHARGE
// ------------------------------------------------------------

function startCharge() {

    currentMode = "charge";
    currentCard = null;

    showScreen("scanScreen");

    document.getElementById("scanTitle").textContent =
        language === "en"
            ? "Please scan your card"
            : "请刷卡";

    document.getElementById("scanSubtitle").textContent =
        language === "en"
            ? "Hold your card against the reader"
            : "请将卡靠近读卡器";

    startCountdown(
        "scanTimer",
        15,
        () => goHome()
    );

    focusInput("scanInput");
}


async function handleInitialScan(cardId) {

    if (currentMode === "charge") {

        try {

            const response =
                await fetch(
                    "/api/kiosk/scan",
                    {
                        method: "POST",
                        headers: {
                            "Content-Type":
                                "application/json"
                        },
                        body: JSON.stringify({
                            card_id: cardId,
                            mode: "charge"
                        })
                    }
                );

            const data =
                await response.json();

            if (!response.ok || !data.success) {
                showError(
                    data.error ||
                    "Unable to start charge."
                );
                return;
            }

            currentCard = data.card_id;

            showChargeScreen();

        } catch (error) {

            showError(
                "Unable to contact the server."
            );
        }

        return;
    }

    if (currentMode === "balance") {

        await checkBalance(cardId);

        return;
    }

    if (currentMode === "store") {

        await loadStoreForCard(cardId);

        return;
    }
}


function showChargeScreen() {

    stopTimers();

    showScreen("chargeScreen");

    document.getElementById("chargeCard").textContent =
        currentCard;

    document.getElementById("chargeAmount").textContent =
        "$0.00";

    document.getElementById("chargeCoins").textContent =
        "0";

    startChargePolling();

    startCountdown(
        "chargeTimer",
        60,
        () => cancelCharge()
    );

    focusChargeScanner();
}


function focusChargeScanner() {

    focusInput("chargeScanInput");
}


async function handleChargeFinishScan(cardId) {

    try {

        const response =
            await fetch(
                "/api/kiosk/confirm-charge",
                {
                    method: "POST",
                    headers: {
                        "Content-Type":
                            "application/json"
                    },
                    body: JSON.stringify({
                        card_id: cardId
                    })
                }
            );

        const data =
            await response.json();

        if (
            data.different_card
        ) {

            differentCard =
                data.scanned_card;

            differentAmount =
                data.amount_cents;

            document.getElementById(
                "originalCard"
            ).textContent =
                data.original_card;

            document.getElementById(
                "differentCard"
            ).textContent =
                data.scanned_card;

            document.getElementById(
                "differentAmount"
            ).textContent =
                data.amount;

            stopTimers();

            showScreen(
                "differentScreen"
            );

            return;
        }

        if (!response.ok || !data.success) {

            if (
                data.error === "NO_MONEY"
            ) {

                showError(
                    language === "en"
                        ? "No coins have been inserted."
                        : "还没有投入硬币。"
                );

                return;
            }

            showError(
                data.error ||
                "Unable to update card."
            );

            return;
        }

        showSuccess(
            language === "en"
                ? `${data.amount} added to your card.`
                : `已充值 ${data.amount}。`
        );

    } catch (error) {

        showError(
            language === "en"
                ? "Unable to contact the server."
                : "无法连接服务器。"
        );
    }
}


function startChargePolling() {

    if (stateInterval) {
        clearInterval(stateInterval);
    }

    stateInterval = setInterval(
        async () => {

            try {

                const response =
                    await fetch(
                        "/api/kiosk/state"
                    );

                const data =
                    await response.json();

                if (
                    data.active &&
                    data.session
                ) {

                    const session =
                        data.session;

                    document.getElementById(
                        "chargeAmount"
                    ).textContent =
                        session.amount;

                    document.getElementById(
                        "chargeCoins"
                    ).textContent =
                        session.coin_count;

                    document.getElementById(
                        "chargeCard"
                    ).textContent =
                        session.card_id;
                }

            } catch (error) {
                // Keep polling.
            }

        },
        500
    );
}


async function cancelCharge() {

    stopTimers();

    try {

        await fetch(
            "/api/kiosk/end",
            {
                method: "POST"
            }
        );

    } catch (error) {}

    currentMode = null;
    currentCard = null;

    showScreen("homeScreen");
}


// ------------------------------------------------------------
// DIFFERENT CARD
// ------------------------------------------------------------

function continueDifferentCard() {

    stopTimers();

    currentMode =
        "different-confirm";

    showScreen(
        "differentConfirmScreen"
    );

    startCountdown(
        "differentTimer",
        15,
        () => returnToCharge()
    );

    focusInput(
        "differentScanInput"
    );
}


async function handleDifferentConfirmScan(cardId) {

    try {

        const response =
            await fetch(
                "/api/kiosk/confirm-different-card",
                {
                    method: "POST",
                    headers: {
                        "Content-Type":
                            "application/json"
                    },
                    body: JSON.stringify({
                        card_id: cardId
                    })
                }
            );

        const data =
            await response.json();

        if (!response.ok || !data.success) {

            showError(
                data.error ||
                "Unable to update card."
            );

            return;
        }

        currentCard =
            data.card_id;

        showSuccess(
            language === "en"
                ? `${data.amount} added to the card.`
                : `已充值 ${data.amount}。`
        );

    } catch (error) {

        showError(
            language === "en"
                ? "Unable to contact the server."
                : "无法连接服务器。"
        );
    }
}


function returnToCharge() {

    stopTimers();

    currentMode = "charge";

    showChargeScreen();
}


// ------------------------------------------------------------
// BALANCE
// ------------------------------------------------------------

function startBalance() {

    currentMode = "balance";

    showScreen("scanScreen");

    document.getElementById("scanTitle").textContent =
        language === "en"
            ? "Scan your card"
            : "请刷卡";

    document.getElementById("scanSubtitle").textContent =
        language === "en"
            ? "Checking your balance"
            : "正在查询余额";

    startCountdown(
        "scanTimer",
        15,
        () => goHome()
    );

    focusInput("scanInput");
}


async function checkBalance(cardId) {

    try {

        const response =
            await fetch(
                "/api/kiosk/scan",
                {
                    method: "POST",
                    headers: {
                        "Content-Type":
                            "application/json"
                    },
                    body: JSON.stringify({
                        card_id: cardId,
                        mode: "balance"
                    })
                }
            );

        const data =
            await response.json();

        if (!response.ok || !data.success) {

            showError(
                data.error ||
                "Unable to read balance."
            );

            return;
        }

        document.getElementById(
            "balanceAmount"
        ).textContent =
            data.balance;

        document.getElementById(
            "balanceCard"
        ).textContent =
            data.card_id;

        showScreen(
            "balanceScreen"
        );

        startCountdown(
            "balanceTimer",
            5,
            () => goHome()
        );

    } catch (error) {

        showError(
            language === "en"
                ? "Unable to contact the server."
                : "无法连接服务器。"
        );
    }
}


// ------------------------------------------------------------
// STORE
// ------------------------------------------------------------

function startStore() {

    currentMode = "store";

    showScreen("scanScreen");

    document.getElementById("scanTitle").textContent =
        language === "en"
            ? "Scan your card"
            : "请刷卡";

    document.getElementById("scanSubtitle").textContent =
        language === "en"
            ? "Your card is required to use the store"
            : "使用商店需要刷卡";

    startCountdown(
        "scanTimer",
        15,
        () => goHome()
    );

    focusInput("scanInput");
}


async function loadStoreForCard(cardId) {

    try {

        const cardResponse =
            await fetch(
                "/api/kiosk/scan",
                {
                    method: "POST",
                    headers: {
                        "Content-Type":
                            "application/json"
                    },
                    body: JSON.stringify({
                        card_id: cardId,
                        mode: "store"
                    })
                }
            );

        const cardData =
            await cardResponse.json();

        if (
            !cardResponse.ok ||
            !cardData.success
        ) {

            showError(
                cardData.error ||
                "Unable to read card."
            );

            return;
        }

        currentCard =
            cardData.card_id;

        document.getElementById(
            "storeBalance"
        ).textContent =
            cardData.balance;

        const productsResponse =
            await fetch(
                "/api/store/products"
            );

        const productsData =
            await productsResponse.json();

        const container =
            document.getElementById(
                "products"
            );

        container.innerHTML = "";

        productsData.products.forEach(
            product => {

                const button =
                    document.createElement(
                        "button"
                    );

                button.className =
                    "product";

                button.innerHTML = `
                    <div class="product-name">
                        ${escapeHtml(product.name)}
                    </div>

                    <div class="product-price">
                        ${product.price}
                    </div>
                `;

                button.onclick = () =>
                    buyProduct(
                        product.id
                    );

                container.appendChild(
                    button
                );
            }
        );

        stopTimers();

        showScreen(
            "storeScreen"
        );

    } catch (error) {

        showError(
            language === "en"
                ? "Unable to contact the server."
                : "无法连接服务器。"
        );
    }
}


async function buyProduct(productId) {

    try {

        const response =
            await fetch(
                "/api/store/buy",
                {
                    method: "POST",
                    headers: {
                        "Content-Type":
                            "application/json"
                    },
                    body: JSON.stringify({
                        card_id: currentCard,
                        product_id: productId
                    })
                }
            );

        const data =
            await response.json();

        if (!response.ok || !data.success) {

            if (
                data.error ===
                "INSUFFICIENT_BALANCE"
            ) {

                showError(
                    language === "en"
                        ? `Insufficient balance. Current balance: ${data.balance}`
                        : `余额不足。目前余额：${data.balance}`
                );

                return;
            }

            showError(
                data.error ||
                "Purchase failed."
            );

            return;
        }

        document.getElementById(
            "storeBalance"
        ).textContent =
            data.balance;

        showSuccess(
            language === "en"
                ? `${data.product} purchased.`
                : `已购买 ${data.product}。`
        );

    } catch (error) {

        showError(
            language === "en"
                ? "Unable to contact the server."
                : "无法连接服务器。"
        );
    }
}


// ------------------------------------------------------------
// SUCCESS / ERROR
// ------------------------------------------------------------

function showSuccess(text) {

    stopTimers();

    document.getElementById(
        "successText"
    ).textContent = text;

    showScreen(
        "successScreen"
    );

    setTimeout(
        () => goHome(),
        3000
    );
}


function showError(text) {

    stopTimers();

    document.getElementById(
        "errorText"
    ).textContent = text;

    showScreen(
        "errorScreen"
    );
}


// ------------------------------------------------------------
// SECURITY
// ------------------------------------------------------------

function escapeHtml(value) {

    const div =
        document.createElement("div");

    div.textContent = value;

    return div.innerHTML;
}


// ------------------------------------------------------------
// BACKGROUND
// ------------------------------------------------------------

const videos = [
    "https://cdn.coverr.co/videos/coverr-a-person-using-a-credit-card-1576/1080p.mp4",
    "https://cdn.coverr.co/videos/coverr-paying-with-a-credit-card-1577/1080p.mp4"
];

let videoIndex = 0;

function startVideo() {

    const video =
        document.getElementById(
            "backgroundVideo"
        );

    if (!videos.length) {
        return;
    }

    video.src =
        videos[videoIndex];

    video.play().catch(() => {});

    video.addEventListener(
        "ended",
        () => {

            videoIndex =
                (videoIndex + 1) %
                videos.length;

            video.src =
                videos[videoIndex];

            video.play().catch(() => {});

        }
    );
}

startVideo();

</script>

</body>
</html>
"""


# ------------------------------------------------------------
# ADMIN LOGIN HTML
# ------------------------------------------------------------

ADMIN_LOGIN_HTML = r"""
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Admin Login</title>

<style>

body {
    margin: 0;
    min-height: 100vh;
    background: #07111f;
    color: white;
    font-family: Arial, sans-serif;
    display: flex;
    align-items: center;
    justify-content: center;
}

.box {
    width: 380px;
    max-width: 90%;
    background: #101d2d;
    padding: 40px;
    border-radius: 20px;
}

h1 {
    margin-top: 0;
}

input {
    width: 100%;
    padding: 16px;
    margin: 8px 0;
    box-sizing: border-box;
    border-radius: 10px;
    border: 1px solid #33465c;
    background: #07111f;
    color: white;
    font-size: 17px;
}

button {
    width: 100%;
    padding: 16px;
    margin-top: 15px;
    border: 0;
    border-radius: 10px;
    font-size: 17px;
    font-weight: bold;
}

.error {
    color: #ff8f8f;
    margin-bottom: 15px;
}

</style>
</head>

<body>

<div class="box">

<h1>Admin</h1>

{% if error %}
<div class="error">{{ error }}</div>
{% endif %}

<form method="POST">

<input
    name="username"
    placeholder="Username"
    autocomplete="username">

<input
    name="password"
    type="password"
    placeholder="Password"
    autocomplete="current-password">

<button type="submit">
    Login
</button>

</form>

</div>

</body>
</html>
"""


# ------------------------------------------------------------
# ADMIN HTML
# ------------------------------------------------------------

ADMIN_HTML = r"""
<!DOCTYPE html>
<html>

<head>

<meta charset="UTF-8">

<meta name="viewport"
      content="width=device-width, initial-scale=1">

<title>Kiosk Admin</title>

<style>

body {
    margin: 0;
    background: #07111f;
    color: white;
    font-family: Arial, sans-serif;
}

header {
    padding: 25px;
    background: #0d1b2d;
    display: flex;
    justify-content: space-between;
    align-items: center;
}

main {
    padding: 25px;
    max-width: 1500px;
    margin: auto;
}

.card {
    background: #101d2d;
    border: 1px solid #23364c;
    border-radius: 16px;
    padding: 20px;
    margin-bottom: 20px;
}

h1,
h2 {
    margin-top: 0;
}

table {
    width: 100%;
    border-collapse: collapse;
}

th,
td {
    padding: 12px;
    border-bottom: 1px solid #24384d;
    text-align: left;
}

input {
    padding: 12px;
    border-radius: 8px;
    border: 1px solid #33465c;
    background: #07111f;
    color: white;
}

button {
    padding: 11px 16px;
    border: 0;
    border-radius: 8px;
    cursor: pointer;
}

.add {
    background: white;
    color: #07111f;
    font-weight: bold;
}

.delete {
    background: #572222;
    color: white;
}

.status {
    font-size: 18px;
}

</style>

</head>

<body>

<header>

<div>
    <strong>KIOSK ADMIN</strong>
</div>

<div>
    <a
       href="/admin/logout"
       style="color:white;">
       Logout
    </a>
</div>

</header>

<main>

<div class="card">

<h2>Active Session</h2>

<div id="active">
Loading...
</div>

</div>


<div class="card">

<h2>Products</h2>

<div>

<input
    id="newName"
    placeholder="Product name">

<input
    id="newPrice"
    placeholder="Price"
    type="number"
    step="0.01">

<button
    class="add"
    onclick="addProduct()">
    Add Product
</button>

</div>

<br>

<table>

<thead>

<tr>
<th>ID</th>
<th>Name</th>
<th>Price</th>
<th>Active</th>
<th>Action</th>
</tr>

</thead>

<tbody id="products">
</tbody>

</table>

</div>


<div class="card">

<h2>Cards</h2>

<table>

<thead>

<tr>
<th>Card</th>
<th>Balance</th>
<th>Updated</th>
</tr>

</thead>

<tbody id="cards">
</tbody>

</table>

</div>


<div class="card">

<h2>Transactions</h2>

<table>

<thead>

<tr>
<th>Time</th>
<th>Card</th>
<th>Type</th>
<th>Amount</th>
<th>Coins</th>
<th>Description</th>
</tr>

</thead>

<tbody id="transactions">
</tbody>

</table>

</div>


<div class="card">

<h2>Coin Events</h2>

<table>

<thead>

<tr>
<th>Time</th>
<th>Kiosk</th>
<th>Card</th>
<th>Accepted</th>
<th>Coins</th>
<th>Amount</th>
<th>Reason</th>
</tr>

</thead>

<tbody id="coins">
</tbody>

</table>

</div>

</main>


<script>

function money(cents) {
    return "$" +
        (Number(cents) / 100)
        .toFixed(2);
}


function escapeHtml(value) {

    const div =
        document.createElement("div");

    div.textContent = value;

    return div.innerHTML;
}


async function load() {

    const response =
        await fetch(
            "/api/admin/live"
        );

    if (!response.ok) {
        return;
    }

    const data =
        await response.json();

    const active =
        document.getElementById(
            "active"
        );

    if (data.active_session) {

        const s =
            data.active_session;

        active.innerHTML = `
            <div class="status">
                ACTIVE
            </div>
            <p>
                Card:
                <strong>
                    ${escapeHtml(s.card_id)}
                </strong>
            </p>
            <p>
                Coins:
                <strong>
                    ${s.coin_count}
                </strong>
            </p>
            <p>
                Amount:
                <strong>
                    ${money(s.amount_cents)}
                </strong>
            </p>
        `;

    } else {

        active.textContent =
            "No active session.";
    }


    const products =
        document.getElementById(
            "products"
        );

    products.innerHTML = "";

    data.products.forEach(p => {

        products.innerHTML += `
            <tr>
                <td>${p.id}</td>

                <td>
                    <input
                        id="name-${p.id}"
                        value="${escapeHtml(p.name)}">
                </td>

                <td>
                    <input
                        id="price-${p.id}"
                        type="number"
                        step="0.01"
                        value="${(
                            p.price_cents / 100
                        ).toFixed(2)}">
                </td>

                <td>
                    <input
                        id="active-${p.id}"
                        type="checkbox"
                        ${p.active ? "checked" : ""}>
                </td>

                <td>
                    <button
                        onclick="saveProduct(${p.id})">
                        Save
                    </button>

                    <button
                        class="delete"
                        onclick="deleteProduct(${p.id})">
                        Deactivate
                    </button>
                </td>
            </tr>
        `;
    });


    const cards =
        document.getElementById(
            "cards"
        );

    cards.innerHTML = "";

    data.cards.forEach(c => {

        cards.innerHTML += `
            <tr>
                <td>
                    ${escapeHtml(c.card_id)}
                </td>

                <td>
                    ${money(c.balance_cents)}
                </td>

                <td>
                    ${escapeHtml(c.updated_at)}
                </td>
            </tr>
        `;
    });


    const transactions =
        document.getElementById(
            "transactions"
        );

    transactions.innerHTML = "";

    data.transactions.forEach(t => {

        transactions.innerHTML += `
            <tr>
                <td>${escapeHtml(t.created_at)}</td>
                <td>${escapeHtml(t.card_id)}</td>
                <td>${escapeHtml(t.type)}</td>
                <td>${money(t.amount_cents)}</td>
                <td>${t.coins}</td>
                <td>${escapeHtml(t.description)}</td>
            </tr>
        `;
    });


    const coins =
        document.getElementById(
            "coins"
        );

    coins.innerHTML = "";

    data.coin_events.forEach(c => {

        coins.innerHTML += `
            <tr>
                <td>${escapeHtml(c.created_at)}</td>
                <td>${escapeHtml(c.kiosk_id)}</td>
                <td>${escapeHtml(c.card_id || "-")}</td>
                <td>${c.accepted ? "YES" : "NO"}</td>
                <td>${c.coins}</td>
                <td>${money(c.amount_cents)}</td>
                <td>${escapeHtml(c.reason)}</td>
            </tr>
        `;
    });
}


async function addProduct() {

    const name =
        document.getElementById(
            "newName"
        ).value.trim();

    const price =
        Number(
            document.getElementById(
                "newPrice"
            ).value
        );

    if (!name || price < 0) {
        return;
    }

    await fetch(
        "/api/admin/product",
        {
            method: "POST",
            headers: {
                "Content-Type":
                    "application/json"
            },
            body: JSON.stringify({
                name: name,
                price_cents:
                    Math.round(
                        price * 100
                    )
            })
        }
    );

    document.getElementById(
        "newName"
    ).value = "";

    document.getElementById(
        "newPrice"
    ).value = "";

    load();
}


async function saveProduct(id) {

    const name =
        document.getElementById(
            `name-${id}`
        ).value.trim();

    const price =
        Number(
            document.getElementById(
                `price-${id}`
            ).value
        );

    const active =
        document.getElementById(
            `active-${id}`
        ).checked;

    await fetch(
        `/api/admin/product/${id}`,
        {
            method: "POST",
            headers: {
                "Content-Type":
                    "application/json"
            },
            body: JSON.stringify({
                name: name,
                price_cents:
                    Math.round(
                        price * 100
                    ),
                active: active
            })
        }
    );

    load();
}


async function deleteProduct(id) {

    await fetch(
        `/api/admin/product/${id}/delete`,
        {
            method: "POST"
        }
    );

    load();
}


load();

setInterval(
    load,
    2000
);

</script>

</body>

</html>
"""


# ------------------------------------------------------------
# RUN
# ------------------------------------------------------------

if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            "5000"
        )
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )
