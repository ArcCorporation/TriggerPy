import requests
import logging
import json
import threading
import time
import websocket
from Services.enigma3 import  Enigma3Service
from Services.randomness import KEY


class PolygonService:
    def __init__(self):
        # Şifreli API key çözülüyor
        api_key_enc = "SBb(2-n>X0)nJZ6}+[M3b)A>KV%fY}>K"
        eservis = Enigma3Service()
        self.api_key = eservis.decrypt(KEY, api_key_enc)

        self.base_url = "https://api.polygon.io"
        self.ws_url = "wss://socket.polygon.io/stocks"

        # WS için
        self.subscriptions = {}
        self.ws = None
        self.ws_thread = None

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
        Fallback: Pull full chain snapshot and filter the target contract.
        Now with fuzzy expiry/strike matching for Polygon consistency.
        """
        try:
            url = f"{self.base_url}/v3/snapshot/options/{underlying.upper()}"
            params = {"apiKey": self.api_key}
            resp = requests.get(url, params=params, timeout=8)
            resp.raise_for_status()

            results = resp.json().get("results", [])
            if not isinstance(results, list) or len(results) == 0:
                logging.error(f"[Polygon] Chain snapshot empty for {underlying}")
                return None

            target_year = expiry[:4]
            target_month = expiry[4:6]
            target_day = expiry[6:8]
            right = right.upper()
            best_match = None
            best_diff = 999

            for item in results:
                details = item.get("details", {})
                if not details:
                    continue

                exp = details.get("expiration_date", "")
                strike_price = details.get("strike_price")
                ctype = details.get("contract_type", "").lower()

                if not exp or strike_price is None or not ctype:
                    continue

                exp_y, exp_m, exp_d = exp.split("-")
                if exp_y != target_year or exp_m != target_month:
                    continue

                # Allow +/- 7 days tolerance (weekly misalignment)
                day_diff = abs(int(exp_d) - int(target_day))
                if day_diff > 7:
                    continue

                # Compare contract type (first letter enough)
                if ctype[0].upper() != right[0]:
                    continue

                # Compare strike closeness
                diff = abs(float(strike_price) - float(strike))
                if diff < best_diff:
                    best_diff = diff
                    best_match = item

            if not best_match:
                logging.error(f"[Polygon] Contract not found in chain for {underlying} {expiry} {strike}{right}")
                return None

            quote = best_match.get("last_quote", {})
            trade = best_match.get("last_trade", {})
            bid, ask, last = quote.get("bid"), quote.get("ask"), trade.get("price")
            mid = (bid + ask) / 2 if bid and ask else None

            details = best_match.get("details", {})
            return {
                "symbol": details.get("ticker"),
                "bid": bid,
                "ask": ask,
                "last": last,
                "mid": mid,
                "updated": best_match.get("updated", None),
            }

        except Exception as e:
            logging.error(f"[Polygon] _get_option_from_chain failed: {e}")
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
            data = resp.json()
            ticker = data.get("ticker", {})
            return {
                "last": ticker.get("lastTrade", {}).get("p"),
                "bid": ticker.get("lastQuote", {}).get("bp"),
                "ask": ticker.get("lastQuote", {}).get("ap"),
            }
        except Exception as e:
            logging.error(f"[Polygon] get_snapshot failed: {e}")
            return None

    # ---------------- WS METHODS ----------------
    def subscribe(self, symbol: str, callback):
        """Belirli sembol için WS subscription başlat."""
        self.subscriptions[symbol.upper()] = callback
        if self.ws:
            msg = {"action": "subscribe", "params": f"T.{symbol.upper()}"}
            try:
                self.ws.send(json.dumps(msg))
            except Exception as e:
                logging.error(f"[Polygon] WS subscribe error: {e}")

    def unsubscribe(self, symbol: str):
        sym = symbol.upper()
        if sym in self.subscriptions:
            del self.subscriptions[sym]
        if self.ws:
            msg = {"action": "unsubscribe", "params": f"T.{sym}"}
            try:
                self.ws.send(json.dumps(msg))
            except Exception as e:
                logging.error(f"[Polygon] WS unsubscribe error: {e}")

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

    def _on_message(self, ws, message):
        try:
            data = json.loads(message)
            for event in data:
                if event.get("ev") == "T":
                    sym = event.get("sym")
                    price = event.get("p")
                    cb = self.subscriptions.get(sym)
                    if cb:
                        cb(price)
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

polygon_service = PolygonService()
