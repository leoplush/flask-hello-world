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
PIP_VALUE = 0.0001  # Para EUR/USD
BE_PIPS_TRIGGER = 10  # A los 10 pips a favor se activa el Break Even
BE_PROFIT_PIPS = 4    # Se asegura el SL a +4 pips en positivo
TIME_STOP_MINUTES = 45  # 3 velas de 15 min = 45 minutos


def tiene_posicion_activa(pair):
    """Consulta OANDA para saber si ya hay una posición abierta en el par."""
    url = f"{OANDA_BASE_URL}/openPositions"
    try:
        response = requests.get(url, headers=headers)
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
        print(f"Error consultando posiciones: {e}")
        return False


def monitorear_posiciones():
    """Hilo secundario que revisa constantemente Break Even y Time-Stop."""
    while True:
        try:
            url = f"{OANDA_BASE_URL}/openTrades"
            response = requests.get(url, headers=headers)
            
            if response.status_code == 200:
                trades = response.json().get("trades", [])
                
                for trade in trades:
                    trade_id = trade.get("id")
                    instrument = trade.get("instrument")
                    units = float(trade.get("currentUnits", 0))
                    entry_price = float(trade.get("price", 0))
                    open_time_str = trade.get("openTime")
                    
                    # 1. Obtener precio actual de mercado
                    price_url = f"{OANDA_BASE_URL}/pricing?instruments={instrument}"
                    price_res = requests.get(price_url, headers=headers)
                    if price_res.status_code != 200:
                        continue
                    
                    prices = price_res.json().get("prices", [])[0]
                    bid = float(prices.get("bids")[0].get("price"))
                    ask = float(prices.get("asks")[0].get("price"))
                    current_price = bid if units > 0 else ask
                    
                    # --- REGLA 1: TIME-STOP (45 min / 3 velas de 15m) ---
                    open_time = datetime.fromisoformat(open_time_str.replace("Z", "+00:00"))
                    now = datetime.now(timezone.utc)
                    elapsed_minutes = (now - open_time).total_seconds() / 60
                    
                    if elapsed_minutes >= TIME_STOP_MINUTES:
                        print(f"[TIME-STOP] Cerrando trade {trade_id} por inactividad ({elapsed_minutes:.1f} min).")
                        close_url = f"{OANDA_BASE_URL}/trades/{trade_id}/close"
                        requests.put(close_url, headers=headers)
                        continue
                    
                    # --- REGLA 2: BREAK EVEN CON GANANCIA (+4 PIPS) ---
                    if units > 0:  # COMPRA
                        pips_a_favor = (current_price - entry_price) / PIP_VALUE
                        target_sl_price = round(entry_price + (BE_PROFIT_PIPS * PIP_VALUE), 5)
                    else:  # VENTA
                        pips_a_favor = (entry_price - current_price) / PIP_VALUE
                        target_sl_price = round(entry_price - (BE_PROFIT_PIPS * PIP_VALUE), 5)
                    
                    # Verificar Stop Loss actual
                    has_sl = "stopLossOrder" in trade
                    current_sl = float(trade["stopLossOrder"]["price"]) if has_sl else None
                    
                    # Si va 10 pips a favor y el SL aún no está asegurado en +4 pips
                    if pips_a_favor >= BE_PIPS_TRIGGER:
                        necesita_actualizar = False
                        if current_sl is None:
                            necesita_actualizar = True
                        elif units > 0 and current_sl < target_sl_price:
                            necesita_actualizar = True
                        elif units < 0 and current_sl > target_sl_price:
                            necesita_actualizar = True
                            
                        if necesita_actualizar:
                            print(f"[BREAK EVEN +4] Modificando SL del trade {trade_id} a precio asegurado: {target_sl_price}")
                            update_sl_url = f"{OANDA_BASE_URL}/orders"
                            body_sl = {
                                "order": {
                                    "timeInForce": "GTC",
                                    "tradeID": trade_id,
                                    "type": "STOP_LOSS",
                                    "price": str(target_sl_price)
                                }
                            }
                            requests.post(update_sl_url, headers=headers, json=body_sl)
                            
        except Exception as e:
            print(f"Error en el monitor de posiciones: {e}")
            
        time.sleep(10)


threading.Thread(target=monitorear_posiciones, daemon=True).start()


@app.route('/', methods=['GET'])
def home():
    return "Bot de OANDA activo y escuchando señales.", 200


@app.route('/webhook', methods=['POST'])
def webhook():
    datos = request.json
    
    if not datos:
        return jsonify({"status": "error", "message": "No llegaron datos JSON"}), 400

    pair = datos.get("pair", "EUR_USD")
    action = datos.get("action", "BUY")
    units = datos.get("units", 300)
    sl = datos.get("sl")
    tp = datos.get("tp")

    if tiene_posicion_activa(pair):
        return jsonify({
            "status": "ignorado", 
            "message": f"Ya existe una posición abierta en {pair}. Orden bloqueada."
        }), 200

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

    response = requests.post(OANDA_ORDERS_URL, headers=headers, json=body)
    
    return jsonify({"status": "procesado", "oanda_response": response.json()}), response.status_code


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
