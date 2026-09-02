import os
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

OANDA_API_KEY = os.environ.get("OANDA_API_KEY")
OANDA_ACCOUNT_ID = os.environ.get("OANDA_ACCOUNT_ID")

OANDA_URL = f"https://api-fxtrade.oanda.com/v3/accounts/{OANDA_ACCOUNT_ID}/orders"

@app.route('/', methods=['GET'])
def home():
    return "Bot de OANDA activo y escuchando señales.", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    datos = request.json
    
    if not datos:
        return jsonify({"status": "error", "message": "No llegaron datos JSON"}), 400

    pair = datos.get("pair", "USD_JPY")
    action = datos.get("action", "BUY")
    units = datos.get("units", 300)
    sl = datos.get("sl")
    tp = datos.get("tp")

    if action.upper() == "SELL":
        units = -abs(int(units))
    else:
        units = abs(int(units))

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {OANDA_API_KEY}"
    }

    body = {
        "order": {
            "units": str(units),
            "instrument": pair,
            "timeInForce": "FOK",
            "type": "MARKET",
            "positionFill": "DEFAULT"
        }
    }

    if sl:
        body["order"]["stopLossOnFill"] = {"price": str(sl)}
    if tp:
        body["order"]["takeProfitOnFill"] = {"price": str(tp)}

    response = requests.post(OANDA_URL, headers=headers, json=body)
    
    return jsonify({"status": "procesado", "oanda_response": response.json()}), response.status_code

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
