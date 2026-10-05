import os
import sqlite3
import threading
import secrets
from datetime import datetime, timezone

from flask import Flask, jsonify, request, render_template_string, session, redirect, url_for

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret-key")
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024

KIOSK_ID = "KIOSK1"
COIN_VALUE_CENTS = 100
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin"
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

if DATABASE_URL:
    import psycopg2
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    DB_TYPE = "postgres"
else:
    DB_TYPE = "sqlite"
    DB_FILE = os.environ.get("DB_FILE", "kiosk.db")

db_lock = threading.RLock()


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def get_db():
    if DB_TYPE == "postgres":
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = False
        return conn
    conn = sqlite3.connect(DB_FILE, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def close_db(conn):
    try:
        conn.close()
    except Exception:
        pass


def execute(sql, params=()):
    with db_lock:
        conn = get_db()
        try:
            cur = conn.cursor()
            cur.execute(sql, params)
            result = None
            if DB_TYPE == "postgres":
                try:
                    row = cur.fetchone()
                    if row:
                        result = row[0]
                except Exception:
                    result = None
            else:
                result = cur.lastrowid
            conn.commit()
            return result
        except Exception:
            conn.rollback()
            raise
        finally:
            close_db(conn)


def query_one(sql, params=()):
    with db_lock:
        conn = get_db()
        try:
            cur = conn.cursor()
            cur.execute(sql, params)
            row = cur.fetchone()
            if not row:
                return None
            if DB_TYPE == "postgres":
                columns = [c[0] for c in cur.description]
                return dict(zip(columns, row))
            if isinstance(row, sqlite3.Row):
                return dict(row)
            columns = [c[0] for c in cur.description]
            return dict(zip(columns, row))
        finally:
            close_db(conn)


def query_all(sql, params=()):
    with db_lock:
        conn = get_db()
        try:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()
            if not rows:
                return []
            if DB_TYPE == "postgres":
                columns = [c[0] for c in cur.description]
                return [dict(zip(columns, row)) for row in rows]
            if isinstance(rows[0], sqlite3.Row):
                return [dict(row) for row in rows]
            columns = [c[0] for c in cur.description]
            return [dict(zip(columns, row)) for row in rows]
        finally:
            close_db(conn)


def column_exists(table, column):
    if DB_TYPE == "sqlite":
        with db_lock:
            conn = get_db()
            try:
                rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
                return any(row[1] == column for row in rows)
            finally:
                close_db(conn)
    row = query_one(
        "SELECT 1 FROM information_schema.columns WHERE table_name=%s AND column_name=%s",
        (table, column),
    )
    return bool(row)


def add_column_if_missing(table, column, definition):
    if column_exists(table, column):
        return
    execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db():
    with db_lock:
        conn = get_db()
        try:
            cur = conn.cursor()
            if DB_TYPE == "postgres":
                statements = [
                    """CREATE TABLE IF NOT EXISTS cards (card_id TEXT PRIMARY KEY,balance_cents INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)""",
                    """CREATE TABLE IF NOT EXISTS sessions (id SERIAL PRIMARY KEY,kiosk_id TEXT NOT NULL,card_id TEXT NOT NULL,mode TEXT NOT NULL,status TEXT NOT NULL,coin_count INTEGER NOT NULL DEFAULT 0,amount_cents INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)""",
                    """CREATE TABLE IF NOT EXISTS transactions (id SERIAL PRIMARY KEY,kiosk_id TEXT NOT NULL,card_id TEXT NOT NULL,type TEXT NOT NULL,amount_cents INTEGER NOT NULL DEFAULT 0,coins INTEGER NOT NULL DEFAULT 0,description TEXT NOT NULL,created_at TEXT NOT NULL)""",
                    """CREATE TABLE IF NOT EXISTS coin_events (id SERIAL PRIMARY KEY,kiosk_id TEXT NOT NULL,card_id TEXT,accepted INTEGER NOT NULL,coins INTEGER NOT NULL,amount_cents INTEGER NOT NULL,reason TEXT NOT NULL,created_at TEXT NOT NULL)""",
                    """CREATE TABLE IF NOT EXISTS products (id SERIAL PRIMARY KEY,name TEXT NOT NULL,name_zh TEXT NOT NULL DEFAULT '',description TEXT NOT NULL DEFAULT '',description_zh TEXT NOT NULL DEFAULT '',image_data TEXT NOT NULL DEFAULT '',price_cents INTEGER NOT NULL,active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)""",
                    """CREATE TABLE IF NOT EXISTS receipts (id SERIAL PRIMARY KEY,receipt_number TEXT UNIQUE NOT NULL,kiosk_id TEXT NOT NULL,card_id TEXT NOT NULL,type TEXT NOT NULL,description TEXT NOT NULL,amount_cents INTEGER NOT NULL,balance_after_cents INTEGER NOT NULL,created_at TEXT NOT NULL)""",
                ]
            else:
                statements = [
                    """CREATE TABLE IF NOT EXISTS cards (card_id TEXT PRIMARY KEY,balance_cents INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)""",
                    """CREATE TABLE IF NOT EXISTS sessions (id INTEGER PRIMARY KEY AUTOINCREMENT,kiosk_id TEXT NOT NULL,card_id TEXT NOT NULL,mode TEXT NOT NULL,status TEXT NOT NULL,coin_count INTEGER NOT NULL DEFAULT 0,amount_cents INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)""",
                    """CREATE TABLE IF NOT EXISTS transactions (id INTEGER PRIMARY KEY AUTOINCREMENT,kiosk_id TEXT NOT NULL,card_id TEXT NOT NULL,type TEXT NOT NULL,amount_cents INTEGER NOT NULL DEFAULT 0,coins INTEGER NOT NULL DEFAULT 0,description TEXT NOT NULL,created_at TEXT NOT NULL)""",
                    """CREATE TABLE IF NOT EXISTS coin_events (id INTEGER PRIMARY KEY AUTOINCREMENT,kiosk_id TEXT NOT NULL,card_id TEXT,accepted INTEGER NOT NULL,coins INTEGER NOT NULL,amount_cents INTEGER NOT NULL,reason TEXT NOT NULL,created_at TEXT NOT NULL)""",
                    """CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,name_zh TEXT NOT NULL DEFAULT '',description TEXT NOT NULL DEFAULT '',description_zh TEXT NOT NULL DEFAULT '',image_data TEXT NOT NULL DEFAULT '',price_cents INTEGER NOT NULL,active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)""",
                    """CREATE TABLE IF NOT EXISTS receipts (id INTEGER PRIMARY KEY AUTOINCREMENT,receipt_number TEXT UNIQUE NOT NULL,kiosk_id TEXT NOT NULL,card_id TEXT NOT NULL,type TEXT NOT NULL,description TEXT NOT NULL,amount_cents INTEGER NOT NULL,balance_after_cents INTEGER NOT NULL,created_at TEXT NOT NULL)""",
                ]
            for statement in statements:
                cur.execute(statement)
            conn.commit()
        finally:
            close_db(conn)

    # Migrate older installations created by the previous version.
    add_column_if_missing("products", "name_zh", "TEXT NOT NULL DEFAULT ''")
    add_column_if_missing("products", "description", "TEXT NOT NULL DEFAULT ''")
    add_column_if_missing("products", "description_zh", "TEXT NOT NULL DEFAULT ''")
    add_column_if_missing("products", "image_data", "TEXT NOT NULL DEFAULT ''")

    if query_one("SELECT COUNT(*) AS count FROM products")["count"] == 0:
        now = utc_now()
        defaults = [("Service 1", "服务 1", "", "", 200), ("Service 2", "服务 2", "", "", 500), ("Service 3", "服务 3", "", "", 1000)]
        for name, name_zh, desc, desc_zh, price in defaults:
            if DB_TYPE == "sqlite":
                execute("INSERT INTO products (name,name_zh,description,description_zh,image_data,price_cents,active,created_at,updated_at) VALUES (?,?,?,?,?, ?,1,?,?)", (name, name_zh, desc, desc_zh, "", price, now, now))
            else:
                execute("INSERT INTO products (name,name_zh,description,description_zh,image_data,price_cents,active,created_at,updated_at) VALUES (%s,%s,%s,%s,%s,%s,1,%s,%s)", (name, name_zh, desc, desc_zh, "", price, now, now))


init_db()


def money(cents):
    return f"${cents / 100:.2f}"


def create_machine_log(event_type, message, card_id=None, amount_cents=0):
    now = utc_now()
    card = card_id or "-"
    if DB_TYPE == "sqlite":
        execute("INSERT INTO transactions (kiosk_id,card_id,type,amount_cents,coins,description,created_at) VALUES (?,?,?,?,0,?,?)", (KIOSK_ID, card, "SYSTEM_" + event_type, amount_cents, message, now))
    else:
        execute("INSERT INTO transactions (kiosk_id,card_id,type,amount_cents,coins,description,created_at) VALUES (%s,%s,%s,%s,0,%s,%s)", (KIOSK_ID, card, "SYSTEM_" + event_type, amount_cents, message, now))


def ensure_card(card_id):
    card_id = str(card_id).strip()
    if not card_id:
        return None
    if DB_TYPE == "sqlite":
        card = query_one("SELECT * FROM cards WHERE card_id=?", (card_id,))
    else:
        card = query_one("SELECT * FROM cards WHERE card_id=%s", (card_id,))
    if card:
        return card
    now = utc_now()
    if DB_TYPE == "sqlite":
        execute("INSERT INTO cards (card_id,balance_cents,created_at,updated_at) VALUES (?,0,?,?)", (card_id, now, now))
    else:
        execute("INSERT INTO cards (card_id,balance_cents,created_at,updated_at) VALUES (%s,0,%s,%s)", (card_id, now, now))
    create_machine_log("CARD_CREATED", "New card created", card_id)
    return ensure_card(card_id)


def get_active_session():
    if DB_TYPE == "sqlite":
        return query_one("SELECT * FROM sessions WHERE kiosk_id=? AND status='active' ORDER BY id DESC LIMIT 1", (KIOSK_ID,))
    return query_one("SELECT * FROM sessions WHERE kiosk_id=%s AND status='active' ORDER BY id DESC LIMIT 1", (KIOSK_ID,))


def close_active_session():
    now = utc_now()
    if DB_TYPE == "sqlite":
        execute("UPDATE sessions SET status='cancelled',updated_at=? WHERE kiosk_id=? AND status='active'", (now, KIOSK_ID))
    else:
        execute("UPDATE sessions SET status='cancelled',updated_at=%s WHERE kiosk_id=%s AND status='active'", (now, KIOSK_ID))


def create_receipt(card_id, receipt_type, description, amount_cents, balance_after_cents):
    now = utc_now()
    receipt_number = "R-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + "-" + secrets.token_hex(3).upper()
    if DB_TYPE == "sqlite":
        execute("INSERT INTO receipts (receipt_number,kiosk_id,card_id,type,description,amount_cents,balance_after_cents,created_at) VALUES (?,?,?,?,?,?,?,?)", (receipt_number, KIOSK_ID, card_id, receipt_type, description, amount_cents, balance_after_cents, now))
    else:
        execute("INSERT INTO receipts (receipt_number,kiosk_id,card_id,type,description,amount_cents,balance_after_cents,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", (receipt_number, KIOSK_ID, card_id, receipt_type, description, amount_cents, balance_after_cents, now))
    return {"receipt_number": receipt_number, "created_at": now, "card_id": card_id, "type": receipt_type, "description": description, "amount_cents": amount_cents, "amount": money(amount_cents), "balance_after_cents": balance_after_cents, "balance_after": money(balance_after_cents)}


def record_coin_event(kiosk_id, card_id, accepted, coins, amount_cents, reason):
    now = utc_now()
    if DB_TYPE == "sqlite":
        execute("INSERT INTO coin_events (kiosk_id,card_id,accepted,coins,amount_cents,reason,created_at) VALUES (?,?,?,?,?,?,?)", (kiosk_id, card_id, accepted, coins, amount_cents, reason, now))
    else:
        execute("INSERT INTO coin_events (kiosk_id,card_id,accepted,coins,amount_cents,reason,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)", (kiosk_id, card_id, accepted, coins, amount_cents, reason, now))


@app.route("/")
def index():
    return redirect(url_for("kiosk"))


@app.route("/health")
def health():
    try:
        result = query_one("SELECT 1 AS test")
        return jsonify({"success": bool(result), "database": DB_TYPE, "kiosk_id": KIOSK_ID})
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "database": DB_TYPE}), 500


@app.route("/kiosk")
def kiosk():
    return render_template_string(KIOSK_HTML, KIOSK_ID=KIOSK_ID)


@app.route("/api/kiosk/scan", methods=["POST"])
def kiosk_scan():
    data = request.get_json(silent=True) or {}
    card_id = str(data.get("card_id", "")).strip()
    mode = str(data.get("mode", "")).strip().lower()
    if not card_id:
        return jsonify(success=False, error="CARD_REQUIRED"), 400
    if mode not in ("charge", "balance", "store"):
        return jsonify(success=False, error="INVALID_MODE"), 400
    card = ensure_card(card_id)
    if not card:
        return jsonify(success=False, error="CARD_ERROR"), 500
    create_machine_log("CARD_SCAN", f"Card scanned in {mode} mode", card_id)
    if mode == "charge":
        close_active_session()
        now = utc_now()
        if DB_TYPE == "sqlite":
            execute("INSERT INTO sessions (kiosk_id,card_id,mode,status,coin_count,amount_cents,created_at,updated_at) VALUES (?,?,?,'active',0,0,?,?)", (KIOSK_ID, card_id, "charge", now, now))
        else:
            execute("INSERT INTO sessions (kiosk_id,card_id,mode,status,coin_count,amount_cents,created_at,updated_at) VALUES (%s,%s,%s,'active',0,0,%s,%s)", (KIOSK_ID, card_id, "charge", now, now))
        create_machine_log("SESSION_STARTED", "Charge session started", card_id)
    return jsonify(success=True, card_id=card_id, balance_cents=card["balance_cents"], balance=money(card["balance_cents"]), mode=mode)


@app.route("/api/kiosk/state")
def kiosk_state():
    active = get_active_session()
    if not active:
        return jsonify(success=True, active=False)
    return jsonify(success=True, active=True, session={"id": active["id"], "card_id": active["card_id"], "coin_count": active["coin_count"], "amount_cents": active["amount_cents"], "amount": money(active["amount_cents"])})


@app.route("/api/kiosk/end", methods=["POST"])
def kiosk_end():
    active = get_active_session()
    if active:
        create_machine_log("SESSION_ENDED", "Charge session cancelled", active["card_id"], active["amount_cents"])
    close_active_session()
    return jsonify(success=True)


@app.route("/api/coin", methods=["POST"])
def coin():
    data = request.get_json(silent=True) or {}
    kiosk_id = str(data.get("kiosk_id", "")).strip()
    try:
        coins = max(1, int(data.get("coins", 1)))
    except Exception:
        coins = 1
    amount = coins * COIN_VALUE_CENTS
    if kiosk_id != KIOSK_ID:
        record_coin_event(kiosk_id or "UNKNOWN", None, 0, coins, amount, "INVALID_KIOSK_ID")
        create_machine_log("COIN_REJECTED", "Invalid kiosk ID")
        return jsonify(success=False, error="INVALID_KIOSK_ID"), 403
    active = get_active_session()
    if not active:
        record_coin_event(KIOSK_ID, None, 0, coins, amount, "NO_ACTIVE_CARD_SESSION")
        create_machine_log("COIN_REJECTED", "Coin received with no active card session", None, amount)
        return jsonify(success=False, error="NO_ACTIVE_CARD_SESSION", message="No active charge session."), 409
    new_count = active["coin_count"] + coins
    new_amount = active["amount_cents"] + amount
    now = utc_now()
    with db_lock:
        conn = get_db()
        try:
            cur = conn.cursor()
            if DB_TYPE == "sqlite":
                cur.execute("UPDATE sessions SET coin_count=?,amount_cents=?,updated_at=? WHERE id=? AND kiosk_id=? AND status='active'", (new_count, new_amount, now, active["id"], KIOSK_ID))
                cur.execute("INSERT INTO coin_events (kiosk_id,card_id,accepted,coins,amount_cents,reason,created_at) VALUES (?,?,1,?,?,?,?)", (KIOSK_ID, active["card_id"], coins, amount, "ACCEPTED", now))
            else:
                cur.execute("UPDATE sessions SET coin_count=%s,amount_cents=%s,updated_at=%s WHERE id=%s AND kiosk_id=%s AND status='active'", (new_count, new_amount, now, active["id"], KIOSK_ID))
                cur.execute("INSERT INTO coin_events (kiosk_id,card_id,accepted,coins,amount_cents,reason,created_at) VALUES (%s,%s,1,%s,%s,%s,%s)", (KIOSK_ID, active["card_id"], coins, amount, "ACCEPTED", now))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            close_db(conn)
    create_machine_log("COIN", "Coin accepted", active["card_id"], amount)
    return jsonify(success=True, card_id=active["card_id"], coins=new_count, amount_cents=new_amount, amount=money(new_amount))


@app.route("/api/kiosk/confirm-charge", methods=["POST"])
def confirm_charge():
    data = request.get_json(silent=True) or {}
    scanned_card = str(data.get("card_id", "")).strip()
    if not scanned_card:
        return jsonify(success=False, error="CARD_REQUIRED"), 400
    active = get_active_session()
    if not active:
        return jsonify(success=False, error="NO_ACTIVE_CARD_SESSION"), 409
    amount = active["amount_cents"]
    if amount <= 0:
        return jsonify(success=False, error="NO_MONEY"), 400
    original_card = active["card_id"]
    if scanned_card != original_card:
        ensure_card(scanned_card)
        create_machine_log("DIFFERENT_CARD", "Different card detected during charge confirmation", scanned_card, amount)
        return jsonify(success=False, different_card=True, original_card=original_card, scanned_card=scanned_card, amount_cents=amount, amount=money(amount)), 409
    return finalize_charge(active, scanned_card)


def finalize_charge(active, card_id):
    amount = active["amount_cents"]
    coins = active["coin_count"]
    now = utc_now()
    with db_lock:
        conn = get_db()
        try:
            cur = conn.cursor()
            if DB_TYPE == "sqlite":
                cur.execute("SELECT balance_cents FROM cards WHERE card_id=?", (card_id,))
            else:
                cur.execute("SELECT balance_cents FROM cards WHERE card_id=%s FOR UPDATE", (card_id,))
            row = cur.fetchone()
            if not row:
                conn.rollback()
                return jsonify(success=False, error="CARD_NOT_FOUND"), 404
            balance = row[0]
            new_balance = balance + amount
            if DB_TYPE == "sqlite":
                cur.execute("UPDATE cards SET balance_cents=?,updated_at=? WHERE card_id=?", (new_balance, now, card_id))
                cur.execute("INSERT INTO transactions (kiosk_id,card_id,type,amount_cents,coins,description,created_at) VALUES (?,?, 'CHARGE',?,?,?,?)", (KIOSK_ID, card_id, amount, coins, "Card charged", now))
                cur.execute("UPDATE sessions SET status='completed',updated_at=? WHERE id=?", (now, active["id"]))
            else:
                cur.execute("UPDATE cards SET balance_cents=%s,updated_at=%s WHERE card_id=%s", (new_balance, now, card_id))
                cur.execute("INSERT INTO transactions (kiosk_id,card_id,type,amount_cents,coins,description,created_at) VALUES (%s,%s,'CHARGE',%s,%s,%s,%s)", (KIOSK_ID, card_id, amount, coins, "Card charged", now))
                cur.execute("UPDATE sessions SET status='completed',updated_at=%s WHERE id=%s", (now, active["id"]))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            close_db(conn)
    receipt = create_receipt(card_id, "CHARGE", "Card charge", amount, new_balance)
    create_machine_log("CHARGE", "Card charged successfully", card_id, amount)
    return jsonify(success=True, card_id=card_id, amount_cents=amount, amount=money(amount), balance_cents=new_balance, balance=money(new_balance), receipt=receipt)


@app.route("/api/kiosk/confirm-different-card", methods=["POST"])
def confirm_different_card():
    data = request.get_json(silent=True) or {}
    scanned_card = str(data.get("card_id", "")).strip()
    if not scanned_card:
        return jsonify(success=False, error="CARD_REQUIRED"), 400
    active = get_active_session()
    if not active:
        return jsonify(success=False, error="NO_ACTIVE_CARD_SESSION"), 409
    ensure_card(scanned_card)
    return finalize_charge(active, scanned_card)


@app.route("/api/store/products")
def store_products():
    products = query_all("SELECT * FROM products WHERE active=1 ORDER BY id ASC")
    return jsonify(success=True, products=[{"id": p["id"], "name": p["name"], "name_zh": p.get("name_zh", ""), "description": p.get("description", ""), "description_zh": p.get("description_zh", ""), "image_data": p.get("image_data", ""), "price_cents": p["price_cents"], "price": money(p["price_cents"])} for p in products])


def buy_cart_for_card(card_id, items):
    card_id = str(card_id).strip()
    if not card_id:
        return None, (jsonify(success=False, error="CARD_REQUIRED"), 400)
    if not isinstance(items, list) or not items:
        return None, (jsonify(success=False, error="CART_EMPTY"), 400)

    normalized = []
    for item in items:
        try:
            product_id = int(item.get("product_id"))
            quantity = int(item.get("quantity", 1))
        except Exception:
            return None, (jsonify(success=False, error="INVALID_CART"), 400)
        if product_id < 1 or quantity < 1 or quantity > 99:
            return None, (jsonify(success=False, error="INVALID_CART"), 400)
        normalized.append((product_id, quantity))

    # Merge duplicate product lines.
    merged = {}
    for product_id, quantity in normalized:
        merged[product_id] = merged.get(product_id, 0) + quantity

    with db_lock:
        conn = get_db()
        try:
            cur = conn.cursor()
            if DB_TYPE == "sqlite":
                cur.execute("SELECT balance_cents FROM cards WHERE card_id=?", (card_id,))
            else:
                cur.execute("SELECT balance_cents FROM cards WHERE card_id=%s FOR UPDATE", (card_id,))
            row = cur.fetchone()
            if not row:
                conn.rollback()
                return None, (jsonify(success=False, error="CARD_NOT_FOUND"), 404)
            balance = int(row[0])

            line_items = []
            total = 0
            descriptions = []
            for product_id, quantity in merged.items():
                if DB_TYPE == "sqlite":
                    cur.execute("SELECT * FROM products WHERE id=? AND active=1", (product_id,))
                else:
                    cur.execute("SELECT * FROM products WHERE id=%s AND active=1", (product_id,))
                row = cur.fetchone()
                if not row:
                    conn.rollback()
                    return None, (jsonify(success=False, error="PRODUCT_NOT_FOUND"), 404)
                if DB_TYPE == "postgres":
                    cols = [c[0] for c in cur.description]
                    product = dict(zip(cols, row))
                else:
                    product = dict(row)
                line_total = int(product["price_cents"]) * quantity
                total += line_total
                line_items.append({"id": product["id"], "name": product["name"], "name_zh": product.get("name_zh", ""), "quantity": quantity, "unit_price_cents": int(product["price_cents"]), "line_total_cents": line_total})
                descriptions.append(f'{product["name"]} x{quantity}')

            if balance < total:
                conn.rollback()
                return None, (jsonify(success=False, error="INSUFFICIENT_BALANCE", balance_cents=balance, balance=money(balance), total_cents=total, total=money(total)), 409)

            new_balance = balance - total
            now = utc_now()
            description = ", ".join(descriptions)
            if DB_TYPE == "sqlite":
                cur.execute("UPDATE cards SET balance_cents=?,updated_at=? WHERE card_id=?", (new_balance, now, card_id))
                cur.execute("INSERT INTO transactions (kiosk_id,card_id,type,amount_cents,coins,description,created_at) VALUES (?,?, 'PURCHASE',?,0,?,?)", (KIOSK_ID, card_id, -total, description, now))
            else:
                cur.execute("UPDATE cards SET balance_cents=%s,updated_at=%s WHERE card_id=%s", (new_balance, now, card_id))
                cur.execute("INSERT INTO transactions (kiosk_id,card_id,type,amount_cents,coins,description,created_at) VALUES (%s,%s,'PURCHASE',%s,0,%s,%s)", (KIOSK_ID, card_id, -total, description, now))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            close_db(conn)

    receipt = create_receipt(card_id, "PURCHASE", description, -total, new_balance)
    create_machine_log("PURCHASE", "Purchase: " + description, card_id, -total)
    return {"card_id": card_id, "items": line_items, "total_cents": total, "total": money(total), "balance_cents": new_balance, "balance": money(new_balance), "receipt": receipt}, None


@app.route("/api/store/buy-cart", methods=["POST"])
def store_buy_cart():
    data = request.get_json(silent=True) or {}
    result, error = buy_cart_for_card(data.get("card_id", ""), data.get("items", []))
    if error:
        return error
    return jsonify(success=True, **result)


# Backwards-compatible single-product endpoint.
@app.route("/api/store/buy", methods=["POST"])
def store_buy():
    data = request.get_json(silent=True) or {}
    result, error = buy_cart_for_card(data.get("card_id", ""), [{"product_id": data.get("product_id"), "quantity": 1}])
    if error:
        return error
    return jsonify(success=True, **result)


# ============================================================
# ADMIN
# ============================================================

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session["admin"] = True
            return redirect(url_for("admin"))
        return render_template_string(ADMIN_LOGIN_HTML, error="Incorrect username or password.")
    return render_template_string(ADMIN_LOGIN_HTML, error="")


@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))


@app.route("/admin")
def admin():
    if not session.get("admin"):
        return redirect(url_for("admin_login"))
    return render_template_string(ADMIN_HTML, kiosk_id=KIOSK_ID)


def require_admin():
    if not session.get("admin"):
        return jsonify(success=False, error="UNAUTHORIZED"), 401
    return None


@app.route("/api/admin/live")
def admin_live():
    auth = require_admin()
    if auth:
        return auth
    return jsonify(
        success=True,
        active_session=get_active_session(),
        transactions=query_all("SELECT * FROM transactions ORDER BY id DESC LIMIT 200"),
        coin_events=query_all("SELECT * FROM coin_events ORDER BY id DESC LIMIT 200"),
        cards=query_all("SELECT * FROM cards ORDER BY updated_at DESC LIMIT 200"),
        products=query_all("SELECT * FROM products ORDER BY id ASC"),
        receipts=query_all("SELECT * FROM receipts ORDER BY id DESC LIMIT 100"),
    )


def product_payload(data, existing=None):
    name = str(data.get("name", "")).strip()
    name_zh = str(data.get("name_zh", "")).strip()
    description = str(data.get("description", "")).strip()
    description_zh = str(data.get("description_zh", "")).strip()
    try:
        price_cents = int(data.get("price_cents"))
    except Exception:
        raise ValueError("INVALID_PRICE")
    if not name:
        raise ValueError("NAME_REQUIRED")
    if not name_zh:
        name_zh = name
    if price_cents < 0:
        raise ValueError("INVALID_PRICE")
    image_data = data.get("image_data")
    if image_data is None:
        image_data = existing.get("image_data", "") if existing else ""
    image_data = str(image_data or "")
    if len(image_data) > 9_000_000:
        raise ValueError("IMAGE_TOO_LARGE")
    if data.get("remove_image"):
        image_data = ""
    return name, name_zh, description, description_zh, price_cents, image_data


@app.route("/api/admin/product", methods=["POST"])
def admin_product_create():
    auth = require_admin()
    if auth:
        return auth
    data = request.get_json(silent=True) or {}
    try:
        name, name_zh, description, description_zh, price_cents, image_data = product_payload(data)
    except ValueError as e:
        return jsonify(success=False, error=str(e)), 400
    now = utc_now()
    if DB_TYPE == "sqlite":
        product_id = execute("INSERT INTO products (name,name_zh,description,description_zh,image_data,price_cents,active,created_at,updated_at) VALUES (?,?,?,?,?,?,1,?,?)", (name, name_zh, description, description_zh, image_data, price_cents, now, now))
    else:
        product_id = execute("INSERT INTO products (name,name_zh,description,description_zh,image_data,price_cents,active,created_at,updated_at) VALUES (%s,%s,%s,%s,%s,%s,1,%s,%s) RETURNING id", (name, name_zh, description, description_zh, image_data, price_cents, now, now))
    return jsonify(success=True, id=product_id)


@app.route("/api/admin/product/<int:product_id>", methods=["POST"])
def admin_product_update(product_id):
    auth = require_admin()
    if auth:
        return auth
    existing = query_one("SELECT * FROM products WHERE id=?" if DB_TYPE == "sqlite" else "SELECT * FROM products WHERE id=%s", (product_id,))
    if not existing:
        return jsonify(success=False, error="PRODUCT_NOT_FOUND"), 404
    data = request.get_json(silent=True) or {}
    try:
        name, name_zh, description, description_zh, price_cents, image_data = product_payload(data, existing)
    except ValueError as e:
        return jsonify(success=False, error=str(e)), 400
    active = 1 if data.get("active", True) else 0
    now = utc_now()
    if DB_TYPE == "sqlite":
        execute("UPDATE products SET name=?,name_zh=?,description=?,description_zh=?,image_data=?,price_cents=?,active=?,updated_at=? WHERE id=?", (name, name_zh, description, description_zh, image_data, price_cents, active, now, product_id))
    else:
        execute("UPDATE products SET name=%s,name_zh=%s,description=%s,description_zh=%s,image_data=%s,price_cents=%s,active=%s,updated_at=%s WHERE id=%s", (name, name_zh, description, description_zh, image_data, price_cents, active, now, product_id))
    return jsonify(success=True)


@app.route("/api/admin/product/<int:product_id>/delete", methods=["POST"])
def admin_product_delete(product_id):
    auth = require_admin()
    if auth:
        return auth
    now = utc_now()
    if DB_TYPE == "sqlite":
        execute("UPDATE products SET active=0,updated_at=? WHERE id=?", (now, product_id))
    else:
        execute("UPDATE products SET active=0,updated_at=%s WHERE id=%s", (now, product_id))
    return jsonify(success=True)


# ============================================================
# BACK CONSOLE
# ============================================================

@app.route("/back")
def backend_console():
    return render_template_string(BACK_HTML, kiosk_id=KIOSK_ID)


@app.route("/api/back/live")
def backend_live():
    return jsonify(success=True, active_session=get_active_session(), transactions=query_all("SELECT * FROM transactions ORDER BY id DESC LIMIT 150"), coin_events=query_all("SELECT * FROM coin_events ORDER BY id DESC LIMIT 150"), receipts=query_all("SELECT * FROM receipts ORDER BY id DESC LIMIT 75"))


# ============================================================
# KIOSK HTML
# ============================================================

KIOSK_HTML = r"""
<!doctype html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>Self Service</title>
<style>
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}html,body{margin:0;width:100%;height:100%;overflow:hidden;font-family:Inter,Arial,sans-serif;background:#07111f;color:#fff}body{user-select:none}button{font-family:inherit;color:inherit;cursor:pointer}#app{width:100%;height:100%;position:relative;overflow:hidden;background:radial-gradient(circle at 20% 20%,rgba(55,110,180,.20),transparent 40%),radial-gradient(circle at 80% 70%,rgba(0,170,255,.13),transparent 40%),linear-gradient(135deg,#07111f,#0c1a2c 50%,#07111f)}#backgroundVideo{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;opacity:.08;pointer-events:none}.overlay{position:absolute;inset:0;background:linear-gradient(135deg,rgba(4,11,20,.93),rgba(7,20,35,.89));pointer-events:none}.screen{position:absolute;inset:0;display:none;flex-direction:column;padding:34px;z-index:5}.screen.active{display:flex;animation:screenIn .22s ease}@keyframes screenIn{from{opacity:0;transform:scale(.985)}to{opacity:1;transform:scale(1)}}.top{display:flex;align-items:center;justify-content:space-between}.brand{font-size:20px;font-weight:800;letter-spacing:3px}.global-language{position:fixed;right:28px;top:26px;z-index:100;border:1px solid rgba(255,255,255,.18);background:rgba(255,255,255,.06);color:#fff;border-radius:14px;padding:13px 20px;font-size:17px}.center{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center}.title{font-size:clamp(36px,5vw,70px);font-weight:800;margin-bottom:15px;text-align:center}.subtitle{font-size:clamp(18px,2.2vw,28px);color:rgba(255,255,255,.68);text-align:center;margin-bottom:32px}.menu{width:min(900px,100%);display:grid;grid-template-columns:repeat(3,1fr);gap:20px}.big-button{min-height:150px;border:1px solid rgba(255,255,255,.12);border-radius:28px;background:linear-gradient(145deg,rgba(255,255,255,.11),rgba(255,255,255,.035));color:#fff;font-size:25px;font-weight:700}.big-button:active,.action:active,.back-button:active,.cart-button:active,.product:active{transform:scale(.98)}.back-button{min-width:190px;min-height:72px;padding:15px 25px;border:1px solid rgba(255,255,255,.15);border-radius:18px;background:rgba(255,255,255,.07);font-size:22px;font-weight:700}.card-area{width:min(760px,100%);min-height:230px;border-radius:30px;border:2px solid rgba(100,190,255,.30);background:rgba(20,45,70,.55);display:flex;align-items:center;justify-content:center;position:relative;overflow:hidden;margin-bottom:24px}.card-area:after{content:"";position:absolute;left:0;right:0;height:3px;background:rgba(100,200,255,.8);animation:scanner 2s ease-in-out infinite}@keyframes scanner{0%,100%{top:20%;opacity:.25}50%{top:80%;opacity:1}}.card-text{font-size:31px;font-weight:700;z-index:2}.scan-input{position:absolute;left:-1000px;opacity:0}.timer{font-size:19px;color:rgba(255,255,255,.55);margin-top:15px}.charge-box{width:min(900px,100%);display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:24px}.info-box{padding:24px;border-radius:25px;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.10)}.info-label{color:rgba(255,255,255,.55);font-size:17px;margin-bottom:8px}.info-value{font-size:36px;font-weight:800;overflow:hidden;text-overflow:ellipsis}.amount{font-size:clamp(55px,8vw,100px);font-weight:900;margin:15px 0}.button-row{display:flex;gap:18px;justify-content:center;flex-wrap:wrap}.action{min-width:260px;min-height:82px;border-radius:20px;border:0;background:#fff;color:#07111f;font-size:23px;font-weight:800;padding:20px 35px}.action.secondary{background:rgba(255,255,255,.09);color:#fff;border:1px solid rgba(255,255,255,.15)}.warning{width:min(850px,100%);padding:35px;border-radius:25px;background:rgba(160,100,20,.16);border:1px solid rgba(255,180,70,.35);text-align:center;margin-bottom:30px}.warning-title{font-size:34px;font-weight:800;margin-bottom:15px}.warning-text{font-size:21px;line-height:1.5;color:rgba(255,255,255,.72)}.balance{font-size:clamp(65px,10vw,120px);font-weight:900;margin:20px 0}.products{width:min(1100px,100%);display:grid;grid-template-columns:repeat(3,1fr);gap:18px;max-height:61vh;overflow-y:auto;padding:8px 8px 110px}.product{min-height:235px;border-radius:25px;border:1px solid rgba(255,255,255,.12);background:rgba(255,255,255,.07);padding:0;text-align:left;overflow:hidden;display:flex;flex-direction:column}.product-image{height:135px;width:100%;object-fit:cover;background:linear-gradient(135deg,#13263c,#0b1725)}.product-content{padding:18px}.product-name{font-size:24px;font-weight:800;margin-bottom:8px}.product-description{font-size:16px;color:rgba(255,255,255,.62);min-height:42px;line-height:1.35;margin-bottom:12px}.product-bottom{display:flex;align-items:end;justify-content:space-between;gap:15px}.product-price{font-size:31px;font-weight:900}.add-label{font-size:14px;color:rgba(255,255,255,.55)}.store-header{width:min(1100px,100%);display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}.store-balance{padding:13px 18px;border-radius:14px;background:rgba(255,255,255,.06);font-size:18px}.cart-button{position:fixed;left:24px;right:24px;bottom:22px;z-index:30;min-height:70px;border:0;border-radius:20px;background:#fff;color:#07111f;font-size:22px;font-weight:900;box-shadow:0 12px 40px rgba(0,0,0,.25)}.cart-list{width:min(900px,100%);max-height:55vh;overflow-y:auto;padding:5px}.cart-row{display:grid;grid-template-columns:1fr auto auto;align-items:center;gap:15px;padding:18px;border-bottom:1px solid rgba(255,255,255,.08)}.cart-name{font-size:20px;font-weight:700}.cart-price{font-size:18px;color:rgba(255,255,255,.72)}.qty-controls{display:flex;align-items:center;gap:10px}.qty-button{width:44px;height:44px;border-radius:12px;border:1px solid rgba(255,255,255,.15);background:rgba(255,255,255,.08);font-size:25px}.qty{min-width:28px;text-align:center;font-size:20px;font-weight:800}.cart-total{font-size:38px;font-weight:900;margin:16px 0 24px} .loading-box{width:min(700px,100%);padding:45px 35px;border-radius:28px;background:rgba(255,255,255,.055);border:1px solid rgba(255,255,255,.12);text-align:center}.loading-ring{width:72px;height:72px;border:5px solid rgba(255,255,255,.14);border-top-color:#fff;border-radius:50%;margin:0 auto 28px;animation:spin .8s linear infinite}.loading-text{font-size:30px;font-weight:800}.loading-sub{margin-top:12px;font-size:18px;color:rgba(255,255,255,.58)}@keyframes spin{to{transform:rotate(360deg)}}.receipt-box{width:min(650px,100%);padding:30px;border-radius:25px;background:rgba(255,255,255,.07);border:1px solid rgba(255,255,255,.14)}.receipt-line{display:flex;justify-content:space-between;gap:25px;padding:16px 0;border-bottom:1px solid rgba(255,255,255,.08);font-size:19px}.receipt-line:last-child{border-bottom:0}.receipt-line span{color:rgba(255,255,255,.55)}.receipt-line strong{text-align:right;word-break:break-word}.error{color:#ff9b9b;font-size:21px;margin:20px;text-align:center}.footer{text-align:center;color:rgba(255,255,255,.38);font-size:15px}.empty{padding:50px;text-align:center;color:rgba(255,255,255,.55);font-size:22px}@media(max-width:900px){.screen{padding:24px}.menu{grid-template-columns:1fr}.big-button{min-height:105px}.charge-box{grid-template-columns:1fr}.products{grid-template-columns:1fr 1fr}.cart-row{grid-template-columns:1fr auto}.cart-price{display:none}}@media(max-width:600px){.global-language{right:18px;top:18px;padding:10px 14px}.products{grid-template-columns:1fr}.product{min-height:220px}.cart-button{left:16px;right:16px;bottom:16px}}
</style>
</head>
<body>
<div id="app">
<video id="backgroundVideo" autoplay muted loop playsinline></video>
<div class="overlay"></div>
<button id="languageButton" class="global-language" onclick="toggleLanguage()">中文</button>

<section id="homeScreen" class="screen active"><div class="top"><div class="brand">SELF SERVICE</div></div><div class="center"><div class="title" data-en="Welcome" data-zh="欢迎">Welcome</div><div class="subtitle" data-en="Please select a service" data-zh="请选择服务">Please select a service</div><div class="menu"><button class="big-button" onclick="startCharge()" data-en="Charge Card" data-zh="充值卡片">Charge Card</button><button class="big-button" onclick="startBalance()" data-en="Check Balance" data-zh="查询余额">Check Balance</button><button class="big-button" onclick="startStore()" data-en="Store" data-zh="商店">Store</button></div></div><div class="footer">KIOSK1</div></section>

<section id="scanScreen" class="screen"><div class="top"><div class="brand">SCAN CARD</div></div><div class="center"><div class="title" id="scanTitle">Please scan your card</div><div class="subtitle" id="scanSubtitle">Hold your card against the reader</div><div class="card-area"><div class="card-text" id="scanPrompt">READY FOR CARD</div><input id="scanInput" class="scan-input" autocomplete="off"></div><div class="timer"><span data-en="Timeout in" data-zh="将在">Timeout in</span> <span id="scanTimer">15</span> <span data-en="seconds" data-zh="秒后超时">seconds</span></div><button class="back-button" onclick="goHome()" data-en="Back" data-zh="返回">Back</button></div></section>

<section id="chargeScreen" class="screen"><div class="top"><div class="brand">CHARGE</div></div><div class="center"><div class="subtitle" data-en="Insert coins" data-zh="投入硬币">Insert coins</div><div class="amount" id="chargeAmount">$0.00</div><div class="charge-box"><div class="info-box"><div class="info-label" data-en="Card" data-zh="卡片">Card</div><div class="info-value" id="chargeCard">-</div></div><div class="info-box"><div class="info-label" data-en="Coins" data-zh="硬币">Coins</div><div class="info-value" id="chargeCoins">0</div></div></div><div class="subtitle" data-en="When finished, scan the card again to update it." data-zh="完成后，请再次刷卡更新卡片余额。">When finished, scan the card again to update it.</div><input id="chargeScanInput" class="scan-input" autocomplete="off"><div class="button-row"><button class="action" onclick="focusChargeScanner()" data-en="Tap Card to Finish" data-zh="刷卡完成">Tap Card to Finish</button><button class="action secondary" onclick="cancelCharge()" data-en="Cancel" data-zh="取消">Cancel</button></div><div class="timer"><span data-en="Session timeout in" data-zh="会话将在">Session timeout in</span> <span id="chargeTimer">60</span> <span data-en="seconds" data-zh="秒后超时">seconds</span></div></div></section>

<section id="differentScreen" class="screen"><div class="top"><div class="brand">CARD CHANGE</div></div><div class="center"><div class="warning"><div class="warning-title" data-en="Different card detected" data-zh="检测到不同卡片">Different card detected</div><div class="warning-text"><div><span data-en="Original card:" data-zh="原卡片：">Original card:</span> <strong id="originalCard">-</strong></div><br><div><span data-en="Scanned card:" data-zh="扫描卡片：">Scanned card:</span> <strong id="differentCard">-</strong></div><br><div><span data-en="Amount:" data-zh="金额：">Amount:</span> <strong id="differentAmount">$0.00</strong></div><br><span data-en="Continue to charge the scanned card?" data-zh="是否继续将金额充值到扫描的卡片？">Continue to charge the scanned card?</span></div></div><div class="button-row"><button class="action" onclick="continueDifferentCard()" data-en="Continue" data-zh="继续">Continue</button><button class="action secondary" onclick="returnToCharge()" data-en="Back" data-zh="返回">Back</button></div></div></section>

<section id="differentConfirmScreen" class="screen"><div class="center"><div class="title" data-en="Scan the new card again" data-zh="请再次刷卡">Scan the new card again</div><div class="subtitle" data-en="This confirms that the balance should be transferred to this card." data-zh="再次刷卡以确认余额充值到此卡。">This confirms that the balance should be transferred to this card.</div><div class="card-area"><div class="card-text" data-en="SCAN NEW CARD" data-zh="再次刷卡">SCAN NEW CARD</div><input id="differentScanInput" class="scan-input" autocomplete="off"></div><div class="timer"><span data-en="Timeout in" data-zh="将在">Timeout in</span> <span id="differentTimer">15</span> <span data-en="seconds" data-zh="秒后超时">seconds</span></div><button class="back-button" onclick="returnToCharge()" data-en="Back" data-zh="返回">Back</button></div></section>

<section id="balanceScreen" class="screen"><div class="top"><div class="brand">BALANCE</div></div><div class="center"><div class="subtitle" data-en="Card balance" data-zh="卡片余额">Card balance</div><div class="balance" id="balanceAmount">$0.00</div><div class="subtitle" id="balanceCard">-</div><div class="timer"><span data-en="Returning in" data-zh="将在">Returning in</span> <span id="balanceTimer">5</span> <span data-en="seconds" data-zh="秒后返回">seconds</span></div></div></section>

<section id="storeScreen" class="screen"><div class="top"><div class="brand">STORE</div><div class="store-balance" id="storeBalance">$0.00</div></div><div class="center"><div class="store-header"><div class="title" style="margin:0" data-en="Store" data-zh="商店">Store</div></div><div class="subtitle" data-en="Select products to add to your cart" data-zh="选择商品加入购物车">Select products to add to your cart</div><div class="products" id="products"></div></div><button class="cart-button" id="cartButton" onclick="openCart()">View Cart</button></section>

<section id="cartScreen" class="screen"><div class="top"><div class="brand">CART</div></div><div class="center"><div class="title" data-en="Your Cart" data-zh="购物车">Your Cart</div><div class="cart-list" id="cartList"></div><div class="cart-total" id="cartTotal">$0.00</div><input id="cartPayInput" class="scan-input" autocomplete="off"><div class="button-row"><button class="back-button" onclick="returnToStore()" data-en="Back to Store" data-zh="返回商店">Back to Store</button><button class="action" onclick="startCartPaymentScan()" data-en="Tap Card to Pay" data-zh="刷卡支付">Tap Card to Pay</button></div></div></section>



<section id="loadingScreen" class="screen"><div class="center"><div class="loading-box"><div class="loading-ring"></div><div class="loading-text" id="loadingText">Please wait</div><div class="loading-sub" id="loadingSub">Processing</div></div></div></section>

<section id="receiptScreen" class="screen"><div class="center"><div class="title" data-en="Receipt" data-zh="收据">Receipt</div><div class="receipt-box"><div class="receipt-line"><span data-en="Receipt" data-zh="收据号">Receipt</span><strong id="receiptNumber">-</strong></div><div class="receipt-line"><span data-en="Card" data-zh="卡片">Card</span><strong id="receiptCard">-</strong></div><div class="receipt-line"><span data-en="Transaction" data-zh="交易">Transaction</span><strong id="receiptDescription">-</strong></div><div class="receipt-line"><span data-en="Amount" data-zh="金额">Amount</span><strong id="receiptAmount">$0.00</strong></div><div class="receipt-line"><span data-en="Balance" data-zh="余额">Balance</span><strong id="receiptBalance">$0.00</strong></div><div class="receipt-line"><span data-en="Time" data-zh="时间">Time</span><strong id="receiptTime">-</strong></div></div><div class="timer"><span data-en="Returning in" data-zh="将在">Returning in</span> <span id="receiptTimer">5</span> <span data-en="seconds" data-zh="秒后返回">seconds</span></div></div></section>

<section id="errorScreen" class="screen"><div class="center"><div class="title" data-en="Something went wrong" data-zh="发生错误">Something went wrong</div><div class="error" id="errorText"></div><button class="back-button" onclick="goHome()" data-en="Back" data-zh="返回">Back</button></div></section>
</div>
<script>
const KIOSK_ID={{ KIOSK_ID|tojson }};
let language='en', currentMode=null, currentCard=null, differentCard=null;
let timeoutInterval=null, chargePollingInterval=null;
let cart=[]; let productsCache=[];
let loadingToken=0;

function showLoading(enText,zhText,enSub,zhSub,duration,callback){
    stopAllTimers();
    const token=++loadingToken;
    document.getElementById('loadingText').textContent=t(enText,zhText);
    document.getElementById('loadingSub').textContent=t(enSub,zhSub);
    showScreen('loadingScreen');
    setTimeout(()=>{
        if(token!==loadingToken)return;
        callback();
    },duration);
}

function cancelLoading(){loadingToken++;stopAllTimers()}

function t(en,zh){return language==='en'?en:zh}
function toggleLanguage(){language=language==='en'?'zh':'en';document.querySelectorAll('[data-en]').forEach(e=>e.textContent=language==='en'?e.dataset.en:e.dataset.zh);document.getElementById('languageButton').textContent=language==='en'?'中文':'English';if(document.getElementById('storeScreen').classList.contains('active'))renderStore();if(document.getElementById('cartScreen').classList.contains('active'))renderCart();}
function showScreen(id){document.querySelectorAll('.screen').forEach(s=>s.classList.remove('active'));document.getElementById(id).classList.add('active')}
function stopTimeout(){if(timeoutInterval){clearInterval(timeoutInterval);timeoutInterval=null}}
function stopPolling(){if(chargePollingInterval){clearInterval(chargePollingInterval);chargePollingInterval=null}}
function stopAllTimers(){stopTimeout();stopPolling()}
function startCountdown(id,seconds,callback){stopTimeout();let n=seconds;const el=document.getElementById(id);el.textContent=n;timeoutInterval=setInterval(()=>{n--;el.textContent=n;if(n<=0){stopTimeout();callback()}},1000)}
function goHome(){loadingToken++;stopAllTimers();fetch('/api/kiosk/end',{method:'POST'}).catch(()=>{});currentMode=null;currentCard=null;differentCard=null;cart=[];showScreen('homeScreen')}

let audioContext=null;
function playTapSound(){try{audioContext=audioContext||new(window.AudioContext||window.webkitAudioContext)();if(audioContext.state==='suspended')audioContext.resume();const o=audioContext.createOscillator();const g=audioContext.createGain();o.type='sine';o.frequency.setValueAtTime(880,audioContext.currentTime);o.frequency.exponentialRampToValueAtTime(1180,audioContext.currentTime+0.07);g.gain.setValueAtTime(.0001,audioContext.currentTime);g.gain.exponentialRampToValueAtTime(.16,audioContext.currentTime+0.015);g.gain.exponentialRampToValueAtTime(.0001,audioContext.currentTime+0.13);o.connect(g);g.connect(audioContext.destination);o.start();o.stop(audioContext.currentTime+0.14)}catch(e){}}

function focusInput(id){const input=document.getElementById(id);if(!input)return;input.value='';setTimeout(()=>input.focus(),100)}
function setupScanner(id,handler){const input=document.getElementById(id);input.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();const value=input.value.trim();input.value='';if(value){playTapSound();handler(value)}}});input.addEventListener('blur',()=>setTimeout(()=>{if(document.querySelector('.screen.active')&&document.activeElement===document.body)input.focus()},100))}


function startCharge(){currentMode='charge';currentCard=null;showScan('Please scan your card','请刷卡','Hold your card against the reader','请将卡靠近读卡器');startCountdown('scanTimer',15,goHome);focusInput('scanInput')}
function startBalance(){currentMode='balance';showScan('Scan your card','请刷卡','Checking your balance','正在查询余额');startCountdown('scanTimer',15,goHome);focusInput('scanInput')}
function startStore(){currentMode='store';showScan('Scan your card','请刷卡','Your card is required to use the store','使用商店需要刷卡');startCountdown('scanTimer',15,goHome);focusInput('scanInput')}
function showScan(enTitle,zhTitle,enSub,zhSub){showScreen('scanScreen');document.getElementById('scanTitle').textContent=t(enTitle,zhTitle);document.getElementById('scanSubtitle').textContent=t(enSub,zhSub);document.getElementById('scanPrompt').textContent=t('READY FOR CARD','等待刷卡')}

async function handleInitialScan(cardId){try{const response=await fetch('/api/kiosk/scan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({card_id:cardId,mode:currentMode})});const data=await response.json();if(!response.ok||!data.success){showError(data.error||t('Unable to scan card.','无法读取卡片。'));return}currentCard=data.card_id;if(currentMode==='charge'){showChargeScreen()}else if(currentMode==='balance'){showLoading('Checking balance','正在查询余额','Please wait','请稍候',500,()=>showBalance(data))}else if(currentMode==='store'){await loadStore(data)}}catch(e){console.error(e);showError(t('Unable to contact the server.','无法连接服务器。'))}}

function showChargeScreen(){stopTimeout();stopPolling();showScreen('chargeScreen');document.getElementById('chargeCard').textContent=currentCard;document.getElementById('chargeAmount').textContent='$0.00';document.getElementById('chargeCoins').textContent='0';startChargePolling();startCountdown('chargeTimer',60,cancelCharge);focusChargeScanner()}
function focusChargeScanner(){focusInput('chargeScanInput')}
async function handleChargeFinishScan(cardId){try{const response=await fetch('/api/kiosk/confirm-charge',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({card_id:cardId})});const data=await response.json();if(data.different_card){differentCard=data.scanned_card;document.getElementById('originalCard').textContent=data.original_card;document.getElementById('differentCard').textContent=data.scanned_card;document.getElementById('differentAmount').textContent=data.amount;stopAllTimers();showScreen('differentScreen');return}if(!response.ok||!data.success){showError(data.error||t('Unable to update card.','无法更新卡片。'));return}currentCard=data.card_id;showLoading('Updating card','正在更新卡片','Please wait','请稍候',2000,()=>showReceipt(data.receipt))}catch(e){console.error(e);showError(t('Unable to contact the server.','无法连接服务器。'))}}
function startChargePolling(){stopPolling();async function update(){try{const r=await fetch('/api/kiosk/state?t='+Date.now(),{cache:'no-store'});const d=await r.json();if(d.active&&d.session){document.getElementById('chargeAmount').textContent=d.session.amount;document.getElementById('chargeCoins').textContent=d.session.coin_count;document.getElementById('chargeCard').textContent=d.session.card_id}}catch(e){console.log('Charge polling error',e)}}update();chargePollingInterval=setInterval(update,250)}
async function cancelCharge(){stopAllTimers();await fetch('/api/kiosk/end',{method:'POST'}).catch(()=>{});currentMode=null;currentCard=null;showScreen('homeScreen')}

function continueDifferentCard(){stopAllTimers();currentMode='different-confirm';showScreen('differentConfirmScreen');startCountdown('differentTimer',15,returnToCharge);focusInput('differentScanInput')}
async function handleDifferentConfirmScan(cardId){try{const response=await fetch('/api/kiosk/confirm-different-card',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({card_id:cardId})});const data=await response.json();if(!response.ok||!data.success){showError(data.error||t('Unable to update card.','无法更新卡片。'));return}currentCard=data.card_id;showLoading('Updating card','正在更新卡片','Please wait','请稍候',2000,()=>showReceipt(data.receipt))}catch(e){console.error(e);showError(t('Unable to contact the server.','无法连接服务器。'))}}
function returnToCharge(){stopAllTimers();currentMode='charge';showChargeScreen()}

function showBalance(data){stopAllTimers();document.getElementById('balanceAmount').textContent=data.balance;document.getElementById('balanceCard').textContent=data.card_id;showScreen('balanceScreen');startCountdown('balanceTimer',5,goHome)}

async function loadStore(data){try{const response=await fetch('/api/store/products?t='+Date.now(),{cache:'no-store'});const result=await response.json();if(!response.ok||!result.success)throw new Error('products');productsCache=result.products||[];cart=[];document.getElementById('storeBalance').textContent=data.balance;renderStore();showScreen('storeScreen')}catch(e){console.error(e);showError(t('Unable to load products.','无法加载商品。'))}}
function renderStore(){const box=document.getElementById('products');box.innerHTML='';if(!productsCache.length){box.innerHTML=`<div class="empty">${t('No products available.','暂无商品。')}</div>`;updateCartButton();return}productsCache.forEach(p=>{const name=language==='en'?p.name:(p.name_zh||p.name);const desc=language==='en'?p.description:(p.description_zh||p.description);const img=p.image_data||'';const button=document.createElement('button');button.className='product';button.innerHTML=`${img?`<img class="product-image" src="${escapeHtml(img)}">`:`<div class="product-image"></div>`}<div class="product-content"><div class="product-name">${escapeHtml(name)}</div><div class="product-description">${escapeHtml(desc||'')}</div><div class="product-bottom"><div class="product-price">${p.price}</div><div class="add-label">${t('ADD','加入')}</div></div></div>`;button.onclick=()=>addToCart(p.id);box.appendChild(button)});updateCartButton()}
function addToCart(id){const item=cart.find(x=>x.product_id===id);if(item)item.quantity++;else cart.push({product_id:id,quantity:1});updateCartButton();playTapSound()}
function updateCartButton(){const count=cart.reduce((sum,x)=>sum+x.quantity,0);document.getElementById('cartButton').textContent=count?t('View Cart ('+count+')','查看购物车 ('+count+')'):t('View Cart','查看购物车')}
async function openCart(){playTapSound();showLoading('Opening cart','正在打开购物车','Please wait','请稍候',500,()=>{renderCart();showScreen('cartScreen')})}
function returnToStore(){renderStore();showScreen('storeScreen')}
function cartProduct(id){return productsCache.find(p=>p.id===id)}
function renderCart(){const box=document.getElementById('cartList');box.innerHTML='';if(!cart.length){box.innerHTML=`<div class="empty">${t('Your cart is empty.','购物车为空。')}</div>`;document.getElementById('cartTotal').textContent='$0.00';return}let total=0;cart.forEach(item=>{const p=cartProduct(item.product_id);if(!p)return;total+=p.price_cents*item.quantity;const row=document.createElement('div');row.className='cart-row';row.innerHTML=`<div><div class="cart-name">${escapeHtml(language==='en'?p.name:(p.name_zh||p.name))}</div><div class="cart-price">${p.price} × ${item.quantity}</div></div><div class="qty-controls"><button class="qty-button" onclick="changeQty(${p.id},-1)">−</button><div class="qty">${item.quantity}</div><button class="qty-button" onclick="changeQty(${p.id},1)">+</button></div><strong>${money(totalFor(item))}</strong>`;box.appendChild(row)});document.getElementById('cartTotal').textContent=money(total)}
function totalFor(item){const p=cartProduct(item.product_id);return p?p.price_cents*item.quantity:0}
function changeQty(id,delta){const item=cart.find(x=>x.product_id===id);if(!item)return;item.quantity+=delta;if(item.quantity<=0)cart=cart.filter(x=>x.product_id!==id);renderCart();updateCartButton();playTapSound()}
function startCartPaymentScan(){if(!cart.length){showError(t('Your cart is empty.','购物车为空。'));return}showScreen('scanScreen');document.getElementById('scanTitle').textContent=t('Tap your card to pay','请刷卡支付');document.getElementById('scanSubtitle').textContent=t('Use the same card used for the store','请使用进入商店时刷的同一张卡');document.getElementById('scanPrompt').textContent=t('READY FOR CARD','等待刷卡');startCountdown('scanTimer',30,()=>{showScreen('cartScreen');renderCart()});focusInput('cartPayInput');document.getElementById('cartPayInput').onkeydown=async function(e){if(e.key!=='Enter')return;e.preventDefault();const value=this.value.trim();this.value='';if(!value)return;playTapSound();await handleCartPayment(value)}}
async function handleCartPayment(cardId){stopAllTimers();if(cardId!==currentCard){showError(t('That is not the same card. Please use the card used for the store.','这不是进入商店时使用的同一张卡。请使用原卡支付。'));return}const items=cart.map(x=>({product_id:x.product_id,quantity:x.quantity}));try{const response=await fetch('/api/store/buy-cart',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({card_id:cardId,items})});const data=await response.json();if(!response.ok||!data.success){if(data.error==='INSUFFICIENT_BALANCE'){showError(t('Insufficient balance. Current balance: '+data.balance,'余额不足。目前余额：'+data.balance));}else showError(data.error||t('Payment failed.','支付失败。'));return}cart=[];document.getElementById('storeBalance').textContent=data.balance;showLoading('Processing payment','正在处理付款','Please keep your card with you','请保留卡片',4000,()=>showReceipt(data.receipt))}catch(e){console.error(e);showError(t('Unable to contact the server.','无法连接服务器。'))}}

function showReceipt(receipt){stopAllTimers();document.getElementById('receiptNumber').textContent=receipt.receipt_number;document.getElementById('receiptCard').textContent=receipt.card_id;document.getElementById('receiptDescription').textContent=receipt.description;document.getElementById('receiptAmount').textContent=signedMoney(receipt.amount_cents);document.getElementById('receiptBalance').textContent=money(receipt.balance_after_cents);document.getElementById('receiptTime').textContent=receipt.created_at;showScreen('receiptScreen');startCountdown('receiptTimer',5,goHome)}
function money(cents){return '$'+(Number(cents)/100).toFixed(2)}
function signedMoney(cents){const n=Number(cents);return n<0?'-$'+(Math.abs(n)/100).toFixed(2):'+$'+(n/100).toFixed(2)}
function escapeHtml(v){const d=document.createElement('div');d.textContent=v??'';return d.innerHTML}
function showError(text){stopAllTimers();document.getElementById('errorText').textContent=text;showScreen('errorScreen')}

setupScanner('scanInput',handleInitialScan);setupScanner('chargeScanInput',handleChargeFinishScan);setupScanner('differentScanInput',handleDifferentConfirmScan)

const videos=['https://cdn.coverr.co/videos/coverr-a-person-using-a-credit-card-1576/1080p.mp4','https://cdn.coverr.co/videos/coverr-paying-with-a-credit-card-1577/1080p.mp4'];let videoIndex=0;const video=document.getElementById('backgroundVideo');function startVideo(){if(!videos.length)return;video.src=videos[videoIndex];video.play().catch(()=>{});video.addEventListener('ended',()=>{videoIndex=(videoIndex+1)%videos.length;video.src=videos[videoIndex];video.play().catch(()=>{})})}startVideo();
</script>
</body>
</html>
"""


# ============================================================
# ADMIN LOGIN / ADMIN HTML
# ============================================================

ADMIN_LOGIN_HTML = r"""
<!doctype html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Admin Login</title><style>body{margin:0;min-height:100vh;background:#07111f;color:#fff;font-family:Arial,sans-serif;display:flex;align-items:center;justify-content:center}.box{width:390px;max-width:90%;background:#101d2d;padding:40px;border-radius:20px}input{width:100%;padding:15px;margin:8px 0;box-sizing:border-box;border-radius:10px;border:1px solid #33465c;background:#07111f;color:#fff;font-size:17px}button{width:100%;padding:15px;margin-top:15px;border:0;border-radius:10px;font-size:17px;font-weight:bold}.error{color:#ff8f8f;margin-bottom:15px}</style></head><body><div class="box"><h1>Admin</h1>{% if error %}<div class="error">{{error}}</div>{% endif %}<form method="POST"><input name="username" placeholder="Username"><input name="password" type="password" placeholder="Password"><button type="submit">Login</button></form></div></body></html>
"""


ADMIN_HTML = r"""
<!doctype html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Kiosk Admin</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#07111f;color:#fff;font-family:Arial,sans-serif}header{padding:20px 25px;background:#0d1b2d;display:flex;justify-content:space-between;align-items:center;position:sticky;top:0;z-index:10}header a{color:#fff}main{padding:25px;max-width:1700px;margin:auto}.card{background:#101d2d;border:1px solid #23364c;border-radius:16px;padding:20px;margin-bottom:20px}h1,h2{margin-top:0}input,textarea{padding:10px;border-radius:8px;border:1px solid #33465c;background:#07111f;color:#fff}table{width:100%;border-collapse:collapse}th,td{padding:10px;border-bottom:1px solid #24384d;text-align:left;vertical-align:top}button{padding:10px 14px;border:0;border-radius:8px;cursor:pointer}.add{background:#fff;color:#07111f;font-weight:bold}.delete{background:#572222;color:#fff}.wide{width:100%}.product-admin{display:grid;grid-template-columns:120px 1fr 1fr 140px;gap:12px;align-items:start}.thumb{width:120px;height:90px;object-fit:cover;border-radius:10px;background:#091522;border:1px solid #23364c}.fields{display:grid;grid-template-columns:1fr 1fr;gap:8px}.fields textarea{min-height:70px;resize:vertical}.new-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}.new-grid textarea{width:100%;min-height:85px}.product-row{padding:18px 0;border-bottom:1px solid #24384d}.small{font-size:12px;color:#8194a8}@media(max-width:900px){.product-admin{grid-template-columns:1fr}.fields,.new-grid{grid-template-columns:1fr}.thumb{width:160px;height:110px}}
</style>
</head>
<body>
<header><strong>KIOSK ADMIN — {{kiosk_id}}</strong><a href="/admin/logout">Logout</a></header>
<main>
<div class="card"><h2>Active Session</h2><div id="active">Loading...</div></div>
<div class="card"><h2>Add Food Product</h2><div class="new-grid"><input id="newName" placeholder="English name"><input id="newNameZh" placeholder="Chinese name"><textarea id="newDescription" placeholder="English description"></textarea><textarea id="newDescriptionZh" placeholder="Chinese description"></textarea><input id="newPrice" type="number" step="0.01" placeholder="Price"><input id="newImage" type="file" accept="image/*"></div><button class="add" onclick="addProduct()">Add Product</button><div class="small">Images are resized in the browser before being saved to the database.</div></div>
<div class="card"><h2>Products</h2><div id="products">Loading...</div></div>
<div class="card"><h2>Cards</h2><table><thead><tr><th>Card</th><th>Balance</th><th>Updated</th></tr></thead><tbody id="cards"></tbody></table></div>
<div class="card"><h2>Transactions</h2><table><thead><tr><th>Time</th><th>Card</th><th>Type</th><th>Amount</th><th>Coins</th><th>Description</th></tr></thead><tbody id="transactions"></tbody></table></div>
<div class="card"><h2>Receipts</h2><table><thead><tr><th>Receipt</th><th>Time</th><th>Card</th><th>Type</th><th>Description</th><th>Amount</th><th>Balance</th></tr></thead><tbody id="receipts"></tbody></table></div>
<div class="card"><h2>Coin Events</h2><table><thead><tr><th>Time</th><th>Kiosk</th><th>Card</th><th>Accepted</th><th>Coins</th><th>Amount</th><th>Reason</th></tr></thead><tbody id="coins"></tbody></table></div>
</main>
<script>
function escapeHtml(v){const d=document.createElement('div');d.textContent=v??'';return d.innerHTML}function money(c){return '$'+(Number(c)/100).toFixed(2)}
async function resizeImage(file){if(!file)return '';return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=e=>{const img=new Image();img.onload=()=>{const maxW=1000,maxH=700;let w=img.width,h=img.height;const scale=Math.min(1,maxW/w,maxH/h);w=Math.max(1,Math.round(w*scale));h=Math.max(1,Math.round(h*scale));const canvas=document.createElement('canvas');canvas.width=w;canvas.height=h;const ctx=canvas.getContext('2d');ctx.drawImage(img,0,0,w,h);resolve(canvas.toDataURL('image/jpeg',.82))};img.onerror=reject;img.src=e.target.result};reader.onerror=reject;reader.readAsDataURL(file)})}
async function addProduct(){const name=document.getElementById('newName').value.trim(),nameZh=document.getElementById('newNameZh').value.trim(),description=document.getElementById('newDescription').value.trim(),descriptionZh=document.getElementById('newDescriptionZh').value.trim(),price=Number(document.getElementById('newPrice').value),file=document.getElementById('newImage').files[0];if(!name||price<0)return;let image='';try{image=await resizeImage(file)}catch(e){}await fetch('/api/admin/product',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,name_zh:nameZh,description,description_zh:descriptionZh,price_cents:Math.round(price*100),image_data:image})});['newName','newNameZh','newDescription','newDescriptionZh','newPrice','newImage'].forEach(id=>document.getElementById(id).value='');load()}
async function saveProduct(id){const file=document.getElementById('image-'+id).files[0];let image=null;try{if(file)image=await resizeImage(file)}catch(e){}const body={name:document.getElementById('name-'+id).value.trim(),name_zh:document.getElementById('namezh-'+id).value.trim(),description:document.getElementById('desc-'+id).value.trim(),description_zh:document.getElementById('desczh-'+id).value.trim(),price_cents:Math.round(Number(document.getElementById('price-'+id).value)*100),active:document.getElementById('active-'+id).checked};if(image!==null)body.image_data=image;await fetch('/api/admin/product/'+id,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});load()}
async function deleteProduct(id){await fetch('/api/admin/product/'+id+'/delete',{method:'POST'});load()}
async function load(){try{const response=await fetch('/api/admin/live?t='+Date.now(),{cache:'no-store'});if(!response.ok)return;const data=await response.json();const active=document.getElementById('active');if(data.active_session){const s=data.active_session;active.innerHTML='<strong>ACTIVE</strong><br>Card: '+escapeHtml(s.card_id)+'<br>Coins: '+s.coin_count+'<br>Amount: '+money(s.amount_cents)}else active.textContent='No active session.';const products=document.getElementById('products');products.innerHTML=data.products.map(p=>`<div class="product-row"><div class="product-admin"><div>${p.image_data?`<img class="thumb" src="${escapeHtml(p.image_data)}">`:'<div class="thumb"></div>'}</div><div class="fields"><input id="name-${p.id}" value="${escapeHtml(p.name)}"><input id="namezh-${p.id}" value="${escapeHtml(p.name_zh||'')}"><textarea id="desc-${p.id}" placeholder="English description">${escapeHtml(p.description||'')}</textarea><textarea id="desczh-${p.id}" placeholder="Chinese description">${escapeHtml(p.description_zh||'')}</textarea><input id="price-${p.id}" type="number" step="0.01" value="${(p.price_cents/100).toFixed(2)}"></div><div><input id="image-${p.id}" type="file" accept="image/*"><label><input id="active-${p.id}" type="checkbox" ${p.active?'checked':''}> Active</label></div><div><button onclick="saveProduct(${p.id})">Save</button> <button class="delete" onclick="deleteProduct(${p.id})">Deactivate</button></div></div></div>`).join('');document.getElementById('cards').innerHTML=data.cards.map(c=>`<tr><td>${escapeHtml(c.card_id)}</td><td>${money(c.balance_cents)}</td><td>${escapeHtml(c.updated_at)}</td></tr>`).join('');document.getElementById('transactions').innerHTML=data.transactions.map(t=>`<tr><td>${escapeHtml(t.created_at)}</td><td>${escapeHtml(t.card_id)}</td><td>${escapeHtml(t.type)}</td><td>${money(t.amount_cents)}</td><td>${t.coins}</td><td>${escapeHtml(t.description)}</td></tr>`).join('');document.getElementById('receipts').innerHTML=data.receipts.map(r=>`<tr><td>${escapeHtml(r.receipt_number)}</td><td>${escapeHtml(r.created_at)}</td><td>${escapeHtml(r.card_id)}</td><td>${escapeHtml(r.type)}</td><td>${escapeHtml(r.description)}</td><td>${money(r.amount_cents)}</td><td>${money(r.balance_after_cents)}</td></tr>`).join('');document.getElementById('coins').innerHTML=data.coin_events.map(c=>`<tr><td>${escapeHtml(c.created_at)}</td><td>${escapeHtml(c.kiosk_id)}</td><td>${escapeHtml(c.card_id||'-')}</td><td>${c.accepted?'YES':'NO'}</td><td>${c.coins}</td><td>${money(c.amount_cents)}</td><td>${escapeHtml(c.reason)}</td></tr>`).join('')}catch(e){console.error(e)}}load();setInterval(load,2000)
</script>
</body>
</html>
"""


BACK_HTML = r"""
<!doctype html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Backend Console</title><style>*{box-sizing:border-box}body{margin:0;background:#050b12;color:#d8e5f2;font-family:Consolas,"Courier New",monospace}header{position:sticky;top:0;z-index:10;background:#08131f;border-bottom:1px solid #1c3349;padding:18px 25px;display:flex;justify-content:space-between}.title{color:#8dd8ff;font-size:20px;font-weight:bold}.live{color:#71e39a}main{padding:20px;max-width:1700px;margin:auto}.grid{display:grid;grid-template-columns:1fr 2fr;gap:20px}.panel{background:#091522;border:1px solid #1a3045;border-radius:12px;padding:18px;margin-bottom:20px}.panel h2{margin-top:0;color:#8dd8ff}.session{font-size:18px;line-height:1.8}.log{max-height:70vh;overflow-y:auto}.event{padding:9px 5px;border-bottom:1px solid #122536}.time{color:#687f93}.type{color:#8dd8ff;font-weight:bold}.amount{color:#71e39a}.receipt{color:#d2a8ff}.error{color:#ff8585}@media(max-width:900px){.grid{grid-template-columns:1fr}}</style></head><body><header><div class="title">BACKEND CONSOLE — {{kiosk_id}}</div><div class="live">LIVE</div></header><main><div class="grid"><div><div class="panel"><h2>Current Session</h2><div class="session" id="session">Loading...</div></div><div class="panel"><h2>Latest Receipts</h2><div class="log" id="receipts">Loading...</div></div></div><div><div class="panel"><h2>Live Machine Activity</h2><div class="log" id="events">Loading...</div></div></div></div></main><script>function e(v){const d=document.createElement('div');d.textContent=v??'';return d.innerHTML}function m(c){return '$'+(Number(c)/100).toFixed(2)}async function load(){try{const r=await fetch('/api/back/live?t='+Date.now(),{cache:'no-store'}),d=await r.json();document.getElementById('session').innerHTML=d.active_session?`ACTIVE<br>Card: ${e(d.active_session.card_id)}<br>Coins: ${d.active_session.coin_count}<br>Amount: ${m(d.active_session.amount_cents)}`:'NO ACTIVE SESSION';const events=[];d.transactions.forEach(x=>events.push({time:x.created_at,type:x.type,card:x.card_id,msg:x.description,amount:x.amount_cents,id:x.id}));d.coin_events.forEach(x=>events.push({time:x.created_at,type:x.accepted?'COIN':'COIN REJECTED',card:x.card_id||'-',msg:x.reason,amount:x.amount_cents,id:x.id}));events.sort((a,b)=>b.time.localeCompare(a.time));document.getElementById('events').innerHTML=events.slice(0,150).map(x=>`<div class="event"><span class="time">${e(x.time)}</span> &nbsp; <span class="${x.type.includes('REJECTED')?'error':'type'}">${e(x.type)}</span> &nbsp; KIOSK1 &nbsp; CARD: ${e(x.card)} &nbsp; ${e(x.msg)} ${x.amount?`<span class="amount">${m(x.amount)}</span>`:''}</div>`).join('');document.getElementById('receipts').innerHTML=d.receipts.slice(0,50).map(x=>`<div class="event"><span class="time">${e(x.created_at)}</span><br><span class="receipt">${e(x.receipt_number)}</span><br>CARD: ${e(x.card_id)}<br>${e(x.description)} &nbsp; ${m(x.amount_cents)}<br>BALANCE: ${m(x.balance_after_cents)}</div>`).join('')}catch(err){console.error(err)}}load();setInterval(load,500)</script></body></html>
"""


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
