import requests
import logging
import json
import threading
import time
import websocket
from Services.enigma3 import  Enigma3Service
from Services.randomness import  KEY


class PolygonService:
    def __init__(self):
        # Şifreli API key çözülüyor
        api_key_enc = "Y+s5w[!V3[K3):c%0wgSl;|Ps;2Av%KL"
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
        Fetch option snapshot from Polygon for a given contract.
        Returns {'bid', 'ask', 'last', 'mid'} or None.
        """
        # Build OCC symbol, e.g. TSLA251010C00450000
        y, m, d = expiry[:4], expiry[4:6], expiry[6:8]
        strike_str = f"{int(strike*1000):08d}"
        occ = f"{underlying.upper()}{y[2:]}{m}{d}{right.upper()}{strike_str}"

        url = f"{self.base_url}/v3/snapshot/options/{underlying.upper()}/{occ}"
        params = {"apiKey": self.api_key}

        try:
            resp = requests.get(url, params=params, timeout=5)
            resp.raise_for_status()
            data = resp.json().get("results", {})
            quote = data.get("last_quote", {})
            trade = data.get("last_trade", {})
            bid, ask, last = quote.get("bid"), quote.get("ask"), trade.get("price")
            mid = None
            if bid and ask:
                mid = (bid + ask) / 2
            return {"bid": bid, "ask": ask, "last": last, "mid": mid}
        except Exception as e:
            logging.error(f"[Polygon] get_option_snapshot failed: {e}")
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
                time.sleep(5)  # reconnect denemesi

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
                if event.get("ev") == "T":  # trade event
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
    svc.subscribe("TSLA", lambda p: print("TSLA tick:", p))

    # Test için 10 saniye bekleyelim
    time.sleep(10)


polygon_service = PolygonService()
