from flask import Flask, request, jsonify, render_template, redirect, url_for, session
import sqlite3
from datetime import datetime, timezone
from functools import wraps

app = Flask(__name__)

# Test-only secret
app.secret_key = "test-secret-key-change-later"

DATABASE = "requests.db"

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin"


def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kiosk_id TEXT NOT NULL,
            coins INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'Pending',
            created_at TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()


def admin_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("login"))
        return func(*args, **kwargs)

    return wrapper


@app.route("/")
def index():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Coin Service</title>
        <style>
            body {
                font-family: Arial, sans-serif;
                background: #f4f6f8;
                display: flex;
                align-items: center;
                justify-content: center;
                height: 100vh;
                margin: 0;
            }

            .box {
                background: white;
                padding: 40px;
                border-radius: 15px;
                text-align: center;
                box-shadow: 0 10px 30px rgba(0,0,0,.1);
            }

            h1 {
                margin-bottom: 10px;
            }

            a {
                color: #2563eb;
                text-decoration: none;
            }
        </style>
    </head>

    <body>
        <div class="box">
            <h1>Coin Service Server</h1>
            <p>Server is online.</p>
            <a href="/admin">Admin Dashboard</a>
        </div>
    </body>
    </html>
    """


@app.route("/api/coin", methods=["POST"])
def receive_coin():

    data = request.get_json(silent=True)

    if not data:
        return jsonify({
            "success": False,
            "error": "JSON data required"
        }), 400

    kiosk_id = data.get("kiosk_id")
    coins = data.get("coins")

    if not kiosk_id:
        return jsonify({
            "success": False,
            "error": "kiosk_id is required"
        }), 400

    if coins is None:
        return jsonify({
            "success": False,
            "error": "coins is required"
        }), 400

    try:
        coins = int(coins)
    except (ValueError, TypeError):
        return jsonify({
            "success": False,
            "error": "coins must be a number"
        }), 400

    if coins <= 0:
        return jsonify({
            "success": False,
            "error": "coins must be greater than 0"
        }), 400

    now = datetime.now(timezone.utc).isoformat()

    conn = get_db()

    cursor = conn.execute("""
        INSERT INTO requests
        (kiosk_id, coins, status, created_at)
        VALUES (?, ?, ?, ?)
    """, (
        str(kiosk_id),
        coins,
        "Pending",
        now
    ))

    request_id = cursor.lastrowid

    conn.commit()
    conn.close()

    return jsonify({
        "success": True,
        "request_id": request_id,
        "kiosk_id": kiosk_id,
        "coins": coins,
        "status": "Pending"
    }), 201


@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        username = request.form.get("username", "")
        password = request.form.get("password", "")

        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session["admin_logged_in"] = True
            return redirect(url_for("admin"))

        return render_template(
            "login.html",
            error="Incorrect username or password"
        )

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/admin")
@admin_required
def admin():

    conn = get_db()

    rows = conn.execute("""
        SELECT *
        FROM requests
        ORDER BY id DESC
    """).fetchall()

    conn.close()

    return render_template(
        "admin.html",
        requests=rows
    )


@app.route("/admin/status/<int:request_id>", methods=["POST"])
@admin_required
def update_status(request_id):

    status = request.form.get("status")

    allowed_statuses = [
        "Pending",
        "In Progress",
        "Completed"
    ]

    if status not in allowed_statuses:
        return "Invalid status", 400

    conn = get_db()

    conn.execute("""
        UPDATE requests
        SET status = ?
        WHERE id = ?
    """, (
        status,
        request_id
    ))

    conn.commit()
    conn.close()

    return redirect(url_for("admin"))


@app.route("/admin/delete/<int:request_id>", methods=["POST"])
@admin_required
def delete_request(request_id):

    conn = get_db()

    conn.execute("""
        DELETE FROM requests
        WHERE id = ?
    """, (request_id,))

    conn.commit()
    conn.close()

    return redirect(url_for("admin"))


@app.route("/api/requests")
@admin_required
def api_requests():

    conn = get_db()

    rows = conn.execute("""
        SELECT *
        FROM requests
        ORDER BY id DESC
    """).fetchall()

    conn.close()

    return jsonify([
        dict(row)
        for row in rows
    ])


init_db()


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False
    )
