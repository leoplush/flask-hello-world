from flask import Flask, request, jsonify
import requests
import json
import os

app = Flask(__name__)

# --- CONFIGURACIÓN DE CREDENCIALES OANDA ---
OANDA_ACCOUNT_ID = os.environ.get("OANDA_ACCOUNT_ID", "101-001-20711675-001")
OANDA_API_KEY = os.environ.get("OANDA_API_KEY", "TU_API_KEY_AQUI")
OANDA_URL = os.environ.get("OANDA_URL", "https://api-fxpractice.oanda.com")

HEADERS = {
    "Authorization": f"Bearer {OANDA_API_KEY}",
    "Content-Type": "application/json"
}

def get_current_price(pair):
    url = f"{OANDA_URL}/v3/accounts/{OANDA_ACCOUNT_ID}/pricing?instruments={pair}"
    res = requests.get(url, headers=HEADERS)
    if res.status_code == 200:
        prices = res.json()["prices"][0]
        return float(prices["closeoutBid"]), float(prices["closeoutAsk"])
    return None, None

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json(force=True)
    if not data:
        return jsonify({"status": "error", "message": "No JSON received"}), 400

    pair = data.get("pair", "EUR_USD")
    action = data.get("action")
    risk_usd = float(data.get("risk_usd", 1.0))
    sl_pips = float(data.get("sl_pips", 12.0))
    be_pips = float(data.get("be_pips", 3.5))
    tp1_pips = float(data.get("tp1_pips", 8.0))
    tp2_pips = float(data.get("tp2_pips", 18.0))

    bid, ask = get_current_price(pair)
    if not bid or not ask:
        return jsonify({"status": "error", "message": "Failed to fetch market price"}), 500

    # Cálculo dinámico de unidades basado en $1.00 USD de riesgo
    # 1 pip en EUR_USD = 0.0001
    pip_value = 0.0001
    units_calculated = int(risk_usd / (sl_pips * pip_value))

    if action == "BUY":
        entry_price = ask
        sl_price = round(entry_price - (sl_pips * pip_value), 5)
        tp_price = round(entry_price + (tp2_pips * pip_value), 5)
        units = units_calculated
    elif action == "SELL":
        entry_price = bid
        sl_price = round(entry_price + (sl_pips * pip_value), 5)
        tp_price = round(entry_price - (tp2_pips * pip_value), 5)
        units = -units_calculated
    else:
        return jsonify({"status": "error", "message": "Invalid action"}), 400

    order_body = {
        "order": {
            "units": str(units),
            "instrument": pair,
            "timeInForce": "FOK",
            "type": "MARKET",
            "positionFill": "DEFAULT",
            "stopLossOnFill": {"price": str(sl_price)},
            "takeProfitOnFill": {"price": str(tp_price)}
        }
    }

    url = f"{OANDA_URL}/v3/accounts/{OANDA_ACCOUNT_ID}/orders"
    response = requests.post(url, headers=HEADERS, json=order_body)

    if response.status_code in [200, 201]:
        return jsonify({
            "status": "success", 
            "action": action, 
            "units": units, 
            "entry": entry_price,
            "sl": sl_price,
            "tp2": tp_price
        }), 200
    else:
        return jsonify({"status": "error", "details": response.json()}), response.status_code

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
