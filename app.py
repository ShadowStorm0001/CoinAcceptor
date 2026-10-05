from flask import Flask, request, jsonify, session, redirect, render_template_string
import sqlite3
import os
import time
from datetime import datetime

app = Flask(__name__)
app.secret_key = "kiosk-secret-key-change-this"

DB_FILE = "kiosk.db"
KIOSK_ID = "KIOSK1"
COIN_VALUE_CENTS = 100
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin"

# Replace these only if you want different stock videos.
# The kiosk will automatically fall back to the animated background
# if a video cannot be loaded.
STOCK_VIDEOS = [
    "https://cdn.coverr.co/videos/coverr-a-person-using-a-credit-card-1576/1080p.mp4",
    "https://cdn.coverr.co/videos/coverr-paying-with-a-credit-card-1577/1080p.mp4"
]


def db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def money(cents):
    return f"${cents / 100:.2f}"


def init_db():
    conn = db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS cards (
            card_id TEXT PRIMARY KEY,
            balance_cents INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kiosk_id TEXT NOT NULL,
            card_id TEXT,
            mode TEXT NOT NULL,
            status TEXT NOT NULL,
            coin_count INTEGER NOT NULL DEFAULT 0,
            amount_cents INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kiosk_id TEXT NOT NULL,
            card_id TEXT,
            type TEXT NOT NULL,
            amount_cents INTEGER NOT NULL DEFAULT 0,
            coins INTEGER NOT NULL DEFAULT 0,
            description TEXT,
            created_at TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS coin_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kiosk_id TEXT NOT NULL,
            card_id TEXT,
            accepted INTEGER NOT NULL,
            coins INTEGER NOT NULL,
            amount_cents INTEGER NOT NULL,
            reason TEXT,
            created_at TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            price_cents INTEGER NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        )
    """)

    if conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0:
        conn.executemany(
            "INSERT INTO products (name, price_cents, active) VALUES (?, ?, 1)",
            [
                ("Service 1", 200),
                ("Service 2", 500),
                ("Service 3", 1000)
            ]
        )

    conn.commit()
    conn.close()


def get_active_session(conn, kiosk_id):
    return conn.execute("""
        SELECT *
        FROM sessions
        WHERE kiosk_id = ?
        AND status = 'active'
        ORDER BY id DESC
        LIMIT 1
    """, (kiosk_id,)).fetchone()


def create_card(conn, card_id):
    existing = conn.execute(
        "SELECT * FROM cards WHERE card_id = ?",
        (card_id,)
    ).fetchone()

    if not existing:
        timestamp = now()
        conn.execute("""
            INSERT INTO cards
            (card_id, balance_cents, created_at, updated_at)
            VALUES (?, 0, ?, ?)
        """, (card_id, timestamp, timestamp))


def close_active_sessions(conn, kiosk_id):
    conn.execute("""
        UPDATE sessions
        SET status = 'closed', updated_at = ?
        WHERE kiosk_id = ?
        AND status = 'active'
    """, (now(), kiosk_id))


init_db()


@app.route("/")
def index():
    return redirect("/kiosk")


@app.route("/kiosk")
def kiosk():
    return render_template_string(KIOSK_HTML, videos=STOCK_VIDEOS)


@app.route("/api/kiosk/scan", methods=["POST"])
def scan_card():
    data = request.get_json(silent=True) or {}

    kiosk_id = str(data.get("kiosk_id", KIOSK_ID)).strip()
    card_id = str(data.get("card_id", "")).strip()
    mode = str(data.get("mode", "balance")).strip().lower()

    if not card_id:
        return jsonify({
            "success": False,
            "message": "No card detected"
        }), 400

    if mode not in ("charge", "balance", "store"):
        return jsonify({
            "success": False,
            "message": "Invalid mode"
        }), 400

    conn = db()

    create_card(conn, card_id)

    if mode == "charge":
        close_active_sessions(conn, kiosk_id)

        timestamp = now()

        cursor = conn.execute("""
            INSERT INTO sessions
            (kiosk_id, card_id, mode, status, coin_count,
             amount_cents, created_at, updated_at)
            VALUES (?, ?, 'charge', 'active', 0, 0, ?, ?)
        """, (
            kiosk_id,
            card_id,
            timestamp,
            timestamp
        ))

        session_id = cursor.lastrowid

    else:
        session_id = None

    card = conn.execute(
        "SELECT * FROM cards WHERE card_id = ?",
        (card_id,)
    ).fetchone()

    conn.commit()
    conn.close()

    return jsonify({
        "success": True,
        "kiosk_id": kiosk_id,
        "card_id": card_id,
        "balance_cents": card["balance_cents"],
        "balance": money(card["balance_cents"]),
        "session_id": session_id,
        "mode": mode
    })


@app.route("/api/kiosk/state")
def kiosk_state():
    kiosk_id = request.args.get("kiosk_id", KIOSK_ID)

    conn = db()
    active = get_active_session(conn, kiosk_id)

    if not active:
        conn.close()
        return jsonify({
            "active": False
        })

    card = conn.execute(
        "SELECT * FROM cards WHERE card_id = ?",
        (active["card_id"],)
    ).fetchone()

    conn.close()

    return jsonify({
        "active": True,
        "session_id": active["id"],
        "card_id": active["card_id"],
        "coin_count": active["coin_count"],
        "amount_cents": active["amount_cents"],
        "amount": money(active["amount_cents"]),
        "balance_cents": card["balance_cents"],
        "balance": money(card["balance_cents"])
    })


@app.route("/api/coin", methods=["POST"])
def coin():
    data = request.get_json(silent=True) or {}

    kiosk_id = str(data.get("kiosk_id", KIOSK_ID)).strip()

    try:
        coins = int(data.get("coins", 1))
    except:
        coins = 1

    if coins < 1:
        coins = 1

    amount = coins * COIN_VALUE_CENTS
    timestamp = now()

    conn = db()

    active = get_active_session(conn, kiosk_id)

    if not active:
        conn.execute("""
            INSERT INTO coin_events
            (kiosk_id, card_id, accepted, coins, amount_cents,
             reason, created_at)
            VALUES (?, NULL, 0, ?, ?, ?, ?)
        """, (
            kiosk_id,
            coins,
            amount,
            "NO_ACTIVE_CARD_SESSION",
            timestamp
        ))

        conn.commit()
        conn.close()

        return jsonify({
            "success": False,
            "accepted": False,
            "reason": "NO_ACTIVE_CARD_SESSION"
        }), 409

    card_id = active["card_id"]

    new_coin_count = active["coin_count"] + coins
    new_amount = active["amount_cents"] + amount

    conn.execute("""
        UPDATE sessions
        SET coin_count = ?,
            amount_cents = ?,
            updated_at = ?
        WHERE id = ?
    """, (
        new_coin_count,
        new_amount,
        timestamp,
        active["id"]
    ))

    conn.execute("""
        INSERT INTO coin_events
        (kiosk_id, card_id, accepted, coins, amount_cents,
         reason, created_at)
        VALUES (?, ?, 1, ?, ?, ?, ?)
    """, (
        kiosk_id,
        card_id,
        coins,
        amount,
        "ACTIVE_CHARGE_SESSION",
        timestamp
    ))

    conn.commit()

    card = conn.execute(
        "SELECT * FROM cards WHERE card_id = ?",
        (card_id,)
    ).fetchone()

    conn.close()

    return jsonify({
        "success": True,
        "accepted": True,
        "card_id": card_id,
        "coin_count": new_coin_count,
        "amount_cents": new_amount,
        "amount": money(new_amount),
        "balance_cents": card["balance_cents"],
        "balance": money(card["balance_cents"])
    })


@app.route("/api/kiosk/confirm-charge", methods=["POST"])
def confirm_charge():
    data = request.get_json(silent=True) or {}

    kiosk_id = str(data.get("kiosk_id", KIOSK_ID)).strip()
    scanned_card = str(data.get("card_id", "")).strip()

    if not scanned_card:
        return jsonify({
            "success": False,
            "message": "Please scan a card"
        }), 400

    conn = db()

    active = get_active_session(conn, kiosk_id)

    if not active:
        conn.close()
        return jsonify({
            "success": False,
            "message": "No active charge session"
        }), 409

    if active["card_id"] != scanned_card:
        conn.close()

        return jsonify({
            "success": False,
            "different_card": True,
            "original_card": active["card_id"],
            "scanned_card": scanned_card,
            "message": "A different card was detected"
        }), 409

    if active["amount_cents"] <= 0:
        conn.close()

        return jsonify({
            "success": False,
            "message": "No coins have been inserted"
        }), 400

    amount = active["amount_cents"]
    card_id = active["card_id"]
    timestamp = now()

    conn.execute("""
        UPDATE cards
        SET balance_cents = balance_cents + ?,
            updated_at = ?
        WHERE card_id = ?
    """, (
        amount,
        timestamp,
        card_id
    ))

    conn.execute("""
        INSERT INTO transactions
        (kiosk_id, card_id, type, amount_cents, coins,
         description, created_at)
        VALUES (?, ?, 'charge', ?, ?, ?, ?)
    """, (
        kiosk_id,
        card_id,
        amount,
        active["coin_count"],
        "Card charged",
        timestamp
    ))

    conn.execute("""
        UPDATE sessions
        SET status = 'completed',
            updated_at = ?
        WHERE id = ?
    """, (
        timestamp,
        active["id"]
    ))

    card = conn.execute(
        "SELECT * FROM cards WHERE card_id = ?",
        (card_id,)
    ).fetchone()

    conn.commit()
    conn.close()

    return jsonify({
        "success": True,
        "card_id": card_id,
        "added_cents": amount,
        "added": money(amount),
        "balance_cents": card["balance_cents"],
        "balance": money(card["balance_cents"])
    })


@app.route("/api/kiosk/confirm-different-card", methods=["POST"])
def confirm_different_card():
    data = request.get_json(silent=True) or {}

    kiosk_id = str(data.get("kiosk_id", KIOSK_ID)).strip()
    card_id = str(data.get("card_id", "")).strip()

    if not card_id:
        return jsonify({
            "success": False,
            "message": "Please scan a card"
        }), 400

    conn = db()

    active = get_active_session(conn, kiosk_id)

    if not active:
        conn.close()

        return jsonify({
            "success": False,
            "message": "Session expired"
        }), 409

    if active["amount_cents"] <= 0:
        conn.close()

        return jsonify({
            "success": False,
            "message": "No amount is waiting to be transferred"
        }), 400

    create_card(conn, card_id)

    amount = active["amount_cents"]
    timestamp = now()

    conn.execute("""
        UPDATE cards
        SET balance_cents = balance_cents + ?,
            updated_at = ?
        WHERE card_id = ?
    """, (
        amount,
        timestamp,
        card_id
    ))

    conn.execute("""
        INSERT INTO transactions
        (kiosk_id, card_id, type, amount_cents, coins,
         description, created_at)
        VALUES (?, ?, 'charge', ?, ?, ?, ?)
    """, (
        kiosk_id,
        card_id,
        amount,
        active["coin_count"],
        "Card charged after different-card confirmation",
        timestamp
    ))

    conn.execute("""
        UPDATE sessions
        SET status = 'completed',
            updated_at = ?
        WHERE id = ?
    """, (
        timestamp,
        active["id"]
    ))

    card = conn.execute(
        "SELECT * FROM cards WHERE card_id = ?",
        (card_id,)
    ).fetchone()

    conn.commit()
    conn.close()

    return jsonify({
        "success": True,
        "card_id": card_id,
        "added_cents": amount,
        "added": money(amount),
        "balance_cents": card["balance_cents"],
        "balance": money(card["balance_cents"])
    })


@app.route("/api/store/products")
def products():
    conn = db()

    rows = conn.execute("""
        SELECT *
        FROM products
        WHERE active = 1
        ORDER BY id
    """).fetchall()

    conn.close()

    return jsonify([
        {
            "id": row["id"],
            "name": row["name"],
            "price_cents": row["price_cents"],
            "price": money(row["price_cents"])
        }
        for row in rows
    ])


@app.route("/api/store/buy", methods=["POST"])
def buy():
    data = request.get_json(silent=True) or {}

    kiosk_id = str(data.get("kiosk_id", KIOSK_ID)).strip()
    card_id = str(data.get("card_id", "")).strip()

    try:
        product_id = int(data.get("product_id"))
    except:
        return jsonify({
            "success": False,
            "message": "Invalid product"
        }), 400

    conn = db()

    product = conn.execute("""
        SELECT *
        FROM products
        WHERE id = ?
        AND active = 1
    """, (product_id,)).fetchone()

    if not product:
        conn.close()

        return jsonify({
            "success": False,
            "message": "Product unavailable"
        }), 404

    card = conn.execute("""
        SELECT *
        FROM cards
        WHERE card_id = ?
    """, (card_id,)).fetchone()

    if not card:
        conn.close()

        return jsonify({
            "success": False,
            "message": "Card not found"
        }), 404

    if card["balance_cents"] < product["price_cents"]:
        conn.close()

        return jsonify({
            "success": False,
            "insufficient": True,
            "balance_cents": card["balance_cents"],
            "balance": money(card["balance_cents"]),
            "required_cents": product["price_cents"],
            "required": money(product["price_cents"])
        }), 409

    timestamp = now()

    conn.execute("""
        UPDATE cards
        SET balance_cents = balance_cents - ?,
            updated_at = ?
        WHERE card_id = ?
    """, (
        product["price_cents"],
        timestamp,
        card_id
    ))

    conn.execute("""
        INSERT INTO transactions
        (kiosk_id, card_id, type, amount_cents, coins,
         description, created_at)
        VALUES (?, ?, 'purchase', ?, 0, ?, ?)
    """, (
        kiosk_id,
        card_id,
        -product["price_cents"],
        product["name"],
        timestamp
    ))

    new_balance = card["balance_cents"] - product["price_cents"]

    conn.commit()
    conn.close()

    return jsonify({
        "success": True,
        "product": product["name"],
        "price_cents": product["price_cents"],
        "price": money(product["price_cents"]),
        "balance_cents": new_balance,
        "balance": money(new_balance)
    })


@app.route("/api/kiosk/end", methods=["POST"])
def end_session():
    data = request.get_json(silent=True) or {}
    kiosk_id = str(data.get("kiosk_id", KIOSK_ID)).strip()

    conn = db()

    conn.execute("""
        UPDATE sessions
        SET status = 'closed',
            updated_at = ?
        WHERE kiosk_id = ?
        AND status = 'active'
    """, (
        now(),
        kiosk_id
    ))

    conn.commit()
    conn.close()

    return jsonify({
        "success": True
    })


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")

        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session["admin"] = True
            return redirect("/admin")

    return render_template_string(ADMIN_LOGIN_HTML)


@app.route("/admin")
def admin():
    if not session.get("admin"):
        return redirect("/admin/login")

    return render_template_string(ADMIN_HTML)


@app.route("/api/admin/live")
def admin_live():
    if not session.get("admin"):
        return jsonify({
            "error": "Unauthorized"
        }), 401

    conn = db()

    sessions = conn.execute("""
        SELECT *
        FROM sessions
        WHERE status = 'active'
        ORDER BY id DESC
    """).fetchall()

    transactions = conn.execute("""
        SELECT *
        FROM transactions
        ORDER BY id DESC
        LIMIT 50
    """).fetchall()

    coins = conn.execute("""
        SELECT *
        FROM coin_events
        ORDER BY id DESC
        LIMIT 50
    """).fetchall()

    conn.close()

    return jsonify({
        "sessions": [
            {
                "id": row["id"],
                "kiosk_id": row["kiosk_id"],
                "card_id": row["card_id"],
                "mode": row["mode"],
                "coin_count": row["coin_count"],
                "amount": money(row["amount_cents"]),
                "updated_at": row["updated_at"]
            }
            for row in sessions
        ],
        "transactions": [
            {
                "time": row["created_at"],
                "kiosk": row["kiosk_id"],
                "card": row["card_id"],
                "type": row["type"],
                "amount": money(row["amount_cents"]),
                "coins": row["coins"],
                "description": row["description"]
            }
            for row in transactions
        ],
        "coins": [
            {
                "time": row["created_at"],
                "kiosk": row["kiosk_id"],
                "card": row["card_id"] or "-",
                "accepted": bool(row["accepted"]),
                "coins": row["coins"],
                "amount": money(row["amount_cents"]),
                "reason": row["reason"]
            }
            for row in coins
        ]
    })


KIOSK_HTML = r"""
<!DOCTYPE html>
<html lang="en">
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
    width: 100%;
    height: 100%;
    margin: 0;
    overflow: hidden;
    font-family: Arial, Helvetica, sans-serif;
    background: #071018;
    color: white;
    user-select: none;
}

body {
    touch-action: manipulation;
}

#app {
    position: relative;
    width: 100%;
    height: 100%;
    overflow: hidden;
    background:
        radial-gradient(circle at 50% 20%, #193449 0%, #071018 60%);
}

.video-background {
    position: absolute;
    inset: 0;
    overflow: hidden;
    opacity: 0.22;
    z-index: 0;
}

.video-background video {
    width: 100%;
    height: 100%;
    object-fit: cover;
    filter: brightness(0.55) saturate(0.65);
}

.video-overlay {
    position: absolute;
    inset: 0;
    background:
        linear-gradient(
            180deg,
            rgba(4, 11, 17, 0.35),
            rgba(4, 11, 17, 0.9)
        );
}

.animated-background {
    position: absolute;
    inset: 0;
    z-index: 0;
    overflow: hidden;
    pointer-events: none;
}

.orb {
    position: absolute;
    width: 35vw;
    height: 35vw;
    border-radius: 50%;
    filter: blur(70px);
    opacity: 0.18;
    animation: float 12s ease-in-out infinite alternate;
}

.orb.one {
    left: -10%;
    top: 5%;
    background: #3d8fc4;
}

.orb.two {
    right: -10%;
    bottom: -5%;
    background: #7558bd;
    animation-delay: -5s;
}

@keyframes float {
    from {
        transform: translate3d(0, 0, 0);
    }
    to {
        transform: translate3d(5vw, -4vh, 0);
    }
}

.screen {
    position: absolute;
    inset: 0;
    z-index: 2;
    display: none;
    flex-direction: column;
    padding: 3vh 4vw;
    animation: screenIn 0.25s ease;
}

.screen.active {
    display: flex;
}

@keyframes screenIn {
    from {
        opacity: 0;
        transform: translateY(12px);
    }
    to {
        opacity: 1;
        transform: translateY(0);
    }
}

.topbar {
    height: 9vh;
    min-height: 55px;
    display: flex;
    align-items: center;
    justify-content: space-between;
}

.logo {
    font-size: clamp(22px, 3vw, 42px);
    font-weight: 700;
    letter-spacing: 2px;
}

.language {
    min-width: 125px;
    height: 60px;
    padding: 0 24px;
    border: 1px solid rgba(255,255,255,0.25);
    border-radius: 16px;
    background: rgba(255,255,255,0.08);
    color: white;
    font-size: clamp(18px, 2vw, 28px);
    font-weight: 600;
}

.language:active {
    transform: scale(0.96);
}

.content {
    flex: 1;
    display: flex;
    flex-direction: column;
    justify-content: center;
    align-items: center;
    width: 100%;
}

.title {
    font-size: clamp(30px, 5vw, 70px);
    font-weight: 700;
    text-align: center;
    margin-bottom: 3vh;
}

.subtitle {
    font-size: clamp(18px, 2.4vw, 34px);
    text-align: center;
    opacity: 0.72;
    margin-bottom: 4vh;
    max-width: 850px;
    line-height: 1.4;
}

.menu {
    width: min(900px, 92vw);
    display: flex;
    flex-direction: column;
    gap: 18px;
}

.big-button {
    width: 100%;
    min-height: 100px;
    border: 1px solid rgba(255,255,255,0.18);
    border-radius: 24px;
    background: rgba(255,255,255,0.09);
    color: white;
    font-size: clamp(24px, 3.5vw, 46px);
    font-weight: 650;
    letter-spacing: 0.5px;
    box-shadow: 0 12px 40px rgba(0,0,0,0.18);
    transition:
        transform 0.15s ease,
        background 0.15s ease,
        box-shadow 0.15s ease;
}

.big-button:active {
    transform: scale(0.975);
    background: rgba(255,255,255,0.17);
    box-shadow: 0 4px 15px rgba(0,0,0,0.25);
}

.back-button {
    width: min(330px, 85vw);
    min-height: 82px;
    border: 1px solid rgba(255,255,255,0.18);
    border-radius: 20px;
    background: rgba(255,255,255,0.08);
    color: white;
    font-size: clamp(22px, 2.8vw, 38px);
    font-weight: 600;
}

.back-button:active {
    transform: scale(0.96);
}

.card-area {
    width: min(700px, 92vw);
    min-height: 190px;
    border-radius: 30px;
    border: 2px dashed rgba(255,255,255,0.28);
    background: rgba(255,255,255,0.055);
    display: flex;
    align-items: center;
    justify-content: center;
    flex-direction: column;
    padding: 30px;
    position: relative;
    overflow: hidden;
}

.scan-line {
    position: absolute;
    left: 5%;
    right: 5%;
    height: 2px;
    background: rgba(255,255,255,0.65);
    box-shadow: 0 0 20px rgba(255,255,255,0.7);
    animation: scan 2.2s ease-in-out infinite;
}

@keyframes scan {
    0%, 100% {
        top: 15%;
        opacity: 0;
    }
    15% {
        opacity: 1;
    }
    50% {
        top: 85%;
        opacity: 1;
    }
    85% {
        opacity: 0;
    }
}

.scan-text {
    font-size: clamp(25px, 4vw, 48px);
    font-weight: 600;
    text-align: center;
}

.scan-input {
    position: absolute;
    left: 50%;
    top: 50%;
    width: 1px;
    height: 1px;
    opacity: 0;
    pointer-events: none;
}

.amount {
    font-size: clamp(55px, 10vw, 120px);
    font-weight: 700;
    letter-spacing: 2px;
    margin: 15px 0;
}

.card-number {
    font-size: clamp(20px, 2.5vw, 34px);
    opacity: 0.65;
    word-break: break-all;
    text-align: center;
}

.info-box {
    width: min(800px, 92vw);
    padding: 30px;
    border-radius: 28px;
    background: rgba(255,255,255,0.075);
    border: 1px solid rgba(255,255,255,0.12);
    text-align: center;
}

.info-label {
    font-size: clamp(18px, 2vw, 28px);
    opacity: 0.65;
}

.info-value {
    font-size: clamp(40px, 7vw, 80px);
    font-weight: 700;
    margin-top: 8px;
}

.charge-row {
    display: flex;
    width: min(900px, 92vw);
    gap: 18px;
    margin: 20px 0;
}

.charge-box {
    flex: 1;
    min-height: 130px;
    padding: 25px;
    border-radius: 24px;
    background: rgba(255,255,255,0.07);
    text-align: center;
}

.charge-label {
    font-size: clamp(17px, 2vw, 27px);
    opacity: 0.65;
}

.charge-value {
    font-size: clamp(35px, 5vw, 60px);
    font-weight: 700;
    margin-top: 10px;
}

.product-grid {
    width: min(1100px, 95vw);
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
    gap: 18px;
    max-height: 55vh;
    overflow-y: auto;
    padding: 5px;
}

.product {
    min-height: 210px;
    border-radius: 25px;
    border: 1px solid rgba(255,255,255,0.15);
    background: rgba(255,255,255,0.075);
    color: white;
    padding: 25px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    text-align: center;
}

.product-name {
    font-size: clamp(22px, 2.5vw, 34px);
    font-weight: 650;
}

.product-price {
    font-size: clamp(32px, 4vw, 50px);
    font-weight: 700;
}

.product button {
    width: 100%;
    min-height: 65px;
    border: 0;
    border-radius: 17px;
    background: rgba(255,255,255,0.14);
    color: white;
    font-size: 23px;
    font-weight: 600;
}

.product button:active {
    transform: scale(0.96);
}

.warning {
    width: min(850px, 92vw);
    padding: 35px;
    border-radius: 28px;
    background: rgba(255,170,0,0.09);
    border: 1px solid rgba(255,190,50,0.35);
    text-align: center;
}

.warning-title {
    font-size: clamp(28px, 4vw, 48px);
    font-weight: 700;
    margin-bottom: 20px;
}

.warning-text {
    font-size: clamp(19px, 2.3vw, 30px);
    line-height: 1.5;
    opacity: 0.85;
}

.button-row {
    display: flex;
    width: min(850px, 92vw);
    gap: 18px;
    margin-top: 30px;
}

.action-button {
    flex: 1;
    min-height: 85px;
    border: 1px solid rgba(255,255,255,0.18);
    border-radius: 20px;
    background: rgba(255,255,255,0.09);
    color: white;
    font-size: clamp(21px, 2.6vw, 34px);
    font-weight: 650;
}

.action-button:active {
    transform: scale(0.97);
}

.success {
    animation: successPulse 1.3s ease-in-out infinite alternate;
}

@keyframes successPulse {
    from {
        transform: scale(1);
    }
    to {
        transform: scale(1.025);
    }
}

.countdown {
    margin-top: 25px;
    font-size: clamp(18px, 2.2vw, 28px);
    opacity: 0.6;
}

.status {
    position: absolute;
    left: 25px;
    bottom: 20px;
    z-index: 5;
    font-size: 16px;
    opacity: 0.5;
}

@media (max-width: 600px) {
    .screen {
        padding: 2vh 4vw;
    }

    .topbar {
        height: 8vh;
    }

    .logo {
        font-size: 22px;
    }

    .language {
        min-width: 90px;
        height: 50px;
        padding: 0 14px;
        font-size: 17px;
    }

    .big-button {
        min-height: 85px;
        border-radius: 20px;
    }

    .charge-row {
        flex-direction: column;
    }

    .charge-box {
        min-height: 100px;
    }

    .button-row {
        flex-direction: column;
    }

    .action-button {
        min-height: 75px;
    }

    .product-grid {
        grid-template-columns: 1fr;
        max-height: 48vh;
    }
}
</style>
</head>

<body>

<div id="app">

    <div class="video-background">
        <video id="backgroundVideo"
               autoplay
               muted
               loop
               playsinline></video>

        <div class="video-overlay"></div>
    </div>

    <div class="animated-background">
        <div class="orb one"></div>
        <div class="orb two"></div>
    </div>

    <input
        id="scanner"
        class="scan-input"
        type="text"
        autocomplete="off"
        autocorrect="off"
        autocapitalize="off"
        spellcheck="false"
    >

    <div class="screen active" id="homeScreen">

        <div class="topbar">
            <div class="logo">KIOSK</div>
            <button class="language" onclick="toggleLanguage()">
                <span id="languageButton">中文</span>
            </button>
        </div>

        <div class="content">

            <div class="title" id="homeTitle">
                Welcome
            </div>

            <div class="subtitle" id="homeSubtitle">
                Please select a service
            </div>

            <div class="menu">

                <button class="big-button" onclick="startMode('charge')">
                    <span data-en="Charge Card"
                          data-zh="充值卡">
                        Charge Card
                    </span>
                </button>

                <button class="big-button" onclick="startMode('balance')">
                    <span data-en="Check Balance"
                          data-zh="查询余额">
                        Check Balance
                    </span>
                </button>

                <button class="big-button" onclick="startMode('store')">
                    <span data-en="Store"
                          data-zh="商店">
                        Store
                    </span>
                </button>

            </div>

        </div>

    </div>


    <div class="screen" id="scanScreen">

        <div class="topbar">
            <div class="logo" id="scanModeTitle">
                Scan Card
            </div>

            <button class="language" onclick="toggleLanguage()">
                <span id="languageButton2">中文</span>
            </button>
        </div>

        <div class="content">

            <div class="title" id="scanTitle">
                Please scan your card
            </div>

            <div class="subtitle" id="scanSubtitle">
                Use the card reader to continue
            </div>

            <div class="card-area"
                 onclick="focusScanner()">

                <div class="scan-line"></div>

                <div class="scan-text" id="scanText">
                    Waiting for card
                </div>

            </div>

            <div class="countdown" id="scanCountdown"></div>

            <button class="back-button"
                    onclick="goBack()"
                    style="margin-top:30px">

                <span data-en="Back"
                      data-zh="返回">
                    Back
                </span>

            </button>

        </div>

    </div>


    <div class="screen" id="chargeScreen">

        <div class="topbar">

            <div class="logo">
                <span data-en="Charge Card"
                      data-zh="充值卡">
                    Charge Card
                </span>
            </div>

            <button class="language" onclick="toggleLanguage()">
                <span id="languageButton3">中文</span>
            </button>

        </div>

        <div class="content">

            <div class="subtitle" id="chargeCardText">
                Card
            </div>

            <div class="card-number" id="chargeCardId"></div>

            <div class="charge-row">

                <div class="charge-box">

                    <div class="charge-label"
                         data-en="Amount"
                         data-zh="充值金额">
                        Amount
                    </div>

                    <div class="charge-value"
                         id="chargeAmount">
                        $0.00
                    </div>

                </div>

                <div class="charge-box">

                    <div class="charge-label"
                         data-en="Coins"
                         data-zh="硬币数量">
                        Coins
                    </div>

                    <div class="charge-value"
                         id="chargeCoins">
                        0
                    </div>

                </div>

            </div>

            <div class="info-box">

                <div class="info-label"
                     data-en="Current Balance"
                     data-zh="当前余额">
                    Current Balance
                </div>

                <div class="info-value"
                     id="chargeBalance">
                    $0.00
                </div>

            </div>

            <div class="subtitle"
                 id="chargeInstruction"
                 style="margin-top:25px">
                Insert coins
            </div>

            <button class="back-button"
                    onclick="cancelCharge()">

                <span data-en="Back"
                      data-zh="返回">
                    Back
                </span>

            </button>

        </div>

    </div>


    <div class="screen" id="confirmCardScreen">

        <div class="topbar">
            <div class="logo">
                <span data-en="Update Card"
                      data-zh="更新卡片">
                    Update Card
                </span>
            </div>
        </div>

        <div class="content">

            <div class="title"
                 data-en="Tap your card again"
                 data-zh="请再次刷卡">
                Tap your card again
            </div>

            <div class="subtitle"
                 data-en="The amount will be added to your card after it is confirmed."
                 data-zh="确认卡片后，充值金额将加入您的卡中。">
                The amount will be added to your card after it is confirmed.
            </div>

            <div class="card-area"
                 onclick="focusScanner()">

                <div class="scan-line"></div>

                <div class="scan-text"
                     data-en="Tap card"
                     data-zh="请刷卡">
                    Tap card
                </div>

            </div>

            <div class="countdown" id="confirmCountdown"></div>

        </div>

    </div>


    <div class="screen" id="differentCardScreen">

        <div class="topbar">
            <div class="logo">
                <span data-en="Card Warning"
                      data-zh="卡片警告">
                    Card Warning
                </span>
            </div>
        </div>

        <div class="content">

            <div class="warning">

                <div class="warning-title"
                     data-en="Different card detected"
                     data-zh="检测到不同的卡">
                    Different card detected
                </div>

                <div class="warning-text">

                    <div>
                        <span data-en="Original card:"
                              data-zh="原卡：">
                            Original card:
                        </span>

                        <br>

                        <strong id="originalCard"></strong>
                    </div>

                    <br>

                    <div>
                        <span data-en="Card scanned:"
                              data-zh="扫描的卡：">
                            Card scanned:
                        </span>

                        <br>

                        <strong id="differentCard"></strong>
                    </div>

                    <br>

                    <span data-en="Do you want to continue with this card?"
                          data-zh="是否要使用这张卡继续？">
                        Do you want to continue with this card?
                    </span>

                </div>

            </div>

            <div class="button-row">

                <button class="action-button"
                        onclick="continueDifferentCard()">

                    <span data-en="Continue"
                          data-zh="继续">
                        Continue
                    </span>

                </button>

                <button class="action-button"
                        onclick="goBack()">

                    <span data-en="Back"
                          data-zh="返回">
                        Back
                    </span>

                </button>

            </div>

            <div class="countdown"
                 id="differentCountdown"></div>

        </div>

    </div>


    <div class="screen" id="balanceScreen">

        <div class="topbar">
            <div class="logo">
                <span data-en="Balance"
                      data-zh="余额">
                    Balance
                </span>
            </div>
        </div>

        <div class="content">

            <div class="subtitle"
                 data-en="Card balance"
                 data-zh="卡片余额">
                Card balance
            </div>

            <div class="amount"
                 id="balanceAmount">
                $0.00
            </div>

            <div class="card-number"
                 id="balanceCard">
            </div>

            <div class="countdown"
                 id="balanceCountdown">
            </div>

        </div>

    </div>


    <div class="screen" id="storeScreen">

        <div class="topbar">

            <div class="logo">
                <span data-en="Store"
                      data-zh="商店">
                    Store
                </span>
            </div>

        </div>

        <div class="content">

            <div class="subtitle">

                <span data-en="Available Balance"
                      data-zh="可用余额">
                    Available Balance
                </span>

            </div>

            <div class="amount"
                 id="storeBalance">
                $0.00
            </div>

            <div class="product-grid"
                 id="products">
            </div>

            <button class="back-button"
                    onclick="goBack()"
                    style="margin-top:20px">

                <span data-en="Back"
                      data-zh="返回">
                    Back
                </span>

            </button>

        </div>

    </div>


    <div class="screen" id="successScreen">

        <div class="content">

            <div class="title success"
                 id="successTitle">
                Complete
            </div>

            <div class="subtitle"
                 id="successMessage">
            </div>

            <div class="amount"
                 id="successAmount">
            </div>

            <div class="subtitle"
                 id="successBalance">
            </div>

            <div class="countdown"
                 id="successCountdown">
            </div>

        </div>

    </div>


    <div class="screen" id="errorScreen">

        <div class="content">

            <div class="title"
                 data-en="Unable to continue"
                 data-zh="无法继续">
                Unable to continue
            </div>

            <div class="subtitle"
                 id="errorMessage">
            </div>

            <button class="back-button"
                    onclick="goHome()">

                <span data-en="Back"
                      data-zh="返回">
                    Back
                </span>

            </button>

        </div>

    </div>

    <div class="status" id="status">
        ● READY
    </div>

</div>


<script>
const KIOSK_ID = "{{ KIOSK_ID }}";
const videos = {{ videos | tojson }};

let language = "en";
let currentMode = null;
let currentCard = null;
let pendingDifferentCard = null;
let chargeTimer = null;
let countdownTimer = null;

const scanner = document.getElementById("scanner");
const video = document.getElementById("backgroundVideo");


function setLanguageText() {
    document.querySelectorAll("[data-en]").forEach(element => {
        element.textContent =
            language === "en"
                ? element.dataset.en
                : element.dataset.zh;
    });

    const languageText = language === "en" ? "中文" : "English";

    document.querySelectorAll(
        "#languageButton, #languageButton2, #languageButton3"
    ).forEach(element => {
        element.textContent = languageText;
    });

    document.documentElement.lang =
        language === "en" ? "en" : "zh-CN";
}


function toggleLanguage() {
    language = language === "en" ? "zh" : "en";
    setLanguageText();
}


function showScreen(id) {
    document.querySelectorAll(".screen").forEach(screen => {
        screen.classList.remove("active");
    });

    document.getElementById(id).classList.add("active");

    clearInterval(countdownTimer);

    setLanguageText();
}


function focusScanner() {
    scanner.value = "";

    setTimeout(() => {
        scanner.focus();
    }, 50);
}


setInterval(() => {
    const active = document.querySelector(".screen.active");

    if (
        active &&
        (
            active.id === "scanScreen" ||
            active.id === "confirmCardScreen"
        )
    ) {
        if (document.activeElement !== scanner) {
            scanner.focus();
        }
    }
}, 300);


scanner.addEventListener("keydown", async event => {
    if (event.key !== "Enter") {
        return;
    }

    event.preventDefault();

    const cardId = scanner.value.trim();

    if (!cardId) {
        return;
    }

    scanner.value = "";

    await handleCardScan(cardId);
});


async function handleCardScan(cardId) {

    /*
        CHARGE FLOW

        First scan:
        Scan screen -> create charge session -> charge screen

        Second scan:
        Confirm screen -> confirm the same card -> transfer money

        Different card:
        Show warning -> user chooses Continue -> scan that card again
    */

    if (currentMode === "charge") {

        const confirmScreen =
            document.getElementById("confirmCardScreen")
                .classList.contains("active");

        if (confirmScreen) {
            await confirmChargeCard(cardId);
            return;
        }

        const chargeScreen =
            document.getElementById("chargeScreen")
                .classList.contains("active");

        if (chargeScreen) {
            await beginCardUpdate();
            return;
        }
    }

    /*
        DIFFERENT CARD CONFIRMATION
    */

    if (currentMode === "different-confirm") {
        await confirmDifferentCard(cardId);
        return;
    }

    /*
        NORMAL FIRST CARD SCAN
    */

    await firstCardScan(cardId);
}


async function firstCardScan(cardId) {

    currentCard = cardId;

    try {

        const response = await fetch("/api/kiosk/scan", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                kiosk_id: KIOSK_ID,
                card_id: cardId,
                mode: currentMode
            })
        });

        const data = await response.json();

        if (!response.ok || !data.success) {
            showError(
                data.message ||
                (
                    language === "en"
                        ? "Unable to scan card."
                        : "无法扫描卡片。"
                )
            );
            return;
        }

        if (currentMode === "charge") {

            document.getElementById("chargeCardId")
                .textContent = data.card_id;

            document.getElementById("chargeAmount")
                .textContent = "$0.00";

            document.getElementById("chargeCoins")
                .textContent = "0";

            document.getElementById("chargeBalance")
                .textContent = data.balance;

            document.getElementById("chargeInstruction")
                .textContent =
                    language === "en"
                        ? "Insert coins"
                        : "请投入硬币";

            showScreen("chargeScreen");

            startChargePolling();

            return;
        }


        if (currentMode === "balance") {

            document.getElementById("balanceAmount")
                .textContent = data.balance;

            document.getElementById("balanceCard")
                .textContent = data.card_id;

            showScreen("balanceScreen");

            startCountdown(
                10,
                "balanceCountdown",
                goHome
            );

            return;
        }


        if (currentMode === "store") {

            document.getElementById("storeBalance")
                .textContent = data.balance;

            await loadProducts();

            showScreen("storeScreen");

            return;
        }

    } catch (error) {

        showError(
            language === "en"
                ? "Unable to connect to the server."
                : "无法连接到服务器。"
        );
    }
}


function startMode(mode) {

    currentMode = mode;
    currentCard = null;
    pendingDifferentCard = null;

    if (mode === "charge") {
        document.getElementById("scanModeTitle")
            .textContent =
                language === "en"
                    ? "Charge Card"
                    : "充值卡";
    }

    if (mode === "balance") {
        document.getElementById("scanModeTitle")
            .textContent =
                language === "en"
                    ? "Check Balance"
                    : "查询余额";
    }

    if (mode === "store") {
        document.getElementById("scanModeTitle")
            .textContent =
                language === "en"
                    ? "Store"
                    : "商店";
    }

    document.getElementById("scanTitle")
        .textContent =
            language === "en"
                ? "Please scan your card"
                : "请扫描您的卡";

    document.getElementById("scanSubtitle")
        .textContent =
            language === "en"
                ? "Use the card reader to continue"
                : "请使用读卡器继续";

    document.getElementById("scanText")
        .textContent =
            language === "en"
                ? "Waiting for card"
                : "等待刷卡";

    showScreen("scanScreen");

    focusScanner();

    startCountdown(
        30,
        "scanCountdown",
        goHome
    );
}


function startChargePolling() {

    clearInterval(chargeTimer);

    chargeTimer = setInterval(async () => {

        try {

            const response = await fetch(
                "/api/kiosk/state?kiosk_id=" +
                encodeURIComponent(KIOSK_ID)
            );

            const data = await response.json();

            if (!data.active) {
                clearInterval(chargeTimer);
                return;
            }

            document.getElementById("chargeAmount")
                .textContent = data.amount;

            document.getElementById("chargeCoins")
                .textContent = data.coin_count;

            document.getElementById("chargeBalance")
                .textContent = data.balance;

            if (data.amount_cents > 0) {

                document.getElementById("chargeInstruction")
                    .textContent =
                        language === "en"
                            ? "Tap your card again to update it"
                            : "请再次刷卡以更新余额";
            }

        } catch (error) {
            console.log("Charge state update failed");
        }

    }, 500);
}


/*
    Called when the user scans again after
    inserting coins.
*/
async function beginCardUpdate() {

    clearInterval(chargeTimer);

    try {

        const response = await fetch(
            "/api/kiosk/state?kiosk_id=" +
            encodeURIComponent(KIOSK_ID)
        );

        const data = await response.json();

        if (!data.active) {

            showError(
                language === "en"
                    ? "The charge session has ended."
                    : "充值会话已结束。"
            );

            return;
        }

        if (data.amount_cents <= 0) {

            showError(
                language === "en"
                    ? "Please insert coins first."
                    : "请先投入硬币。"
            );

            startCountdown(
                5,
                "scanCountdown",
                () => {
                    showScreen("chargeScreen");
                    startChargePolling();
                }
            );

            return;
        }

        showScreen("confirmCardScreen");

        document.querySelector(
            "#confirmCardScreen .title"
        ).textContent =
            language === "en"
                ? "Tap your card again"
                : "请再次刷卡";

        document.querySelector(
            "#confirmCardScreen .subtitle"
        ).textContent =
            language === "en"
                ? "Tap the card used for this charge."
                : "请刷入本次充值使用的卡。";

        focusScanner();

        startCountdown(
            30,
            "confirmCountdown",
            cancelCharge
        );

    } catch (error) {

        showError(
            language === "en"
                ? "Unable to check the charge."
                : "无法检查充值状态。"
        );
    }
}


/*
    The second scan.
*/
async function confirmChargeCard(cardId) {

    try {

        const response = await fetch(
            "/api/kiosk/confirm-charge",
            {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({
                    kiosk_id: KIOSK_ID,
                    card_id: cardId
                })
            }
        );

        const data = await response.json();

        /*
            SAME CARD
        */

        if (response.ok && data.success) {

            currentCard = data.card_id;

            showSuccess(
                language === "en"
                    ? "Card updated successfully"
                    : "卡片更新成功",
                data.added,
                language === "en"
                    ? "New balance: " + data.balance
                    : "新余额：" + data.balance
            );

            return;
        }


        /*
            DIFFERENT CARD
        */

        if (data.different_card) {

            pendingDifferentCard = data.scanned_card;

            document.getElementById("originalCard")
                .textContent = data.original_card;

            document.getElementById("differentCard")
                .textContent = data.scanned_card;

            showScreen("differentCardScreen");

            startCountdown(
                20,
                "differentCountdown",
                cancelCharge
            );

            return;
        }


        showError(
            data.message ||
            (
                language === "en"
                    ? "Unable to update card."
                    : "无法更新卡片。"
            )
        );

    } catch (error) {

        showError(
            language === "en"
                ? "Unable to connect to the server."
                : "无法连接到服务器。"
        );
    }
}


/*
    User chose CONTINUE after
    a different card was detected.
*/
function continueDifferentCard() {

    if (!pendingDifferentCard) {
        return;
    }

    currentMode = "different-confirm";

    showScreen("confirmCardScreen");

    document.querySelector(
        "#confirmCardScreen .title"
    ).textContent =
        language === "en"
            ? "Tap the new card again"
            : "请再次刷这张新卡";

    document.querySelector(
        "#confirmCardScreen .subtitle"
    ).textContent =
        language === "en"
            ? "Tap the same card again to confirm."
            : "请再次刷同一张卡以确认。";

    focusScanner();

    startCountdown(
        30,
        "confirmCountdown",
        cancelCharge
    );
}


/*
    Confirms the DIFFERENT card twice.
*/
async function confirmDifferentCard(cardId) {

    if (cardId !== pendingDifferentCard) {

        document.getElementById("originalCard")
            .textContent = pendingDifferentCard;

        document.getElementById("differentCard")
            .textContent = cardId;

        showScreen("differentCardScreen");

        startCountdown(
            20,
            "differentCountdown",
            cancelCharge
        );

        return;
    }

    try {

        const response = await fetch(
            "/api/kiosk/confirm-different-card",
            {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({
                    kiosk_id: KIOSK_ID,
                    card_id: cardId
                })
            }
        );

        const data = await response.json();

        if (!response.ok || !data.success) {

            showError(
                data.message ||
                (
                    language === "en"
                        ? "Unable to update card."
                        : "无法更新卡片。"
                )
            );

            return;
        }

        currentCard = data.card_id;

        showSuccess(
            language === "en"
                ? "Card updated successfully"
                : "卡片更新成功",
            data.added,
            language === "en"
                ? "New balance: " + data.balance
                : "新余额：" + data.balance
        );

    } catch (error) {

        showError(
            language === "en"
                ? "Unable to connect to the server."
                : "无法连接到服务器。"
        );
    }
}


async function loadProducts() {

    const container =
        document.getElementById("products");

    container.innerHTML = "";

    try {

        const response =
            await fetch("/api/store/products");

        const products =
            await response.json();

        products.forEach(product => {

            const card =
                document.createElement("div");

            card.className = "product";

            card.innerHTML = `
                <div class="product-name">
                    ${escapeHtml(product.name)}
                </div>

                <div class="product-price">
                    ${escapeHtml(product.price)}
                </div>

                <button>
                    ${language === "en" ? "BUY" : "购买"}
                </button>
            `;

            card.querySelector("button")
                .addEventListener(
                    "click",
                    () => buyProduct(product.id)
                );

            container.appendChild(card);
        });

    } catch (error) {

        container.innerHTML = `
            <div class="subtitle">
                ${
                    language === "en"
                        ? "Unable to load products."
                        : "无法加载商品。"
                }
            </div>
        `;
    }
}


async function buyProduct(productId) {

    const buttons =
        document.querySelectorAll(".product button");

    buttons.forEach(button => {
        button.disabled = true;
    });

    try {

        const response =
            await fetch("/api/store/buy", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({
                    kiosk_id: KIOSK_ID,
                    card_id: currentCard,
                    product_id: productId
                })
            });

        const data =
            await response.json();

        if (data.success) {

            showSuccess(
                language === "en"
                    ? "Purchase complete"
                    : "购买完成",
                data.price,
                language === "en"
                    ? "Remaining balance: " + data.balance
                    : "剩余余额：" + data.balance
            );

            return;
        }

        buttons.forEach(button => {
            button.disabled = false;
        });

        if (data.insufficient) {

            showError(
                language === "en"
                    ? "Insufficient balance. You have " +
                      data.balance +
                      " but need " +
                      data.required + "."
                    : "余额不足。当前余额 " +
                      data.balance +
                      "，需要 " +
                      data.required + "。"
            );

            return;
        }

        showError(data.message || "Purchase failed");

    } catch (error) {

        buttons.forEach(button => {
            button.disabled = false;
        });

        showError(
            language === "en"
                ? "Unable to connect to the server."
                : "无法连接到服务器。"
        );
    }
}


async function cancelCharge() {

    clearInterval(chargeTimer);
    clearInterval(countdownTimer);

    try {

        await fetch("/api/kiosk/end", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                kiosk_id: KIOSK_ID
            })
        });

    } catch (error) {
    }

    goHome(false);
}


function startCountdown(seconds, elementId, callback) {

    clearInterval(countdownTimer);

    let remaining = seconds;

    const element =
        document.getElementById(elementId);

    if (!element) {
        return;
    }

    function update() {

        if (remaining < 0) {

            clearInterval(countdownTimer);

            if (callback) {
                callback();
            }

            return;
        }

        element.textContent =
            language === "en"
                ? "Returning in " + remaining + " seconds"
                : remaining + " 秒后返回";

        remaining--;
    }

    update();

    countdownTimer =
        setInterval(update, 1000);
}


function showSuccess(title, amount, message) {

    clearInterval(chargeTimer);
    clearInterval(countdownTimer);

    document.getElementById("successTitle")
        .textContent = title;

    document.getElementById("successAmount")
        .textContent = amount;

    document.getElementById("successMessage")
        .textContent =
            language === "en"
                ? "Transaction complete"
                : "交易完成";

    document.getElementById("successBalance")
        .textContent = message;

    showScreen("successScreen");

    startCountdown(
        8,
        "successCountdown",
        () => goHome(false)
    );
}


function showError(message) {

    clearInterval(chargeTimer);

    document.getElementById("errorMessage")
        .textContent = message;

    showScreen("errorScreen");
}


function goBack() {

    clearInterval(countdownTimer);

    if (currentMode === "charge" ||
        currentMode === "different-confirm") {

        cancelCharge();
        return;
    }

    goHome();
}


function goHome(endServerSession = true) {

    clearInterval(chargeTimer);
    clearInterval(countdownTimer);

    currentCard = null;
    pendingDifferentCard = null;
    currentMode = null;

    if (endServerSession) {

        fetch("/api/kiosk/end", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                kiosk_id: KIOSK_ID
            })
        }).catch(() => {});
    }

    showScreen("homeScreen");
}


function escapeHtml(value) {

    return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}


function startBackgroundVideo() {

    if (!videos || videos.length === 0) {
        return;
    }

    let index = 0;

    function loadVideo() {

        video.src = videos[index];
        video.load();

        video.play().catch(() => {});
    }

    video.addEventListener("error", () => {

        index++;

        if (index >= videos.length) {
            video.style.display = "none";
            return;
        }

        loadVideo();
    });

    video.addEventListener("ended", () => {

        index++;

        if (index >= videos.length) {
            index = 0;
        }

        loadVideo();
    });

    loadVideo();
}


document.addEventListener("click", () => {

    const active =
        document.querySelector(".screen.active");

    if (
        active &&
        (
            active.id === "scanScreen" ||
            active.id === "confirmCardScreen"
        )
    ) {
        focusScanner();
    }

}, true);


setLanguageText();
startBackgroundVideo();
</script>

</body>
</html>
"""


ADMIN_LOGIN_HTML = r"""
<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Admin Login</title>
<style>
body {
    margin:0;
    min-height:100vh;
    display:flex;
    justify-content:center;
    align-items:center;
    background:#071018;
    color:white;
    font-family:Arial;
}
form {
    width:350px;
    padding:35px;
    border-radius:25px;
    background:#111d27;
}
input,button {
    width:100%;
    height:55px;
    margin-top:15px;
    border-radius:12px;
    border:0;
    padding:0 15px;
    font-size:18px;
    box-sizing:border-box;
}
button {
    background:#3d8fc4;
    color:white;
    font-weight:bold;
}
</style>
</head>
<body>
<form method="post">
    <h1>Admin</h1>
    <input name="username" placeholder="Username">
    <input name="password" type="password" placeholder="Password">
    <button>Login</button>
</form>
</body>
</html>
"""


ADMIN_HTML = r"""
<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Kiosk Admin</title>
<style>
body {
    margin:0;
    background:#071018;
    color:white;
    font-family:Arial;
    padding:30px;
}
h1 {
    margin-top:0;
}
.panel {
    background:#111d27;
    border-radius:20px;
    padding:25px;
    margin-bottom:25px;
}
table {
    width:100%;
    border-collapse:collapse;
}
th,td {
    padding:12px;
    border-bottom:1px solid #263540;
    text-align:left;
}
.online {
    color:#62d99b;
}
</style>
</head>
<body>

<h1>Kiosk Administration</h1>

<div class="panel">
    <h2>Active Sessions</h2>
    <div id="sessions">Loading...</div>
</div>

<div class="panel">
    <h2>Transactions</h2>
    <div id="transactions">Loading...</div>
</div>

<div class="panel">
    <h2>Coin Events</h2>
    <div id="coins">Loading...</div>
</div>

<script>

function escapeHtml(value) {
    return String(value)
        .replaceAll("&","&amp;")
        .replaceAll("<","&lt;")
        .replaceAll(">","&gt;")
        .replaceAll('"',"&quot;")
        .replaceAll("'","&#039;");
}

async function refresh() {

    try {

        const response =
            await fetch("/api/admin/live");

        const data =
            await response.json();

        let sessions = "";

        if (!data.sessions.length) {
            sessions = "No active sessions.";
        } else {

            sessions = `
            <table>
            <tr>
                <th>Kiosk</th>
                <th>Card</th>
                <th>Mode</th>
                <th>Coins</th>
                <th>Amount</th>
                <th>Updated</th>
            </tr>
            `;

            data.sessions.forEach(row => {

                sessions += `
                <tr>
                    <td>${escapeHtml(row.kiosk_id)}</td>
                    <td>${escapeHtml(row.card_id)}</td>
                    <td>${escapeHtml(row.mode)}</td>
                    <td>${row.coin_count}</td>
                    <td>${escapeHtml(row.amount)}</td>
                    <td>${escapeHtml(row.updated_at)}</td>
                </tr>
                `;
            });

            sessions += "</table>";
        }

        document.getElementById("sessions")
            .innerHTML = sessions;


        let transactions = `
        <table>
        <tr>
            <th>Time</th>
            <th>Kiosk</th>
            <th>Card</th>
            <th>Type</th>
            <th>Amount</th>
            <th>Coins</th>
            <th>Description</th>
        </tr>
        `;

        data.transactions.forEach(row => {

            transactions += `
            <tr>
                <td>${escapeHtml(row.time)}</td>
                <td>${escapeHtml(row.kiosk)}</td>
                <td>${escapeHtml(row.card)}</td>
                <td>${escapeHtml(row.type)}</td>
                <td>${escapeHtml(row.amount)}</td>
                <td>${row.coins}</td>
                <td>${escapeHtml(row.description)}</td>
            </tr>
            `;
        });

        transactions += "</table>";

        document.getElementById("transactions")
            .innerHTML = transactions;


        let coins = `
        <table>
        <tr>
            <th>Time</th>
            <th>Kiosk</th>
            <th>Card</th>
            <th>Accepted</th>
            <th>Coins</th>
            <th>Amount</th>
            <th>Reason</th>
        </tr>
        `;

        data.coins.forEach(row => {

            coins += `
            <tr>
                <td>${escapeHtml(row.time)}</td>
                <td>${escapeHtml(row.kiosk)}</td>
                <td>${escapeHtml(row.card)}</td>
                <td>${row.accepted ? "YES" : "NO"}</td>
                <td>${row.coins}</td>
                <td>${escapeHtml(row.amount)}</td>
                <td>${escapeHtml(row.reason)}</td>
            </tr>
            `;
        });

        coins += "</table>";

        document.getElementById("coins")
            .innerHTML = coins;

    } catch (error) {

        document.getElementById("sessions")
            .textContent = "Unable to load data.";
    }
}

refresh();

setInterval(refresh, 1000);

</script>

</body>
</html>
"""


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=False
    )
