import os
import requests
import math
from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

@app.route('/api/price', methods=['GET', 'POST'])
def get_btc_price():
    try:
        data = request.json if request.is_json else {}
        symbol = (data.get('symbol') or 'BTC').upper()
        curr = (data.get('currency') or 'USD').upper()
        p = int(data.get('hma_period') or 16)
        limit = int(data.get('limit') or 48)
        
        # Add extraParams and optional API key
        api_key = os.environ.get("CRYPTOCOMPARE_API_KEY", "")
        url = f"https://min-api.cryptocompare.com/data/v2/histohour?fsym={symbol}&tsym={curr}&limit={limit + 50}&extraParams=KryptosQuant"
        if api_key:
            url += f"&api_key={api_key}"
        
        # User-Agent header prevents cloud IP blocks
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
            "symbol": symbol,
            "currency": curr,
            "hma_period": p,
            "candles": candles,
            "hma": hma_values,
            "hma_offset": offset,
            "current": closes[-1]
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
