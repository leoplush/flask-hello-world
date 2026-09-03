import os
import time
import threading
import requests
from datetime import datetime, timezone
from flask import Flask, request, jsonify

app = Flask(__name__)

OANDA_API_KEY = os.environ.get("OANDA_API_KEY")
OANDA_ACCOUNT_ID = os.environ.get("OANDA_ACCOUNT_ID")

OANDA_BASE_URL = f"https://api-fxtrade.oanda.com/v3/accounts/{OANDA_ACCOUNT_ID}"
OANDA_ORDERS_URL = f"{OANDA_BASE_URL}/orders"

headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {OANDA_API_KEY}"
}

# Configuración de gestión de riesgo
PIP_VALUE = 0.0001
BE_PIPS_TRIGGER = 10
BE_PROFIT_PIPS = 4
TIME_STOP_MINUTES = 45


def tiene_posicion_activa(pair):
    """Consulta OANDA de forma aislada para verificar posiciones abiertas."""
    url = f"{OANDA_BASE_URL}/openPositions"
    try:
        with requests.Session() as session:
            session.headers.update(headers)
            response = session.get(url, timeout=5)
            if response.status_code == 200:
                positions = response.json().get("positions", [])
                for pos in positions:
                    if pos.get("instrument") == pair:
                        long_units = int(pos.get("long", {}).get("units", 0))
                        short_units = int(pos.get("short", {}).get("units", 0))
                        if long_units != 0 or short_units != 0:
                            return True
        return False
    except Exception as e:
        print(f"[Error] Fallo al verificar posición activa: {e}")
        return False


def monitorear_posiciones():
    """Hilo secundario aislado con manejo de excepciones independiente."""
    while True:
        try:
            url = f"{OANDA_BASE_URL}/openTrades"
            with requests.Session() as session:
                session.headers.update(headers)
                response = session.get(url, timeout=5)
                
                if response.status_code == 200:
                    trades = response.json().get("trades", [])
                    
                    for trade in trades:
                        trade_id = trade.get("id")
                        instrument = trade.get("instrument")
                        units = float(trade.get("currentUnits", 0))
                        entry_price = float(trade.get("price", 0))
                        open_time_str = trade.get("openTime")
                        
                        price_url = f"{OANDA_BASE_URL}/pricing?instruments={instrument}"
                        price_res = session.get(price_url, timeout=5)
                        if price_res.status_code != 200:
                            continue
                        
                        prices = price_res.json().get("prices", [])[0]
                        bid = float(prices.get("bids")[0].get("price"))
                        ask = float(prices.get("asks")[0].get("price"))
                        current_price = bid if units > 0 else ask
                        
                        # --- REGLA 1: TIME-STOP (45 min) ---
                        open_time = datetime.fromisoformat(open_time_str.replace("Z", "+00:00"))
                        now = datetime.now(timezone.utc)
                        elapsed_minutes = (now - open_time).total_seconds() / 60
                        
                        if elapsed_minutes >= TIME_STOP_MINUTES:
                            print(f"[TIME-STOP] Cerrando trade {trade_id} ({elapsed_minutes:.1f} min).")
                            close_url = f"{OANDA_BASE_URL}/trades/{trade_id}/close"
                            session.put(close_url, timeout=5)
                            continue
                        
                        # --- REGLA 2: BREAK EVEN (+4 PIPS) ---
                        if units > 0:
                            pips_a_favor = (current_price - entry_price) / PIP_VALUE
                            target_sl_price = round(entry_price + (BE_PROFIT_PIPS * PIP_VALUE), 5)
                        else:
                            pips_a_favor = (entry_price - current_price) / PIP_VALUE
                            target_sl_price = round(entry_price - (BE_PROFIT_PIPS * PIP_VALUE), 5)
                        
                        has_sl = "stopLossOrder" in trade
                        current_sl = float(trade["stopLossOrder"]["price"]) if has_sl else None
                        
                        if pips_a_favor >= BE_PIPS_TRIGGER:
                            necesita_actualizar = False
                            if current_sl is None:
                                necesita_actualizar = True
                            elif units > 0 and current_sl < target_sl_price:
                                necesita_actualizar = True
                            elif units < 0 and current_sl > target_sl_price:
                                necesita_actualizar = True
                                
                            if necesita_actualizar:
                                print(f"[BE +4] Ajustando SL del trade {trade_id} a {target_sl_price}")
                                body_sl = {
                                    "order": {
                                        "timeInForce": "GTC",
                                        "tradeID": trade_id,
                                        "type": "STOP_LOSS",
                                        "price": str(target_sl_price)
                                    }
                                }
                                session.post(f"{OANDA_BASE_URL}/orders", json=body_sl, timeout=5)
                                
        except Exception as e:
            print(f"[Error Monitor] {e}")
            
        time.sleep(10)


threading.Thread(target=monitorear_posiciones, daemon=True).start()


@app.route('/', methods=['GET'])
def home():
    return "Bot OANDA activo.", 200


@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        datos = request.get_json(force=True, silent=True)
        
        if not datos:
            return jsonify({"status": "error", "message": "JSON invalido"}), 400

        pair = datos.get("pair", "EUR_USD")
        action = datos.get("action", "BUY")
        units = datos.get("units", 300)
        sl = datos.get("sl")
        tp = datos.get("tp")

        if tiene_posicion_activa(pair):
            return jsonify({"status": "ignorado", "message": "Posicion previa detectada."}), 200

        if action.upper() == "SELL":
            units = -abs(int(units))
        else:
            units = abs(int(units))

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

        with requests.Session() as session:
            session.headers.update(headers)
            response = session.post(OANDA_ORDERS_URL, json=body, timeout=5)
            
        return jsonify({"status": "procesado", "oanda_response": response.json()}), response.status_code

    except Exception as e:
        print(f"[Error Webhook] {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
