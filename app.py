from flask import Flask, request, jsonify
import requests
import json
import os
import time
import threading

app = Flask(__name__)

# --- CONFIGURACIÓN DE CREDENCIALES OANDA ---
OANDA_ACCOUNT_ID = os.environ.get("OANDA_ACCOUNT_ID", "101-001-20711675-001")
OANDA_API_KEY = os.environ.get("OANDA_API_KEY")
OANDA_URL = os.environ.get("OANDA_URL", "https://api-fxtrade.oanda.com")
HEADERS = {
    "Authorization": f"Bearer {OANDA_API_KEY}",
    "Content-Type": "application/json"
}

def get_current_price(pair):
    url = f"{OANDA_URL}/v3/accounts/{OANDA_ACCOUNT_ID}/pricing?instruments={pair}"
    try:
        res = requests.get(url, headers=HEADERS, timeout=5)
        if res.status_code == 200:
            prices = res.json()["prices"][0]
            return float(prices["closeoutBid"]), float(prices["closeoutAsk"])
    except Exception as e:
        print(f"Error obteniendo precio: {e}")
    return None, None

def monitor_trade(trade_id, action, entry_price, initial_units, be_pips, tp1_pips, pair):
    """ Monitorea la orden activa en segundo plano para aplicar Break Even y Cierre Parcial (TP1) """
    pip_value = 0.0001
    be_triggered = False
    tp1_triggered = False
    current_units = initial_units

    print(f"Iniciando monitoreo para Trade ID {trade_id}...")

    while True:
        time.sleep(2)  # Escaneo cada 2 segundos
        bid, ask = get_current_price(pair)
        if not bid or not ask:
            continue

        # Precio actual según la dirección
        current_price = bid if action == "BUY" else ask
        
        # Calcular pips ganados
        pips_gained = (current_price - entry_price) / pip_value if action == "BUY" else (entry_price - current_price) / pip_value

        # 1. EJECUTAR BREAK EVEN (3.5 PIPS A FAVOR)
        if pips_gained >= be_pips and not be_triggered:
            new_sl = round(entry_price + (1.0 * pip_value), 5) if action == "BUY" else round(entry_price - (1.0 * pip_value), 5)
            url_sl = f"{OANDA_URL}/v3/accounts/{OANDA_ACCOUNT_ID}/orders"
            body_sl = {
                "order": {
                    "type": "STOP_LOSS",
                    "tradeID": str(trade_id),
                    "price": str(new_sl),
                    "timeInForce": "GTC"
                }
            }
            res = requests.post(url_sl, headers=HEADERS, json=body_sl)
            if res.status_code in [200, 201]:
                print(f"Break Even aplicado a Trade {trade_id} en precio {new_sl}")
                be_triggered = True

        # 2. EJECUTAR TAKE PROFIT 1 / CIERRE PARCIAL (8 PIPS A FAVOR)
        if pips_gained >= tp1_pips and not tp1_triggered:
            close_units = str(abs(int(current_units / 2)))
            url_close = f"{OANDA_URL}/v3/accounts/{OANDA_ACCOUNT_ID}/trades/{trade_id}/close"
            body_close = {"units": close_units}
            res = requests.put(url_close, headers=HEADERS, json=body_close)
            if res.status_code == 200:
                print(f"TP1 alcanzado. Cierre parcial de {close_units} unidades en Trade {trade_id}")
                tp1_triggered = True

        # Salir del bucle si ya se ejecutaron las acciones o si la posición se cerró
        if be_triggered and tp1_triggered:
            break

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
        res_data = response.json()
        trade_id = None
        if "orderFillTransaction" in res_data:
            trade_id = res_data["orderFillTransaction"]["tradeOpened"]["tradeID"]

        # Si la orden abrió con éxito, iniciamos el hilo de monitoreo para BE y TP1
        if trade_id:
            thread = threading.Thread(
                target=monitor_trade, 
                args=(trade_id, action, entry_price, units, be_pips, tp1_pips, pair)
            )
            thread.daemon = True
            thread.start()

        return jsonify({"status": "success", "action": action, "trade_id": trade_id, "units": units}), 200
    else:
        return jsonify({"status": "error", "details": response.json()}), response.status_code

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
