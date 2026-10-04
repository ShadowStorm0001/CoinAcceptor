from flask import Flask, request, jsonify, render_template_string, redirect, session
import sqlite3
from datetime import datetime

app = Flask(__name__)
app.secret_key = "service-kiosk-secret"

DB_FILE = "kiosk.db"

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin"

KIOSK_ID = "KIOSK-01"

# One coin pulse = $1.00
COIN_VALUE_CENTS = 100


# ============================================================
# DATABASE
# ============================================================

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def init_database():
    conn = get_db()
    cur = conn.cursor()

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
            card_id TEXT,
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
            kiosk_id TEXT,
            card_id TEXT,
            type TEXT NOT NULL,
            amount_cents INTEGER NOT NULL DEFAULT 0,
            coins INTEGER NOT NULL DEFAULT 0,
            description TEXT,
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
            reason TEXT,
            created_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            price_cents INTEGER NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        )
    """)

    if cur.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0:
        cur.execute(
            "INSERT INTO products (name, price_cents) VALUES (?, ?)",
            ("Service 1", 200)
        )
        cur.execute(
            "INSERT INTO products (name, price_cents) VALUES (?, ?)",
            ("Service 2", 500)
        )
        cur.execute(
            "INSERT INTO products (name, price_cents) VALUES (?, ?)",
            ("Service 3", 1000)
        )

    conn.commit()
    conn.close()


init_database()


# ============================================================
# HELPERS
# ============================================================

def money(cents):
    return f"${cents / 100:.2f}"


def create_card(conn, card_id):
    card_id = str(card_id).strip()

    if not card_id:
        return None

    card = conn.execute(
        "SELECT * FROM cards WHERE card_id = ?",
        (card_id,)
    ).fetchone()

    if card is None:
        t = timestamp()

        conn.execute("""
            INSERT INTO cards
            (card_id, balance_cents, created_at, updated_at)
            VALUES (?, 0, ?, ?)
        """, (card_id, t, t))

        conn.commit()

        card = conn.execute(
            "SELECT * FROM cards WHERE card_id = ?",
            (card_id,)
        ).fetchone()

    return card


def close_sessions(conn, kiosk_id):
    conn.execute("""
        UPDATE sessions
        SET status = 'closed',
            updated_at = ?
        WHERE kiosk_id = ?
        AND status = 'active'
    """, (timestamp(), kiosk_id))


def active_session(conn, kiosk_id):
    return conn.execute("""
        SELECT *
        FROM sessions
        WHERE kiosk_id = ?
        AND status = 'active'
        ORDER BY id DESC
        LIMIT 1
    """, (kiosk_id,)).fetchone()


# ============================================================
# KIOSK HTML
# ============================================================

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

<title>Service Kiosk</title>

<style>

* {
    box-sizing: border-box;
}

body {
    margin: 0;
    background: #0e1014;
    color: white;
    font-family: Arial, Helvetica, sans-serif;
    min-height: 100vh;
}

.header {
    height: 76px;
    background: #171a20;
    border-bottom: 1px solid #292d35;

    display: flex;
    align-items: center;
    justify-content: space-between;

    padding: 0 32px;
}

.logo {
    font-size: 25px;
    font-weight: 800;
}

.kiosk-id {
    color: #8e96a3;
    font-size: 14px;
}

.container {
    width: min(1100px, 94%);
    margin: 40px auto;
}

.screen {
    display: none;
}

.screen.active {
    display: block;
}

.title {
    font-size: 42px;
    font-weight: 800;
    margin-bottom: 8px;
}

.subtitle {
    font-size: 19px;
    color: #8f97a5;
    margin-bottom: 35px;
}

.menu {
    display: grid;
    grid-template-columns:
        repeat(3, 1fr);

    gap: 22px;
}

.menu-button {
    min-height: 230px;

    border: 1px solid #303641;
    border-radius: 22px;

    background: #181b22;
    color: white;

    cursor: pointer;

    transition:
        transform .15s,
        background .15s;
}

.menu-button:hover {
    transform: translateY(-4px);
    background: #20242c;
}

.menu-icon {
    font-size: 58px;
    margin-bottom: 20px;
}

.menu-name {
    font-size: 25px;
    font-weight: 800;
}

.menu-description {
    color: #8f97a5;
    margin-top: 10px;
    font-size: 15px;
}

.back {
    border: none;
    background: #252a33;
    color: white;

    padding: 13px 22px;
    border-radius: 12px;

    font-size: 16px;
    cursor: pointer;

    margin-bottom: 25px;
}

.back:hover {
    background: #303641;
}

.scan-card {
    width: min(700px, 100%);
    margin: 50px auto;

    text-align: center;

    background: #181b22;
    border: 1px solid #303641;
    border-radius: 25px;

    padding: 55px 30px;
}

.scan-icon {
    font-size: 75px;
    margin-bottom: 20px;
}

.scan-title {
    font-size: 32px;
    font-weight: 800;
}

.scan-description {
    margin-top: 12px;
    color: #8f97a5;
    font-size: 18px;
}

.scanner-input {
    position: absolute;
    left: -9999px;
    top: -9999px;

    opacity: 0;
}

.card-box {
    background: #181b22;
    border: 1px solid #303641;
    border-radius: 22px;

    padding: 30px;
}

.card-id {
    color: #8f97a5;
    font-size: 15px;
}

.balance {
    font-size: 58px;
    font-weight: 900;
    margin: 10px 0 25px;
}

.coin-panel {
    display: grid;
    grid-template-columns:
        repeat(3, 1fr);

    gap: 15px;
}

.info {
    background: #111318;
    border-radius: 15px;
    padding: 20px;
}

.info-label {
    color: #8f97a5;
    font-size: 14px;
}

.info-value {
    font-size: 27px;
    font-weight: 800;
    margin-top: 7px;
}

.done {
    width: 100%;
    margin-top: 25px;

    padding: 18px;

    border: none;
    border-radius: 14px;

    background: #ffffff;
    color: #111318;

    font-size: 18px;
    font-weight: 800;

    cursor: pointer;
}

.done:hover {
    background: #dddddd;
}

.products {
    display: grid;
    grid-template-columns:
        repeat(3, 1fr);

    gap: 18px;
}

.product {
    background: #181b22;
    border: 1px solid #303641;

    border-radius: 18px;
    padding: 25px;
}

.product-name {
    font-size: 22px;
    font-weight: 800;
}

.product-price {
    font-size: 30px;
    font-weight: 900;

    margin: 15px 0;
}

.buy {
    width: 100%;

    padding: 13px;

    border: none;
    border-radius: 10px;

    background: white;
    color: #111318;

    font-weight: 800;
    cursor: pointer;
}

.buy:disabled {
    opacity: .35;
    cursor: not-allowed;
}

.status {
    position: fixed;

    left: 50%;
    bottom: 25px;

    transform: translateX(-50%);

    padding: 14px 25px;

    background: #242932;
    border: 1px solid #383e49;

    border-radius: 14px;

    display: none;

    z-index: 100;
}

.status.show {
    display: block;
}

.status.error {
    background: #4b1f25;
}

.status.success {
    background: #1e4931;
}

@media(max-width: 750px) {

    .menu {
        grid-template-columns: 1fr;
    }

    .menu-button {
        min-height: 150px;
    }

    .coin-panel {
        grid-template-columns: 1fr;
    }

    .products {
        grid-template-columns: 1fr;
    }

    .title {
        font-size: 32px;
    }

}

</style>

</head>

<body>

<header class="header">

    <div class="logo">
        SERVICE KIOSK
    </div>

    <div class="kiosk-id">
        {{ kiosk_id }}
    </div>

</header>


<div class="container">

    <!-- HOME -->

    <section id="home" class="screen active">

        <div class="title">
            What would you like to do?
        </div>

        <div class="subtitle">
            Select a service below.
        </div>

        <div class="menu">

            <button
                class="menu-button"
                onclick="startMode('charge')">

                <div class="menu-icon">
                    💳
                </div>

                <div class="menu-name">
                    Charge Card
                </div>

                <div class="menu-description">
                    Add money to your card
                </div>

            </button>


            <button
                class="menu-button"
                onclick="startMode('balance')">

                <div class="menu-icon">
                    💰
                </div>

                <div class="menu-name">
                    Check Balance
                </div>

                <div class="menu-description">
                    View your card balance
                </div>

            </button>


            <button
                class="menu-button"
                onclick="startMode('store')">

                <div class="menu-icon">
                    🛒
                </div>

                <div class="menu-name">
                    Store
                </div>

                <div class="menu-description">
                    Purchase available services
                </div>

            </button>

        </div>

    </section>


    <!-- SCAN -->

    <section id="scan" class="screen">

        <button class="back"
                onclick="goHome()">
            ← Back
        </button>

        <div class="scan-card">

            <div class="scan-icon">
                💳
            </div>

            <div class="scan-title">
                Scan your card
            </div>

            <div class="scan-description">
                Place your card on the card reader.
            </div>

            <input
                id="scannerInput"
                class="scanner-input"
                autocomplete="off"
                autofocus>

        </div>

    </section>


    <!-- CHARGE -->

    <section id="charge" class="screen">

        <button class="back"
                onclick="goHome()">
            ← Back
        </button>

        <div class="title">
            Charge Card
        </div>

        <div class="subtitle">
            Insert coins to add money to this card.
        </div>

        <div class="card-box">

            <div class="card-id">
                Card: <span id="chargeCardId">---</span>
            </div>

            <div
                class="balance"
                id="chargeBalance">
                $0.00
            </div>

            <div class="coin-panel">

                <div class="info">

                    <div class="info-label">
                        Coins inserted
                    </div>

                    <div
                        class="info-value"
                        id="coinCount">
                        0
                    </div>

                </div>


                <div class="info">

                    <div class="info-label">
                        Added
                    </div>

                    <div
                        class="info-value"
                        id="coinAmount">
                        $0.00
                    </div>

                </div>


                <div class="info">

                    <div class="info-label">
                        New balance
                    </div>

                    <div
                        class="info-value"
                        id="newBalance">
                        $0.00
                    </div>

                </div>

            </div>


            <button
                class="done"
                onclick="finishSession()">

                FINISHED

            </button>

        </div>

    </section>


    <!-- BALANCE -->

    <section id="balance" class="screen">

        <button class="back"
                onclick="goHome()">
            ← Back
        </button>

        <div class="title">
            Card Balance
        </div>

        <div class="subtitle">
            Current balance
        </div>

        <div class="card-box">

            <div class="card-id">
                Card: <span id="balanceCardId">---</span>
            </div>

            <div
                class="balance"
                id="balanceAmount">
                $0.00
            </div>

        </div>

    </section>


    <!-- STORE -->

    <section id="store" class="screen">

        <button class="back"
                onclick="goHome()">
            ← Back
        </button>

        <div class="title">
            Store
        </div>

        <div class="subtitle">

            Balance:
            <strong id="storeBalance">
                $0.00
            </strong>

        </div>

        <div
            id="products"
            class="products">
        </div>

    </section>

</div>


<div id="status"
     class="status">
</div>


<script>

const kioskId = {{ kiosk_id|tojson }};

let currentMode = null;
let currentCard = null;

let scannerTimer = null;


function showScreen(name) {

    document
        .querySelectorAll(".screen")
        .forEach(screen => {
            screen.classList.remove("active");
        });

    document
        .getElementById(name)
        .classList.add("active");
}


function showStatus(message, type="") {

    const box = document.getElementById("status");

    box.innerText = message;

    box.className = "status show " + type;

    setTimeout(() => {
        box.className = "status";
    }, 3000);
}


function focusScanner() {

    const input =
        document.getElementById("scannerInput");

    input.value = "";

    input.focus();

}


function startMode(mode) {

    currentMode = mode;
    currentCard = null;

    showScreen("scan");

    setTimeout(() => {
        focusScanner();
    }, 100);

}


function goHome() {

    fetch("/api/kiosk/end", {
        method: "POST",

        headers: {
            "Content-Type": "application/json"
        },

        body: JSON.stringify({
            kiosk_id: kioskId
        })
    }).catch(() => {});

    currentMode = null;
    currentCard = null;

    showScreen("home");

}


async function scanCard(cardId) {

    cardId = cardId.trim();

    if (!cardId) {
        return;
    }

    try {

        const response =
            await fetch("/api/kiosk/scan", {

                method: "POST",

                headers: {
                    "Content-Type": "application/json"
                },

                body: JSON.stringify({
                    kiosk_id: kioskId,
                    card_id: cardId,
                    mode: currentMode
                })

            });

        const data =
            await response.json();

        if (!response.ok) {

            showStatus(
                data.error || "Card could not be scanned.",
                "error"
            );

            focusScanner();

            return;
        }

        currentCard = cardId;

        if (currentMode === "charge") {

            document
                .getElementById("chargeCardId")
                .innerText = cardId;

            updateCharge(data);

            showScreen("charge");

        }

        else if (currentMode === "balance") {

            document
                .getElementById("balanceCardId")
                .innerText = cardId;

            document
                .getElementById("balanceAmount")
                .innerText =
                data.balance;

            showScreen("balance");

        }

        else if (currentMode === "store") {

            updateStore(data);

            showScreen("store");

        }

    } catch (error) {

        showStatus(
            "Could not connect to the service.",
            "error"
        );

        focusScanner();

    }

}


document
    .getElementById("scannerInput")
    .addEventListener("keydown", function(event) {

        if (event.key === "Enter") {

            event.preventDefault();

            const card =
                this.value.trim();

            this.value = "";

            scanCard(card);

        }

    });


function updateCharge(data) {

    document
        .getElementById("chargeBalance")
        .innerText = data.balance;

    document
        .getElementById("coinCount")
        .innerText = data.coin_count;

    document
        .getElementById("coinAmount")
        .innerText = data.added;

    document
        .getElementById("newBalance")
        .innerText = data.balance;

}


function updateStore(data) {

    document
        .getElementById("storeBalance")
        .innerText = data.balance;

    const container =
        document.getElementById("products");

    container.innerHTML = "";

    data.products.forEach(product => {

        const item =
            document.createElement("div");

        item.className = "product";

        item.innerHTML = `

            <div class="product-name">
                ${escapeHtml(product.name)}
            </div>

            <div class="product-price">
                ${product.price}
            </div>

            <button
                class="buy"
                ${product.price_cents > data.balance_cents ? "disabled" : ""}
                onclick="buyProduct(${product.id})">

                ${product.price_cents > data.balance_cents
                    ? "Insufficient Balance"
                    : "Purchase"}

            </button>

        `;

        container.appendChild(item);

    });

}


async function buyProduct(productId) {

    try {

        const response =
            await fetch("/api/store/buy", {

                method: "POST",

                headers: {
                    "Content-Type": "application/json"
                },

                body: JSON.stringify({

                    kiosk_id: kioskId,

                    card_id: currentCard,

                    product_id: productId

                })

            });

        const data =
            await response.json();

        if (!response.ok) {

            showStatus(
                data.error || "Purchase failed.",
                "error"
            );

            return;
        }

        updateStore(data);

        showStatus(
            "Purchase successful.",
            "success"
        );

    } catch {

        showStatus(
            "Could not complete purchase.",
            "error"
        );

    }

}


async function finishSession() {

    await fetch("/api/kiosk/end", {

        method: "POST",

        headers: {
            "Content-Type": "application/json"
        },

        body: JSON.stringify({
            kiosk_id: kioskId
        })

    }).catch(() => {});

    currentMode = null;
    currentCard = null;

    showScreen("home");

}


function escapeHtml(value) {

    return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");

}


async function updateLive() {

    if (
        currentMode !== "charge" ||
        !currentCard
    ) {
        return;
    }

    try {

        const response =
            await fetch(
                "/api/kiosk/state?kiosk_id="
                + encodeURIComponent(kioskId)
            );

        const data =
            await response.json();

        if (!data.session) {
            return;
        }

        updateCharge(data);

    } catch {}

}


setInterval(updateLive, 500);


window.addEventListener(
    "click",
    () => {

        if (
            currentMode === null
        ) {
            return;
        }

    }
);

</script>

</body>
</html>
"""


# ============================================================
# KIOSK ROUTE
# ============================================================

@app.route("/")
def kiosk():
    return render_template_string(
        KIOSK_HTML,
        kiosk_id=KIOSK_ID
    )


@app.route("/kiosk")
def kiosk_custom():
    kiosk_id = request.args.get(
        "kiosk_id",
        KIOSK_ID
    )

    return render_template_string(
        KIOSK_HTML,
        kiosk_id=kiosk_id
    )


# ============================================================
# CARD SCANNING
# ============================================================

@app.route("/api/kiosk/scan", methods=["POST"])
def scan_card():

    data = request.get_json(
        silent=True
    ) or {}

    kiosk_id = str(
        data.get("kiosk_id", KIOSK_ID)
    ).strip()

    card_id = str(
        data.get("card_id", "")
    ).strip()

    mode = str(
        data.get("mode", "")
    ).strip().lower()

    if not card_id:
        return jsonify({
            "error": "No card ID was received."
        }), 400

    if mode not in (
        "charge",
        "balance",
        "store"
    ):
        return jsonify({
            "error": "Invalid operation."
        }), 400

    conn = get_db()

    card = create_card(
        conn,
        card_id
    )

    close_sessions(
        conn,
        kiosk_id
    )

    if mode == "charge":

        t = timestamp()

        conn.execute("""
            INSERT INTO sessions
            (
                kiosk_id,
                card_id,
                mode,
                status,
                coin_count,
                amount_cents,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, 0, 0, ?, ?)
        """, (
            kiosk_id,
            card_id,
            "charge",
            "active",
            t,
            t
        ))

        conn.commit()

    balance_cents = card["balance_cents"]

    products = []

    if mode == "store":

        rows = conn.execute("""
            SELECT id, name, price_cents
            FROM products
            WHERE active = 1
            ORDER BY id
        """).fetchall()

        for product in rows:

            products.append({
                "id": product["id"],
                "name": product["name"],
                "price_cents": product["price_cents"],
                "price": money(
                    product["price_cents"]
                )
            })

    conn.close()

    result = {
        "success": True,
        "card_id": card_id,
        "balance_cents": balance_cents,
        "balance": money(balance_cents)
    }

    if mode == "charge":

        result.update({
            "coin_count": 0,
            "added_cents": 0,
            "added": "$0.00"
        })

    if mode == "store":
        result["products"] = products

    return jsonify(result)


# ============================================================
# LIVE KIOSK STATE
# ============================================================

@app.route("/api/kiosk/state")
def kiosk_state():

    kiosk_id = request.args.get(
        "kiosk_id",
        KIOSK_ID
    )

    conn = get_db()

    current = active_session(
        conn,
        kiosk_id
    )

    if current is None:

        conn.close()

        return jsonify({
            "session": None
        })

    card = conn.execute("""
        SELECT *
        FROM cards
        WHERE card_id = ?
    """, (
        current["card_id"],
    )).fetchone()

    conn.close()

    return jsonify({

        "session": True,

        "card_id":
            current["card_id"],

        "coin_count":
            current["coin_count"],

        "added_cents":
            current["amount_cents"],

        "added":
            money(current["amount_cents"]),

        "balance_cents":
            card["balance_cents"],

        "balance":
            money(card["balance_cents"])

    })


# ============================================================
# COIN ACCEPTOR
# ============================================================

@app.route("/api/coin", methods=["POST"])
def coin():

    data = request.get_json(
        silent=True
    ) or {}

    kiosk_id = str(
        data.get("kiosk_id", KIOSK_ID)
    ).strip()

    # Number of pulses/coins sent by the Pi
    coins = int(
        data.get("coins", 1)
    )

    if coins < 1:
        coins = 1

    amount = coins * COIN_VALUE_CENTS

    conn = get_db()

    current = active_session(
        conn,
        kiosk_id
    )

    # Every physical coin event is recorded.
    # It is NOT silently discarded.
    # It only becomes card credit when a charge
    # session is active.

    if current is None:

        conn.execute("""
            INSERT INTO coin_events
            (
                kiosk_id,
                card_id,
                accepted,
                coins,
                amount_cents,
                reason,
                created_at
            )
            VALUES (?, NULL, 0, ?, ?, ?, ?)
        """, (
            kiosk_id,
            coins,
            amount,
            "NO_ACTIVE_CARD_SESSION",
            timestamp()
        ))

        conn.commit()
        conn.close()

        return jsonify({

            "success": False,

            "accepted": False,

            "coins": coins,

            "amount_cents": amount,

            "amount": money(amount),

            "message":
                "A card must be scanned before charging."

        }), 409

    card_id = current["card_id"]

    card = conn.execute("""
        SELECT *
        FROM cards
        WHERE card_id = ?
    """, (
        card_id,
    )).fetchone()

    new_balance = (
        card["balance_cents"]
        + amount
    )

    new_coin_count = (
        current["coin_count"]
        + coins
    )

    new_session_amount = (
        current["amount_cents"]
        + amount
    )

    t = timestamp()

    conn.execute("""
        UPDATE cards

        SET balance_cents = ?,
            updated_at = ?

        WHERE card_id = ?
    """, (
        new_balance,
        t,
        card_id
    ))

    conn.execute("""
        UPDATE sessions

        SET coin_count = ?,
            amount_cents = ?,
            updated_at = ?

        WHERE id = ?
    """, (
        new_coin_count,
        new_session_amount,
        t,
        current["id"]
    ))

    conn.execute("""
        INSERT INTO coin_events
        (
            kiosk_id,
            card_id,
            accepted,
            coins,
            amount_cents,
            reason,
            created_at
        )
        VALUES (?, ?, 1, ?, ?, ?, ?)
    """, (
        kiosk_id,
        card_id,
        coins,
        amount,
        "CARD_CHARGE",
        t
    ))

    conn.execute("""
        INSERT INTO transactions
        (
            kiosk_id,
            card_id,
            type,
            amount_cents,
            coins,
            description,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        kiosk_id,
        card_id,
        "coin_charge",
        amount,
        coins,
        "Coin charge",
        t
    ))

    conn.commit()
    conn.close()

    return jsonify({

        "success": True,

        "accepted": True,

        "card_id": card_id,

        "coins": coins,

        "amount_cents": amount,

        "amount": money(amount),

        "coin_count":
            new_coin_count,

        "balance_cents":
            new_balance,

        "balance":
            money(new_balance)

    })


# ============================================================
# END KIOSK SESSION
# ============================================================

@app.route("/api/kiosk/end", methods=["POST"])
def end_session():

    data = request.get_json(
        silent=True
    ) or {}

    kiosk_id = str(
        data.get("kiosk_id", KIOSK_ID)
    ).strip()

    conn = get_db()

    close_sessions(
        conn,
        kiosk_id
    )

    conn.commit()
    conn.close()

    return jsonify({
        "success": True
    })


# ============================================================
# STORE
# ============================================================

@app.route("/api/store/buy", methods=["POST"])
def store_buy():

    data = request.get_json(
        silent=True
    ) or {}

    kiosk_id = str(
        data.get("kiosk_id", KIOSK_ID)
    ).strip()

    card_id = str(
        data.get("card_id", "")
    ).strip()

    try:
        product_id = int(
            data.get("product_id")
        )
    except:
        return jsonify({
            "error": "Invalid product."
        }), 400

    if not card_id:
        return jsonify({
            "error": "Scan a card first."
        }), 400

    conn = get_db()

    card = conn.execute("""
        SELECT *
        FROM cards
        WHERE card_id = ?
    """, (
        card_id,
    )).fetchone()

    if card is None:

        conn.close()

        return jsonify({
            "error": "Card not found."
        }), 404

    product = conn.execute("""
        SELECT *
        FROM products
        WHERE id = ?
        AND active = 1
    """, (
        product_id,
    )).fetchone()

    if product is None:

        conn.close()

        return jsonify({
            "error": "Product not found."
        }), 404

    price = product["price_cents"]

    if card["balance_cents"] < price:

        balance = card["balance_cents"]

        conn.close()

        return jsonify({

            "error": "Insufficient balance.",

            "balance_cents": balance,

            "balance":
                money(balance)

        }), 400

    new_balance = (
        card["balance_cents"]
        - price
    )

    t = timestamp()

    conn.execute("""
        UPDATE cards

        SET balance_cents = ?,
            updated_at = ?

        WHERE card_id = ?
    """, (
        new_balance,
        t,
        card_id
    ))

    conn.execute("""
        INSERT INTO transactions
        (
            kiosk_id,
            card_id,
            type,
            amount_cents,
            coins,
            description,
            created_at
        )
        VALUES (?, ?, ?, ?, 0, ?, ?)
    """, (
        kiosk_id,
        card_id,
        "store_purchase",
        -price,
        product["name"],
        t
    ))

    conn.commit()

    products = conn.execute("""
        SELECT *
        FROM products
        WHERE active = 1
        ORDER BY id
    """).fetchall()

    conn.close()

    return jsonify({

        "success": True,

        "balance_cents":
            new_balance,

        "balance":
            money(new_balance),

        "products": [

            {
                "id": p["id"],
                "name": p["name"],
                "price_cents":
                    p["price_cents"],
                "price":
                    money(p["price_cents"])
            }

            for p in products

        ]

    })


# ============================================================
# ADMIN LOGIN
# ============================================================

ADMIN_LOGIN_HTML = r"""
<!DOCTYPE html>
<html>

<head>

<meta charset="UTF-8">

<title>Admin Login</title>

<style>

body {
    margin: 0;

    background: #0e1014;
    color: white;

    font-family:
        Arial,
        Helvetica,
        sans-serif;

    min-height: 100vh;

    display: flex;
    align-items: center;
    justify-content: center;
}

.login {
    width: 360px;

    background: #181b22;

    border: 1px solid #303641;

    border-radius: 20px;

    padding: 35px;
}

h1 {
    margin-top: 0;
}

input {
    width: 100%;

    padding: 14px;

    margin-top: 10px;
    margin-bottom: 18px;

    border: 1px solid #353b46;

    border-radius: 10px;

    background: #0e1014;

    color: white;

    font-size: 16px;
}

button {
    width: 100%;

    padding: 14px;

    border: none;

    border-radius: 10px;

    background: white;
    color: black;

    font-weight: 800;

    cursor: pointer;
}

.error {
    color: #ff7777;
    margin-bottom: 15px;
}

</style>

</head>

<body>

<div class="login">

<h1>Admin Login</h1>

{% if error %}
<div class="error">
{{ error }}
</div>
{% endif %}

<form method="POST">

<input
    name="username"
    placeholder="Username"
    autocomplete="username"
    required>

<input
    name="password"
    type="password"
    placeholder="Password"
    autocomplete="current-password"
    required>

<button>
LOGIN
</button>

</form>

</div>

</body>

</html>
"""


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():

    if request.method == "POST":

        username = request.form.get(
            "username",
            ""
        )

        password = request.form.get(
            "password",
            ""
        )

        if (
            username == ADMIN_USERNAME
            and
            password == ADMIN_PASSWORD
        ):

            session["admin"] = True

            return redirect("/admin")

        return render_template_string(
            ADMIN_LOGIN_HTML,
            error="Incorrect username or password."
        )

    return render_template_string(
        ADMIN_LOGIN_HTML,
        error=None
    )


@app.route("/admin/logout")
def admin_logout():

    session.clear()

    return redirect("/admin/login")


# ============================================================
# ADMIN DASHBOARD
# ============================================================

ADMIN_HTML = r"""
<!DOCTYPE html>
<html>

<head>

<meta charset="UTF-8">

<meta name="viewport"
      content="width=device-width, initial-scale=1.0">

<title>Service Kiosk Admin</title>

<style>

* {
    box-sizing: border-box;
}

body {
    margin: 0;

    background: #0e1014;
    color: white;

    font-family:
        Arial,
        Helvetica,
        sans-serif;
}

.header {
    height: 75px;

    background: #171a20;

    border-bottom: 1px solid #292d35;

    display: flex;

    align-items: center;

    justify-content: space-between;

    padding: 0 30px;
}

.logo {
    font-size: 23px;
    font-weight: 900;
}

.logout {
    color: white;
    text-decoration: none;

    background: #252a33;

    padding: 10px 16px;

    border-radius: 9px;
}

.container {
    width: min(1250px, 94%);
    margin: 35px auto;
}

.stats {
    display: grid;

    grid-template-columns:
        repeat(4, 1fr);

    gap: 18px;

    margin-bottom: 30px;
}

.stat {
    background: #181b22;

    border: 1px solid #303641;

    border-radius: 16px;

    padding: 23px;
}

.label {
    color: #8f97a5;
    font-size: 14px;
}

.value {
    font-size: 30px;
    font-weight: 900;
    margin-top: 8px;
}

.panel {
    background: #181b22;

    border: 1px solid #303641;

    border-radius: 18px;

    padding: 25px;

    margin-bottom: 25px;
}

h2 {
    margin-top: 0;
}

table {
    width: 100%;
    border-collapse: collapse;
}

th,
td {
    text-align: left;

    padding: 14px 10px;

    border-bottom:
        1px solid #2b3039;
}

th {
    color: #8f97a5;
    font-size: 13px;
}

.status {
    display: inline-block;

    padding: 5px 9px;

    border-radius: 7px;

    background: #243b2b;

    color: #8de0a0;

    font-size: 12px;
}

@media(max-width: 800px) {

    .stats {
        grid-template-columns: 1fr 1fr;
    }

}

</style>

</head>

<body>

<header class="header">

<div class="logo">
SERVICE KIOSK ADMIN
</div>

<a
    href="/admin/logout"
    class="logout">
    Logout
</a>

</header>


<div class="container">

<div class="stats">

<div class="stat">

<div class="label">
Cards
</div>

<div
    class="value"
    id="cards">
0
</div>

</div>


<div class="stat">

<div class="label">
Active Sessions
</div>

<div
    class="value"
    id="sessions">
0
</div>

</div>


<div class="stat">

<div class="label">
Coins Accepted
</div>

<div
    class="value"
    id="coins">
0
</div>

</div>


<div class="stat">

<div class="label">
Money Added
</div>

<div
    class="value"
    id="money">
$0.00
</div>

</div>

</div>


<div class="panel">

<h2>
Active Kiosks
</h2>

<table>

<thead>

<tr>

<th>Kiosk</th>
<th>Mode</th>
<th>Card</th>
<th>Coins</th>
<th>Added</th>
<th>Updated</th>

</tr>

</thead>

<tbody id="sessionsTable">

</tbody>

</table>

</div>


<div class="panel">

<h2>
Recent Transactions
</h2>

<table>

<thead>

<tr>

<th>Time</th>
<th>Kiosk</th>
<th>Card</th>
<th>Type</th>
<th>Coins</th>
<th>Amount</th>

</tr>

</thead>

<tbody id="transactionsTable">

</tbody>

</table>

</div>


<div class="panel">

<h2>
Recent Coin Events
</h2>

<table>

<thead>

<tr>

<th>Time</th>
<th>Kiosk</th>
<th>Card</th>
<th>Status</th>
<th>Coins</th>
<th>Amount</th>
<th>Reason</th>

</tr>

</thead>

<tbody id="coinsTable">

</tbody>

</table>

</div>

</div>


<script>

function escapeHtml(value) {

    return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");

}


async function updateDashboard() {

    try {

        const response =
            await fetch(
                "/api/admin/live"
            );

        if (!response.ok) {
            return;
        }

        const data =
            await response.json();


        document
            .getElementById("cards")
            .innerText =
            data.cards;


        document
            .getElementById("sessions")
            .innerText =
            data.active_sessions;


        document
            .getElementById("coins")
            .innerText =
            data.total_coins;


        document
            .getElementById("money")
            .innerText =
            data.total_money;


        const sessions =
            document.getElementById(
                "sessionsTable"
            );

        sessions.innerHTML = "";

        data.sessions.forEach(row => {

            sessions.innerHTML += `

                <tr>

                    <td>
                        ${escapeHtml(row.kiosk_id)}
                    </td>

                    <td>
                        ${escapeHtml(row.mode)}
                    </td>

                    <td>
                        ${escapeHtml(row.card_id)}
                    </td>

                    <td>
                        ${row.coin_count}
                    </td>

                    <td>
                        ${escapeHtml(row.amount)}
                    </td>

                    <td>
                        ${escapeHtml(row.updated_at)}
                    </td>

                </tr>

            `;

        });


        const transactions =
            document.getElementById(
                "transactionsTable"
            );

        transactions.innerHTML = "";

        data.transactions.forEach(row => {

            transactions.innerHTML += `

                <tr>

                    <td>
                        ${escapeHtml(row.created_at)}
                    </td>

                    <td>
                        ${escapeHtml(row.kiosk_id)}
                    </td>

                    <td>
                        ${escapeHtml(row.card_id)}
                    </td>

                    <td>
                        ${escapeHtml(row.type)}
                    </td>

                    <td>
                        ${row.coins}
                    </td>

                    <td>
                        ${escapeHtml(row.amount)}
                    </td>

                </tr>

            `;

        });


        const coins =
            document.getElementById(
                "coinsTable"
            );

        coins.innerHTML = "";

        data.coin_events.forEach(row => {

            coins.innerHTML += `

                <tr>

                    <td>
                        ${escapeHtml(row.created_at)}
                    </td>

                    <td>
                        ${escapeHtml(row.kiosk_id)}
                    </td>

                    <td>
                        ${escapeHtml(row.card_id || "-")}
                    </td>

                    <td>

                        <span class="status">

                            ${row.accepted
                                ? "Accepted"
                                : "Not assigned"}

                        </span>

                    </td>

                    <td>
                        ${row.coins}
                    </td>

                    <td>
                        ${escapeHtml(row.amount)}
                    </td>

                    <td>
                        ${escapeHtml(row.reason || "-")}
                    </td>

                </tr>

            `;

        });

    } catch {}

}


updateDashboard();

setInterval(
    updateDashboard,
    1000
);

</script>

</body>

</html>
"""


@app.route("/admin")
def admin():

    if not session.get("admin"):
        return redirect("/admin/login")

    return render_template_string(
        ADMIN_HTML
    )


# ============================================================
# ADMIN LIVE DATA
# ============================================================

@app.route("/api/admin/live")
def admin_live():

    if not session.get("admin"):
        return jsonify({
            "error": "Unauthorized"
        }), 401

    conn = get_db()

    cards = conn.execute("""
        SELECT COUNT(*) AS count
        FROM cards
    """).fetchone()["count"]

    active_sessions = conn.execute("""
        SELECT COUNT(*) AS count
        FROM sessions
        WHERE status = 'active'
    """).fetchone()["count"]

    total_coins = conn.execute("""
        SELECT COALESCE(
            SUM(coins),
            0
        ) AS total
        FROM coin_events
        WHERE accepted = 1
    """).fetchone()["total"]

    total_money = conn.execute("""
        SELECT COALESCE(
            SUM(amount_cents),
            0
        ) AS total
        FROM coin_events
        WHERE accepted = 1
    """).fetchone()["total"]

    sessions = conn.execute("""
        SELECT *
        FROM sessions
        WHERE status = 'active'
        ORDER BY id DESC
        LIMIT 50
    """).fetchall()

    transactions = conn.execute("""
        SELECT *
        FROM transactions
        ORDER BY id DESC
        LIMIT 50
    """).fetchall()

    coin_events = conn.execute("""
        SELECT *
        FROM coin_events
        ORDER BY id DESC
        LIMIT 50
    """).fetchall()

    conn.close()

    return jsonify({

        "cards": cards,

        "active_sessions":
            active_sessions,

        "total_coins":
            total_coins,

        "total_money":
            money(total_money),

        "sessions": [

            {
                "kiosk_id":
                    row["kiosk_id"],

                "mode":
                    row["mode"],

                "card_id":
                    row["card_id"],

                "coin_count":
                    row["coin_count"],

                "amount":
                    money(row["amount_cents"]),

                "updated_at":
                    row["updated_at"]

            }

            for row in sessions

        ],

        "transactions": [

            {
                "created_at":
                    row["created_at"],

                "kiosk_id":
                    row["kiosk_id"],

                "card_id":
                    row["card_id"],

                "type":
                    row["type"],

                "coins":
                    row["coins"],

                "amount":
                    money(abs(row["amount_cents"]))

            }

            for row in transactions

        ],

        "coin_events": [

            {
                "created_at":
                    row["created_at"],

                "kiosk_id":
                    row["kiosk_id"],

                "card_id":
                    row["card_id"],

                "accepted":
                    bool(row["accepted"]),

                "coins":
                    row["coins"],

                "amount":
                    money(row["amount_cents"]),

                "reason":
                    row["reason"]

            }

            for row in coin_events

        ]

    })


# ============================================================
# CARD BALANCE API
# ============================================================

@app.route("/api/card/<card_id>")
def card_balance(card_id):

    conn = get_db()

    card = conn.execute("""
        SELECT *
        FROM cards
        WHERE card_id = ?
    """, (
        card_id,
    )).fetchone()

    conn.close()

    if card is None:
        return jsonify({
            "error": "Card not found."
        }), 404

    return jsonify({

        "card_id":
            card["card_id"],

        "balance_cents":
            card["balance_cents"],

        "balance":
            money(card["balance_cents"])

    })


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    print()
    print("========================================")
    print("        SERVICE KIOSK SERVER")
    print("========================================")
    print()
    print("Kiosk:")
    print("http://127.0.0.1:5000")
    print()
    print("Admin:")
    print("http://127.0.0.1:5000/admin")
    print()
    print("Admin username: admin")
    print("Admin password: admin")
    print()
    print("Kiosk ID:", KIOSK_ID)
    print()
    print("========================================")
    print()

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False
    )
