import math
import os
import sqlite3
from pathlib import Path

import requests
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("KRYPTOS_DB_PATH", BASE_DIR / "kryptos.sqlite3"))
app = Flask(__name__, static_folder=None)
CORS(app)

ALLOWED_CURRENCIES = {"USD", "EUR", "GBP", "CHF"}
DEFAULT_WATCHLIST = ["BTC", "ETH"]


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS watchlist (
            symbol TEXT PRIMARY KEY, created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT NOT NULL,
            symbol TEXT NOT NULL,
            currency TEXT NOT NULL,
            hma_period INTEGER NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")
        count = conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0]
        if count == 0:
            conn.executemany("INSERT OR IGNORE INTO watchlist(symbol) VALUES (?)",
                             [(s,) for s in DEFAULT_WATCHLIST])


def json_error(message, status=400):
    return jsonify({"error": str(message)}), status


def calculate_wma(values, period):
    if period < 1 or len(values) < period:
        return None
    weights = range(1, period + 1)
    denominator = period * (period + 1) / 2
    return sum(value * weight for value, weight in zip(values[-period:], weights)) / denominator


def calculate_hma(prices, period):
    """Return HMA values and the candle index where the first value aligns."""
    if period < 2 or len(prices) < period:
        return [], len(prices)
    half = max(1, period // 2)
    root = max(1, int(math.sqrt(period)))
    raw = []
    for end in range(period, len(prices) + 1):
        window = prices[:end]
        half_wma = calculate_wma(window, half)
        full_wma = calculate_wma(window, period)
        raw.append(2 * half_wma - full_wma)
    if len(raw) < root:
        return [], len(prices)
    values = [calculate_wma(raw[:end], root) for end in range(root, len(raw) + 1)]
    offset = len(prices) - len(values)
    return values, offset


@app.get("/")
def home():
    return send_from_directory(BASE_DIR, "index.html")


@app.get("/api/health")
def health():
    return jsonify({"ok": True})


@app.route("/api/price", methods=["GET", "POST"])
def get_price():
    data = request.get_json(silent=True) or request.args
    symbol = str(data.get("symbol", "BTC")).strip().upper()
    currency = str(data.get("currency", "USD")).strip().upper()
    try:
        period = int(data.get("hma_period", 16))
        limit = int(data.get("limit", 48))
    except (TypeError, ValueError):
        return json_error("HMA period and chart range must be whole numbers.")
    if not symbol or len(symbol) > 12 or not symbol.isalnum():
        return json_error("Enter a valid ticker symbol, such as BTC or ETH.")
    if currency not in ALLOWED_CURRENCIES:
        return json_error("Unsupported currency. Choose USD, EUR, GBP, or CHF.")
    if not 2 <= period <= 200:
        return json_error("HMA period must be between 2 and 200.")
    if not 24 <= limit <= 336:
        return json_error("Chart range must be between 24 and 336 hours.")

    url = "https://min-api.cryptocompare.com/data/v2/histohour"
    try:
        response = requests.get(
            url,
            params={"fsym": symbol, "tsym": currency, "limit": limit},
            timeout=12,
        )
        response.raise_for_status()
        result = response.json()
        if result.get("Response") == "Error":
            return json_error(result.get("Message", "Market data provider rejected the request."), 400)
        raw = result.get("Data", {}).get("Data", [])
        if not raw:
            return json_error("No market data was returned for this ticker.", 404)
        candles = [
            {"t": int(item["time"]), "o": float(item["open"]), "h": float(item["high"]),
             "l": float(item["low"]), "c": float(item["close"])}
            for item in raw
            if all(item.get(key) is not None for key in ("time", "open", "high", "low", "close"))
        ]
        if not candles:
            return json_error("The market data response did not contain valid candles.", 502)
        hma, offset = calculate_hma([c["c"] for c in candles], period)
        return jsonify({
            "symbol": symbol, "currency": currency, "hma_period": period,
            "candles": candles, "hma": hma, "hma_offset": offset,
            "current": candles[-1]["c"],
        })
    except requests.Timeout:
        return json_error("Market data request timed out. Please try again.", 504)
    except requests.RequestException:
        return json_error("Could not reach the market data provider. Check your connection and retry.", 502)
    except (ValueError, KeyError, TypeError):
        app.logger.exception("Invalid market data response")
        return json_error("The market data provider returned an unexpected response.", 502)


def read_watchlist(conn):
    rows = conn.execute("SELECT symbol FROM watchlist ORDER BY created_at, symbol").fetchall()
    return [{"symbol": row["symbol"]} for row in rows]


@app.route("/api/watchlist", methods=["GET", "POST"])
def watchlist():
    with get_db() as conn:
        if request.method == "GET":
            return jsonify({"watchlist": read_watchlist(conn)})
        data = request.get_json(silent=True) or {}
        symbol = str(data.get("symbol", "")).strip().upper()
        if not symbol or len(symbol) > 12 or not symbol.isalnum():
            return json_error("Enter a valid ticker symbol.")
        conn.execute("INSERT OR IGNORE INTO watchlist(symbol) VALUES (?)", (symbol,))
        return jsonify({"watchlist": read_watchlist(conn)})


@app.delete("/api/watchlist/<symbol>")
def remove_watch(symbol):
    with get_db() as conn:
        conn.execute("DELETE FROM watchlist WHERE symbol = ?", (symbol.strip().upper(),))
        return jsonify({"watchlist": read_watchlist(conn)})


@app.route("/api/snapshots", methods=["GET", "POST"])
def snapshots():
    import json
    with get_db() as conn:
        if request.method == "GET":
            rows = conn.execute(
                "SELECT id, label, symbol, created_at FROM snapshots ORDER BY id DESC"
            ).fetchall()
            return jsonify({"snapshots": [dict(row) for row in rows]})
        data = request.get_json(silent=True) or {}
        label = str(data.get("label", "")).strip()[:120]
        symbol = str(data.get("symbol", "BTC")).strip().upper()
        currency = str(data.get("currency", "USD")).strip().upper()
        try:
            period = int(data.get("hma_period", 16))
        except (TypeError, ValueError):
            return json_error("Invalid HMA period.")
        candles = data.get("candles")
        hma = data.get("hma", [])
        offset = data.get("hma_offset", 0)
        if not label:
            return json_error("Snapshot name cannot be empty.")
        if not isinstance(candles, list) or not candles:
            return json_error("Snapshot must contain chart data.")
        if currency not in ALLOWED_CURRENCIES or not 2 <= period <= 200:
            return json_error("Invalid currency or HMA period.")
        payload = json.dumps({"candles": candles, "hma": hma, "hma_offset": offset})
        cur = conn.execute(
            "INSERT INTO snapshots(label, symbol, currency, hma_period, payload) VALUES (?, ?, ?, ?, ?)",
            (label, symbol, currency, period, payload),
        )
        rows = conn.execute(
            "SELECT id, label, symbol, created_at FROM snapshots ORDER BY id DESC"
        ).fetchall()
        return jsonify({"id": cur.lastrowid, "snapshots": [dict(row) for row in rows]})


@app.get("/api/snapshots/<int:snapshot_id>")
def get_snapshot(snapshot_id):
    import json
    with get_db() as conn:
        row = conn.execute("SELECT * FROM snapshots WHERE id = ?", (snapshot_id,)).fetchone()
        if row is None:
            return json_error("Snapshot not found.", 404)
        return jsonify({"snapshot": {
            "id": row["id"], "label": row["label"], "symbol": row["symbol"],
            "currency": row["currency"], "hma_period": row["hma_period"],
            "payload": json.loads(row["payload"]), "created_at": row["created_at"],
        }})


@app.delete("/api/snapshots/<int:snapshot_id>")
def delete_snapshot(snapshot_id):
    with get_db() as conn:
        conn.execute("DELETE FROM snapshots WHERE id = ?", (snapshot_id,))
        rows = conn.execute(
            "SELECT id, label, symbol, created_at FROM snapshots ORDER BY id DESC"
        ).fetchall()
        return jsonify({"snapshots": [dict(row) for row in rows]})


@app.post("/api/ai")
def ai_chat():
    data = request.get_json(silent=True) or {}
    message = str(data.get("msg", "")).strip()
    if not message:
        return json_error("Enter a question first.")
    if len(message) > 400:
        return json_error("Question is too long (maximum 400 characters).")
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        return jsonify({
            "reply": "AI is not configured yet. Set the GROQ_API_KEY environment variable to enable the assistant.",
            "available": False,
        })
    try:
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": "llama-3.1-8b-instant",
                "messages": [
                    {"role": "system", "content": (
                        "You are KRYPTOS, a concise cryptocurrency market-analysis assistant. "
                        "Clearly distinguish observations from predictions and never promise returns. "
                        "Keep answers to 2-4 sentences."
                    )},
                    {"role": "user", "content": message},
                ],
                "temperature": 0.3,
            },
            timeout=30,
        )
        if response.status_code in (401, 403):
            return jsonify({"reply": "AI configuration failed: the GROQ_API_KEY is invalid or lacks access.", "available": False})
        response.raise_for_status()
        result = response.json()
        reply = result.get("choices", [{}])[0].get("message", {}).get("content")
        if not reply:
            raise ValueError("Empty AI response")
        return jsonify({"reply": reply, "available": True})
    except requests.Timeout:
        return jsonify({"reply": "The AI service timed out. Please try again.", "available": False})
    except (requests.RequestException, ValueError, KeyError, IndexError):
        app.logger.exception("AI request failed")
        return jsonify({"reply": "The AI service is temporarily unavailable. Please try again later.", "available": False})


init_db()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "8000")), debug=False)
