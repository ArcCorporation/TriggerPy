import requests
import logging
from enigma3 import Enigma3Service
from randomness import KEY

class PolygonService:
    def __init__(self):
        # Senin API key
        api_key = "Y+s5w[!V3[K3):c%0wgSl;|Ps;2Av%KL"
        

        eservis = Enigma3Service()
        self.api_key = eservis.decrypt(KEY, api_key)

        self.base_url = "https://api.polygon.io"

    def get_last_trade(self, symbol: str):
        """
        Fetch latest trade price (v2 endpoint).
        """
        url = f"{self.base_url}/v2/last/trade/{symbol.upper()}"
        params = {"apiKey": self.api_key}
        try:
            resp = requests.get(url, params=params, timeout=5)
            resp.raise_for_status()
            data = resp.json()
            price = data.get("results", {}).get("p")
            return price
        except Exception as e:
            logging.error(f"[Polygon] get_last_trade failed: {e}")
            return None

    def get_snapshot(self, symbol: str):
        """
        Fetch snapshot with bid/ask/last.
        """
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


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    svc = PolygonService()
    print("TSLA last trade:", svc.get_last_trade("TSLA"))
    print("TSLA snapshot:", svc.get_snapshot("TSLA"))
