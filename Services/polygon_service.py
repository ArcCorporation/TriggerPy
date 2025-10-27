import requests
import logging
import json
import threading
import time
import websocket
from Services.enigma3 import Enigma3Service
from Services.randomness import KEY
# Import the new callback manager
from Services.callback_manager import callback_manager, ThreadedCallbackService 


class PolygonService:
    def __init__(self):
        # Şifreli API key çözülüyor
        api_key_enc = "SBb(2-n>X0)nJZ6}+[M3b)A>KV%fY}>K"
        eservis = Enigma3Service()
        self.api_key = eservis.decrypt(KEY, api_key_enc)

        self.base_url = "https://api.polygon.io"
        self.ws_url = "wss://socket.polygon.io/stocks"

        # WS için:
        # ❌ self.subscriptions = {}  <-- REMOVED: Now managed by callback_manager
        self.ws = None
        self.ws_thread = None
        # Track active WS subscriptions to avoid sending 'subscribe' message multiple times
        self._active_ws_symbols = set() 
        self._ws_lock = threading.Lock()


        # Background websocket start
        self._start_ws()

    # ---------------- REST METHODS ----------------
    def get_option_snapshot(self, underlying: str, expiry: str, strike: float, right: str):
        """
        Fetch current snapshot for a given option contract.
        Falls back to full chain if single-contract call fails.
        Returns {'symbol', 'bid', 'ask', 'last', 'mid', 'updated'} or None.
        """
        try:
            y, m, d = expiry[:4], expiry[4:6], expiry[6:8]
            strike_str = f"{int(strike * 1000):08d}"
            occ = f"O:{underlying.upper()}{y[2:]}{m}{d}{right.upper()}{strike_str}"

            # --- 1) Try single-contract endpoint first ---
            url = f"{self.base_url}/v3/snapshot/options/{occ}"
            params = {"apiKey": self.api_key}
            resp = requests.get(url, params=params, timeout=5)

            # If 404, fallback to chain below
            if resp.status_code == 404:
                logging.warning(f"[Polygon] Single snapshot not found for {occ}, trying chain...")
                return self._get_option_from_chain(underlying, expiry, strike, right)

            resp.raise_for_status()
            resp_json = resp.json()
            data = resp_json.get("results", {})

            # Handle both dict and list formats
            if isinstance(data, list):
                if len(data) == 0:
                    logging.warning(f"[Polygon] Empty results list for {occ}, trying chain...")
                    return self._get_option_from_chain(underlying, expiry, strike, right)
                elif isinstance(data[0], dict):
                    data = data[0]
                else:
                    logging.error(f"[Polygon] Unexpected inner list type: {type(data[0])}")
                    return None
            elif not isinstance(data, dict):
                logging.error(f"[Polygon] Unexpected snapshot format: {type(data)}")
                return None

            quote = data.get("last_quote", {})
            trade = data.get("last_trade", {})
            bid, ask, last = quote.get("bid"), quote.get("ask"), trade.get("price")
            mid = (bid + ask) / 2 if bid and ask else None

            return {
                "symbol": occ,
                "bid": bid,
                "ask": ask,
                "last": last,
                "mid": mid,
                "updated": data.get("updated", None)
            }

        except Exception as e:
            logging.error(f"[Polygon] get_option_snapshot failed: {e}")
            return None

    def _get_option_from_chain(self, underlying: str, expiry: str, strike: float, right: str):
        """
        Fallback: scan full chain with wider tolerance and debug logs.
        """
        try:
            url  = f"{self.base_url}/v3/snapshot/options/{underlying.upper()}"
            params = {"apiKey": self.api_key}
            resp = requests.get(url, params=params, timeout=8)
            resp.raise_for_status()

            results = resp.json().get("results", [])
            if not results:
                logging.warning("[Polygon] Chain returned zero contracts for %s", underlying)
                return None

            target_year  = expiry[:4]
            target_month = expiry[4:6]
            target_day   = int(expiry[6:8])
            right        = right.upper()[0]
            best_match   = None
            best_diff    = 999.

            for item in results:
                details = item.get("details", {})
                if not details:
                    continue
                exp  = details.get("expiration_date", "")
                str_strike = details.get("strike_price")
                ctype  = details.get("contract_type", "").upper()

                if not exp or str_strike is None or not ctype:
                    continue

                exp_y, exp_m, exp_d = exp.split("-")
                if exp_y != target_year or exp_m != target_month:
                    continue

                # ±14 calendar days
                if abs(int(exp_d) - target_day) > 14:
                    continue

                if ctype[0] != right:
                    continue

                # 3-decimal strike tolerance
                diff = abs(round(float(str_strike), 3) - round(float(strike), 3))
                if diff < best_diff:
                    best_diff = diff
                    best_match = item

            if not best_match:
                logging.error("[Polygon] No matching contract in chain for %s %s %s%s",
                            underlying, expiry, strike, right)
                return None

            quote = best_match.get("last_quote", {})
            trade = best_match.get("last_trade", {})
            bid   = quote.get("bid")
            ask   = quote.get("ask")
            last  = trade.get("price")
            mid   = (bid + ask) / 2 if (bid and ask) else last
            details = best_match.get("details", {})
            return {
                "symbol": details.get("ticker"),
                "bid": bid,
                "ask": ask,
                "last": last,
                "mid": mid,
                "updated": best_match.get("updated"),
            }

        except Exception as e:
            logging.error("[Polygon] _get_option_from_chain failed: %s", e)
            return None


    def get_last_trade(self, symbol: str):
        url = f"{self.base_url}/v2/last/trade/{symbol.upper()}"
        params = {"apiKey": self.api_key}
        try:
            resp = requests.get(url, params=params, timeout=5)
            resp.raise_for_status()
            data = resp.json()
            return data.get("results", {}).get("p")
        except Exception as e:
            logging.error(f"[Polygon] get_last_trade failed: {e}")
            return None

    def get_snapshot(self, symbol: str):
        url = f"{self.base_url}/v2/snapshot/locale/us/markets/stocks/tickers/{symbol.upper()}"
        params = {"apiKey": self.api_key}
        try:
            resp = requests.get(url, params=params, timeout=5)
            resp.raise_for_status()
            payload = resp.json()

            ticker_node = payload.get("ticker")
            if not isinstance(ticker_node, dict):
                logging.warning("[Polygon] snapshot 'ticker' node is not a dict (%s)", type(ticker_node))
                return None
            logging.info(f"ticker_node:{ticker_node}")
            last_trade = ticker_node.get("lastTrade", {})
            last_quote = ticker_node.get("lastQuote", {})
            today_bar  = ticker_node.get("day", {})
            prev_bar   = ticker_node.get("prevDay", {})

            return {
                "last":       last_trade.get("p"),
                "bid":        last_quote.get("p"),   # best bid price
                "ask":        last_quote.get("P"),   # best ask price
                "today_high": today_bar.get("h"),
                "today_low":  today_bar.get("l"),
                "prev_high":  prev_bar.get("h"),
                "prev_low":   prev_bar.get("l"),
            }
        except Exception as e:
            logging.error("[Polygon] get_snapshot failed: %s", e)
            return None


    # ---------------- WS METHODS ----------------
    def subscribe(self, symbol: str, callback):
        """Register a callback and send WS subscription if it's the first for this symbol."""
        sym = symbol.upper()
        # 1. Add callback to manager
        callback_manager.add_callback(sym, callback)
        
        # 2. Check if WS subscription is needed (thread-safe check)
        with self._ws_lock:
            if sym in self._active_ws_symbols:
                logging.debug(f"[Polygon] Callback added for {sym}. WS subscription already active.")
                return

            # If not active, mark it and send WS message
            self._active_ws_symbols.add(sym)
        
        if self.ws:
            msg = {"action": "subscribe", "params": f"T.{sym}"}
            try:
                self.ws.send(json.dumps(msg))
                logging.info(f"[Polygon] WS subscribed to T.{sym}")
            except Exception as e:
                logging.error(f"[Polygon] WS subscribe error: {e}")


    def unsubscribe(self, symbol: str, callback):
        """Remove a specific callback and send WS unsubscribe if it was the last."""
        sym = symbol.upper()

        # 1. Remove callback from manager
        callback_manager.remove_callback(sym, callback)

        # 2. Check if WS unsubscription is needed (thread-safe check)
        remaining_symbols = callback_manager.list_symbols()

        with self._ws_lock:
            # Check if symbol is still required by any other callback
            if sym not in remaining_symbols and sym in self._active_ws_symbols:
                self._active_ws_symbols.remove(sym)
                
                if self.ws:
                    msg = {"action": "unsubscribe", "params": f"T.{sym}"}
                    try:
                        self.ws.send(json.dumps(msg))
                        logging.info(f"[Polygon] WS unsubscribed from T.{sym}")
                    except Exception as e:
                        logging.error(f"[Polygon] WS unsubscribe error: {e}")
            elif sym in self._active_ws_symbols:
                 logging.debug(f"[Polygon] Callback removed for {sym}. WS subscription remains active.")


    def _start_ws(self):
        """Background thread ile WS başlat."""
        def run():
            while True:
                try:
                    self.ws = websocket.WebSocketApp(
                        self.ws_url,
                        on_open=self._on_open,
                        on_message=self._on_message,
                        on_error=self._on_error,
                        on_close=self._on_close
                    )
                    self.ws.run_forever(ping_interval=20, ping_timeout=10)
                except Exception as e:
                    logging.error(f"[Polygon] WS connection error: {e}")
                time.sleep(5)

        self.ws_thread = threading.Thread(target=run, daemon=True)
        self.ws_thread.start()

    def _on_open(self, ws):
        auth_msg = {"action": "auth", "params": self.api_key}
        ws.send(json.dumps(auth_msg))
        logging.info("[Polygon] WS connected & authenticated")

        # After re-authentication, resubscribe to all symbols
        with self._ws_lock:
            for sym in self._active_ws_symbols:
                msg = {"action": "subscribe", "params": f"T.{sym}"}
                try:
                    ws.send(json.dumps(msg))
                    logging.info(f"[Polygon] Re-subscribed to T.{sym}")
                except Exception as e:
                    logging.error(f"[Polygon] WS re-subscribe error for {sym}: {e}")

    def _on_message(self, ws, message):
        """
        Receives message and triggers ALL registered callbacks via the manager.
        """
        try:
            data = json.loads(message)
            for event in data:
                if event.get("ev") == "T":
                    sym = event.get("sym")
                    price = event.get("p")
                    if sym and price is not None:
                        # 🎯 The FIX: Trigger all callbacks for this symbol via the manager
                        callback_manager.trigger(sym, price)
        except Exception as e:
            logging.error(f"[Polygon] WS message error: {e} | {message}")

    def _on_error(self, ws, error):
        logging.error(f"[Polygon] WS error: {error}")

    def _on_close(self, ws, close_status_code, close_msg):
        logging.warning(f"[Polygon] WS closed: {close_status_code} {close_msg}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    svc = PolygonService()
    snapshot = svc.get_option_snapshot("QQQ", "20251021", 612.5, "C")
    print(snapshot)
    time.sleep(3)

# Note: assuming polygon_service is initialized after callback_manager is available
polygon_service = PolygonService()