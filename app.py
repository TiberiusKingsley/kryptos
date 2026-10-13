import os
import requests
import math
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# Serve the frontend interface from the root directory
# Serve the frontend interface from the root directory
@app.route('/')
def home():
    try:
        return send_from_directory('.', 'index.html')
    except Exception as e:
        return f"KRYPTOS_ERROR: index.html not found ({str(e)})", 404
def calculate_wma(data, period):
    if len(data) < period: return 0
    weights = list(range(1, period + 1))
    return sum(p * w for p, w in zip(data[-period:], weights)) / sum(weights)

def calculate_hma(prices, period):
    if len(prices) < period: return []
    half_period = max(1, period // 2)
    sqrt_period = max(1, int(math.sqrt(period)))
    raw_hma = [2 * calculate_wma(prices[:i], half_period) - calculate_wma(prices[:i], period) for i in range(period, len(prices) + 1)]
    return [calculate_wma(raw_hma[:i], sqrt_period) for i in range(sqrt_period, len(raw_hma) + 1)]

@app.route('/api/price', methods=['GET', 'POST'])
def get_btc_price():
    try:
        data = request.json if request.is_json else {}
        symbol = (data.get('symbol') or 'BTC').upper()
        curr = (data.get('currency') or 'USD').upper()
        p = int(data.get('hma_period') or 16)
        limit = int(data.get('limit') or 48)
        
        api_key = os.environ.get("CRYPTOCOMPARE_API_KEY", "")
        url = f"https://min-api.cryptocompare.com/data/v2/histohour?fsym={symbol}&tsym={curr}&limit={limit + 50}&extraParams=KryptosQuant"
        if api_key:
            url += f"&api_key={api_key}"
        
        headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
        r = requests.get(url, headers=headers, timeout=10).json()
        
        if r.get('Response') == 'Error':
            return jsonify({"error": r.get('Message')}), 400

        raw_points = r.get('Data', {}).get('Data', [])
        if not raw_points:
            return jsonify({"error": "No market data returned from provider."}), 400

        closes = [d['close'] for d in raw_points]
        hma_values = calculate_hma(closes, p)
        
        offset = len(raw_points) - len(hma_values)
        candles = [{"t": d['time'], "o": d['open'], "h": d['high'], "l": d['low'], "c": d['close']} for d in raw_points[offset:]]
        
        return jsonify({
            "symbol": symbol, "currency": curr, "hma_period": p,
            "candles": candles, "hma": hma_values, "hma_offset": offset, "current": closes[-1]
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/ai', methods=['POST'])
def ai_chat():
    try:
        data = request.json or {}
        groq_api_key = os.environ.get("GROQ_API_KEY")
        if not groq_api_key:
            return jsonify({"reply": "AI_OFFLINE: Missing GROQ_API_KEY environment variable."})
            
        payload = {
            "model": "llama-3.1-8b-instant", 
            "messages": [
                {"role": "system", "content": "You are KRYPTOS. Minimalist Nordic Quant. Brutal, direct, 1 sentence. Use advanced market terms."}, 
                {"role": "user", "content": data.get('msg') or ''}
            ]
        }
        headers = {"Authorization": f"Bearer {groq_api_key}", "Content-Type": "application/json"}
        res = requests.post("https://api.groq.com/openai/v1/chat/completions", json=payload, headers=headers, timeout=30)
        return jsonify({"reply": res.json()['choices'][0]['message']['content']})
    except Exception as e:
        return jsonify({"reply": f"AI_OFFLINE: {str(e)}"})

if __name__ == '__main__':
    app.run(port=8000)
