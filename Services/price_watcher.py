# Services/price_watcher.py
import threading
import time


class PriceWatcher:
    def __init__(self, symbol: str, update_fn, polygon_service, poll_interval: float = 1.0):
        """
        :param symbol: İzlenecek sembol (örn: 'TSLA')
        :param update_fn: UI update callback, fiyat float parametre alır
        :param polygon_service: PolygonService instance
        :param poll_interval: kaç saniyede bir kontrol edilecek (default 1.0)
        """
        self.symbol = symbol
        self.update_fn = update_fn
        self.polygon = polygon_service
        self.poll_interval = poll_interval
        self.running = True
        self.current_price = None
        self._lock = threading.Lock()

        # Thread’i başlat
        self.thread = threading.Thread(target=self._watch_loop, daemon=True)
        self.thread.start()

    def _watch_loop(self):
        """Fiyatı sürekli izle ve callback ile bildir."""
        while self.running:
            try:
                snap = self.polygon.get_snapshot(self.symbol)
                if snap and "last" in snap:
                    price = snap["last"]
                    with self._lock:
                        self.current_price = price
                    self.update_fn(price)  # UI’yi besle
            except Exception as e:
                print(f"[PriceWatcher] Error: {e}")
            time.sleep(self.poll_interval)

    def get_price(self):
        """En son bilinen fiyatı döndürür (None olabilir)."""
        with self._lock:
            return self.current_price

    def stop(self):
        """Watcher’ı durdurmak için."""
        self.running = False
        if self.thread.is_alive():
            self.thread.join(timeout=1)
